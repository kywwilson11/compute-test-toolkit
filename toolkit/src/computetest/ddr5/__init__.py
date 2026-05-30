"""DDR5 RAS / reliability subpackage (Sprint 4.3).

* ``ras`` — the DDR5 RAS control plane (Linux 6.15 EDAC ECS / scrub / mem_repair
  sysfs) as a deterministic device, extending the memory.py EDAC CE/UE reader.
"""
from .ras import (  # noqa: F401
    ECS_THRESHOLDS,
    Ddr5RasError,
    EcsConfig,
    MemRepairRequest,
    MockDdr5Ras,
    ScrubConfig,
)

__all__ = [
    "MockDdr5Ras", "Ddr5RasError", "EcsConfig", "ScrubConfig", "MemRepairRequest",
    "ECS_THRESHOLDS",
]
