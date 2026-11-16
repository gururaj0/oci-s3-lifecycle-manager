import time
import uuid
from tests.contract.test_api import issue

def test_no_early_or_stale_generation_deletion(env,caller):
    e,cloud,_,client=env
    body=issue(client,caller).json();r=e.db.lease(body['lease_id'])
    assert e.delete(r['id'],r['generation']) is False
    assert e.delete(r['id'],r['generation']+1) is False
    assert cloud.list_keys(r['user_id'])
    with e.db.tx() as c:
        c.execute('UPDATE leases SET expires_at=? WHERE id=?',(time.time()-1,r['id']))
    e.reconcile()
    assert e.db.lease(r['id'])['state']=='REVOKED'
    assert not cloud.list_keys(r['user_id'])


def test_create_lost_response_revokes_by_name(env,caller):
    e,cloud,_,client=env
    cloud.fail['create_after']=1
    response=issue(client,caller)
    assert response.status_code==503
    r=e.db.lease(response.json()['error']['lease_id'])
    assert r['state']=='REVOKED' and r['key_id']
    assert cloud.list_keys(r['user_id'])==[]


def test_vault_and_schedule_compensation(env,caller):
    e,cloud,_,client=env
    for stage in ['vault','schedule']:
        cloud.fail[stage]=1
        response=issue(client,caller)
        assert response.status_code==503
        r=e.db.lease(response.json()['error']['lease_id'])
        assert r['state']=='REVOKED' and cloud.list_keys(r['user_id'])==[]


def test_delete_retry_foreign_key_preserved(env,caller):
    e,cloud,_,client=env
    body=issue(client,caller).json();r=e.db.lease(body['lease_id'])
    foreign=cloud.create_key(r['user_id'],'someone-elses-key')
    cloud.fail['delete']=1
    e.revoke(r['id'],{'issuer':r['issuer'],'subject':r['subject']})
    assert e.db.lease(r['id'])['state']=='REVOKE_PENDING'
    with e.db.tx() as c:
        c.execute('UPDATE leases SET next_retry=0 WHERE id=?',(r['id'],))
    e.reconcile()
    assert e.db.lease(r['id'])['state']=='REVOKED'
    assert [k['id'] for k in cloud.list_keys(r['user_id'])]==[foreign['id']]
    with e.db.connect() as c:
        assert c.execute('SELECT quarantined FROM users WHERE id=?',(r['user_id'],)).fetchone()[0]==1


def test_events_dropped_duplicate_and_out_of_order(env,caller):
    e,cloud,_,client=env
    body=issue(client,caller).json();r=e.db.lease(body['lease_id'])
    event={'eventID':str(uuid.uuid4()),'eventType':'com.oraclecloud.identityControlPlane.CreateCustomerSecretKey',
           'data':{'additionalDetails':{'userId':r['user_id']}}}
    assert e.event(event)=='CONFIRMED'
    assert e.event(event)=='DUPLICATE'
    with e.db.tx() as c:
        c.execute('DELETE FROM schedules WHERE lease_id=?',(r['id'],))
    e.reconcile()
    with e.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM schedules WHERE lease_id=?',(r['id'],)).fetchone()[0]==1
    before=e.db.lease(r['id'])['expires_at']
    event['eventID']=str(uuid.uuid4())
    assert e.event(event)=='CONFIRMED'
    assert e.db.lease(r['id'])['expires_at']==before
    event['data']['additionalDetails']['userId']='foreign-user'
    event['eventID']=str(uuid.uuid4())
    assert e.event(event)=='IGNORED'


def test_crash_before_persist_and_process_restart(env,caller):
    from shared.lifecycle import Lifecycle
    from shared.ledger import Ledger
    from shared.mock import MockCloud
    e,cloud,_,client=env
    body=issue(client,caller).json();r=e.db.lease(body['lease_id'])
    # Preserve the separate provider key, simulate crash before key-ID persistence.
    with e.db.tx() as c:
        c.execute("UPDATE leases SET state='PREPARING',key_id=NULL,created_at=?,expires_at=NULL WHERE id=?",(time.time()-31,r['id']))
    restarted=Lifecycle(e.cfg,Ledger(e.cfg.state_dir/'ledger.db'),MockCloud(e.cfg))
    restarted.reconcile()
    assert restarted.db.lease(r['id'])['state']=='REVOKED'
    assert not restarted.cloud.list_keys(r['user_id'])


