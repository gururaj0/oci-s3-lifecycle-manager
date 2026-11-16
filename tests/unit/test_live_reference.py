def test_live_reference_excludes_synthetic_endpoints(env):
    from fastapi.testclient import TestClient
    from service.app import create_app

    engine = env[0]
    engine.cfg.mode = 'oci'
    engine.cfg.jwks_url = 'https://issuer.example.com/jwks.json'
    with TestClient(create_app(engine, worker=False)) as client:
        paths = client.get('/openapi.json').json()['paths']
        assert '/v1/leases' in paths
        assert '/v1/leases/{lease_id}' in paths
        assert not any(path.startswith(('/demo/', '/mock-s3/', '/.well-known/')) for path in paths)
        assert client.post('/demo/session').status_code == 404
