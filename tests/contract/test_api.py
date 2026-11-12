import concurrent.futures
import json
import time
import uuid
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa


def issue(client,headers,profile='sales-read',idem=None,ttl=300,**extra):
    return client.post('/v1/leases',headers=dict(headers,**{'Idempotency-Key':idem or str(uuid.uuid4())}),
                       json=dict(scope_profile=profile,ttl_seconds=ttl,**extra))

def test_contract_replay_and_status_secrecy(env,caller):
    e,cloud,app,client=env
    idem=str(uuid.uuid4())
    r=issue(client,caller,idem=idem)
    assert r.status_code==201,r.text
    body=r.json()
    assert body['status']=='ACTIVE'
    assert body['expiry_mode']=='managed_key_deletion'
    assert 'session_token' not in body
    assert body['access_key_id'].startswith('MOCK')
    assert r.headers['cache-control']=='no-store'
    replay=issue(client,caller,idem=idem)
    assert replay.status_code==200 and replay.json()==body
    status=client.get('/v1/leases/'+body['lease_id'],headers=caller)
    assert status.status_code==200
    assert 'secret_access_key' not in status.text and body['access_key_id'] not in status.text
    assert issue(client,caller,idem=idem,ttl=301).status_code==409
    with e.db.tx() as c:
        c.execute('UPDATE leases SET replay_until=? WHERE id=?',(time.time()-1,body['lease_id']))
    expired=issue(client,caller,idem=idem)
    assert expired.status_code==409 and expired.json()['error']['code']=='CREDENTIALS_NOT_REPLAYABLE'
    assert expired.json()['error']['lease_id']==body['lease_id']
    assert len(cloud.list_keys(e.db.lease(body['lease_id'])['user_id']))==1

@pytest.mark.parametrize('ttl',[0,299,3601,'300',True,1.2])
def test_ttl_validation(env,caller,ttl):
    r=issue(env[3],caller,ttl=ttl)
    assert r.status_code==400


def test_default_ttl_and_validation(env,caller):
    client=env[3]
    r=client.post('/v1/leases',headers=dict(caller,**{'Idempotency-Key':str(uuid.uuid4())}),json={'scope_profile':'sales-read'})
    assert r.status_code==201
    assert env[0].db.lease(r.json()['lease_id'])['requested_ttl']==900
    assert client.post('/v1/leases',headers=caller,json={'scope_profile':'sales-read'}).status_code==400
    assert issue(client,caller,profile='prefix-only').status_code==422
    assert issue(client,caller,unexpected='scope').status_code==400


def test_owner_and_forged_headers(env,caller):
    client=env[3]
    body=issue(client,caller).json()
    bob={'Authorization':'Bearer '+env[2].state.auth.mint_demo('demo:bob')}
    assert client.get('/v1/leases/'+body['lease_id'],headers=bob).status_code==403
    assert client.delete('/v1/leases/'+body['lease_id'],headers=bob).status_code==403
    assert client.post('/v1/leases',headers={'Celonis-User':'demo:alice','Idempotency-Key':str(uuid.uuid4())},json={'scope_profile':'sales-read'}).status_code==401
    stranger={'Authorization':'Bearer '+env[2].state.auth.mint_demo('unapproved:alice')}
    assert issue(client,stranger).status_code==403

@pytest.mark.parametrize('change',[{'iss':'evil'},{'aud':'evil'},{'exp':1},{'sub':''}])
def test_bad_claims(env,change):
    auth=env[2].state.auth
    claims={'iss':env[0].cfg.issuer,'aud':'oci-lcm','sub':'demo:a','iat':int(time.time()),'exp':int(time.time()+60),**change}
    token=jwt.encode(claims,auth.private,algorithm='RS256')
    assert env[3].get('/v1/config',headers={'Authorization':'Bearer '+token}).status_code==401


def test_invalid_signatures_and_algorithms(env):
    auth=env[2].state.auth
    claims={'iss':env[0].cfg.issuer,'aud':'oci-lcm','sub':'demo:a','iat':int(time.time()),'exp':int(time.time()+60)}
    tokens=[jwt.encode(claims,rsa.generate_private_key(public_exponent=65537,key_size=2048),algorithm='RS256'),
            jwt.encode(claims,'x'*32,algorithm='HS256'),jwt.encode(claims,None,algorithm='none')]
    for token in tokens:
        assert env[3].get('/v1/config',headers={'Authorization':'Bearer '+token}).status_code==401


