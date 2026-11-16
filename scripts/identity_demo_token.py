"""Operator-only service token helper. Run through DemoBastion; never log output.
The OAuth secret is read from Vault by the private VM's instance principal.
"""
from pathlib import Path
import subprocess
code='''import base64,json,oci,requests
from pathlib import Path
cfg=json.loads(Path('/etc/lcm-live/config.json').read_text())['oauth']
signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
client=oci.secrets.SecretsClient({},signer=signer)
bundle=client.get_secret_bundle(cfg['secret_id']).data
secret=json.loads(base64.b64decode(bundle.secret_bundle_content.content))['connector_client_secret']
r=requests.post(cfg['token_endpoint'],auth=(cfg['connector_client_id'],secret),data={'grant_type':'client_credentials','scope':cfg['scope']},timeout=30)
if r.status_code!=200:raise RuntimeError('Identity Domains token request failed')
print(r.json()['access_token'])
'''
if __name__=='__main__':
 result=subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','sudo /opt/lcm-live/venv/bin/python -'],input=code,cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,check=True)
 print(result.stdout.strip())
