from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing
from ..util import fingerprint,write_json,read_json,make_sums,verify_tree,confined
from ..errors import ServiceError

def build_index(release,index_root):
    m=release.manifest; binding={'bank_id':m['bank_id'],'release_id':m['release_id'],'release_sums_sha256':release.sums_sha256,
                              'question_count':len(release.store.questions),'semantic':'unavailable','format':'fts5-trigram-v1'}
    id='idx-'+fingerprint(binding)[:20]; parent=Path(index_root)/m['bank_id']/m['release_id']; parent.mkdir(parents=True,exist_ok=True)
    target=parent/id
    if not target.exists():
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            work=Path(tmp)/id; work.mkdir()
            with closing(sqlite3.connect(work/'fts.sqlite3')) as db, db:
                db.execute("CREATE VIRTUAL TABLE fts USING fts5(ref UNINDEXED,text,content_sha256 UNINDEXED,tokenize='trigram')")
                for qid,q in release.store.questions.items():
                    doc=release.store.documents[q['document_id']]; t=release.tags[qid]
                    text=doc['title']+' 原第'+q['original_question_number']+'题 '+q['retrieval_text']+' '+' '.join(t['topics'])
                    db.execute('INSERT INTO fts VALUES(?,?,?)',(m['bank_id']+'/'+qid,text,q['retrieval_text_sha256']))
            write_json(work/'index.json',{**binding,'index_id':id,'embedding_calls':0,'embedding_tokens':0},0o644); make_sums(work); work.rename(target)
    verify_tree(target); write_json(parent/'active.json',{'index_id':id},0o644)
    return read_json(target/'index.json')

def query_index(release,index_root,query,eligible):
    parent=Path(index_root)/release.manifest['bank_id']/release.manifest['release_id']; pointer=parent/'active.json'
    if not pointer.exists(): return None
    meta=read_json(pointer); index=confined(parent,meta['index_id']); verify_tree(index); binding=read_json(index/'index.json')
    if binding['release_sums_sha256']!=release.sums_sha256 or binding['release_id']!=release.manifest['release_id']: raise ServiceError('index-binding-drift')
    if not eligible: return []
    with closing(sqlite3.connect('file:'+str(index/'fts.sqlite3')+'?mode=ro',uri=True)) as db:
        sql='SELECT ref,content_sha256 FROM fts WHERE text MATCH ? AND ref IN ('+','.join('?' for _ in eligible)+') ORDER BY bm25(fts),ref LIMIT 50'
        matches=db.execute(sql,['"'+query.replace('"','""')+'"',*eligible]).fetchall()
    for ref,digest in matches:
        if release.store.questions[ref.split('/',1)[1]]['retrieval_text_sha256']!=digest: raise ServiceError('index-binding-drift')
    return [ref for ref,_ in matches]
