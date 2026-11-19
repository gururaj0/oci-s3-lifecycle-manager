"""Measure a diagnostic during setup and distinguish API latency from S3 readiness.
No credentials or object contents are logged. Use --baseline before deploying.
"""
import json,sys,time,uuid,subprocess,shlex
from pathlib import Path

def remote():
 import requests,boto3,oci,base64
 from botocore.config import Config
 from botocore.exceptions import ClientError
 from shared.runtime import engine
 e=engine();cfg=e.cfg;o=cfg.oauth;base=o['public_url'].rstrip('/');baseline='--baseline' in sys.argv
 secret=json.loads(base64.b64decode(e.cloud.secrets.get_secret_bundle(o['secret_id']).data.secret_bundle_content.content))['connector_client_secret']
 token=requests.post(o['token_endpoint'],auth=(o['connector_client_id'],secret),data={'grant_type':'client_credentials','scope':o['scope']},timeout=30);token.raise_for_status()
 headers={'Authorization':'Bearer '+token.json()['access_token']};idem=str(uuid.uuid4());lid=None;credential=None;session=requests.Session();samples=[]
 result={'baseline':baseline,'oauth_generation_excluded':True,'samples':samples,'passed':False}
 def emit(v):print(json.dumps(v),flush=True)
 def meta():
  r=session.get(base+'/v1/leases/'+lid,headers=headers,timeout=30);r.raise_for_status();return r.json()
 try:
  with e.db.connect() as c:
   old=c.execute("SELECT g.group_id FROM prefix_grants g JOIN leases l ON l.id=g.lease_id WHERE g.prefix='quarterly/' AND g.operations=? ORDER BY l.created_at DESC FETCH FIRST 1 ROWS ONLY",(json.dumps(['GET','LIST']),)).fetchone()
  start=time.monotonic();r=session.post(base+'/v1/leases',headers={**headers,'Idempotency-Key':idem},json={'bucket':cfg.profiles['sales-read']['bucket'],'prefix':'quarterly/','operations':['GET','LIST'],'ttl_seconds':900,'delivery_mode':'immediate'},timeout=150)
  result['credential_response_seconds']=round(time.monotonic()-start,4)
  if r.status_code!=201:raise RuntimeError('ISSUE_HTTP_'+str(r.status_code))
  credential=r.json();lid=credential['lease_id'];result.update(lease_id=lid,backend_timings=credential.get('timings',{}))
  emit({'event':'credentials','seconds':result['credential_response_seconds'],'backend_timings':result['backend_timings']})
  phase=meta();result['same_existing_group']=bool(old and old['group_id']==phase['group_id']);result['phase_before_test']=phase['phase']
  start_test=time.monotonic();test=session.post(base+'/v1/leases/'+lid+'/tests',headers=headers,json={'access_key_id':credential['access_key_id'],'secret_access_key':credential['secret_access_key'],'operation':'GET','object_name':'quarterly/revenue.csv'},timeout=120)
  result['diagnostic_http_status']=test.status_code
  if not test.ok:
   result['diagnostic_error']=test.json().get('error',test.json().get('message','HTTP_ERROR'))
  test.raise_for_status()
  result['diagnostic_round_trip_seconds']=round(time.monotonic()-start_test,4);result['diagnostic']=test.json();result['phase_after_test']=meta()['phase']
  emit({'event':'diagnostic','seconds':result['diagnostic_round_trip_seconds'],'result':result['diagnostic'],'phase_after':result['phase_after_test']})
  s3=boto3.client('s3',endpoint_url=credential['endpoint'],region_name=credential['region'],aws_access_key_id=credential['access_key_id'],aws_secret_access_key=credential['secret_access_key'],config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=5,read_timeout=15,retries={'max_attempts':0}))
  ready=set();deadline=time.monotonic()+(0 if baseline else 360)
  while True:
   for op in ('LIST','GET'):
    if op in ready:continue
    started=time.monotonic();sample={'operation':op,'since_response_seconds':round(started-start-result['credential_response_seconds'],3)}
    try:
     if op=='GET':v=s3.get_object(Bucket=cfg.profiles['sales-read']['bucket'],Key='quarterly/revenue.csv');v['Body'].close()
     else:v=s3.list_objects_v2(Bucket=cfg.profiles['sales-read']['bucket'],Prefix='quarterly/',MaxKeys=1)
     sample['http_status']=v['ResponseMetadata']['HTTPStatusCode'];ready.add(op)
    except ClientError as error:
     sample.update(http_status=error.response['ResponseMetadata']['HTTPStatusCode'],error_code=error.response.get('Error',{}).get('Code'))
    sample['call_seconds']=round(time.monotonic()-started,4);samples.append(sample);emit({'event':'s3_sample',**sample})
   if baseline or len(ready)==2 or time.monotonic()>=deadline:break
   time.sleep(10)
  s3.close();result['both_s3_operations_observed_ready']=len(ready)==2
  for _ in range(80):
   state=meta()['status']
   if state=='ACTIVE':break
   if state in ('REVOKED','REVOKE_PENDING'):raise RuntimeError('BACKGROUND_FINALIZATION_FAILED')
   time.sleep(2)
  else:raise RuntimeError('FINALIZATION_TIMEOUT')
  result['passed']=True
 finally:
  if not lid:
   recovery=session.get(base+'/v1/lease-requests/'+idem,headers=headers,timeout=30)
   if recovery.status_code==200:lid=recovery.json()['lease']['lease_id']
  if lid:
   started=time.monotonic();r=session.delete(base+'/v1/leases/'+lid,headers=headers,timeout=120);r.raise_for_status();result['revoke_response_seconds']=round(time.monotonic()-started,4)
   for _ in range(70):
    if meta()['status']=='REVOKED':break
    time.sleep(3)
   else:result['passed']=False;raise RuntimeError('CLEANUP_TIMEOUT')
   row=e.db.lease(lid)
   result['key_absent']=not e.cloud.list_keys(row['user_id'])
   result['membership_absent']=not e.cloud.identity.list_user_group_memberships(cfg.tenancy_id,user_id=row['user_id']).data
   result['passed']=result['passed'] and result['key_absent'] and result['membership_absent']
  if credential:credential.clear()
  session.close();emit({'event':'result',**result})

if __name__=='__main__':
 if '--remote' in sys.argv:
  try:remote()
  except Exception as exc:print(json.dumps({'event':'failure','error':type(exc).__name__}),flush=True);raise SystemExit(1)
 else:
  args=['sudo','podman','exec','-i','lcm-api','python','-','--remote']
  if '--baseline' in sys.argv:args.append('--baseline')
  p=subprocess.Popen(['ssh','-F','.local/ssh-config','lcm-live',shlex.join(args)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
  p.stdin.write(Path(__file__).read_text());p.stdin.close()
  for line in p.stdout:
   print(line,end='',flush=True);value=json.loads(line)
   if value.get('event')=='result':Path('docs/async-latency-'+('before' if '--baseline' in sys.argv else 'after')+'.json').write_text(json.dumps(value,indent=2)+'\n')
  raise SystemExit(p.wait())
