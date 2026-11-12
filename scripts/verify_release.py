"""Offline secret/path/manifest checks for the Resource Manager artifact."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import zipfile

root=Path(__file__).resolve().parents[1]
archive=root/'release/oci-s3-lcm-0.1.0-mock.zip'
with zipfile.ZipFile(archive) as z:
    names=z.namelist()
    assert not any(n.startswith('/') or '..' in Path(n).parts for n in names)
    for n in names:
        data=z.read(n)
        assert b'-----BEGIN PRIVATE KEY-----' not in data
        assert b'-----BEGIN RSA PRIVATE KEY-----' not in data
    raw=base64.b64decode(z.read('assets/application.tar.xz.b64'))
    manifest=json.loads(z.read('release-manifest.json'))
    assert hashlib.sha256(raw).hexdigest()==manifest['application_sha256']
    with tarfile.open(fileobj=io.BytesIO(raw)) as t:
        for entry in t.getmembers():
            assert not entry.name.startswith('/') and '..' not in Path(entry.name).parts
            assert not entry.issym() and not entry.islnk()
            if entry.isfile():
                data=t.extractfile(entry).read()
                assert b'-----BEGIN PRIVATE KEY-----' not in data
                assert b'-----BEGIN RSA PRIVATE KEY-----' not in data
                assert not any(n in entry.name for n in ['.local/','.oci/','ledger.db','mock-cloud.db','__pycache__'])
    assert manifest['profile']=='mock' and manifest['production_ready'] is False
print(json.dumps({'artifact':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'embedded_manifest_verified':True,'private_key_scan':'PASS','path_safety':'PASS','mode':'mock'},indent=2))
