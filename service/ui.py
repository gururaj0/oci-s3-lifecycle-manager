"""Browser session handler. All lifecycle operations traverse the JWT gateway."""
import re
from fastapi import Request,Response
import httpx
from service.app import create_app
from service.connector_auth import ConnectorAuth
from shared.auth import Auth
from shared.errors import Problem
from shared.runtime import engine

# Exact supported operations; the client cannot choose a host, arbitrary path,
# query string or forwarded identity headers.
LEASE=r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'
ROUTES={
 'GET':re.compile(r'v1/(?:config|leases|metrics|audit|test-setup|leases/'+LEASE+r')\Z'),
 'POST':re.compile(r'v1/(?:leases|leases/'+LEASE+r'/tests)\Z'),
 'DELETE':re.compile(r'v1/leases/'+LEASE+r'\Z')}

def create_ui_app(e=None):
    e=e or engine()
    if e.cfg.auth_mode!='gateway':raise ValueError('Split UI requires gateway authentication')
    app=create_app(e,worker=False)
    schema=app.openapi()
    # Reuse public assets, API reference, security/error handlers and readiness.
    # No lifecycle or mock route is registered on the UI listener.
    public={'/','/unity','/static','/docs','/redoc','/openapi.json','/health/live','/health/ready'}
    app.router.routes[:]=[r for r in app.router.routes if r.path in public]
    app.openapi=lambda:schema
    connector=ConnectorAuth(e,Auth(e.cfg))
    connector.install(app)
    app.state.connector=connector

    @app.api_route('/connector/{path:path}',methods=['GET','POST','DELETE'],include_in_schema=False)
    async def proxy(path:str,request:Request):
        if not ROUTES[request.method].fullmatch(path) or request.url.query:
            raise Problem(404,'NOT_FOUND')
        session=connector.session(request,csrf=request.method!='GET')
        headers={'Authorization':'Bearer '+session['token'],'Content-Type':'application/json'}
        if 'idempotency-key' in request.headers:headers['Idempotency-Key']=request.headers['idempotency-key']
        try:
            async with httpx.AsyncClient(timeout=150,follow_redirects=False) as client:
                upstream=await client.request(request.method,e.cfg.oauth['public_url']+'/'+path,
                    headers=headers,content=await request.body())
        except httpx.HTTPError:
            raise Problem(503,'GATEWAY_UNAVAILABLE') from None
        forwarded={k:upstream.headers[k] for k in ('content-type','retry-after','x-correlation-id') if k in upstream.headers}
        return Response(upstream.content,status_code=upstream.status_code,headers=forwarded)
    return app
