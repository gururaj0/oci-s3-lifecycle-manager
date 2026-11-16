"""OCI-hosted build: Podman + signed instance-principal OCIR token; no local Docker/Fn CLI.
Run as root on the private LCM builder VM. Registry auth is temporary and deleted after push.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import oci
import requests

root=Path('/opt/lcm-live/source')
cfg=json.loads(Path('/etc/lcm-live/build.json').read_text())
signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
base='docker.io/library/python:3.12-slim-bookworm'
def run(args,**kwargs):
    p=subprocess.run(args,capture_output=True,text=True,**kwargs)
    if p.returncode:
        print(p.stdout[-14000:]);print(p.stderr[-14000:]);p.check_returncode()
    return p
run(['podman','pull',base])
info=json.loads(run(['podman','image','inspect',base]).stdout)[0]
base_digest=info['RepoDigests'][0]
(root/'Dockerfile.functions').write_text('''FROM '''+base_digest+'''
WORKDIR /function
COPY requirements*.txt requirements-runtime.lock /function/
RUN pip install --no-cache-dir --upgrade pip==26.3 && pip install --no-cache-dir -r requirements-runtime.lock -r requirements-functions.txt pip-audit==2.10.1 && pip-audit --format json > /function/dependency-audit.json
COPY shared /function/shared
COPY service /function/service
COPY scripts/worker.py scripts/lcm_operator.py /function/scripts/
COPY functions/live_handler.py /function/func.py
ENV LCM_WORKLOAD=function PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/function
ENTRYPOINT ["/usr/local/bin/fdk", "/function/func.py", "handler"]
''')
inputs=sorted([*root.glob('requirements*'),*(root/'shared').glob('*.py'),*(root/'service').rglob('*'),root/'functions/live_handler.py',root/'Dockerfile.functions'])
source_hash=hashlib.sha256(b''.join(str(p.relative_to(root)).encode()+b'\0'+p.read_bytes()+b'\0' for p in inputs if p.is_file())).hexdigest()
image=cfg['registry']+'/'+cfg['namespace']+'/'+cfg['repository']+':v020-'+source_hash[:12]
run(['podman','build','--format=docker','-f','Dockerfile.functions','-t',image,str(root)],cwd=root)
# The OCI registry's documented Docker token endpoint accepts an OCI-signed request.
r=requests.get('https://'+cfg['registry']+'/20180419/docker/token',auth=signer,timeout=30)
r.raise_for_status();token=r.json()['token']
with tempfile.TemporaryDirectory(prefix='lcm-ocir-',dir='/run') as private:
    os.chmod(private,0o700);authfile=str(Path(private)/'auth.json')
    run(['podman','login','--authfile',authfile,'-u','BEARER_TOKEN','--password-stdin',cfg['registry']],input=token)
    del token
    run(['podman','push','--authfile',authfile,'--digestfile',str(root/'function-image.digest'),image])
digest=(root/'function-image.digest').read_text().strip()
result={'image':image.split(':')[0]+'@'+digest,'tagged_image':image,'digest':digest,'base_image':base_digest,'source_sha256':source_hash,'builder':'OCI private VM / instance principal','dependency_scan':'passed'}
(root/'function-build-manifest.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
