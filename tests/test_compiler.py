import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from physics_worksheet_mcp.render.adapters.compiler import XeLaTeXCompiler


class CompilerProcessTests(unittest.TestCase):
    def test_material_box_overflow_fails_but_rounding_tolerance_does_not(self):
        for log_line, expected in (("Overfull \\hbox (14.213pt too wide)", False),
                                   ("Overfull \\vbox (21.0pt too high)", False),
                                   ("Overfull \\hbox (0.00002pt too wide)", True),
                                   ("Underfull \\hbox (badness 10000)", True)):
            with self.subTest(log_line=log_line), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                process = MagicMock()
                process.wait.return_value = 0

                def spawn_with_log(*args, **kwargs):
                    kwargs["stdout"].write((log_line + "\n").encode())
                    kwargs["stdout"].flush()
                    return process

                with patch("physics_worksheet_mcp.render.adapters.compiler.shutil.which", return_value="xelatex"), \
                     patch("physics_worksheet_mcp.render.adapters.compiler.subprocess.run", return_value=SimpleNamespace(stdout="test compiler\n")), \
                     patch("physics_worksheet_mcp.render.adapters.compiler.subprocess.Popen", side_effect=spawn_with_log):
                    result = XeLaTeXCompiler().compile(root / "worksheet.tex", root)
                self.assertIs(result.success, expected)
                if not expected:
                    self.assertIn("layout-overfull", result.error)

    def test_managed_mode_remains_in_outer_group_and_kills_child_on_timeout(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            process = MagicMock()
            process.wait.side_effect = [subprocess.TimeoutExpired("xelatex", 1), 0]
            with patch.dict(os.environ, {"PHYSICS_MANAGED_JOB": "1"}), \
                 patch("physics_worksheet_mcp.render.adapters.compiler.shutil.which", return_value="xelatex"), \
                 patch("physics_worksheet_mcp.render.adapters.compiler.subprocess.run", return_value=SimpleNamespace(stdout="test compiler\n")), \
                 patch("physics_worksheet_mcp.render.adapters.compiler.subprocess.Popen", return_value=process) as spawn:
                result = XeLaTeXCompiler().compile(root / "worksheet.tex", root)
            self.assertFalse(result.success)
            self.assertEqual(result.error, "compile-timeout")
            self.assertIs(spawn.call_args.kwargs["start_new_session"], False)
            process.kill.assert_called_once()

    def test_standalone_mode_retains_platform_process_group_policy(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            process = MagicMock()
            process.wait.return_value = 0
            with patch.dict(os.environ, {"PHYSICS_MANAGED_JOB": "0"}), \
                 patch("physics_worksheet_mcp.render.adapters.compiler.shutil.which", return_value="xelatex"), \
                 patch("physics_worksheet_mcp.render.adapters.compiler.subprocess.run", return_value=SimpleNamespace(stdout="test compiler\n")), \
                 patch("physics_worksheet_mcp.render.adapters.compiler.subprocess.Popen", return_value=process) as spawn:
                result = XeLaTeXCompiler().compile(root / "worksheet.tex", root)
            self.assertTrue(result.success)
            self.assertEqual(spawn.call_args.kwargs["start_new_session"], os.name == "posix")


if __name__ == "__main__":
    unittest.main()
