from copy import deepcopy
from pathlib import Path
from ..catalog.service import Catalog
from ..errors import ServiceError
from ..util import sha,write_json,fingerprint
from ..render.contract import validate_spec

def resolve_selection(registry,selection,title,answers,out):
    out=Path(out); out.mkdir(parents=True,exist_ok=True)
    catalog=Catalog(registry,list(selection['bindings']),selection['bindings'])
    spec={'schema_version':'1.2','document':{'title':title,'subtitle':'原题来源可追溯；标签为 AI 分类','include_answers':False},'questions':[]}
    proof={'resolution':'bank-release-v1','bindings':selection['bindings'],'selection_basis':{k:v for k,v in selection.items() if k!='questions'},
           'sources':[],'boundary':'Engineering source and image checks only; physics correctness, teacher review and learner outcomes unverified.'}
    assets={}; answers_count=0; missing=0; without=set(); seen=set()
    for i,row in enumerate(selection['questions'],1):
        if set(row)-{'ref','bank','paper','original_number','question_type','choice_mode','topics','images','answer','question_sha256'}: raise ServiceError('model_content_forbidden')
        r,qid,current=catalog.get(row['ref']); q=r.store.questions[qid]; d=r.store.documents[q['document_id']]
        if fingerprint(q)!=row['question_sha256']: raise ServiceError('question_drift')
        identity=(d['source_document']['sha256'],q['original_question_number'])
        if identity in seen: raise ServiceError('duplicate_question')
        seen.add(identity)
        canonical=r.store.resolve({'question_id':qid,'source_excerpt':q['retrieval_text']})
        oq={'id':f'q{i}','body':deepcopy(q['body']),'choices':deepcopy(q['choices']),
            'choice_mode':'unspecified' if current['choice_mode']=='none' else current['choice_mode'],
            'points':None,'review_status':'retrieved-complete','representation':q['representation'],
            'original_number':q['original_question_number'],'assets':[],
            'source':{'kb_name':r.manifest['bank_name'],'title':d['title'],'page':q['page'],'chunk_id':'question-package:'+qid,
                      'score':None,'source_excerpt':q['retrieval_text'],'content_sha256':q['retrieval_text_sha256']}}
        if 'layout' in q: oq['layout']=deepcopy(q['layout'])
        evidence={'display_number':i,'bank_id':current['bank'],'release_id':r.manifest['release_id'],'question_id':qid,
                  'paper_title':d['title'],'source_document_id':q['document_id'],'source_document_sha256':d['source_document']['sha256'],
                  **{k:q[k] for k in ('original_question_number','page','representation','retrieval_text_sha256','body_sha256','choices_sha256','asset_relation_sha256','review')},
                  'tags':r.tags[qid],'assets':[],'answer':{'status':'no-package' if not r.answers else 'not-requested'}}
        def stage(asset,data,answer=False):
            aid=asset['asset_id']; ext='.png' if asset['media_type']=='image/png' else '.jpg'
            relative='verified-assets/'+aid+ext; target=out/relative; target.parent.mkdir(exist_ok=True)
            if sha(data)!=aid: raise ServiceError('source_hash_drift')
            if aid not in assets: target.write_bytes(data)
            if sha(target.read_bytes())!=aid: raise ServiceError('asset_copy_drift')
            assets[aid]={'path':relative,'sha256':aid,**{k:asset[k] for k in ('media_type','width','height','bytes')}}
            if answer:
                return {'asset_id':aid,'role':'answer_figure','required':True,'order':asset['order'],'placement':'in_content','width_mm':asset.get('width_mm',100)}
            return {k:deepcopy(asset[k]) for k in ('asset_id','role','required','order','placement','width_mm','source_layout') if k in asset}
        for asset in canonical['assets']:
            oq['assets'].append(stage(asset,Path(asset['resolved_path']).read_bytes()))
            evidence['assets'].append({k:v for k,v in asset.items() if k!='resolved_path'})
        if answers:
            if r.answers is None: without.add(current['bank'])
            else:
                ap=r.answers; ar=ap.answers[qid]; evidence['answer']={**ar,**ap.evidence()}
                if ar['status']=='missing': missing+=1
                else:
                    aa=[stage(ap.assets[b['asset_id']],ap.asset_bytes[b['asset_id']],True) for b in ar['body'] if b['type']=='figure']
                    oq['answer']={'origin':'source','key':ar['key'],'body':deepcopy(ar['body']),'assets':aa}; answers_count+=1
        spec['questions'].append(oq); proof['sources'].append(evidence)
    if answers_count:
        spec['schema_version']='1.3'; spec['document']['include_answers']=True
    validate_spec(spec)
    write_json(out/'exam-spec.json',spec)
    write_json(out/'verified-assets.json',{'schema_version':'1.0','assets':assets,'provider':selection['bindings'],'question_count':len(spec['questions'])})
    proof['exam_spec_sha256']=sha((out/'exam-spec.json').read_bytes()); proof['verified_assets_sha256']=sha((out/'verified-assets.json').read_bytes())
    proof['answers']={'requested':answers,'with_source_answer':answers_count,'missing':missing,'banks_without_package':sorted(without)}
    write_json(out/'source-resolution.json',proof)
    return spec,proof
