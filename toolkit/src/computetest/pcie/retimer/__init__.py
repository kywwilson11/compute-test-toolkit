"""Vendor-agnostic PCIe retimer telemetry.

Exports the ``Retimer`` ABC + result dataclasses + the ``MockRetimer`` test
double + the ``AriesRetimer`` Astera Labs boundary stub.

See ``base.py`` for the contract; ``aries.py`` for the SDK-gated impl.
"""

from .aries import AriesRetimer
from .base import (
    BISTResult,
    EqLevels,
    EyeMeasurement,
    LaneStatus,
    LoopbackSide,
    MockRetimer,
    PRBSPattern,
    Retimer,
    RetimerError,
    RetimerInfo,
    eye_quality_verdict,
)

__all__ = [
    "AriesRetimer", "BISTResult", "EqLevels", "EyeMeasurement",
    "LaneStatus", "LoopbackSide", "MockRetimer", "PRBSPattern", "Retimer",
    "RetimerError", "RetimerInfo", "eye_quality_verdict",
]
