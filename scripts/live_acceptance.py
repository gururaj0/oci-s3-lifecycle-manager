"""Opt-in real HTTPS/S3 acceptance. Tokens and customer secrets stay in process memory.
Uses the existing DemoBastion only to mint a private test subject at the demo issuer.
"""
import argparse,json,math,subprocess,time,uuid
from pathlib import Path
from datetime import datetime
import boto3,httpx
from botocore.config import Config
from botocore.exceptions import ClientError

root=Path(__file__).resolve().parents[1]
args=argparse.ArgumentParser();args.add_argument('--count',type=int,default=0);args.add_argument('--expiry',action='store_true');args.add_argument('--scheduler-only',action='store_true');args.add_argument('--performance-only',action='store_true');args.add_argument('--upgrade-check',action='store_true');args=args.parse_args()
if args.scheduler_only and not args.expiry:raise SystemExit('--scheduler-only requires --expiry')
cfg=json.loads((root/'.local/live-config.json').read_text());url=cfg['public_url']
subject=next(iter(cfg['identities'][cfg['issuer']]))
code="""from pathlib import Path
import json,time,jwt
from shared.config import Config
from shared.auth import Auth
a=Auth(Config(state_dir=Path('/var/lib/lcm')))
now=int(time.time())
claims={'iss':a.cfg.issuer,'aud':a.cfg.audience,'sub':SUBJECT,'iat':now,'exp':now+3600}
out={}
for name,changes in [('valid',{}),('other',{'sub':'demo:unmapped-negative-owner'}),('issuer',{'iss':'urn:wrong'}),('audience',{'aud':'wrong'}),('expired',{'iat':now-100,'exp':now-10})]:
 out[name]=jwt.encode(dict(claims,**changes),a.private,algorithm='RS256',headers={'kid':'demo-v1'})
print(json.dumps(out))
""".replace('SUBJECT',repr(subject))
mint=subprocess.run(['ssh','-F','.local/ssh-config','lcm-backend','cd /opt/lcm && sudo env PYTHONPATH=/opt/lcm /opt/lcm/venv/bin/python -'],input=code,cwd=root,capture_output=True,text=True,check=True)
tokens=json.loads(mint.stdout);del mint
token_time=time.monotonic()
client=httpx.Client(base_url=url,timeout=150)
headers={'Authorization':'Bearer '+tokens['valid']}
out={'mode':'live-oci','credentials_logged':False,'checks':{},'requests':[]}
leases=[]
cleanup_stopped=False
def services(action):
 command='sudo systemctl '+action+' lcm-cleanup.timer lcm-api.service'
 if action=='stop':command+=' lcm-cleanup.service'
 subprocess.run(['ssh','-F','.local/ssh-config','lcm-live',command],cwd=root,check=True,capture_output=True,text=True)
 if action=='stop':
  processes=subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','sudo podman ps --format "{{.Names}} {{.Command}}"'],cwd=root,check=True,capture_output=True,text=True).stdout
  assert 'service.main:app' not in processes and '-m scripts.worker' not in processes,'VM cleanup container remained running'
def deletion_actor(lease_id):
 code="""from shared.config import Config
from shared.oracle_ledger import OracleLedger
d=OracleLedger(Config.env())
with d.connect() as c:
 r=c.execute('SELECT detail FROM audit WHERE lease_id=? AND action=? ORDER BY at DESC LIMIT 1',(LEASE,'REVOKED')).fetchone()
 print(r[0] if r else 'PENDING')
""".replace('LEASE',repr(lease_id))
 for attempt in range(4):
  result=subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','cd /opt/lcm-live/source && sudo env PYTHONPATH=. LCM_CONFIG=/etc/lcm-live/config.json /opt/lcm-live/venv/bin/python -'],input=code,cwd=root,capture_output=True,text=True)
  if result.returncode==0:return result.stdout.strip()
  if result.returncode!=255:raise RuntimeError('ACTOR_QUERY_FAILED')
  time.sleep(2)
 return 'QUERY_UNAVAILABLE'
def request(method,path,token='valid',**kwargs):
 global tokens,token_time
 if time.monotonic()-token_time>2400:
  refreshed=subprocess.run(['ssh','-F','.local/ssh-config','lcm-backend','cd /opt/lcm && sudo env PYTHONPATH=/opt/lcm /opt/lcm/venv/bin/python -'],input=code,cwd=root,capture_output=True,text=True,check=True)
  tokens=json.loads(refreshed.stdout);token_time=time.monotonic()
 return client.request(method,path,headers={'Authorization':'Bearer '+tokens[token],**kwargs.pop('headers',{})},**kwargs)
