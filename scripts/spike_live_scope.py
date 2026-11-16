"""Real S3/IAM spike on private OCI VM using instance principal. Never prints credentials."""
import json
from pathlib import Path
import time
import uuid
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
import requests
import oci

cfg=json.loads(Path('/etc/lcm-live/scope.json').read_text())
signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
iam=oci.identity.IdentityClient({'region':cfg['region']},signer=signer)
endpoint='https://'+cfg['namespace']+'.compat.objectstorage.'+cfg['region']+'.oci.customer-oci.com'
results={'mode':'live-oci','credentials_printed':False,'profiles':{}}
def denied(call):
 try:call()
 except ClientError as exc:
  assert exc.response['ResponseMetadata']['HTTPStatusCode']in (403,404),exc.response['Error']['Code']
  return True
 raise AssertionError('Scope exceeded configured IAM profile')
for profile,user in cfg['users'].items():
 existing=iam.list_customer_secret_keys(user).data
 assert not existing,'Managed user has unexpected outstanding keys; do not delete them to make room'
 display=cfg['deployment_id']+'/scope-spike-'+str(uuid.uuid4())
 key=None
 try:
  for permission_attempt in range(30):
   try:
    key=iam.create_customer_secret_key(oci.identity.models.CreateCustomerSecretKeyDetails(display_name=display),user,retry_strategy=oci.retry.NoneRetryStrategy()).data;break
   except oci.exceptions.ServiceError as exc:
    if exc.status not in (403,404) or permission_attempt==29:raise
    time.sleep(5)
  s3=boto3.client('s3',endpoint_url=endpoint,region_name=cfg['region'],aws_access_key_id=key.id,aws_secret_access_key=key.key,
     config=Config(signature_version='s3v4',s3={'addressing_style':'path'},retries={'max_attempts':1}))
  # IAM/S3 propagation can lag a new policy/user. Retry positive read before negative checks.
  for attempt in range(120):
   try:s3.get_object(Bucket=cfg['bucket'],Key='quarterly/revenue.csv')['Body'].read();break
   except ClientError:
    if attempt==119:raise
    time.sleep(5)
  s3.list_objects_v2(Bucket=cfg['bucket'])
  denied(lambda:s3.get_object(Bucket=cfg['foreign_bucket'],Key='negative-scope.txt'))
  denied(lambda:s3.list_objects_v2(Bucket=cfg['foreign_bucket']))
  denied(lambda:s3.delete_object(Bucket=cfg['bucket'],Key='quarterly/revenue.csv'))
  write='write' in profile
  if write:
   s3.put_object(Bucket=cfg['bucket'],Key='scope-write.txt',Body=b'live scope fixture')
   s3.put_object(Bucket=cfg['bucket'],Key='scope-write.txt',Body=b'overwrite tested')
   upload=s3.create_multipart_upload(Bucket=cfg['bucket'],Key='scope-multipart.txt')['UploadId']
   part=s3.upload_part(Bucket=cfg['bucket'],Key='scope-multipart.txt',UploadId=upload,PartNumber=1,Body=b'live multipart')
   s3.complete_multipart_upload(Bucket=cfg['bucket'],Key='scope-multipart.txt',UploadId=upload,MultipartUpload={'Parts':[{'ETag':part['ETag'],'PartNumber':1}]})
   denied(lambda:s3.put_object(Bucket=cfg['foreign_bucket'],Key='forbidden.txt',Body=b'no'))
  else:denied(lambda:s3.put_object(Bucket=cfg['bucket'],Key='forbidden.txt',Body=b'no'))
  raw=AWSRequest(method='GET',url='https://identity.'+cfg['region']+'.oci.oraclecloud.com/20160918/users/'+user)
  SigV4Auth(Credentials(key.id,key.key),'iam',cfg['region']).add_auth(raw)
  r=requests.get(raw.url,headers=dict(raw.headers),timeout=20)
  assert r.status_code in (401,403),'S3 key unexpectedly authenticated to native IAM'
  results['profiles'][profile]={'list':True,'get':True,'write':write,'overwrite':write,'multipart':write,'delete_denied':True,'foreign_bucket_denied':True,'native_iam_denied':True}
 finally:
  for created in iam.list_customer_secret_keys(user).data:
   if created.display_name==display:iam.delete_customer_secret_key(user,created.id)
  assert not iam.list_customer_secret_keys(user).data
# General user updates must remain denied even for managed users.
try:iam.update_user(next(iter(cfg['users'].values())),oci.identity.models.UpdateUserDetails(description='Unauthorized update must fail'))
except oci.exceptions.ServiceError as exc:
 assert exc.status in (403,404);results['general_user_update_denied']=True
else:raise AssertionError('Runtime was granted general user modification')
# Exercise the real IAM boundary without the adapter's local managed-user guard.
foreign_created=None
try:
 try:
  foreign_created=iam.create_customer_secret_key(oci.identity.models.CreateCustomerSecretKeyDetails(display_name=cfg['deployment_id']+'/foreign-denial-test'),cfg['sentinel_id'],retry_strategy=oci.retry.NoneRetryStrategy()).data
 except oci.exceptions.ServiceError as exc:
  assert exc.status in (403,404);results['foreign_user_management_denied']=True
 else:raise AssertionError('Runtime IAM can create a key for an unmanaged user')
finally:
 if foreign_created:iam.delete_customer_secret_key(cfg['sentinel_id'],foreign_created.id)
print(json.dumps(results,indent=2))
