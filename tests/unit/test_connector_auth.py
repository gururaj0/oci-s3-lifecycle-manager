import base64
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs,urlparse
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from service.connector_auth import ConnectorAuth,COOKIE,STATE_COOKIE
from shared.auth import Auth
from shared.config import Config
from shared.errors import Problem

@pytest.fixture
def connector(tmp_path):
 cfg=Config(state_dir=tmp_path,issuer='https://issuer.test/',audience='api',
  identities={'https://issuer.test/':{'connector':['sales-read','sales-write']}},
  profile_scopes={'sales-read':'sales-read','sales-write':'sales-write'},oauth={
   'secret_id':'secret','public_url':'https://demo.test','authorization_endpoint':'https://issuer.test/authorize',
   'token_endpoint':'https://issuer.test/token','login_client_id':'browser','connector_client_id':'connector',
   'allowed_login_subjects':['operator'],'scope':'sales-read sales-write'})
 auth=Auth(cfg);auth.jwk_client=SimpleNamespace(get_signing_key_from_jwt=lambda token:SimpleNamespace(key=auth.public))
 engine=SimpleNamespace(cfg=cfg)
 value=ConnectorAuth(engine,auth)
 value.credentials=lambda:{'login_client_secret':'secret1','connector_client_secret':'secret2'}
 app=FastAPI();value.install(app)
 with TestClient(app,base_url='https://demo.test',follow_redirects=False) as client:yield value,auth,client

def signed(auth,sub,aud,**extra):
 return jwt.encode(dict(iss=auth.cfg.issuer,sub=sub,aud=aud,iat=int(time.time()),exp=int(time.time())+600,**extra),auth.private,algorithm='RS256')

def login(connector,subject='operator',bad_nonce=False):
 value,auth,client=connector
 r=client.get('/auth/login');query=parse_qs(urlparse(r.headers['location']).query)
 nonce=query['nonce'][0]
 calls=[]
 def exchange(cid,secret,data):
  calls.append(cid)
  if cid=='browser':
   assert data['code_verifier'] and data['redirect_uri']=='https://demo.test/auth/callback'
   return {'id_token':signed(auth,subject,'browser',nonce='bad' if bad_nonce else nonce)}
  return {'access_token':signed(auth,'connector','api',scope='sales-read sales-write'),'expires_in':600}
 value.exchange=exchange
 r=client.get('/auth/callback',params={'state':query['state'][0],'code':'private-code'})
 return r,query,calls

def test_real_session_keeps_jwt_server_side_and_requires_csrf(connector):
 value,auth,client=connector
 r,query,calls=login(connector)
 assert r.headers['location']=='/' and calls==['browser','connector']
 assert 'Secure' in r.headers['set-cookie'] and 'HttpOnly' in r.headers['set-cookie']
 session=client.get('/auth/session').json()
 assert session['authenticated'] and session['login_subject']=='operator'
 assert 'token' not in session and 'secret' not in json.dumps(session)
 assert len(value.sessions)==1
 with pytest.raises(Problem):
  value.session(SimpleNamespace(cookies={COOKIE:next(iter(value.sessions))},headers={}),csrf=True)
 r=client.post('/auth/logout',headers={'Origin':'https://demo.test','X-LCM-CSRF':session['csrf']})
 assert r.status_code==200 and not value.sessions
 assert not client.get('/auth/session').json()['authenticated']

@pytest.mark.parametrize('subject,bad_nonce',[('intruder',False),('operator',True)])
def test_unapproved_user_and_wrong_nonce_never_get_service_token(connector,subject,bad_nonce):
 value,auth,client=connector
 r,query,calls=login(connector,subject,bad_nonce)
 assert r.headers['location']=='/?sign_in=failed'
 assert calls==['browser'] and not value.sessions

def test_callback_state_binding_and_replay(connector):
 value,auth,client=connector
 r,query,calls=login(connector)
 r=client.get('/auth/callback',params={'state':query['state'][0],'code':'replay'})
 assert r.headers['location']=='/?sign_in=failed' and calls==['browser','connector']
 r=client.get('/auth/login');state=parse_qs(urlparse(r.headers['location']).query)['state'][0]
 client.cookies.clear()
 r=client.get('/auth/callback',params={'state':state,'code':'stolen'})
 assert r.headers['location']=='/?sign_in=failed' and calls==['browser','connector']

def test_scope_intersection_signature_and_audience(connector):
 value,auth,client=connector
 who=auth.verify('Bearer '+signed(auth,'connector','api',scope='sales-read'))
 assert auth.allowed(who)==['sales-read']
 assert not auth.allowed(auth.verify('Bearer '+signed(auth,'connector','api')))
 with pytest.raises(Problem):auth.verify('Bearer '+signed(auth,'connector','other-api',scope='sales-read'))
 with pytest.raises(Problem):auth.verify('Bearer '+signed(auth,'connector','api',scope=['sales-read']))

def test_expired_session_is_removed(connector):
 value,auth,client=connector
 login(connector)
 next(iter(value.sessions.values()))['expires']=time.time()-1
 assert not client.get('/auth/session').json()['authenticated']
 assert not value.sessions

@pytest.mark.parametrize('failure',['expired','issuer','signature'])
def test_access_token_trust_failures(connector,failure):
    value,auth,client=connector
    claims={'iss':auth.cfg.issuer,'aud':'api','sub':'connector','iat':int(time.time())-60,
            'exp':int(time.time())+600,'scope':'sales-read'}
    if failure=='expired':claims['exp']=int(time.time())-1
    if failure=='issuer':claims['iss']='https://untrusted.test/'
    token=jwt.encode(claims,auth.private,algorithm='RS256')
    if failure=='signature':
        head,body,signature=token.split('.')
        token='.'.join((head,body,('A' if signature[0]!='A' else 'B')+signature[1:]))
    with pytest.raises(Problem) as error:auth.verify('Bearer '+token)
    assert error.value.status==401


def test_split_ui_checks_service_identity_through_gateway_only(connector,monkeypatch):
    value,auth,client=connector
    value.engine.cfg.auth_mode='gateway'
    monkeypatch.setattr(auth,'verify',lambda *a:pytest.fail('UI must not validate service JWT'))
    seen=[]
    def gateway(url,**kwargs):
        seen.append((url,kwargs))
        return SimpleNamespace(status_code=200,json=lambda:{'profiles':{'sales-read':{}},
            'identity':{'issuer':auth.cfg.issuer,'subject':'connector','expires_at':int(time.time())+600}})
    monkeypatch.setattr('service.connector_auth.requests.get',gateway)
    r,query,calls=login(connector)
    assert r.headers['location']=='/'
    assert seen[0][0]=='https://demo.test/v1/config'
    assert seen[0][1]['headers']['Authorization'].startswith('Bearer ')
    assert seen[0][1]['allow_redirects'] is False
    assert client.get('/auth/session').json()['authenticated']