def status(response,wanted,label):
 assert response.status_code==wanted,label+': HTTP '+str(response.status_code)+' '+response.json().get('error',{}).get('code','')
def create(ttl=900,idem=None):
 key=idem or str(uuid.uuid4());body={'scope_profile':'sales-read','ttl_seconds':ttl}
 started=time.monotonic();r=request('POST','/v1/leases',headers={'Idempotency-Key':key},json=body)
 elapsed=time.monotonic()-started
 if r.status_code not in (200,201):
  if r.json().get('error',{}).get('lease_id'):leases.append(r.json()['error']['lease_id'])
  out['requests'].append({'seconds':round(elapsed,3),'status':r.status_code,'code':r.json().get('error',{}).get('code')})
  status(r,201,'issuance')
 b=r.json();leases.append(b['lease_id'])
 out['requests'].append({'seconds':round(elapsed,3),'status':r.status_code})
 (root/'.local/live-performance-progress.json').write_text(json.dumps({'completed_issuances':len(out['requests']),'last_seconds':round(elapsed,3)}))
 return b,key,body
def s3(lease):return boto3.client('s3',endpoint_url=lease['endpoint'],region_name=lease['region'],
 aws_access_key_id=lease['access_key_id'],aws_secret_access_key=lease['secret_access_key'],
 config=Config(signature_version='s3v4',s3={'addressing_style':'path'},retries={'max_attempts':0}))
def allowed(s):
 try:
  body=s.get_object(Bucket=cfg['profiles']['sales-read']['bucket'],Key='quarterly/revenue.csv')['Body'].read()
  assert body.startswith(b'quarter,revenue\n');return True
 except ClientError as e:
  if e.response['ResponseMetadata']['HTTPStatusCode'] in (403,404):return False
  raise
def hygiene(lease):
 journal=subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','sudo journalctl -u lcm-api -u lcm-cleanup --no-pager -n 2000 -o cat'],cwd=root,capture_output=True,check=True).stdout
 sources={'runtime_journal':journal}
 for name in ('live.tfstate','live-outputs.json','live-config.json'):
  p=root/'.local'/name
  if p.exists():sources[name]=p.read_bytes()
 import oci
 schedules=oci.resource_scheduler.ScheduleClient(oci.config.from_file()).list_schedules(compartment_id=cfg['compartment_id']).data.items
 sources['schedule_payloads']=json.dumps([oci.util.to_dict(x) for x in schedules if (x.freeform_tags or {}).get('deployment')==cfg['deployment_id']],default=str).encode()
 material=[lease['access_key_id'].encode(),lease['secret_access_key'].encode()]
 findings={name:sum(value in data for value in material) for name,data in sources.items()}
 out['secret_hygiene_findings']=findings
 return not any(findings.values())
