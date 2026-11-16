"""Small single-process BFF for the Unity demo UI.
OAuth credentials and service JWTs never leave the backend. Restart requires sign-in.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from urllib.parse import urlencode

import jwt
import requests
from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from shared.errors import Problem

COOKIE='__Host-lcm-session'
STATE_COOKIE='__Host-lcm-login'

class ConnectorAuth:
    def __init__(self, engine, auth):
        self.engine=engine
        self.auth=auth
        self.cfg=engine.cfg.oauth
        self.pending={}
        self.sessions={}
        self.lock=threading.Lock()

    def prune(self):
        now=time.time()
        for items in (self.pending,self.sessions):
            for key in list(items):
                if items[key]['expires']<=now:items.pop(key,None)

    def credentials(self):
        try:
            bundle=self.engine.cloud.secrets.get_secret_bundle(self.cfg['secret_id']).data
            return json.loads(base64.b64decode(bundle.secret_bundle_content.content))
        except Exception:
            raise Problem(503,'IDENTITY_CREDENTIALS_UNAVAILABLE') from None

    def exchange(self, client, secret, data):
        try:
            r=requests.post(self.cfg['token_endpoint'],auth=(client,secret),data=data,timeout=(5,20),allow_redirects=False)
            if r.status_code!=200:raise ValueError('token exchange')
            return r.json()
        except (requests.RequestException,ValueError):
            raise Problem(503,'IDENTITY_TOKEN_EXCHANGE_FAILED') from None

    def session(self, request, csrf=False):
        with self.lock:
            self.prune()
            value=self.sessions.get(request.cookies.get(COOKIE,''))
        if not value:raise Problem(401,'SIGN_IN_REQUIRED')
        if csrf and (request.headers.get('origin')!=self.cfg['public_url'] or
                     not secrets.compare_digest(request.headers.get('x-lcm-csrf',''),value['csrf'])):
            raise Problem(403,'CSRF_REJECTED')
        return value

    def owner(self,request):
        session=self.session(request,csrf=request.method not in ('GET','HEAD','OPTIONS'))
        return self.auth.verify('Bearer '+session['token'])

    def install(self,app):
        @app.get('/auth/login',include_in_schema=False)
        def login():
            state=secrets.token_urlsafe(32);binding=secrets.token_urlsafe(32)
            nonce=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48)
            with self.lock:
                self.prune()
                if len(self.pending)>=256:raise Problem(429,'LOGIN_BUSY')
                self.pending[state]={'binding':binding,'nonce':nonce,'verifier':verifier,'expires':time.time()+300}
            query=urlencode({'response_type':'code','client_id':self.cfg['login_client_id'],
                'redirect_uri':self.cfg['public_url']+'/auth/callback','scope':'openid',
                'state':state,'nonce':nonce,'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                'code_challenge_method':'S256'})
            result=RedirectResponse(self.cfg['authorization_endpoint']+'?'+query,status_code=303)
            result.set_cookie(STATE_COOKIE,binding,max_age=300,secure=True,httponly=True,samesite='lax',path='/')
            return result

        @app.get('/auth/callback',include_in_schema=False)
        def callback(request:Request):
            try:
                with self.lock:
                    self.prune()
                    state=self.pending.get(request.query_params.get('state',''))
                    if not state or not secrets.compare_digest(request.cookies.get(STATE_COOKIE,''),state['binding']):
                        raise Problem(401,'LOGIN_STATE_INVALID')
                    self.pending.pop(request.query_params.get('state',''))
                code=request.query_params.get('code')
                if not code or request.query_params.get('error'):raise Problem(401,'LOGIN_CANCELLED')
                creds=self.credentials()
                tokens=self.exchange(self.cfg['login_client_id'],creds['login_client_secret'],{
                    'grant_type':'authorization_code','code':code,'redirect_uri':self.cfg['public_url']+'/auth/callback','code_verifier':state['verifier']})
                token=tokens['id_token']
                key=self.auth.jwk_client.get_signing_key_from_jwt(token).key
                claims=jwt.decode(token,key,algorithms=['RS256'],issuer=self.engine.cfg.issuer,
                    audience=self.cfg['login_client_id'],options={'require':['iss','aud','sub','exp','iat','nonce']})
                if not secrets.compare_digest(claims['nonce'],state['nonce']):raise ValueError('nonce')
                if claims['sub'] not in self.cfg['allowed_login_subjects']:raise Problem(403,'LOGIN_USER_NOT_ALLOWED')
                if claims.get('azp',self.cfg['login_client_id'])!=self.cfg['login_client_id']:raise ValueError('azp')
                issued_at=time.time()
                service_response=self.exchange(self.cfg['connector_client_id'],creds['connector_client_secret'],{
                    'grant_type':'client_credentials','scope':self.cfg['scope']})
                service=service_response['access_token']
                if self.engine.cfg.auth_mode=='gateway':
                    # The connector consumes the protected API as a real Unity client would.
                    # JWT verification happens at the gateway, not in this UI process.
                    try:
                        verified=requests.get(self.cfg['public_url']+'/v1/config',
                            headers={'Authorization':'Bearer '+service},timeout=(5,20),allow_redirects=False)
                        if verified.status_code!=200:raise ValueError('gateway authentication')
                        info=verified.json()
                        if not info['profiles']:raise ValueError('connector authorization')
                        owner=info['identity']
                        lifetime=int(service_response.get('expires_in',0))
                        if lifetime<=0:raise ValueError('token lifetime')
                        expires=min(issued_at+min(600,lifetime),claims['exp'])
                    except (requests.RequestException,ValueError,KeyError,TypeError):
                        raise Problem(503,'GATEWAY_CONNECTOR_AUTH_FAILED') from None
                else:
                    owner=self.auth.verify('Bearer '+service)
                    if not self.auth.allowed(owner):raise Problem(403,'CONNECTOR_NOT_ALLOWED')
                    service_claims=jwt.decode(service,options={'verify_signature':False})
                    expires=min(time.time()+600,service_claims['exp'],claims['exp'])
                session_id=secrets.token_urlsafe(32)
                with self.lock:
                    self.prune()
                    if len(self.sessions)>=256:raise Problem(429,'LOGIN_BUSY')
                    self.sessions.pop(request.cookies.get(COOKIE,''),None)
                    self.sessions[session_id]={'expires':expires,'token':service,'csrf':secrets.token_urlsafe(32),
                        'login_subject':claims['sub'],'subject':owner['subject']}
                result=RedirectResponse('/',status_code=303)
                result.set_cookie(COOKIE,session_id,max_age=max(1,int(expires-time.time())),secure=True,httponly=True,samesite='lax',path='/')
            except (Problem,jwt.PyJWTError,ValueError,KeyError,TypeError):
                # Do not reflect IdP details, tokens or authorization codes into URLs/logs.
                result=RedirectResponse('/?sign_in=failed',status_code=303)
            result.delete_cookie(STATE_COOKIE,path='/',secure=True,httponly=True,samesite='lax')
            return result

        @app.get('/auth/session',include_in_schema=False)
        def current(request:Request):
            try:value=self.session(request)
            except Problem:return {'enabled':True,'authenticated':False}
            return {'enabled':True,'authenticated':True,'csrf':value['csrf'],
                'login_subject':value['login_subject'],'connector_subject':value['subject'],
                'expires_at':value['expires'],'issuer':self.engine.cfg.issuer}

        @app.post('/auth/logout',include_in_schema=False)
        def logout(request:Request):
            self.session(request,csrf=True)
            with self.lock:self.sessions.pop(request.cookies.get(COOKIE,''),None)
            result=JSONResponse({'signed_out':True})
            result.delete_cookie(COOKIE,path='/',secure=True,httponly=True,samesite='lax')
            return result
