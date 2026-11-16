"""Print a short-lived token for the explicitly mapped private demo subject.
Operator-only: requires the existing SSH key and DemoBastion route. Not a Unity IdP.
"""
import json,subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
cfg=json.loads((root/'.local/live-config.json').read_text())
subject=next(iter(cfg['identities'][cfg['issuer']]))
code="from pathlib import Path\nfrom shared.config import Config\nfrom shared.auth import Auth\nprint(Auth(Config(state_dir=Path('/var/lib/lcm'))).mint_demo("+repr(subject)+"))\n"
result=subprocess.run(['ssh','-F','.local/ssh-config','lcm-backend','cd /opt/lcm && sudo env PYTHONPATH=/opt/lcm /opt/lcm/venv/bin/python -'],input=code,cwd=root,capture_output=True,text=True,check=True)
print(result.stdout.strip())
