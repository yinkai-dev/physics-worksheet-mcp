"""Validated spec -> deterministic TeX -> compiler -> verified artifacts."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time

from . import __version__
from .adapters.compiler import XeLaTeXCompiler
from .contract import ContractError, load_spec, source_choice_labels, validate_spec
from .safety import escape_text, normalize_math_unicode

ARTIFACT_NAMES = ("worksheet.pdf", "worksheet.tex", "compile.log", "manifest.json", "rejected-worksheet.pdf")
ANSWER_ARTIFACT_NAMES = ("answers.pdf", "answers.tex", "answers-compile.log", "answers-manifest.json", "rejected-answers.pdf")

# Strip a trailing numeric page number from each page's text before concatenating.
# Continuous-Chinese substring QA was misreporting cross-page question text where
# a standalone page number (e.g. "1") sat between two character runs from the
# same block ("根据以上数据\n1\n" + "绘出图线Ⅰ和Ⅱ"). The pattern only removes an
# isolated trailing numeric line; in-text numbers and missing glyphs are unaffected.
_PAGE_NUMBER_TRAILING = re.compile(r"(?:^|\n)[ \t]*\d+[ \t]*\n*$")


def _strip_page_numbers(page_texts):
    """Return a copy of ``page_texts`` with a trailing page number removed per page."""
    return [_PAGE_NUMBER_TRAILING.sub("", text, count=1) for text in page_texts]


class DirectoryVerifiedAssetReader:
    """Read resolver-materialized assets without accepting paths from ExamSpec."""

    def __init__(self, manifest_path):
        path = Path(manifest_path)
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ContractError("verified-asset-manifest-unavailable")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("verified-asset-manifest-invalid") from exc
        if not isinstance(value, dict) or set(value) != {"schema_version", "assets", "provider", "question_count"} or value["schema_version"] != "1.0" or not isinstance(value["assets"], dict):
            raise ContractError("verified-asset-manifest-invalid")
        self.root, self.assets = path.parent.resolve(), value["assets"]

    def read(self, asset_id):
        record = self.assets.get(asset_id)
        required = {"path", "media_type", "width", "height", "bytes", "sha256"}
        if not isinstance(record, dict) or set(record) != required or record.get("sha256") != asset_id:
            raise ContractError("verified-asset-not-declared")
        relative = Path(record["path"])
        if relative.is_absolute() or ".." in relative.parts or record["path"].startswith(("http:", "https:", "data:")):
            raise ContractError("verified-asset-path-invalid")
        path = self.root / relative
        if not path.is_file() or path.is_symlink():
            raise ContractError("verified-asset-unavailable")
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ContractError("verified-asset-path-escape") from exc
        data = path.read_bytes()
        if len(data) != record["bytes"]:
            raise ContractError("verified-asset-byte-drift")
        return {"bytes": data, "media_type": record["media_type"],
                "width": record["width"], "height": record["height"]}


@dataclass
class RenderResult:
    status: str
    pdf_path: str | None = None
    tex_path: str | None = None
    log_path: str | None = None
    manifest_path: str | None = None
    warnings: list[str] | None = None
    compiler: str | None = None
    compiler_version: str | None = None
    error: str | None = None

    def to_dict(self):
        return asdict(self)


# Source figure labels: 甲, 图乙, (b), 图2, 第8题图甲; a bare 第6题图 captions a group.
_SUB_LABEL = re.compile(r"(?:第\s*\d+\s*题\s*)?图\s*(?:[甲乙丙丁戊]|\d{1,2}|[(（]?[a-dA-D][)）]?)|[甲乙丙丁戊]|[(（][a-dA-D][)）]")
_GROUP_CAPTION = re.compile(r"第\s*\d+\s*题\s*图")
_NUMBERED = re.compile(r"第\s*\d+\s*题")
_OPTION_LABEL = re.compile(r"[A-D][.．]")
PANEL_MAX_MM, PANEL_GAP_MM = 80, 5


def _inline_tex(block):
    if block["type"] == "text":
        return escape_text(block["value"])
    if block["type"] == "math":
        return r"\(" + normalize_math_unicode(block["value"]) + r"\)"
    columns = len(block["rows"][0])
    rows = "".join(" & ".join(_block_tex(cell) for cell in row) + r" \\ \hline " for row in block["rows"])
    return (r"\par\begingroup\small\setlength{\tabcolsep}{3pt}\renewcommand{\arraystretch}{1.3}"
            + r"\begin{tabularx}{\linewidth}{|*{" + str(columns) + r"}{>{\centering\arraybackslash}X|}}\hline "
            + rows + r"\end{tabularx}\par\endgroup")


def _block_tex(blocks, assets=None, asset_files=None):
    output = []
    for segment in _figure_segments(blocks):
        if segment[0] == "figures":
            output.append(_figure_cluster_tex(segment[1], segment[2], assets or [], asset_files or {}))
        else:
            output.extend(_inline_tex(block) for block in segment[1])
    return "".join(output)


def _label_lines(value):
    """Leading label-only lines of a text block, and the verbatim remainder."""
    position, labels = 0, []
    while position < len(value):
        end = value.find("\n", position)
        end = len(value) if end < 0 else end
        line = value[position:end].strip()
        if line and not (_SUB_LABEL.fullmatch(line) or _GROUP_CAPTION.fullmatch(line)):
            break
        if line:
            labels.append(line)
        position = end + 1
    return labels, value[position:]


def _assign_labels(labels, slots, groups):
    """Labels follow the figures they name, in order; captions name the group."""
    bare = any(not _NUMBERED.match(label) for label in labels)
    own = []
    for label in labels:
        # "甲 乙 丙 第15题图2": the numbered line captions the whole lettered set.
        (groups if _GROUP_CAPTION.fullmatch(label) or (bare and _NUMBERED.match(label)) else own).append(label)
    if len(own) <= len(slots):
        for slot, label in zip(slots[len(slots) - len(own):], own):
            slot.append(label)
    else:
        for slot, label in zip(slots[:-1], own):
            slot.append(label)
        slots[-1].extend(own[len(slots) - 1:])


def _trailing_option_label(value):
    parts = value.split("\n")
    end = len(parts)
    while end and not parts[end - 1].strip():
        end -= 1
    if not end or not _OPTION_LABEL.fullmatch(parts[end - 1].strip()):
        return None, value
    return parts[end - 1].strip(), "\n".join(parts[:end - 1] + parts[end:])


def _trailing_label_lines(value):
    parts = value.split("\n")
    end = len(parts)
    while end and not parts[end - 1].strip():
        end -= 1
    start, labels = end, []
    while start and (parts[start - 1].strip() and
                     (_SUB_LABEL.fullmatch(parts[start - 1].strip()) or _GROUP_CAPTION.fullmatch(parts[start - 1].strip()))):
        labels.append(parts[start - 1].strip())
        start -= 1
    if not labels:
        return [], value
    return list(reversed(labels)), "\n".join(parts[:start] + parts[end:])


def _shared_panel_caption(labels, items):
    previous_a = any(re.fullmatch(r"[(（]a[)）]", label, re.I)
                     for _, own in items[:-1] for label in own)
    current_b = any(re.fullmatch(r"[(（]b[)）]", label, re.I) for label in labels[1:])
    return len(labels) > 1 and re.fullmatch(r"[甲乙丙丁戊]", labels[0]) and previous_a and current_b


def _figure_segments(blocks):
    """Group display labels without changing source blocks or plain-text order."""
    segments, plain, cursor = [], [], 0
    while cursor < len(blocks):
        if blocks[cursor]["type"] != "figure":
            plain.append(blocks[cursor])
            cursor += 1
            continue
        option_label = None
        if plain and plain[-1]["type"] == "text":
            option_label, rest = _trailing_option_label(plain[-1]["value"])
            if option_label:
                if rest.strip():
                    plain[-1] = {"type": "text", "value": rest}
                else:
                    plain.pop()
        if plain:
            segments.append(("blocks", plain))
            plain = []
        items, groups, pending = [], [], []
        while cursor < len(blocks):
            block = blocks[cursor]
            if block["type"] == "figure":
                shown = {**block, "_option_label": option_label} if option_label else block
                items.append((shown, []))
                pending.append(items[-1][1])
                option_label = None
                cursor += 1
                continue
            if block["type"] != "text":
                break
            stripped = block["value"].strip()
            if (cursor + 1 < len(blocks) and blocks[cursor + 1]["type"] == "figure" and
                    _OPTION_LABEL.fullmatch(stripped)):
                option_label = stripped
                cursor += 1
                continue
            labels, rest = _label_lines(block["value"])
            if labels:
                if _shared_panel_caption(labels, items):
                    groups.append(labels[0])
                    labels = labels[1:]
                _assign_labels(labels, pending or [items[-1][1]], groups)
                pending = []
            cursor += 1
            if rest.strip():
                plain.append({"type": "text", "value": rest})
                break
        segments.append(("figures", items, groups))
    if plain:
        segments.append(("blocks", plain))
    for index, segment in enumerate(segments[:-1]):
        if segment[0] != "figures" or segments[index + 1][0] != "blocks":
            continue
        following = segments[index + 1][1]
        if not following or following[-1]["type"] != "text":
            continue
        labels, rest = _trailing_label_lines(following[-1]["value"])
        if labels:
            following[-1] = {"type": "text", "value": rest}
            _assign_labels(labels, [own for _, own in segment[1]], segment[2])
    return segments


def _figure_gap(asset):
    # Source distance between neighbours, kept readable and compact.
    return min(12.0, max(4.0, asset.get("source_layout", {}).get("gap_before_mm") or 0))


def _figure_rows(items, assets_by_id, available_mm):
    """Pack figures by source-derived physical widths and source rows, keeping order."""
    rows, row, used = [], [], 0.0
    for figure, labels in items:
        asset = assets_by_id[figure["asset_id"]]
        group = asset.get("source_layout", {}).get("row_group")
        changed = bool(row and group and group != row[-1][2].get("source_layout", {}).get("row_group"))
        if row and (used + _figure_gap(asset) + asset["width_mm"] > available_mm or changed):
            rows.append(row)
            row, used = [], 0.0
        row.append((figure, labels, asset))
        used += asset["width_mm"] + (_figure_gap(asset) if len(row) > 1 else 0)
    if row:
        rows.append(row)
    return rows


def _row_width(row):
    return sum(asset["width_mm"] for *_, asset in row) + sum(_figure_gap(asset) for *_, asset in row[1:])


def _figure_cluster_tex(items, groups, assets, asset_files, *, available_mm=170, _measure_options=True):
    """Centred rows of figures, each label under its figure and captions under the set."""
    if (_measure_options and items and not groups and
            all(figure.get("_option_label") and not labels for figure, labels in items)):
        choices = [[{"type": "text", "value": figure["_option_label"]}, figure] for figure, _ in items]
        stacked = [_figure_cluster_tex(items, groups, assets, asset_files, available_mm=0, _measure_options=False)]
        return "\n".join(_graphic_choice_tex(choices, "body", assets, asset_files, stacked)) + "\n"
    rows = _figure_rows(items, {asset["asset_id"]: asset for asset in assets}, available_mm)
    rendered = []
    for row_index, row in enumerate(rows):
        cells = []
        for figure, labels, asset in row:
            path = asset_files.get(asset["asset_id"])
            if path is None:
                raise ContractError("verified-asset-not-staged:" + asset["asset_id"])
            width = format(asset["width_mm"], ".3f")
            under = ([_block_tex(figure["caption"])] if figure.get("caption") else []) + [escape_text(label) for label in labels]
            option = (r"{\raggedright\strut " + escape_text(figure["_option_label"]) + r"\par}"
                      if figure.get("_option_label") else "")
            cells.append(r"\begin{minipage}[b]{" + width + r"mm}\centering" + option
                         + r"\includegraphics[width=" + width + r"mm,height=.60\textheight,keepaspectratio]{" + path + "}"
                         + (r"\par\vspace{1pt}{\footnotesize " + r"\\".join(under) + r"\par}" if under else "")
                         + r"\end{minipage}")
        joined = cells[0] + "".join(r"\hspace{" + format(_figure_gap(asset), "g") + "mm}" + cell
                                    for cell, (*_, asset) in zip(cells[1:], row[1:]))
        caption = (r"{\centering\footnotesize " + r"\quad ".join(escape_text(group) for group in groups) + r"\par}"
                   if groups and row_index == len(rows) - 1 else "")
        # Each row (the last one with its captions) is indivisible and tied to the
        # text above; a page may still break between rows of a long figure set.
        rendered.append(r"\par\nobreak\vspace{2pt}\noindent\begin{minipage}{\dimexpr\linewidth-\leftskip\relax}{\centering "
                        + joined + r"\par}" + caption + r"\end{minipage}\par\vspace{2pt}")
    return "\n".join(rendered) + "\n"


def _renumber_captions(blocks, index, original_number):
    """Print source figure captions with the worksheet number; source blocks stay intact."""
    def line(text):
        stripped = text.strip()
        if _SUB_LABEL.fullmatch(stripped) or _GROUP_CAPTION.fullmatch(stripped):
            return _NUMBERED.sub(f"第 {index} 题", text, count=1)
        if original_number is None:
            return text
        return re.sub(r"第\s*" + re.escape(str(original_number)) + r"\s*题\s*图", f"第 {index} 题图", text)
    result = []
    for block in blocks:
        if block["type"] == "text":
            block = {**block, "value": "\n".join(line(part) for part in block["value"].split("\n"))}
        elif block["type"] == "figure" and block.get("caption"):
            block = {**block, "caption": _renumber_captions(block["caption"], index, original_number)}
        result.append(block)
    return result


_QUESTION_PREFIX = re.compile(
    r"^\s*(?:第\s*(?P<named>[1-9]\d{0,2})\s*题\s*[.．、:：]?\s*|"
    r"(?P<plain>[1-9]\d{0,2})(?:[．、]\s*|\.(?!\d)(?:\s+|(?=[\u4e00-\u9fff]))))"
)


def _display_body(question):
    """Move a corroborated original question number into the source line only.

    Decimal values, sub-question numbers and unmatched prefixes remain verbatim.
    The normalized spec and exact source excerpt are never changed.
    """
    blocks = question["body"]
    if not blocks or blocks[0]["type"] != "text":
        return blocks, question.get("original_number")
    body_prefix = _QUESTION_PREFIX.match(blocks[0]["value"])
    excerpt = re.sub(r"^\s*#{1,6}[ \t]+", "", question["source"]["source_excerpt"])
    source_prefix = _QUESTION_PREFIX.match(excerpt)
    if not body_prefix or not source_prefix:
        return blocks, question.get("original_number")
    number = body_prefix.group("named") or body_prefix.group("plain")
    if number != (source_prefix.group("named") or source_prefix.group("plain")):
        return blocks, question.get("original_number")
    return [{"type": "text", "value": blocks[0]["value"][body_prefix.end():]}, *blocks[1:]], number


def _source_tex(source, original_number):
    # File suffixes are storage details. Keep the exact title in the manifest.
    title = re.sub(r"(?:[-_ ]OCR)?\.(?:md|markdown|pdf|txt)$", "", source["title"], flags=re.I)
    title = title or source["title"]
    parts = [" ".join(title.split())]
    if original_number is not None:
        parts.append(f"原第 {original_number} 题")
    parts.append("页码未知" if source["page"] is None else f"第 {source['page']} 页")
    # Give long filenames safe line-break opportunities without truncating them.
    result, ascii_run = [], 0
    for char in "；".join(parts):
        result.append(escape_text(char))
        ascii_run = ascii_run + 1 if char.isascii() and char.isalnum() else 0
        if char in "-_/ ." or ascii_run == 24:
            result.append(r"\allowbreak{}")
            ascii_run = 0
    return "".join(result)


def _choice_tex(choices, question_id, assets=None, asset_files=None):
    rendered = [escape_text(choice) if isinstance(choice, str) else _block_tex(choice, assets, asset_files)
                for choice in choices]
    stacked = [r"\typeout{physics-choice-layout:" + question_id + ":stacked}"]
    stacked += [r"\par{\leftskip=1em\noindent " + choice + r"\par}" for choice in rendered]
    # Paragraphs and very long inputs must not enter TeX's finite-width hbox.
    if any(isinstance(choice, list) and any(b["type"] == "table" for b in choice) for choice in choices):
        return stacked
    if any(isinstance(choice, list) and any(b["type"] == "figure" for b in choice) for choice in choices):
        return _graphic_choice_tex(choices, question_id, assets or [], asset_files or {}, stacked)
    values = [choice if isinstance(choice, str) else "".join(block["value"] for block in choice)
              for choice in choices]
    if any("\n" in value or len(value) > 300 for value in values):
        return stacked
    lines = [r"\setlength{\choicemax}{0pt}"]
    for choice in rendered:
        lines += [r"\settowidth{\choicewidth}{\strut " + choice + "}",
                  r"\ifdim\choicewidth>\choicemax\setlength{\choicemax}{\choicewidth}\fi"]

    def rows(columns, label):
        result = [r"\typeout{physics-choice-layout:" + question_id + ":" + label + "}"]
        for start in range(0, len(rendered), columns):
            cells = [r"\parbox[t]{\choicecell}{\raggedright\strut " + choice + r"\strut}"
                     for choice in rendered[start:start + columns]]
            result.append(r"\par\noindent " + r"\hfill ".join(cells) + r"\par")
        return result

    if len(choices) <= 4:
        lines += [r"\setlength{\choicecell}{\dimexpr(\linewidth-" + str(len(choices) - 1)
                  + r"\choicegap)/" + str(len(choices)) + r"\relax}",
                  r"\ifdim\choicemax>\choicecell"]
    lines += [r"\setlength{\choicecell}{\dimexpr(\linewidth-\choicegap)/2\relax}",
              r"\ifdim\choicemax>\choicecell", *stacked, r"\else",
              *rows(2, "two-column"), r"\fi"]
    if len(choices) <= 4:
        lines += [r"\else", *rows(len(choices), "single-row"), r"\fi"]
    return lines


def _graphic_choice_tex(choices, question_id, assets, asset_files, stacked):
    """Diagram options side by side when the measured row fits the line."""
    by_id = {asset["asset_id"]: asset for asset in assets}
    cells = []
    for choice in choices:
        blocks = [{"type": "text", "value": choice}] if isinstance(choice, str) else choice
        figures = [block for block in blocks if block["type"] == "figure"]
        width = max(18.0, sum(by_id[f["asset_id"]]["width_mm"] for f in figures) + 4 * max(0, len(figures) - 1))
        images = []
        for figure in figures:
            path = asset_files.get(figure["asset_id"])
            if path is None:
                raise ContractError("verified-asset-not-staged:" + figure["asset_id"])
            images.append(r"\includegraphics[width=" + format(by_id[figure["asset_id"]]["width_mm"], ".3f")
                          + r"mm,height=.60\textheight,keepaspectratio]{" + path + "}")
        cells.append(r"\begin{minipage}[t]{" + format(width, ".3f") + r"mm}\raggedright\strut "
                     + "".join(_inline_tex(block) for block in blocks if block["type"] != "figure")
                     + r"\par\centering " + r"\hspace{4mm}".join(images) + r"\end{minipage}")
    return [r"\sbox\choicerow{" + r"\hspace{4mm}".join(cells) + "}",
            r"\ifdim\wd\choicerow>\linewidth", *stacked, r"\else",
            r"\typeout{physics-choice-layout:" + question_id + ":graphic-row}",
            r"\par\noindent " + r"\hfill ".join(cells) + r"\par", r"\fi"]


def _asset_tex(question, asset_files, *, inline=False):
    lines = []
    for asset in question.get("assets", []):
        if asset["placement"] == "in_content" and not inline:
            continue
        path = asset_files.get(asset["asset_id"])
        if path is None:
            raise ContractError("verified-asset-not-staged:" + asset["asset_id"])
        is_facsimile = asset["role"] == "full_question_facsimile"
        dimensions = (r"width=\linewidth,height=.72\textheight"
                      if is_facsimile else r"width=.55\linewidth,height=.32\textheight")
        if asset["placement"] == "in_content":
            dimensions = "width=" + format(asset["width_mm"], ".3f") + r"mm,height=.60\textheight"
        lines += [r"\par\begin{center}" if is_facsimile else r"\par\nobreak\begin{center}",
                  r"\includegraphics[" + dimensions + r",keepaspectratio]{" + path + "}",
                  r"\end{center}\par"]
    return lines


def _side_by_side(left, left_mm, figure, figure_mm, gap_mm):
    return (r"\noindent\begin{minipage}[t]{" + format(left_mm, ".3f") + r"mm}\vspace{0pt}" + left + r"\end{minipage}%"
            + "\n" + r"\hspace{" + format(gap_mm, "g") + r"mm}\begin{minipage}[t]{" + format(figure_mm, ".3f")
            + r"mm}\vspace{0pt}" + figure + r"\end{minipage}\par" + "\n")


def _source_panel_tex(question, body, heading, asset_files):
    """Reviewed source layout: text left, the listed figures and their labels right."""
    layout, assets = question["layout"], question["assets"]
    start, right_ids = layout["start_block"], set(layout["right_asset_ids"])
    right_items, right_groups, left = [], [], []
    for segment in _figure_segments(body[start:]):
        if segment[0] == "blocks":
            left.append(_block_tex(segment[1], assets, asset_files))
            continue
        mine = [item for item in segment[1] if item[0]["asset_id"] in right_ids]
        others = [item for item in segment[1] if item[0]["asset_id"] not in right_ids]
        right_items += mine
        right_groups += segment[2] if mine else []
        if others:
            left.append(_figure_cluster_tex(others, [] if mine else segment[2], assets, asset_files))
    # Preserve source rows within the side panel where they fit. Otherwise
    # stack complete figures instead of shrinking labels to fit a column.
    by_id = {asset["asset_id"]: asset for asset in assets}
    width = max(_row_width(row) for row in _figure_rows(right_items, by_id, 100 - layout["gap_mm"]))
    left_text = (heading if start == 0 else "") + "".join(left)
    if question["choices"]:
        left_text += "\n".join(_choice_tex(question["choices"], question["id"], assets, asset_files))
    figure = _figure_cluster_tex(right_items, right_groups, assets, asset_files, available_mm=width + 0.01)
    lines = ([heading + _block_tex(body[:start], assets, asset_files), r"\par"] if start else [])
    return lines + [_side_by_side(left_text, 170 - layout["gap_mm"] - width, figure, width, layout["gap_mm"])]


def _structured_tex(question, body, heading, asset_files):
    """Stacked layout, or a measured side panel when one narrow figure set saves space."""
    assets = question.get("assets") or []
    choices = _choice_tex(question["choices"], question["id"], assets, asset_files) if question["choices"] else []
    stacked = "\n".join([heading + _block_tex(body, assets, asset_files), *_asset_tex(question, asset_files), *choices])
    segments = _figure_segments(body)
    figure_segments = [index for index, segment in enumerate(segments) if segment[0] == "figures"]
    if len(figure_segments) != 1 or any(asset["placement"] != "in_content" for asset in assets):
        return [stacked]
    index = figure_segments[0]
    rows = _figure_rows(segments[index][1], {asset["asset_id"]: asset for asset in assets}, PANEL_MAX_MM)
    if len(rows) != 1 or _row_width(rows[0]) > PANEL_MAX_MM:
        return [stacked]
    width = _row_width(rows[0])
    figure = _figure_cluster_tex(segments[index][1], segments[index][2], assets, asset_files, available_mm=width + 0.01)
    before = "".join(_block_tex(segment[1]) for segment in segments[:index])
    after = "".join(_block_tex(segment[1]) for segment in segments[index + 1:]) + ("\n" + "\n".join(choices) if choices else "")
    left_mm = 170 - PANEL_GAP_MM - width
    variants = [("side", _side_by_side(heading + before, left_mm, figure, width, PANEL_GAP_MM) + after)] if after.strip() else []
    variants.append(("sideall", _side_by_side(heading + before + after, left_mm, figure, width, PANEL_GAP_MM)))
    # TeX measures each complete variant; the side panel must save at least 8 mm.
    # Tall questions keep the stacked, page-breakable form.
    lines = [r"\sbox\layoutstacked{\begin{minipage}{\linewidth}" + stacked + r"\end{minipage}}%",
             r"\ifdim\dimexpr\ht\layoutstacked+\dp\layoutstacked\relax>.6\textheight", stacked, r"\else"]
    lines += [r"\sbox\layout" + name + r"{\begin{minipage}{\linewidth}" + tex + r"\end{minipage}}%" for name, tex in variants]
    lines.append(r"\setlength\layoutbest{\dimexpr\ht\layoutstacked+\dp\layoutstacked-8mm\relax}\def\layoutpick{stacked}%")
    for name, _ in variants:
        height = r"\dimexpr\ht\layout" + name + r"+\dp\layout" + name + r"\relax"
        lines.append(r"\ifdim" + height + r"<\layoutbest\setlength\layoutbest{" + height + r"}\def\layoutpick{" + name + r"}\fi")
    return lines + [r"\typeout{physics-figure-layout:" + question["id"] + r":\layoutpick}%",
                    r"\noindent\usebox{\csname layout\layoutpick\endcsname}\par", r"\fi"]


def _question_tex(question, index, asset_files=None):
    asset_files = asset_files or {}
    body, original_number = _display_body(question)
    body = _renumber_captions(body, index, original_number)
    score = "" if question["points"] is None else f"（{question['points']:g}分）"
    choice_mode = {"single": "（单选）", "multiple": "（多选）"}.get(question.get("choice_mode"), "")
    heading = r"\noindent\textbf{第 " + str(index) + " 题" + choice_mode + score + r"}\quad "
    if question.get("layout", {}).get("mode") == "right_panel":
        lines = _source_panel_tex(question, body, heading, asset_files)
    elif question.get("representation") == "facsimile":
        lines = [r"\noindent\textbf{第 " + str(index) + " 题" + choice_mode + score + r"}",
                 *_asset_tex(question, asset_files)]
    else:
        lines = _structured_tex(question, body, heading, asset_files)
    lines += [r"\par\nobreak{\footnotesize\raggedright 来源："
              + _source_tex(question["source"], original_number) + r"\par}"]
    if "answer" in question:
        label = "模型解答（非原卷答案）" if question["answer"]["origin"] == "model" else "资料答案"
        lines.append(r"\par\nobreak\textbf{" + label + r"}：" + _block_tex(question["answer"]["body"]) + r"\par")
    return lines


def _writing_space(question):
    """Writing room follows the keep-together box: kept mid-page, dropped at a page break."""
    if "answer" in question:
        return None
    return "9mm" if question["choices"] else "30mm"


def _document_header(spec, title):
    doc = spec["document"]
    lines = [r"\documentclass[UTF8,a4paper,11pt,fontset=fandol]{ctexart}",
             # xeCJK otherwise sends circled 1..10 to Latin Modern, which has no
             # glyphs for them. The pinned Fandol fonts cover exactly this range.
             r'\xeCJKDeclareCharClass{CJK}{"2160->"216B,"2460->"2469}',
             (r"\usepackage{amsmath,amssymb,graphicx}" if spec["schema_version"] != "1.0"
              else r"\usepackage{amsmath,amssymb}"),
             r"\usepackage[margin=20mm,headheight=14pt]{geometry}",
             r"\setlength{\parindent}{0pt}", r"\setlength{\parskip}{4pt}",
             r"\setlength{\emergencystretch}{2em}", r"\linespread{1.18}",
             r"\newlength{\choicewidth}\newlength{\choicemax}\newlength{\choicecell}",
             r"\newlength{\choicegap}\setlength{\choicegap}{1.5em}",
             r"\newsavebox{\questionbox}", r"\pagestyle{plain}",
             r"\widowpenalty=10000", r"\clubpenalty=10000",
             *([r"\usepackage{tabularx,array,needspace}",
                r"\newsavebox{\layoutstacked}\newsavebox{\layoutside}\newsavebox{\layoutsideall}\newsavebox{\choicerow}",
                r"\newlength{\layoutbest}"] if spec["schema_version"] in {"1.2", "1.3"} else []), r"\begin{document}",
             r"\begin{center}{\LARGE\bfseries " + escape_text(title) + r"}\par"]
    if doc["subtitle"]:
        lines.append(r"{\small " + escape_text(doc["subtitle"]) + r"}\par")
    return lines + [r"\end{center}"]


def _tex(spec, asset_files=None):
    asset_files = asset_files or {}
    lines = _document_header(spec, spec["document"]["title"])
    lines += [r"\noindent 姓名：\underline{\makebox[28mm]{}}\hfill 班级：\underline{\makebox[28mm]{}}\hfill 日期：\underline{\makebox[32mm]{}}\par",
              r"\vspace{1mm}\hrule\vspace{3mm}"]
    for index, question in enumerate(spec["questions"], 1):
        if spec["schema_version"] == "1.3" and "answer" in question:
            question = {key: value for key, value in question.items() if key != "answer"}
        content = _question_tex(question, index, asset_files)
        space = _writing_space(question)
        # Measure short questions with their sources. A tall question is emitted
        # normally so it can span pages instead of clipping.
        lines += [r"\par\addvspace{3mm}",
                  *([r"\Needspace{6\baselineskip}"] if spec["schema_version"] in {"1.2", "1.3"} else []),
                  r"\begin{lrbox}{\questionbox}\begin{minipage}{\linewidth}", *content,
                  r"\end{minipage}\end{lrbox}",
                  r"\ifdim\dimexpr\ht\questionbox+\dp\questionbox\relax<.68\textheight",
                  r"\noindent\usebox{\questionbox}\par", r"\else", *content, r"\fi",
                  *([r"\par\vspace{" + space + "}"] if space else [])]
    lines += [r"\end{document}", ""]
    return "\n".join(lines)


def _answer_groups(spec):
    choices, others = [], []
    for index, question in enumerate(spec["questions"], 1):
        (choices if source_choice_labels(question, question["choice_mode"]) else others).append((index, question))
    return choices, others


def _choice_answer_cell(question):
    if "answer" not in question:
        return "原卷无答案"
    return question["answer"]["key"] or "见解析"


def _answer_table_chunks(choices):
    # Long multi-select keys need more horizontal room than A-D keys.
    columns = 4 if any(len(q.get("answer", {}).get("key") or "") > 7 for _, q in choices) else 6
    for start in range(0, len(choices), columns):
        yield choices[start:start + columns]


def _answer_reference(text, original_number, index):
    # Only replace an unambiguous reference to this question's figure. A
    # reference to any other original number or a bare number stays verbatim.
    pattern = r"第\s*" + re.escape(original_number) + r"(?P<part>\s*[-－]\s*(?:III|II|I|Ⅲ|Ⅱ|Ⅰ))?\s*题\s*图"
    return re.sub(pattern, lambda match: "第" + str(index) + (match["part"] or "") + "题图", text)


def _answer_tex(spec, asset_files):
    lines = _document_header(spec, spec["document"]["title"] + "·参考答案")
    choices, others = _answer_groups(spec)
    if choices:
        lines.append(r"\section*{一、选择题}")
        for group in _answer_table_chunks(choices):
            columns = r"|l|*{" + str(len(group)) + r"}{>{\centering\arraybackslash}X|}"
            labels = [f"第 {index} 题" for index, _ in group]
            keys = [_choice_answer_cell(q) for _, q in group]
            lines += [r"\Needspace{4\baselineskip}", r"\renewcommand{\arraystretch}{1.45}",
                      r"\begin{tabularx}{\linewidth}{" + columns + r"}\hline",
                      " & ".join(["题号", *labels]) + r" \\ \hline",
                      " & ".join(["答案", *keys]) + r" \\ \hline",
                      r"\end{tabularx}\par"]
    choice_details = [(index, q) for index, q in choices if q.get("answer", {}).get("body")]
    if choice_details:
        lines.append(r"\subsection*{选择题解析}")
    for entries, section in ((choice_details, None), (others, "二、实验题与计算题" if choices else "实验题与计算题")):
        if entries and section:
            lines.append(r"\section*{" + section + "}")
        for index, question in entries:
            original = question["original_number"]
            heading = f"第 {index} 题（来源：{question['source']['title']} 原第{original}题）"
            lines += [r"\par\addvspace{3mm}\Needspace{3\baselineskip}",
                      r"\noindent\textbf{" + escape_text(heading) + r"}\par\nobreak"]
            if "answer" not in question:
                lines.append(r"原卷无答案\par")
                continue
            answer = question["answer"]
            assets = {asset["asset_id"]: asset for asset in answer["assets"]}
            for block in answer["body"]:
                kind = block["type"]
                if kind == "figure":
                    aid = block["asset_id"]
                    width = assets[aid]["width_mm"]
                    lines.append(r"\par\begin{center}\begin{minipage}{\linewidth}\centering\includegraphics[width=" + f"{width:g}"
                                 + r"mm,height=.62\textheight,keepaspectratio]{" + asset_files[aid] + "}")
                    if "caption" in block:
                        caption = _answer_reference(block["caption"], original, index)
                        lines.append(r"\par{\small " + escape_text(caption) + "}")
                    lines.append(r"\end{minipage}\end{center}")
                elif kind in {"text", "score"}:
                    lines.append(escape_text(_answer_reference(block["value"], original, index)))
                else:
                    lines.append(_inline_tex(block))
    return "\n".join([*lines, r"\end{document}", ""])


def _pdf_image_pixels(pdf, xref, source):
    import fitz

    pixmap = fitz.Pixmap(pdf, xref)
    kind, mask = pdf.xref_get_key(xref, "SMask")
    if kind == "xref":
        pixmap = fitz.Pixmap(pixmap, fitz.Pixmap(pdf, int(mask.split()[0])))
    expected = fitz.Pixmap(str(source))
    if expected.alpha and not pixmap.alpha:
        # An all-opaque PNG may be embedded without a separate alpha mask.
        if expected.samples[expected.n - 1::expected.n] != b"\xff" * (expected.width * expected.height):
            raise ContractError("pdf-qa-failed: answer image alpha lost")
        expected = fitz.Pixmap(expected, 0)
    if pixmap.colorspace != expected.colorspace:
        pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        expected = fitz.Pixmap(fitz.csRGB, expected)
    if (pixmap.width, pixmap.height, pixmap.n, pixmap.samples) != (
            expected.width, expected.height, expected.n, expected.samples):
        raise ContractError("pdf-qa-failed: answer image pixels changed")


def inspect_pdf(path, spec, asset_metadata=None, *, answer_files=None):
    """Geometry/text QA cannot establish physics correctness or visual perfection."""
    try:
        import fitz
    except ImportError as exc:
        raise ContractError("pdf-qa-unavailable: PyMuPDF is required") from exc
    try:
        with fitz.open(path) as pdf:
            maximum_pages=max(100,4*len(spec['questions']))
            if pdf.is_encrypted or not 1 <= len(pdf) <= maximum_pages:
                raise ContractError("pdf-qa-failed: invalid page count or encryption")
            page_texts = [page.get_text() for page in pdf]
            if any(not text.strip() for text in page_texts):
                raise ContractError("pdf-qa-failed: blank page/text")
            # Cross-page text: drop standalone numeric page footers so a Chinese
            # substring that continues on the next page still matches once joined.
            text = re.sub(r"\s+", "", "".join(_strip_page_numbers(page_texts)))
            if any(char in text for char in "\ufffd\u25a1\u25a0"):
                raise ContractError("pdf-qa-failed: replacement/tofu glyph in extracted text")
            answer_sheet = answer_files is not None
            if answer_sheet:
                groups = _answer_groups(spec)
                ordered_questions = [q for group in groups for _, q in group]
                for group in _answer_table_chunks(groups[0]):
                    labels = "".join(f"第{index}题" for index, _ in group)
                    keys = "".join(_choice_answer_cell(q) for _, q in group)
                    if f"题号{labels}答案{keys}" not in text:
                        raise ContractError("pdf-qa-failed: answer table mismatch")
                if text.count("原卷无答案") < sum("answer" not in q for q in ordered_questions):
                    raise ContractError("pdf-qa-failed: missing-answer marker absent")
            else:
                ordered_questions = spec["questions"]
            for index, question in enumerate(spec["questions"], 1):
                if f"第{index}题" not in text:
                    raise ContractError(f"pdf-qa-failed: missing question marker {index}")
                # Each body block contributes a Chinese snippet when present.
                blocks = question.get("answer", {}).get("body", []) if answer_sheet else _display_body(question)[0]
                for block in blocks:
                    if block["type"] not in {"text", "score"}:
                        continue
                    match = re.search(r"[\u4e00-\u9fff]{2,8}", block["value"])
                    if match and match.group(0) not in text:
                        raise ContractError(f"pdf-qa-failed: Chinese text missing for {question['id']}")
            image_placements = []
            for page_number, page in enumerate(pdf, 1):
                if not 400 <= page.rect.width <= 650 or not 650 <= page.rect.height <= 950:
                    raise ContractError("pdf-qa-failed: unexpected page geometry")
                # Actually rasterize at low resolution; malformed page objects fail here.
                page.get_pixmap(matrix=fitz.Matrix(0.3, 0.3), alpha=False)
                for image in page.get_image_info(xrefs=True):
                    for rect in [fitz.Rect(image["bbox"])]:
                        if rect.is_empty or rect.x0 < 0 or rect.y0 < 0 or rect.x1 > page.rect.width or rect.y1 > page.rect.height:
                            raise ContractError("pdf-qa-failed: image outside page")
                        image_placements.append({"page": page_number,
                                                 "bbox": [round(value, 3) for value in rect],
                                                 "width": round(rect.width, 3), "height": round(rect.height, 3),
                                                 **({"xref": image["xref"]} if answer_sheet else {})})
            expected_assets = [asset for question in ordered_questions
                               for asset in (question.get("answer", {}) if answer_sheet else question).get("assets", [])]
            if len(image_placements) != len(expected_assets):
                raise ContractError("pdf-qa-failed: embedded image count mismatch")
            verified_assets = []
            for asset, placement in zip(expected_assets, image_placements):
                metadata = (asset_metadata or {}).get(asset["asset_id"], {})
                source_ratio = metadata.get("width", 0) / metadata.get("height", 1)
                placed_ratio = placement["width"] / placement["height"]
                if source_ratio <= 0 or abs(source_ratio / placed_ratio - 1) > 0.03:
                    raise ContractError("pdf-qa-failed: image aspect ratio changed")
                if answer_sheet:
                    _pdf_image_pixels(pdf, placement.pop("xref"), answer_files[asset["asset_id"]])
                    placement["pixels_match"] = True
                verified_assets.append({"asset_id": asset["asset_id"], "role": asset["role"],
                                        "source_width": metadata.get("width"), "source_height": metadata.get("height"),
                                        **placement, "aspect_ratio_preserved": True})
            return {"passed": True, "page_count": len(pdf), "text_characters": len(text),
                    "all_question_markers_present": True, "chinese_snippets_present": True,
                    "rasterization_passed": True, "embedded_asset_count": len(verified_assets),
                    "asset_placements": verified_assets, "visual_review_required": True,
                    "boundary": "structural/text QA only; not human layout, content, OCR or teaching acceptance"}
    except ContractError:
        raise
    except Exception as exc:
        raise ContractError(f"pdf-qa-failed:{type(exc).__name__}") from exc


def _write_new(path, content):
    with path.open("xb") as handle:
        handle.write(content)


def _stage_assets(spec, staging, asset_reader, *, answers_only=False):
    expected = [asset for question in spec["questions"]
                for asset in (question.get("answer", {}) if answers_only else question).get("assets", [])]
    if not expected:
        return {}, {}
    if asset_reader is None:
        raise ContractError("verified-asset-reader-required")
    target = staging / "verified-assets"
    target.mkdir()
    files, metadata = {}, {}
    try:
        import fitz
    except ImportError as exc:
        raise ContractError("asset-qa-unavailable: PyMuPDF is required") from exc
    for asset in expected:
        asset_id = asset["asset_id"]
        if asset_id in files:
            continue
        value = asset_reader.read(asset_id)
        if not isinstance(value, dict) or set(value) != {"bytes", "media_type", "width", "height"}:
            raise ContractError("verified-asset-record-invalid")
        data = value["bytes"]
        if not isinstance(data, bytes) or hashlib.sha256(data).hexdigest() != asset_id:
            raise ContractError("verified-asset-hash-mismatch")
        if value["media_type"] not in {"image/png", "image/jpeg"}:
            raise ContractError("verified-asset-media-type-forbidden")
        if len(data) > 8 * 1024 * 1024 or type(value["width"]) is not int or type(value["height"]) is not int:
            raise ContractError("verified-asset-size-invalid")
        if value["width"] * value["height"] > 20_000_000 or max(value["width"], value["height"]) > 10_000:
            raise ContractError("verified-asset-pixel-limit")
        ratio = max(value["width"] / value["height"], value["height"] / value["width"])
        if ratio > 20:
            raise ContractError("verified-asset-readable-aspect-limit")
        try:
            pixmap = fitz.Pixmap(data)
        except Exception as exc:
            raise ContractError("verified-asset-decode-failed") from exc
        if (pixmap.width, pixmap.height) != (value["width"], value["height"]):
            raise ContractError("verified-asset-dimension-mismatch")
        extension = ".png" if value["media_type"] == "image/png" else ".jpg"
        path = target / (asset_id + extension)
        _write_new(path, data)
        files[asset_id] = "verified-assets/" + path.name
        metadata[asset_id] = {key: value[key] for key in ("media_type", "width", "height")} | {"bytes": len(data), "sha256": asset_id}
    return files, metadata


def render_exam(exam_spec, output_dir, *, compiler=None, asset_reader=None, answers_only=False):
    """Render once into an artifact-free output directory; retain failure evidence.

    exam_spec accepts a JSON path or a dict. The CLI additionally confines paths
    to its working directory. Core callers must provide an authorized output dir.
    """
    started = time.monotonic()
    out = Path(output_dir).absolute()
    result = RenderResult("failed", warnings=[])
    names = ANSWER_ARTIFACT_NAMES if answers_only else ARTIFACT_NAMES
    pdf_name, tex_name, log_name, manifest_name, rejected_name = names
    if out.is_symlink() or any((out / name).exists() or (out / name).is_symlink() for name in names):
        result.error = "output-exists: choose a new directory; existing artifacts are never overwritten"
        return result
    try:
        out.mkdir(parents=True, exist_ok=True)
        out = out.resolve()
    except OSError as exc:
        result.error = f"output-unavailable:{type(exc).__name__}"
        return result
    manifest = {"extension_version": __version__, "schema_version": "1.0", "status": "failed",
                "created_at": datetime.now(timezone.utc).isoformat(), "warnings": result.warnings,
                "artifacts": {}, "exam_spec": None, "input_sha256": None}
    compiler = compiler or XeLaTeXCompiler()
    copied = []
    try:
        if isinstance(exam_spec, dict):
            raw = exam_spec
            manifest["input_sha256"] = hashlib.sha256(json.dumps(raw, ensure_ascii=False, sort_keys=True,
                                                               separators=(",", ":"), allow_nan=False).encode()).hexdigest()
            manifest["input_hash_encoding"] = "canonical-json-utf8"
        else:
            raw, manifest["input_sha256"] = load_spec(exam_spec)
            manifest["input_hash_encoding"] = "exact-file-bytes"
        spec, warnings = validate_spec(raw)
        result.warnings.extend(warnings)
        manifest["exam_spec"] = spec
        if answers_only and (spec["schema_version"] != "1.3" or not spec["document"]["include_answers"]):
            raise ContractError("answer-sheet-requires-source-answers-spec")
        with tempfile.TemporaryDirectory(prefix=".latex-job-", dir=out) as work:
            staging = Path(work)
            asset_files, asset_metadata = _stage_assets(spec, staging, asset_reader, answers_only=answers_only)
            manifest["verified_assets"] = asset_metadata
            tex_path = staging / "worksheet.tex"
            tex = _answer_tex(spec, asset_files) if answers_only else _tex(spec, asset_files)
            tex_path.write_text(tex, encoding="utf-8", newline="\n")
            compile_started = time.monotonic()
            compiled = compiler.compile(tex_path, staging)
            manifest["timings_ms"] = {"compile": round((time.monotonic() - compile_started) * 1000, 2)}
            result.compiler, result.compiler_version = compiled.compiler, compiled.compiler_version
            manifest["compiler"] = {"name": compiled.compiler, "version": compiled.compiler_version,
                                    "shell_escape": False, "network_policy": "enforced by deployment runner"}
            for internal, name in (("worksheet.tex", tex_name), ("compile.log", log_name)):
                if (staging / internal).is_file():
                    _write_new(out / name, (staging / internal).read_bytes())
                    copied.append(name)
            result.tex_path = str(out / tex_name) if tex_name in copied else None
            result.log_path = str(out / log_name) if log_name in copied else None
            if not compiled.success:
                partial_pdf = staging / "worksheet.pdf"
                if partial_pdf.is_file() and partial_pdf.stat().st_size <= 128 * 1024 * 1024:
                    _write_new(out / rejected_name, partial_pdf.read_bytes())
                    copied.append(rejected_name)
                raise ContractError(compiled.error or "compiler-failed")
            if not (staging / "worksheet.pdf").is_file():
                raise ContractError("compiler-produced-no-pdf")
            if (staging / "worksheet.pdf").stat().st_size > 128 * 1024 * 1024:
                raise ContractError("pdf-size-limit: 128 MiB exceeded")
            qa_started = time.monotonic()
            try:
                answer_files = {aid: staging / file for aid, file in asset_files.items()} if answers_only else None
                manifest["pdf_qa"] = inspect_pdf(staging / "worksheet.pdf", spec, asset_metadata, answer_files=answer_files)
                manifest["timings_ms"]["qa"] = round((time.monotonic() - qa_started) * 1000, 2)
            except ContractError as exc:
                manifest["pdf_qa"] = {"passed": False, "error": str(exc)}
                _write_new(out / rejected_name, (staging / "worksheet.pdf").read_bytes())
                copied.append(rejected_name)
                raise
            if result.log_path:
                log = Path(result.log_path).read_text(encoding="utf-8", errors="replace")
                if "Overfull \\hbox" in log or "Overfull \\vbox" in log:
                    result.warnings.append("layout-overfull: inspect compile.log and rendered pages before acceptance")
            _write_new(out / pdf_name, (staging / "worksheet.pdf").read_bytes())
            copied.append(pdf_name)
            result.pdf_path = str(out / pdf_name)
        result.status = "success"
        manifest["status"] = "success"
    except Exception as exc:
        result.error = str(exc) if isinstance(exc, (ValueError, OSError)) else f"renderer-error:{type(exc).__name__}:{exc}"
        manifest["error"] = result.error
        if result.log_path is None and not (out / log_name).exists():
            _write_new(out / log_name, ("Render failed: " + result.error + "\n").encode("utf-8"))
            copied.append(log_name)
            result.log_path = str(out / log_name)
    manifest["elapsed_seconds"] = round(time.monotonic() - started, 3)
    for name in copied:
        data = (out / name).read_bytes()
        manifest["artifacts"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    _write_new(out / manifest_name, (json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    result.manifest_path = str(out / manifest_name)
    return result
