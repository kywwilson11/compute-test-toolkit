"""
Kernel-log (dmesg) monitoring for PCIe/AER events — a complementary, kernel-decoded
view alongside the register reads. The Linux pcieport/AER driver logs events like:

    pcieport 0000:00:1c.0: AER: Corrected error received: 0000:04:00.0
    pcieport 0000:00:1c.0: AER: Uncorrected (Non-Fatal) error received: 0000:04:00.0
    nvme 0000:04:00.0: PCIe Bus Error: severity=Corrected, type=Physical Layer ...

This is NOT the fast path — it's a per-window before/after diff (Python, ms cadence)
that corroborates the register-based BERT and catches events the kernel handled (it
may clear AER out from under us). The reader is injectable so it runs/tests on a
laptop with no real kernel log.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

_BDF = re.compile(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]")
_KEYWORDS = ("AER", "PCIe Bus Error", "link is down", "Link Down", "link down")


@dataclass
class DmesgEvent:
    severity: str           # "corrected" | "non_fatal" | "fatal" | "link" | "other"
    bdfs: list[str]         # all BDFs named on the line (source + reporter)
    text: str

    @property
    def uncorrectable(self) -> bool:
        return self.severity in ("non_fatal", "fatal")

    def involves(self, bdf: str) -> bool:
        return bdf in self.bdfs


def _severity(line: str) -> str:
    low = line.lower()
    if "fatal" in low and "non-fatal" not in low:
        return "fatal"
    if "non-fatal" in low or "uncorrected" in low:
        return "non_fatal"
    if "corrected" in low:
        return "corrected"
    if "link is down" in low or "link down" in low:
        return "link"
    return "other"


def parse_events(text: str, only_bdfs: list[str] | None = None) -> list[DmesgEvent]:
    """Extract PCIe/AER events from kernel-log text, optionally only those naming one
    of ``only_bdfs``."""
    keep = set(only_bdfs) if only_bdfs is not None else None
    events = []
    for line in text.splitlines():
        if not any(k in line for k in _KEYWORDS):
            continue
        bdfs = _BDF.findall(line)
        if keep is not None and not (set(bdfs) & keep):
            continue
        events.append(DmesgEvent(_severity(line), bdfs, line.strip()))
    return events


def read_kernel_log(reader=None) -> str:
    """Return the current kernel log. ``reader`` (a no-arg callable) overrides the
    source — used by tests and the mock; default shells out to `dmesg` on Linux."""
    if reader is not None:
        return reader()
    if not os.path.isdir("/sys/bus/pci"):   # not a Linux PCI host -> nothing to read
        return ""
    try:  # pragma: no cover - real-hw path
        return subprocess.run(["dmesg", "--ctime"], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


@dataclass
class DmesgMonitor:
    """Capture PCIe/AER kernel-log events that appear during a window (before/after diff)."""
    reader: object = None
    _baseline: set = field(default_factory=set)

    def start(self) -> None:
        self._baseline = set(read_kernel_log(self.reader).splitlines())

    def collect(self, only_bdfs: list[str] | None = None) -> list[DmesgEvent]:
        new_lines = [ln for ln in read_kernel_log(self.reader).splitlines()
                     if ln not in self._baseline]
        return parse_events("\n".join(new_lines), only_bdfs)
