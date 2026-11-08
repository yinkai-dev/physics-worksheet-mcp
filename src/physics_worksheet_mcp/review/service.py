import json
from ..util import canonical,fingerprint
from ..storage import new_id
from ..errors import ServiceError

def page(catalog,data):
    result=[]; size=0
    for row in data['selection']['questions'][data['offset']:]:
        r,qid,_=catalog.get(row['ref']); text=r.store.questions[qid]['retrieval_text']
        item={**row,'retrieval_text':text}; n=len(canonical(item))
        if result and (size+n>30000 or len(result)>=15): break
        result.append(item); size+=n
    return result

def review(db,user,catalog,review_id=None,banks=None,topic=None,filters=None,decisions=None):
    if not review_id:
        if not isinstance(topic,str) or not topic.strip() or len(topic)>1000: raise ServiceError('invalid_filters')
        selection=catalog.filter({**(filters or {}),'banks':catalog.banks(banks)})
        if (filters or {}).get('count'): raise ServiceError('invalid_filters')
        data={'topic':topic,'selection':selection,'offset':0,'decisions':[],'last_batch':None,'selection_id':None}
        review_id=new_id('rev'); db.execute('INSERT INTO reviews VALUES(?,?,?)',(review_id,user,canonical(data).decode()))
    with db.transaction() as c:
        record=c.execute('SELECT data FROM reviews WHERE id=? AND user_id=?',(review_id,user)).fetchone()
        if not record: raise ServiceError('review_not_found')
        data=json.loads(record['data'])
        if data['selection']['bindings']!=catalog.bindings(): raise ServiceError('question_drift')
        if decisions is not None:
            batch=fingerprint(decisions)
            if batch!=data['last_batch']:
                expected={r['ref'] for r in page(catalog,data)}
                if not isinstance(decisions,list) or len(decisions)!=len(expected) or {d.get('ref') for d in decisions}!=expected or any(set(d)!={'ref','include','reason'} or type(d.get('include')) is not bool or not isinstance(d.get('reason'),str) or not 1<=len(d['reason'])<=2000 for d in decisions): raise ServiceError('review_page_mismatch')
                if not expected: raise ServiceError('review_incomplete')
                data['decisions'].extend(decisions); data['offset']+=len(expected); data['last_batch']=batch
        if data['offset']==len(data['selection']['questions']) and not data['selection_id']:
            included={d['ref'] for d in data['decisions'] if d['include']}; sel={**data['selection'],'questions':[r for r in data['selection']['questions'] if r['ref'] in included],'basis':'topic_review','review_id':review_id,'decisions':data['decisions']}
            id=new_id('sel'); c.execute('INSERT INTO selections VALUES(?,?,?,strftime("%s","now"))',(id,user,canonical(sel).decode())); data['selection_id']=id
        c.execute('UPDATE reviews SET data=? WHERE id=?',(canonical(data).decode(),review_id))
    total=len(data['selection']['questions'])
    return {'review_id':review_id,'topic':data['topic'],'total':total,'reviewed':data['offset'],'page':page(catalog,data),
            'complete':data['offset']==total,'included':sum(d['include'] for d in data['decisions']),
            'excluded':sum(not d['include'] for d in data['decisions']),'selection_id':data['selection_id']}
