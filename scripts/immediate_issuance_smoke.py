"""Verify early key delivery on OCI; never persist or print credential values."""
import json,subprocess,sys,time,uuid
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]
base=json.loads((ROOT/'.local/live-config.json').read_text())['oauth']['public_url']
p=subprocess.run([sys.executable,'scripts/identity_demo_token.py'],cwd=ROOT,capture_output=True,text=True,check=True)
headers={'Authorization':'Bearer '+p.stdout.strip(),'Idempotency-Key':str(uuid.uuid4())};del p
body={'scope_profile':'sales-read','ttl_seconds':300,'delivery_mode':'immediate','client_request_id':'immediate-issuance-test'}
lease_id=None;checks={};stages=[];started=time.monotonic();timings={}
try:
 r=requests.post(base+'/v1/leases',headers=headers,json=body,timeout=150)
 timings['credentials_returned_seconds']=round(time.monotonic()-started,2)
 assert r.status_code==201,r.status_code
 credentials=r.json();lease_id=credentials['lease_id']
 assert credentials['access_key_id'] and credentials['secret_access_key']
 checks['credentials_returned_before_finalization']=credentials['status']=='SCHEDULE_PENDING'
 for _ in range(60):
  r=requests.get(base+'/v1/lease-requests/'+headers['Idempotency-Key'],headers=headers,timeout=30)
  assert r.status_code==200,r.status_code
  assert credentials['secret_access_key'] not in r.text and credentials['access_key_id'] not in r.text
  phase=r.json()['phase']
  assert r.json()['lease']['phase']==phase
  listing=requests.get(base+'/v1/leases',headers=headers,timeout=30);assert listing.status_code==200
  listed=next(l for l in listing.json()['leases'] if l['lease_id']==lease_id)
  assert listed['phase'] in ('CONFIRMING_SCHEDULE','PREPARING_REPLAY','ACTIVE')
  if not stages or stages[-1]['phase']!=phase:stages.append({'phase':phase,'elapsed_seconds':round(time.monotonic()-started,2)})
  assert phase not in ('REVOKED','REVOKE_PENDING'),phase
  if phase=='ACTIVE':break
  time.sleep(2)
 else:raise AssertionError('Background finalization timed out')
 timings['finalization_observed_seconds']=round(time.monotonic()-started,2)
 checks['background_completed']=True
 checks['progress_never_exposes_credentials']=True
 r=requests.post(base+'/v1/leases',headers=headers,json=body,timeout=30)
 assert r.status_code==200 and r.json()['status']=='ACTIVE'
 assert r.json()['secret_access_key']==credentials['secret_access_key'] and r.json()['lease_id']==lease_id
 credentials.clear();checks['same_key_replay_after_finalization']=True
finally:
 if lease_id:
  r=requests.delete(base+'/v1/leases/'+lease_id,headers=headers,timeout=90);assert r.status_code in (202,204),r.status_code
  for _ in range(30):
   r=requests.get(base+'/v1/leases/'+lease_id,headers=headers,timeout=30)
   if r.json()['status']=='REVOKED':break
   time.sleep(2)
  else:raise AssertionError('Test lease cleanup incomplete')
  checks['only_test_lease_revoked']=True
 result={'checks':checks,'timings':timings,'observed_stages':stages,'passed':len(checks)==5 and all(checks.values()),'production_ready':False}
 (ROOT/'docs/immediate-issuance-live-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2));assert result['passed']
