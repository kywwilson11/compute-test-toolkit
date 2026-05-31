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
# Linux IFNAMSIZ-1 (15) characters, the alphabet `ip`/`ethtool` accept. The leading
# char is constrained to alnum/_ so a value like `--help` can't slip through and flow
# into argv as an option (real Linux iface names never start with `-` or `.`).
_IFACE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$")


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


def _parse_role(ethtool_out: str) -> str:
    """Role from the NEGOTIATED ``master-slave status:`` line (values: master / slave /
    unknown / resolution error), falling back to ``master-slave cfg:`` only when status is
    absent. The configured *preference* (cfg: ``preferred master``/``forced slave`` ...) is
    NOT the resolved role: a PHY that prefers master can negotiate to slave, so reporting cfg
    would mislabel the link. See ethtool netlink master-slave UAPI (kernel commit 558f7cc)."""
    m = re.search(r"master-slave status:\s*(\S+)", ethtool_out, re.I)
    if m is None:
        m = re.search(r"master-slave cfg:\s*(\S+)", ethtool_out, re.I)
    if m is None:
        return ""
    val = m.group(1).lower()
    if "master" in val:
        return "master"
    if "slave" in val:
        return "slave"
    return ""


def _run_checked(cmd: list[str], *, timeout: int) -> str:  # pragma: no cover - real-hw path
    """Run a real-hw tool and return stdout, raising on a non-zero exit so a tool failure is
    never silently scored as a clean link / CAN PASS. Mirrors the returncode-check convention
    in lmt_adapter._run_pci_lmt (rc!=0 -> RuntimeError) and bert._run_c_engine."""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


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
        et = _run_checked(["ethtool", iface], timeout=10)
        up = "Link detected: yes" in et
        spd = _parse_speed(et)
        role = _parse_role(et)
        stats = _run_checked(["ethtool", "-S", iface], timeout=10)
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
        return {"status": "fault", "faults": [{"pair": "A", "code": "open", "distance_m": 2.3}]}
    return {"status": "ok", "faults": []}


def _parse_cable_test(out: str) -> list[dict]:
    """Parse `ethtool --cable-test-tdr` text into a fault list. ethtool prints the pair
    result code and the fault length from distinct netlink attributes, so on the modern
    layout they land on SEPARATE lines, e.g. (lowercased)::

        pair a code ok
        pair b code open circuit
        pair b, fault length: 16.80m

    A single-line `.*?` regex (a) cannot span the newline -> misses the fault entirely, and
    (b) `(\\d+)` captures only the post-decimal fragment of `16.80m` -> reports 80 m. So we
    scan line-by-line: accumulate the last faulted pair/code, then pair it with the next
    `fault length:` line, reading the distance as a full decimal (`\\d+(?:\\.\\d+)?`). `OK`/
    `good cable` lines clear the accumulator so a clean pair never yields a fault. The legacy
    same-line layout (`pair b code open circuit, fault length: 2.31m`) is also handled because
    the code on that line sets the accumulator before the fault-length match fires."""
    faults: list[dict] = []
    last_pair: str = ""
    last_code: str | None = None
    for line in out.splitlines():
        ln = line.strip().lower()
        pm = re.search(r"\bpair[:\s]+(\w+)", ln)
        cm = re.search(r"\b(open|short|impedance)\b", ln)
        if pm and cm:
            last_pair, last_code = pm.group(1).upper(), cm.group(1)
        elif pm and re.search(r"\b(ok|good)\b", ln):
            last_pair, last_code = pm.group(1).upper(), None
        if "fault length" in ln:
            dm = re.search(r"(\d+(?:\.\d+)?)\s*m\b", ln)
            if dm and last_code is not None:
                faults.append({"pair": pm.group(1).upper() if pm else last_pair,
                               "code": last_code, "distance_m": float(dm.group(1))})
    return faults


def _real_cable_test(iface: str) -> dict:  # pragma: no cover - real-hw path
    """Best-effort TDR via ethtool. PHY/driver support varies; treated as 'skipped' when
    unsupported (subprocess error OR non-zero exit) so it never false-fails a good link."""
    try:
        proc = subprocess.run(["ethtool", "--cable-test-tdr", iface],
                              capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"status": "skipped", "faults": []}
    if proc.returncode != 0:
        return {"status": "skipped", "faults": []}
    faults = _parse_cable_test(proc.stdout)
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
        # Inspect returncode (mirror _run_checked / lmt_adapter): a failed `ip` returns empty
        # stdout, which would otherwise default to ERROR-ACTIVE and falsely PASS the bus.
        out = _run_checked(["ip", "-details", "-statistics", "link", "show", iface], timeout=10)
        state = ("BUS-OFF" if "BUS-OFF" in out else
                 "ERROR-PASSIVE" if "ERROR-PASSIVE" in out else
                 "ERROR-WARNING" if "ERROR-WARNING" in out else "ERROR-ACTIVE")
        berr = re.search(r"berr-counter tx (\d+) rx (\d+)", out)
        tec, rec = (int(berr.group(1)), int(berr.group(2))) if berr else (0, 0)
        fd = _can_fd_enabled(out)
    checks = {"not_bus_off": state != "BUS-OFF", "error_active": state == "ERROR-ACTIVE",
              "low_errors": tec < 96 and rec < 96}
    return CanHealth(iface, state, tec, rec, fd, checks)
