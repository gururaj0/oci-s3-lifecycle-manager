"""Verify packaged runtime file hashes over the authorized bastion route; no credential reads."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
FILES=['service/app.py','service/s3.py','service/static/app.js','shared/lifecycle.py','scripts/lcm_operator.py','requirements-runtime.lock']
code='import hashlib,json;from pathlib import Path;names='+repr(FILES)+';print(json.dumps({n:hashlib.sha256(Path(n).read_bytes()).hexdigest() for n in names}))'
# Send Python via stdin; avoid shell interpolation of workspace paths or Python source.
command='cd /opt/lcm && /opt/lcm/venv/bin/python -'
p=subprocess.run(['ssh','-F','.local/ssh-config','lcm-backend',command],input=code,capture_output=True,text=True,cwd=ROOT,check=True)
actual=json.loads(p.stdout)
expected={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
assert actual==expected,'Deployed runtime differs from release source'
result={'mode':'mock','runtime_matches_release':True,'files':expected}
(ROOT/'docs/deployed-source-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print('Deployed runtime hashes match release source.')
