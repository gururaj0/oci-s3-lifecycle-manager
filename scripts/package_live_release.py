"""Create a secret-free private Resource Manager template with a pinned image."""
import json,subprocess,sys,zipfile,hashlib,re
from pathlib import Path
root=Path(__file__).resolve().parents[1]
manifest_path=root/'release/live-image-manifest.json'
if not manifest_path.exists():raise SystemExit('Record the audited immutable image manifest before packaging')
manifest=json.loads(manifest_path.read_text())
assert re.fullmatch(r'sha256:[0-9a-f]{64}',manifest['digest'])
subprocess.run([sys.executable,str(root/'scripts/package_live_source.py')],check=True,capture_output=True)
out=root/'release/oci-s3-lcm-0.2.0-live.zip'
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
 for p in sorted((root/'infra/live').iterdir()):
  if p.is_file() and (p.suffix in ('.tf','.yaml','.tftpl') or p.name=='.terraform.lock.hcl'):
   value=p.read_bytes()
   if p.name=='variables.tf':
    text=value.decode()
    for name,v in [('function_image',manifest['tagged_image']),('function_image_digest',manifest['digest'])]:
     pattern=r'(variable "'+name+r'"\s*\{[^}]*default\s*=\s*)""'
     text,n=re.subn(pattern,lambda m:m[1]+json.dumps(v),text);assert n==1
    value=text.encode()
   z.writestr(p.name,value)
 z.write(root/'release/lcm-live-source.tar.xz','lcm-live-source.tar.xz')
 z.write(manifest_path,'live-image-manifest.json')
print(json.dumps({'archive':out.name,'sha256':hashlib.sha256(out.read_bytes()).hexdigest(),'image_digest':manifest['digest'],'source':'authenticated private Resource Manager template'}))
