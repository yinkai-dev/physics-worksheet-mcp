"""Compiler seam: callers do not select TeX flags, packages or shell commands."""
from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time


@dataclass(frozen=True)
class CompileResult:
    success: bool
    compiler: str
    compiler_version: str
    error: str | None = None


class XeLaTeXCompiler:
    name = "xelatex"

    def __init__(self, timeout_seconds=60):
        self.timeout_seconds = timeout_seconds

    def compile(self, tex_path: Path, output_dir: Path) -> CompileResult:
        executable = shutil.which("xelatex")
        log_path = output_dir / "compile.log"
        if not executable:
            log_path.write_text("xelatex is unavailable; no compiler fallback was attempted\n", encoding="utf-8")
            return CompileResult(False, self.name, "unavailable", "compiler-unavailable: xelatex")
        started, version = time.monotonic(), "unknown"
        # A runner-managed compiler must not escape the runner's process group.
        # Standalone use owns its own group and retains whole-group cleanup.
        managed_job = os.environ.get("PHYSICS_MANAGED_JOB") == "1"
        environment = {key: value for key, value in os.environ.items()
                       if key in {"PATH", "SystemRoot", "WINDIR", "COMSPEC", "LD_LIBRARY_PATH"}}
        environment.update({"HOME": str(output_dir), "TMPDIR": str(output_dir), "TEMP": str(output_dir),
                            "TMP": str(output_dir), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
                            "openin_any": "p", "openout_any": "p", "shell_escape": "f",
                            "SOURCE_DATE_EPOCH": "946684800", "FORCE_SOURCE_DATE": "1"})
        try:
            version_output = subprocess.run([executable, "--version"], capture_output=True, timeout=5,
                                            check=True, env=environment, text=True)
            version = version_output.stdout.splitlines()[0][:300]
            with log_path.open("wb") as log:
                for pass_number in (1, 2):
                    remaining = self.timeout_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        return CompileResult(False, self.name, version, "compile-timeout")
                    log.write(f"=== XeLaTeX pass {pass_number}; shell escape disabled ===\n".encode())
                    log.flush()
                    argv = [executable, "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error",
                            "-file-line-error", "-jobname=worksheet", "-output-directory=.", tex_path.name]
                    process = subprocess.Popen(argv, cwd=output_dir, env=environment, stdout=log,
                                               stderr=subprocess.STDOUT, shell=False,
                                               start_new_session=(os.name == "posix" and not managed_job))
                    try:
                        code = process.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        if os.name == "posix" and not managed_job:
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                        process.wait()
                        log.write(b"\ncompile-timeout; process terminated\n")
                        return CompileResult(False, self.name, version, "compile-timeout")
                    if code:
                        return CompileResult(False, self.name, version, f"compile-exit:{code}")
                    if log.tell() > 1024 * 1024:
                        return CompileResult(False, self.name, version, "compile-log-limit: 1 MiB exceeded")
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            if "Missing character:" in log_text:
                return CompileResult(False, self.name, version, "missing-glyph: compiler reported missing characters")
            overflow = [float(value) for value in re.findall(
                r"Overfull \\[hv]box \(([0-9]+(?:\.[0-9]+)?)pt too (?:wide|high)\)", log_text)]
            if overflow and max(overflow) > 1.0:
                return CompileResult(False, self.name, version,
                                     f"layout-overfull: {max(overflow):g}pt exceeds 1pt tolerance")
            return CompileResult(True, self.name, version)
        except (OSError, subprocess.SubprocessError) as exc:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\nCompiler exception: {type(exc).__name__}: {exc}\n")
            return CompileResult(False, self.name, version, f"compiler-error:{type(exc).__name__}")
