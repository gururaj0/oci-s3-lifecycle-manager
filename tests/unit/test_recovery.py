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
