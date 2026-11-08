import hashlib
import json
from pathlib import Path
import shutil
import pytest
import pymupdf
from physics_worksheet_mcp.render.renderer import render_exam

@pytest.mark.skipif(not shutil.which('xelatex'),reason='Real CJK table regression requires XeLaTeX on Linux/WSL')
def test_cjk_paragraph_immediately_after_table(tmp_path):
    path=Path(__file__).resolve().parents[1]/'fixtures/02-formulas.json'
    spec=json.loads(path.read_text(encoding='utf-8')); spec['schema_version']='1.2'
    text=lambda value:{'type':'text','value':value}
    q=spec['questions'][0]; q.update(representation='structured',original_number='1',assets=[],choice_mode='unspecified')
    q['body']=[text('合成测试表格如下。'),{'type':'table','rows':[[[text('时间')],[text('速度')]],[[text('零')],[text('二')]]]},text('请写出计算过程并标明单位。')]
    q['source']['source_excerpt']='合成测试表格如下。时间速度零二。请写出计算过程并标明单位。'
    q['source']['content_sha256']=hashlib.sha256(q['source']['source_excerpt'].encode()).hexdigest()
    result=render_exam(spec,tmp_path/'render')
    assert result.status=='success',result.error
    with pymupdf.open(result.pdf_path) as pdf:
        content=''.join(page.get_text() for page in pdf)
    assert '请写出计算过程并标明单位' in ''.join(content.split())
    assert '时间' in content and '速度' in content
