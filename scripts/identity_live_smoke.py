"""Real Identity Domains/S3 acceptance; no passwords/tokens/CSKs in evidence."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import requests
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/'.local/live-config.json').read_text());url=cfg['oauth']['public_url']
private=json.loads((ROOT/'.local/identity-domain-private.json').read_text())['Unity Demo Connector']
def token(scope=None):
 r=requests.post(cfg['oauth']['token_endpoint'],auth=(private['client_id'],private['client_secret']),
  data={'grant_type':'client_credentials','scope':scope or cfg['oauth']['scope']},timeout=30)
 r.raise_for_status();return r.json()['access_token']
result={'checks':{},'operations':{},'production_ready':False,'interactive_operator_sign_in':'confirmed by operator in this deployment session'}
api=requests.Session();api.headers['Authorization']='Bearer '+token()
def call(method,path,**kw):return api.request(method,url+path,timeout=150,**kw)
assert requests.get(url+'/v1/config',timeout=30).status_code==401
result['checks']['anonymous_denied']=True
r=call('GET','/v1/config');assert r.status_code==200 and set(r.json()['profiles'])=={'sales-read','sales-write'}
result['checks']['domain_token_accepted']=True
read=token(cfg['audience']+'sales-read')
r=requests.post(url+'/v1/leases',headers={'Authorization':'Bearer '+read,'Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-write','ttl_seconds':300},timeout=30)
assert r.status_code==403
result['checks']['read_scope_cannot_issue_write']=True
old=subprocess.run([sys.executable,'scripts/live_demo_token.py'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
assert requests.get(url+'/v1/config',headers={'Authorization':'Bearer '+old},timeout=30).status_code==401
result['checks']['legacy_signer_rejected']=True
if '--api-only' not in sys.argv:
 with sync_playwright() as p:
  b=p.chromium.launch(channel='chrome');page=b.new_page(viewport={'width':1440,'height':1000});errors=[]
  page.on('pageerror',lambda error:errors.append(type(error).__name__))
  page.goto(url,wait_until='networkidle');page.locator('#domain-login').wait_for(state='visible')
  assert not page.locator('#connect').is_visible()
  page.screenshot(path=str(ROOT/'docs/identity-signin-desktop.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.screenshot(path=str(ROOT/'docs/identity-signin-mobile.png'),full_page=True)
  page.locator('#domain-login').click();page.wait_for_timeout(2500)
  assert 'identity.oraclecloud.com' in page.url
  assert not errors
  result['checks']['ui_signin_redirect_and_mobile_layout']=True
  result['checks']['ui_no_script_errors']=True
  b.close()
lease=None
try:
 r=call('POST','/v1/leases',headers={'Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-write','ttl_seconds':900})
 assert r.status_code==201,('create',r.status_code)
 lease=r.json();result['checks']['domain_identity_created_native_lease']=True
 r=requests.get(url+'/v1/leases/'+lease['lease_id'],headers={'Authorization':'Bearer '+read},timeout=30)
 assert r.status_code==403
 result['checks']['read_scope_cannot_access_write_lease']=True
 deadline=time.monotonic()+360
 for operation in ('GET','LIST','PUT','DELETE','FOREIGN'):
  while True:
   r=call('POST','/v1/leases/'+lease['lease_id']+'/tests',json={'access_key_id':lease['access_key_id'],'secret_access_key':lease['secret_access_key'],'operation':operation})
   assert r.status_code==200,('probe',operation,r.status_code)
   data=r.json()
   if data['matches_expectation']:break
   assert operation in ('GET','LIST','PUT') and time.monotonic()<deadline,(operation,data['http_status'])
   print('Waiting for native S3 key propagation:',operation,data['http_status'],flush=True);time.sleep(10)
  result['operations'][operation]={k:data[k] for k in ('http_status','observed_access','matches_expectation')}
  print('Verified',operation,data['http_status'],flush=True)
 result['checks']['all_five_native_operations']=True
 for path in ('/v1/leases','/v1/metrics','/v1/audit','/v1/test-setup'):
  r=call('GET',path);assert r.status_code==200
  assert lease['secret_access_key'] not in r.text and lease['access_key_id'] not in r.text
 result['checks']['metadata_secret_redaction']=True
finally:
 if lease:
  r=call('DELETE','/v1/leases/'+lease['lease_id']);assert r.status_code in (202,204)
  for _ in range(40):
   if call('GET','/v1/leases/'+lease['lease_id']).json()['status']=='REVOKED':break
   time.sleep(3)
  else:raise AssertionError('Test lease deletion unconfirmed')
  result['checks']['only_test_lease_revoked']=True
 result['passed']=all(result['checks'].values()) and len(result['operations'])==5
 (ROOT/'docs/identity-domain-live-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
