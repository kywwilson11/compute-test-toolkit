"""
CXL link bring-up / alternate-protocol negotiation check (Sprint 4.4).

Extends the ``linkstate.check_link`` expected-speed/width pattern with CXL
awareness: the Flex Bus must negotiate the CXL alternate protocol (not silently
fall back to plain PCIe), at the expected speed (32/64 GT/s) and width, in the
expected flit mode (68B vs 256B standard vs 256B latency-optimized). A silent
degrade to 68B or to plain PCIe is a failure, not a pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FlitMode(str, Enum):
    PCIE_68B = "68B"                         # PCIe / CXL 1.1 flit
    CXL_256B_STD = "256B-standard"
    CXL_256B_LATENCY_OPT = "256B-latency-optimized"


@dataclass
class CxlLinkHealth:
    """CXL link negotiation verdict."""
    speed_gt: int
    width: int
    flit_mode: str
    cxl_negotiated: bool
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        proto = "CXL" if self.cxl_negotiated else "PCIe-fallback"
        return (f"CXL link {proto} {self.speed_gt}GT/s x{self.width} "
                f"flit={self.flit_mode} -> {state}")

    def to_dict(self) -> dict:
        return {"speed_gt": self.speed_gt, "width": self.width,
                "flit_mode": self.flit_mode, "cxl_negotiated": self.cxl_negotiated,
                "checks": self.checks, "ok": self.ok}


def check_cxl_link(*, speed_gt: int, width: int, flit_mode: FlitMode,
                   cxl_negotiated: bool, expect_speed_gt: int = 32,
                   expect_width: int = 16,
                   expect_flit: FlitMode = FlitMode.CXL_256B_STD) -> CxlLinkHealth:
    """Verify the negotiated CXL link state (from the Flex Bus DVSEC + PCIe link
    status). A silent fallback to plain PCIe (``cxl_negotiated`` False) or a
    silent degrade to a lower flit mode / speed / width is a failure."""
    checks = {
        "cxl_alt_protocol_negotiated": cxl_negotiated,
        "speed_ok": speed_gt >= expect_speed_gt,
        "width_ok": width >= expect_width,
        "flit_mode_ok": flit_mode == expect_flit,
    }
    return CxlLinkHealth(speed_gt=speed_gt, width=width, flit_mode=flit_mode.value,
                         cxl_negotiated=cxl_negotiated, checks=checks)
