"""Check a same-schema image rollback/upgrade while an owned lease remains active."""
import argparse,subprocess,uuid
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('lease_id',type=uuid.UUID);a=p.parse_args()
code="""import hashlib,json,subprocess,time,urllib.request
from pathlib import Path
from shared.config import Config
from shared.oracle_ledger import OracleLedger
cfg=Config.env();db=OracleLedger(cfg)
def snapshot():
 with db.connect() as c:
  r=c.execute('SELECT id,state,key_id,generation,expires_at FROM leases WHERE id=?',(LEASE,)).fetchone()
  return dict(r)
unit=Path('/etc/systemd/system/lcm-api.service');original=unit.read_text()
current=json.load(open('/opt/lcm-live/image.json'))['image']
prior='iad.ocir.io/iddjqcmpf2tk/lcm-live-v2/worker@sha256:633313a0142d453dd15791d2568b394b8fb0fe31e637684d2bd2656038de8b01'
subprocess.run(['podman','image','exists',prior],check=True)
before=snapshot();assert before['state']=='ACTIVE'
def restart(text):
 unit.write_text(text);subprocess.run(['systemctl','daemon-reload'],check=True)
 subprocess.run(['systemctl','restart','lcm-api.service'],check=True)
 for attempt in range(30):
  try:
   if urllib.request.urlopen('http://127.0.0.1:8080/health/ready',timeout=2).status==200:return
  except Exception:pass
  time.sleep(1)
 raise RuntimeError('UPGRADE_READINESS_TIMEOUT')
try:
 assert current in original
 restart(original.replace(current,prior))
 assert snapshot()==before,'Rollback changed the active lease'
 restart(original)
 assert snapshot()==before,'Upgrade changed the active lease'
 print(json.dumps({'same_schema_rollback_and_upgrade':True,'active_lease_preserved':True,'key_reference_unchanged':True,'deadline_unchanged':True,'generation_unchanged':True,'independent_timer_kept_running':True}))
finally:
 if unit.read_text()!=original:restart(original)
""".replace('LEASE',repr(str(a.lease_id)))
subprocess.run(['ssh','-F','.local/ssh-config','lcm-live','cd /opt/lcm-live/source && sudo env PYTHONPATH=. LCM_CONFIG=/etc/lcm-live/config.json /opt/lcm-live/venv/bin/python -'],input=code,text=True,cwd=Path(__file__).resolve().parents[1],check=True)
