import argparse
import json
from pathlib import Path
import sqlite3
import time
from .config import Config
from .storage import Database
from .auth.service import Auth
from .bank.release import BankRegistry,verify_release,import_legacy
from .util import write_json

def main():
    p=argparse.ArgumentParser(prog='pwmcp'); p.add_argument('--config')
    commands=p.add_subparsers(dest='command',required=True)
    for name in ('serve','worker'):
        sub=commands.add_parser(name); sub.add_argument('--config',dest='runtime_config',required=True)
        if name=='worker': sub.add_argument('--once',action='store_true')
    admin=commands.add_parser('admin'); admin.add_argument('--config',dest='admin_config'); sections=admin.add_subparsers(dest='section',required=True)
    bank=sections.add_parser('bank'); b=bank.add_subparsers(dest='action',required=True)
    imp=b.add_parser('import-legacy')
    for opt in ('provider-dir','tags','bank-id','out'): imp.add_argument('--'+opt,required=True)
    for opt in ('selections','answers'): imp.add_argument('--'+opt)
    verify=b.add_parser('verify'); verify.add_argument('path')
    install=b.add_parser('install'); install.add_argument('archive'); install.add_argument('--sha256',required=True)
    activate=b.add_parser('activate'); activate.add_argument('--bank',required=True); activate.add_argument('--release',required=True)
    rollback=b.add_parser('rollback'); rollback.add_argument('--to-generation',required=True)
    b.add_parser('status')
    users=sections.add_parser('user'); us=users.add_subparsers(dest='action',required=True)
    ua=us.add_parser('add'); ua.add_argument('--name',required=True)
    ud=us.add_parser('disable'); ud.add_argument('--user',required=True)
    tokens=sections.add_parser('token'); ts=tokens.add_subparsers(dest='action',required=True)
    ti=ts.add_parser('issue'); ti.add_argument('--user',required=True); ti.add_argument('--out',required=True); ti.add_argument('--expires-in',type=int)
    tr=ts.add_parser('revoke'); tr.add_argument('--id',required=True)
    tl=ts.add_parser('list'); tl.add_argument('--user',required=True)
    grant=sections.add_parser('grant'); gs=grant.add_subparsers(dest='action',required=True)
    for name in ('add','remove'):
        s=gs.add_parser(name); s.add_argument('--user',required=True); s.add_argument('--bank',required=True)
    jobs=sections.add_parser('jobs'); js=jobs.add_subparsers(dest='action',required=True); js.add_parser('list')
    ji=js.add_parser('inspect'); ji.add_argument('id')
    jg=js.add_parser('gc'); jg.add_argument('--dry-run',action='store_true',required=True)
    backup=sections.add_parser('backup'); backup.add_argument('--out',required=True)
    a=p.parse_args(); config_path=getattr(a,'runtime_config',None) or getattr(a,'admin_config',None) or a.config; config=Config.load(config_path)
    if a.command=='serve':
        import uvicorn
        from .mcp_app import build_app
        uvicorn.run(build_app(config),host=config.host,port=config.port,access_log=False); return
    if a.command=='worker':
        from .jobs.worker import run_worker
        run_worker(config,Path(config_path).absolute(),a.once); return
    registry=BankRegistry(config.banks); result=None
    if a.section=='bank':
        if a.action=='import-legacy': result=import_legacy(a.provider_dir,a.tags,a.selections,a.answers,a.bank_id,a.out).manifest
        elif a.action=='verify': result=verify_release(a.path).manifest
        elif a.action=='activate': result=registry.activate(a.bank,a.release)
        elif a.action=='rollback': result=registry.rollback(a.to_generation)
        elif a.action=='install': result=registry.install(a.archive,a.sha256).manifest
        else: result={'active':registry.active(),'generations':sorted(p.stem for p in (config.banks/'pointers').glob('*.json'))}
    else:
        db=Database(config.state); auth=Auth(db)
        if a.section=='user':
            if a.action=='add': result={'user_id':auth.add_user(a.name)}
            else: db.execute('UPDATE users SET status="disabled" WHERE id=?',(a.user,)); result={'disabled':True}
        elif a.section=='token':
            if a.action=='issue':
                target=Path(a.out); target.parent.mkdir(parents=True,exist_ok=True)
                with target.open('x',encoding='utf-8') as f: f.write(auth.issue(a.user,time.time()+a.expires_in if a.expires_in else None)+'\n')
                import os
                if os.name=='posix': os.chmod(target,0o600)
                result={'written':True}
            elif a.action=='revoke': db.execute('UPDATE tokens SET revoked=1 WHERE id=?',(a.id,)); result={'revoked':True}
            else: result=[dict(r) for r in db.all('SELECT id,prefix,expires,revoked FROM tokens WHERE user_id=?',(a.user,))]
        elif a.section=='grant':
            if a.action=='add': registry.load(a.bank); db.execute('INSERT OR IGNORE INTO grants VALUES(?,?)',(a.user,a.bank))
            else: db.execute('DELETE FROM grants WHERE user_id=? AND bank_id=?',(a.user,a.bank))
            result={'updated':True}
        elif a.section=='jobs':
            if a.action=='inspect': result=dict(db.one('SELECT * FROM jobs WHERE id=?',(a.id,)))
            elif a.action=='gc': result={'dry_run':True,'would_delete':[],'notice':'No automatic retention deletion in v0.1.'}
            else: result=[dict(r) for r in db.all('SELECT id,status,stage,created,updated FROM jobs ORDER BY created DESC LIMIT 100')]
        elif a.section=='backup':
            target=Path(a.out); target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists(): raise FileExistsError(target)
            with db.connect() as source,sqlite3.connect(target) as dest: source.backup(dest); result={'integrity_check':dest.execute('PRAGMA integrity_check').fetchone()[0]}
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
