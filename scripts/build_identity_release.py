"""Build on OCI with temporary repository permission removed in finally.
No database bootstrap permission; failures always attempt revocation and verify it.
"""
import json
import subprocess
import sys
import time
from pathlib import Path
import oci
ROOT=Path(__file__).resolve().parents[1]
PYTHON=sys.executable
variables=ROOT/'.local/live-variables.json'
client=oci.resource_manager.ResourceManagerClient(oci.config.from_file())
def apply():
 subprocess.run([PYTHON,'scripts/live_stack.py','update'],cwd=ROOT,check=True)
 record=json.loads((ROOT/'.local/live-deployment.json').read_text())
 for _ in range(120):
  job=client.get_job(record['job_id']).data
  if job.lifecycle_state=='SUCCEEDED':
   subprocess.run([PYTHON,'scripts/live_stack.py','status'],cwd=ROOT,check=True);return
  if job.lifecycle_state in ('FAILED','CANCELED'):raise RuntimeError('Resource Manager update failed')
  time.sleep(5)
 raise RuntimeError('Resource Manager update timed out; inspect active job before retry')
def save(enabled):
 v=json.loads(variables.read_text());assert not v.get('bootstrap_enabled')
 v['image_build_enabled']=enabled
 v['runtime_config_json']=(ROOT/'.local/identity-domain-config.json').read_text()
 variables.write_text(json.dumps(v,indent=2))
def ssh(code,data=None):
 return subprocess.run(['ssh','-F','.local/ssh-config','lcm-live',code],input=data,cwd=ROOT,check=True,capture_output=True)
try:
 save(True);apply()
 ssh('sudo tee /opt/lcm-live/identity-source.tar.xz >/dev/null',(ROOT/'release/lcm-live-source.tar.xz').read_bytes())
 result=ssh('sudo /opt/lcm-live/venv/bin/python -',b"import tarfile,subprocess\nwith tarfile.open('/opt/lcm-live/identity-source.tar.xz') as t:t.extractall('/opt/lcm-live/source',filter='data')\nsubprocess.run(['/opt/lcm-live/venv/bin/python','/opt/lcm-live/source/scripts/build_function_image.py'],check=True)\n")
 (ROOT/'.local/identity-build.log').write_bytes(result.stdout+result.stderr)
 manifest=ssh('sudo cat /opt/lcm-live/source/function-build-manifest.json').stdout
 (ROOT/'release/live-image-manifest.json').write_bytes(manifest)
 print('Built immutable image',json.loads(manifest)['image'],flush=True)
finally:
 save(False);apply()
 # Verify effective permission removal, not only the local desired setting.
 state=json.loads((ROOT/'.local/live.tfstate').read_text())
 policy=next(r['instances'][0]['attributes']['id'] for r in state['resources'] if r['type']=='oci_identity_policy' and r['name']=='issuer_services')
 statements=oci.identity.IdentityClient(oci.config.from_file()).get_policy(policy).data.statements
 assert not any('manage repos' in s for s in statements),'Build permission remains enabled'
 print('Verified temporary build permission removed; bootstrap stayed disabled',flush=True)
