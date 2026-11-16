import time
import uuid
import pytest
from fastapi import BackgroundTasks
from shared.errors import Problem

OWNER={'issuer':'test','subject':'connector'}
BODY={'scope_profile':'sales-read','ttl_seconds':300,'delivery_mode':'immediate'}

def early(e):
    return e.create(OWNER,BODY,str(uuid.uuid4()),'test','https://s3.test',defer_setup=True)[1]

def test_early_delivery_precedes_schedule_and_vault(env,monkeypatch):
    e,cloud,*_=env
    def forbidden(*args):raise AssertionError('SLOW_SETUP_ON_RESPONSE_PATH')
    with monkeypatch.context() as patch:
        patch.setattr(e,'setup',forbidden);patch.setattr(cloud,'store_response',forbidden)
        payload=early(e)
    row=e.db.lease(payload['lease_id'])
    assert payload['status']=='SCHEDULE_PENDING' and payload['secret_access_key']
    assert row['delivered_at'] and not row['schedule_id'] and not row['replay_ref']
    with e.db.connect() as c:assert c.execute('SELECT operation FROM outbox WHERE lease_id=?',(row['id'],)).fetchone()[0]=='SETUP'
    e.finish_in_background(dict(payload),'test')
    row=e.db.lease(row['id'])
    assert row['state']=='ACTIVE' and row['schedule_id'] and row['replay_ref']
    assert e.replay(row,row['request_hash'])[1]['secret_access_key']==payload['secret_access_key']

@pytest.mark.parametrize('fault',['schedule','vault'])
def test_background_failure_revokes_delivered_key(env,fault):
    e,cloud,*_=env;payload=early(e);row=e.db.lease(payload['lease_id'])
    cloud.fail[fault]=1;e.finish_in_background(payload,'test')
    assert payload=={} and e.db.lease(row['id'])['state']=='REVOKED'
    assert not cloud.list_keys(row['user_id'])

def test_process_loss_after_delivery_reconciles_to_revocation(env):
    e,cloud,*_=env;payload=early(e);row=e.db.lease(payload['lease_id'])
    with e.db.tx() as c:c.execute('UPDATE leases SET created_at=? WHERE id=?',(time.time()-61,row['id']))
    e.reconcile()
    assert e.db.lease(row['id'])['state']=='REVOKED' and not cloud.list_keys(row['user_id'])

def test_pending_retry_never_creates_second_key(env):
    e,cloud,*_=env;idem=str(uuid.uuid4())
    first=e.create(OWNER,BODY,idem,'test','endpoint',defer_setup=True)[1]
    with pytest.raises(Problem) as err:e.create(OWNER,BODY,idem,'test','endpoint',defer_setup=True)
    assert err.value.code=='ISSUANCE_PENDING'
    assert len(cloud.list_keys(e.db.lease(first['lease_id'])['user_id']))==1

def test_revoke_before_finalization_cannot_resurrect_key(env):
    e,cloud,*_=env;payload=early(e);row=e.db.lease(payload['lease_id'])
    e.revoke(row['id'],OWNER);e.finish_in_background(payload,'test')
    row=e.db.lease(row['id'])
    assert row['state']=='REVOKED' and not row['replay_ref'] and not cloud.list_keys(row['user_id'])

def test_revoke_during_vault_storage_removes_late_replay(env,monkeypatch):
    e,cloud,*_=env;payload=early(e);lease_id=payload['lease_id'];store=cloud.store_response;refs=[]
    def raced_store(value,until):
        ref=store(value,until);refs.append(ref);e.revoke(lease_id,OWNER);return ref
    monkeypatch.setattr(cloud,'store_response',raced_store)
    e.finish_in_background(payload,'test')
    assert e.db.lease(lease_id)['state']=='REVOKED'
    assert not cloud.get_response(refs[0])

def test_api_schedules_completion_only_for_new_immediate_request(env,caller,monkeypatch):
    e,cloud,app,client=env;tasks=[]
    monkeypatch.setattr(BackgroundTasks,'add_task',lambda self,func,*args,**kwargs:tasks.append((func,args)))
    idem=str(uuid.uuid4());headers={**caller,'Idempotency-Key':idem}
    r=client.post('/v1/leases',headers=headers,json=BODY)
    assert r.status_code==201 and r.json()['status']=='SCHEDULE_PENDING' and len(tasks)==1
    assert client.post('/v1/leases',headers=headers,json=BODY).status_code==409 and len(tasks)==1
    func,args=tasks.pop();func(*args)
    replay=client.post('/v1/leases',headers=headers,json=BODY)
    assert replay.status_code==200 and replay.json()['status']=='ACTIVE' and not tasks

def test_http_response_is_sent_before_background_work_finishes(env,caller,monkeypatch):
    import asyncio,json,threading
    e,cloud,app,_=env;entered=threading.Event();release=threading.Event()
    original=e.finish_in_background
    def blocked(payload,correlation):
        entered.set()
        if not release.wait(5):raise AssertionError('BACKGROUND_BLOCKED_RESPONSE')
        original(payload,correlation)
    monkeypatch.setattr(e,'finish_in_background',blocked)
    async def run():
        emitted=asyncio.Event();messages=[];received=False
        async def receive():
            nonlocal received
            if not received:
                received=True;return {'type':'http.request','body':json.dumps(BODY).encode(),'more_body':False}
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
            if message['type']=='http.response.body' and not message.get('more_body',False):emitted.set()
        headers=[(b'authorization',caller['Authorization'].encode()),(b'idempotency-key',str(uuid.uuid4()).encode()),(b'content-type',b'application/json')]
        scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1','method':'POST','scheme':'https','path':'/v1/leases','raw_path':b'/v1/leases','query_string':b'','headers':headers,'client':('127.0.0.1',1234),'server':('test',443),'root_path':''}
        task=asyncio.create_task(app(scope,receive,send))
        try:
            await asyncio.wait_for(emitted.wait(),2)
            assert await asyncio.to_thread(entered.wait,2)
            payload=json.loads(b''.join(m.get('body',b'') for m in messages))
            assert payload['secret_access_key'] and payload['status']=='SCHEDULE_PENDING'
            assert e.db.lease(payload['lease_id'])['schedule_id'] is None
        finally:
            release.set();await task
    asyncio.run(run())
