from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import asyncio
import base64
import hashlib
import json
import logging
import re
import time
import uuid
from fastapi import FastAPI, Request, Depends, Response, BackgroundTasks
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from shared.auth import Auth
from shared.errors import Problem
from shared.runtime import engine as default_engine
from service.s3 import handle_s3

class CreateLease(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scope_profile: str = Field(min_length=1,max_length=80)
    ttl_seconds: int = 900
    client_request_id: str | None = Field(default=None,max_length=120)
    delivery_mode: Literal['confirmed','immediate'] = 'confirmed'

class Probe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lease_id: uuid.UUID
    access_key_id: str = Field(max_length=128)
    secret_access_key: str = Field(max_length=256)
    operation: str = 'GET'
    bucket: str = Field(default='sales-demo',max_length=80)
    object_name: str = Field(default='quarterly/revenue.csv',max_length=256)

class LiveTest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    access_key_id: str = Field(min_length=1,max_length=128)
    secret_access_key: str = Field(min_length=1,max_length=256,repr=False)
    operation: Literal['GET','LIST','PUT','DELETE','FOREIGN'] = 'GET'


def create_app(lcm=None, worker=True):
    e = lcm or default_engine()
    if e.cfg.auth_mode=='gateway':
        from shared.gateway_auth import GatewayAuth
        auth=GatewayAuth(e.cfg)
    else:
        auth=Auth(e.cfg)

    async def cleanup_loop():
        while True:
            try:
                await asyncio.to_thread(e.reconcile)
            except Exception:
                logging.getLogger('lcm').error('cleanup_iteration_failed')
            await asyncio.sleep(e.cfg.worker_interval)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        if worker:
            await asyncio.to_thread(e.reconcile)
            task=asyncio.create_task(cleanup_loop())
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app=FastAPI(title='OCI S3 Credential Lifecycle Manager', version='0.2.0' if e.cfg.mode=='oci' else '0.1.0', lifespan=lifespan, docs_url=None, redoc_url=None,
                description='Managed key deletion is not native STS expiry.')
    app.state.engine=e
    app.state.auth=auth
    connector=None
    if e.cfg.mode=='oci' and e.cfg.oauth and e.cfg.auth_mode!='gateway':
        from service.connector_auth import ConnectorAuth
        connector=ConnectorAuth(e,auth)
        connector.install(app)
    app.state.connector=connector

    @app.get('/docs', include_in_schema=False)
    def api_reference():
        assets='https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.35.0/'
        page=get_swagger_ui_html(openapi_url='/openapi.json', title=app.title+' — API reference',
            swagger_js_url=assets+'swagger-ui-bundle.js', swagger_css_url=assets+'swagger-ui.css',
            swagger_favicon_url='data:,',
            swagger_ui_parameters={'persistAuthorization':False,'validatorUrl':None})
        # Authorize only this generated initializer, without allowing arbitrary inline JS.
        initializer=re.search(r'<script>(.*?)</script>',page.body.decode(),re.S).group(1)
        digest=base64.b64encode(hashlib.sha256(initializer.encode()).digest()).decode()
        page.headers['Content-Security-Policy']=(
            "default-src 'self'; script-src 'self' "+assets+" 'sha256-"+digest+"'; "
            "style-src 'self' "+assets+"; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'")
        return page

    @app.get('/redoc', include_in_schema=False)
    def alternate_reference():
        return RedirectResponse('/docs')

    @app.middleware('http')
    async def safety_headers(request,call_next):
        request.state.correlation=str(uuid.uuid4())
        if e.cfg.mode=='oci' and (request.url.path.startswith('/demo/') or request.url.path.startswith('/mock-s3/') or request.url.path=='/.well-known/jwks.json'):
            return JSONResponse({'error':{'code':'NOT_FOUND','correlation_id':request.state.correlation}},status_code=404,headers={'Cache-Control':'no-store'})
        # Bound payloads, including streamed bodies. Do not log tokens, request/response bodies or URLs.
        body=await request.body()
        if len(body)>1048576:
            return JSONResponse({'error':{'code':'PAYLOAD_TOO_LARGE','correlation_id':request.state.correlation}},status_code=413)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Correlation-ID']=request.state.correlation
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        if 'Content-Security-Policy' not in response.headers:
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        return response

    @app.exception_handler(Problem)
    async def problem(request,exc):
        error={'code':exc.code,'correlation_id':request.state.correlation}
        if exc.lease_id:
            error['lease_id']=exc.lease_id
        return JSONResponse({'error':error},status_code=exc.status,
                            headers={'Retry-After':'30'} if exc.status==429 else {})

    @app.exception_handler(RequestValidationError)
    async def bad_input(request,exc):
        return JSONResponse({'error':{'code':'INVALID_INPUT','correlation_id':request.state.correlation}},status_code=400)

    @app.exception_handler(Exception)
    async def unexpected(request,exc):
        return JSONResponse({'error':{'code':'DEPENDENCY_UNAVAILABLE','correlation_id':request.state.correlation}},status_code=503)

    bearer=HTTPBearer(auto_error=False)

    def owner(request: Request, credentials=Depends(bearer)):
        if e.cfg.auth_mode=='gateway':
            who=auth.verify_request(request)
        elif request.headers.get('Authorization'):
            who=auth.verify(request.headers.get('Authorization'))
        elif connector:
            who=connector.owner(request)
        else:
            who=auth.verify(None)
        if e.cfg.profile_scopes and not auth.allowed(who):
            raise Problem(403,'SCOPE_FORBIDDEN')
        return who

    @app.get('/health/live')
    def live():
        return {'status':'LIVE'}

    @app.get('/health/ready')
    def ready():
        healthy=e.healthy()
        if e.cfg.mode=='oci':healthy=healthy and all(e.cfg.integration_gates.get(k) for k in ('iam_scope','vault_replay','database','scheduler'))
        return JSONResponse({'status':'READY' if healthy else 'PENDING','mode':e.cfg.mode,'production_ready':False}, status_code=200 if healthy else 503)

    from service.bucket_access import BucketOperation, execute as execute_bucket_operation

    @app.post('/v1/bucket-operations', include_in_schema=e.cfg.mode=='oci', tags=['Automated bucket access'])
    def bucket_operation(body:BucketOperation,request:Request,who=Depends(owner)):
        if e.cfg.mode!='oci':raise Problem(404,'NOT_FOUND')
        return execute_bucket_operation(e,who,body,auth.allowed(who),request.state.correlation)

    @app.get('/v1/config')
    def config(who=Depends(owner)):
        return {'mode':e.cfg.mode,'region':e.cfg.region,'ttl':{'default':900,'min':e.cfg.ttl_min,'max':e.cfg.ttl_max},
                'profiles':{k:v for k,v in e.cfg.profiles.items() if k in auth.allowed(who)},
                'expiry_mode':'managed_key_deletion','replay_seconds':e.cfg.replay_seconds,
                'identity':{k:who[k] for k in ('issuer','subject','expires_at') if k in who}}

    @app.post('/v1/leases')
    def create(body:CreateLease,request:Request,background_tasks:BackgroundTasks,who=Depends(owner)):
        if e.cfg.mode=='oci' and not all(e.cfg.integration_gates.get(k) for k in ('iam_scope','vault_replay','database','scheduler')):
            raise Problem(503,'LIVE_CAPABILITY_GATES_PENDING')
        idem=request.headers.get('Idempotency-Key','')
        try:
            uuid.UUID(idem)
        except ValueError:
            raise Problem(400,'INVALID_IDEMPOTENCY_KEY') from None
        if body.scope_profile not in e.cfg.profiles:
            raise Problem(422,'UNSUPPORTED_SCOPE')
        if body.scope_profile not in auth.allowed(who):
            raise Problem(403,'PROFILE_FORBIDDEN')
        # Use an operator-configured public URL, never arbitrary Host/X-Forwarded-Host.
        endpoint=('https://'+e.cfg.namespace+'.compat.objectstorage.'+e.cfg.region+'.oci.customer-oci.com') if e.cfg.mode=='oci' else (e.cfg.public_url or str(request.base_url).rstrip('/'))+'/mock-s3'
        data=body.model_dump()
        immediate=data['delivery_mode']=='immediate'
        if not immediate:data.pop('delivery_mode')  # Preserve existing idempotency hashes.
        status,payload=e.create(who,data,idem,request.state.correlation,endpoint,defer_setup=immediate)
        if status==201 and immediate:
            background_tasks.add_task(e.finish_in_background,dict(payload),request.state.correlation)
        return JSONResponse(payload,status_code=status,background=background_tasks)

    def scoped_lease(lease_id,who):
        row=e.owned(str(lease_id),who)
        if row['profile'] not in auth.allowed(who):raise Problem(403,'PROFILE_FORBIDDEN')
        return row

    @app.get('/v1/lease-requests/{request_id}')
    def lease_request_progress(request_id:str,who=Depends(owner)):
        try:uuid.UUID(request_id)
        except ValueError:raise Problem(400,'INVALID_REQUEST_ID') from None
        with e.db.connect() as c:
            row=c.execute('SELECT * FROM leases WHERE issuer=? AND subject=? AND idem=?',
                (who['issuer'],who['subject'],str(request_id))).fetchone()
        if not row:raise Problem(404,'LEASE_REQUEST_NOT_FOUND')
        if row['profile'] not in auth.allowed(who):raise Problem(403,'PROFILE_FORBIDDEN')
        state=row['state']
        phase=('PREPARING_REPLAY' if row['schedule_id'] else 'CONFIRMING_SCHEDULE') if state=='SCHEDULE_PENDING' else state
        return {'request_id':str(request_id),'phase':phase,'lease':e.view(row)}

    @app.get('/v1/leases/{lease_id}')
    def get(lease_id:uuid.UUID,who=Depends(owner)):
        return e.view(scoped_lease(lease_id,who))

    @app.delete('/v1/leases/{lease_id}')
    def delete(lease_id:uuid.UUID,who=Depends(owner)):
        scoped_lease(lease_id,who)
        status=e.revoke(str(lease_id),who)
        return Response(status_code=status)

    @app.get('/v1/leases')
    def leases(who=Depends(owner)):
        with e.db.connect() as c:
            rows=c.execute('SELECT * FROM leases WHERE issuer=? AND subject=? ORDER BY created_at DESC LIMIT 100',(who['issuer'],who['subject'])).fetchall()
        return {'leases':[e.view(r) for r in rows if r['profile'] in auth.allowed(who)]}

    @app.get('/v1/test-setup', include_in_schema=e.cfg.mode=='oci')
    def test_setup(who=Depends(owner)):
        if e.cfg.mode!='oci':raise Problem(404,'NOT_FOUND')
        allowed=auth.allowed(who)
        users=[]
        for profile,ids in e.cfg.service_users.items():
            if profile not in allowed:continue
            for uid in ids:
                user=e.cloud.identity.get_user(uid).data
                users.append({'profile':profile,'user_id':uid,'name':user.name,
                              'customer_secret_key_count':len(e.cloud.list_keys(uid))})
        return {'mode':'oci','region':e.cfg.region,'compartment_id':e.cfg.compartment_id,
                'service_users':users,'profiles':{p:e.cfg.profiles[p] for p in allowed if p in e.cfg.profiles},
                'read_object':'quarterly/revenue.csv','delete_test_object':'diagnostics/delete-denied.txt',
                'foreign_bucket':e.cfg.deployment_id+'-foreign','foreign_object':'negative-scope.txt'}

    @app.get('/v1/metrics')
    def live_metrics(who=Depends(owner)):
        allowed=auth.allowed(who)
        result=e.metrics()
        result['pools']=[p for p in result['pools'] if p['profile'] in allowed]
        with e.db.connect() as c:
            result['states']={r[0]:r[1] for r in c.execute('SELECT state,COUNT(*) FROM leases WHERE issuer=? AND subject=? GROUP BY state',(who['issuer'],who['subject']))}
        return result

    @app.get('/v1/audit')
    def live_audit(who=Depends(owner)):
        with e.db.connect() as c:
            rows=c.execute('SELECT a.seq,a.at,a.lease_id,a.action,a.detail FROM audit a JOIN leases l ON a.lease_id=l.id WHERE l.issuer=? AND l.subject=? ORDER BY a.seq DESC LIMIT 60',(who['issuer'],who['subject'])).fetchall()
        return {'events':[dict(r) for r in rows]}

    @app.post('/v1/leases/{lease_id}/tests', include_in_schema=e.cfg.mode=='oci')
    def live_test(lease_id:uuid.UUID,body:LiveTest,who=Depends(owner)):
        if e.cfg.mode!='oci':raise Problem(404,'NOT_FOUND')
        scoped_lease(lease_id,who)
        from service.live_tests import run_test
        return run_test(e,who,str(lease_id),body)

    @app.get('/demo/info', include_in_schema=e.cfg.mode=='mock')
    def info():
        return {'mode':'mock','version':'0.1.0','production_ready':False,
                'warning':'Synthetic credentials and S3 only. No OCI IAM users or Customer Secret Keys are created.',
                'unverified':['Unity query engine','OCI IAM scope isolation','Autonomous Database and Vault','Events and Function scheduling','OCI S3 revocation targets']}

    @app.post('/demo/session', include_in_schema=e.cfg.mode=='mock')
    def session(request:Request):
        e.rate({'issuer':'mock-session','subject':request.client.host if request.client else 'unknown'})
        return {'token':auth.mint_demo(),'expires_in':3600,'mode':'mock'}

    @app.get('/demo/metrics', include_in_schema=e.cfg.mode=='mock')
    def metrics(who=Depends(owner)):
        return e.metrics()

    @app.get('/demo/audit', include_in_schema=e.cfg.mode=='mock')
    def audit(who=Depends(owner)):
        with e.db.connect() as c:
            rows=c.execute('SELECT a.seq,a.at,a.lease_id,a.action,a.detail FROM audit a JOIN leases l ON a.lease_id=l.id WHERE l.issuer=? AND l.subject=? ORDER BY a.seq DESC LIMIT 60',(who['issuer'],who['subject'])).fetchall()
        return {'events':[dict(r) for r in rows]}

    @app.post('/demo/probe', include_in_schema=e.cfg.mode=='mock')
    async def probe(body:Probe,request:Request,who=Depends(owner)):
        import httpx
        from botocore.auth import S3SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
        from urllib.parse import quote
        lease=e.owned(str(body.lease_id),who)
        if lease['key_id']!=body.access_key_id:
            raise Problem(403,'NOT_OWNER')
        if body.operation not in ('GET','PUT','DELETE','LIST'):
            raise Problem(400,'INVALID_OPERATION')
        method='GET' if body.operation=='LIST' else body.operation
        path='/mock-s3/'+quote(body.bucket,safe='')+('/'+quote(body.object_name,safe='/') if body.operation!='LIST' else '?list-type=2')
        content=b'mock demo write\n' if method=='PUT' else b''
        aws=AWSRequest(method=method,url='http://mock-internal'+path,data=content)
        S3SigV4Auth(Credentials(body.access_key_id,body.secret_access_key),'s3',e.cfg.region).add_auth(aws)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://mock-internal') as client:
            r=await client.request(method,path,content=content,headers=dict(aws.headers))
        return {'status_code':r.status_code,'allowed':r.is_success,'body':r.text[:4096],'mode':'mock'}

    @app.api_route('/mock-s3/{bucket}',methods=['GET','PUT','DELETE'],include_in_schema=False)
    @app.api_route('/mock-s3/{bucket}/{name:path}',methods=['GET','PUT','DELETE'],include_in_schema=False)
    async def s3(request:Request,bucket:str,name:str=''):
        return await handle_s3(request,e,bucket,name)

    @app.get('/.well-known/jwks.json', include_in_schema=e.cfg.mode=='mock')
    def jwks():
        return auth.jwks

    static=Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=static),name='static')

    @app.get('/unity',include_in_schema=False)
    def unity_reference():
        return FileResponse(static/'unity-api.html')

    @app.get('/',include_in_schema=False)
    def index():
        return FileResponse(static/('live.html' if e.cfg.mode=='oci' else 'index.html'))

    from service.api_reference import install_reference
    install_reference(app, e.cfg)
    return app
