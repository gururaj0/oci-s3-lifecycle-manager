import io
import json
import time
import pytest
from tests.contract.test_api import issue

@pytest.mark.parametrize('module_name',['delete_key','setup_scheduler'])
def test_functions_reject_arbitrary_key_authority(env,module_name,monkeypatch):
    import importlib
    module=importlib.import_module('functions.'+module_name+'.func')
    monkeypatch.setattr(module,'engine',lambda:env[0])
    with pytest.raises(ValueError):
        module.handler(None,io.BytesIO(json.dumps({'user_id':'foreign','key_id':'foreign','lease_id':'x','generation':1}).encode()))


def test_function_invocation_stale_generation_and_early_time(env,caller,monkeypatch):
    from functions.delete_key import func
    monkeypatch.setattr(func,'engine',lambda:env[0])
    lease=issue(env[3],caller).json()
    for generation in [1,2]:
        r=json.loads(func.handler(None,io.BytesIO(json.dumps({'lease_id':lease['lease_id'],'generation':generation}).encode())))
        assert r['revoked'] is False
    with env[0].db.tx() as c:
        c.execute('UPDATE leases SET expires_at=? WHERE id=?',(time.time()-1,lease['lease_id']))
    r=json.loads(func.handler(None,io.BytesIO(json.dumps({'lease_id':lease['lease_id'],'generation':1}).encode())))
    assert r['revoked'] is True


def test_replay_secret_purged_while_lease_active(env,caller):
    e,cloud,_,client=env
    lease=issue(client,caller).json();r=e.db.lease(lease['lease_id'])
    with cloud.connect() as c:
        c.execute('UPDATE secrets SET expires=? WHERE id=?',(time.time()-1,r['replay_ref']))
    assert cloud.get_response(r['replay_ref']) is None
    e.reconcile()
    with cloud.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM secrets WHERE id=?',(r['replay_ref'],)).fetchone()[0]==0
    assert e.db.lease(r['id'])['state']=='ACTIVE'
    assert cloud.list_keys(r['user_id'])
