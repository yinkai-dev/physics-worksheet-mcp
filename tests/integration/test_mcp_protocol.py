import asyncio
from contextlib import contextmanager
import json
import socket
import threading
import time
import httpx
import httpx2
import pytest
import uvicorn
from starlette.testclient import TestClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from physics_worksheet_mcp.mcp_app import build_app

@contextmanager
def live_server(system):
    sock=socket.socket(); sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    config=system['config']; config.port=port; config.base_url=f'http://127.0.0.1:{port}'; config.origins=(config.base_url,)
    server=uvicorn.Server(uvicorn.Config(build_app(config),host=config.host,port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True); thread.start()
    end=time.monotonic()+10
    while not server.started:
        if time.monotonic()>end: raise TimeoutError('server startup')
        time.sleep(.01)
    try: yield config.base_url
    finally: server.should_exit=True; thread.join(timeout=10); sock.close()

@pytest.mark.parametrize('mode',['auto','legacy'])
async def test_all_nine_tools_over_real_http(system,mode):
    with live_server(system) as base:
        async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+system['token']},trust_env=False,timeout=60) as http:
            async with Client(streamable_http_client(base+'/mcp',http_client=http),mode=mode) as c:
                listed=await c.list_tools(); names=[t.name for t in listed.tools]
                assert names==['list_banks','find_questions','search_questions','get_question','review_topic','create_worksheet','get_job','cancel_job','get_files']
                assert [t.name for t in (await c.list_tools()).tools]==names
                async def call(name,args):
                    result=await c.call_tool(name,args)
                    assert not result.is_error, result.structured_content
                    return result.structured_content
                banks=await call('list_banks',{'include_papers':True}); assert banks['banks'][0]['question_count']==1
                found=await call('find_questions',{'banks':['demo-bank']}); ref=found['questions'][0]['ref']
                question=await c.call_tool('get_question',{'ref':ref}); assert question.content[1].type=='image'
                result=await call('search_questions',{'query':'回旋加速器'}); assert result['coverage']=='partial'
                reviewed=await call('review_topic',{'banks':['demo-bank'],'topic':'合成主题'})
                completed=await call('review_topic',{'review_id':reviewed['review_id'],'decisions':[{'ref':ref,'include':True,'reason':'synthetic full review'}]}); assert completed['complete']
                empty=await call('create_worksheet',{'title':'合成零匹配','filters':{'topics':['光学']},'wait_seconds':0}); assert empty['status']=='empty'
                job=await call('create_worksheet',{'title':'合成练习','selection_id':completed['selection_id'],'wait_seconds':.1}); assert job['status']=='queued'
                assert (await call('get_job',{'job_id':job['job_id'],'wait_seconds':.1}))['status']=='queued'
                assert (await call('create_worksheet',{'title':'合成练习','selection_id':completed['selection_id'],'wait_seconds':0}))['job_id']==job['job_id']
                assert (await call('get_files',{'job_id':job['job_id']}))['files']==[]
                assert (await call('cancel_job',{'job_id':job['job_id']}))['status']=='cancelled'
                forbidden=await c.call_tool('create_worksheet',{'title':'合成','items':[{'ref':ref,'answer':'invented'}],'wait_seconds':0}); assert forbidden.is_error and forbidden.structured_content['code']=='model_content_forbidden'
                bad=await c.call_tool('find_questions',{'banks':['hidden-bank']}); assert bad.is_error and bad.structured_content['code']=='bank_not_available'
        async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+system['other_token']},trust_env=False) as http:
            async with Client(streamable_http_client(base+'/mcp',http_client=http),mode=mode) as c:
                for tool in ('get_job','get_files','cancel_job'):
                    result=await c.call_tool(tool,{'job_id':job['job_id']}); assert result.is_error and result.structured_content['code']=='job_not_found'
                result=await c.call_tool('find_questions',{'banks':['demo-bank']}); assert result.is_error and result.structured_content['code']=='bank_not_available'

@pytest.mark.parametrize('token_kind,status',[('none',401),('wrong',401),('revoked',401),('disabled',403)])
def test_http_auth(system,token_kind,status):
    db=system['db']; token=system['token']
    if token_kind=='revoked': db.execute('UPDATE tokens SET revoked=1')
    if token_kind=='disabled': db.execute('UPDATE users SET status="disabled"')
    headers={} if token_kind=='none' else {'Authorization':'Bearer '+('wrong' if token_kind=='wrong' else token)}
    with TestClient(build_app(system['config'])) as client:
        response=client.post('/mcp',json={},headers=headers); assert response.status_code==status

def test_origin_health_and_unauthorized_file(system):
    with TestClient(build_app(system['config'])) as client:
        assert client.get('/healthz').status_code==200
        assert client.get('/readyz').status_code==503
        assert client.post('/mcp',headers={'Authorization':'Bearer '+system['token'],'Origin':'https://untrusted.example'},json={}).status_code==403
        assert client.get('/files/nonexistent').status_code==401
        assert client.get('/files/nonexistent',headers={'Authorization':'Bearer '+system['other_token']}).status_code==404
        assert client.get('/d/nonexistent').status_code==404