def test_capacity_and_retry_races(env,caller):
    client=env[3]
    idem=str(uuid.uuid4())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results=list(pool.map(lambda _:issue(client,caller,idem=idem),range(8)))
    assert sum(r.status_code==201 for r in results)==1
    assert all(r.status_code in (200,201,409) for r in results)
    with env[0].db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM leases').fetchone()[0]==1
    assert issue(client,caller).status_code==201
    r=issue(client,caller)
    assert r.status_code==429 and r.headers['Retry-After']
    with env[0].db.connect() as c:
        assert c.execute('SELECT COUNT(DISTINCT key_id) FROM leases').fetchone()[0]==2


def test_scope_and_revoke_signed_s3(env,caller):
    e,cloud,app,client=env
    body=issue(client,caller).json()
    def probe(op='GET',bucket='sales-demo',secret=None):
        return client.post('/demo/probe',headers=caller,json={
            'lease_id':body['lease_id'],'access_key_id':body['access_key_id'],
            'secret_access_key':secret or body['secret_access_key'],'operation':op,'bucket':bucket}).json()
    assert probe()['status_code']==200
    assert 'Q1,124000' in probe()['body']
    assert probe('LIST')['status_code']==200
    assert probe('PUT')['status_code']==403
    assert probe('DELETE')['status_code']==403
    assert probe(bucket='foreign')['status_code']==403
    assert probe(secret='wrong')['status_code']==403
    assert client.delete('/v1/leases/'+body['lease_id'],headers=caller).status_code==202
    assert probe()['status_code']==403
    assert client.delete('/v1/leases/'+body['lease_id'],headers=caller).status_code==204
    assert cloud.get_response(e.db.lease(body['lease_id'])['replay_ref']) is None
    w=issue(client,caller,profile='sales-write').json()
    r=client.post('/demo/probe',headers=caller,json={'lease_id':w['lease_id'],'access_key_id':w['access_key_id'],
        'secret_access_key':w['secret_access_key'],'operation':'PUT','object_name':'test-write.txt'})
    assert r.json()['status_code']==200


def test_ready_and_cleanup_stale(env,caller):
    e,_,_,client=env
    assert client.get('/health/ready').json()=={'status':'READY','mode':'mock','production_ready':False}
    with e.db.tx() as c:
        c.execute("UPDATE control SET value=? WHERE id='heartbeat'",(str(time.time()-121),))
    assert client.get('/health/ready').status_code==503
    assert issue(client,caller).json()['error']['code']=='CLEANUP_UNHEALTHY'
    assert 'user_id' not in client.get('/health/ready').text


def test_no_secret_in_public_surfaces_or_ledger(env,caller):
    body=issue(env[3],caller).json()
    for path in ['/v1/leases','/demo/audit','/demo/metrics','/health/ready','/demo/info']:
        text=env[3].get(path,headers=caller).text
        assert body['secret_access_key'] not in text and body['access_key_id'] not in text
    raw=(env[0].cfg.state_dir/'ledger.db').read_bytes()
    assert body['secret_access_key'].encode() not in raw
    assert body['secret_access_key'].encode() not in (env[0].cfg.state_dir/'mock-cloud.db').read_bytes()


def test_pinned_gateway_hostname_for_s3_signature(env,caller):
    from botocore.auth import S3SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials
    e,cloud,app,client=env
    e.cfg.public_url='https://gateway.example'
    body=issue(client,caller).json()
    assert body['endpoint']=='https://gateway.example/mock-s3'
    request=AWSRequest(method='GET',url='https://gateway.example/mock-s3/sales-demo/quarterly/revenue.csv')
    S3SigV4Auth(Credentials(body['access_key_id'],body['secret_access_key']),'s3',e.cfg.region).add_auth(request)
    headers=dict(request.headers)
    headers['host']='172.16.61.8:8080'
    r=client.get('/mock-s3/sales-demo/quarterly/revenue.csv',headers=headers)
    assert r.status_code==200,r.text
    headers['X-Forwarded-Host']='evil.example'
    assert client.get('/mock-s3/sales-demo/quarterly/revenue.csv',headers=headers).status_code==200
