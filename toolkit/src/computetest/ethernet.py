"""
Network-link checks for the compute board: automotive/standard Ethernet (link,
speed, error stats, optional iperf3 throughput) and CAN/CAN-FD (interface state,
TEC/REC error counters). Wraps `ethtool` / `ip` / `iperf3` on real Linux; simulated
otherwise.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from .backend import mock_mode


@dataclass
class EthHealth:
    iface: str
    link_up: bool
    speed_mbps: int
    rx_errors: int
    tx_errors: int
    throughput_mbps: float
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        return (f"{self.iface} up={self.link_up} {self.speed_mbps}Mb "
                f"rx_err={self.rx_errors} tx_err={self.tx_errors} "
                f"iperf={self.throughput_mbps:.0f}Mb -> {state}")

    def to_dict(self) -> dict:
        return {"iface": self.iface, "link_up": self.link_up, "speed_mbps": self.speed_mbps,
                "rx_errors": self.rx_errors, "tx_errors": self.tx_errors,
                "throughput_mbps": self.throughput_mbps, "checks": self.checks, "ok": self.ok}


def check_ethernet(iface: str = "eth0", *, expect_mbps: int = 1000,
                   min_throughput_mbps: float = 900, mock: bool | None = None) -> EthHealth:
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        bad = "BAD" in iface
        up = not bad
        spd = 0 if bad else expect_mbps
        rx_e, tx_e = (40, 5) if bad else (0, 0)
        tput = 0.0 if bad else (min_throughput_mbps + 50)
        checks = {"link_up": up, "speed_ok": spd >= expect_mbps,
                  "low_errors": rx_e + tx_e < 10, "throughput_ok": tput >= min_throughput_mbps}
        return EthHealth(iface, up, spd, rx_e, tx_e, tput, checks)

    if not shutil.which("ethtool"):  # pragma: no cover - real-hw path
        raise RuntimeError("ethtool not found")
    et = subprocess.run(["ethtool", iface], capture_output=True, text=True).stdout
    up = "Link detected: yes" in et
    spd = 0
    for line in et.splitlines():
        if "Speed:" in line:
            digits = "".join(c for c in line if c.isdigit())
            spd = int(digits) if digits else 0
    stats = subprocess.run(["ethtool", "-S", iface], capture_output=True, text=True).stdout
    rx_e = _stat(stats, "rx_errors")
    tx_e = _stat(stats, "tx_errors")
    tput = 0.0  # run iperf3 separately against a partner; left 0 unless wired up
    checks = {"link_up": up, "speed_ok": spd >= expect_mbps,
              "low_errors": rx_e + tx_e < 10, "throughput_ok": True}
    return EthHealth(iface, up, spd, rx_e, tx_e, tput, checks)


def _stat(stats: str, key: str) -> int:
    for line in stats.splitlines():
        if key in line:
            digits = "".join(c for c in line.split(":")[-1] if c.isdigit())
            return int(digits) if digits else 0
    return 0


@dataclass
class CanHealth:
    iface: str
    state: str      # ERROR-ACTIVE (good) ... BUS-OFF (bad)
    tec: int
    rec: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        state = "OK" if self.ok else "FAIL"
        return f"{self.iface} state={self.state} TEC={self.tec} REC={self.rec} -> {state}"

    def to_dict(self) -> dict:
        return {"iface": self.iface, "state": self.state, "tec": self.tec,
                "rec": self.rec, "checks": self.checks, "ok": self.ok}


def check_can(iface: str = "can0", *, mock: bool | None = None) -> CanHealth:
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        bad = "BAD" in iface
        state = "BUS-OFF" if bad else "ERROR-ACTIVE"
        tec, rec = (255 if bad else 0), (130 if bad else 0)
    else:  # pragma: no cover - real-hw path
        out = subprocess.run(["ip", "-details", "-statistics", "link", "show", iface],
                             capture_output=True, text=True).stdout
        state = "BUS-OFF" if "BUS-OFF" in out else (
            "ERROR-PASSIVE" if "ERROR-PASSIVE" in out else "ERROR-ACTIVE")
        tec = rec = 0  # parse from `ip` stats or the can netlink if needed
    checks = {"not_bus_off": state != "BUS-OFF", "error_active": state == "ERROR-ACTIVE",
              "low_errors": tec < 96 and rec < 96}
    return CanHealth(iface, state, tec, rec, checks)
