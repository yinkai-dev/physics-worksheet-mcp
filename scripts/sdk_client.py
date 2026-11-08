"""SDK smoke client: discover, list banks, create, poll, download, verify SHA.

Credentials and results belong in an ignored private directory. No tokens printed.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time
import httpx
import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

async def run(a):
    token=Path(a.token_file).read_text(encoding='utf-8-sig').strip(); out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
    request=json.loads(Path(a.request).read_text(encoding='utf-8-sig')) if a.request else None
    async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=120) as http:
        async with Client(streamable_http_client(a.url,http_client=http),mode=a.mode,read_timeout_seconds=120) as client:
            listed=await client.list_tools(); tools=listed.tools if hasattr(listed,'tools') else listed
            names=[t.name for t in tools]
            result=await client.call_tool('list_banks',{}); banks=result.structuredContent
            if result.isError: raise RuntimeError(banks)
            (out/'banks.json').write_text(json.dumps(banks,ensure_ascii=False,indent=2),encoding='utf-8')
            if not request: request={'title':'MCP 出卷验收','filters':{'banks':[banks['banks'][0]['bank_id']],'count':1},'wait_seconds':0}
            call=await client.call_tool('create_worksheet',request); job=call.structuredContent
            if call.isError: raise RuntimeError(job)
            deadline=time.monotonic()+900
            while job['status'] in ('queued','running'):
                if time.monotonic()>deadline: raise TimeoutError('job deadline')
                await asyncio.sleep(1); call=await client.call_tool('get_job',{'job_id':job['job_id'],'wait_seconds':40}); job=call.structuredContent
            if job['status'] not in ('succeeded','empty'): raise RuntimeError(job)
            for f in job.get('files',[]):
                async with httpx.AsyncClient(trust_env=False,timeout=120) as download:
                    response=await download.get(f['download_url']); response.raise_for_status(); data=response.content
                if hashlib.sha256(data).hexdigest()!=f['sha256']: raise ValueError('artifact hash mismatch')
                target=out/f['name']; target.write_bytes(data)
            (out/'result.json').write_text(json.dumps({'client':'official Python SDK','mode':a.mode,'tools':names,'request':request,'job':job},ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'mode':a.mode,'tools':len(names),'status':job['status'],'files':[{'kind':f['kind'],'pages':f['pages'],'sha256':f['sha256']} for f in job.get('files',[])]},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--url',default='http://127.0.0.1:8770/mcp'); p.add_argument('--token-file',required=True); p.add_argument('--request'); p.add_argument('--out',required=True); p.add_argument('--mode',choices=['auto','legacy'],default='auto'); asyncio.run(run(p.parse_args()))
