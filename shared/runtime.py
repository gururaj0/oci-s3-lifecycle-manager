from functools import lru_cache
from shared.config import Config
from shared.ledger import Ledger
from shared.mock import MockCloud
from shared.lifecycle import Lifecycle

@lru_cache(maxsize=1)
def engine():
    cfg = Config.env()
    if cfg.mode=='oci':
        from shared.oracle_ledger import OracleLedger
        from shared.oci_cloud import OCICloud
        db=OracleLedger(cfg)
        return Lifecycle(cfg,db,OCICloud(cfg,db))
    return Lifecycle(cfg,Ledger(cfg.state_dir / 'ledger.db'),MockCloud(cfg))
