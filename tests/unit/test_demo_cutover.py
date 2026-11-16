def test_retired_demo_redirects_without_breaking_live_issuer(env):
    engine, _, _, client = env
    public_keys = client.get('/.well-known/jwks.json').json()
    engine.cfg.live_redirect_url = 'https://live.example.com'

    for method, path in [('GET', '/'), ('GET', '/docs'), ('POST', '/demo/session'),
                         ('POST', '/v1/leases'), ('GET', '/mock-s3/sales-demo')]:
        response = client.request(method, path, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers['location'] == 'https://live.example.com' + path
        assert response.headers['cache-control'] == 'no-store'

    response = client.get('/.well-known/jwks.json')
    assert response.status_code == 200
    assert response.json() == public_keys
    with engine.db.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM leases').fetchone()[0] == 0


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
