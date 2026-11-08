"""Compact figure layout: display-only grouping, side panels and writing room."""
import copy
import hashlib
import unittest

from physics_worksheet_mcp.render.contract import validate_spec
from physics_worksheet_mcp.render.renderer import _block_tex, _figure_segments, _renumber_captions, _tex
from test_contract import valid_spec


def figure(letter):
    return {"type": "figure", "asset_id": letter * 64}


def asset(letter, order, width, alignment="center", row="row1", gap=0):
    return {"asset_id": letter * 64, "role": "question_figure", "required": True, "order": order,
            "placement": "in_content", "width_mm": width,
            "source_layout": {"page_size_pt": [595, 842], "bbox_pt": [10, 10, 100, 100], "alignment": alignment,
                              "row_group": row, "gap_before_mm": gap, "anchor": "fixture", "basis": "fixture"}}


def spec(body, assets, choices=None, original="17"):
    raw = valid_spec()
    raw["schema_version"] = "1.2"
    question = raw["questions"][0]
    question.update(body=body, assets=assets, original_number=original, representation="structured")
    if choices is not None:
        question["choices"] = choices
    text = "".join(block.get("value", "") for block in body if block["type"] == "text")
    question["source"]["source_excerpt"] = text
    question["source"]["content_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    normalized, _ = validate_spec(raw)
    return normalized


def files(*letters):
    return {letter * 64: f"verified-assets/{letter}.png" for letter in letters}


class FigureGroupTests(unittest.TestCase):
    def test_labels_between_figures_keep_one_source_row(self):
        blocks = [figure("a"), {"type": "text", "value": "\n甲\n"}, figure("b"),
                  {"type": "text", "value": "\n乙\n第6题图\n"}, figure("c"), {"type": "text", "value": "\n丙\n"}]
        original = copy.deepcopy(blocks)
        assets = [asset("a", 1, 25), asset("b", 2, 20, gap=13), asset("c", 3, 22, gap=12)]
        tex = _block_tex(blocks, assets, files("a", "b", "c"))
        self.assertEqual(tex.count(r"\noindent\begin{minipage}{\dimexpr\linewidth-\leftskip\relax}"), 1)
        self.assertLess(tex.index("a.png"), tex.index("甲"))
        self.assertLess(tex.index("甲"), tex.index("b.png"))
        self.assertLess(tex.index("c.png"), tex.index("第6题图"), "group caption sits under the row")
        self.assertNotIn(r"\par" + "\n乙", tex, "label lines leave the text flow")
        self.assertEqual(blocks, original)

    def test_label_run_after_consecutive_figures_names_each_figure_in_order(self):
        segments = _figure_segments([figure("a"), figure("b"), {"type": "text", "value": "\n图甲\n图乙\n"}])
        self.assertEqual([labels for _, labels in segments[0][1]], [["图甲"], ["图乙"]])
        segments = _figure_segments([figure("a"), figure("b"), figure("c"), {"type": "text", "value": "\n图2\n图3\n"}])
        self.assertEqual([labels for _, labels in segments[0][1]], [[], ["图2"], ["图3"]])

    def test_numbered_line_after_lettered_labels_captions_the_set(self):
        segments = _figure_segments([figure("a"), figure("b"), figure("c"),
                                     {"type": "text", "value": "\n甲\n乙\n丙\n第15题图2\n"}])
        self.assertEqual([labels for _, labels in segments[0][1]], [["甲"], ["乙"], ["丙"]])
        self.assertEqual(segments[0][2], ["第15题图2"])
        segments = _figure_segments([figure("a"), figure("b"), {"type": "text", "value": "\n第 17 题图甲\n第17题图乙"}])
        self.assertEqual([labels for _, labels in segments[0][1]], [["第 17 题图甲"], ["第17题图乙"]])

    def test_shared_chinese_caption_covers_lettered_panels(self):
        blocks = [figure("a"), {"type": "text", "value": "\n(a)\n"}, figure("b"),
                  {"type": "text", "value": "\n甲\n(b)\n"}, figure("c"), {"type": "text", "value": "\n乙\n"}]
        original = copy.deepcopy(blocks)
        segments = _figure_segments(blocks)
        self.assertEqual([labels for _, labels in segments[0][1]], [["(a)"], ["(b)"], ["乙"]])
        self.assertEqual(segments[0][2], ["甲"])
        self.assertEqual(blocks, original)

    def test_detached_caption_suffix_moves_without_reordering_plain_text(self):
        blocks = [figure("a"), {"type": "text", "value": "\n\n原卷公共参数：重力加速度取"},
                  {"type": "math", "value": "10"}, {"type": "text", "value": "。\n第4题图\n"}]
        original = copy.deepcopy(blocks)
        tex = _block_tex(blocks, [asset("a", 1, 40)], files("a"))
        self.assertLess(tex.index("第4题图"), tex.index("原卷公共参数"))
        self.assertIn("原卷公共参数", tex)
        self.assertEqual(blocks, original)
        segments = _figure_segments([figure("a"), figure("b"), {"type": "text", "value": "公共参数\n"},
                                     {"type": "math", "value": "10"},
                                     {"type": "text", "value": "。\n第 13 题 图\n乙\n"}])
        self.assertEqual([labels for _, labels in segments[0][1]], [[], ["乙"]])
        self.assertEqual(segments[0][2], ["第 13 题 图"])

    def test_body_graphic_options_share_a_measured_row(self):
        blocks = [{"type": "text", "value": "（1）下列图像正确的是\nA. "}, figure("a"),
                  {"type": "text", "value": "\nB. "}, figure("b"),
                  {"type": "text", "value": "\nC. "}, figure("c"),
                  {"type": "text", "value": "\nD. "}, figure("d"),
                  {"type": "text", "value": "\n（2）继续作答。"}]
        original = copy.deepcopy(blocks)
        segments = _figure_segments(blocks)
        figure_segments = [segment for segment in segments if segment[0] == "figures"]
        self.assertEqual(len(figure_segments), 1)
        self.assertEqual(len(figure_segments[0][1]), 4)
        tex = _block_tex(blocks, [asset(c, i, 32) for i, c in enumerate("abcd", 1)], files(*"abcd"))
        self.assertIn(r"\sbox\choicerow{", tex)
        self.assertIn("physics-choice-layout:body:graphic-row", tex)
        self.assertEqual(blocks, original)
        prose = _block_tex([{"type": "text", "value": "A. 普通句子不能抽取\n"}, figure("a")],
                           [asset("a", 1, 32)], files("a"))
        self.assertNotIn(r"\sbox\choicerow{", prose)
        self.assertIn("普通句子不能抽取", prose)

    def test_text_after_labels_stays_in_flow_and_breakable(self):
        tex = _block_tex([figure("a"), {"type": "text", "value": "\n丁\n\n原卷公共参数：重力加速度取10。"}],
                         [asset("a", 1, 40)], files("a"))
        self.assertGreater(tex.index("原卷公共参数"), tex.rindex(r"\end{minipage}"))
        tex = _block_tex([figure("a"), {"type": "text", "value": "第17题图甲说明了受力关系，下面继续讨论。"}],
                         [asset("a", 1, 40)], files("a"))
        self.assertGreater(tex.index("说明了受力关系"), tex.rindex(r"\end{minipage}"))

    def test_rows_wrap_by_physical_width_and_source_row(self):
        tex = _block_tex([figure("a"), figure("b"), figure("c")],
                         [asset("a", 1, 30), asset("b", 2, 70), asset("c", 3, 90)], files("a", "b", "c"))
        self.assertEqual(tex.count(r"\begin{minipage}{\dimexpr\linewidth-\leftskip\relax}"), 2)
        tex = _block_tex([figure("a"), figure("b")], [asset("a", 1, 30), asset("b", 2, 30, row="row2")], files("a", "b"))
        self.assertEqual(tex.count(r"\begin{minipage}{\dimexpr\linewidth-\leftskip\relax}"), 2)


class CaptionNumberTests(unittest.TestCase):
    def test_captions_use_the_worksheet_number_without_mutating_source(self):
        blocks = [{"type": "text", "value": "如第17题图所示，参照第5题图。\n"}, figure("a"),
                  {"type": "text", "value": "\n第 17 题图甲\n第17题图乙"}]
        original = copy.deepcopy(blocks)
        shown = _renumber_captions(blocks, 3, "17")
        self.assertEqual(shown[0]["value"], "如第 3 题图所示，参照第5题图。\n")
        self.assertEqual(shown[2]["value"], "\n第 3 题图甲\n第 3 题图乙")
        self.assertEqual(blocks, original)
        # A label-only caption under this question's own figure is renumbered even
        # when the source printed another number.
        self.assertEqual(_renumber_captions([{"type": "text", "value": "\n甲\n第4题图\n"}], 2, "6")[0]["value"],
                         "\n甲\n第 2 题图\n")

    def test_worksheet_prints_new_number_and_keeps_original_in_source_line(self):
        normalized = spec([{"type": "text", "value": "17. 如图所示，小货箱在传送带上运动。\n"}, figure("a"),
                           {"type": "text", "value": "\n第 17 题图\n"}], [asset("a", 1, 120)])
        tex = _tex(normalized, files("a"))
        self.assertIn("第 1 题图", tex)
        self.assertNotIn("第 17 题图", tex)
        self.assertIn(r"原第 \allowbreak{}17 \allowbreak{}题", tex)


class SidePanelTests(unittest.TestCase):
    def test_narrow_figure_set_is_measured_against_stacked_layout(self):
        normalized = spec([{"type": "text", "value": "如图所示，一带电小球在电场中运动，下列说法正确的是\n"}, figure("a"),
                           {"type": "text", "value": "\n第 4 题图\n"}], [asset("a", 1, 45, alignment="right")],
                          choices=["A. 甲", "B. 乙", "C. 丙", "D. 丁"])
        tex = _tex(normalized, files("a"))
        for name in ("layoutstacked", "layoutside", "layoutsideall"):
            self.assertIn(r"\sbox\%s{" % name, tex)
        self.assertIn(r"\typeout{physics-figure-layout:q1:\layoutpick}", tex)
        self.assertIn(r"-8mm\relax}\def\layoutpick{stacked}", tex)
        self.assertIn(r">.6\textheight", tex, "tall questions keep the breakable stacked form")
        self.assertIn(r"\begin{minipage}[t]{120.000mm}", tex)

    def test_wide_or_several_figure_sets_stay_stacked(self):
        wide = spec([{"type": "text", "value": "如图所示的装置。\n"}, figure("a")], [asset("a", 1, 96)])
        self.assertNotIn(r"\sbox\layoutstacked", _tex(wide, files("a")))
        several = spec([{"type": "text", "value": "如图甲所示的装置。\n"}, figure("a"),
                        {"type": "text", "value": "再如图乙所示的装置。\n"}, figure("b")],
                       [asset("a", 1, 30), asset("b", 2, 30, row="row2")])
        self.assertNotIn(r"\sbox\layoutstacked", _tex(several, files("a", "b")))

    def test_graphic_options_share_a_measured_row(self):
        normalized = spec([{"type": "text", "value": "下列图像中正确的是\n"}],
                          [asset(c, i, 32) for i, c in enumerate("abcd", 1)],
                          choices=[[{"type": "text", "value": f"{label}. "}, figure(c)] for label, c in zip("ABCD", "abcd")])
        tex = _tex(normalized, files(*"abcd"))
        self.assertIn(r"\sbox\choicerow{", tex)
        self.assertIn("physics-choice-layout:q1:graphic-row", tex)
        self.assertIn("physics-choice-layout:q1:stacked", tex, "too-wide rows fall back to stacked options")


class WritingSpaceTests(unittest.TestCase):
    def test_writing_room_follows_the_keep_together_box(self):
        raw = valid_spec()
        raw["schema_version"] = "1.2"
        raw["questions"][0]["original_number"] = "1"
        tex = _tex(validate_spec(raw)[0])
        box = tex[tex.index(r"\begin{lrbox}{\questionbox}"):tex.index(r"\end{lrbox}")]
        self.assertNotIn(r"\vspace{30mm}", box)
        self.assertIn("\\fi\n\\par\\vspace{30mm}", tex)


if __name__ == "__main__":
    unittest.main()
