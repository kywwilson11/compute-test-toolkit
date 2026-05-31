"""
Tegra (NVIDIA Jetson) SoC-telemetry check, built on ``tegrastats``.

This is deliberately NOT the discrete-GPU check in ``gpu.py``. A Jetson's GPU is an
on-die iGPU that shares LPDDR with the CPU: it has **no ECC, no PCIe link, and no
row-remapping**, so the ``nvidia-smi`` fields ``gpu.py`` relies on simply do not exist
here (``nvidia-smi`` isn't even shipped on L4T — Jetson uses ``tegrastats``). Rather
than fake those fields, this check judges what a Tegra actually exposes:

  * per-zone die temperatures (GPU/CPU/tj/...),
  * per-rail power (POM_5V_* on Nano, VDD_*/VIN_* on Orin),
  * GPU 3D-engine load (GR3D_FREQ) and memory-controller load (EMC_FREQ),

plus the one thing both worlds share — kernel ``NVRM: Xid`` faults, reused verbatim
from ``gpu.py``. Pass/fail is temperature + thermal-throttle-by-inference + critical
XIDs; power and load are reported, not gated (the "input" rail name varies by board,
so a cross-board power budget would be a guess we decline to make).

tegrastats output format VARIES by JetPack/L4T version and by board. The parser is
tolerant and is unit-tested against synthetic Nano + Orin lines, but the real
collection path (``# pragma: no cover - real-hw path``) should be verified against the
JetPack version on the actual DUT before trusting the numbers.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .backend import mock_mode
from .gpu import _XID_CRITICAL, _scan_xids  # XID semantics are arch-agnostic — reuse them

# A zone whose reading is a fixed dummy (Nano's PMIC always reports 100C) or an offline
# sensor (Orin spare zones read -256C) must not drive pass/fail. Reported, never gated.
_IGNORE_ZONES = {"PMIC"}
_MIN_PLAUSIBLE_C = -40.0

# `<zone>@<temp>C`  — GPU@38C, AO@45.5C, thermal@40.25C, tj@47.343C, cpu@-256C.
_TEMP_RE = re.compile(r"\b([A-Za-z][\w.]*)@(-?\d+(?:\.\d+)?)C\b")
# `<RAIL> <instant>/<avg>` in mW — POM_5V_IN 2532/2698, VDD_GPU_SOC 1191/1191. The
# `(?=\s|$)` lookahead keeps `RAM 2594/3956MB` / `SWAP 0/1978MB` out (they're suffixed MB).
_RAIL_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})\s+(\d+)(?:mW)?/(\d+)(?:mW)?(?=\s|$)")
_RAM_RE = re.compile(r"\bRAM\s+(\d+)/(\d+)MB\b")
_GR3D_RE = re.compile(r"\bGR3D_FREQ\s+(\d+)%")
_EMC_RE = re.compile(r"\bEMC_FREQ\s+(\d+)%")

_MOCK_LINE = (
    "RAM 2594/3956MB (lfb 12x4MB) SWAP 0/1978MB (cached 0MB) "
    "CPU [12%@1479,4%@1479,5%@1479,3%@1479] EMC_FREQ 8% GR3D_FREQ 37% "
    "PLL@39C CPU@41C PMIC@100C GPU@38C AO@45.5C thermal@40.25C "
    "POM_5V_IN 2532/2698 POM_5V_GPU 0/123 POM_5V_CPU 401/452"
)


@dataclass
class TegraHealth:
    model: str
    thermal_c: dict[str, float]
    rails_mw: dict[str, tuple[int, int]]   # rail -> (instant, average) mW
    metrics: dict
    checks: dict[str, bool] = field(default_factory=dict)
    xid_errors: dict[int, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        m = self.metrics
        xids = ",".join(str(c) for c in self.xid_errors) or "-"
        thr = "throttle" if m.get("throttle_inferred") else "-"
        note = f"  note[{';'.join(self.notes)}]" if self.notes else ""
        return (f"Tegra {self.model} GPU={m.get('gpu_temp_c')}C "
                f"max={m.get('max_zone_c')}C({m.get('hot_zone')}) "
                f"gr3d={m.get('gr3d_pct')}% emc={m.get('emc_pct')}% "
                f"throttle={thr} xid={xids} -> {state}{note}")

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "thermal_c": self.thermal_c,
            "rails_mw": {r: avg for r, (_inst, avg) in self.rails_mw.items()},
            "metrics": self.metrics,
            "checks": self.checks,
            "xid_errors": self.xid_errors,
            "notes": self.notes,
            "ok": self.ok,
        }


def parse_tegrastats(line: str) -> dict:
    """Parse one ``tegrastats`` sample line into a structured dict (single sample)."""
    thermal = {z: float(t) for z, t in _TEMP_RE.findall(line)}
    rails = {name: (int(inst), int(avg)) for name, inst, avg in _RAIL_RE.findall(line)}
    gr3d = int(m.group(1)) if (m := _GR3D_RE.search(line)) else None
    emc = int(m.group(1)) if (m := _EMC_RE.search(line)) else None
    ram = _RAM_RE.search(line)
    return {
        "thermal_c": thermal,
        "rails_mw": rails,
        "gr3d_pct": gr3d,
        "emc_pct": emc,
        "ram_used_mb": int(ram.group(1)) if ram else None,
        "ram_total_mb": int(ram.group(2)) if ram else None,
    }


def _merge(samples: list[dict]) -> dict:
    """Fold many samples to a worst-case snapshot: max temp/load/power per key.

    Qualification cares about the peak seen during the window, not the last sample."""
    thermal: dict[str, float] = {}
    rails: dict[str, tuple[int, int]] = {}
    gr3d: int | None = None
    emc: int | None = None
    ram_used: int | None = None
    ram_total: int | None = None
    for s in samples:
        for z, t in s["thermal_c"].items():
            thermal[z] = max(thermal.get(z, t), t)
        for r, (inst, avg) in s["rails_mw"].items():
            prev_i, prev_a = rails.get(r, (inst, avg))
            rails[r] = (max(prev_i, inst), max(prev_a, avg))
        if s["gr3d_pct"] is not None:
            gr3d = max(gr3d or 0, s["gr3d_pct"])
        if s["emc_pct"] is not None:
            emc = max(emc or 0, s["emc_pct"])
        if s["ram_used_mb"] is not None:
            ram_used = max(ram_used or 0, s["ram_used_mb"])
            ram_total = s["ram_total_mb"]
    return {"thermal_c": thermal, "rails_mw": rails, "gr3d_pct": gr3d,
            "emc_pct": emc, "ram_used_mb": ram_used, "ram_total_mb": ram_total}


def _build_health(lines: list[str], model: str, xids: dict[int, int],
                  max_temp_c: float, throttle_temp_c: float) -> TegraHealth:
    merged = _merge([parse_tegrastats(line) for line in lines if line.strip()])
    thermal = merged["thermal_c"]
    notes: list[str] = []

    meaningful: dict[str, float] = {}
    for z, t in thermal.items():
        if z in _IGNORE_ZONES:
            notes.append(f"{z}@{t:g}C ignored (fixed/dummy sensor)")
        elif t <= _MIN_PLAUSIBLE_C:
            notes.append(f"{z}@{t:g}C ignored (offline sensor)")
        else:
            meaningful[z] = t

    have_data = bool(meaningful)
    if have_data:
        hot_zone = max(meaningful, key=lambda z: meaningful[z])
        max_zone = meaningful[hot_zone]
    else:
        hot_zone, max_zone = "?", 0.0
    # Resolve gpu_temp against the offline-filtered `meaningful` dict with explicit
    # membership: an offline GPU sensor (-256C) falls back to tj/max_zone instead of
    # being surfaced verbatim, and a legitimate 0.0C reading is preserved (0.0 is falsy
    # under the old `or` chain, so it was wrongly dropped).
    gpu_temp = meaningful["GPU"] if "GPU" in meaningful else meaningful.get("tj", max_zone)
    throttle_inferred = have_data and max_zone >= throttle_temp_c
    bad_xid = [c for c in xids if c in _XID_CRITICAL]

    checks = {
        # Gate on have_data (not a 0< lower bound): a sub-zero-but-plausible cold-start
        # reading (the module admits zones down to _MIN_PLAUSIBLE_C) must pass, while a
        # genuine no-data window (max_zone defaults to 0.0, have_data False) still fails.
        f"max_zone<={max_temp_c:g}C": have_data and max_zone <= max_temp_c,
        "no_thermal_throttle": not throttle_inferred,
        "no_critical_xid": not bad_xid,
    }
    metrics = {
        "gpu_temp_c": gpu_temp,
        "max_zone_c": max_zone,
        "hot_zone": hot_zone,
        "throttle_inferred": throttle_inferred,
        "gr3d_pct": merged["gr3d_pct"],
        "emc_pct": merged["emc_pct"],
        "ram_used_mb": merged["ram_used_mb"],
        "ram_total_mb": merged["ram_total_mb"],
    }
    return TegraHealth(model, thermal, merged["rails_mw"], metrics, checks, xids, notes)


def check_tegra(*, mock: bool | None = None, max_temp_c: float = 85.0,
                throttle_temp_c: float = 97.0, samples: int = 5,
                interval_ms: int = 1000, dmesg_reader=None,
                _lines: list[str] | None = None) -> TegraHealth:
    """Sample ``tegrastats`` and judge SoC thermals + throttle + critical XIDs.

    ``_lines`` injects pre-captured tegrastats lines (used by tests and the real
    collector); ``dmesg_reader`` injects the kernel log for the XID scan.
    """
    use_mock = mock_mode() if mock is None else mock
    if _lines is not None:
        xids = _scan_xids(dmesg_reader) if dmesg_reader is not None else {}
        return _build_health(_lines, "Tegra (test)", xids, max_temp_c, throttle_temp_c)
    if use_mock:
        xids = _scan_xids(dmesg_reader) if dmesg_reader is not None else {}
        return _build_health([_MOCK_LINE], "Tegra (mock Jetson)", xids,
                             max_temp_c, throttle_temp_c)
    return _real_check_tegra(max_temp_c, throttle_temp_c, samples,  # pragma: no cover
                             interval_ms)


def is_tegra() -> bool:
    """True on an L4T/Jetson host: the Tegra release file exists, or ``tegrastats`` is
    present while ``nvidia-smi`` (discrete-GPU only) is not."""
    if os.path.exists("/etc/nv_tegra_release"):
        return True
    return shutil.which("tegrastats") is not None and shutil.which("nvidia-smi") is None


def _read_model() -> str:  # pragma: no cover - real-hw path
    try:
        with open("/proc/device-tree/model") as fh:
            return fh.read().strip("\x00\n ") or "Tegra"
    except OSError:
        return "Tegra"


def _real_check_tegra(max_temp_c: float, throttle_temp_c: float, samples: int,
                      interval_ms: int) -> TegraHealth:  # pragma: no cover - real-hw path
    if not shutil.which("tegrastats"):
        raise RuntimeError("tegrastats not found (Tegra/Jetson L4T only)")
    lines = _collect_tegrastats(samples, interval_ms)
    xids = _scan_xids(None)
    return _build_health(lines, _read_model(), xids, max_temp_c, throttle_temp_c)


def _collect_tegrastats(samples: int, interval_ms: int) -> list[str]:  # pragma: no cover
    """Stream N samples from ``tegrastats`` then stop it. Format varies by JetPack;
    verify the field names against the DUT's L4T version before trusting numbers."""
    proc = subprocess.Popen(["tegrastats", "--interval", str(interval_ms)],
                            stdout=subprocess.PIPE, text=True)
    lines: list[str] = []
    try:
        assert proc.stdout is not None
        for _ in range(max(1, samples)):
            line = proc.stdout.readline()
            if not line:
                break
            lines.append(line.strip())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    return lines
