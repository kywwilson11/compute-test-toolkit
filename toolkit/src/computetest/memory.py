"""
DRAM/ECC health via EDAC — the memory analog of AER. Reads the kernel's EDAC counters
(/sys/devices/system/edac/mc/mc*/) for corrected (CE) and uncorrectable (UE) errors,
per-DIMM, and maps them to the DIMM silkscreen label (so a failure is an actionable
RMA: "DIMM_A1", not "the board has memory errors"). Optionally drives a stressapptest
soak and re-reads. Simulated off Linux.

Pass/fail: zero UE (any UE = fail), bounded total CE, and no single DIMM above a
per-DIMM CE limit (a hot DIMM even within the total budget is suspect). Run it HOT —
DRAM marginality is strongly temperature/voltage dependent.
"""
from __future__ import annotations

import glob
import os
import subprocess
from dataclasses import dataclass, field

from .backend import mock_mode

EDAC = "/sys/devices/system/edac/mc"


@dataclass
class MemoryHealth:
    controllers: int
    total_ce: int                       # corrected errors
    total_ue: int                       # uncorrectable errors
    per_dimm: dict                      # {label: ce_count}
    checks: dict[str, bool] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    @property
    def worst_dimm(self) -> str | None:
        # lambda (vs dict.get) returns int, not int | None, so mypy types max() correctly.
        return max(self.per_dimm, key=lambda d: self.per_dimm[d]) if self.per_dimm else None

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        worst = f" worst={self.worst_dimm}({self.per_dimm.get(self.worst_dimm)})" \
            if self.worst_dimm and self.per_dimm.get(self.worst_dimm) else ""
        return (f"memory: {self.controllers} mc, CE={self.total_ce} UE={self.total_ue}"
                f"{worst} -> {state}")

    def to_dict(self) -> dict:
        return {"controllers": self.controllers, "total_ce": self.total_ce,
                "total_ue": self.total_ue, "per_dimm": self.per_dimm,
                "checks": self.checks, "history": self.history, "ok": self.ok}


def _limits(total_ce: int, total_ue: int, per_dimm: dict,
            max_ce_total: int, max_ce_per_dimm: int) -> dict[str, bool]:
    worst = max(per_dimm.values()) if per_dimm else 0
    # If the controller reports corrected errors but no per-DIMM attribution was
    # available (e.g. a legacy-EDAC driver exposing only csrow/channel counters we
    # could not map to a DIMM), the concentration check would silently pass. Fail it
    # instead so a hot stick can't hide behind missing per-DIMM data.
    concentration_ok = worst <= max_ce_per_dimm and not (total_ce > 0 and not per_dimm)
    return {
        "no_uncorrectable": total_ue == 0,
        f"ce_total<={max_ce_total}": total_ce <= max_ce_total,
        f"no_ce_concentration(<={max_ce_per_dimm}/dimm)": concentration_ok,
    }


def _read_edac() -> tuple[int, int, int, dict]:  # pragma: no cover - real-hw path
    mcs = sorted(glob.glob(f"{EDAC}/mc[0-9]*"))
    total_ce = total_ue = 0
    per_dimm: dict[str, int] = {}
    for mc in mcs:
        total_ce += _read_int(f"{mc}/ce_count")
        total_ue += _read_int(f"{mc}/ue_count")
        for dimm in sorted(glob.glob(f"{mc}/dimm[0-9]*") + glob.glob(f"{mc}/rank[0-9]*")):
            label = _read_str(f"{dimm}/dimm_label") or os.path.basename(dimm)
            per_dimm[label] = per_dimm.get(label, 0) + _read_int(f"{dimm}/dimm_ce_count")
        # Legacy EDAC ABI: drivers that predate the dimm* devices expose per-channel
        # counters under csrowY/ (chZ_ce_count + chZ_dimm_label). Fall back to those so
        # per-DIMM attribution still works (Linux Documentation/admin-guide/ras.rst).
        for csrow in sorted(glob.glob(f"{mc}/csrow[0-9]*")):
            for ce in sorted(glob.glob(f"{csrow}/ch[0-9]*_ce_count")):
                ch = os.path.basename(ce)[: -len("_ce_count")]
                label = (_read_str(f"{csrow}/{ch}_dimm_label")
                         or f"{os.path.basename(csrow)}/{ch}")
                per_dimm[label] = per_dimm.get(label, 0) + _read_int(ce)
    return len(mcs), total_ce, total_ue, per_dimm


def check_memory(*, mock: bool | None = None, max_ce_total: int = 100,
                 max_ce_per_dimm: int = 20, fault: str | None = None) -> MemoryHealth:
    """Read EDAC counters and apply limits. ``fault`` (mock only) injects 'ue' or 'ce'."""
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        controllers = 1
        per_dimm = {"DIMM_A1": 0, "DIMM_A2": 0, "DIMM_B1": 0, "DIMM_B2": 0}
        total_ue = 0
        if fault == "ue":
            total_ue = 2
            per_dimm["DIMM_B1"] = 3
        elif fault == "ce":
            per_dimm["DIMM_A1"] = 150        # a hot DIMM
        total_ce = sum(per_dimm.values())
    else:  # pragma: no cover - real-hw path
        controllers, total_ce, total_ue, per_dimm = _read_edac()

    checks = _limits(total_ce, total_ue, per_dimm, max_ce_total, max_ce_per_dimm)
    history = []
    worst = max(per_dimm, key=lambda d: per_dimm[d]) if per_dimm else None
    if worst and per_dimm[worst] > 0:
        history.append(f"{worst} has {per_dimm[worst]} CE (swap that stick)")
    return MemoryHealth(controllers, total_ce, total_ue, per_dimm, checks, history)


def stress_memory(seconds: int = 60, mb: int | None = None, *, mock: bool | None = None) -> bool:
    """Run a stressapptest soak (provokes errors EDAC then counts). Returns success."""
    if (mock_mode() if mock is None else mock):
        return True
    return _real_stress_memory(seconds, mb)  # pragma: no cover - real-hw path


def _real_stress_memory(seconds: int, mb: int | None) -> bool:  # pragma: no cover - real-hw path
    cmd = ["stressapptest", "-s", str(seconds), "-W"]
    if mb:
        cmd += ["-M", str(mb)]
    # +30 s wall-clock slack for the tool's own teardown; a runaway run is treated as fail.
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=seconds + 30).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _read_int(path: str) -> int:  # pragma: no cover - real-hw path
    try:
        with open(path) as fh:
            return int(fh.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def _read_str(path: str) -> str:  # pragma: no cover - real-hw path
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""
