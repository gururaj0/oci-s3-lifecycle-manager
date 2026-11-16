"""Redirect the legacy demo to the verified live service, retaining its JWT issuer key.

Uses the existing DemoBastion SSH configuration; never replaces either VM.
Run after installing and enabling the live image. No credentials are printed.
"""
import json
from pathlib import Path
import subprocess
from urllib.parse import urlparse
import httpx
import oci

root = Path(__file__).resolve().parents[1]
cfg = json.loads((root / '.local/live-config.json').read_text())
legacy = json.loads((root / '.local/deployment.json').read_text())
url = cfg['public_url'].rstrip('/')
parsed = urlparse(url)
assert cfg['mode'] == 'oci' and parsed.scheme == 'https'
assert parsed.hostname.endswith('.apigateway.us-ashburn-1.oci.customer-oci.com')
assert not parsed.query and not parsed.fragment and not parsed.username
assert legacy['compartment_id'] == cfg['compartment_id']
compartment = oci.identity.IdentityClient(oci.config.from_file()).get_compartment(cfg['compartment_id']).data
assert compartment.name == 'Demo' and compartment.lifecycle_state == 'ACTIVE'
with httpx.Client(timeout=30) as client:
    response = client.get(url + '/health/ready')
    assert response.status_code == 200 and response.json()['mode'] == 'oci'
    assert client.get(url + '/demo/session').status_code == 404
    assert 'sha256-' in client.get(url + '/docs').headers['content-security-policy']

for source, target in [('service/app.py', '/tmp/lcm-cutover-app.py'),
                       ('shared/config.py', '/tmp/lcm-cutover-config.py')]:
    subprocess.run(['scp', '-F', '.local/ssh-config', source, 'lcm-backend:' + target],
                   cwd=root, check=True, capture_output=True)
code = """import json,subprocess
from pathlib import Path
for source,target in [('/tmp/lcm-cutover-app.py','/opt/lcm/service/app.py'),
                      ('/tmp/lcm-cutover-config.py','/opt/lcm/shared/config.py')]:
    destination=Path(target)
    backup=destination.with_suffix(destination.suffix+'.before-live-cutover')
    if not backup.exists():backup.write_bytes(destination.read_bytes())
    destination.write_bytes(Path(source).read_bytes())
directory=Path('/etc/systemd/system/lcm-api.service.d');directory.mkdir(exist_ok=True)
(directory/'live-demo.conf').write_text('[Service]\\nEnvironment=LCM_LIVE_DEMO_URL='+__LIVE_ENDPOINT__+'\\n')
subprocess.run(['systemctl','daemon-reload'],check=True)
subprocess.run(['systemctl','restart','lcm-api.service'],check=True)
""".replace('__LIVE_ENDPOINT__', repr(url))
subprocess.run(['ssh', '-F', '.local/ssh-config', 'lcm-backend',
                'sudo /opt/lcm/venv/bin/python -'], input=code, text=True,
               cwd=root, check=True, capture_output=True)
legacy.update(mode='live_redirect', live_endpoint=url)
(root / '.local/deployment.json').write_text(json.dumps(legacy, indent=2) + '\n')
print(json.dumps({'legacy_demo_redirected': True, 'live_endpoint': url,
                  'compartment_verified': compartment.id, 'issuer_key_preserved': True}))
