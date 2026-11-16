"""Install pinned containers and independent systemd cleanup on the private demo VM.
Run as root after the schema and image bootstrap. Uses workload authentication only.
"""
import json
from pathlib import Path
import subprocess

root=Path('/opt/lcm-live')
manifest=json.loads((root/'source/function-build-manifest.json').read_text())
image=manifest['image']
assert image.startswith('iad.ocir.io/') and '@sha256:' in image
pull=Path('/opt/lcm-live/pull_image.py')
pull.write_text('''import json,os,subprocess,tempfile
import oci,requests
from pathlib import Path
image=json.loads(Path('/opt/lcm-live/image.json').read_text())['image']
signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
r=requests.get('https://iad.ocir.io/20180419/docker/token',auth=signer,timeout=30)
r.raise_for_status()
with tempfile.TemporaryDirectory(prefix='lcm-pull-',dir='/run') as d:
 os.chmod(d,0o700)
 auth=d+'/auth.json'
 subprocess.run(['podman','login','--authfile',auth,'-u','BEARER_TOKEN','--password-stdin','iad.ocir.io'],input=r.json()['token'],text=True,check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['podman','pull','--authfile',auth,image],check=True,stdout=subprocess.DEVNULL)
''')
(root/'image.json').write_text(json.dumps({'image':image}))
subprocess.run(['/opt/lcm-live/venv/bin/python',str(pull)],check=True)
subprocess.run(['systemctl','stop','lcm-cleanup.timer','lcm-api.service','lcm-cleanup.service'],capture_output=True)
# Migrate the initial unnamed containers. Match only this deployment's image and
# API/worker command; never stop unrelated containers on a shared host.
repository=image.split('@')[0]
running=json.loads(subprocess.check_output(['podman','ps','--format','json']))
for item in running:
    command_args=item.get('Command') or []
    text=' '.join(command_args) if isinstance(command_args,list) else command_args
    if str(item.get('Image','')).split('@')[0].split(':')[0]==repository and ('service.main:app' in text or 'scripts.worker' in text):
        subprocess.run(['podman','stop','--time=120',item['Id']],check=True,stdout=subprocess.DEVNULL)
base=['/usr/bin/podman','run','--rm','--replace','--label=application=oci-s3-lcm','--network=host','--user=10001:10001','--read-only',
      '--tmpfs=/tmp:rw,noexec,nosuid,size=64m','--cap-drop=all','--ulimit=core=0:0','--security-opt=no-new-privileges',
      '-v','/etc/lcm-live/config.json:/run/lcm-config:ro,z','-e','LCM_CONFIG=/run/lcm-config',
      '-e','LCM_WORKLOAD=instance','--entrypoint=/usr/local/bin/python',image]
def command(args):
    # systemd quotes arguments itself; the values are fixed argv, never a shell fragment.
    return ' '.join(json.dumps(x) for x in args)
common='''[Unit]
After=network-online.target
Wants=network-online.target
[Service]
TimeoutStartSec=240
TimeoutStopSec=150
UMask=0077
LimitCORE=0
'''
Path('/etc/systemd/system/lcm-image.service').write_text(common+'''Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/lcm-live/venv/bin/python /opt/lcm-live/pull_image.py
''')
Path('/etc/systemd/system/lcm-api.service').write_text(common.replace('[Service]', 'Requires=lcm-image.service\nAfter=lcm-image.service\n[Service]')+'''Restart=on-failure
RestartSec=5
ExecStart='''+command(base[:-1]+['--name=lcm-api',image,'-m','uvicorn','service.main:app','--host','0.0.0.0','--port','8080','--no-access-log'])+'''
ExecStop=/usr/bin/podman stop --ignore --time=120 lcm-api
ExecStopPost=/usr/bin/podman rm --force --ignore lcm-api
[Install]
WantedBy=multi-user.target
''')
Path('/etc/systemd/system/lcm-cleanup.service').write_text(common+'Type=oneshot\nExecStart='+command(base[:-1]+['--name=lcm-cleanup',image,'-m','scripts.worker'])+'\nExecStop=/usr/bin/podman stop --ignore --time=120 lcm-cleanup\nExecStopPost=/usr/bin/podman rm --force --ignore lcm-cleanup\n')
Path('/etc/systemd/system/lcm-cleanup.timer').write_text('''[Unit]
Description=LCM independent credential cleanup
[Timer]
OnBootSec=30
OnUnitInactiveSec=15
AccuracySec=1
Persistent=true
[Install]
WantedBy=timers.target
''')
subprocess.run(['systemctl','daemon-reload'],check=True)
if subprocess.run(['systemctl','is-active','--quiet','firewalld']).returncode==0:
    # OCI NSGs constrain this private listener to the gateway NSG.
    subprocess.run(['firewall-cmd','--permanent','--add-port=8080/tcp'],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['firewall-cmd','--add-port=8080/tcp'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['systemctl','enable','lcm-api.service','lcm-cleanup.timer'],check=True)
subprocess.run(['systemctl','restart','lcm-api.service'],check=True)
subprocess.run(['systemctl','start','lcm-cleanup.timer'],check=True)
print(json.dumps({'image':image,'api_service':'lcm-api','independent_cleanup_timer':'lcm-cleanup.timer'}))
