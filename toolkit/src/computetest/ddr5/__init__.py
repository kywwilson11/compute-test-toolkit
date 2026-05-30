"""DDR5 RAS / reliability subpackage (Sprint 4.3).

* ``ras`` — the DDR5 RAS control plane (Linux 6.15 EDAC ECS / scrub / mem_repair
  sysfs) as a deterministic device, extending the memory.py EDAC CE/UE reader.
* ``ecc`` — on-die ECC + EDAC reporting verification via EINJ (CE/UE).
"""
from .ecc import (  # noqa: F401
    EccReportHealth,
    check_ce_reporting,
    check_ue_reporting,
)
from .ecs import (  # noqa: F401
    DEFAULT_MAX_SCRUB_TIME_S,
    EcsHealth,
    check_ecs,
)
from .ras import (  # noqa: F401
    ECS_THRESHOLDS,
    Ddr5RasError,
    EcsConfig,
    MemRepairRequest,
    MockDdr5Ras,
    ScrubConfig,
)

__all__ = [
    # ras (Sprint 4.3)
    "MockDdr5Ras", "Ddr5RasError", "EcsConfig", "ScrubConfig", "MemRepairRequest",
    "ECS_THRESHOLDS",
    # ecc (Sprint 4.3)
    "EccReportHealth", "check_ce_reporting", "check_ue_reporting",
    # ecs (Sprint 4.3)
    "EcsHealth", "check_ecs", "DEFAULT_MAX_SCRUB_TIME_S",
]
