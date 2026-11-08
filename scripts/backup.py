import argparse
from datetime import datetime,timezone
from pathlib import Path
import sqlite3
import json
from physics_worksheet_mcp.config import Config
from physics_worksheet_mcp.util import sha

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--config',required=True); p.add_argument('--out',required=True); a=p.parse_args()
    config=Config.load(a.config); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    target=out/('state-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.sqlite3')
    with sqlite3.connect(config.state) as source,sqlite3.connect(target) as dest:
        source.backup(dest); assert dest.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    print(json.dumps({'name':target.name,'sha256':sha(target.read_bytes()),'integrity_check':'ok'}))
    for old in sorted(out.glob('state-*.sqlite3'))[:-14]: old.unlink()
