"""One supervised job process: immutable resolution, render, QA, publication."""
import argparse
import json
from pathlib import Path
import time
import pymupdf as fitz
from ..config import Config
from ..storage import Database,new_id
from ..bank.release import BankRegistry
from ..resolve.service import resolve_selection
from ..render.renderer import render_exam,DirectoryVerifiedAssetReader
from ..render.adapters.compiler import XeLaTeXCompiler
from ..util import write_json,sha,canonical

def run(config,id):
    db=Database(config.state); job=db.one('SELECT * FROM jobs WHERE id=?',(id,)); data=json.loads(job['data'])
    attempt=int(job['retries']); out=config.jobs/id/f'attempt-{attempt}'; out.mkdir(parents=True,exist_ok=False)
    def stage(s): db.execute('UPDATE jobs SET stage=?,updated=? WHERE id=? AND status="running"',(s,time.time(),id))
    timings={'query':data.get('query_ms',0)}; start=time.monotonic()
    try:
        stage('verify'); t=time.monotonic()
        spec,proof=resolve_selection(BankRegistry(config.banks),data['selection'],data['title'],data['answers'],out)
        timings['verify']=round((time.monotonic()-t)*1000,2)
        reader=DirectoryVerifiedAssetReader(out/'verified-assets.json')
        cpu=min(240,45*((len(spec['questions'])+79)//80)); compiler=XeLaTeXCompiler(cpu+10)
        rendered=[]; qa={}; t=time.monotonic(); compile_ms=0; qa_ms=0
        for kind in ['worksheet']+(['answers'] if proof['answers']['with_source_answer'] else []):
            stage('render_'+kind)
            result=render_exam(spec,out/kind,compiler=compiler,asset_reader=reader,answers_only=kind=='answers')
            if result.status!='success': raise ValueError(result.error)
            path=Path(result.pdf_path); manifest=json.loads(Path(result.manifest_path).read_bytes()); qa[kind]=manifest['pdf_qa']
            compile_ms+=manifest['timings_ms']['compile']; qa_ms+=manifest['timings_ms']['qa']
            with fitz.open(path) as pdf: pages=len(pdf)
            rendered.append({'id':new_id('file'),'kind':kind,'path':path.relative_to(config.jobs).as_posix(),'sha256':sha(path.read_bytes()),'pages':pages,'bytes':path.stat().st_size,
                             'name':data['title']+('—答案' if kind=='answers' else '')+'.pdf'})
        timings['render_total']=round((time.monotonic()-t)*1000,2); timings['compile']=compile_ms; timings['qa']=qa_ms
        stage('publish'); t=time.monotonic(); short=data['selection']['shortfall']; requested=data['selection']['requested_count']; actual=len(spec['questions'])
        notice=f'原要求 {requested} 道，实际 {actual} 道，差额 {short} 道。'
        if proof['answers']['banks_without_package']: notice+=' 部分题库没有原卷答案包，未生成其答案。'
        if proof['answers']['missing']: notice+=f" 原卷缺答 {proof['answers']['missing']} 题，未补写答案。"
        result={'answers':proof['answers'],'timings_ms':timings,'pdf_qa':qa,'notice':notice}
        with db.transaction() as c:
            row=c.execute('SELECT status,cancel FROM jobs WHERE id=?',(id,)).fetchone()
            if row['status']!='running' or row['cancel']: return
            for f in rendered: c.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)',(f['id'],job['user_id'],id,f['kind'],f['path'],f['sha256'],f['pages'],f['bytes'],f['name']))
            timings['register']=round((time.monotonic()-t)*1000,2)
            timings['deliver']=timings['register']
            timings['total_worker']=round((time.monotonic()-start)*1000,2)
            proof['files']=rendered; proof['timings_ms']=timings; proof['pdf_qa']=qa
            write_json(out/'source-resolution.json',proof); write_json(out/'manifest.json',result)
            c.execute('UPDATE jobs SET status="succeeded",stage="succeeded",result=?,updated=?,lease=NULL WHERE id=?',(canonical(result).decode(),time.time(),id))
    except Exception as exc:
        write_json(out/'failure.json',{'type':type(exc).__name__,'error':str(exc)})
        # Detailed diagnostics remain in the private job directory.
        code=getattr(exc,'code',None) or ('render-timeout' if 'timeout' in str(exc) else 'render-failed')
        db.execute('UPDATE jobs SET status="failed",stage="failed",result=?,updated=?,lease=NULL WHERE id=? AND status="running" AND cancel=0',(canonical({'failure_code':code,'notice':'任务失败；管理员可按任务编号查看私有诊断。'}).decode(),time.time(),id))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--config',required=True); p.add_argument('--job',required=True); a=p.parse_args(); run(Config.load(a.config),a.job)
