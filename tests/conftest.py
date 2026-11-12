import pytest
from fastapi.testclient import TestClient
from shared.config import Config
from shared.ledger import Ledger
from shared.mock import MockCloud
from shared.lifecycle import Lifecycle
from service.app import create_app

@pytest.fixture
def env(tmp_path):
    cfg=Config(state_dir=tmp_path,worker_interval=0.1,rate_limit=1000)
    cloud=MockCloud(cfg)
    e=Lifecycle(cfg,Ledger(tmp_path/'ledger.db'),cloud)
    e.reconcile()
    app=create_app(e,worker=False)
    with TestClient(app) as client:
        yield e,cloud,app,client

@pytest.fixture
def caller(env):
    app=env[2]
    return {'Authorization':'Bearer '+app.state.auth.mint_demo('demo:alice')}
