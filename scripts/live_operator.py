"""Run the host-local operator in the pinned live image through DemoBastion."""
import argparse,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser()
p.add_argument('action',choices=['status','pause','resume','drain','reconcile','repair-schedule','reconcile-user','export-audit'])
p.add_argument('--lease-id');p.add_argument('--profile');p.add_argument('--user-id');args=p.parse_args()
argv=[args.action]
for name in ('lease_id','profile','user_id'):
 if getattr(args,name):argv+=['--'+name.replace('_','-'),getattr(args,name)]
code="""import json,subprocess
image=json.load(open('/opt/lcm-live/image.json'))['image']
command=['podman','run','--rm','--network=host','--user=10001:10001','--read-only','--tmpfs=/tmp:rw,noexec,nosuid,size=64m',
 '--cap-drop=all','--ulimit=core=0:0','--security-opt=no-new-privileges','-v','/etc/lcm-live/config.json:/run/lcm-config:ro,z',
 '-e','LCM_CONFIG=/run/lcm-config','-e','LCM_WORKLOAD=instance','--entrypoint=/usr/local/bin/python',image,'-m','scripts.lcm_operator']
subprocess.run(command+ARGS,check=True)
""".replace('ARGS',repr(argv))
subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','sudo /opt/lcm-live/venv/bin/python -'],input=code,text=True,cwd=Path(__file__).resolve().parents[1],check=True)
