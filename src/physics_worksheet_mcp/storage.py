from contextlib import contextmanager
import sqlite3
import time
import uuid
import os
from .util import canonical

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS tokens(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,hash TEXT NOT NULL UNIQUE,prefix TEXT NOT NULL,expires REAL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS grants(user_id TEXT,bank_id TEXT,PRIMARY KEY(user_id,bank_id));
CREATE TABLE IF NOT EXISTS selections(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,data TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,idem TEXT NOT NULL,request_hash TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL,data TEXT NOT NULL,result TEXT,created REAL NOT NULL,updated REAL NOT NULL,lease REAL,retries INTEGER DEFAULT 0,cancel INTEGER DEFAULT 0,previous_id TEXT);
CREATE INDEX IF NOT EXISTS jobs_idem ON jobs(user_id,idem,created);
CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY,user_id TEXT NOT NULL,job_id TEXT NOT NULL,kind TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,pages INTEGER NOT NULL,bytes INTEGER NOT NULL,name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS downloads(hash TEXT PRIMARY KEY,file_id TEXT NOT NULL,user_id TEXT NOT NULL,expires REAL NOT NULL,revoked INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at REAL,token_id TEXT,tool TEXT,args_hash TEXT,code TEXT,elapsed_ms REAL);
CREATE TABLE IF NOT EXISTS rate(token_id TEXT,at REAL);
CREATE INDEX IF NOT EXISTS rate_time ON rate(at);
CREATE TABLE IF NOT EXISTS heartbeat(name TEXT PRIMARY KEY,at REAL);
CREATE TABLE IF NOT EXISTS embedding_usage(id INTEGER PRIMARY KEY,at REAL,model TEXT,tokens INTEGER,elapsed_ms REAL);
'''

def new_id(prefix): return prefix+'_'+uuid.uuid4().hex

class Database:
    def __init__(self,path):
        self.path=path; path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as c: c.executescript(SCHEMA)
        if os.name=='posix': os.chmod(path,0o600)
    def _open(self):
        c=sqlite3.connect(self.path,timeout=30); c.row_factory=sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=30000')
        return c
    @contextmanager
    def connect(self):
        c=self._open()
        try:
            with c: yield c
        finally: c.close()
    @contextmanager
    def transaction(self):
        c=self._open()
        try:
            c.execute('BEGIN IMMEDIATE'); yield c; c.commit()
        except BaseException: c.rollback(); raise
        finally: c.close()
    def execute(self,sql,args=()):
        with self.connect() as c: return c.execute(sql,args).rowcount
    def one(self,sql,args=()):
        with self.connect() as c: return c.execute(sql,args).fetchone()
    def all(self,sql,args=()):
        with self.connect() as c: return c.execute(sql,args).fetchall()
    def save_selection(self,user,data):
        id=new_id('sel'); self.execute('INSERT INTO selections VALUES(?,?,?,?)',(id,user,canonical(data).decode(),time.time())); return id
