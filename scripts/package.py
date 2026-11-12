"""Build a secret-free, reproducible Resource Manager source bundle."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def application_archive():
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w:xz') as out:
        for directory in ['shared','service','scripts']:
            for path in sorted((ROOT/directory).rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix not in ('.pyc','.zip') and (directory != 'scripts' or path.name in ('worker.py','lcm_operator.py')):
                    info=out.gettarinfo(str(path),arcname=str(path.relative_to(ROOT)))
                    info.uid=info.gid=0;info.uname=info.gname='';info.mtime=0
                    with path.open('rb') as source:out.addfile(info,source)
        for name in ['requirements.txt','requirements-runtime.lock','pyproject.toml']:
            p=ROOT/name
            info=out.gettarinfo(str(p),arcname=name);info.mtime=0;info.uid=info.gid=0;info.uname=info.gname=''
            with p.open('rb') as source:out.addfile(info,source)
    return buf.getvalue()

if __name__=='__main__':
    payload=application_archive()
    assets=ROOT/'infra'/'assets';assets.mkdir(exist_ok=True)
    (assets/'application.tar.xz.b64').write_text(base64.b64encode(payload).decode())
    manifest={'version':'0.1.0','profile':'mock','production_ready':False,
              'application_sha256':hashlib.sha256(payload).hexdigest(),'artifact':'embedded-source',
              'production_gates':'docs/acceptance.md'}
    (ROOT/'release'/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    destination=ROOT/'release'/'oci-s3-lcm-0.1.0-mock.zip'
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as out:
        for path in sorted((ROOT/'infra').rglob('*')):
            if path.is_file() and '.terraform' not in path.parts and path.suffix in ('.tf','.yaml','.tftpl','.b64','.hcl'):
                entry=zipfile.ZipInfo(str(path.relative_to(ROOT/'infra')),date_time=(2026,11,12,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;entry.external_attr=0o644<<16;out.writestr(entry,path.read_bytes())
        entry=zipfile.ZipInfo('release-manifest.json',date_time=(2026,11,12,0,0,0));entry.compress_type=zipfile.ZIP_DEFLATED;out.writestr(entry,(ROOT/'release'/'manifest.json').read_bytes())
    (ROOT/'release'/'SHA256SUMS').write_text(hashlib.sha256(destination.read_bytes()).hexdigest()+'  '+destination.name+'\n')
    print(json.dumps({'archive':str(destination),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'bytes':destination.stat().st_size}))
