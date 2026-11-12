from functools import lru_cache
from shared.config import Config
from shared.ledger import Ledger
from shared.mock import MockCloud
from shared.lifecycle import Lifecycle

@lru_cache(maxsize=1)
def engine():
    cfg = Config.env()
    return Lifecycle(cfg,Ledger(cfg.state_dir / 'ledger.db'),MockCloud(cfg))
