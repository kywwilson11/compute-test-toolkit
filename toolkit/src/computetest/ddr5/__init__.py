"""DDR5 RAS / reliability subpackage (Sprint 4.3).

* ``ras`` — the DDR5 RAS control plane (Linux 6.15 EDAC ECS / scrub / mem_repair
  sysfs) as a deterministic device, extending the memory.py EDAC CE/UE reader.
* ``ecc`` — on-die ECC + EDAC reporting verification via EINJ (CE/UE).
"""
from .crc import (  # noqa: F401
    DEFAULT_MAX_CRC_RETRIES,
    CrcHealth,
    check_crc_parity,
)
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
from .eye import (  # noqa: F401
    DEFAULT_DDR5_EYE_MV_MIN,
    DEFAULT_DDR5_EYE_UI_MIN,
    Ddr5EyeHealth,
    check_dq_eye,
)
from .lmt import (  # noqa: F401
    DqMargin,
    training_to_lmt_records,
)
from .ppr import (  # noqa: F401
    PprHealth,
    check_ppr,
)
from .ras import (  # noqa: F401
    ECS_THRESHOLDS,
    Ddr5RasError,
    EcsConfig,
    MemRepairRequest,
    MockDdr5Ras,
    ScrubConfig,
)
from .rfm import (  # noqa: F401
    RfmPracHealth,
    check_rfm_prac,
)

__all__ = [
    # crc (Sprint 4.3)
    "CrcHealth", "check_crc_parity", "DEFAULT_MAX_CRC_RETRIES",
    # ras (Sprint 4.3)
    "MockDdr5Ras", "Ddr5RasError", "EcsConfig", "ScrubConfig", "MemRepairRequest",
    "ECS_THRESHOLDS",
    # ecc (Sprint 4.3)
    "EccReportHealth", "check_ce_reporting", "check_ue_reporting",
    # ecs (Sprint 4.3)
    "EcsHealth", "check_ecs", "DEFAULT_MAX_SCRUB_TIME_S",
    # ppr (Sprint 4.3)
    "PprHealth", "check_ppr",
    # lmt adapter (Sprint 4.3)
    "DqMargin", "training_to_lmt_records",
    # eye (Sprint 4.3)
    "Ddr5EyeHealth", "check_dq_eye", "DEFAULT_DDR5_EYE_UI_MIN",
    "DEFAULT_DDR5_EYE_MV_MIN",
    # rfm/prac (Sprint 4.3)
    "RfmPracHealth", "check_rfm_prac",
]
