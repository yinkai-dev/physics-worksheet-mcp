"""Derived FTS5 and optional embedding ranking. Every Top-K result is partial."""
from typing import Protocol
import sqlite3
import numpy as np
from ..util import fingerprint
from ..errors import ServiceError

class Embedder(Protocol):
    model: str
    def embed(self,texts:list[str])->np.ndarray: ...

class FakeEmbedder:
    model='synthetic-hash-v1'
    def embed(self,texts):
        rows=[]
        for t in texts:
            row=np.zeros(32,dtype=np.float32)
            for c in t: row[ord(c)%32]+=1
            rows.append(row/max(float(np.linalg.norm(row)),1e-12))
        return np.array(rows)

def search(catalog,query,banks=None,filters=None,top_k=10,embedder=None,index_root=None):
    if not isinstance(query,str) or not query.strip() or len(query)>2000 or type(top_k) is not int or not 1<=top_k<=20: raise ServiceError('invalid_filters')
    rows=catalog.filter({**(filters or {}),'banks':catalog.banks(banks)})['questions']
    db=sqlite3.connect(':memory:'); db.execute("CREATE VIRTUAL TABLE fts USING fts5(ref UNINDEXED,text,tokenize='trigram')")
    texts=[]
    for row in rows:
        r,qid,_=catalog.get(row['ref']); text=row['paper']+' 原第'+row['original_number']+'题 '+r.store.questions[qid]['retrieval_text']+' '+' '.join(row['topics'])
        texts.append(text); db.execute('INSERT INTO fts VALUES(?,?)',(row['ref'],text))
    if len(query)<3:
        lexical=[r['ref'] for r,t in zip(rows,texts) if query.casefold() in t.casefold()]
    else:
        lexical=[r[0] for r in db.execute('SELECT ref FROM fts WHERE text MATCH ? ORDER BY bm25(fts),ref LIMIT 50',('"'+query.replace('"','""')+'"',))]
    db.close()
    if index_root is not None and len(query)>=3:
        from .index import query_index
        persisted=[]; complete=True
        for bank in catalog.banks(banks):
            eligible=[r['ref'] for r in rows if r['bank']==bank]
            found=query_index(catalog.releases[bank],index_root,query,eligible)
            if found is None: complete=False; break
            persisted.extend(found)
        # Per-bank ranked lists are fused deterministically; every hit remains partial.
        if complete: lexical=list(dict.fromkeys(persisted))
    ranks={ref:1/(60+i) for i,ref in enumerate(lexical,1)}; semantic=[]
    if embedder and rows:
        vectors=embedder.embed(texts); q=embedder.embed([query])[0]
        scores=vectors@q; semantic=[rows[int(i)]['ref'] for i in np.argsort(-scores,kind='stable')[:50]]
        for i,ref in enumerate(semantic,1): ranks[ref]=ranks.get(ref,0)+1/(60+i)
    byref={r['ref']:r for r in rows}
    hits=[{**byref[ref],'score':score,'snippet':texts[next(i for i,r in enumerate(rows) if r['ref']==ref)][:240],
           'matched_by':[name for name,lst in (('fts',lexical),('semantic',semantic)) if ref in lst]} for ref,score in sorted(ranks.items(),key=lambda x:(-x[1],x[0]))[:top_k]]
    return {'hits':hits,'coverage':'partial','semantic':'ok' if embedder else 'unavailable','index_id':'idx-'+fingerprint(catalog.bindings())[:16]}
