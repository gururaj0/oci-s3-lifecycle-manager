import base64
import io
from types import SimpleNamespace as NS
import oci
import pytest
from fastapi.testclient import TestClient
from service.app import create_app
from service.bucket_access import BucketOperation,MAX_BYTES
from tests.unit.test_gateway_auth import cfg,headers

class Objects:
    def __init__(self):
        self.calls=[];self.closed=False;self.body_closed=False;self.fail=None
        self.base_client=NS(session=NS(close=self.close))
    def close(self):self.closed=True
    def list_objects(self,**kw):
        self.calls.append(('LIST',kw))
        if self.fail:raise self.fail
        return NS(data=NS(objects=[NS(name='quarterly/revenue.csv',size=3,etag='etag')],next_start_with=None))
    def get_object(self,**kw):
        self.calls.append(('GET',kw));raw=io.BytesIO(b'abc')
        def close():self.body_closed=True;raw.close()
        return NS(headers={'etag':'etag','content-length':'3'},data=NS(raw=raw,close=close))
    def put_object(self,**kw):
        self.calls.append(('PUT',kw));return NS(headers={'etag':'new-etag'})

@pytest.fixture
def automated(env,monkeypatch):
    e=env[0];e.cfg=cfg();e.cfg.namespace='test-namespace'
    objects=Objects()
    monkeypatch.setattr('service.bucket_access.object_client',lambda _:objects)
    monkeypatch.setattr(e,'create',lambda *a,**k:pytest.fail('Automated access must not mint a lease'))
    with TestClient(create_app(e,worker=False),client=('10.73.0.8',1234)) as client:
        yield client,objects,e

def test_read_auto_selects_profile_and_never_creates_lease(automated):
    client,objects,e=automated
    r=client.post('/v1/bucket-operations',headers=headers(scope='sales-read sales-write'),json={'bucket':'sales-demo','operation':'GET','key':'quarterly/revenue.csv'})
    assert r.status_code==200
    assert r.json()['selected_profile']=='sales-read'
    assert r.json()['content_base64']==base64.b64encode(b'abc').decode()
    assert r.json()['lease_created'] is False
    assert objects.closed and objects.body_closed
    assert objects.calls[0][1]['namespace_name']=='test-namespace'
    with e.db.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM leases').fetchone()[0]==0
        detail=c.execute("SELECT detail FROM audit WHERE action='BUCKET_OPERATION_SUCCEEDED'").fetchone()[0]
        assert 'quarterly/revenue.csv' not in detail and 'connector' in detail

@pytest.mark.parametrize('body',[
    {'bucket':'other-bucket','operation':'LIST'},
    {'bucket':'sales-demo','operation':'PUT','key':'a','content_base64':'YQ=='},
])
def test_profile_authorization_before_any_oci_call(automated,body):
    client,objects,_=automated
    r=client.post('/v1/bucket-operations',headers=headers(scope='sales-read'),json=body)
    assert r.status_code==403 and objects.calls==[]

def test_unmapped_identity_rejected(automated):
    client,objects,_=automated
    assert client.post('/v1/bucket-operations',headers=headers(subject='unmapped'),json={'bucket':'sales-demo','operation':'LIST'}).status_code==403
    assert not objects.calls

@pytest.mark.parametrize('extra',[
 {'endpoint':'https://evil.test'}, {'operation':'DELETE'}, {'profile':'sales-write'},
 {'operation':'PUT','key':'a','content_base64':'not base64'},
 {'operation':'GET'}, {'limit':101},
])
def test_invalid_inputs_are_rejected(automated,extra):
    client,objects,_=automated
    r=client.post('/v1/bucket-operations',headers=headers(),json={'bucket':'sales-demo','operation':'LIST',**extra})
    assert r.status_code==400 and not objects.calls

@pytest.mark.parametrize('etag',[None,'previous-etag'])
def test_put_is_conditional_and_selects_write_profile(automated,etag):
    client,objects,_=automated
    body={'bucket':'sales-demo','operation':'PUT','key':'test','content_base64':'YQ=='}
    if etag:body['if_match']=etag
    r=client.post('/v1/bucket-operations',headers=headers(scope='sales-write'),json=body)
    assert r.status_code==200 and r.json()['selected_profile']=='sales-write'
    args=objects.calls[0][1]
    assert args['put_object_body']==b'a'
    assert args.get('if_match')==etag
    assert args.get('if_none_match')==('*' if etag is None else None)
    assert objects.closed

def test_native_iam_denial_not_treated_as_success(automated):
    client,objects,_=automated
    objects.fail=oci.exceptions.ServiceError(403,'NotAuthorized',{},'sensitive upstream message')
    r=client.post('/v1/bucket-operations',headers=headers(),json={'bucket':'sales-demo','operation':'LIST'})
    assert r.status_code==403 and r.json()['error']['code']=='OCI_ACCESS_DENIED'
    assert 'sensitive' not in r.text and objects.closed

def test_oversized_download_closes_body(automated,monkeypatch):
    client,objects,_=automated
    def get(**kw):
        return NS(headers={'content-length':str(MAX_BYTES+1)},data=NS(close=lambda:setattr(objects,'body_closed',True)))
    monkeypatch.setattr(objects,'get_object',get)
    r=client.post('/v1/bucket-operations',headers=headers(),json={'bucket':'sales-demo','operation':'GET','key':'large'})
    assert r.status_code==413 and objects.closed and objects.body_closed

def test_ui_automatic_operation_requires_session_and_csrf(env,monkeypatch):
    import time,httpx
    from service.ui import create_ui_app
    from service.connector_auth import COOKIE
    e=env[0];e.cfg=cfg();app=create_ui_app(e);seen=[]
    async def upstream(self,method,url,**kwargs):
        seen.append((method,url,kwargs));return httpx.Response(200,json={'lease_created':False})
    monkeypatch.setattr(httpx.AsyncClient,'request',upstream)
    with TestClient(app,base_url='https://gateway.test') as client:
        body={'bucket':'sales-demo','operation':'LIST'}
        assert client.post('/connector/v1/bucket-operations',json=body).status_code==401
        app.state.connector.sessions['session']={'expires':time.time()+600,'token':'server-only-token','csrf':'csrf','login_subject':'operator','subject':'connector'}
        client.cookies.set(COOKIE,'session')
        assert client.post('/connector/v1/bucket-operations',json=body).status_code==403
        r=client.post('/connector/v1/bucket-operations',json=body,headers={'Origin':'https://gateway.test','X-LCM-CSRF':'csrf','X-LCM-Subject':'forged'})
        assert r.status_code==200 and len(seen)==1
        assert seen[0][1]=='https://gateway.test/v1/bucket-operations'
        assert seen[0][2]['headers']['Authorization']=='Bearer server-only-token'
        assert 'X-LCM-Subject' not in seen[0][2]['headers']
        assert 'server-only-token' not in r.text
