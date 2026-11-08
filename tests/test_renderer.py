import hashlib
import copy
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz
from physics_worksheet_mcp.render.adapters.compiler import CompileResult
from physics_worksheet_mcp.render.contract import validate_spec
from physics_worksheet_mcp.render.renderer import (_choice_tex, _display_body,
                                           _source_tex, _strip_page_numbers,
                                           _tex, render_exam)
from test_contract import valid_spec


class FakeCompiler:
    name = "fake-test-only"

    def __init__(self, mode="success"):
        self.mode = mode
        self.calls = 0

    def compile(self, tex_path, output_dir):
        self.calls += 1
        log = output_dir / "compile.log"
        log.write_text("synthetic compiler; not XeLaTeX evidence\n", encoding="utf-8")
        if self.mode == "fail":
            return CompileResult(False, self.name, "test-1", "synthetic compiler failed")
        pdf = fitz.open()
        page = pdf.new_page()
        text = "第1题 合成物理练习 合成测试题：物体做匀速直线运动。求速度。" if self.mode == "success" else "wrong question"
        page.insert_text((40, 50), text, fontname="china-s", fontsize=12)
        pdf.save(output_dir / "worksheet.pdf")
        pdf.close()
        return CompileResult(True, self.name, "test-1")


class RendererTests(unittest.TestCase):
    def test_source_caption_group_stays_with_figure_without_changing_blocks(self):
        from physics_worksheet_mcp.render.renderer import _block_tex
        blocks=[{'type':'figure','asset_id':'image'},
                {'type':'text','value':'\n第 17 题图甲\n第17题图乙'}]
        original=copy.deepcopy(blocks)
        tex=_block_tex(blocks,[{'asset_id':'image','width_mm':100}],{'image':'assets/image.png'})
        group=r'\begin{minipage}{\dimexpr\linewidth-\leftskip\relax}'
        self.assertLess(tex.index(group),tex.index(r'\includegraphics'))
        self.assertGreater(tex.rindex(r'\end{minipage}'),tex.index('题图乙'))
        self.assertEqual(blocks,original)

    def test_ordinary_text_after_figure_remains_breakable(self):
        from physics_worksheet_mcp.render.renderer import _block_tex
        tex=_block_tex([{'type':'figure','asset_id':'image'},
                        {'type':'text','value':'第17题图甲说明了受力关系，下面继续讨论另一个过程。'}],
                       [{'asset_id':'image','width_mm':100}],{'image':'assets/image.png'})
        self.assertGreater(tex.index('说明了受力关系'),tex.rindex(r'\end{minipage}'))

    def test_large_topic_pdf_can_exceed_100_pages_without_weakening_text_checks(self):
        from physics_worksheet_mcp.render.renderer import inspect_pdf
        raw=valid_spec();question=raw['questions'][0]
        raw['questions']=[dict(copy.deepcopy(question),id='q'+str(i)) for i in range(160)]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'long.pdf';pdf=fitz.open()
            for page_index in range(101):
                page=pdf.new_page();numbers=[page_index+1]+([page_index+102] if page_index<59 else [])
                page.insert_text((50,80),'合成测试题 '+''.join('第'+str(n)+'题 ' for n in numbers),fontname='china-s')
            pdf.save(path);pdf.close()
            self.assertEqual(inspect_pdf(path,raw)['page_count'],101)

    def test_chinese_table_header_cannot_join_the_hline_control_word(self):
        from physics_worksheet_mcp.render.renderer import _block_tex
        cell=lambda text:[{'type':'text','value':text}]
        tex=_block_tex([{'type':'table','rows':[[cell('参数'),cell('数值')],[cell('额定电压'),cell('220V')]]}])
        self.assertNotIn(r'\hline参数',tex)
        self.assertNotIn(r'\hline额定',tex)
        self.assertIn(r'\hline 参数',tex)

    def test_outputs_record_hashes_sources_and_default_no_answers(self):
        with tempfile.TemporaryDirectory() as d:
            result = render_exam(valid_spec(), d, compiler=FakeCompiler())
            self.assertEqual(result.status, "success", result.error)
            manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
            self.assertFalse(manifest["exam_spec"]["document"]["include_answers"])
            self.assertEqual(manifest["exam_spec"]["questions"][0]["source"]["chunk_id"], "synthetic-1")
            for name, metadata in manifest["artifacts"].items():
                self.assertEqual(hashlib.sha256((Path(d) / name).read_bytes()).hexdigest(), metadata["sha256"])
            self.assertEqual(manifest["pdf_qa"]["page_count"], 1)
            tex = Path(result.tex_path).read_text(encoding="utf-8")
            self.assertIn(r"\par\nobreak{\footnotesize\raggedright 来源：", tex)

    def test_invalid_input_never_reaches_compiler(self):
        with tempfile.TemporaryDirectory() as d:
            compiler = FakeCompiler()
            raw = valid_spec()
            raw["questions"][0]["review_status"] = "needs-review"
            result = render_exam(raw, d, compiler=compiler)
            self.assertEqual(result.status, "failed")
            self.assertEqual(compiler.calls, 0)
            self.assertTrue(Path(result.manifest_path).is_file())
            self.assertIsNone(result.pdf_path)

    def test_compiler_failure_and_invalid_pdf_are_not_success(self):
        for mode in ("fail", "wrong-pdf"):
            with tempfile.TemporaryDirectory() as d:
                result = render_exam(valid_spec(), d, compiler=FakeCompiler(mode))
                self.assertEqual(result.status, "failed")
                self.assertIsNone(result.pdf_path)
                self.assertTrue(Path(result.log_path).is_file())
                if mode == "wrong-pdf":
                    self.assertTrue((Path(d) / "rejected-worksheet.pdf").is_file())
                    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
                    self.assertFalse(manifest["pdf_qa"]["passed"])

    def test_existing_artifacts_and_symlinks_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / "worksheet.pdf"
            target.write_bytes(b"original")
            result = render_exam(valid_spec(), d, compiler=FakeCompiler())
            self.assertEqual(result.status, "failed")
            self.assertEqual(target.read_bytes(), b"original")

    def test_json_duplicate_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "input.json"
            path.write_text('{"schema_version":"1.0","schema_version":"2.0"}', encoding="utf-8")
            result = render_exam(path, Path(d) / "out", compiler=FakeCompiler())
            self.assertEqual(result.status, "failed")
            self.assertIn("duplicate", result.error)

    def test_choice_math_is_rendered_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as d:
            raw = valid_spec()
            raw["questions"][0]["choices"] = ["A. $not_math$", [{"type": "text", "value": "B. "},
                                                                  {"type": "math", "value": r"\mathrm{N/C}"}]]
            result = render_exam(raw, d, compiler=FakeCompiler())
            self.assertEqual(result.status, "success", result.error)
            tex = Path(result.tex_path).read_text(encoding="utf-8")
            self.assertIn(r"A. \$not\_math\$", tex)
            self.assertIn(r"B. \(\mathrm{N/C}\)", tex)

    def test_original_number_moves_to_source_without_mutating_spec(self):
        raw = valid_spec()
        question = raw["questions"][0]
        question["body"][0]["value"] = "12. 物体的速度为 1.5 米每秒。"
        question["source"]["source_excerpt"] = "# 12. 物体的速度为 1.5 米每秒。\nA. 零"
        before = copy.deepcopy(question)
        blocks, number = _display_body(question)
        self.assertEqual(number, "12")
        self.assertEqual(blocks[0]["value"], "物体的速度为 1.5 米每秒。")
        self.assertEqual(question, before)
        self.assertIn("原第", _source_tex(question["source"], number))

    def test_original_number_before_score_year_and_math_is_not_duplicated(self):
        for body in ("13. (3 分) 物体的速度是多少？", "13. 2025 年进行一次实验。", "13. "):
            question = valid_spec()["questions"][0]
            question["body"][0]["value"] = body
            question["body"].append({"type": "math", "value": "F=ma"})
            question["source"]["source_excerpt"] = body + "$F=ma$"
            blocks, number = _display_body(question)
            self.assertEqual(number, "13", body)
            self.assertEqual(blocks[0]["value"], body[4:])
            self.assertEqual(blocks[1], {"type": "math", "value": "F=ma"})

    def test_values_subquestion_markers_and_unmatched_numbers_remain(self):
        for body, source in (("1.5 米每秒是物体的初速度。", "1.5 米每秒是物体的初速度。"),
                             ("13.25 是一个数值。", "13.25 是一个数值。"),
                             ("（1）求物体的速度。", "（1）求物体的速度。"),
                             ("2. 物体匀速运动。", "1. 物体匀速运动。"),
                             ("1. 物体匀速运动。", "物体匀速运动。")):
            question = valid_spec()["questions"][0]
            question["body"][0]["value"] = body
            question["source"]["source_excerpt"] = source
            blocks, number = _display_body(question)
            self.assertIsNone(number, body)
            self.assertEqual(blocks[0]["value"], body)

    def test_sources_remain_readable_and_escape_long_storage_titles(self):
        source = valid_spec()["questions"][0]["source"]
        source["title"] = "试卷-" + "A" * 70 + r"_100%_\input{x}-OCR.md"
        tex = _source_tex(source, "1")
        self.assertNotIn("OCR.md", tex)
        self.assertIn(r"\%", tex)
        self.assertIn(r"\textbackslash{}input\{x\}", tex)
        self.assertNotIn("A" * 25, tex)
        self.assertIn("页码未知", tex)
        self.assertEqual(tex.replace(r"\allowbreak{}", "").count("A"), 70)

    def test_choices_are_measured_by_tex_but_oversize_paragraphs_are_not_boxed(self):
        tex = "\n".join(_choice_tex(["A. 电容", [{"type": "text", "value": "B. "},
                                                {"type": "math", "value": r"\frac{1}{2}mv^2"}]], "q1"))
        self.assertIn(r"\settowidth", tex)
        self.assertIn("physics-choice-layout:q1:single-row", tex)
        self.assertIn("physics-choice-layout:q1:two-column", tex)
        self.assertIn("physics-choice-layout:q1:stacked", tex)
        for choice in ("A. " + "物理" * 180, "A. 第一段\n第二段"):
            tex = "\n".join(_choice_tex([choice, "B. 零"], "q1"))
            self.assertNotIn(r"\settowidth", tex)
            self.assertNotIn(r"\parbox", tex)
            self.assertIn("physics-choice-layout:q1:stacked", tex)

    def test_worksheet_has_student_fields_and_long_question_page_break_escape(self):
        spec, _ = validate_spec(valid_spec())
        tex = _tex(spec)
        self.assertIn("姓名：", tex)
        self.assertIn("班级：", tex)
        self.assertIn("日期：", tex)
        self.assertIn(r"\pagestyle{plain}", tex)
        self.assertIn(r"\ht\questionbox+\dp\questionbox", tex)
        self.assertIn(r"\vspace{30mm}", tex)
        self.assertIn("\\noindent\\usebox{\\questionbox}\\par\n\\else\n\\noindent\\textbf{第 1 题}", tex)

    def test_single_multiple_and_unspecified_choice_modes_print_truthfully(self):
        spec, _ = validate_spec(valid_spec())
        question = spec["questions"][0]
        for mode, expected in (("single", "（单选）"), ("multiple", "（多选）"), ("unspecified", "")):
            question["choice_mode"] = mode
            tex = _tex(spec)
            self.assertIn(r"\textbf{第 1 题" + expected + "}", tex)
            if not expected:
                self.assertNotIn("单选", tex)
                self.assertNotIn("多选", tex)


