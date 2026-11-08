from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from make_demo import make_release
from physics_worksheet_mcp.config import Config
from physics_worksheet_mcp.storage import Database
from physics_worksheet_mcp.bank.release import BankRegistry
from physics_worksheet_mcp.auth.service import Auth
from physics_worksheet_mcp.files.service import FileService
from physics_worksheet_mcp.jobs.service import Jobs
from physics_worksheet_mcp.catalog.service import Catalog

@pytest.fixture
def system(tmp_path):
    config=Config(banks=tmp_path/'banks',state=tmp_path/'state.sqlite3',jobs=tmp_path/'jobs',rate_limit=1000)
    release=make_release(config.banks); registry=BankRegistry(config.banks); registry.activate('demo-bank',release.root.name)
    db=Database(config.state); auth=Auth(db); user=auth.add_user('Synthetic user'); other=auth.add_user('Other synthetic user')
    db.execute('INSERT INTO grants VALUES(?,?)',(user,'demo-bank'))
    token=auth.issue(user); other_token=auth.issue(other); files=FileService(db,config)
    return {'config':config,'registry':registry,'db':db,'auth':auth,'user':user,'other':other,'token':token,'other_token':other_token,'files':files,'jobs':Jobs(db,config,files),'catalog':Catalog(registry,['demo-bank']),'tmp':tmp_path}
