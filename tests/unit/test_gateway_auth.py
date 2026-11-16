import time
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from shared.config import Config
from shared.errors import Problem
from shared.gateway_auth import GatewayAuth
from service.app import create_app
from service.ui import create_ui_app
from service.connector_auth import COOKIE

ISSUER='https://identity.oraclecloud.com/'
def cfg():
 return Config(mode='oci',auth_mode='gateway',gateway_trusted_networks=['10.73.0.0/24'],
  issuer=ISSUER,jwks_url='https://domain.test/jwk',profile_scopes={'sales-read':'sales-read','sales-write':'sales-write'},
  identities={ISSUER:{'connector':['sales-read','sales-write']}},
  oauth={'public_url':'https://gateway.test','login_client_id':'ui','connector_client_id':'connector'})
def headers(subject='connector',scope='sales-read'):
 return {'X-LCM-Issuer':ISSUER,'X-LCM-Subject':subject,'X-LCM-Scope':scope}
def request(peer='10.73.0.8',values=None):
 return Request({'type':'http','method':'GET','path':'/v1/config','client':(peer,1234),
  'headers':[(k.lower().encode(),v.encode()) for k,v in (values or headers()).items()]})

def test_gateway_assertion_uses_transport_peer_and_never_decodes_jwt(monkeypatch):
 auth=GatewayAuth(cfg())
 monkeypatch.setattr('jwt.decode',lambda *a,**k:pytest.fail('Lifecycle API must not decode JWTs'))
 r=request(values={**headers(),'Authorization':'Bearer deliberately-not-a-jwt'})
 who=auth.verify_request(r)
 assert who['subject']=='connector' and auth.allowed(who)==['sales-read']

@pytest.mark.parametrize('peer',['127.0.0.1','10.73.1.6','172.16.61.78','198.51.100.1'])
def test_forged_identity_from_non_gateway_transport_is_denied(peer):
 with pytest.raises(Problem) as e:
  GatewayAuth(cfg()).verify_request(request(peer,dict(headers(),**{'X-Forwarded-For':'10.73.0.8'})))
 assert e.value.status==401

@pytest.mark.parametrize('claim',['Issuer','Subject','Scope'])
def test_missing_or_duplicated_gateway_headers_denied(claim):
 h=headers();h.pop('X-LCM-'+claim)
 with pytest.raises(Problem):GatewayAuth(cfg()).verify_request(request(values=h))
 r=request();r.scope['headers'].append((('x-lcm-'+claim.lower()).encode(),b'forged'))
 with pytest.raises(Problem):GatewayAuth(cfg()).verify_request(r)

def test_api_authorizes_profiles_from_verified_gateway_claims(env):
 e=env[0];e.cfg=cfg()
 app=create_app(e,worker=False)
 # Use a test transport with an explicit gateway address, not a forged XFF header.
 with TestClient(app,base_url='https://api.test',client=('10.73.0.8',1234)) as client:
  r=client.get('/v1/config',headers=headers());assert r.status_code==200
  assert list(r.json()['profiles'])==['sales-read']
  assert client.get('/v1/config',headers=headers(subject='unmapped')).status_code==403
  assert client.get('/v1/config',headers=headers(scope='unrelated')).status_code==403
  assert client.get('/v1/config',headers={'Authorization':'Bearer token'}).status_code==401

def test_ui_has_no_direct_lifecycle_routes_and_proxies_only_safe_request(env,monkeypatch):
 e=env[0];e.cfg=cfg();app=create_ui_app(e)
 connector=app.state.connector
 connector.sessions['session']={'expires':time.time()+600,'token':'server-only-token','csrf':'csrf','login_subject':'operator','subject':'connector'}
 captured=[]
 async def upstream(self,method,url,**kwargs):
  captured.append((method,url,kwargs))
  return httpx.Response(200,json={'ok':True})
 monkeypatch.setattr(httpx.AsyncClient,'request',upstream)
 with TestClient(app,base_url='https://gateway.test') as client:
  client.cookies.set(COOKIE,'session')
  assert client.get('/v1/config',headers=headers()).status_code==404
  assert client.post('/v1/leases',headers=headers()).status_code==404
  assert client.get('/connector/https://evil.test').status_code==404
  assert client.get('/connector/v1/config?target=evil').status_code==404
  assert client.post('/connector/v1/leases',json={}).status_code==403
  r=client.post('/connector/v1/leases',json={'scope_profile':'sales-read'},headers={
   'Origin':'https://gateway.test','X-LCM-CSRF':'csrf','Idempotency-Key':'request-id',
   'Authorization':'Bearer attacker','X-LCM-Subject':'attacker','Cookie':COOKIE+'=session'})
  assert r.status_code==200
  method,url,options=captured[0]
  assert method=='POST' and url=='https://gateway.test/v1/leases'
  assert options['headers']=={'Authorization':'Bearer server-only-token','Content-Type':'application/json','Idempotency-Key':'request-id'}
  assert 'server-only-token' not in r.text
  assert client.get('/openapi.json').status_code==200


def test_lease_owner_check_survives_gateway_authentication(env):
 import uuid
 e=env[0]
 owner={'issuer':ISSUER,'subject':'another-client'}
 _,lease=e.create(owner,{'scope_profile':'sales-read','ttl_seconds':300},str(uuid.uuid4()),'ownership','https://s3.test')
 e.cfg=cfg();app=create_app(e,worker=False)
 with TestClient(app,client=('10.73.0.8',1234)) as client:
  r=client.get('/v1/leases/'+lease['lease_id'],headers=headers())
  assert r.status_code==403
  r=client.delete('/v1/leases/'+lease['lease_id'],headers=headers())
  assert r.status_code==403
