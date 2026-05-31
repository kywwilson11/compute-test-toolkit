"""Automotive-Ethernet Time-Sensitive Networking (TSN) subpackage (Sprint 4.2).

* ``phy``  — vendor-agnostic automotive-Ethernet PHY abstraction (SQI, link-up
  time, master/slave role, TDR, PRBS) generalized from the retimer pattern.
* ``gptp`` — 802.1AS gPTP recovered-clock Max|TE| check (Avnu criteria).
* ``gptp_proto`` — 802.1AS protocol conformance (Pdelay, correction field,
  neighborRateRatio, asCapable, BMCA election + failover).
"""
from .cable import (  # noqa: F401
    CableHealth,
    check_cable_tdr,
)
from .frer import (  # noqa: F401
    FrerHealth,
    SequenceRecovery,
    check_frer,
)
from .gptp import (  # noqa: F401
    DEFAULT_LOCK_LIMIT_S,
    DEFAULT_MAX_TE_NS,
    GptpTeHealth,
    check_gptp_time_error,
    emit_gptp_te,
)
from .gptp_hotstandby import (  # noqa: F401
    DEFAULT_HOLDOVER_NS,
    HotStandbyHealth,
    check_hot_standby_failover,
)
from .gptp_proto import (  # noqa: F401
    DEFAULT_CORRECTION_TOL_NS,
    DEFAULT_MAX_TURNAROUND_NS,
    DEFAULT_RATE_RATIO_TOL,
    AnnounceMsg,
    GptpProtocolHealth,
    PdelayExchange,
    as_capable,
    bmca_elect,
    check_gptp_protocol,
    correction_field_ok,
    rate_ratio_valid,
)
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
from .pma import (  # noqa: F401
    DEFAULT_PMA_MAX_LINKUP_MS,
    DEFAULT_PMA_SQI_MIN,
    PmaHealth,
    check_pma_electrical,
)
from .preemption import (  # noqa: F401
    ADD_FRAG_SIZE_BYTES,
    VERIFY_TIME_MAX_MS,
    VERIFY_TIME_MIN_MS,
    Fragment,
    PreemptionHealth,
    check_fragmentation,
    check_preemption,
    min_fragment_bytes,
    run_verify_respond,
    verify_time_valid,
)
from .qbv import (  # noqa: F401
    DEFAULT_MAX_EDGE_JITTER_NS,
    GclEntry,
    GclSchedule,
    QbvHealth,
    check_qbv_gate_timing,
    emit_qbv,
)

__all__ = [
    # phy (Sprint 4.2)
    "EthPhy", "MockPhy", "PhyError", "PhyInfo", "MasterSlave",
    "PhyPrbsPattern", "PhyPrbsResult", "TdrResult",
    "DEFAULT_SQI_MIN", "DEFAULT_MAX_LINKUP_MS",
    # gptp (Sprint 4.2)
    "GptpTeHealth", "check_gptp_time_error", "emit_gptp_te",
    "DEFAULT_MAX_TE_NS", "DEFAULT_LOCK_LIMIT_S",
    # gptp protocol conformance (Sprint 4.2)
    "PdelayExchange", "AnnounceMsg", "GptpProtocolHealth", "bmca_elect",
    "correction_field_ok", "rate_ratio_valid", "as_capable",
    "check_gptp_protocol", "DEFAULT_RATE_RATIO_TOL", "DEFAULT_MAX_TURNAROUND_NS",
    "DEFAULT_CORRECTION_TOL_NS",
    # qbv (Sprint 4.2)
    "GclEntry", "GclSchedule", "QbvHealth", "check_qbv_gate_timing", "emit_qbv",
    "DEFAULT_MAX_EDGE_JITTER_NS",
    # preemption / Clause 99 (Sprint 4.2)
    "Fragment", "PreemptionHealth", "min_fragment_bytes", "verify_time_valid",
    "run_verify_respond", "check_fragmentation", "check_preemption",
    "ADD_FRAG_SIZE_BYTES", "VERIFY_TIME_MIN_MS", "VERIFY_TIME_MAX_MS",
    # frer / 802.1CB (Sprint 4.2)
    "SequenceRecovery", "FrerHealth", "check_frer",
    # hot-standby / 802.1ASdm (Sprint 4.2)
    "HotStandbyHealth", "check_hot_standby_failover", "DEFAULT_HOLDOVER_NS",
    # pma electrical / TC8 L1 (Sprint 4.2)
    "PmaHealth", "check_pma_electrical", "DEFAULT_PMA_MAX_LINKUP_MS",
    "DEFAULT_PMA_SQI_MIN",
    # cable TDR / OABR_CABLE (Sprint 4.2)
    "CableHealth", "check_cable_tdr",
]
