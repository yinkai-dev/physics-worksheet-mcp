import copy
import io
import json
import tarfile
import time
from pathlib import Path
import pytest
from physics_worksheet_mcp.bank.release import verify_release,BankRegistry
from physics_worksheet_mcp.catalog.service import Catalog
from physics_worksheet_mcp.errors import ServiceError
from physics_worksheet_mcp.jobs.worker import recover
from physics_worksheet_mcp.resolve.service import resolve_selection
from physics_worksheet_mcp.review.service import review
from physics_worksheet_mcp.search.service import search,FakeEmbedder
from physics_worksheet_mcp.storage import new_id
from physics_worksheet_mcp.util import make_sums,read_json,write_json,sha

@pytest.mark.parametrize('match,topics,expected',[('any',['力学'],1),('all',['力学'],1),('exact',['力学'],1),('any',['力学','电学'],1),('all',['力学','电学'],0),('exact',['力学','电学'],0)])
def test_tag_modes(system,match,topics,expected):
    assert system['catalog'].filter({'topics':topics,'topic_match':match})['actual_count']==expected

def test_shortfall_zero_exclusion_and_sampling(system):
    cat=system['catalog']; sel=cat.filter({'count':8},'fixed')
    assert (sel['actual_count'],sel['shortfall'])==(1,7)
    assert cat.filter({'count':8},'fixed')==sel
    assert cat.filter({'exclude_refs':[sel['questions'][0]['ref']]})['actual_count']==0
    assert cat.filter({'topics':['光学']})['actual_count']==0

@pytest.mark.parametrize('filters',[{'topics':['unknown']},{'topic_match':'invalid'},{'topic_match':'exact'},{'count':0},{'count':True},{'question_type':'invented'},{'answer':'model'},{'numbers':[13]},{'banks':['hidden-bank']}])
def test_invalid_filters(system,filters):
    with pytest.raises(ServiceError): system['catalog'].filter(filters)

def test_exact_paper_number_and_unavailable_subtype(system):
    cat=system['catalog']; assert cat.filter({'paper':'合成','numbers':[13]})['actual_count']==1
    with pytest.raises(ServiceError,match='paper_not_found'): cat.filter({'paper':'absent'})
    with pytest.raises(ServiceError,match='number_not_found'): cat.filter({'paper':'合成','numbers':[14]})
    with pytest.raises(ServiceError,match='tags_unavailable'): cat.filter({'question_type':'单选题'})

def test_cross_bank_deduplication(system):
    from make_demo import make_release
    r=make_release(system['config'].banks,bank='demo-copy'); system['registry'].activate('demo-copy',r.root.name)
    cat=Catalog(system['registry'],['demo-bank','demo-copy']); sel=cat.filter({})
    assert (sel['candidate_count'],sel['duplicate_count'])==(1,1)

@pytest.mark.parametrize('target',['question-package/questions.jsonl','question-package/asset-manifest.jsonl','tags.json','release.json'])
def test_release_byte_drift(system,target):
    r=system['catalog'].releases['demo-bank']; p=r.root/target; p.write_bytes(p.read_bytes()+b' ')
    with pytest.raises((ServiceError,ValueError)): verify_release(r.root)

def test_extra_file_and_missing_image(system):
    r=system['catalog'].releases['demo-bank']; p=r.root/'extra'; p.write_bytes(b'x')
    with pytest.raises(ServiceError): verify_release(r.root)
    p.unlink(); next((r.root/'question-package/assets').iterdir()).unlink()
    with pytest.raises((ServiceError,ValueError)): verify_release(r.root)

def test_recomputed_hashes_do_not_bypass_tag_fingerprint(system):
    r=system['catalog'].releases['demo-bank']; tags=read_json(r.root/'tags.json'); tags['questions'][0]['question_sha256']='b'*64
    write_json(r.root/'tags.json',tags); make_sums(r.root)
    with pytest.raises(ServiceError,match='tags_content_drift'): verify_release(r.root)

@pytest.mark.parametrize('name,kind',[('../escape','file'),('/absolute','file'),('bank/sym','symlink'),('bank/hard','hardlink')])
def test_unsafe_archive(system,name,kind):
    archive=system['tmp']/'unsafe.tar'
    with tarfile.open(archive,'w') as tar:
        m=tarfile.TarInfo(name)
        if kind!='file': m.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE; m.linkname='../escape'; tar.addfile(m)
        else: m.size=1; tar.addfile(m,io.BytesIO(b'x'))
    with pytest.raises(ServiceError): system['registry'].install(archive,sha(archive.read_bytes()))

def test_resolve_from_server_and_reject_model_answer(system):
    sel=system['catalog'].filter({}); spec,proof=resolve_selection(system['registry'],sel,'合成练习',True,system['tmp']/'resolved')
    assert proof['resolution']=='bank-release-v1'; assert len(spec['questions'][0]['assets'])==1
    assert proof['answers']['banks_without_package']==['demo-bank']
    sel['questions'][0]['answer']='MODEL BODY'
    with pytest.raises(ServiceError,match='model_content_forbidden'): resolve_selection(system['registry'],sel,'合成练习',True,system['tmp']/'metadata-only')
    sel['questions'][0]['body']=[]
    with pytest.raises(ServiceError,match='model_content_forbidden'): resolve_selection(system['registry'],sel,'合成练习',True,system['tmp']/'rejected')

