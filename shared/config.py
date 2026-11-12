from dataclasses import dataclass, field
from pathlib import Path
import json
import os

@dataclass
class Config:
    mode: str = 'mock'
    state_dir: Path = field(default_factory=lambda: Path(os.getenv('LCM_STATE_DIR', '.local')))
    region: str = 'us-ashburn-1'
    deployment_id: str = 'lcm-demo-v1'
    issuer: str = 'urn:oci-lcm:mock-demo'
    audience: str = 'oci-lcm'
    jwks_url: str = ''
    database_url: str = ''
    pool_size: int = 2
    ttl_min: int = 300
    ttl_max: int = 3600
    replay_seconds: int = 60
    cleanup_stale: int = 120
    overdue_threshold: int = 60
    worker_interval: float = 2.0
    public_url: str = ''
    rate_limit: int = 60
    identities: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=lambda: {
        'sales-read': {'bucket': 'sales-demo', 'operations': ['LIST', 'GET'], 'version': 1},
        'sales-write': {'bucket': 'sales-demo', 'operations': ['LIST', 'GET', 'PUT'], 'version': 1},
    })

    @classmethod
    def env(cls):
        cfg = cls()
        path = os.getenv('LCM_CONFIG')
        if path:
            for k, v in json.loads(Path(path).read_text()).items():
                if k not in cls.__dataclass_fields__:
                    raise ValueError('Unknown configuration field: ' + k)
                setattr(cfg, k, Path(v) if k == 'state_dir' else v)
        cfg.mode = os.getenv('LCM_MODE', cfg.mode)
        cfg.state_dir = Path(os.getenv('LCM_STATE_DIR', str(cfg.state_dir)))
        cfg.public_url = os.getenv('LCM_PUBLIC_URL', cfg.public_url).rstrip('/')
        if cfg.mode not in ('mock', 'oci'):
            raise ValueError('LCM_MODE must be mock or oci')
        if cfg.mode == 'oci':
            # M0 is an explicit development gate; an apply is never readiness evidence.
            raise RuntimeError('OCI issuance disabled: M0 Unity, IAM, event and schedule capability gates require live signoff. See docs/acceptance.md')
        if cfg.ttl_min < 300 or cfg.ttl_max > 3600 or cfg.replay_seconds > 60:
            raise ValueError('TTL and replay bounds exceed PRD limits')
        return cfg
