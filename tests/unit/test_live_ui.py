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
