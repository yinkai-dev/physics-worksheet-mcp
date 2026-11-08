import asyncio
import json
import time
from ..errors import ServiceError
from ..storage import new_id
from ..util import canonical,fingerprint

ACTIVE=('queued','running')
class Jobs:
    def __init__(self,db,config,files): self.db,self.config,self.files=db,config,files
    def submit(self,user,selection,title,answers,seed,request_id=None,based_on_job=None,query_ms=0):
        if not isinstance(title,str) or not 1<=len(title.strip())<=200 or any(ord(c)<32 or c in '/\\' for c in title) or type(answers) is not bool: raise ServiceError('invalid_filters')
        if not selection['questions']: return {'status':'empty','selection':selection,'notice':'按当前条件没有匹配题目，未生成 PDF。'}
        if len(selection['questions'])>self.config.max_questions: raise ServiceError('too_many_questions')
        if request_id is not None and (not isinstance(request_id,str) or not 1<=len(request_id)<=200): raise ServiceError('invalid_filters')
        if based_on_job and not self.db.one('SELECT id FROM jobs WHERE id=? AND user_id=?',(based_on_job,user)): raise ServiceError('job_not_found')
        data={'selection':selection,'title':title,'answers':answers,'seed':seed,'based_on_job':based_on_job,'query_ms':query_ms}
        request_hash=fingerprint({k:v for k,v in data.items() if k!='query_ms'})
        idem=fingerprint([user,request_id]) if request_id else fingerprint([user,request_hash])
        now=time.time()
        with self.db.transaction() as c:
            old=c.execute('SELECT * FROM jobs WHERE user_id=? AND idem=? AND created>? ORDER BY created DESC LIMIT 1',(user,idem,now-86400)).fetchone()
            if old and old['status'] in ('queued','running','succeeded'):
                if old['request_hash']!=request_hash: raise ServiceError('idempotency_conflict')
                id=old['id']
            else:
                if c.execute('SELECT COUNT(*) FROM jobs WHERE status IN ("queued","running")').fetchone()[0]>=self.config.queue_limit or c.execute('SELECT COUNT(*) FROM jobs WHERE user_id=? AND status IN ("queued","running")',(user,)).fetchone()[0]>=self.config.per_user_limit: raise ServiceError('busy',retry_after_seconds=5)
                id=new_id('job')
                c.execute('INSERT INTO jobs(id,user_id,idem,request_hash,status,stage,data,created,updated,previous_id) VALUES(?,?,?,?,?,?,?,?,?,?)',(id,user,idem,request_hash,'queued','queued',canonical(data).decode(),now,now,old['id'] if old else None))
        return self.get(user,id)
    def get(self,user,id=None):
        if id is None:
            return {'jobs':[{'job_id':r['id'],'status':r['status'],'stage':r['stage'],'created_at':r['created']} for r in self.db.all('SELECT * FROM jobs WHERE user_id=? ORDER BY created DESC LIMIT 30',(user,))]}
        r=self.db.one('SELECT * FROM jobs WHERE id=? AND user_id=?',(id,user))
        if not r: raise ServiceError('job_not_found')
        data=json.loads(r['data']); result={'job_id':id,'status':r['status'],'stage':r['stage'],'selection':data['selection'],'poll_after_seconds':2}
        if r['result']: result.update(json.loads(r['result']))
        # Private binding details are safe as opaque hashes; server paths never escape.
        if r['status']=='succeeded': result['files']=self.files.links(user,id)
        return result
    async def wait(self,user,id,seconds=0,ctx=None):
        if type(seconds) not in (int,float) or not 0<=seconds<=45: raise ServiceError('invalid_filters')
        end=time.monotonic()+seconds; stage=None
        while True:
            result=self.get(user,id)
            if ctx and stage!=result['stage']:
                stage=result['stage']; await ctx.report_progress(progress=0,total=1,message=stage)
            if result['status'] not in ACTIVE or time.monotonic()>=end: return result
            await asyncio.sleep(min(.25,max(0,end-time.monotonic())))
    def cancel(self,user,id):
        with self.db.transaction() as c:
            r=c.execute('SELECT status FROM jobs WHERE id=? AND user_id=?',(id,user)).fetchone()
            if not r: raise ServiceError('job_not_found')
            if r['status']=='queued': c.execute('UPDATE jobs SET status="cancelled",stage="cancelled",cancel=1,updated=? WHERE id=?',(time.time(),id))
            elif r['status']=='running': c.execute('UPDATE jobs SET cancel=1,updated=? WHERE id=?',(time.time(),id))
        return self.get(user,id)
