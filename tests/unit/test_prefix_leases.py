import uuid,time,json
import pytest
from shared.lifecycle import Lifecycle
from shared.prefix_access import statements

def enable(env):
 e,cloud,app,client=env;e.cfg.prefix_enabled=True;Lifecycle(e.cfg,e.db,cloud);return e,cloud,client

def issue(client,caller,**overrides):
 body={'bucket':'sales-demo','prefix':'finance/invoices/','operations':['GET','LIST'],'ttl_seconds':300,'delivery_mode':'immediate',**overrides}
 return client.post('/v1/leases',headers={**caller,'Idempotency-Key':str(uuid.uuid4())},json=body)

def test_dynamic_membership_lifecycle(env,caller):
 e,cloud,client=enable(env);r=issue(client,caller);assert r.status_code==201,r.text
 lease=r.json();assert lease['prefix']=='finance/invoices/' and lease['scope_profile']=='sales-read'
 row=e.db.lease(lease['lease_id']);grant=e.prefix_grant(row['id'])
 assert ':prefix:' in row['user_id'] and cloud.prefix_memberships[row['user_id']]==grant['group_id']
 assert 'finance/invoices/*' in statements(e.cfg,grant,grant['group_id'])[0]
 meta=client.get('/v1/leases/'+row['id'],headers=caller).json();assert meta['prefix']==lease['prefix']
 assert client.delete('/v1/leases/'+row['id'],headers=caller).status_code==202
 assert row['user_id'] not in cloud.prefix_memberships and not cloud.list_keys(row['user_id'])
 assert e.db.lease(row['id'])['state']=='REVOKED'

@pytest.mark.parametrize('prefix',['','/finance/','finance','finance//invoices/','finance/../hr/','finance/*/','finance/%2f/','finance/\'/','finance/./'])
def test_prefix_injection_denied(env,caller,prefix):
 _,_,client=enable(env);assert issue(client,caller,prefix=prefix).status_code==400

def test_foreign_bucket_and_operation_denied(env,caller):
 _,_,client=enable(env)
 assert issue(client,caller,bucket='foreign').status_code==403
 assert issue(client,caller,operations=['DELETE']).status_code==400

@pytest.mark.parametrize('failure',['prefix_provision','membership_after','vault'])
def test_prefix_failure_compensates_membership_and_key(env,caller,failure):
 e,cloud,client=enable(env);cloud.fail[failure]=1;r=issue(client,caller)
 lease_id=r.json().get('lease_id') or r.json()['error']['lease_id']
 assert e.db.lease(lease_id)['state']=='REVOKED'
 assert not cloud.prefix_memberships

def test_membership_remove_retry_holds_pool_slot(env,caller):
 e,cloud,client=enable(env);r=issue(client,caller);lid=r.json()['lease_id'];row=e.db.lease(lid)
 cloud.fail['membership_remove']=1;client.delete('/v1/leases/'+lid,headers=caller)
 assert e.db.lease(lid)['state']=='REVOKE_PENDING'
 with e.db.tx() as c:c.execute('UPDATE leases SET next_retry=0 WHERE id=?',(lid,))
 e.reconcile();assert e.db.lease(lid)['state']=='REVOKED' and not cloud.prefix_memberships

def test_ttl_removes_membership(env,caller):
 e,cloud,client=enable(env);lid=issue(client,caller).json()['lease_id']
 with e.db.tx() as c:c.execute('UPDATE leases SET expires_at=? WHERE id=?',(time.time()-1,lid))
 e.reconcile();assert e.db.lease(lid)['state']=='REVOKED' and not cloud.prefix_memberships

def test_disjoint_requests_use_isolated_users_and_scopes(env,caller):
 e,cloud,client=enable(env)
 a=issue(client,caller).json();b=issue(client,caller,prefix='hr/salaries/').json()
 assert e.db.lease(a['lease_id'])['user_id']!=e.db.lease(b['lease_id'])['user_id']
 assert e.prefix_grant(a['lease_id'])['group_id']!=e.prefix_grant(b['lease_id'])['group_id']

def test_s3_prefix_contents_denied_outside_and_revocation(env,caller):
 e,cloud,client=enable(env);lease=issue(client,caller,operations=['GET','LIST','PUT']).json()
 def probe(key,op):
  return client.post('/demo/probe',headers=caller,json={'lease_id':lease['lease_id'],'access_key_id':lease['access_key_id'],'secret_access_key':lease['secret_access_key'],'operation':op,'bucket':'sales-demo','object_name':key}).json()['status_code']
 assert probe('finance/invoices/test.txt','PUT')==200
 assert probe('finance/invoices/test.txt','GET')==200
 assert probe('finance/invoices-other/test.txt','PUT')==403
 assert probe('finance/budget.txt','PUT')==403
 assert probe('quarterly/revenue.csv','GET')==403
 client.delete('/v1/leases/'+lease['lease_id'],headers=caller)
 assert probe('finance/invoices/test.txt','GET')==403

def test_prefix_entitlement_root_and_idempotency(env,caller):
 e,cloud,client=enable(env)
 for p in e.cfg.profiles.values():p['allowed_prefixes']=['finance/']
 assert issue(client,caller,prefix='hr/').status_code==403
 body={'bucket':'sales-demo','prefix':'finance/invoices/','operations':['GET'],'ttl_seconds':300,'delivery_mode':'immediate'}
 headers={**caller,'Idempotency-Key':str(uuid.uuid4())}
 a=client.post('/v1/leases',headers=headers,json=body);assert a.status_code==201
 b=client.post('/v1/leases',headers=headers,json=body);assert b.status_code==200 and b.json()['access_key_id']==a.json()['access_key_id']
 assert client.post('/v1/leases',headers=headers,json={**body,'prefix':'finance/other/'}).status_code==409

def test_prefix_mode_rejects_legacy_broad_issuance(env,caller):
 _,_,client=enable(env)
 r=client.post('/v1/leases',headers={**caller,'Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-read','ttl_seconds':300})
 assert r.status_code==400 and r.json()['error']['code']=='PREFIX_REQUIRED'
