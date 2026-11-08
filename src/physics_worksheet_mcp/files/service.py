from pathlib import Path
import secrets
import time
from ..errors import ServiceError
from ..util import sha,confined

class FileService:
    def __init__(self,db,config): self.db,self.config=db,config
    def checked(self,row):
        path=confined(self.config.jobs,row['path'])
        if not path.is_file() or sha(path.read_bytes())!=row['sha256']: raise ServiceError('artifact_drift')
        return path
    def links(self,user,job,kinds=None):
        record=self.db.one('SELECT status FROM jobs WHERE id=? AND user_id=?',(job,user))
        if not record: raise ServiceError('job_not_found')
        if kinds is not None and (not isinstance(kinds,list) or not set(kinds)<={'worksheet','answers'}): raise ServiceError('invalid_filters')
        result=[]
        for r in self.db.all('SELECT * FROM files WHERE job_id=? AND user_id=? ORDER BY kind DESC',(job,user)):
            if kinds is not None and r['kind'] not in kinds: continue
            self.checked(r); token=secrets.token_urlsafe(32); expires=time.time()+self.config.download_ttl
            self.db.execute('INSERT INTO downloads(hash,file_id,user_id,expires) VALUES(?,?,?,?)',(sha(token.encode()),r['id'],user,expires))
            result.append({k:r[k] for k in ('id','kind','name','sha256','pages','bytes')} | {'download_url':self.config.base_url+'/d/'+token,'expires_at':expires})
        return result
    def download(self,token):
        r=self.db.one('SELECT f.*,d.expires,d.revoked FROM downloads d JOIN files f ON f.id=d.file_id AND f.user_id=d.user_id JOIN users u ON u.id=d.user_id WHERE d.hash=? AND u.status="active"',(sha(token.encode()),))
        if not r or r['revoked'] or r['expires']<=time.time(): raise ServiceError('file_not_found')
        # Download handles are bearer capabilities; revocation removes access immediately.
        return r,self.checked(r)
