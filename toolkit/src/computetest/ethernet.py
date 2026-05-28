"""
Network-link checks: automotive/standard Ethernet (link, speed, error stats, optional
iperf3 throughput, and a cable-test TDR that locates an open/short + distance-to-fault
on supported PHYs) and CAN/CAN-FD (interface state, TEC/REC error counters, FD mode).
Wraps `ethtool` / `ip` / `iperf3` on real Linux; simulated otherwise.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from .backend import mock_mode

_SPEED_RE = re.compile(r"Speed:\s*(\d+(?:\.\d+)?)\s*([MG])b?", re.I)
# Linux IFNAMSIZ-1 (15) characters, the alphabet `ip`/`ethtool` accept. Rejects shell
# metacharacters and option-like values (`--help`) — the iface flows into argv positions.
_IFACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")


def _validate_iface(iface: str) -> None:
    if not isinstance(iface, str) or not _IFACE_RE.match(iface):
        raise ValueError(f"invalid network interface name: {iface!r}")


@dataclass
class EthHealth:
    iface: str
    link_up: bool
    speed_mbps: int
    rx_errors: int
    tx_errors: int
    throughput_mbps: float
    role: str = ""                       # "master" | "slave" | "" (automotive 1000BASE-T1)
    cable_test: dict | None = None       # TDR result, if run
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        ct = ""
        if self.cable_test:
            ct = f"  cable={self.cable_test.get('status')}"
            if self.cable_test.get("faults"):
                f0 = self.cable_test["faults"][0]
                ct += f"({f0['code']}@{f0['distance_m']}m)"
        role = f" {self.role}" if self.role else ""
        return (f"{self.iface}{role} up={self.link_up} {self.speed_mbps}Mb "
                f"rx_err={self.rx_errors} tx_err={self.tx_errors} "
                f"iperf={self.throughput_mbps:.0f}Mb -> {state}{ct}")

    def to_dict(self) -> dict:
        return {"iface": self.iface, "link_up": self.link_up, "speed_mbps": self.speed_mbps,
                "rx_errors": self.rx_errors, "tx_errors": self.tx_errors,
                "throughput_mbps": self.throughput_mbps, "role": self.role,
                "cable_test": self.cable_test, "checks": self.checks, "ok": self.ok}


def _parse_speed(ethtool_out: str) -> int:
    """Parse the ethtool Speed line to Mbps, handling Mb/s and G (e.g. '2.5G' -> 2500)."""
    m = _SPEED_RE.search(ethtool_out)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).upper()
    return int(val * (1000 if unit == "G" else 1))


def check_ethernet(iface: str = "eth0", *, expect_mbps: int = 1000,
                   min_throughput_mbps: float = 900, cable_test: bool = False,
                   iperf: bool = False, iperf_server: str | None = None,
                   mock: bool | None = None) -> EthHealth:
    _validate_iface(iface)
    """Check link/speed/errors, and (opt-in) cable TDR + iperf3 throughput.

    Throughput is only measured (and gated by ``min_throughput_mbps``) when
    ``iperf=True`` — a link-only check must not fail on a throughput it never ran.
    """
    use_mock = mock_mode() if mock is None else mock
    measured_tput = iperf            # whether a throughput number was actually obtained
    if use_mock:
        bad = "BAD" in iface
        up = not bad
        spd = 0 if bad else expect_mbps
        rx_e, tx_e = (40, 5) if bad else (0, 0)
        tput = (0.0 if bad else (min_throughput_mbps + 50)) if iperf else 0.0
        role = "master"
        ct = _mock_cable_test(bad) if cable_test else None
    else:  # pragma: no cover - real-hw path
        if not shutil.which("ethtool"):
            raise RuntimeError("ethtool not found")
        # timeout=10 because a wedged/flaky PHY can wedge `ethtool` indefinitely.
        et = subprocess.run(["ethtool", iface],
                            capture_output=True, text=True, timeout=10).stdout
        up = "Link detected: yes" in et
        spd = _parse_speed(et)
        mlb = re.search(r"master-slave (?:cfg|status):\s*(\S+)", et, re.I)
        role = "master" if (mlb and "master" in mlb.group(1).lower()) else \
               ("slave" if mlb else "")
        stats = subprocess.run(["ethtool", "-S", iface],
                               capture_output=True, text=True, timeout=10).stdout
        rx_e, tx_e = _stat(stats, "rx_errors"), _stat(stats, "tx_errors")
        tput = _real_iperf(iperf_server) if iperf else 0.0
        ct = _real_cable_test(iface) if cable_test else None

    checks = {"link_up": up, "speed_ok": spd >= expect_mbps,
              "low_errors": rx_e + tx_e < 10}
    if measured_tput:
        checks["throughput_ok"] = tput >= min_throughput_mbps
    if ct is not None:
        checks["cable_ok"] = ct.get("status") != "fault"
    return EthHealth(iface, up, spd, rx_e, tx_e, tput, role, ct, checks)


def _real_iperf(server: str | None) -> float:  # pragma: no cover - real-hw path
    """Best-effort iperf3 client throughput in Mb/s; 0.0 if iperf3/server unavailable."""
    if not server or not shutil.which("iperf3"):
        return 0.0
    try:
        import json as _json
        out = subprocess.run(["iperf3", "-c", server, "-J"],
                             capture_output=True, text=True, timeout=30).stdout
        bps = _json.loads(out)["end"]["sum_received"]["bits_per_second"]
        return bps / 1e6
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return 0.0


def _mock_cable_test(bad: bool) -> dict:
    if bad:
        return {"status": "fault", "faults": [{"pair": 0, "code": "open", "distance_m": 2.3}]}
    return {"status": "ok", "faults": []}


def _real_cable_test(iface: str) -> dict:  # pragma: no cover - real-hw path
    """Best-effort TDR via ethtool. PHY/driver support varies; treated as 'skipped'
    when unsupported so it never false-fails a good link."""
    try:
        out = subprocess.run(["ethtool", "--cable-test-tdr", iface],
                             capture_output=True, text=True, timeout=15).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return {"status": "skipped", "faults": []}
    faults = []
    for m in re.finditer(r"pair (\w).*?(open|short|impedance).*?(\d+)\s*m", out):
        faults.append({"pair": m.group(1), "code": m.group(2),
                       "distance_m": float(m.group(3))})
    return {"status": "fault" if faults else "ok", "faults": faults}


def _stat(stats: str, key: str) -> int:
    """Value of an exact `ethtool -S` counter (lines look like '   rx_errors: 5'). Match the
    name before the ':' EXACTLY so a superstring counter (e.g. 'rx_errors_phy') can't shadow
    the one we want, and keep only ASCII decimal digits (str.isdigit() also accepts Unicode
    digits such as superscripts, which int() then rejects with ValueError)."""
    for line in stats.splitlines():
        name, sep, val = line.partition(":")
        if sep and name.strip() == key:
            digits = "".join(c for c in val if c in "0123456789")
            return int(digits) if digits else 0
    return 0


@dataclass
class CanHealth:
    iface: str
    state: str          # ERROR-ACTIVE (good) ... ERROR-PASSIVE ... BUS-OFF (bad)
    tec: int            # transmit error counter
    rec: int            # receive error counter
    fd: bool = False    # CAN-FD enabled
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        state = "OK" if self.ok else "FAIL"
        fd = " FD" if self.fd else ""
        return f"{self.iface}{fd} state={self.state} TEC={self.tec} REC={self.rec} -> {state}"

    def to_dict(self) -> dict:
        return {"iface": self.iface, "state": self.state, "tec": self.tec, "rec": self.rec,
                "fd": self.fd, "checks": self.checks, "ok": self.ok}


def _can_fd_enabled(ip_link_output: str) -> bool:
    """CAN-FD is on iff `ip -details link` reports the explicit 'fd on' flag. Keyed on
    'fd on' (not a loose \\bfd\\b, which also matches 'fd off' and stray 'fd' tokens)."""
    return "fd on" in ip_link_output


def check_can(iface: str = "can0", *, mock: bool | None = None) -> CanHealth:
    _validate_iface(iface)
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        bad = "BAD" in iface
        state = "BUS-OFF" if bad else "ERROR-ACTIVE"
        tec, rec, fd = (255 if bad else 0), (130 if bad else 0), ("fd" in iface.lower())
    else:  # pragma: no cover - real-hw path
        out = subprocess.run(["ip", "-details", "-statistics", "link", "show", iface],
                             capture_output=True, text=True, timeout=10).stdout
        state = ("BUS-OFF" if "BUS-OFF" in out else
                 "ERROR-PASSIVE" if "ERROR-PASSIVE" in out else
                 "ERROR-WARNING" if "ERROR-WARNING" in out else "ERROR-ACTIVE")
        berr = re.search(r"berr-counter tx (\d+) rx (\d+)", out)
        tec, rec = (int(berr.group(1)), int(berr.group(2))) if berr else (0, 0)
        fd = _can_fd_enabled(out)
    checks = {"not_bus_off": state != "BUS-OFF", "error_active": state == "ERROR-ACTIVE",
              "low_errors": tec < 96 and rec < 96}
    return CanHealth(iface, state, tec, rec, fd, checks)
