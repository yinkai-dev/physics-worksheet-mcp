import json
import os
import signal
import subprocess
import sys
import time
from ..storage import Database
from ..util import canonical,file_lock

def kill(process):
    if process.poll() is None:
        if os.name=='posix': os.killpg(process.pid,signal.SIGKILL)
        else: process.kill()
        process.wait(timeout=10)

def recover(db,now):
    with db.transaction() as c:
        for r in c.execute('SELECT * FROM jobs WHERE status="running" AND lease<?',(now,)).fetchall():
            if r['cancel']: status='cancelled'
            elif r['retries']<1: status='queued'
            else: status='failed'
            c.execute('UPDATE jobs SET status=?,stage=?,retries=retries+1,lease=NULL,updated=?,result=? WHERE id=?',(status,'interrupted' if status=='queued' else status,now,canonical({'failure_code':'worker-lost'}).decode() if status=='failed' else None,r['id']))

def run_worker(config,config_path,once=False):
    db=Database(config.state); config.jobs.mkdir(parents=True,exist_ok=True)
    # Single-worker deployment; DB transaction also prevents parallel job claiming.
    with file_lock(config.jobs/'.worker.lock'):
        while True:
            now=time.time(); db.execute('INSERT OR REPLACE INTO heartbeat VALUES("worker",?)',(now,)); recover(db,now)
            with db.transaction() as c:
                r=None if c.execute('SELECT 1 FROM jobs WHERE status="running"').fetchone() else c.execute('SELECT * FROM jobs WHERE status="queued" ORDER BY created LIMIT 1').fetchone()
                if r: c.execute('UPDATE jobs SET status="running",stage="verify",lease=?,updated=? WHERE id=?',(now+30,now,r['id']))
            if not r:
                if once: return
                time.sleep(1); continue
            out=config.jobs/r['id']; out.mkdir(parents=True,exist_ok=True)
            env={**os.environ,'PHYSICS_MANAGED_JOB':'1'}
            cpu=min(240,45*((len(json.loads(r['data'])['selection']['questions'])+79)//80))
            argv=[sys.executable,'-m','physics_worksheet_mcp.jobs.entry','--config',str(config_path),'--job',r['id']]
            if os.name=='posix': argv=['prlimit',f'--cpu={cpu+15}', '--as=2147483648','--']+argv
            with (out/f"supervisor-{r['retries']}.log").open('ab') as log:
                proc=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT,env=env,start_new_session=os.name=='posix')
                start=time.monotonic(); last=0
                try:
                    while proc.poll() is None:
                        row=db.one('SELECT cancel FROM jobs WHERE id=?',(r['id'],))
                        if row['cancel'] or time.monotonic()-start>min(900,cpu+60):
                            kill(proc); status='cancelled' if row['cancel'] else 'failed'
                            db.execute('UPDATE jobs SET status=?,stage=?,result=?,lease=NULL,updated=? WHERE id=?',(status,status,canonical({'failure_code':'cancelled' if row['cancel'] else 'render-timeout'}).decode(),time.time(),r['id'])); break
                        if time.monotonic()-last>=5:
                            last=time.monotonic(); now=time.time(); db.execute('UPDATE jobs SET lease=? WHERE id=? AND status="running"',(now+30,r['id'])); db.execute('INSERT OR REPLACE INTO heartbeat VALUES("worker",?)',(now,))
                        time.sleep(.25)
                except BaseException:
                    kill(proc); raise
            row=db.one('SELECT status,cancel FROM jobs WHERE id=?',(r['id'],))
            if row['status']=='running':
                db.execute('UPDATE jobs SET status=?,stage=?,lease=NULL,result=?,updated=? WHERE id=?',('cancelled' if row['cancel'] else 'failed','failed',canonical({'failure_code':'worker-child-exit'}).decode(),time.time(),r['id']))
            if once: return
