"""Read-only browser check of Swagger rendering under the deployed content policy."""
import argparse
import json
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument('url')
args = parser.parse_args()
origin = args.url.rstrip('/')
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
    response = page.goto(origin + '/docs', wait_until='networkidle')
    assert response.status == 200
    expect(page.locator('.swagger-ui .info .title')).to_contain_text('OCI S3 Credential Lifecycle Manager')
    operation = page.locator('.opblock-post').filter(has_text='/v1/leases').first
    expect(operation).to_be_visible()
    operation.locator('.opblock-summary').click()
    expect(operation.locator('.opblock-body')).to_be_visible()
    expect(operation).to_contain_text('Request body')
    assert not errors, errors
    assert "'unsafe-inline'" not in response.headers['content-security-policy']
    schema = page.request.get(origin + '/openapi.json')
    assert schema.status == 200
    paths = schema.json()['paths']
    assert '/v1/leases' in paths and '/v1/leases/{lease_id}' in paths
    home = page.request.get(origin + '/')
    assert "script-src 'self';" in home.headers['content-security-policy']
    alternate = page.request.get(origin + '/redoc')
    assert alternate.status == 200 and alternate.url.endswith('/docs')
    print(json.dumps({'url': origin + '/docs', 'operations_rendered': page.locator('.opblock').count(),
                      'request_body_expands': True, 'schema_paths': len(paths),
                      'application_policy_preserved': True, 'browser_errors': errors}))
    browser.close()
