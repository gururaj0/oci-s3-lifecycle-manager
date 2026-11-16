"""Investigate the ambiguous foreign-fixture 404; preserve every observation."""
import json,subprocess,sys,time,uuid
from pathlib import Path
import boto3,oci,requests
from botocore.config import Config
from botocore.exceptions import ClientError
ROOT=Path(__file__).resolve().parents[1];cfg=json.loads((ROOT/'.local/live-config.json').read_text());url=cfg['oauth']['public_url']
token=subprocess.run([sys.executable,'scripts/identity_demo_token.py'],capture_output=True,text=True,check=True).stdout.strip()
api=requests.Session();api.headers['Authorization']='Bearer '+token
storage=oci.object_storage.ObjectStorageClient(oci.config.from_file())
assert storage.head_object(cfg['namespace'],cfg['deployment_id']+'-foreign','negative-scope.txt').status==200
out={'fixture_independently_verified':True,'initial_ui_result':{'status':404,'classification':'ERROR'},'observations':[]}
lease=None
try:
 r=api.post(url+'/v1/leases',headers={'Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-write','ttl_seconds':900},timeout=150);r.raise_for_status();lease=r.json()
 client=boto3.client('s3',endpoint_url=lease['endpoint'],region_name=cfg['region'],aws_access_key_id=lease['access_key_id'],aws_secret_access_key=lease['secret_access_key'],config=Config(signature_version='s3v4',s3={'addressing_style':'path'},retries={'max_attempts':0},connect_timeout=5,read_timeout=15))
 deadline=time.monotonic()+360
 while True:
  try:
   response=client.get_object(Bucket=cfg['profiles']['sales-write']['bucket'],Key='quarterly/revenue.csv');response['Body'].close();break
  except ClientError:
   assert time.monotonic()<deadline,'Positive control failed'
   time.sleep(10)
 out['positive_read_control']=True
 for _ in range(3):
  try:
   response=client.get_object(Bucket=cfg['deployment_id']+'-foreign',Key='negative-scope.txt');response['Body'].close()
   out['observations'].append({'status':response['ResponseMetadata']['HTTPStatusCode'],'code':'UNEXPECTED_ACCESS'})
  except ClientError as error:
   out['observations'].append({'status':error.response['ResponseMetadata']['HTTPStatusCode'],'code':error.response.get('Error',{}).get('Code','')})
  time.sleep(2)
 print(json.dumps(out),flush=True)
finally:
 if lease:
  api.delete(url+'/v1/leases/'+lease['lease_id'],timeout=150).raise_for_status()
  for _ in range(40):
   if api.get(url+'/v1/leases/'+lease['lease_id'],timeout=30).json()['status']=='REVOKED':break
   time.sleep(3)
  else:raise AssertionError('Probe lease deletion unconfirmed')
  out['only_probe_lease_revoked']=True
 (ROOT/'docs/gateway-foreign-followup.json').write_text(json.dumps(out,indent=2)+'\n')
