"""
Automotive single-pair cable TDR check — OPEN Alliance OABR_CABLE_01/02
(Sprint 4.2).

A thin verdict over ``EthPhy.run_tdr()``, which already returns the same
status/faults/distance shape as ``ethernet.py``'s ethtool ``--cable-test-tdr``
path. An open or short on the single pair (with a distance-to-fault) fails the
cable check.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .phy import EthPhy


@dataclass
class CableHealth:
    """Single-pair cable TDR verdict."""
    status: str                      # "ok" | "fault" | "skipped"
    faults: list[dict]
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    @property
    def fault_distance_m(self) -> float | None:
        return self.faults[0]["distance_m"] if self.faults else None

    def summary(self) -> str:
        state = "OK" if self.ok else "FAIL"
        dist = "" if self.fault_distance_m is None else f" @{self.fault_distance_m}m"
        return f"cable TDR status={self.status}{dist} -> {state}"

    def to_dict(self) -> dict:
        return {"status": self.status, "faults": list(self.faults),
                "fault_distance_m": self.fault_distance_m,
                "checks": self.checks, "ok": self.ok}


def check_cable_tdr(phy: EthPhy) -> CableHealth:
    """Run a cable TDR on the PHY's single pair and verdict it: any open/short
    fault fails. A ``skipped`` result (PHY/driver without TDR support) does not
    false-fail a good link."""
    tdr = phy.run_tdr()
    checks = {"no_open_or_short": tdr.status != "fault"}
    return CableHealth(status=tdr.status, faults=list(tdr.faults), checks=checks)
