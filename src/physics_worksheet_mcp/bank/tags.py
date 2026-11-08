from ..util import fingerprint, read_json, sha
from ..errors import ServiceError

TOPICS = ('力学', '电学', '光学', '热学', '近代物理')
QUESTION_TYPES = ('选择题', '实验题', '计算题')
question_fingerprint = fingerprint

def validate_rows(data, store, provider, review=False):
    if data.get('provider')!=provider or not data.get('reviewed_by'):
        raise ServiceError('tags_provider_drift')
    rows=data.get('questions',[])
    if len(rows)!=len(store.questions) or {r.get('question_id') for r in rows}!=set(store.questions):
        raise ServiceError('tags_coverage_invalid')
    for r in rows:
        if r.get('question_sha256')!=fingerprint(store.questions[r['question_id']]) or not r.get('reason'):
            raise ServiceError('tags_content_drift')
        if review:
            if type(r.get('include')) is not bool: raise ServiceError('review_invalid')
        elif (r.get('question_type') not in QUESTION_TYPES or not r.get('topics')
              or not set(r['topics'])<=set(TOPICS) or len(set(r['topics']))!=len(r['topics'])):
            raise ServiceError('tags_invalid')
    if not review:
        if data.get('schema_version') not in (1,2): raise ServiceError('tags_invalid')
        if data['schema_version']==2:
            profiles=data.get('paper_structure',{})
            if set(profiles)!=set(store.documents) or not data.get('structure_basis'): raise ServiceError('tags_structure_invalid')
            expected={'profile':'zhejiang-18-v1','single':[1,10],'multiple':[11,13],'experiment':[14,14],'calculation':[15,18]}
            for doc, profile in profiles.items():
                if profile!=expected: raise ServiceError('tags_structure_invalid')
                qs=[q for q in store.questions.values() if q['document_id']==doc]
                if sorted(int(q['original_question_number']) for q in qs)!=list(range(1,19)): raise ServiceError('tags_structure_invalid')
            for r in rows:
                n=int(store.questions[r['question_id']]['original_question_number'])
                mode='single' if n<=10 else 'multiple' if n<=13 else 'none'
                kind='选择题' if n<=13 else '实验题' if n==14 else '计算题'
                if r.get('choice_mode')!=mode or r['question_type']!=kind: raise ServiceError('tags_structure_invalid')
    return {r['question_id']:r for r in rows}
