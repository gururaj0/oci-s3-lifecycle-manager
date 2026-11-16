def test_reference_describes_actual_lease_contract(env, caller):
    import uuid
    client=env[3]
    spec=client.get('/openapi.json').json()
    from fastapi.openapi.models import OpenAPI
    OpenAPI.model_validate(spec)
    post=spec['paths']['/v1/leases']['post']
    assert post['parameters'][0]['name']=='Idempotency-Key'
    assert post['parameters'][0]['required']
    assert post['parameters'][0]['schema']['format']=='uuid'
    assert post['responses']['201']['content']['application/json']['schema']['$ref'].endswith('/LeaseCredentials')
    headers={**caller,'Idempotency-Key':str(uuid.uuid4())}
    response=client.post('/v1/leases',headers=headers,json={'scope_profile':'sales-read','ttl_seconds':300})
    assert response.status_code==201
    assert set(spec['components']['schemas']['LeaseCredentials']['required'])<=set(response.json())
    lease=response.json()['lease_id']
    assert client.delete('/v1/leases/'+lease,headers=caller).status_code==202
    assert client.delete('/v1/leases/'+lease,headers=caller).status_code==204
    responses=spec['paths']['/v1/leases/{lease_id}']['delete']['responses']
    assert '200' not in responses and {'202','204'}<=set(responses)
    assert '422' not in post['responses'] or post['responses']['422']['description']=='Unsupported profile'


def test_unity_guide_is_available_on_split_ui(env):
    from fastapi.testclient import TestClient
    from service.ui import create_ui_app
    from tests.unit.test_gateway_auth import cfg
    e=env[0];e.cfg=cfg()
    with TestClient(create_ui_app(e)) as client:
        r=client.get('/unity')
        assert r.status_code==200 and 'Unity connector integration guide' in r.text
        assert '/static/unity_client.py' in r.text
        assert client.get('/static/unity-api.css').status_code==200
        assert client.get('/v1/config').status_code==404
