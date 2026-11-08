import os
from pathlib import Path
import shutil
import pytest
from physics_worksheet_mcp.render.adapters.compiler import XeLaTeXCompiler

@pytest.mark.skipif(not shutil.which('xelatex'),reason='Real XeLaTeX missing-glyph test requires Linux/WSL TeX')
def test_real_missing_glyph_is_rejected(tmp_path):
    path=tmp_path/'input.tex'
    path.write_text('\\documentclass{article}\n\\begin{document}\n\\char"10FFFF\n\\end{document}\n',encoding='utf-8')
    result=XeLaTeXCompiler(20).compile(path,tmp_path)
    assert not result.success and 'missing-glyph' in result.error

@pytest.mark.skipif(os.name!='posix',reason='POSIX process-group timeout injection requires Linux/WSL')
def test_wall_timeout_kills_real_compiler_process(tmp_path,monkeypatch):
    fake=tmp_path/'bin'; fake.mkdir(); executable=fake/'xelatex'
    executable.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo synthetic-timeout-compiler; exit 0; fi\nexec sleep 30\n',encoding='utf-8'); executable.chmod(0o755)
    monkeypatch.setenv('PATH',str(fake)+os.pathsep+os.environ['PATH'])
    path=tmp_path/'input.tex'; path.write_text('synthetic timeout injection')
    result=XeLaTeXCompiler(.5).compile(path,tmp_path)
    assert not result.success and result.error=='compile-timeout'