def test_pause_overdue_and_outage_recovery(env,caller):
    e,cloud,_,client=env
    body=issue(client,caller).json();r=e.db.lease(body['lease_id'])
    with e.db.tx() as c:
        c.execute('UPDATE leases SET expires_at=? WHERE id=?',(time.time()-121,r['id']))
    assert issue(client,caller).status_code==503
    cloud.fail['delete']=1
    e.reconcile()
    assert e.db.lease(r['id'])['state']=='REVOKE_PENDING'
    assert not e.healthy()
    with e.db.tx() as c:
        c.execute('UPDATE leases SET next_retry=0 WHERE id=?',(r['id'],))
    e.reconcile()
    assert e.healthy()
    e.pause()
    assert issue(client,caller).status_code==503
    e.pause(False)
    assert issue(client,caller).status_code==201


def test_vault_cleanup_failure_does_not_block_overdue_key_deletion(env,caller,monkeypatch):
    e,cloud,_,client=env
    body=issue(client,caller).json();r=e.db.lease(body['lease_id'])
    with e.db.tx() as c:
        c.execute('UPDATE leases SET expires_at=? WHERE id=?',(time.time()-1,r['id']))
    real_purge=cloud.purge
    def unavailable():raise TimeoutError('vault unavailable')
    monkeypatch.setattr(cloud,'purge',unavailable)
    e.reconcile()
    assert not cloud.list_keys(r['user_id'])
    assert e.db.lease(r['id'])['state']=='REVOKED'
    assert not e.healthy()
    assert issue(client,caller).status_code==503
    monkeypatch.setattr(cloud,'purge',real_purge)
    e.reconcile()
    assert e.healthy()


def test_reconcile_identity_calls_do_not_hold_writer_lock(env,monkeypatch):
    from contextlib import contextmanager
    e,cloud,_,_=env
    original_tx=e.db.tx;original_list=cloud.list_keys
    depth=0
    @contextmanager
    def transaction():
        nonlocal depth
        with original_tx() as connection:
            depth+=1
            try:yield connection
            finally:depth-=1
    def list_keys(user):
        assert depth==0,'Remote identity inspection held the writer lock'
        return original_list(user)
    monkeypatch.setattr(e.db,'tx',transaction)
    monkeypatch.setattr(cloud,'list_keys',list_keys)
    e.reconcile()
    assert e.healthy()


def test_live_identity_drift_quarantines_and_revokes_owned_lease(env,caller,monkeypatch):
    e,cloud,_,client=env
    lease=issue(client,caller).json();record=e.db.lease(lease['lease_id'])
    e.cfg.mode='oci'
    def validate(user):
        if user==record['user_id']:raise PermissionError('IDENTITY_MEMBERSHIP_DRIFT')
    monkeypatch.setattr(cloud,'validate_user',validate,raising=False)
    monkeypatch.setattr(cloud,'publish_metrics',lambda _:None,raising=False)
    monkeypatch.setattr(cloud,'remove_schedule',lambda _:None,raising=False)
    e.reconcile()
    assert e.db.lease(record['id'])['state']=='REVOKE_PENDING'
    with e.db.connect() as c:
        assert c.execute('SELECT quarantined FROM users WHERE id=?',(record['user_id'],)).fetchone()[0]==1
    e.reconcile()
    assert e.db.lease(record['id'])['state']=='REVOKED'
    assert not cloud.list_keys(record['user_id'])


def test_late_provider_create_is_recovered_after_initial_absence(env,caller):
    e,cloud,_,client=env
    lease=issue(client,caller).json();record=e.db.lease(lease['lease_id'])
    e.revoke(record['id'],{'issuer':record['issuer'],'subject':record['subject']})
    # Model a create with an unknown reply that appears only after compensation
    # initially listed no key. No foreign or unrelated display name is adopted.
    with e.db.tx() as c:c.execute('UPDATE leases SET key_id=NULL WHERE id=?',(record['id'],))
    cloud.create_key(record['user_id'],record['display_name'])
    e.reconcile()
    assert e.db.lease(record['id'])['state']=='REVOKE_PENDING'
    e.reconcile()
    assert e.db.lease(record['id'])['state']=='REVOKED'
    assert not cloud.list_keys(record['user_id'])
