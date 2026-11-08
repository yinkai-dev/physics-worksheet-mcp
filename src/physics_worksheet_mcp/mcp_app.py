import asyncio
import base64
from functools import wraps
import io
import json
import shutil
import time
from urllib.parse import quote
from PIL import Image
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult,TextContent,ImageContent,ToolAnnotations
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse,FileResponse
from starlette.routing import Route
from .auth.service import Auth,principal
from .bank.release import BankRegistry
from .catalog.service import Catalog
from .config import Config
from .errors import ServiceError
from .files.service import FileService
from .jobs.service import Jobs
from .review.service import review
from .search.service import search
from .storage import Database,new_id
from .util import fingerprint,canonical,sha
from . import __version__
from urllib.parse import urlparse

INSTRUCTIONS='''服务端从不可变题包读取题干、公式、题图、原卷答案；不要传入题文或自写答案。先 list_banks 再选题。
标签 any 命中任一，all 包含全部且允许其他，exact 标签集合相同；“只包含”用 exact。
search_questions 总是相关候选 coverage=partial；自由主题找全部必须 review_topic 逐页审核范围内每题，完成后使用 selection_id。
图题判断前用 get_question 看图。零匹配不出 PDF；不足按原条件出题并报告差额。标签是 AI 分类，不代表教师复核。
create_worksheet 可能返回 queued/running，用 get_job 轮询；断开等待不会取消任务，取消必须 cancel_job。
成功后下载 download_url 并核对 SHA256；get_files 重签链接不会重编译。真实客户端兼容和教学效果分别验收。'''

