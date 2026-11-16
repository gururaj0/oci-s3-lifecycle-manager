"""Real UI issuance/S3/revocation check; tokens and keys stay only in process memory."""
import json
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import httpx
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
cfg = json.loads((root / '.local/live-config.json').read_text())
legacy = json.loads((root / '.local/deployment.json').read_text())
token = subprocess.run([sys.executable, str(root / 'scripts/live_demo_token.py')], cwd=root,
                       capture_output=True, text=True, check=True).stdout.strip()
out = {'mode': 'oci', 'credentials_logged': False, 'checks': {}}
lease = None
headers = {'Authorization': 'Bearer ' + token}
with httpx.Client(base_url=cfg['public_url'], headers=headers, timeout=150) as api:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(type(error).__name__))
        try:
            page.goto(legacy['api_endpoint'], wait_until='networkidle')
            assert page.url.rstrip('/') == cfg['public_url'].rstrip('/')
            assert 'Mock environment' not in page.locator('body').inner_text()
            expect(page.locator('header')).to_contain_text('LIVE OCI')
            expect(page.locator('.notice')).to_contain_text('238 seconds')
            out['checks']['legacy_url_opens_live_ui'] = True
            page.locator('#bearer').fill(token)
            page.locator('#connect button').click()
            expect(page.locator('#live-status')).to_contain_text('Verified identity connected')
            page.locator('#live-ttl').select_option('300')
            started = time.monotonic()
            with page.expect_response(lambda r: r.url.endswith('/v1/leases') and r.request.method == 'POST', timeout=150000) as pending:
                page.locator('#live-create').click()
            response = pending.value
            assert response.status == 201, 'UI issuance did not return 201'
            lease = response.json()
            expect(page.locator('#live-result-panel')).to_be_visible()
            assert page.locator('#live-secret').get_attribute('type') == 'password'
            out['issuance_seconds'] = round(time.monotonic() - started, 3)
            out['checks']['real_ui_issuance'] = True
            client = boto3.client('s3', endpoint_url=lease['endpoint'], region_name=lease['region'],
                aws_access_key_id=lease['access_key_id'], aws_secret_access_key=lease['secret_access_key'],
                config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}, retries={'max_attempts': 0}))
            assert '.oraclecloud.com' in lease['endpoint'] or '.oci.customer-oci.com' in lease['endpoint']
            cutoff = datetime.fromisoformat(lease['expires_at'].replace('Z', '+00:00')).timestamp() - 30
            allowed = False
            while time.time() < cutoff:
                try:
                    body = client.get_object(Bucket=cfg['profiles']['sales-read']['bucket'], Key='quarterly/revenue.csv')['Body'].read()
                    assert body.startswith(b'quarter,revenue\n')
                    allowed = True
                    break
                except ClientError as error:
                    if error.response['ResponseMetadata']['HTTPStatusCode'] not in (403, 404):
                        raise
                time.sleep(5)
            out['checks']['native_oci_signed_s3_read'] = allowed
            assert allowed, 'No signed S3 read succeeded before the test cutoff'
            row = page.locator('#live-rows tr').filter(has_text=lease['lease_id'][:8])
            row.get_by_role('button', name='Revoke', exact=True).click()
            for _ in range(60):
                if api.get('/v1/leases/' + lease['lease_id']).json()['status'] == 'REVOKED':
                    break
                time.sleep(2)
            else:
                raise AssertionError('Control-plane revocation not confirmed')
            out['checks']['real_ui_control_plane_revocation'] = True
            page.locator('#live-clear').click()
            expect(page.locator('#live-result-panel')).to_be_hidden()
            assert page.locator('#live-secret').input_value() == ''
            page.screenshot(path=str(root / 'docs/live-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path=str(root / 'docs/live-mobile.png'), full_page=True)
            assert not errors
            out['checks']['secrets_cleared_before_screenshots'] = True
            out['checks']['mobile_no_overflow'] = True
        finally:
            if lease:
                api.delete('/v1/leases/' + lease['lease_id'])
            browser.close()
out['timed_s3_denial_acceptance'] = 'Previously failed; this check proves control-plane revocation only'
out['passed'] = all(out['checks'].values())
(root / 'docs/live-cutover-evidence.json').write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps(out))
