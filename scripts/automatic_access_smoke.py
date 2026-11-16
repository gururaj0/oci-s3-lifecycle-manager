"""Exercise automatic access and one existing lease; clean up only owned test artifacts.
Uses local operator-only OAuth bootstrap credentials; never records tokens or object bodies.
"""
import base64,json,time,uuid
from pathlib import Path
import requests,oci
ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/'.local/live-config.json').read_text());base=cfg['oauth']['public_url']
private=json.loads((ROOT/'.local/identity-domain-private.json').read_text())['Unity Demo Connector']
def token(scope):
 r=requests.post(cfg['oauth']['token_endpoint'],auth=(private['client_id'],private['client_secret']),data={'grant_type':'client_credentials','scope':scope},timeout=30)
 if r.status_code!=200:raise RuntimeError('Token request failed')
 return r.json()['access_token']
full=token(cfg['oauth']['scope']);read=token(cfg['audience']+'sales-read')
def call(method,path,access=full,**kwargs):
 return requests.request(method,base+path,headers={'Authorization':'Bearer '+access,**kwargs.pop('headers',{})},timeout=150,**kwargs)
def operation(body,access=full):return call('POST','/v1/bucket-operations',access,json=body)
checks={};key='automation/acceptance-'+str(uuid.uuid4())+'.txt';created=False;lease=None
bucket=cfg['profiles']['sales-read']['bucket'];evidence={'checks':checks,'production_ready':False}
try:
 before=call('GET','/v1/test-setup').json();before_leases=call('GET','/v1/leases').json()['leases']
 body={'bucket':bucket,'operation':'LIST','limit':5}
 assert requests.post(base+'/v1/bucket-operations',json=body,timeout=30).status_code==401
 checks['anonymous_denied']=True
 for _ in range(24):
  r=operation(body,read)
  if r.status_code==200:break
  assert r.status_code==403 and r.json()['error']['code']=='OCI_ACCESS_DENIED',r.status_code
  time.sleep(5)
 assert r.status_code==200,r.status_code
 assert r.json()['selected_profile']=='sales-read' and r.json()['lease_created'] is False
 checks['read_scope_lists_bucket']=True
 r=operation({'bucket':bucket,'operation':'GET','key':'quarterly/revenue.csv'},read)
 assert r.status_code==200 and base64.b64decode(r.json()['content_base64']).startswith(b'quarter,revenue\n')
 checks['read_fixture_returned']=True
 r=operation({'bucket':cfg['deployment_id']+'-foreign','operation':'LIST'},full)
 assert r.status_code==403 and r.json()['error']['code']=='BUCKET_OPERATION_FORBIDDEN'
 checks['foreign_bucket_denied_before_oci']=True
 put={'bucket':bucket,'operation':'PUT','key':key,'content_base64':base64.b64encode(b'LCM automatic access acceptance\n').decode()}
 r=operation(put,read);assert r.status_code==403 and r.json()['error']['code']=='BUCKET_OPERATION_FORBIDDEN'
 checks['read_scope_cannot_write']=True
 r=operation(put);assert r.status_code==200,r.status_code
 created=True;etag=r.json()['etag'];assert r.json()['selected_profile']=='sales-write'
 checks['write_scope_creates_object']=True
 assert operation(put).status_code==412
 checks['default_put_prevents_overwrite']=True
 r=operation({**put,'if_match':etag});assert r.status_code==200
 checks['conditional_overwrite_succeeds']=True
 assert operation({'bucket':bucket,'operation':'DELETE','key':key}).status_code==400
 checks['delete_operation_not_exposed']=True
 after=call('GET','/v1/test-setup').json();after_leases=call('GET','/v1/leases').json()['leases']
 assert {x['lease_id'] for x in before_leases}=={x['lease_id'] for x in after_leases}
 assert {x['user_id']:x['customer_secret_key_count'] for x in before['service_users']}=={x['user_id']:x['customer_secret_key_count'] for x in after['service_users']}
 checks['automatic_path_created_no_leases_or_customer_keys']=True
 # Regression: existing lease issuance still works and only this test lease is revoked.
 r=call('POST','/v1/leases',headers={'Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-read','ttl_seconds':300,'client_request_id':'automatic-access-regression'})
 if r.status_code not in (200,201):
  lease=r.json().get('error',{}).get('lease_id')
  raise RuntimeError('Existing lease issuance failed: HTTP '+str(r.status_code))
 lease=r.json()['lease_id'];assert call('GET','/v1/leases/'+lease).status_code==200
 checks['existing_lease_api_preserved']=True
finally:
 if lease:
  fresh=token(cfg['oauth']['scope']);r=call('DELETE','/v1/leases/'+lease,fresh);assert r.status_code in (202,204)
  for _ in range(30):
   if call('GET','/v1/leases/'+lease,fresh).json()['status']=='REVOKED':break
   time.sleep(2)
  else:raise AssertionError('Test lease cleanup pending')
  checks['only_regression_test_lease_revoked']=True
 if created:
  # The proxy deliberately cannot delete. Use operator authority for our exact disposable fixture.
  client=oci.object_storage.ObjectStorageClient(oci.config.from_file())
  client.delete_object(cfg['namespace'],bucket,key)
  checks['owned_test_object_removed']=True
 evidence['passed']=all(checks.values()) and len(checks)==13
 (ROOT/'docs/automatic-access-live-evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
print(json.dumps(evidence,indent=2))
