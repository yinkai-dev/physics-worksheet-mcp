"""Build a synthetic engineering bank and private token for a local smoke run."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests'))
from test_question_package import QuestionPackageTests
from physics_worksheet_mcp.config import Config
from physics_worksheet_mcp.storage import Database
from physics_worksheet_mcp.auth.service import Auth
from physics_worksheet_mcp.bank.release import BankRegistry,verify_release
from physics_worksheet_mcp.util import fingerprint,write_json,make_sums

def make_release(root,bank='demo-bank',rel='rel-synthetic-v1',with_image=True):
    out=Path(root)/bank/'releases'/rel
    if out.exists(): return verify_release(out)
    q,_=QuestionPackageTests().make_package(out/'question-package',kb_name='合成测试题库',image_requirement='required' if with_image else 'none')
    provider={'provider_id':'synthetic-provider','revision_id':'synthetic-revision','provider_fingerprint':'a'*64,'adapter_kind':'question-package-v1'}
    tags={'schema_version':1,'provider':provider,'reviewed_by':'synthetic-test','policy':'engineering only','questions':[{'question_id':q['question_id'],'question_sha256':fingerprint(q),'question_type':'选择题','topics':['力学'],'reason':'synthetic'}]}
    write_json(out/'tags.json',tags)
    write_json(out/'release.json',{'schema':'pwmcp.bank-release/1','bank_id':bank,'bank_name':'合成测试题库','release_id':rel,'legacy':{'provider':provider},'components':{'question_package':{'question_count':1,'document_count':1,'required_asset_count':1 if with_image else 0},'answers':None}})
    make_sums(out); return verify_release(out)

if __name__=='__main__':
    config=Config(); r=make_release(config.banks); BankRegistry(config.banks).activate('demo-bank',r.root.name)
    db=Database(config.state); auth=Auth(db); user=auth.add_user('Demo user'); db.execute('INSERT INTO grants VALUES(?,?)',(user,'demo-bank'))
    token=Path('private/tokens/demo.txt'); token.parent.mkdir(parents=True,exist_ok=True)
    if token.exists(): raise FileExistsError('Demo token file exists; use admin token issue for a fresh token.')
    token.write_text(auth.issue(user)+'\n',encoding='utf-8'); print('Synthetic bank activated; credential written to ignored private token file.')