def test_selection_fingerprint_drift(system):
    sel=system['catalog'].filter({}); sel['questions'][0]['question_sha256']='0'*64
    with pytest.raises(ServiceError,match='question_drift'): resolve_selection(system['registry'],sel,'合成练习',False,system['tmp']/'rejected')

@pytest.mark.parametrize('query',['回旋加速器','图','合成选项','不存在的主题','\\frac'])
def test_search_always_partial(system,query):
    result=search(system['catalog'],query)
    assert result['coverage']=='partial'; assert result['semantic']=='unavailable'

def test_fake_embedding_interface(system):
    result=search(system['catalog'],'运动',embedder=FakeEmbedder())
    assert result['semantic']=='ok' and len(result['hits'])==1 and result['coverage']=='partial'

def test_complete_topic_review_and_duplicate_submit(system):
    db=system['db']; cat=system['catalog']; user=system['user']; first=review(db,user,cat,banks=['demo-bank'],topic='合成主题')
    with pytest.raises(ServiceError,match='review_page_mismatch'): review(db,user,cat,first['review_id'],decisions=[])
    decisions=[{'ref':first['page'][0]['ref'],'include':True,'reason':'synthetic judgment'}]
    result=review(db,user,cat,first['review_id'],decisions=decisions)
    assert result['complete'] and result['reviewed']==result['total']==1
    assert review(db,user,cat,first['review_id'],decisions=decisions)['selection_id']==result['selection_id']
    with pytest.raises(ServiceError,match='review_not_found'): review(db,system['other'],cat,first['review_id'])

def test_zero_does_not_create_job_idempotency_and_cancel(system):
    jobs=system['jobs']; cat=system['catalog']; user=system['user']
    assert jobs.submit(user,cat.filter({'topics':['光学']}),'合成',True,0)['status']=='empty'
    assert system['db'].one('SELECT count(*) FROM jobs')[0]==0
    sel=cat.filter({}); first=jobs.submit(user,sel,'合成',True,0,'one')
    assert jobs.submit(user,sel,'合成',True,0,'one')['job_id']==first['job_id']
    with pytest.raises(ServiceError,match='idempotency_conflict'): jobs.submit(user,sel,'changed',True,0,'one')
    with pytest.raises(ServiceError,match='job_not_found'): jobs.get(system['other'],first['job_id'])
    assert jobs.cancel(user,first['job_id'])['status']=='cancelled'
    second=jobs.submit(user,sel,'合成',True,0,'one'); assert second['job_id']!=first['job_id']

def test_queue_limit_and_lease_recovery(system):
    jobs=system['jobs']; sel=system['catalog'].filter({}); user=system['user']; db=system['db']
    ids=[jobs.submit(user,sel,'合成',False,0,str(i))['job_id'] for i in range(3)]
    with pytest.raises(ServiceError,match='busy'): jobs.submit(user,sel,'合成',False,0,'full')
    id=ids[0]; db.execute('UPDATE jobs SET status="running",lease=? WHERE id=?',(time.time()-1,id)); recover(db,time.time())
    assert db.one('SELECT status,retries FROM jobs WHERE id=?',(id,))['status']=='queued'
    db.execute('UPDATE jobs SET status="running",lease=? WHERE id=?',(time.time()-1,id)); recover(db,time.time())
    assert db.one('SELECT status FROM jobs WHERE id=?',(id,))['status']=='failed'

def test_token_revoke_expire_disable_and_download_expiry(system):
    auth=system['auth']; user=system['user']; db=system['db']; token=system['token']
    assert auth.verify(token).user_id==user
    db.execute('UPDATE tokens SET revoked=1 WHERE user_id=?',(user,))
    with pytest.raises(ServiceError,match='unauthorized'): auth.verify(token)
    with pytest.raises(ServiceError,match='unauthorized'): auth.verify(auth.issue(user,time.time()-1))
    fresh=auth.issue(user); db.execute('UPDATE users SET status="disabled" WHERE id=?',(user,))
    with pytest.raises(ServiceError,match='user_disabled'): auth.verify(fresh)

def test_files_ownership_hash_and_fresh_links(system):
    db=system['db']; user=system['user']; job=system['jobs'].submit(user,system['catalog'].filter({}),'合成',False,0)['job_id']
    path=system['config'].jobs/job/'synthetic.pdf'; path.parent.mkdir(parents=True); path.write_bytes(b'%PDF-synthetic-artifact')
    file=new_id('file'); db.execute('INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)',(file,user,job,'worksheet',path.relative_to(system['config'].jobs).as_posix(),sha(path.read_bytes()),1,path.stat().st_size,'synthetic.pdf'))
    db.execute('UPDATE jobs SET status="succeeded" WHERE id=?',(job,))
    a=system['files'].links(user,job); b=system['files'].links(user,job); assert a[0]['sha256']==b[0]['sha256'] and a[0]['download_url']!=b[0]['download_url']
    token=a[0]['download_url'].rsplit('/',1)[1]; assert system['files'].download(token)[1]==path
    db.execute('UPDATE downloads SET expires=0')
    with pytest.raises(ServiceError,match='file_not_found'): system['files'].download(token)
    with pytest.raises(ServiceError,match='job_not_found'): system['files'].links(system['other'],job)
    path.write_bytes(b'changed')
    with pytest.raises(ServiceError,match='artifact_drift'): system['files'].links(user,job)
