import io
import json
import uuid
from types import SimpleNamespace
import pytest
from botocore.exceptions import ClientError
from service.live_tests import run_test
from shared.errors import Problem


def lease(env, subject='demo:alice'):
    e=env[0]
    owner={'issuer':e.cfg.issuer,'subject':subject}
    _,payload=e.create(owner,{'scope_profile':'sales-read','ttl_seconds':300},str(uuid.uuid4()),'test','https://example.com')
    return owner,payload


def test_diagnostic_binds_owner_and_key_before_any_s3_request(env,monkeypatch):
    owner,payload=lease(env)
    monkeypatch.setattr('service.live_tests.boto3.client',lambda *a,**k:pytest.fail('Unauthorized SDK request'))
    body=SimpleNamespace(access_key_id=payload['access_key_id'],secret_access_key='secret',operation='GET')
    with pytest.raises(Problem) as error:
        run_test(env[0],dict(owner,subject='demo:other'),payload['lease_id'],body)
    assert error.value.status==403
    body.access_key_id='another-key'
    with pytest.raises(Problem) as error:
        run_test(env[0],owner,payload['lease_id'],body)
    assert error.value.status==403


@pytest.mark.parametrize('operation,code,status,observed,matched',[
    ('PUT','AccessDenied',403,'DENY',True),
    ('GET','AccessDenied',403,'DENY',False),
    ('PUT','NoSuchKey',404,'ERROR',False),
])
def test_diagnostic_reports_actual_errors_without_false_denial_proof(env,monkeypatch,operation,code,status,observed,matched):
    owner,payload=lease(env)
    class Client:
        def get_object(self,**kwargs):raise ClientError({'Error':{'Code':code},'ResponseMetadata':{'HTTPStatusCode':status}},'GetObject')
        put_object=get_object
        def close(self):pass
    monkeypatch.setattr('service.live_tests.boto3.client',lambda *a,**k:Client())
    result=run_test(env[0],owner,payload['lease_id'],SimpleNamespace(access_key_id=payload['access_key_id'],secret_access_key='sensitive-test-value',operation=operation))
    assert result['observed_access']==observed and result['matches_expectation']==matched
    assert 'sensitive-test-value' not in json.dumps(result)


def test_diagnostic_is_fixed_fixture_and_returns_no_object_content_or_secret(env,monkeypatch):
    owner,payload=lease(env)
    class Client:
        def get_object(self,**kwargs):
            assert kwargs=={'Bucket':'sales-demo','Key':'quarterly/revenue.csv'}
            return {'Body':io.BytesIO(b'quarter,revenue\nQ1,124000\n'),'ResponseMetadata':{'HTTPStatusCode':200}}
        def close(self):pass
    monkeypatch.setattr('service.live_tests.boto3.client',lambda *a,**k:Client())
    result=run_test(env[0],owner,payload['lease_id'],SimpleNamespace(access_key_id=payload['access_key_id'],secret_access_key='sensitive-test-value',operation='GET'))
    assert result['matches_expectation'] and result['fixture_verified']
    assert '124000' not in json.dumps(result)
    with env[0].db.connect() as c:
        audit=c.execute("SELECT detail FROM audit WHERE action='S3_TEST'").fetchone()[0]
    assert 'sensitive-test-value' not in audit and payload['access_key_id'] not in audit


def test_live_metrics_and_audit_are_owner_bound(env,caller):
    lease(env,'demo:alice')
    lease(env,'demo:other')
    client=env[3]
    metrics=client.get('/v1/metrics',headers=caller).json()
    assert metrics['states']['ACTIVE']==1
    events=client.get('/v1/audit',headers=caller).json()['events']
    with env[0].db.connect() as c:
        other=c.execute('SELECT id FROM leases WHERE subject=?',('demo:other',)).fetchone()[0]
    assert events and all(e['lease_id']!=other for e in events)
    assert client.get('/v1/metrics').status_code==401
    assert client.get('/v1/audit').status_code==401


def test_monthly_diagnostic_lists_and_reads_only_requested_prefix(env,monkeypatch):
    owner,payload=lease(env);e=env[0];e.cfg.prefix_enabled=True
    monkeypatch.setattr(e,'prefix_grant',lambda _: {'bucket':'sales-demo','prefix':'Monthly/','operations':'["GET","LIST"]'})
    calls=[]
    class Client:
        def list_objects_v2(self,**kw):
            calls.append(kw);return {'Contents':[{'Key':'Monthly/report.csv'}],'ResponseMetadata':{'HTTPStatusCode':200}}
        def get_object(self,**kw):
            calls.append(kw);return {'Body':io.BytesIO(b'customer contents'),'ResponseMetadata':{'HTTPStatusCode':200}}
        def close(self):pass
    monkeypatch.setattr('service.live_tests.boto3.client',lambda *a,**k:Client())
    body=SimpleNamespace(access_key_id=payload['access_key_id'],secret_access_key='secret',operation='LIST')
    result=run_test(e,owner,payload['lease_id'],body)
    assert result['object_names']==['Monthly/report.csv'] and calls[-1]['Prefix']=='Monthly/'
    body.operation='GET';body.object_name='Monthly/report.csv'
    result=run_test(e,owner,payload['lease_id'],body)
    assert calls[-1]['Key']=='Monthly/report.csv' and result['matches_expectation']
    assert 'customer contents' not in json.dumps(result)


@pytest.mark.parametrize('key,code',[(None,'OBJECT_NAME_REQUIRED'),('quarterly/revenue.csv','OBJECT_OUTSIDE_LEASE_PREFIX')])
def test_prefix_diagnostic_rejects_missing_or_foreign_read_key(env,monkeypatch,key,code):
    owner,payload=lease(env);e=env[0];e.cfg.prefix_enabled=True
    monkeypatch.setattr(e,'prefix_grant',lambda _: {'bucket':'sales-demo','prefix':'Monthly/','operations':'["GET","LIST"]'})
    monkeypatch.setattr('service.live_tests.boto3.client',lambda *a,**k:pytest.fail('Unexpected S3 request'))
    body=SimpleNamespace(access_key_id=payload['access_key_id'],secret_access_key='secret',operation='GET',object_name=key)
    with pytest.raises(Problem) as error:run_test(e,owner,payload['lease_id'],body)
    assert error.value.code==code
