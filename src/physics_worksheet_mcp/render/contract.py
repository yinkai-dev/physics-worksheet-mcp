"""ExamSpec contract. Validation is structural, never an OCR truth claim."""
import hashlib
import json
import math
import re
from pathlib import Path

from .safety import SafetyError, validate_math

MAX_INPUT_BYTES = 8 * 1024 * 1024


class ContractError(ValueError):
    pass


def _keys(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ContractError(f"{label}: expected object")
    missing, extra = set(required) - value.keys(), value.keys() - set(allowed)
    if missing or extra:
        raise ContractError(f"{label}: missing={sorted(missing)}, unknown={sorted(extra)}")


def _text(value, label, maximum, allow_empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value.strip()):
        raise ContractError(f"{label}: expected {'0' if allow_empty else '1'}..{maximum} characters")
    if any((ord(char) < 32 and char != "\n") or ord(char) == 127 for char in value):
        raise ContractError(f"{label}: control characters forbidden")
    return value


def _source(raw, label, warnings):
    _keys(raw, {"kb_name", "title", "page", "chunk_id", "score", "source_excerpt", "content_sha256"},
          {"kb_name", "title", "chunk_id", "source_excerpt", "content_sha256"}, label)
    result = {name: _text(raw[name], f"{label}.{name}", maximum)
              for name, maximum in (("kb_name", 300), ("title", 500), ("chunk_id", 500), ("source_excerpt", 30000))}
    digest = raw["content_sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        raise ContractError(f"{label}.content_sha256: expected SHA-256 hex")
    if hashlib.sha256(result["source_excerpt"].encode("utf-8")).hexdigest() != digest.lower():
        raise ContractError(f"{label}: hash-mismatch against exact source_excerpt UTF-8")
    result["content_sha256"] = digest.lower()
    page = raw.get("page")
    if page is not None and (type(page) is not int or page < 1 or page > 100000):
        raise ContractError(f"{label}.page: positive integer or null required")
    result["page"] = page
    if page is None:
        warnings.append(f"page-unknown:{label}; page remains null")
    score = raw.get("score")
    if score is not None and (type(score) not in (int, float) or not math.isfinite(score)):
        raise ContractError(f"{label}.score: finite number or null required")
    result["score"] = score
    return result


def _blocks(raw, label, *, allow_empty=False, rich=False):
    minimum = 0 if allow_empty else 1
    maximum = 400 if rich else 80
    if not isinstance(raw, list) or not minimum <= len(raw) <= maximum:
        raise ContractError(f"{label}: expected {minimum}..{maximum} content blocks")
    blocks = []
    for index, block in enumerate(raw):
        if rich and isinstance(block, dict) and block.get("type") == "figure":
            _keys(block, {"type", "asset_id", "caption"}, {"type", "asset_id"}, label)
            if not isinstance(block["asset_id"], str) or not re.fullmatch(r"[a-f0-9]{64}", block["asset_id"]):
                raise ContractError(f"{label}: invalid figure reference")
            figure = dict(block)
            if "caption" in block:
                figure["caption"] = _blocks(block["caption"], label + ".caption", allow_empty=True)
            blocks.append(figure)
            continue
        if rich and isinstance(block, dict) and block.get("type") == "table":
            _keys(block, {"type", "rows"}, {"type", "rows"}, label)
            rows = block["rows"]
            if not isinstance(rows, list) or not 1 <= len(rows) <= 30:
                raise ContractError(f"{label}: table row limit")
            columns = len(rows[0]) if isinstance(rows[0], list) else 0
            if not 1 <= columns <= 12 or any(not isinstance(row, list) or len(row) != columns for row in rows):
                raise ContractError(f"{label}: rectangular table required")
            blocks.append({"type": "table", "rows": [[_blocks(cell, label + ".cell", allow_empty=True)
                                                       for cell in row] for row in rows]})
            continue
        if isinstance(block, dict) and block.get("type") == "image":
            raise ContractError(f"{label}[{index}]: unsupported-image; do not omit required diagrams")
        _keys(block, {"type", "value"}, {"type", "value"}, f"{label}[{index}]")
        if block["type"] == "text":
            value = _text(block["value"], label, 8000)
        elif block["type"] == "math":
            try:
                value = validate_math(block["value"])
            except SafetyError as exc:
                raise ContractError(str(exc)) from exc
        else:
            raise ContractError(f"{label}: unsupported block type")
        blocks.append({"type": block["type"], "value": value})
    if (len(json.dumps(blocks, ensure_ascii=False)) > 50000 if rich else sum(len(block["value"]) for block in blocks) > 16000):
        raise ContractError(f"{label}: combined text limit exceeded")
    return blocks


def source_choice_labels(question, mode):
    """Return A-D for typed choices printed inside a structured source figure."""
    if question["choices"]:
        return {chr(65 + index) for index in range(len(question["choices"]))}
    if (mode in {"single", "multiple"} and question.get("representation", "structured") == "structured"
            and any(block["type"] == "figure" for block in question["body"])):
        return set("ABCD")
    return set()


def _answer_blocks(raw, label):
    if not isinstance(raw, list) or len(raw) > 200:
        raise ContractError(f"{label}: expected at most 200 answer blocks")
    blocks = []
    for block in raw:
        if isinstance(block, dict) and block.get("type") == "figure":
            _keys(block, {"type", "asset_id", "caption"}, {"type", "asset_id"}, label)
            if not isinstance(block["asset_id"], str) or not re.fullmatch(r"[a-f0-9]{64}", block["asset_id"]):
                raise ContractError(f"{label}: invalid answer figure reference")
            if "caption" in block:
                _text(block["caption"], label + ".caption", 400)
            blocks.append(dict(block))
        elif isinstance(block, dict) and block.get("type") == "score":
            _keys(block, {"type", "value"}, {"type", "value"}, label)
            blocks.append({"type": "score", "value": _text(block["value"], label, 8000)})
        else:
            blocks.extend(_blocks([block], label))
    if len(json.dumps(blocks, ensure_ascii=False)) > 50000:
        raise ContractError(f"{label}: combined text limit exceeded")
    return blocks


def _assets(raw, label, *, rich=False, answer=False):
    if not isinstance(raw, list) or not 0 <= len(raw) <= 12:
        raise ContractError(f"{label}: expected 0..12 required verified assets")
    result, ids, orders = [], set(), set()
    for index, value in enumerate(raw):
        item = f"{label}[{index}]"
        _keys(value, {"asset_id", "role", "required", "order", "placement"} | ({"width_mm", "source_layout"} if rich else set()),
              {"asset_id", "role", "required", "order", "placement"}, item)
        asset_id = value["asset_id"]
        if not isinstance(asset_id, str) or not re.fullmatch(r"[a-f0-9]{64}", asset_id):
            raise ContractError(f"{item}.asset_id: SHA-256 required")
        if asset_id in ids:
            raise ContractError(f"{item}.asset_id: duplicate asset")
        ids.add(asset_id)
        roles = ("answer_figure",) if answer else ("question_figure", "shared_figure", "full_question_facsimile")
        if value["role"] not in roles:
            raise ContractError(f"{item}.role: unsupported asset role")
        if value["required"] is not True:
            raise ContractError(f"{item}.required: all ExamSpec 1.1 assets must be required")
        if type(value["order"]) is not int or value["order"] < 1 or value["order"] in orders:
            raise ContractError(f"{item}.order: positive unique order required")
        orders.add(value["order"])
        if value["placement"] not in (("after_stem", "replace_question", "in_content") if rich else ("after_stem", "replace_question")):
            raise ContractError(f"{item}.placement: unsupported placement")
        if value["placement"] == "in_content":
            width = value.get("width_mm")
            if type(width) not in (int, float) or not math.isfinite(width) or not 5 <= width <= 170:
                raise ContractError(f"{item}.width_mm: expected verified physical width in 5..170 mm")
        if "source_layout" in value:
            layout = value["source_layout"]
            _keys(layout, {"page_size_pt", "bbox_pt", "alignment", "row_group", "gap_before_mm", "anchor", "basis"},
                  {"page_size_pt", "bbox_pt", "alignment", "row_group", "gap_before_mm", "anchor", "basis"}, item)
            for field, count in (("page_size_pt", 2), ("bbox_pt", 4)):
                if not isinstance(layout[field], list) or len(layout[field]) != count or any(type(v) not in (int, float) or not math.isfinite(v) for v in layout[field]):
                    raise ContractError(f"{item}: invalid source layout geometry")
            pw, ph = layout["page_size_pt"]
            x0, y0, x1, y1 = layout["bbox_pt"]
            if not (0 <= x0 < x1 <= pw and 0 <= y0 < y1 <= ph):
                raise ContractError(f"{item}: source layout outside page")
            if layout["alignment"] not in ("left", "center", "right"):
                raise ContractError(f"{item}: invalid layout alignment")
            if type(layout["gap_before_mm"]) not in (int, float) or not 0 <= layout["gap_before_mm"] <= 25:
                raise ContractError(f"{item}: invalid layout gap")
            for field in ("row_group", "anchor", "basis"):
                _text(layout[field], item + "." + field, 500)
        result.append(dict(value))
    if sorted(orders) != list(range(1, len(result) + 1)):
        raise ContractError(f"{label}: contiguous asset order required")
    return sorted(result, key=lambda item: item["order"])


def validate_spec(raw):
    """Return a new normalized spec and warnings; never mutate caller data."""
    try:
        if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_INPUT_BYTES:
            raise ContractError("input-too-large: maximum 8 MiB")
    except (TypeError, ValueError) as exc:
        raise ContractError(f"invalid-json-value: {exc}") from exc
    _keys(raw, {"schema_version", "document", "questions"}, {"schema_version", "document", "questions"}, "spec")
    schema_version = raw["schema_version"]
    if schema_version not in ("1.0", "1.1", "1.2", "1.3"):
        raise ContractError("unsupported-schema: expected 1.0, 1.1, 1.2 or 1.3")
    separate_answers = schema_version == "1.3"
    rich = schema_version in ("1.2", "1.3")
    doc = raw["document"]
    _keys(doc, {"title", "subtitle", "include_answers"}, {"title"}, "document")
    document = {"title": _text(doc["title"], "document.title", 200),
                "subtitle": _text(doc.get("subtitle", ""), "document.subtitle", 300, True),
                "include_answers": doc.get("include_answers", False)}
    if type(document["include_answers"]) is not bool:
        raise ContractError("document.include_answers: boolean required")
    maximum_questions = 1000 if rich else 50
    if not isinstance(raw["questions"], list) or not 1 <= len(raw["questions"]) <= maximum_questions:
        raise ContractError(f"questions: expected 1..{maximum_questions} questions")
    warnings, questions, seen = [], [], set()
    for index, raw_q in enumerate(raw["questions"]):
        label = f"questions[{index}]"
        allowed = {"id", "body", "choices", "choice_mode", "points", "source", "review_status", "answer"}
        if schema_version != "1.0":
            allowed |= {"representation", "assets", "original_number"}
        if rich:
            allowed.add("layout")
        _keys(raw_q, allowed, {"id", "body", "source", "review_status"}, label)
        qid = raw_q["id"]
        if not isinstance(qid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", qid):
            raise ContractError(f"{label}.id: invalid identifier")
        if qid in seen:
            raise ContractError(f"{label}.id: duplicate identifier")
        seen.add(qid)
        if raw_q["review_status"] != "retrieved-complete":
            raise ContractError(f"{label}: needs-review or unsupported review status; incomplete questions cannot render")
        representation = raw_q.get("representation", "structured") if schema_version != "1.0" else "structured"
        if representation not in ("structured", "facsimile"):
            raise ContractError(f"{label}.representation: structured or facsimile required")
        question = {"id": qid, "body": _blocks(raw_q["body"], f"{label}.body", allow_empty=representation == "facsimile", rich=rich),
                    "source": _source(raw_q["source"], f"{label}.source", warnings),
                    "review_status": "retrieved-complete"}
        choices = raw_q.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 10:
            raise ContractError(f"{label}.choices: list of at most 10 text strings or block lists required")
        question["choices"] = [(_text(choice, f"{label}.choices[{choice_index}]", 2000)
                                if isinstance(choice, str)
                                else _blocks(choice, f"{label}.choices[{choice_index}]", rich=rich))
                               for choice_index, choice in enumerate(choices)]
        if schema_version != "1.0":
            assets = _assets(raw_q.get("assets", []), f"{label}.assets", rich=rich)
            if representation == "facsimile":
                if question["body"] or question["choices"]:
                    raise ContractError(f"{label}: facsimile must not duplicate structured body or choices")
                if (not assets or any(asset["role"] != "full_question_facsimile"
                                      or asset["placement"] != "replace_question" for asset in assets)):
                    raise ContractError(f"{label}: facsimile requires ordered full_question_facsimile replacement assets")
            elif any(asset["role"] == "full_question_facsimile" or asset["placement"] not in (("after_stem", "in_content") if rich else ("after_stem",)) for asset in assets):
                raise ContractError(f"{label}: structured questions accept only non-facsimile after_stem assets")
            references = [block["asset_id"] for blocks in [question["body"], *[c for c in question["choices"] if isinstance(c, list)]]
                          for block in blocks if block["type"] == "figure"]
            inline_ids = [asset["asset_id"] for asset in assets if asset["placement"] == "in_content"]
            if references != inline_ids:
                raise ContractError(f"{label}: figure references must match assets exactly once in reading order")
            question["representation"] = representation
            question["assets"] = assets
            original_number = raw_q.get("original_number")
            if not isinstance(original_number, str) or not re.fullmatch(r"[1-9][0-9]{0,3}", original_number):
                raise ContractError(f"{label}.original_number: required original question number")
            question["original_number"] = original_number
            if "layout" in raw_q:
                layout = raw_q["layout"]
                _keys(layout, {"mode", "start_block", "right_asset_ids", "gap_mm"},
                      {"mode", "start_block", "right_asset_ids", "gap_mm"}, label + ".layout")
                if layout["mode"] != "right_panel" or representation != "structured":
                    raise ContractError(f"{label}: unsupported question layout")
                if type(layout["start_block"]) is not int or not 0 <= layout["start_block"] < len(question["body"]):
                    raise ContractError(f"{label}: invalid layout anchor")
                ids = layout["right_asset_ids"]
                if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or not set(ids) <= set(inline_ids):
                    raise ContractError(f"{label}: invalid layout assets")
                ordered = [b["asset_id"] for b in question["body"][layout["start_block"]:] if b["type"] == "figure" and b["asset_id"] in ids]
                if ordered != ids:
                    raise ContractError(f"{label}: layout must preserve figure order and anchor")
                if type(layout["gap_mm"]) not in (int, float) or not 2 <= layout["gap_mm"] <= 12:
                    raise ContractError(f"{label}: invalid panel gap")
                # A side column must leave at least 70 mm for readable text.
                widths = [a["width_mm"] for a in assets if a["asset_id"] in ids]
                if max(widths) + layout["gap_mm"] > 100:
                    raise ContractError(f"{label}: source panel leaves insufficient text width")
                question["layout"] = dict(layout)
        choice_mode = raw_q.get("choice_mode", "unspecified")
        if choice_mode not in ("single", "multiple", "unspecified"):
            raise ContractError(f"{label}.choice_mode: single, multiple or unspecified required")
        labels = source_choice_labels(question, choice_mode) if separate_answers else set()
        if choice_mode != "unspecified" and not choices and not labels and representation != "facsimile":
            raise ContractError(f"{label}.choice_mode: a choice mode requires choices")
        question["choice_mode"] = choice_mode
        points = raw_q.get("points")
        if points is not None and (type(points) not in (int, float) or not math.isfinite(points) or not 0 <= points <= 1000):
            raise ContractError(f"{label}.points: finite 0..1000 number or null required")
        question["points"] = points
        if separate_answers and "answer" in raw_q and document["include_answers"]:
            answer = raw_q["answer"]
            fields = {"origin", "key", "body", "assets"}
            _keys(answer, fields, fields, f"{label}.answer")
            body = _answer_blocks(answer["body"], f"{label}.answer.body")
            assets = _assets(answer["assets"], f"{label}.answer.assets", rich=True, answer=True)
            key = answer["key"]
            if answer["origin"] != "source":
                raise ContractError(f"{label}.answer: original source required")
            if labels and key is not None:
                if (not isinstance(key, str) or not key or key != "".join(sorted(set(key)))
                        or not set(key) <= labels
                        or (choice_mode == "single" and len(key) != 1)):
                    raise ContractError(f"{label}.answer: invalid choice key")
            elif key is not None or not body:
                raise ContractError(f"{label}.answer: body required when no choice key is available")
            if (any(a["placement"] != "in_content" or "source_layout" in a for a in assets)
                    or [b["asset_id"] for b in body if b["type"] == "figure"] != [a["asset_id"] for a in assets]):
                raise ContractError(f"{label}.answer: figure references must match assets in order")
            question["answer"] = {"origin": "source", "key": key, "body": body, "assets": assets}
        elif "answer" in raw_q and document["include_answers"]:
            answer = raw_q["answer"]
            _keys(answer, {"origin", "body", "source"}, {"origin", "body"}, f"{label}.answer")
            if answer["origin"] not in ("model", "source"):
                raise ContractError(f"{label}.answer.origin: model or source required")
            normalized_answer = {"origin": answer["origin"], "body": _blocks(answer["body"], f"{label}.answer.body")}
            if answer["origin"] == "source":
                normalized_answer["source"] = _source(answer.get("source"), f"{label}.answer.source", warnings)
            else:
                if "source" in answer:
                    raise ContractError(f"{label}.answer: model answer cannot claim source identity")
                warnings.append(f"model-answer:{qid}; not an original-paper answer")
            question["answer"] = normalized_answer
        elif "answer" in raw_q:
            warnings.append(f"answer-omitted:{qid}; include_answers defaults false")
        elif document["include_answers"]:
            warnings.append(f"answer-missing:{qid}; no answer invented")
        questions.append(question)
    warnings.append("provenance-boundary: hashes verify supplied excerpts; retrieval identity, completeness and physics require human/source review")
    return {"schema_version": schema_version, "document": document, "questions": questions}, warnings


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate-json-key: {key}")
        result[key] = value
    return result


def load_spec(path):
    path = Path(path)
    with path.open("rb") as handle:
        data = handle.read(MAX_INPUT_BYTES + 1)
    if len(data) > MAX_INPUT_BYTES:
        raise ContractError("input-too-large: maximum 8 MiB")
    try:
        raw = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                         parse_constant=lambda token: (_ for _ in ()).throw(ContractError(f"invalid-json-number:{token}")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid-json: {exc}") from exc
    return raw, hashlib.sha256(data).hexdigest()
