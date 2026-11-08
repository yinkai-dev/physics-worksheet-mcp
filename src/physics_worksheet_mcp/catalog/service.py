import random
from ..errors import ServiceError
from ..util import fingerprint
from ..bank.tags import TOPICS,QUESTION_TYPES

FILTER_KEYS={'banks','bank','paper','numbers','question_type','choice_mode','topics','topic_match','exclude_refs','reviewed_selection','count'}

class Catalog:
    def __init__(self,registry,grants,bindings=None):
        active=registry.active(); self.releases={}
        for bank in sorted(grants):
            if bank in active or bindings and bank in bindings:
                rel=registry.load(bank,bindings[bank]['release_id'] if bindings else None)
                if bindings and rel.sums_sha256!=bindings[bank]['release_sums_sha256']: raise ServiceError('release_drift')
                self.releases[bank]=rel
    def bindings(self): return {b:{'release_id':r.manifest['release_id'],'release_sums_sha256':r.sums_sha256} for b,r in self.releases.items()}
    def banks(self,requested=None):
        banks=requested if requested is not None else list(self.releases)
        if not isinstance(banks,list) or not banks or len(banks)!=len(set(banks)) or any(b not in self.releases for b in banks): raise ServiceError('bank_not_available')
        return banks
    def row(self,bank,qid):
        r=self.releases.get(bank)
        if not r or qid not in r.store.questions: raise ServiceError('question_not_found')
        q=r.store.questions[qid]; t=r.tags[qid]; d=r.store.documents[q['document_id']]
        answer=r.answers.answers[qid]['status'] if r.answers else 'no-package'
        return {'ref':bank+'/'+qid,'bank':bank,'paper':d['title'],'original_number':q['original_question_number'],
                'question_type':t['question_type'],'choice_mode':t.get('choice_mode',q['choice_mode']),
                'topics':t['topics'],'images':len(q['asset_ids']),'answer':answer,'question_sha256':fingerprint(q)}
    def get(self,ref):
        if not isinstance(ref,str) or ref.count('/')!=1: raise ServiceError('question_not_found')
        bank,qid=ref.split('/'); row=self.row(bank,qid); return self.releases[bank],qid,row
    def filter(self,filters=None,seed=0):
        f=filters or {}
        if not isinstance(f,dict) or set(f)-FILTER_KEYS: raise ServiceError('invalid_filters')
        banks=self.banks(f.get('banks',[f['bank']] if f.get('bank') else None))
        kind=f.get('question_type'); topics=f.get('topics',[]); mode=f.get('topic_match','any'); count=f.get('count')
        if kind is not None and kind not in (*QUESTION_TYPES,'单选题','多选题'): raise ServiceError('invalid_filters')
        if not isinstance(topics,list) or not set(topics)<=set(TOPICS) or len(topics)!=len(set(topics)) or mode not in ('any','all','exact') or mode=='exact' and not topics: raise ServiceError('invalid_filters')
        if count is not None and (type(count) is not int or not 1<=count<=300): raise ServiceError('invalid_filters')
        if 'paper' in f and not isinstance(f['paper'],str): raise ServiceError('invalid_filters')
        if f.get('choice_mode') not in (None,'single','multiple','none','unspecified'): raise ServiceError('invalid_filters')
        if 'exclude_refs' in f and (not isinstance(f['exclude_refs'],list) or any(not isinstance(x,str) for x in f['exclude_refs'])): raise ServiceError('invalid_filters')
        subtype={'单选题':'single','多选题':'multiple'}.get(kind,f.get('choice_mode'))
        rows=[]; seen=set(); duplicates=0
        for bank in banks:
            r=self.releases[bank]
            if subtype and read_schema(r)<2: raise ServiceError('tags_unavailable')
            paper_ids=None
            if f.get('paper'):
                matches=[d for d in r.store.documents.values() if f['paper'].casefold() in d['title'].casefold()]
                if not matches: raise ServiceError('paper_not_found',candidates=[d['title'] for d in r.store.documents.values()])
                if len(matches)!=1: raise ServiceError('paper_ambiguous',candidates=[d['title'] for d in matches])
                paper_ids={matches[0]['document_id']}
            numbers=f.get('numbers')
            if numbers is not None:
                if not paper_ids or not isinstance(numbers,list) or not numbers or len({str(n) for n in numbers})!=len(numbers) or any(type(n) not in (str,int) or not str(n).isdigit() or not 1<=int(n)<=999 for n in numbers): raise ServiceError('invalid_filters')
                available={str(q['original_question_number']) for q in r.store.questions.values() if q['document_id'] in paper_ids}
                if not set(map(str,numbers))<=available: raise ServiceError('number_not_found',candidates=sorted(available,key=int))
            review=r.reviews.get(f.get('reviewed_selection')) if f.get('reviewed_selection') else None
            if f.get('reviewed_selection') and review is None: raise ServiceError('review_not_found')
            for qid,q in sorted(r.store.questions.items(),key=lambda x:(x[1]['document_id'],x[1]['order'],x[0])):
                row=self.row(bank,qid); tags=set(row['topics'])
                if paper_ids and q['document_id'] not in paper_ids: continue
                if numbers is not None and str(q['original_question_number']) not in set(map(str,numbers)): continue
                if kind and row['question_type']!=('选择题' if kind in ('单选题','多选题') else kind): continue
                if subtype and row['choice_mode']!=subtype: continue
                if topics and not (bool(set(topics)&tags) if mode=='any' else set(topics)<=tags if mode=='all' else set(topics)==tags): continue
                if row['ref'] in f.get('exclude_refs',[]): continue
                if review is not None and not review[qid]['include']: continue
                doc=r.store.documents[q['document_id']]; identity=(doc['source_document']['sha256'],str(q['original_question_number']))
                if identity in seen: duplicates+=1; continue
                seen.add(identity); rows.append(row)
        total=len(rows)
        if count and total>count:
            indices=set(random.Random(str(seed)).sample(range(total),count)); rows=[r for i,r in enumerate(rows) if i in indices]
        return {'questions':rows,'candidate_count':total,'actual_count':len(rows),'requested_count':count or total,'shortfall':max(0,(count or total)-len(rows)),
                'duplicate_count':duplicates,'complete_scope':True,'basis':'filter','filters':f,'bindings':self.bindings()}

def read_schema(r):
    from ..util import read_json
    return read_json(r.root/'tags.json')['schema_version']
