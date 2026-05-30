"""
DDR5 per-DQ / per-DQS eye + DFE check (Sprint 4.3).

Reuses the retimer ``EyeMeasurement`` / ``EqLevels`` dataclasses and
``eye_quality_verdict`` (from ``pcie/retimer/base.py``) for the per-DQ eye at the
DRAM ball, so DDR5 SI eye data rides the same portable shape and verdict as PCIe
retimer and GMSL EOM eye data. Eye/DFE values are ingested from a scope-vendor
DDR5 compliance app (Keysight D9050DDRC — NOT the N8841A CAUI-4 app).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..pcie.retimer.base import EqLevels, EyeMeasurement, eye_quality_verdict

# DDR5 DRAM-ball eye thresholds (per JESD79-5 methodology; tighter than PCIe).
DEFAULT_DDR5_EYE_UI_MIN = 0.20
DEFAULT_DDR5_EYE_MV_MIN = 50.0


@dataclass
class Ddr5EyeHealth:
    """Per-DQ eye-margin verdict mapped onto the portable retimer EyeMeasurement."""
    dq: int
    eye: EyeMeasurement
    eq: EqLevels
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        state = "OK" if self.ok else "FAIL(eye)"
        return (f"DDR5 DQ{self.dq} eye {self.eye.eye_ui:.3f}UI/"
                f"{self.eye.eye_mv:.1f}mV -> {state}")

    def to_dict(self) -> dict:
        return {"dq": self.dq, "eye": self.eye.to_dict(), "eq": self.eq.to_dict(),
                "checks": self.checks, "ok": self.ok}


def check_dq_eye(*, dq: int, eye_ui: float, eye_mv: float,
                 dfe_taps: Sequence[float],
                 ui_min: float = DEFAULT_DDR5_EYE_UI_MIN,
                 mv_min: float = DEFAULT_DDR5_EYE_MV_MIN) -> Ddr5EyeHealth:
    """Map a measured per-DQ eye + DFE taps onto the retimer EyeMeasurement /
    EqLevels and verdict it with DDR5 thresholds via ``eye_quality_verdict``."""
    eye = EyeMeasurement(lane=dq, eye_ui=eye_ui, eye_mv=eye_mv,
                         height_mv=eye_mv, width_ui=eye_ui)
    eq = EqLevels(lane=dq, dfe_taps=list(dfe_taps))
    passed = eye_quality_verdict(eye, ui_min=ui_min, mv_min=mv_min)
    return Ddr5EyeHealth(dq=dq, eye=eye, eq=eq, checks={"eye_open": passed})
