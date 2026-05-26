"""
GPU check: ECC (volatile vs aggregate), temperature, decoded throttle reasons, PCIe
replay, row-remapping, and kernel XID errors — the GPU's own telemetry, much of which
points at a PCIe/power/thermal problem. Wraps `nvidia-smi` + `dmesg` on real Linux;
simulated otherwise. For full qualification also run `dcgmi diag -r 3` and a `gpu-burn`
thermal soak.

Pass/fail uses VOLATILE counts (errors since the last reset — i.e. during THIS test).
Lifetime AGGREGATE ECC and already-remapped rows are reported as RMA *history*, not a
fail (a returned/used part), so the line can flag suspicious stock without scrapping a
good board on its lifetime counters.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from . import dmesg
from .backend import mock_mode

# clocks_throttle_reasons bits that indicate a real problem (not idle/app/sync-boost).
_THROTTLE_BAD = {0x8: "hw_slowdown", 0x20: "sw_thermal", 0x40: "hw_thermal",
                 0x80: "hw_power_brake"}
# XID codes that indicate a hardware fault (subset; see Guide A's XID table).
_XID_CRITICAL = {48: "DBE ECC", 63: "row-remap pending", 64: "row-remap failure",
                 74: "NVLink error", 79: "GPU fell off the bus", 92: "contained ECC",
                 94: "contained ECC", 95: "uncontained ECC"}
_XID_RE = re.compile(r"NVRM:\s*Xid\s*\([^)]*\):\s*(\d+)")


@dataclass
class GpuHealth:
    index: int
    name: str
    link_gen: int
    link_width: int
    metrics: dict
    checks: dict[str, bool] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)   # RMA-history flags (not failures)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        m = self.metrics
        thr = ",".join(m.get("throttle_reasons", [])) or "-"
        xids = ",".join(str(c) for c in m.get("xid_errors", {})) or "-"
        hist = f"  history[{';'.join(self.history)}]" if self.history else ""
        return (f"GPU{self.index} {self.name} Gen{self.link_gen}x{self.link_width} "
                f"temp={m.get('temp')}C ecc_unc_vol={m.get('ecc_uncorrected_volatile')} "
                f"replay={m.get('replay_count')} throttle={thr} xid={xids} -> {state}{hist}")

    def to_dict(self) -> dict:
        return {"index": self.index, "name": self.name,
                "link": f"Gen{self.link_gen}x{self.link_width}", "metrics": self.metrics,
                "checks": self.checks, "history": self.history, "ok": self.ok}


def _apply_limits(m: dict, max_temp_c: int, expect_gen: int, expect_width: int,
                  gen: int, width: int, replay_limit: int) -> dict[str, bool]:
    bad_xid = [c for c in m.get("xid_errors", {}) if c in _XID_CRITICAL]
    return {
        "ecc_volatile_uncorrected==0": m.get("ecc_uncorrected_volatile", 0) == 0,
        f"temp<={max_temp_c}": 0 < m.get("temp", 0) <= max_temp_c,
        "no_bad_throttle": not m.get("throttle_reasons"),
        f"replay<{replay_limit}": m.get("replay_count", 0) < replay_limit,
        "link_gen_ok": gen >= expect_gen,
        "link_width_ok": width >= expect_width,
        "no_row_remap_failure": not m.get("row_remap_failure") and not m.get("row_remap_pending"),
        "no_critical_xid": not bad_xid,
    }


def _history(m: dict) -> list[str]:
    h = []
    if m.get("ecc_uncorrected_aggregate", 0) > 0:
        h.append(f"aggregate DBE={m['ecc_uncorrected_aggregate']}")
    if m.get("row_remap_count", 0) > 0:
        h.append(f"remapped_rows={m['row_remap_count']}")
    return h


def _mock_metrics(index: int) -> dict:
    bad = index == 99
    return {
        "temp": 95 if bad else 62,
        "ecc_corrected_volatile": 0,
        "ecc_uncorrected_volatile": 3 if bad else 0,
        "ecc_corrected_aggregate": 12,
        "ecc_uncorrected_aggregate": 5 if bad else 0,
        "replay_count": 500 if bad else 0,
        "throttle_reasons": ["hw_thermal"] if bad else [],
        "power_w": 290,
        "row_remap_pending": 0,
        "row_remap_failure": 1 if bad else 0,
        "row_remap_count": 8 if bad else 0,
        "xid_errors": {},   # XIDs come from dmesg; injected via dmesg_reader in tests
    }


def check_gpu(index: int = 0, *, mock: bool | None = None, max_temp_c: int = 85,
              expect_gen: int = 4, expect_width: int = 16, replay_limit: int = 100,
              dmesg_reader=None) -> GpuHealth:
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        m = _mock_metrics(index)
        m["xid_errors"] = _scan_xids(dmesg_reader) if dmesg_reader else {}
        gen, width = (3 if index == 99 else 4), (8 if index == 99 else 16)
        checks = _apply_limits(m, max_temp_c, expect_gen, expect_width, gen, width, replay_limit)
        return GpuHealth(index, "Mock RTX", gen, width, m, checks, _history(m))

    return _real_check_gpu(index, max_temp_c, expect_gen, expect_width,  # pragma: no cover
                           replay_limit, dmesg_reader)


def _real_check_gpu(index, max_temp_c, expect_gen, expect_width, replay_limit,
                    dmesg_reader) -> GpuHealth:  # pragma: no cover - real-hw path
    if not shutil.which("nvidia-smi"):
        raise RuntimeError("nvidia-smi not found")
    m = _query_nvidia_smi(index)
    m["xid_errors"] = _scan_xids(dmesg_reader)
    gen, width = m.pop("_gen"), m.pop("_width")
    name = m.pop("_name")
    checks = _apply_limits(m, max_temp_c, expect_gen, expect_width, gen, width, replay_limit)
    return GpuHealth(index, name, gen, width, m, checks, _history(m))


def _scan_xids(reader=None) -> dict:
    """Bucket `NVRM: Xid (...): <code>` lines from the kernel log by code."""
    codes: dict[int, int] = {}
    for line in dmesg.read_kernel_log(reader).splitlines():
        match = _XID_RE.search(line)
        if match:
            c = int(match.group(1))
            codes[c] = codes.get(c, 0) + 1
    return codes


def _query_nvidia_smi(index: int) -> dict:  # pragma: no cover - real-hw path
    q = ("name,pcie.link.gen.current,pcie.link.width.current,temperature.gpu,"
         "ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,"
         "ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total,"
         "pcie.replay.counter,power.draw,clocks_throttle_reasons.active")
    out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits",
                          "-i", str(index)], capture_output=True, text=True, check=True).stdout
    f = [x.strip() for x in out.strip().split(",")]
    throttle_bits = int(f[10], 16) if f[10] not in ("", "N/A") else 0
    reasons = [name for bit, name in _THROTTLE_BAD.items() if throttle_bits & bit]
    rr = _query_row_remap(index)
    return {"_name": f[0], "_gen": _int(f[1]), "_width": _int(f[2]), "temp": _int(f[3]),
            "ecc_corrected_volatile": _int(f[4]), "ecc_uncorrected_volatile": _int(f[5]),
            "ecc_corrected_aggregate": _int(f[6]), "ecc_uncorrected_aggregate": _int(f[7]),
            "replay_count": _int(f[8]), "power_w": _float(f[9]), "throttle_reasons": reasons,
            **rr}


def _query_row_remap(index: int) -> dict:  # pragma: no cover - real-hw path
    try:
        out = subprocess.run(["nvidia-smi", "-i", str(index), "-q", "-d", "ROW_REMAPPER"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {"row_remap_pending": 0, "row_remap_failure": 0, "row_remap_count": 0}
    def grab(label):
        m = re.search(rf"{label}\s*:\s*(\w+)", out)
        v = m.group(1) if m else "0"
        return 1 if v.lower() == "yes" else _int(v)
    return {"row_remap_pending": grab("Pending"), "row_remap_failure": grab("Failure Occurred"),
            "row_remap_count": grab("Correctable Error") + grab("Uncorrectable Error")}


def _int(s: str) -> int:
    try:
        return int(s)
    except (ValueError, TypeError):
        return 0


def _float(s: str) -> float:
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0
