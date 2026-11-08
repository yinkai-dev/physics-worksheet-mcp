import tempfile
import unittest
from pathlib import Path

from physics_worksheet_mcp.render.safety import SafetyError, escape_text, safe_workspace_path, validate_math


class SafetyTests(unittest.TestCase):
    def test_literal_angle_symbol_in_original_question_uses_tex_angle_glyph(self):
        from physics_worksheet_mcp.render.safety import normalize_math_unicode
        self.assertEqual(escape_text('∠ABC=30°'), r'\ensuremath{\angle}ABC=30\ensuremath{{}^{\circ}}')
        self.assertEqual(normalize_math_unicode('∠ABC'), r'\angle{}ABC')

    def test_unicode_subscripts_and_temperature_symbols_use_available_glyphs(self):
        result=escape_text('v₀ a₁ T₂ 20℃ 68℉')
        for char in '₀₁₂℃℉':self.assertNotIn(char,result)
        self.assertIn(r'\ensuremath{{}_{0}}',result)
        self.assertIn(r'\mathrm{F}',result)
        self.assertIn(r'\mathrm{C}',result)

    def test_unicode_inside_math_preserves_existing_tex_and_uses_supported_symbols(self):
        from physics_worksheet_mcp.render.safety import normalize_math_unicode
        value=normalize_math_unicode(r'v₀=\frac{a₁t²}{2},T=68℉')
        self.assertIn(r'\frac',value)
        self.assertNotIn('₀',value)
        self.assertNotIn('℉',value)
        self.assertEqual(normalize_math_unicode(r'\frac{x^2}{2}'),r'\frac{x^2}{2}')

    def test_plain_unicode_math_uses_the_same_supported_math_glyphs(self):
        result=escape_text('电压≈3V，符号⊐，电阻Ω')
        self.assertIn(r'\ensuremath{\approx}',result)
        self.assertIn(r'\ensuremath{\sqsupset}',result)
        self.assertIn(r'\ensuremath{\Omega}',result)

    def test_source_answer_blank_can_wrap_without_changing_its_characters(self):
        result=escape_text('倍率后进行________（选填）')
        self.assertEqual(result.count(r'\_'),8)
        self.assertIn(r'\allowbreak{}\_',result)

    def test_math_subset_accepts_common_physics(self):
        for value in (r"E=BLv", r"a=\frac{v_2-v_1}{\Delta t}", r"\sqrt{x^2+y^2}", r"F=2\,\mathrm{N}", r"n_1\gg N"):
            self.assertEqual(validate_math(value), value)

    def test_much_less_than_preserves_original_relation(self):
        value = r"h\ll\sqrt{S}"
        self.assertEqual(validate_math(value), value)
        with self.assertRaisesRegex(SafetyError, r"command \\llfoo is not allowed"):
            validate_math(r"h\llfoo\sqrt{S}")

    def test_escape_cannot_create_tex_command(self):
        result = escape_text(r"\input{secret} 50% & x_y $bad$")
        self.assertNotIn(r"\input", result)
        self.assertIn(r"\textbackslash{}input\{secret\}", result)
        self.assertIn(r"50\%", result)

    def test_original_unit_spacing_is_preserved_without_opening_macro_expansion(self):
        self.assertEqual(validate_math(r"9.3\times10^{-6}\mathrm{~m}"), r"9.3\times10^{-6}\mathrm{~m}")
        for value in (r"~\input{x}", r"\def~{x}", r"~\csname input\endcsname"):
            with self.assertRaises(SafetyError):
                validate_math(value)

    def test_dangerous_math_and_tex_expansion_are_rejected(self):
        for value in (r"\write18{touch /tmp/x}", r"\csname input\endcsname", r"^^5cinput{x}",
                      r"\newcommand{\a}{x}", r"\frac{1}{2", r"x$\input{x}$", r"\href{https://evil}{x}"):
            with self.assertRaises(SafetyError):
                validate_math(value)

    def test_paths_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(safe_workspace_path(root, "outputs/a.json"), root / "outputs/a.json")
            for value in ("../secret", "/etc/passwd", "C:\\secret", "a/../../secret", "a\\..\\..\\secret"):
                with self.assertRaises(SafetyError):
                    safe_workspace_path(root, value)


if __name__ == "__main__":
    unittest.main()
