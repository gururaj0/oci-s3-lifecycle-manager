import uuid
from fastapi.testclient import TestClient
from service.app import create_app
from tests.unit.test_gateway_auth import cfg,headers,ISSUER


def test_progress_is_owner_bound_and_never_returns_credentials(env,caller):
    e,_,app,client=env;request_id=str(uuid.uuid4()).upper()
    r=client.post('/v1/leases',headers={**caller,'Idempotency-Key':request_id},json={'scope_profile':'sales-read','ttl_seconds':300})
    credentials=r.json();path='/v1/lease-requests/'+request_id
    result=client.get(path,headers=caller)
    assert result.status_code==200 and result.json()['phase']=='ACTIVE'
    assert result.json()['lease']['lease_id']==credentials['lease_id']
    assert credentials['secret_access_key'] not in result.text and credentials['access_key_id'] not in result.text
    assert client.get(path,headers={'Authorization':'Bearer '+app.state.auth.mint_demo('demo:other')}).status_code==404
    assert client.get('/v1/lease-requests/'+str(uuid.uuid4()),headers=caller).status_code==404
    for state,schedule,expected in [('PREPARING',None,'PREPARING'),('SCHEDULE_PENDING',None,'CONFIRMING_SCHEDULE'),('SCHEDULE_PENDING','scheduled','PREPARING_REPLAY'),('REVOKE_PENDING',None,'REVOKE_PENDING')]:
        with e.db.tx() as c:c.execute('UPDATE leases SET state=?,schedule_id=? WHERE id=?',(state,schedule,credentials['lease_id']))
        result=client.get(path,headers=caller)
        assert result.json()['phase']==expected
        assert credentials['secret_access_key'] not in result.text


def test_progress_enforces_profile_scope_after_gateway_validation(env):
    e=env[0];request_id=str(uuid.uuid4())
    e.create({'issuer':ISSUER,'subject':'connector'},{'scope_profile':'sales-write','ttl_seconds':300},request_id,'progress-test','https://s3.test')
    e.cfg=cfg()
    with TestClient(create_app(e,worker=False),client=('10.73.0.8',1234)) as client:
        assert client.get('/v1/lease-requests/'+request_id,headers=headers(scope='sales-read')).status_code==403
        assert client.get('/v1/lease-requests/'+request_id,headers=headers(scope='sales-write')).status_code==200