def build_app(config):
    db=Database(config.state); registry=BankRegistry(config.banks); auth=Auth(db); files=FileService(db,config); jobs=Jobs(db,config,files)
    mcp=MCPServer('Physics Worksheets',version=__version__,instructions=INSTRUCTIONS,website_url='https://github.com/yinkai-dev/physics-worksheet-mcp')
    def catalog(): return Catalog(registry,principal.get().grants)
    def tool(readonly=False):
        def decorate(fn):
            @wraps(fn)
            async def wrapped(*args,**kwargs):
                started=time.monotonic(); p=principal.get(); code='ok'
                try:
                    result=await fn(*args,**kwargs)
                    if isinstance(result,CallToolResult): return result
                    notice=result.get('notice') or '操作完成；完整信息见结构化结果。'
                    return CallToolResult(content=[TextContent(type='text',text=notice)],structuredContent=result)
                except ServiceError as exc:
                    code=exc.code; return CallToolResult(content=[TextContent(type='text',text=exc.message)],structuredContent=exc.result(),isError=True)
                except Exception:
                    code='internal_error'; incident=new_id('incident')
                    return CallToolResult(content=[TextContent(type='text',text='内部错误，请按事件编号联系管理员。')],structuredContent={'code':code,'incident_id':incident,'retryable':True},isError=True)
                finally:
                    db.execute('INSERT INTO audit(at,token_id,tool,args_hash,code,elapsed_ms) VALUES(?,?,?,?,?,?)',(time.time(),p.token_id,fn.__name__,fingerprint({k:v for k,v in kwargs.items() if k!='ctx'}),code,(time.monotonic()-started)*1000))
            mcp.tool(annotations=ToolAnnotations(readOnlyHint=readonly,destructiveHint=False,openWorldHint=False))(wrapped)
            return wrapped
        return decorate

    @tool(True)
    async def list_banks(bank_id:str|None=None,include_papers:bool=False)->CallToolResult:
        '''列出令牌已授权的题库、题数、筛选和答案覆盖；可展开原卷。'''
        cat=catalog(); result=[]
        for b in (cat.banks([bank_id] if bank_id else None) if bank_id or cat.releases else []):
            r=cat.releases[b]; row={'bank_id':b,'name':r.manifest['bank_name'],'release_id':r.manifest['release_id'],**r.manifest['components'],
                                   'question_count':len(r.store.questions),'paper_count':len(r.store.documents),
                                   'filters':{'question_types':sorted({t['question_type'] for t in r.tags.values()}),'topics':sorted({v for t in r.tags.values() for v in t['topics']}),'topic_match':['any','all','exact'],'reviewed_selections':list(r.reviews)}}
            if include_papers: row['papers']=[{'title':d['title'],'numbers':sorted([q['original_question_number'] for q in r.store.questions.values() if q['document_id']==did],key=int)} for did,d in r.store.documents.items()]
            result.append(row)
        return {'banks':result}

    @tool(True)
    async def find_questions(banks:list[str]|None=None,bank:str|None=None,paper:str|None=None,numbers:list[int]|None=None,filters:dict|None=None,cursor:int=0)->CallToolResult:
        '''确定筛选或 bank+paper+numbers 精确查题。返回冻结完整集合的 selection_id；每页最多50题。'''
        if type(cursor) is not int or cursor<0: raise ServiceError('invalid_filters')
        cat=catalog(); f={**(filters or {})}
        if banks is not None: f['banks']=banks
        if bank is not None: f['banks']=[bank]
        if paper is not None: f['paper']=paper
        if numbers is not None: f['numbers']=numbers
        if f.get('count'): raise ServiceError('invalid_filters')
        sel=cat.filter(f); id=db.save_selection(principal.get().user_id,sel); rows=sel['questions']
        return {'questions':rows[cursor:cursor+50],'total':len(rows),'complete':True,'selection_id':id,'next_cursor':cursor+50 if cursor+50<len(rows) else None,'coverage':'complete_deterministic_scope'}

    @tool(True)
    async def search_questions(query:str,banks:list[str]|None=None,filters:dict|None=None,top_k:int=10)->CallToolResult:
        '''全文检索相关候选，永远 coverage=partial，不能称全部。语义接口首版未启用。'''
        return search(catalog(),query,banks,filters,top_k,index_root=config.index)

    @tool(True)
    async def get_question(ref:str,image_offset:int=0)->CallToolResult:
        '''读取一题全文及最多2张缩放题图；依据图判断前必须看图。继续用 image_offset。'''
        if type(image_offset) is not int or image_offset<0: raise ServiceError('invalid_filters')
        r,qid,row=catalog().get(ref); q=r.store.questions[qid]; content=[]
        data={**row,'retrieval_text':q['retrieval_text'],'body':q['body'],'choices':q['choices'],'release_id':r.manifest['release_id'],'next_image_offset':image_offset+2 if image_offset+2<len(q['asset_ids']) else None}
        content.append(TextContent(type='text',text=q['retrieval_text']))
        for aid in q['asset_ids'][image_offset:image_offset+2]:
            a=r.store.assets[aid]; raw=__import__('pathlib').Path(a['resolved_path']).read_bytes()
            if sha(raw)!=aid: raise ServiceError('source_hash_drift')
            im=Image.open(io.BytesIO(raw)); im.thumbnail((1600,1600)); out=io.BytesIO(); im.save(out,format='PNG')
            content.append(ImageContent(type='image',data=base64.b64encode(out.getvalue()).decode(),mimeType='image/png'))
        return CallToolResult(content=content,structuredContent=data)

    @tool()
    async def review_topic(banks:list[str]|None=None,topic:str|None=None,filters:dict|None=None,review_id:str|None=None,decisions:list[dict]|None=None)->CallToolResult:
        '''自由主题找全部：逐页返回范围内每题，提交本页全部 ref/include/reason；完成后返回 selection_id。'''
        return review(db,principal.get().user_id,catalog(),review_id,banks,topic,filters,decisions)

    @tool()
    async def create_worksheet(title:str,filters:dict|None=None,items:list[dict]|None=None,selection_id:str|None=None,answers:bool=True,based_on_job:str|None=None,seed:str|int|None=None,request_id:str|None=None,wait_seconds:float=40,ctx:Context=None)->CallToolResult:
        '''三选一：filters / items[{ref}或{bank,paper,number}] / selection_id。题面和答案只能由服务端题包读取。零匹配不建任务，不足报告差额。等待上限45秒，之后 get_job 轮询。'''
        if not 0<=wait_seconds<=45: raise ServiceError('invalid_filters')
        if sum(x is not None for x in (filters,items,selection_id))!=1: raise ServiceError('invalid_filters')
        start=time.monotonic(); cat=catalog(); user=principal.get().user_id
        effective_seed=seed if seed is not None else fingerprint([user,request_id,filters,items,selection_id,title,answers])
        if filters is not None: sel=cat.filter(filters,effective_seed)
        elif items is not None:
            if not 1<=len(items)<=config.max_questions: raise ServiceError('too_many_questions')
            rows=[]; seen=set()
            for item in items:
                if set(item)=={'ref'}: _,_,row=cat.get(item['ref'])
                elif set(item)=={'bank','paper','number'}:
                    found=cat.filter({'banks':[item['bank']],'paper':item['paper'],'numbers':[item['number']]})['questions']
                    if len(found)!=1: raise ServiceError('number_not_found')
                    row=found[0]
                else: raise ServiceError('model_content_forbidden')
                r,qid,_=cat.get(row['ref']); q=r.store.questions[qid]; d=r.store.documents[q['document_id']]; key=(d['source_document']['sha256'],q['original_question_number'])
                if key in seen: raise ServiceError('duplicate_question')
                seen.add(key); rows.append(row)
            sel={'questions':rows,'bindings':cat.bindings(),'basis':'explicit_ref','requested_count':len(rows),'actual_count':len(rows),'shortfall':0,'candidate_count':len(rows),'duplicate_count':0,'complete_scope':True}
        else:
            row=db.one('SELECT data FROM selections WHERE id=? AND user_id=?',(selection_id,user))
            if not row: raise ServiceError('selection_not_found')
            sel=json.loads(row['data'])
            for row in sel['questions']:
                try: _,_,current=cat.get(row['ref'])
                except ServiceError: raise ServiceError('bank_not_available')
                if row['question_sha256']!=current['question_sha256']: raise ServiceError('question_drift')
        result=jobs.submit(user,sel,title,answers,effective_seed,request_id,based_on_job,(time.monotonic()-start)*1000)
        return await jobs.wait(user,result['job_id'],wait_seconds,ctx) if result.get('job_id') else result

    @tool(True)
    async def get_job(job_id:str|None=None,wait_seconds:float=0)->CallToolResult:
        '''查本人任务或列最近任务；长卷用 wait_seconds<=45 轮询。'''
        return await jobs.wait(principal.get().user_id,job_id,wait_seconds) if job_id else jobs.get(principal.get().user_id)

    @tool()
    async def cancel_job(job_id:str)->CallToolResult:
        '''取消本人排队或运行任务，保留诊断现场。'''
        return jobs.cancel(principal.get().user_id,job_id)

    @tool(True)
    async def get_files(job_id:str,kinds:list[str]|None=None)->CallToolResult:
        '''校验 SHA 后重签本人已有 PDF 下载链接，不重新编译。'''
        return {'job_id':job_id,'files':files.links(principal.get().user_id,job_id,kinds)}

    async def health(request): return JSONResponse({'status':'ok','version':__version__})
    async def ready(request):
        reasons=[]
        try:
            for b in registry.active(): registry.load(b)
            if not registry.active(): reasons.append('no_active_banks')
            with db.connect() as c: c.execute('BEGIN IMMEDIATE'); c.rollback()
            heart=db.one('SELECT at FROM heartbeat WHERE name="worker"')
            if not heart or time.time()-heart['at']>60: reasons.append('worker_heartbeat')
            if not shutil.which('xelatex'): reasons.append('xelatex_unavailable')
        except Exception: reasons.append('storage_or_release')
        return JSONResponse({'ready':not reasons,'checks_failed':reasons},status_code=503 if reasons else 200)
    async def download(request):
        try:
            if 'token' in request.path_params: row,path=files.download(request.path_params['token'])
            else:
                row=db.one('SELECT * FROM files WHERE id=? AND user_id=?',(request.path_params['id'],principal.get().user_id))
                if not row: raise ServiceError('file_not_found')
                path=files.checked(row)
            return FileResponse(path,media_type='application/pdf',headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(row['name']),
                 'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-Content-SHA256':row['sha256']})
        except ServiceError: return JSONResponse({'code':'file_not_found'},status_code=404)
    host=urlparse(config.base_url).netloc
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
        allowed_hosts=list(dict.fromkeys([host,'127.0.0.1:*','localhost:*','[::1]:*'])),allowed_origins=list(config.origins))
    app=mcp.streamable_http_app(stateless_http=True,host=config.host,transport_security=transport_security)
    app.router.routes[:0]=[Route('/healthz',health),Route('/readyz',ready),Route('/d/{token}',download),Route('/files/{id}',download)]
    return Security(app,auth,db,config)

