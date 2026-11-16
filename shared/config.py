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
    db_user: str = 'LCM'
    db_secret_id: str = ''
    workload: str = 'instance'
    compartment_id: str = ''
    scheduler_compartment_id: str = ''
    secret_compartment_id: str = ''
    vault_id: str = ''
    vault_key_id: str = ''
    namespace: str = ''
    tenancy_id: str = ''
    resource_tags: dict = field(default_factory=dict)
    service_users: dict = field(default_factory=dict)
    functions: dict = field(default_factory=dict)
    integration_gates: dict = field(default_factory=dict)
    pool_size: int = 2
    ttl_min: int = 300
    ttl_max: int = 3600
    replay_seconds: int = 60
    cleanup_stale: int = 120
    audit_retention_days: int = 90
    overdue_threshold: int = 60
    worker_interval: float = 2.0
    public_url: str = ''
    live_redirect_url: str = ''
    rate_limit: int = 60
    identities: dict = field(default_factory=dict)
    profile_scopes: dict = field(default_factory=dict)
    oauth: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=lambda: {
        'sales-read': {'bucket': 'sales-demo', 'operations': ['LIST', 'GET'], 'version': 1},
        'sales-write': {'bucket': 'sales-demo', 'operations': ['LIST', 'GET', 'PUT'], 'version': 1},
    })

    @classmethod
    def env(cls):
        cfg = cls()
        path = os.getenv('LCM_CONFIG')
        raw=os.getenv('LCM_CONFIG_JSON') or (Path(path).read_text() if path else '')
        if raw:
            for k, v in json.loads(raw).items():
                if k not in cls.__dataclass_fields__:
                    raise ValueError('Unknown configuration field: ' + k)
                setattr(cfg, k, Path(v) if k == 'state_dir' else v)
        cfg.mode = os.getenv('LCM_MODE', cfg.mode)
        cfg.state_dir = Path(os.getenv('LCM_STATE_DIR', str(cfg.state_dir)))
        cfg.public_url = os.getenv('LCM_PUBLIC_URL', cfg.public_url).rstrip('/')
        cfg.live_redirect_url = os.getenv('LCM_LIVE_DEMO_URL', cfg.live_redirect_url).rstrip('/')
        if cfg.live_redirect_url and not cfg.live_redirect_url.startswith('https://'):
            raise ValueError('The live demo redirect requires HTTPS')
        if cfg.mode not in ('mock', 'oci'):
            raise ValueError('LCM_MODE must be mock or oci')
        cfg.workload=os.getenv('LCM_WORKLOAD',cfg.workload)
        if os.getenv('LCM_FUNCTIONS'):cfg.functions=json.loads(os.environ['LCM_FUNCTIONS'])
        if cfg.mode == 'oci':
            required=('database_url','db_secret_id','compartment_id','scheduler_compartment_id','secret_compartment_id','vault_id','vault_key_id','namespace','issuer','audience','jwks_url')
            if any(not getattr(cfg,k) for k in required) or not cfg.service_users:
                raise RuntimeError('Live OCI configuration is incomplete')
            if not cfg.jwks_url.startswith('https://') or not cfg.identities:
                raise RuntimeError('Live OCI requires explicit JWT trust and subject mappings')
        if cfg.ttl_min < 300 or cfg.ttl_max > 3600 or cfg.replay_seconds > 60:
            raise ValueError('TTL and replay bounds exceed PRD limits')
        return cfg
