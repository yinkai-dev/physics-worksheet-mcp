from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import shutil
import tarfile
import tempfile
import time
import os
from .package import VerifiedQuestionStore
from .tags import validate_rows
from .answers import VerifiedAnswerPackage
from ..errors import ServiceError
from ..util import read_json,write_json,sha,make_sums,verify_tree,identifier,confined,file_lock,fingerprint

@dataclass
class Release:
    root: Path
    manifest: dict
    store: VerifiedQuestionStore
    tags: dict
    reviews: dict
    answers: object
    sums_sha256: str

def readable_tree(root):
    if os.name=='posix':
        for p in Path(root).rglob('*'): os.chmod(p,0o755 if p.is_dir() else 0o644)
        os.chmod(root,0o755)

def verify_release(path):
    path=Path(path); sums=verify_tree(path); m=read_json(path/'release.json')
    if m.get('schema')!='pwmcp.bank-release/1' or identifier(m['release_id'])!=path.name: raise ServiceError('release_identity_invalid')
    store=VerifiedQuestionStore(path/'question-package',expected_kb_name=m['bank_name'])
    tags=validate_rows(read_json(path/'tags.json'),store,m['legacy']['provider'])
    reviews={p.stem:validate_rows(read_json(p),store,m['legacy']['provider'],True) for p in sorted((path/'reviewed-selections').glob('*.json'))}
    answers=None
    if m['components'].get('answers'):
        provider=SimpleNamespace(kb_name=m['bank_name'],evidence=lambda:m['legacy']['provider'])
        a=m['components']['answers']
        answers=VerifiedAnswerPackage(path/'answers'/a['answer_package_id'],provider,store,{q:r.get('choice_mode',store.questions[q]['choice_mode']) for q,r in tags.items()})
        if answers.sha256!=a['package_sha256']: raise ServiceError('answer_binding_drift')
    counts={'question_count':len(store.questions),'document_count':len(store.documents),'required_asset_count':len(store.assets)}
    if any(m['components']['question_package'].get(k)!=v for k,v in counts.items()): raise ServiceError('release_count_drift')
    qmeta=m['components']['question_package']
    if qmeta.get('sums_sha256') and qmeta['sums_sha256']!=sha((path/'question-package/SHA256SUMS').read_bytes()): raise ServiceError('release_component_drift')
    tmeta=m['components'].get('tags')
    if tmeta and (tmeta.get('sha256')!=sha((path/'tags.json').read_bytes()) or tmeta.get('covered')!=len(tags)): raise ServiceError('release_component_drift')
    if answers and (a.get('with_answer')!=sum(r['status']=='source' for r in answers.answers.values()) or a.get('missing')!=sum(r['status']=='missing' for r in answers.answers.values())): raise ServiceError('release_count_drift')
    return Release(path,m,store,tags,reviews,answers,sums)

