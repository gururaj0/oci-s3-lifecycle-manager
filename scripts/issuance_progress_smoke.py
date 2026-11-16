"""Observe one real lease issuance without logging or persisting credentials."""
import concurrent.futures,json,subprocess,sys,time,uuid
from pathlib import Path
import requests
ROOT=Path(__file__).resolve().parents[1]
base=json.loads((ROOT/'.local/live-config.json').read_text())['oauth']['public_url']
p=subprocess.run([sys.executable,'scripts/identity_demo_token.py'],cwd=ROOT,capture_output=True,text=True,check=True)
token=p.stdout.strip();del p
headers={'Authorization':'Bearer '+token};request_id=str(uuid.uuid4());lease_id=None;checks={};observations=[];started=time.monotonic()
def progress():return requests.get(base+'/v1/lease-requests/'+request_id,headers=headers,timeout=30)
def create():
 r=requests.post(base+'/v1/leases',headers={**headers,'Idempotency-Key':request_id},json={'scope_profile':'sales-read','ttl_seconds':300,'client_request_id':'issuance-progress-test'},timeout=150)
 return r,round(time.monotonic()-started,2)
try:
 with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
  future=pool.submit(create)
  while not future.done():
   r=progress();assert r.status_code in (200,404),r.status_code
   if r.status_code==200:
    data=r.json();lease_id=data['lease']['lease_id']
    assert 'secret_access_key' not in r.text and 'access_key_id' not in r.text
    if not observations or observations[-1]['phase']!=data['phase']:
     observations.append({'phase':data['phase'],'elapsed_seconds':round(time.monotonic()-started,2)})
   if not future.done():time.sleep(2)
  r,elapsed=future.result()
  if r.status_code not in (200,201):
   lease_id=lease_id or r.json().get('error',{}).get('lease_id');raise RuntimeError('Issuance HTTP '+str(r.status_code))
  credentials=r.json();lease_id=credentials['lease_id']
  r=progress();assert r.status_code==200 and r.json()['phase']=='ACTIVE'
  assert credentials['secret_access_key'] not in r.text and credentials['access_key_id'] not in r.text
  credentials.clear()
 checks['live_issuance_succeeded']=True
 checks['progress_never_exposes_credentials']=True
 checks['pending_stage_observed']=any(x['phase'] in ('PREPARING','CONFIRMING_SCHEDULE','PREPARING_REPLAY') for x in observations)
 checks['ready_stage_confirmed']=True
 assert requests.get(base+'/v1/lease-requests/'+request_id,timeout=30).status_code==401
 checks['anonymous_progress_rejected']=True
finally:
 if lease_id:
  r=requests.delete(base+'/v1/leases/'+lease_id,headers=headers,timeout=90);assert r.status_code in (202,204)
  for _ in range(30):
   r=requests.get(base+'/v1/leases/'+lease_id,headers=headers,timeout=30)
   if r.json()['status']=='REVOKED':break
   time.sleep(2)
  else:raise AssertionError('Test lease cleanup incomplete')
  checks['only_test_lease_revoked']=True
 out={'checks':checks,'observed_stages':observations,'observed_request_seconds':round(time.monotonic()-started,2),'passed':len(checks)==6 and all(checks.values()),'production_ready':False}
 if 'elapsed' in globals():out['observed_issuance_seconds']=elapsed
 (ROOT/'docs/issuance-progress-live-evidence.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
