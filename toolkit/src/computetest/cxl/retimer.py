"""
CXL link-extension (retimer) check (Sprint 4.4).

A CXL link extender is a tested device class in the CXL Compliance Program, and
it *is* a PCIe-style retimer — so this reuses the retimer abstraction
(``pcie/retimer/base.py``) verbatim (eye, EQ, junction temperature, loopback,
PRBS BIST) and adds CXL-context lane thresholds plus an assertion that the
retimer is the expected part (``info()`` vs the fixture map).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..pcie.retimer.base import Retimer, eye_quality_verdict

# CXL runs at 32/64 GT/s; reuse the retimer's eye-quality floor as the default.
DEFAULT_CXL_EYE_UI_MIN = 0.25
DEFAULT_CXL_EYE_MV_MIN = 30.0


@dataclass
class CxlRetimerHealth:
    """CXL retimer verdict (identity + per-lane eye + temperature)."""
    part_number: str
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"CXL retimer {self.part_number} -> {state}"

    def to_dict(self) -> dict:
        return {"part_number": self.part_number, "checks": self.checks, "ok": self.ok}


def check_cxl_retimer(retimer: Retimer, *, expect_part: str,
                      lanes: Sequence[int] | None = None,
                      ui_min: float = DEFAULT_CXL_EYE_UI_MIN,
                      mv_min: float = DEFAULT_CXL_EYE_MV_MIN) -> CxlRetimerHealth:
    """Verify a CXL link-extension retimer: it is the expected part, every lane's
    eye clears the CXL thresholds, and junction temperature is in range."""
    info = retimer.info()
    lane_list = list(range(info.lanes)) if lanes is None else list(lanes)
    # Reject an empty lane set: all([]) is vacuously True, so an empty list would
    # report all_lanes_eye_open=True having probed zero eyes. Guard with bool(...)
    # like gmsl/shmoo.py and ddr5/eye.py do for the same "all eyes open" shape.
    eyes_ok = bool(lane_list) and all(
        eye_quality_verdict(retimer.read_eye(lane), ui_min=ui_min, mv_min=mv_min)
        for lane in lane_list)
    checks = {
        "part_identity_ok": info.part_number == expect_part,
        "temperature_ok": retimer.temperature_ok(),
        "all_lanes_eye_open": eyes_ok,
    }
    return CxlRetimerHealth(part_number=info.part_number, checks=checks)
