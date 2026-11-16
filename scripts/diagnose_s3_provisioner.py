"""Control experiment: same dedicated user/bucket, local provisioner vs VM principal.
Credentials stay in this process, never in files/output. Only the owned diagnostic key is removed.
"""
import json
import time
import uuid
from pathlib import Path
import oci
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from deploy_oci import ROOT
c=oci.config.from_file();iam=oci.identity.IdentityClient(c);os=oci.object_storage.ObjectStorageClient(c)
scope=json.loads((ROOT/'.local/live-scope.json').read_text());user=scope['users']['sales-read-2']
assert os.get_object(scope['namespace'],scope['bucket'],'quarterly/revenue.csv').data.content.startswith(b'quarter,revenue')
name=scope['deployment_id']+'/provisioner-control-'+str(uuid.uuid4())
key=None
try:
 assert not iam.list_customer_secret_keys(user).data
 key=iam.create_customer_secret_key(oci.identity.models.CreateCustomerSecretKeyDetails(display_name=name),user,retry_strategy=oci.retry.NoneRetryStrategy()).data
 start=time.monotonic()
 for elapsed in [0,90,180,300]:
  time.sleep(max(0,start+elapsed-time.monotonic()))
  for domain in ['oci.customer-oci.com','oraclecloud.com']:
   for signing_region in [scope['region'],'us-east-1']:
    client=boto3.client('s3',endpoint_url='https://'+scope['namespace']+'.compat.objectstorage.'+scope['region']+'.'+domain,
      region_name=signing_region,aws_access_key_id=key.id,aws_secret_access_key=key.key,
      config=Config(signature_version='s3v4',s3={'addressing_style':'path'},retries={'max_attempts':0},connect_timeout=10,read_timeout=15,
                    request_checksum_calculation='when_required',response_checksum_validation='when_required'))
    try:
     r=client.get_object(Bucket=scope['bucket'],Key='quarterly/revenue.csv')
     good=r['Body'].read().startswith(b'quarter,revenue')
     print(json.dumps({'elapsed':elapsed,'actor':'local-provisioner','domain':domain,'region':signing_region,'http_status':r['ResponseMetadata']['HTTPStatusCode'],'fixture_verified':good}),flush=True)
    except ClientError as exc:print(json.dumps({'elapsed':elapsed,'actor':'local-provisioner','domain':domain,'region':signing_region,'code':exc.response['Error']['Code'],'http_status':exc.response['ResponseMetadata']['HTTPStatusCode']}),flush=True)
finally:
 for owned in iam.list_customer_secret_keys(user).data:
  if owned.display_name==name:iam.delete_customer_secret_key(user,owned.id)