try:
 for attempt in range(30):
  if client.get('/health/ready').status_code==200:break
  time.sleep(2)
 status(client.get('/health/ready'),200,'readiness')
 status(client.get('/v1/config'),401,'anonymous')
 for kind in ('issuer','audience','expired'):status(request('GET','/v1/config',kind),401,'JWT '+kind)
 bad_signature=tokens['valid'][:-4]+'AAAA'
 status(client.get('/v1/config',headers={'Authorization':'Bearer '+bad_signature}),401,'invalid signature')
 status(client.get('/v1/config',headers={'X-Celonis-User':subject}),401,'unsigned identity header')
 out['checks']['jwt_rejections']=True
 lease,idem,body=create(300 if args.expiry else 900)
 replay=request('POST','/v1/leases',headers={'Idempotency-Key':idem},json=body)
 status(replay,200,'bounded replay');assert replay.json()==lease
 conflict=request('POST','/v1/leases',headers={'Idempotency-Key':idem},json=dict(body,ttl_seconds=body['ttl_seconds']+1));status(conflict,409,'idempotency conflict')
 metadata=request('GET','/v1/leases/'+lease['lease_id']);status(metadata,200,'status')
 assert 'secret_access_key' not in metadata.text and 'access_key_id' not in metadata.text
 status(request('GET','/v1/leases/'+lease['lease_id'],'other'),403,'cross-owner status')
 status(request('DELETE','/v1/leases/'+lease['lease_id'],'other'),403,'cross-owner revoke')
 out['checks'].update({'replay_same_credentials':True,'idempotency_conflict':True,'status_redacted':True,'owner_bound':True})
 out['checks']['actual_key_material_absent_from_logs_state_schedules']=hygiene(lease)
 if args.upgrade_check:
  upgrade=subprocess.run([__import__('sys').executable,str(root/'scripts/live_upgrade_check.py'),lease['lease_id']],cwd=root,capture_output=True,text=True,check=True)
  result=json.loads(upgrade.stdout)
  (root/'docs/live-active-upgrade-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
  out['checks']['active_lease_rollback_upgrade']=all(result.values())
 s=s3(lease);expires=datetime.fromisoformat(lease['expires_at'].replace('Z','+00:00')).timestamp()
 access_at=None
 while not args.performance_only and time.time()<expires:
  if allowed(s):access_at=time.time();break
  time.sleep(5)
 if not args.performance_only:out['checks']['native_s3_before_expiry']=access_at is not None
 if args.scheduler_only:
  cleanup_stopped=True;services('stop')
  out['cleanup_mode']='OCI Scheduler and Function only; API and VM timer stopped'
 if args.expiry:
  # Keep all cleanup paths enabled in the normal deadline acceptance test.
  while time.time()<expires:time.sleep(min(5,expires-time.time()))
 else:
  status(request('DELETE','/v1/leases/'+lease['lease_id']),202,'early revoke')
 denial=None;limit=time.time()+240;confirmed_denials=0
 out['expiry_observations']=[]
 while time.time()<limit:
  if args.scheduler_only:
   # Fresh-key propagation can produce transient 403s. Only count denial after
   # the durable Function audit confirms revocation, with repeated S3 checks.
   actor=deletion_actor(lease['lease_id']);s3_allowed=allowed(s)
   observation={'seconds_after_deadline':round(time.time()-expires,3),'actor':actor,'s3_allowed':s3_allowed}
   out['expiry_observations'].append(observation)
   (root/'.local/live-expiry-progress.json').write_text(json.dumps(observation))
   if actor=='function' and not s3_allowed:
    confirmed_denials+=1
    if confirmed_denials>=3:denial=time.time();break
   else:confirmed_denials=0
  else:
   r=request('GET','/v1/leases/'+lease['lease_id'])
   if r.status_code==200 and r.json()['status']=='REVOKED' and (args.performance_only or not allowed(s)):denial=time.time();break
  time.sleep(5)
 out['checks']['revocation_confirmed' if args.performance_only else 's3_denied_after_confirmed_deletion']=denial is not None
 if args.scheduler_only:
  services('start');cleanup_stopped=False
  for attempt in range(30):
   try:
    r=request('GET','/v1/leases/'+lease['lease_id'])
    if r.status_code==200:break
   except httpx.HTTPError:pass
   time.sleep(2)
  out['checks']['durable_revocation_after_api_restart']=r.status_code==200 and r.json()['status']=='REVOKED'
  out['deletion_actor']=deletion_actor(lease['lease_id'])
  out['checks']['deleted_by_function']=out['deletion_actor']=='function'
 out['lifecycle']={'lease_id':lease['lease_id'],'deadline':lease['expires_at'],'first_success_at':access_at,'observed_denial_at':denial,
  'delay_beyond_deadline_seconds':max(0,denial-expires) if denial else None}
 for _ in range(max(0,args.count-1)):
  b,_,_=create()
  r=request('DELETE','/v1/leases/'+b['lease_id']);status(r,202,'performance revoke')
  for attempt in range(60):
   if request('GET','/v1/leases/'+b['lease_id']).json()['status']=='REVOKED':break
   time.sleep(1)
 values=sorted(x['seconds'] for x in out['requests'] if x['status']==201)
 out['issuance']={'samples':len(values),'p95_seconds':values[max(0,math.ceil(.95*len(values))-1)] if len(values)>=100 else None,'target_seconds':15,
  'acceptance_measured':len(values)>=100,'maximum_observed_seconds':max(values)}
 if len(values)>=100:out['checks']['issuance_p95_target_met']=out['issuance']['p95_seconds']<=15
except Exception as e:
 out['failed']={'class':type(e).__name__,'reason':str(e) if isinstance(e,AssertionError) else 'See redacted dependency diagnostics'}
finally:
 if cleanup_stopped:
  try:services('start')
  except Exception:out['service_restore_failed']=True
 for lease_id in leases:
  try:request('DELETE','/v1/leases/'+lease_id)
  except Exception:pass
 client.close()
 out['passed']=not out.get('failed') and all(out['checks'].values())
 print(json.dumps(out,indent=2))
