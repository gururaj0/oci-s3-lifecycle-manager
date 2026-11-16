"""Live gateway trust-boundary tests; tokens and customer keys never enter evidence."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import requests
ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/'.local/live-config.json').read_text());url=cfg['oauth']['public_url']
private=json.loads((ROOT/'.local/identity-domain-private.json').read_text())['Unity Demo Connector']
def token(scope=None):
 r=requests.post(cfg['oauth']['token_endpoint'],auth=(private['client_id'],private['client_secret']),data={'grant_type':'client_credentials','scope':scope or cfg['oauth']['scope']},timeout=30)
 r.raise_for_status();return r.json()['access_token']
full=token();read=token(cfg['audience']+'sales-read')
checks={};evidence={'checks':checks,'operations':{},'production_ready':False}
def call(path,method='GET',bearer=full,**kwargs):
 h=kwargs.pop('headers',{})
 if bearer:h['Authorization']='Bearer '+bearer
 return requests.request(method,url+path,headers=h,timeout=150,**kwargs)
fake={'X-LCM-Subject':private['client_id'],'X-LCM-Issuer':cfg['issuer'],
 'X-LCM-Scope':'sales-write sales-read','X-LCM-Expiry':str(int(time.time())+3600),'X-Forwarded-For':'10.73.0.8'}
assert call('/v1/config',bearer=None).status_code==401
assert call('/v1/config',bearer=None,headers=fake).status_code==401
checks['gateway_rejects_missing_jwt_and_forged_identity']=True
assert call('/v1/config',bearer='not-a-jwt').status_code==401
parts=full.split('.');parts[-1]=('A' if parts[-1][0]!='A' else 'B')+parts[-1][1:]
assert call('/v1/config',bearer='.'.join(parts)).status_code==401
checks['gateway_rejects_invalid_signature']=True
r=call('/v1/config');assert r.status_code==200,(r.status_code,r.text[:160])
assert r.json()['identity']['subject']==private['client_id']
checks['verified_claims_reach_backend']=True
r=call('/v1/config',bearer=read,headers={**fake,'X-LCM-Subject':'attacker','X-LCM-Issuer':'https://attacker.invalid/'})
assert r.status_code==200 and list(r.json()['profiles'])==['sales-read']
assert r.json()['identity']['subject']==private['client_id']
assert r.json()['identity']['issuer']==cfg['issuer']
checks['gateway_overwrites_identity_and_scope_spoofing']=True
r=call('/v1/leases','POST',bearer=read,headers={**fake,'Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-write','ttl_seconds':300})
assert r.status_code==403
checks['backend_rejects_profile_escalation']=True
assert call('/connector/v1/config',bearer=full).status_code==401
assert call('/connector/v1/config?target=http://example.com',bearer=full).status_code==404
checks['ui_proxy_requires_session_and_fixed_destination']=True
# A raw connection from the existing bastion must not reach the API listener.
code="import socket\ns=socket.socket();s.settimeout(5)\ntry:\n s.connect(('10.73.1.6',8081));print('UNEXPECTED_REACHABLE')\nexcept (TimeoutError,OSError):print('BLOCKED')\nfinally:s.close()\n"
r=subprocess.run(['ssh','-F','.local/ssh-config','lcm-bastion','python3 -'],input=code,cwd=ROOT,text=True,capture_output=True,check=True)
assert r.stdout.strip()=='BLOCKED'
checks['bastion_cannot_bypass_gateway_to_api_listener']=True
# Even a local request with forged proxy/identity headers is rejected by peer binding.
code="import requests\nr=requests.get('http://127.0.0.1:8081/v1/config',headers="+repr(fake)+",timeout=10)\nprint(r.status_code)\n"
r=subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','sudo /opt/lcm-live/venv/bin/python -'],input=code,cwd=ROOT,text=True,capture_output=True,check=True)
assert r.stdout.strip()=='401'
checks['backend_rejects_local_forgery_and_forwarded_for']=True
lease=None
try:
 r=call('/v1/leases','POST',headers={'Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-write','ttl_seconds':900})
 assert r.status_code==201,('create',r.status_code)
 lease=r.json();checks['real_lease_created_through_gateway']=True
 assert call('/v1/leases/'+lease['lease_id'],bearer=read,headers=fake).status_code==403
 checks['backend_checks_lease_profile']=True
 deadline=time.monotonic()+360
 for op in ('GET','LIST','PUT','DELETE','FOREIGN'):
  while True:
   r=call('/v1/leases/'+lease['lease_id']+'/tests','POST',json={'access_key_id':lease['access_key_id'],'secret_access_key':lease['secret_access_key'],'operation':op})
   assert r.status_code==200,('test',op,r.status_code)
   result=r.json()
   if result['matches_expectation']:break
   assert op in ('GET','LIST','PUT') and time.monotonic()<deadline,(op,result['http_status'])
   print('Waiting for native S3 propagation',op,result['http_status'],flush=True);time.sleep(10)
  evidence['operations'][op]={k:result[k] for k in ('http_status','observed_access','matches_expectation')}
  print('Verified',op,result['http_status'],flush=True)
 checks['native_s3_checks_passed']=True
finally:
 if lease:
  # Get a fresh service token in case the propagation checks consumed its lifetime.
  current=token()
  r=call('/v1/leases/'+lease['lease_id'],'DELETE',bearer=current);assert r.status_code in (202,204)
  for _ in range(40):
   if call('/v1/leases/'+lease['lease_id'],bearer=current).json()['status']=='REVOKED':break
   time.sleep(3)
  else:raise AssertionError('Test lease deletion unconfirmed')
  checks['only_test_lease_revoked']=True
 evidence['passed']=all(checks.values()) and len(evidence['operations'])==5
 (ROOT/'docs/gateway-auth-live-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
print(json.dumps(evidence,indent=2))
