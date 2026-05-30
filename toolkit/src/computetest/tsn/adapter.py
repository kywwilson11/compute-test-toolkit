"""
TSN result schema + certified-CTT drop-in (Sprint 4.2).

Mirrors ``lmt_adapter.py``: a stable per-test column set with ``to_json`` /
``to_csv`` so a TSN run drops into the same BigQuery / Looker pipeline as PCIe
margin, plus ``is_ttworkbench_available()`` / ``call_ttworkbench()`` and a
linuxptp ``pmc`` drop-in mirroring ``is_pci_lmt_available()`` / ``call_pci_lmt()``
— a lab can run the certified Conformance Test Tool while keeping one output
contract.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass

# Stable per-test column set — keep this order for CSV to match consumers.
TSN_COLUMNS: tuple[str, ...] = (
    "test_id", "group", "title", "result", "metric", "value", "unit", "limit",
)


@dataclass
class TsnTestRecord:
    """One TSN conformance-test result in the stable column set."""
    test_id: str
    group: str
    title: str
    result: str                 # "pass" | "fail" | "skip"
    metric: str
    value: float
    unit: str
    limit: float | None = None


def to_json(records: list[TsnTestRecord]) -> str:
    """Emit the records as a JSON list (one object per test)."""
    return json.dumps([asdict(r) for r in records], indent=2)


def to_csv(records: list[TsnTestRecord]) -> str:
    """Emit the records as CSV in the canonical column order."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(TSN_COLUMNS))
    w.writeheader()
    for r in records:
        w.writerow(asdict(r))
    return buf.getvalue()


def is_ttworkbench_available() -> bool:
    """True iff the VIAVI TTworkbench CTT CLI is on PATH."""
    return shutil.which("ttworkbench") is not None


def call_ttworkbench(config_path: str, *, output_format: str = "json") -> str:
    """Shell out to the certified TTworkbench CTT and return its stdout. Raises
    ``RuntimeError`` if it's not on PATH or exits non-zero."""
    if not is_ttworkbench_available():
        raise RuntimeError("ttworkbench not on PATH (install the VIAVI CTT)")
    proc = subprocess.run(  # pragma: no cover - real-CTT path
        ["ttworkbench", "--output", output_format, config_path],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:  # pragma: no cover - real-CTT path
        raise RuntimeError(
            f"ttworkbench failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout  # pragma: no cover - real-CTT path


def is_linuxptp_available() -> bool:
    """True iff linuxptp's ``pmc`` is on PATH (for offline gPTP protocol checks)."""
    return shutil.which("pmc") is not None


def call_linuxptp_pmc(query: str = "GET CURRENT_DATA_SET") -> str:
    """Run a linuxptp ``pmc`` management query and return its stdout."""
    if not is_linuxptp_available():
        raise RuntimeError("pmc not on PATH (install linuxptp)")
    proc = subprocess.run(  # pragma: no cover - real-host path
        ["pmc", "-u", "-b", "0", query], capture_output=True, text=True, check=False)
    if proc.returncode != 0:  # pragma: no cover - real-host path
        raise RuntimeError(f"pmc failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout  # pragma: no cover - real-host path
