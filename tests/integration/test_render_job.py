import shutil
import pytest
from physics_worksheet_mcp.jobs.worker import run_worker

@pytest.mark.skipif(not shutil.which('xelatex'),reason='XeLaTeX unavailable; Linux/WSL render test required')
def test_supervised_job_creates_verified_real_pdf(system):
    config=system['config']; config_path=system['tmp']/'config.toml'
    config_path.write_text('\n'.join(f'{k} = "{getattr(config,k).as_posix()}"' for k in ('banks','state','jobs')),encoding='utf-8')
    job=system['jobs'].submit(system['user'],system['catalog'].filter({}),'合成排版验收',True,0)
    run_worker(config,config_path,once=True)
    result=system['jobs'].get(system['user'],job['job_id'])
    assert result['status']=='succeeded',result
    assert len(result['files'])==1 and result['files'][0]['pages']>=1
    assert result['pdf_qa']['worksheet']['passed']
    assert result['answers']['banks_without_package']==['demo-bank']