class StripPageNumberTests(unittest.TestCase):
    """Regression for cross-page Chinese snippet QA (issue: r8 part10 false negative)."""

    def test_removes_only_a_trailing_numeric_line_per_page(self):
        # No trailing number: the page text is returned unchanged.
        self.assertEqual(_strip_page_numbers(["正文\n", "续文\n"]),
                         ["正文\n", "续文\n"])
        # Typical page-number footnote.
        self.assertEqual(_strip_page_numbers(["正文\n1\n", "续文\n2\n"]),
                         ["正文", "续文"])
        # Whitespace around the trailing number.
        self.assertEqual(_strip_page_numbers(["正文\n   1   \n", "续文"]),
                         ["正文", "续文"])

    def test_keeps_inline_numbers_and_non_trailing_digit_lines(self):
        # In-text numbers must remain intact.
        self.assertEqual(_strip_page_numbers(["加速度为10m/s²\n", "末行\n"]),
                         ["加速度为10m/s²\n", "末行\n"])
        # A digit-only line in the middle (followed by more text) is not a footer.
        self.assertEqual(_strip_page_numbers(["表1\n数据\n", "末行\n"]),
                         ["表1\n数据\n", "末行\n"])

    def test_joined_pages_let_cross_page_chinese_match_after_stripping(self):
        page_texts = ["根据以上数据\n1\n", "绘出图线Ⅰ和Ⅱ\n2\n"]
        joined = "".join(_strip_page_numbers(page_texts))
        self.assertEqual(re.sub(r"\s+", "", joined), "根据以上数据绘出图线Ⅰ和Ⅱ")


    @unittest.skipUnless(shutil.which('xelatex'), 'XeLaTeX unavailable on this platform')
    def test_actual_choice_widths_and_long_question_pagination(self):
        raw = valid_spec()
        raw["document"]["title"] = "排版边界验收练习卷⑩"
        questions = []
        for index, choice_length in enumerate((2, 15, 32), 1):
            question = copy.deepcopy(raw["questions"][0])
            question["id"] = f"q{index}"
            question["body"][0]["value"] = f"{index}. 物体做匀速直线运动，请选择正确的说法。"
            if index == 1:
                question["body"][0]["value"] += "保留题内标记①②③④⑤⑥⑦⑧⑨⑩。"
            question["choices"] = [label + ". " + "物" * choice_length for label in "ABCD"]
            if index == 1:
                question["choices"][0] += "①"
            question["source"]["source_excerpt"] = question["body"][0]["value"]
            question["source"]["content_sha256"] = hashlib.sha256(
                question["source"]["source_excerpt"].encode()).hexdigest()
            questions.append(question)
        question = copy.deepcopy(raw["questions"][0])
        question["id"] = "q4"
        question["body"][0]["value"] = "\n".join(
            f"第 {index} 段：物体做匀速直线运动，速度大小保持不变。" for index in range(1, 81)) + "\n末段检验文字。"
        question["source"]["source_excerpt"] = question["body"][0]["value"]
        question["source"]["content_sha256"] = hashlib.sha256(question["source"]["source_excerpt"].encode()).hexdigest()
        questions.append(question)
        raw["questions"] = questions
        with tempfile.TemporaryDirectory() as directory:
            result = render_exam(raw, directory)
            self.assertEqual(result.status, "success", result.error)
            log = Path(result.log_path).read_text(encoding="utf-8")
            self.assertNotIn("Missing character:", log)
            for index, layout in ((1, "single-row"), (2, "two-column"), (3, "stacked")):
                self.assertIn(f"physics-choice-layout:q{index}:{layout}", log)
            with fitz.open(result.pdf_path) as pdf:
                self.assertGreaterEqual(len(pdf), 3)
                text = "".join(page.get_text() for page in pdf)
                self.assertIn("末段检验文字", text)
                for marker in "①②③④⑤⑥⑦⑧⑨⑩":
                    self.assertIn(marker, text, "circled source marker must survive real compilation")
                self.assertEqual(text.count("来源："), 4)
                heading_lefts = [span["bbox"][0]
                                 for page in pdf for block in page.get_text("dict")["blocks"]
                                 for line in block.get("lines", []) for span in line["spans"]
                                 if span["text"].strip() == "第" and "Bold" in span["font"]]
                self.assertEqual(len(heading_lefts), 4)
                self.assertLess(max(heading_lefts) - min(heading_lefts), 0.5,
                                "stacked options must not indent the question heading")
                for page in pdf:
                    for x0, y0, x1, y1, *_ in page.get_text("blocks"):
                        self.assertGreaterEqual(x0, 10)
                        self.assertGreaterEqual(y0, 10)
                        self.assertLessEqual(x1, page.rect.width - 10)
                        self.assertLessEqual(y1, page.rect.height - 10)


if __name__ == "__main__":
    unittest.main()
