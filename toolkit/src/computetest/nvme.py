"""
NVMe health check: SMART decode against new-drive manufacturing limits, the
power-on-hours / power-cycles "used-stock" signal, and an optional Device Self-Test
(DST) that polls the self-test log (page 0x06) to completion. Wraps `nvme-cli` on
real Linux; simulated otherwise. An NVMe drive is a PCIe endpoint, so run the PCIe
diagnostic on its link too (link train + AER).

Like the GPU check: SMART faults are pass/fail; high lifetime counters (power-on
hours/cycles, unsafe shutdowns) are reported as RMA *history* — a re-labeled or
returned drive entering the line — failing only the configurable power-on-hours gate.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .backend import mock_mode


@dataclass
class NvmeHealth:
    device: str
    model: str
    serial: str
    firmware: str
    smart: dict
    checks: dict[str, bool] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)   # used-stock flags (not faults)
    self_test: dict | None = None                       # DST result, if run

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        s = self.smart
        hist = f"  history[{';'.join(self.history)}]" if self.history else ""
        dst = (f"  dst={'pass' if self.self_test.get('passed') else 'FAIL'}"
               if self.self_test else "")
        return (f"{self.device} {self.model} fw={self.firmware} "
                f"temp={s.get('temperature')}C used={s.get('percentage_used')}% "
                f"media_err={s.get('media_errors')} poh={s.get('power_on_hours')} "
                f"-> {state}{dst}{hist}")

    def to_dict(self) -> dict:
        return {"device": self.device, "model": self.model, "serial": self.serial,
                "firmware": self.firmware, "smart": self.smart, "checks": self.checks,
                "history": self.history, "self_test": self.self_test, "ok": self.ok}


def _apply_limits(smart: dict, max_temp_c: int, max_power_on_hours: int) -> dict[str, bool]:
    return {
        "critical_warning==0": smart.get("critical_warning", 0) == 0,
        "media_errors==0": smart.get("media_errors", 0) == 0,
        "no_error_log_entries": smart.get("num_err_log_entries", 0) == 0,
        "percentage_used<2": smart.get("percentage_used", 0) < 2,
        "available_spare>=100": smart.get("available_spare", 0) >= 100,
        f"temp<={max_temp_c}": 0 < smart.get("temperature", 0) <= max_temp_c,
        f"power_on_hours<={max_power_on_hours}":
            smart.get("power_on_hours", 0) <= max_power_on_hours,
    }


def _history(smart: dict) -> list[str]:
    h = []
    if smart.get("power_cycles", 0) > 50:
        h.append(f"power_cycles={smart['power_cycles']}")
    if smart.get("unsafe_shutdowns", 0) > 0:
        h.append(f"unsafe_shutdowns={smart['unsafe_shutdowns']}")
    if smart.get("data_units_written", 0) > 100000:
        h.append("significant lifetime writes")
    return h


def _mock_smart(device: str) -> dict:
    bad = "BAD" in device
    return {
        "critical_warning": 1 if bad else 0,
        "temperature": 96 if bad else 41,
        "available_spare": 100,
        "available_spare_threshold": 10,
        "percentage_used": 0,
        "media_errors": 7 if bad else 0,
        "num_err_log_entries": 7 if bad else 0,
        "data_units_written": 1234,
        "data_units_read": 5678,
        "power_on_hours": 6200 if bad else 1,   # high on a "new" drive = used/returned stock
        "power_cycles": 800 if bad else 3,
        "unsafe_shutdowns": 40 if bad else 0,
    }


# nvme-cli emits some SMART fields under abbreviated JSON keys; map them to the
# canonical names used by the checks/summary (and by the mock) so the real-hardware
# path reads the right values. Without this, e.g. available_spare would always read
# its 0 default and the available_spare>=100 check would false-FAIL every good drive.
_SMART_KEY_ALIASES = {
    "avail_spare": "available_spare",
    "spare_thresh": "available_spare_threshold",
    "percent_used": "percentage_used",
}


def _normalize_smart_keys(smart: dict) -> dict:
    for src, dst in _SMART_KEY_ALIASES.items():
        if src in smart and dst not in smart:
            smart[dst] = smart[src]
    return smart


def check_nvme(device: str = "/dev/nvme0", *, mock: bool | None = None, max_temp_c: int = 70,
               max_power_on_hours: int = 50, run_self_test: bool = False) -> NvmeHealth:
    """Read SMART/identify, apply new-drive limits, and (optionally) run a DST."""
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        smart = _mock_smart(device)
        model, serial, fw = "MockSSD-1TB", "MOCK0001", "MK1.0"
    else:  # pragma: no cover - real-hw path
        if not shutil.which("nvme"):
            raise RuntimeError("nvme-cli not found (apt install nvme-cli)")
        smart = json.loads(subprocess.run(["nvme", "smart-log", device, "-o", "json"],
                                          capture_output=True, text=True, check=True).stdout)
        smart = _normalize_smart_keys(smart)
        if smart.get("temperature", 0) > 200:           # nvme-cli reports Kelvin
            smart["temperature"] -= 273
        ctrl = json.loads(subprocess.run(["nvme", "id-ctrl", device, "-o", "json"],
                                        capture_output=True, text=True, check=True).stdout)
        model, serial, fw = (ctrl.get("mn", "?").strip(), ctrl.get("sn", "?").strip(),
                             ctrl.get("fr", "?").strip())

    checks = _apply_limits(smart, max_temp_c, max_power_on_hours)
    dst = None
    if run_self_test:
        start_self_test(device, extended=False, mock=use_mock)
        dst = poll_self_test(device, mock=use_mock)
        checks["self_test_passed"] = bool(dst.get("passed"))
    return NvmeHealth(device, model, serial, fw, smart, checks, _history(smart), dst)


# --- Device Self-Test (DST), log page 0x06 ------------------------------------- #
def start_self_test(device: str, *, extended: bool = False, mock: bool | None = None) -> bool:
    """Kick off a device self-test. Poll self_test_log()/poll_self_test() for the result.

    `extended` is keyword-only: ``start_self_test(dev, True)`` (a magic-bool at the call
    site) is a footgun the API now refuses by construction.
    """
    if (mock_mode() if mock is None else mock):
        return True
    code = "2" if extended else "1"  # pragma: no cover - real-hw path
    subprocess.run(["nvme", "device-self-test", device, "-s", code], check=True)  # pragma: no cover
    return True  # pragma: no cover - real-hw path


def self_test_log(device: str, *, mock: bool | None = None) -> dict:
    """Read the current self-test log: {percent, in_progress, result, passed}.
    result 0 = completed without error; >0 = a failure code (NVMe spec)."""
    if (mock_mode() if mock is None else mock):
        bad = "BAD" in device
        return {"percent": 100, "in_progress": False, "result": 9 if bad else 0,
                "passed": not bad}
    return _real_self_test_log(device)  # pragma: no cover - real-hw path


def _real_self_test_log(device: str) -> dict:  # pragma: no cover - real-hw path
    raw = json.loads(subprocess.run(["nvme", "self-test-log", device, "-o", "json"],
                                    capture_output=True, text=True, check=True).stdout)
    cur = raw.get("current_operation", 0)
    entry = (raw.get("Self-test Log") or raw.get("logs") or [{}])[0]
    result = entry.get("result", entry.get("Self Test Result", 0)) or 0
    in_progress = cur not in (0, 0xF)
    return {"percent": raw.get("completion", 100), "in_progress": in_progress,
            "result": result, "passed": (not in_progress) and result == 0}


def poll_self_test(device: str, *, mock: bool | None = None, timeout: float = 120.0,
                   interval: float = 2.0) -> dict:
    """Poll the self-test log until the DST completes (or times out)."""
    if (mock_mode() if mock is None else mock):
        return self_test_log(device, mock=True)            # mock completes immediately
    return _real_poll_self_test(device, timeout, interval)  # pragma: no cover - real-hw path


def _real_poll_self_test(device: str, timeout: float,
                         interval: float) -> dict:  # pragma: no cover - real-hw path
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        log = self_test_log(device, mock=False)
        if not log["in_progress"]:
            return log
        time.sleep(interval)
    return {"percent": 0, "in_progress": True, "result": -1, "passed": False, "timeout": True}
