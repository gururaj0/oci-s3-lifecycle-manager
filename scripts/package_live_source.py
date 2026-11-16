"""Secret-free source bundle for the OCI-hosted builder/bootstrap and final deployment."""
from pathlib import Path
import tarfile
ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'release/lcm-live-source.tar.xz'
with tarfile.open(out,'w:xz') as t:
    for directory in ['shared','service','functions','db','scripts']:
        for p in sorted((ROOT/directory).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc',):
                t.add(p,arcname=str(p.relative_to(ROOT)))
    for p in ROOT.glob('requirements*'):t.add(p,arcname=p.name)
print(out)
