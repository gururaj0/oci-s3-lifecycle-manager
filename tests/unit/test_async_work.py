import io,json,uuid
from types import SimpleNamespace as N
import pytest
from shared.lifecycle import Lifecycle
from shared.prefix_cloud import PrefixCloud
from shared.prefix_access import scope_name,statements
from service.live_tests import run_test
from unittest.mock import Mock

OWNER={'issuer':'https://test.example','subject':'async-test'}

def create(env):
    e=env[0]
    _,p=e.create(OWNER,{'scope_profile':'sales-read','ttl_seconds':300},str(uuid.uuid4()),'test','https://s3.test')
    return e,p

def test_deferred_revoke_is_durable_until_reconciler_finishes(env):
    e,p=create(env);row=e.db.lease(p['lease_id'])
    assert e.revoke(row['id'],OWNER,defer_delete=True)==202
    assert e.db.lease(row['id'])['state']=='REVOKE_PENDING'
    assert e.cloud.list_keys(row['user_id'])
    e.reconcile()
    assert e.db.lease(row['id'])['state']=='REVOKED'
    assert not e.cloud.list_keys(row['user_id'])

def test_duplicate_setup_does_not_repeat_provider_work(env):
    e,p=create(env)
    e.cloud.fail['schedule']=1
    assert e.setup(p['lease_id'],1)
    assert e.cloud.fail['schedule']==1
    with e.db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM audit WHERE lease_id=? AND action='SCHEDULE_CONFIRMED'",(p['lease_id'],)).fetchone()[0]==1

def test_diagnostic_returns_before_deferred_audit_and_exposes_sdk_error(env,monkeypatch):
    from botocore.exceptions import ClientError
    e,p=create(env);client=Mock()
    client.get_object.side_effect=ClientError({'Error':{'Code':'AccessDenied'},'ResponseMetadata':{'HTTPStatusCode':403}},'GetObject')
    monkeypatch.setattr('service.live_tests.boto3.client',lambda *a,**k:client)
    work=[]
    r=run_test(e,OWNER,p['lease_id'],N(access_key_id=p['access_key_id'],secret_access_key='private-value',operation='GET'),defer_audit=lambda *args:work.append(args))
    assert r['s3_error_code']=='AccessDenied' and r['backend_test_seconds']>=r['s3_request_seconds']
    with e.db.connect() as c:assert c.execute("SELECT COUNT(*) FROM audit WHERE action='S3_TEST'").fetchone()[0]==0
    fn,*args=work.pop();assert 'private-value' not in args[-1];fn(*args)
    with e.db.connect() as c:assert c.execute("SELECT COUNT(*) FROM audit WHERE action='S3_TEST'").fetchone()[0]==1

@pytest.mark.parametrize('drift',[False,True])
def test_cached_scope_ids_are_revalidated_without_enumeration(env,monkeypatch,drift):
    e=env[0];e.cfg.prefix_enabled=True;Lifecycle(e.cfg,e.db,e.cloud)
    _,p=e.create(OWNER,{'scope_profile':'sales-read','bucket':'sales-demo','prefix':'Monthly/','operations':['GET','LIST'],'ttl_seconds':300},str(uuid.uuid4()),'test','https://s3.test')
    lid=p['lease_id'];grant=e.prefix_grant(lid);uid=e.db.lease(lid)['user_id']
    with e.db.tx() as c:c.execute("UPDATE leases SET state='PREPARING' WHERE id=?",(lid,))
    cloud=PrefixCloud();cloud.cfg=e.cfg;cloud.db=e.db;cloud._user=lambda u:None
    tags={'application':'oci-s3-lcm','deployment':e.cfg.deployment_id,'purpose':'prefix-access'}
    group=N(id=grant['group_id'],name=scope_name(e.cfg,grant),freeform_tags=tags)
    policy=N(id=grant['policy_id'],name=group.name,freeform_tags=tags,statements=['unexpected broader grant'] if drift else statements(e.cfg,grant,group.id))
    cloud.identity=Mock();cloud.identity.get_group.return_value=N(data=group);cloud.identity.get_policy.return_value=N(data=policy)
    cloud.identity.list_user_group_memberships.return_value=N(data=[N(id='membership',group_id=group.id)])
    monkeypatch.setattr('oci.pagination.list_call_get_all_results',lambda f,*a,**kw:f(*a,**kw))
    if drift:
        with pytest.raises(PermissionError,match='PREFIX_POLICY_DRIFT'):cloud.ensure_prefix_membership(lid,uid)
        cloud.identity.list_user_group_memberships.assert_not_called()
    else:
        assert cloud.ensure_prefix_membership(lid,uid)=={'group_reused':True,'policy_reused':True,'lookup':'ledger_ids'}
    cloud.identity.list_groups.assert_not_called();cloud.identity.list_policies.assert_not_called()
    cloud.identity.create_group.assert_not_called();cloud.identity.create_policy.assert_not_called()
