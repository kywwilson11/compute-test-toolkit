"""Automotive-Ethernet Time-Sensitive Networking (TSN) subpackage (Sprint 4.2).

* ``phy`` — vendor-agnostic automotive-Ethernet PHY abstraction (SQI, link-up
  time, master/slave role, TDR, PRBS) generalized from the retimer pattern.
"""
from .phy import (  # noqa: F401
    DEFAULT_MAX_LINKUP_MS,
    DEFAULT_SQI_MIN,
    EthPhy,
    MasterSlave,
    MockPhy,
    PhyError,
    PhyInfo,
    PhyPrbsPattern,
    PhyPrbsResult,
    TdrResult,
)

__all__ = [
    "EthPhy", "MockPhy", "PhyError", "PhyInfo", "MasterSlave",
    "PhyPrbsPattern", "PhyPrbsResult", "TdrResult",
    "DEFAULT_SQI_MIN", "DEFAULT_MAX_LINKUP_MS",
]