def import_legacy(provider_dir,tags,selections,answers,bank_id,out):
    from .legacy import SourceProviderRegistry
    p=Path(provider_dir); registry=SourceProviderRegistry(p.parents[2]); provider=registry.resolve_revision(p.parent.name,p.name)
    # Verify the active registry fingerprint as well as every original revision file.
    if (p.parents[2]/'registry.json').exists(): provider=registry.resolve(provider.kb_name)
    store=VerifiedQuestionStore(p/'question-package',expected_kb_name=provider.kb_name)
    raw=read_json(p/'raw-manifest.json')
    matched=0
    # Original provider raw manifests map raw/<qid>.md to byte hashes.
    raw_rows=raw.get('files',raw)
    if isinstance(raw_rows,list): raw_rows={r.get('path',r.get('name',r.get('relative_path'))):r for r in raw_rows}
    for qid,q in store.questions.items():
        text=f"# {store.documents[q['document_id']]['title']} 原第{q['original_question_number']}题\n\nquestion_id: {qid}\n\n{q['retrieval_text']}\n"
        expected=raw_rows.get(f'raw/{qid}.md',raw_rows.get(f'{qid}.md'))
        if isinstance(expected,dict): expected=expected.get('sha256',expected.get('content_sha256'))
        if expected!=sha(text.encode()): raise ServiceError('raw_text_crosscheck_failed')
        matched+=1
    manifest={'schema':'pwmcp.bank-release/1','bank_id':identifier(bank_id),'bank_name':provider.kb_name,
              'created_at':time.time(),'previous_release_id':None,'components':{},
              'legacy':{'provider':provider.evidence(),'registry_sha256':provider.registry_sha256,'hashes':provider.manifest['hashes'],
                        'embedding_signature':provider.manifest['embedding_signature'],'raw_text_crosscheck':f'{matched}/{len(store.questions)}'},
              'verified_with':{'tool':'pwmcp admin bank import-legacy','code_version':'0.1.0'}}
    parent=Path(out)/bank_id/'releases'; parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=parent,prefix='.import-') as tmp:
        staging=Path(tmp)/'release'; staging.mkdir()
        shutil.copytree(p/'question-package',staging/'question-package')
        shutil.copyfile(tags,staging/'tags.json')
        tagdata=read_json(tags); validate_rows(tagdata,store,provider.evidence())
        manifest['components']['question_package']={'question_count':len(store.questions),'document_count':len(store.documents),'required_asset_count':len(store.assets),'sums_sha256':sha((p/'question-package/SHA256SUMS').read_bytes())}
        manifest['components']['tags']={'sha256':sha(Path(tags).read_bytes()),'schema_version':tagdata['schema_version'],'covered':len(store.questions)}
        review_data=[]; (staging/'reviewed-selections').mkdir()
        key=sha(provider.kb_name.encode())[:24]+'-'
        for path in sorted(Path(selections).glob(key+'*.json')) if selections else []:
            data=read_json(path); rows=validate_rows(data,store,provider.evidence(),True); name=path.name[len(key):]
            shutil.copyfile(path,staging/'reviewed-selections'/name)
            review_data.append({'name':Path(name).stem,'sha256':sha(path.read_bytes()),'question_count':sum(r['include'] for r in rows.values())})
        manifest['components']['reviewed_selections']=review_data
        manifest['components']['answers']=None
        if answers and (Path(answers)/'active.json').exists():
            pointer=read_json(Path(answers)/'active.json'); ap=VerifiedAnswerPackage(Path(answers)/pointer['answer_package_id'],provider,store,{r['question_id']:r.get('choice_mode',store.questions[r['question_id']]['choice_mode']) for r in tagdata['questions']})
            if ap.evidence()!=pointer: raise ServiceError('answer_pointer_drift')
            shutil.copytree(ap.root,staging/'answers'/ap.root.name)
            manifest['components']['answers']={**pointer,'with_answer':sum(r['status']=='source' for r in ap.answers.values()),'missing':sum(r['status']=='missing' for r in ap.answers.values())}
        # Identifier derives from all source component bindings (not a circular manifest hash).
        rel='rel-'+fingerprint({'components':manifest['components'],'legacy':manifest['legacy']})[:20]
        manifest['release_id']=rel; write_json(staging/'release.json',manifest); make_sums(staging)
        final=parent/rel
        if final.exists(): return verify_release(final)
        staging.rename(final); readable_tree(final)
    return verify_release(final)

class BankRegistry:
    def __init__(self,root): self.root=Path(root)
    def active(self): return read_json(self.root/'active.json') if (self.root/'active.json').exists() else {}
    def load(self,bank,release=None):
        identifier(bank); release=release or self.active().get(bank,{}).get('release_id')
        if not release: raise ServiceError('bank_not_available')
        return verify_release(confined(self.root,f'{bank}/releases/{identifier(release)}'))
    def activate(self,bank,release):
        obj=self.load(bank,release)
        with file_lock(self.root/'.admin.lock'):
            active=self.active(); generation=str(time.time_ns()); write_json(self.root/'pointers'/f'{generation}.json',active)
            active[bank]={'release_id':release,'release_sums_sha256':obj.sums_sha256}; write_json(self.root/'active.json',active,0o644)
        return active
    def rollback(self,generation):
        with file_lock(self.root/'.admin.lock'):
            old=read_json(confined(self.root,f'pointers/{identifier(generation)}.json'))
            for bank,b in old.items(): self.load(bank,b['release_id'])
            write_json(self.root/'pointers'/f'{time.time_ns()}.json',self.active()); write_json(self.root/'active.json',old,0o644)
        return old
    def install(self,archive,expected_sha):
        archive=Path(archive)
        if sha(archive.read_bytes())!=expected_sha: raise ServiceError('archive_hash_drift')
        self.root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.root) as work:
            with tarfile.open(archive) as tar:
                members=tar.getmembers(); total=0
                for m in members:
                    confined(work,m.name); total+=m.size
                    if not(m.isdir() or m.isfile()) or total>2*1024**3: raise ServiceError('unsafe_archive')
                tar.extractall(work,filter='data')
            roots=list(Path(work).glob('*/releases/*'))
            if len(roots)!=1: raise ServiceError('archive_layout_invalid')
            r=verify_release(roots[0]); target=self.root/r.manifest['bank_id']/'releases'/r.manifest['release_id']
            if not target.exists(): target.parent.mkdir(parents=True,exist_ok=True); shutil.copytree(r.root,target); readable_tree(target)
            return verify_release(target)
