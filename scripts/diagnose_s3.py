"""Redacted live-S3 diagnostics on one dedicated service user; always remove the owned key."""
import json
import re
import time
from datetime import datetime,timezone
from pathlib import Path
import uuid
import requests
import oci
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
cfg=json.loads(Path('/etc/lcm-live/scope.json').read_text());user=next(iter(cfg['users'].values()))
iam=oci.identity.IdentityClient({'region':cfg['region']},signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner())
name=cfg['deployment_id']+'/diagnostic-'+str(uuid.uuid4());key=None
try:
 key=iam.create_customer_secret_key(oci.identity.models.CreateCustomerSecretKeyDetails(display_name=name),user,retry_strategy=oci.retry.NoneRetryStrategy()).data
 print(json.dumps({'credential_metadata':{'id_length':len(key.id),'secret_length':len(key.key),'id_is_hex':bool(re.fullmatch('[0-9a-fA-F]+',key.id)),'state':key.lifecycle_state,'expires':str(key.time_expires),'inactive_status':key.inactive_status,'created':str(key.time_created),'user_matches':key.user_id==user},'clock_utc':str(datetime.now(timezone.utc))}),flush=True)
 started=time.monotonic()
 for elapsed in [0,120,300,480]:
  time.sleep(max(0,started+elapsed-time.monotonic()))
  for domain in ['oci.customer-oci.com','oraclecloud.com']:
   for region in [cfg['region'],'us-east-1']:
    url='https://'+cfg['namespace']+'.compat.objectstorage.'+cfg['region']+'.'+domain+'/'+cfg['bucket']+'/quarterly/revenue.csv'
    request=AWSRequest(method='GET',url=url,data=b'')
    S3SigV4Auth(Credentials(key.id,key.key),'s3',region).add_auth(request)
    try:
     response=requests.get(url,headers=dict(request.headers),timeout=15)
     code=re.search(r'<Code>([^<]+)',response.text)
     print(json.dumps({'elapsed_seconds':elapsed,'domain':domain,'signature_region':region,'http_status':response.status_code,'s3_code':code[1] if code else None,'server_date':response.headers.get('Date'),'body_matches_fixture':response.content.startswith(b'quarter,revenue'),'redirects':len(response.history)}),flush=True)
    except requests.RequestException as exc:print(json.dumps({'domain':domain,'error':type(exc).__name__}),flush=True)
finally:
 for k in iam.list_customer_secret_keys(user).data:
  if k.display_name==name:iam.delete_customer_secret_key(user,k.id)