class Security:
    def __init__(self,app,auth,db,config): self.app,self.auth,self.db,self.config=app,auth,db,config
    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        headers={k.decode().lower():v.decode() for k,v in scope['headers']}; origin=headers.get('origin')
        if origin and origin not in self.config.origins: return await JSONResponse({'code':'origin_forbidden'},403)(scope,receive,send)
        path=scope['path']; token_ctx=None
        if path.rstrip('/')=='/mcp' or path.startswith('/files/'):
            try:
                value=headers.get('authorization','')
                if not value.startswith('Bearer '): raise ServiceError('unauthorized')
                p=self.auth.verify(value[7:]); token_ctx=principal.set(p)
            except ServiceError as exc:
                status=403 if exc.code=='user_disabled' else 401
                return await JSONResponse({'code':exc.code},status,headers={'WWW-Authenticate':'Bearer realm="pwmcp"'})(scope,receive,send)
            if path.rstrip('/')=='/mcp' and scope['method']=='POST':
                now=time.time()
                with self.db.transaction() as c:
                    c.execute('DELETE FROM rate WHERE at<?',(now-60,)); n=c.execute('SELECT COUNT(*) FROM rate WHERE token_id=?',(p.token_id,)).fetchone()[0]
                    if n<self.config.rate_limit: c.execute('INSERT INTO rate VALUES(?,?)',(p.token_id,now))
                if n>=self.config.rate_limit:
                    principal.reset(token_ctx); return await JSONResponse({'code':'rate_limited'},429,headers={'Retry-After':'60'})(scope,receive,send)
        try: await self.app(scope,receive,send)
        finally:
            if token_ctx is not None: principal.reset(token_ctx)
