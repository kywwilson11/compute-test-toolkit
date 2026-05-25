"""
NVMe health check: SMART decode against new-drive manufacturing limits, plus
identify and (optionally) a controller self-test. Wraps `nvme-cli` on real Linux;
returns simulated data on a laptop. Remember: an NVMe drive is a PCIe endpoint, so
run the PCIe diagnostic on its link too (link train, AER) — this covers the drive.
"""
from __future__ import annotations

import json
import shutil
import subprocess
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

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        s = self.smart
        return (f"{self.device} {self.model} fw={self.firmware} "
                f"temp={s.get('temperature')}C used={s.get('percentage_used')}% "
                f"media_err={s.get('media_errors')} -> {state}")

    def to_dict(self) -> dict:
        return {"device": self.device, "model": self.model, "serial": self.serial,
                "firmware": self.firmware, "smart": self.smart,
                "checks": self.checks, "ok": self.ok}


# Manufacturing limits for a *new* drive (see Guide A, §NVMe).
def _apply_limits(smart: dict, max_temp_c: int = 70) -> dict[str, bool]:
    return {
        "critical_warning==0": smart.get("critical_warning", 0) == 0,
        "media_errors==0": smart.get("media_errors", 0) == 0,
        "no_error_log_entries": smart.get("num_err_log_entries", 0) == 0,
        "percentage_used<2": smart.get("percentage_used", 0) < 2,
        "available_spare>=100": smart.get("available_spare", 0) >= 100,
        f"temp<={max_temp_c}": 0 < smart.get("temperature", 0) <= max_temp_c,
    }


def _mock_smart(device: str) -> dict:
    # A healthy new drive. Inject a fault by putting "BAD" in the device path.
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
        "unsafe_shutdowns": 0,
    }


def check_nvme(device: str = "/dev/nvme0", *, mock: bool | None = None,
               max_temp_c: int = 70) -> NvmeHealth:
    """Read SMART/identify and apply new-drive pass/fail limits."""
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        smart = _mock_smart(device)
        return NvmeHealth(device, "MockSSD-1TB", "MOCK0001", "MK1.0", smart,
                          _apply_limits(smart, max_temp_c))

    if not shutil.which("nvme"):  # pragma: no cover - real-hw path
        raise RuntimeError("nvme-cli not found (apt install nvme-cli)")
    smart_raw = subprocess.run(["nvme", "smart-log", device, "-o", "json"],
                               capture_output=True, text=True, check=True).stdout
    smart = json.loads(smart_raw)
    # nvme-cli reports temperature in Kelvin; normalize to Celsius.
    if smart.get("temperature", 0) > 200:
        smart["temperature"] = smart["temperature"] - 273
    ctrl = json.loads(subprocess.run(["nvme", "id-ctrl", device, "-o", "json"],
                                     capture_output=True, text=True, check=True).stdout)
    return NvmeHealth(device, ctrl.get("mn", "?").strip(), ctrl.get("sn", "?").strip(),
                      ctrl.get("fr", "?").strip(), smart, _apply_limits(smart, max_temp_c))


def start_self_test(device: str, extended: bool = False, *, mock: bool | None = None) -> bool:
    """Kick off a device self-test (DST). Poll self-test-log for the result later."""
    if (mock_mode() if mock is None else mock):
        return True
    code = "2" if extended else "1"  # pragma: no cover - real-hw path
    subprocess.run(["nvme", "device-self-test", device, "-s", code], check=True)
    return True
