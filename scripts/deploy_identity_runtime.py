"""Deploy the verified image and staged OAuth config without touching active leases."""
import json
from pathlib import Path
import subprocess
ROOT=Path(__file__).resolve().parents[1]
def remote(command,data=None):
 return subprocess.run(['ssh','-F','.local/ssh-config','lcm-live',command],input=data,cwd=ROOT,capture_output=True,check=True)
variables=json.loads((ROOT/'.local/live-variables.json').read_text())
assert not variables.get('image_build_enabled') and not variables.get('bootstrap_enabled')
manifest=json.loads((ROOT/'release/live-image-manifest.json').read_text())
cfg=json.loads((ROOT/'.local/identity-domain-config.json').read_text())
# Keep listener/authentication mode aligned with the gateway rollout setting.
split=variables.get('gateway_jwt_enabled',True)
cfg['auth_mode']='gateway' if split else 'jwt'
state=json.loads((ROOT/'.local/live.tfstate').read_text())
subnet=next(r['instances'][0]['attributes']['cidr_block'] for r in state['resources'] if r['type']=='oci_core_subnet' and r['name']=='public')
cfg['gateway_trusted_networks']=[subnet] if split else []
assert cfg['oauth']['secret_id'] and cfg['profile_scopes']=={'sales-read':'sales-read','sales-write':'sales-write'}
remote('sudo tee /etc/lcm-live/identity-config-staged.json >/dev/null',json.dumps(cfg).encode())
code='''import json,subprocess,os,base64
from pathlib import Path
import oci
cfg=json.loads(Path('/etc/lcm-live/identity-config-staged.json').read_text())
signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
secret=oci.secrets.SecretsClient({},signer=signer).get_secret_bundle(cfg['oauth']['secret_id']).data
assert set(json.loads(base64.b64decode(secret.secret_bundle_content.content)))=={'login_client_secret','connector_client_secret'}
p=Path('/etc/lcm-live/config.json');backup=Path('/etc/lcm-live/config-before-identity.json')
if not backup.exists():backup.write_bytes(p.read_bytes());backup.chmod(0o600)
p.write_text(json.dumps(cfg));p.chmod(0o644)
subprocess.run(['/opt/lcm-live/venv/bin/python','/opt/lcm-live/source/scripts/install_live_runtime.py'],check=True)
'''
r=remote('sudo /opt/lcm-live/venv/bin/python -',code.encode())
(ROOT/'.local/identity-install.log').write_bytes(r.stdout+r.stderr)
backup=ROOT/'.local/config-before-identity.json'
if not backup.exists():backup.write_bytes((ROOT/'.local/live-config.json').read_bytes())
(ROOT/'.local/live-config.json').write_text(json.dumps(cfg,indent=2)+'\n')
variables['function_image']=manifest['tagged_image'];variables['function_image_digest']=manifest['digest']
variables['runtime_config_json']=json.dumps(cfg)
(ROOT/'.local/live-variables.json').write_text(json.dumps(variables,indent=2)+'\n')
print('Private runtime installed; OAuth secrets fetched from Vault; existing leases retained')
