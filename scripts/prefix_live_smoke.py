"""Live prefix isolation and TTL membership test. Never log tokens or S3 secrets."""
import json,sys,time,uuid,subprocess,os,re,shlex
from pathlib import Path


def remote():
 import base64,requests,oci,boto3
 from botocore.config import Config
 from botocore.exceptions import ClientError
 cfg=json.loads(Path(os.getenv('LCM_CONFIG','/etc/lcm-live/config.json')).read_text());assert cfg['prefix_enabled']
 signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner();iam=oci.identity.IdentityClient({'region':cfg['region']},signer=signer)
 vault=oci.secrets.SecretsClient({},signer=signer);oauth=cfg['oauth'];base=oauth['public_url'];bucket=cfg['profiles']['sales-write']['bucket']
 secret=json.loads(base64.b64decode(vault.get_secret_bundle(oauth['secret_id']).data.secret_bundle_content.content))['connector_client_secret']
 obj=oci.object_storage.ObjectStorageClient({'region':cfg['region']},signer=signer)
 root='prefix-smoke/'+uuid.uuid4().hex+'/'
 if '--reuse-prefix' in sys.argv:
  reuse=sys.argv[sys.argv.index('--reuse-prefix')+1]
  assert re.fullmatch(r'prefix-smoke/[a-f0-9]{32}/allowed/',reuse)
  root=reuse.removesuffix('allowed/')
 prefix=root+'allowed/';keys=[prefix+'data.txt',root+'sibling/data.txt',prefix+'write.txt']
 for key in keys[:2]:obj.put_object(cfg['namespace'],bucket,key,b'prefix test fixture')
 session=requests.Session();headers={};token_until=0;leases=[];checks={};times={};states=[];creds=None
 def emit(v):print(json.dumps(v),flush=True)
 def call(method,path,**kw):
  nonlocal token_until
  if time.monotonic()>token_until:
   r=requests.post(oauth['token_endpoint'],auth=(oauth['connector_client_id'],secret),data={'grant_type':'client_credentials','scope':oauth['scope']},timeout=30)
   assert r.status_code==200;headers['Authorization']='Bearer '+r.json()['access_token'];token_until=time.monotonic()+480
  for attempt in range(3):
   try:return session.request(method,base+path,headers={**headers,**kw.pop('headers',{})},timeout=150,**kw)
   except requests.ConnectionError:
    if method not in ('GET','DELETE') or attempt==2:raise
    time.sleep(2)
 def issue(ttl):
  started=time.monotonic();idem=str(uuid.uuid4())
  r=call('POST','/v1/leases',headers={'Idempotency-Key':idem},json={'bucket':bucket,'prefix':prefix,'operations':['GET','LIST','PUT'],'ttl_seconds':ttl,'delivery_mode':'immediate'})
  if r.status_code!=201:raise RuntimeError('CREATE_'+str(r.status_code)+'_'+r.json().get('error',{}).get('code',''))
  value=r.json();leases.append(value['lease_id']);times['issuance_'+str(len(leases))]=round(time.monotonic()-started,3)
  emit({'event':'issued','lease_id':value['lease_id'],'seconds':times['issuance_'+str(len(leases))]})
  return value
 def meta(lid):
  r=call('GET','/v1/leases/'+lid);assert r.status_code==200;return r.json()
 def absent(m):
  memberships=oci.pagination.list_call_get_all_results(iam.list_user_group_memberships,cfg['tenancy_id'],user_id=m['service_user_id']).data
  keys_now=oci.pagination.list_call_get_all_results(iam.list_customer_secret_keys,m['service_user_id']).data
  return not memberships and not keys_now
 def revoked(lid,timeout=180):
  deadline=time.monotonic()+timeout
  while time.monotonic()<deadline:
   m=meta(lid)
   if m['status']=='REVOKED':assert absent(m);return m
   time.sleep(3)
  raise RuntimeError('CLEANUP_TIMEOUT')
 try:
  # Prime token acquisition outside the issuance timing.
  assert call('GET','/v1/config').status_code==200
  creds=issue(900);m=meta(creds['lease_id'])
  assert m['prefix']==prefix and m['bucket']==bucket
  memberships=oci.pagination.list_call_get_all_results(iam.list_user_group_memberships,cfg['tenancy_id'],user_id=m['service_user_id']).data
  assert {g.group_id for g in memberships}=={m['group_id']};checks['isolated_membership']=True
  group_id=m['group_id']
  s3=boto3.client('s3',endpoint_url=creds['endpoint'],region_name=creds['region'],aws_access_key_id=creds['access_key_id'],aws_secret_access_key=creds['secret_access_key'],config=Config(signature_version='s3v4',s3={'addressing_style':'path'},connect_timeout=5,read_timeout=15,retries={'max_attempts':0}))
  def get(key):
   r=s3.get_object(Bucket=bucket,Key=key)
   try:return r['Body'].read()
   finally:r['Body'].close()
  start=time.monotonic();deadline=start+360
  while True:
   try:assert get(keys[0])==b'prefix test fixture';break
   except ClientError:
    if time.monotonic()>deadline:raise RuntimeError('PREFIX_GRANT_PROPAGATION_TIMEOUT')
    time.sleep(5)
  times['s3_grant_wait_seconds']=round(time.monotonic()-start,3);checks['inside_prefix_read']=True
  s3.put_object(Bucket=bucket,Key=keys[2],Body=b'owned prefix write');checks['inside_prefix_write']=True
  for operation in ('get','put'):
   try:
    if operation=='get':get(keys[1])
    else:s3.put_object(Bucket=bucket,Key=root+'sibling/write-denied.txt',Body=b'must not write')
   except ClientError as exc:
    assert exc.response['ResponseMetadata']['HTTPStatusCode'] in (403,404)
    checks['outside_prefix_'+operation+'_denied']=True
   else:raise RuntimeError('PREFIX_ISOLATION_FAILED')
  s3.list_objects_v2(Bucket=bucket,Prefix=prefix,MaxKeys=5);checks['prefix_filtered_listing']=True
  assert call('DELETE','/v1/leases/'+creds['lease_id']).status_code in (202,204)
  revoked(creds['lease_id']);checks['early_revoke_removes_membership_and_key']=True;s3.close();creds.clear()
  emit({'event':'isolation_passed','checks':checks,'timings':times})
  creds=issue(300);m=meta(creds['lease_id']);assert m['group_id']==group_id;checks['scope_group_reused']=True
  from datetime import datetime,timezone
  expires=datetime.fromisoformat(m['expires_at'].replace('Z','+00:00')).timestamp()
  while time.time()<expires:
   current=meta(creds['lease_id'])
   if not states or states[-1]!=current['status']:states.append(current['status']);emit({'event':'ttl_status','status':current['status']})
   if current['status'] in ('REVOKED','REVOKE_PENDING'):raise RuntimeError('PREMATURE_REVOKE')
   time.sleep(min(10,max(0,expires-time.time())))
  revoked(creds['lease_id']);checks['ttl_removes_membership_and_key']=True;times['ttl_cleanup_observed_lag_seconds']=round(time.time()-expires,3)
 finally:
  cleanup_errors=[]
  for lid in leases:
   try:
    r=call('DELETE','/v1/leases/'+lid);assert r.status_code in (202,204);revoked(lid)
   except Exception as exc:cleanup_errors.append({'lease_id':lid,'error':type(exc).__name__})
  if creds:creds.clear()
  emit({'event':'result','checks':checks,'timings':times,'passed':len(checks)==9 and all(checks.values()) and not cleanup_errors,'cleanup_errors':cleanup_errors,'fixture_bucket':bucket,'fixture_keys':keys,'scope_prefix':prefix,'leases':leases,'listing_confidentiality_enforced':False,'production_ready':False})
  if cleanup_errors:raise RuntimeError('TEST_CLEANUP_INCOMPLETE')

if __name__=='__main__':
 if '--remote' in sys.argv:
  try:remote()
  except Exception as exc:print(json.dumps({'event':'failure','error':type(exc).__name__,'code':str(exc) if isinstance(exc,RuntimeError) else ''}),flush=True);raise SystemExit(1)
 else:
  root=Path(__file__).resolve().parents[1]
  remote_args=['sudo','podman','exec','-i','lcm-api','python','-','--remote']
  if '--reuse-prefix' in sys.argv:
   reuse=sys.argv[sys.argv.index('--reuse-prefix')+1];assert re.fullmatch(r'prefix-smoke/[a-f0-9]{32}/allowed/',reuse)
   remote_args+=['--reuse-prefix',reuse]
  p=subprocess.Popen(['ssh','-F','.local/ssh-config','lcm-live',shlex.join(remote_args)],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  p.stdin.write(Path(__file__).read_text());p.stdin.close()
  for line in p.stdout:
   print(line,end='',flush=True)
   v=json.loads(line)
   if v.get('event')=='result':(root/'docs/prefix-live-evidence.json').write_text(json.dumps(v,indent=2)+'\n')
  raise SystemExit(p.wait())
