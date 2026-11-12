from contextlib import asynccontextmanager
from pathlib import Path
import asyncio
import json
import logging
import time
import uuid
from fastapi import FastAPI, Request, Depends, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
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

class Probe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lease_id: uuid.UUID
    access_key_id: str = Field(max_length=128)
    secret_access_key: str = Field(max_length=256)
    operation: str = 'GET'
    bucket: str = Field(default='sales-demo',max_length=80)
    object_name: str = Field(default='quarterly/revenue.csv',max_length=256)


def create_app(lcm=None, worker=True):
    e = lcm or default_engine()
    auth = Auth(e.cfg)

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

    app=FastAPI(title='OCI S3 Credential Lifecycle Manager', version='0.1.0', lifespan=lifespan,
                description='Explicit mock demo. Managed key deletion is not native STS expiry.')
    app.state.engine=e
    app.state.auth=auth

    @app.middleware('http')
    async def safety_headers(request,call_next):
        request.state.correlation=str(uuid.uuid4())
        # Bound payloads, including streamed bodies. Do not log tokens, request/response bodies or URLs.
        body=await request.body()
        if len(body)>1048576:
            return JSONResponse({'error':{'code':'PAYLOAD_TOO_LARGE','correlation_id':request.state.correlation}},status_code=413)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Correlation-ID']=request.state.correlation
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
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
        return auth.verify(request.headers.get('Authorization'))

    @app.get('/health/live')
    def live():
        return {'status':'LIVE'}

    @app.get('/health/ready')
    def ready():
        healthy=e.healthy()
        return JSONResponse({'status':'READY' if healthy else 'PENDING','mode':e.cfg.mode,'production_ready':False}, status_code=200 if healthy else 503)

    @app.get('/v1/config')
    def config(who=Depends(owner)):
        return {'mode':e.cfg.mode,'region':e.cfg.region,'ttl':{'default':900,'min':e.cfg.ttl_min,'max':e.cfg.ttl_max},
                'profiles':{k:v for k,v in e.cfg.profiles.items() if k in auth.allowed(who)},
                'expiry_mode':'managed_key_deletion','replay_seconds':e.cfg.replay_seconds}

    @app.post('/v1/leases')
    def create(body:CreateLease,request:Request,who=Depends(owner)):
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
        endpoint=(e.cfg.public_url or str(request.base_url).rstrip('/'))+'/mock-s3'
        status,payload=e.create(who,body.model_dump(),idem,request.state.correlation,endpoint)
        return JSONResponse(payload,status_code=status)

    @app.get('/v1/leases/{lease_id}')
    def get(lease_id:uuid.UUID,who=Depends(owner)):
        return e.view(e.owned(str(lease_id),who))

    @app.delete('/v1/leases/{lease_id}')
    def delete(lease_id:uuid.UUID,who=Depends(owner)):
        status=e.revoke(str(lease_id),who)
        return Response(status_code=status)

    @app.get('/v1/leases')
    def leases(who=Depends(owner)):
        with e.db.connect() as c:
            rows=c.execute('SELECT * FROM leases WHERE issuer=? AND subject=? ORDER BY created_at DESC LIMIT 100',(who['issuer'],who['subject'])).fetchall()
        return {'leases':[e.view(r) for r in rows]}

    @app.get('/demo/info')
    def info():
        return {'mode':'mock','version':'0.1.0','production_ready':False,
                'warning':'Synthetic credentials and S3 only. No OCI IAM users or Customer Secret Keys are created.',
                'unverified':['Unity query engine','OCI IAM scope isolation','Autonomous Database and Vault','Events and Function scheduling','OCI S3 revocation targets']}

    @app.post('/demo/session')
    def session(request:Request):
        e.rate({'issuer':'mock-session','subject':request.client.host if request.client else 'unknown'})
        return {'token':auth.mint_demo(),'expires_in':3600,'mode':'mock'}

    @app.get('/demo/metrics')
    def metrics(who=Depends(owner)):
        return e.metrics()

    @app.get('/demo/audit')
    def audit(who=Depends(owner)):
        with e.db.connect() as c:
            rows=c.execute('SELECT a.seq,a.at,a.lease_id,a.action,a.detail FROM audit a JOIN leases l ON a.lease_id=l.id WHERE l.issuer=? AND l.subject=? ORDER BY a.seq DESC LIMIT 60',(who['issuer'],who['subject'])).fetchall()
        return {'events':[dict(r) for r in rows]}

    @app.post('/demo/probe')
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

    @app.get('/.well-known/jwks.json')
    def jwks():
        return auth.jwks

    static=Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=static),name='static')

    @app.get('/',include_in_schema=False)
    def index():
        return FileResponse(static/'index.html')

    return app
