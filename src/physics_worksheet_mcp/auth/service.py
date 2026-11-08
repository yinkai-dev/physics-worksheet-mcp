from dataclasses import dataclass
from contextvars import ContextVar
import secrets
import time
from ..errors import ServiceError
from ..storage import new_id
from ..util import sha

@dataclass(frozen=True)
class Principal:
    user_id: str
    token_id: str
    grants: tuple

principal = ContextVar('pwmcp_principal')

class Auth:
    def __init__(self,db): self.db=db
    def add_user(self,name):
        id=new_id('usr'); self.db.execute('INSERT INTO users(id,name) VALUES(?,?)',(id,name)); return id
    def issue(self,user,expires=None):
        if not self.db.one('SELECT id FROM users WHERE id=?',(user,)): raise ServiceError('user_not_found')
        token='pwm_'+secrets.token_hex(4)+'_'+secrets.token_urlsafe(32)
        self.db.execute('INSERT INTO tokens(id,user_id,hash,prefix,expires) VALUES(?,?,?,?,?)',(new_id('tok'),user,sha(token.encode()),token[:12],expires)); return token
    def verify(self,token):
        r=self.db.one('SELECT t.*,u.status FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.hash=?',(sha(token.encode()),))
        if not r or r['revoked'] or (r['expires'] is not None and r['expires']<=time.time()): raise ServiceError('unauthorized')
        if r['status']!='active': raise ServiceError('user_disabled')
        grants=tuple(x['bank_id'] for x in self.db.all('SELECT bank_id FROM grants WHERE user_id=? ORDER BY bank_id',(r['user_id'],)))
        return Principal(r['user_id'],r['id'],grants)
