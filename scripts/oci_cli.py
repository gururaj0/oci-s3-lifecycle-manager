"""Shared OCI CLI helper using the operator's existing configuration."""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def oci(*args):
    result = subprocess.run(['oci', *args, '--output', 'json'], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)['data']
