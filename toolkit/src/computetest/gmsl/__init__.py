"""GMSL camera-link domain subpackage.

* ``video``  — the legacy ``check_gmsl`` / ``check_deserializer`` surface: link
  lock + a v4l2 frame grab proving the sensor->serializer->coax->deserializer->
  CSI-2->SoC path (re-exported here so ``from computetest import gmsl`` and
  ``gmsl.check_gmsl(...)`` keep working unchanged after the package split).
* ``serdes`` — Sprint 4.1 vendor-agnostic SerDes link abstraction: negotiated
  mode, pre/post-FEC PRBS margin, EOM eye, and safety error counters.
"""
from .checks import (  # noqa: F401
    DEFAULT_MAX_LOCK_MS,
    SerDesLinkHealth,
    check_serdes_link,
)
from .serdes import (  # noqa: F401
    DEFAULT_BIST_DURATION_S,
    DEFAULT_EOM_MV_MIN,
    DEFAULT_EOM_UI_MIN,
    EomReading,
    ErrorCounters,
    FecStats,
    GmslMode,
    GmslPrbsPattern,
    LinkDirection,
    LinkLock,
    MockSerDes,
    PrbsResult,
    SerDesError,
    SerDesInfo,
    SerDesLink,
    SerDesRole,
)
from .video import (  # noqa: F401
    GmslDeserHealth,
    GmslHealth,
    check_deserializer,
    check_gmsl,
)

__all__ = [
    # video (legacy surface)
    "GmslHealth", "GmslDeserHealth", "check_gmsl", "check_deserializer",
    # serdes (Sprint 4.1)
    "SerDesLink", "MockSerDes", "SerDesError", "SerDesInfo", "SerDesRole",
    "GmslMode", "LinkDirection", "GmslPrbsPattern", "LinkLock", "EomReading",
    "FecStats", "PrbsResult", "ErrorCounters",
    # checks (Sprint 4.1)
    "SerDesLinkHealth", "check_serdes_link",
]
