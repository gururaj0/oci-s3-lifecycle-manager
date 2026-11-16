"""Verify archive boundaries and absence of deployer credentials in the live package."""
import io,json,re,tarfile,zipfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
archive=root/'release/oci-s3-lcm-0.2.0-live.zip'
with zipfile.ZipFile(archive) as z:
 assert {'schema.yaml','runtime.tf','functions.tf','release.tf','bootstrap.py.tftpl','lcm-live-source.tar.xz'}<=set(z.namelist())
 files={n:z.read(n) for n in z.namelist()}
 with tarfile.open(fileobj=io.BytesIO(files['lcm-live-source.tar.xz'])) as t:
  for member in t.getmembers():
   assert member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts
   files['source/'+member.name]=t.extractfile(member).read()
 for name,data in files.items():
  assert '.local/' not in name and '.tfstate' not in name and not name.endswith(('.pem','.key'))
  if name.endswith('.xz'):continue
  assert not re.search(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----(?:\r?\n|\\n)[A-Za-z0-9+/=]{20,}',data),name
  assert not re.search(rb'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}',data),name
print(json.dumps({'live_archive_verified':True,'entries_checked':len(files),'private_keys_or_bearer_tokens_found':False}))
