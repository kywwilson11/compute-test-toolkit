"""
OCP pci_lmt (PCIe Lane Margining Test) schema and CLI compatibility.

Two purposes:

1. **Schema portability** — re-emit ``MarginResult`` data in the per-lane record
   shape that the OCP reference tool ``ocptv-pci_lmt``
   (https://github.com/opencomputeproject/ocp-diag-pci_lmt) writes, so a
   `computetest margin` run drops into hyperscale pipelines that already
   consume that shape (Google / Meta / OCP datacenter NVMe lane).

2. **Drop-in invocation** — if the real OCP binary is installed on the host,
   ``call_pci_lmt()`` shells out to it so a production run can use the
   reference implementation when the user prefers it, without changing
   the upstream consumer.

The canonical per-lane column set (as of pci_lmt v0.3) is:

    bdf, speed, width, lmt_capable, ind_error_sampler, sample_reporting_method,
    ind_left_right_timing, ind_up_down_voltage, voltage_supported,
    num_voltage_steps, num_timing_steps, max_timing_offset, max_voltage_offset,
    sampling_rate_voltage, sampling_rate_timing, max_lanes, lane,
    receiver_number, margin_type, step, sample_count, sample_count_bits,
    error_count, ber

We emit ONE row per (lane × margin_type) representing the final passing step
the toolkit measured. Step-by-step margining sweeps are not (yet) a
computetest output — when we add them, this module gains additional rows per
lane without breaking the column set.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass

from .backend import ECAP_LANE_MARGINING, Backend
from .margining import MarginResult

# OCP defaults (per the pci_lmt CLI):
#   --error-count-limit 63 : stop margining a lane once ≥63 errors accrue
#   --dwell-time 5         : wait 5s at each step before reading the error count
DEFAULT_ERROR_COUNT_LIMIT = 63
DEFAULT_DWELL_TIME_S = 5

# Per the PCIe Lane Margining spec defaults — used when the device capability
# hasn't been read into a richer record (i.e. on the mock backend or before
# the LMT register-walk is wired up).
DEFAULT_NUM_TIMING_STEPS = 32
DEFAULT_NUM_VOLTAGE_STEPS = 64
DEFAULT_MAX_TIMING_OFFSET = 50           # 0.50 UI in 0.01-UI units
DEFAULT_MAX_VOLTAGE_OFFSET = 49          # 0.49 V in 0.01-V register units

# Canonical column order — keep CSV output in this order to match pci_lmt's.
COLUMNS: tuple[str, ...] = (
    "bdf", "speed", "width", "lmt_capable",
    "ind_error_sampler", "sample_reporting_method",
    "ind_left_right_timing", "ind_up_down_voltage",
    "voltage_supported", "num_voltage_steps", "num_timing_steps",
    "max_timing_offset", "max_voltage_offset",
    "sampling_rate_voltage", "sampling_rate_timing", "max_lanes",
    "lane", "receiver_number", "margin_type", "step",
    "sample_count", "sample_count_bits", "error_count", "ber",
)


@dataclass
class LmtLaneRecord:
    """One per-lane (or per-lane-per-margin-type) margining record.

    Fields match the OCP pci_lmt column set exactly so downstream consumers
    (Looker, BigQuery, Splunk, plain ``csv``/``jq``) can ingest a computetest
    run interchangeably with a pci_lmt run.
    """
    bdf: str
    speed: int
    width: int
    lmt_capable: bool
    ind_error_sampler: bool
    sample_reporting_method: int        # 0 = sample-count; 1 = error-count
    ind_left_right_timing: bool
    ind_up_down_voltage: bool
    voltage_supported: bool
    num_voltage_steps: int
    num_timing_steps: int
    max_timing_offset: int              # in 0.01 UI units
    max_voltage_offset: int             # in 0.01 V register units
    sampling_rate_voltage: int
    sampling_rate_timing: int
    max_lanes: int
    lane: int
    receiver_number: int
    margin_type: str                     # "TIMING" | "VOLTAGE"
    step: int
    sample_count: int
    sample_count_bits: int
    error_count: int
    ber: float


# ----------------------------------------------------------------------------
# Mapping: MarginResult -> [LmtLaneRecord]
# ----------------------------------------------------------------------------
def _step_from_timing_ui(timing_ui: float, num_steps: int,
                          max_offset_001ui: int) -> int:
    """Map a UI timing margin to the equivalent pci_lmt step number.

    pci_lmt reports the LARGEST step that survived ``error_count_limit`` errors.
    Our ``timing_ui`` is the UI margin to failure, so the matching step is
    ``round(timing_ui / (max_offset_in_UI / num_steps))``.
    """
    if num_steps <= 0 or max_offset_001ui <= 0:
        return 0
    max_ui = max_offset_001ui / 100.0
    step = round(timing_ui * num_steps / max_ui)
    return max(0, min(num_steps, step))


def _step_from_voltage_mv(voltage_mv: float, num_steps: int,
                          max_offset_001v: int) -> int:
    """Map a mV voltage margin to the equivalent pci_lmt step number.

    ``max_offset_001v`` is the MaxVoltageOffset register value in 0.01-V units
    (per the PCIe LMR spec), so convert it to mV (1 unit = 10 mV) before
    scaling -- mirroring how ``_step_from_timing_ui`` divides the 0.01-UI
    register value by 100.
    """
    if num_steps <= 0 or max_offset_001v <= 0:
        return 0
    max_offset_mv = max_offset_001v * 10.0
    step = round(voltage_mv * num_steps / max_offset_mv)
    return max(0, min(num_steps, step))


def from_margin_result(margin: MarginResult, *, backend: Backend,
                        receiver_number: int = 1,
                        sample_count: int = 18,
                        error_count_limit: int = DEFAULT_ERROR_COUNT_LIMIT,
                        ) -> list[LmtLaneRecord]:
    """Project a ``MarginResult`` onto pci_lmt's per-lane schema.

    One TIMING record per lane; one VOLTAGE record per lane that has a
    ``voltage_mv``. Link-level fields are read from the device; LMT cap-detail
    fields default to spec values until the device-cap register walk lands.

    ``receiver_number`` is the spec's per-RX number (1..6, with 6 reserved).
    Most downstream ports report on RX 1 — that's the default.
    """
    dev = backend.get_device(margin.bdf)
    lmt_capable = backend.find_ext_cap(margin.bdf, ECAP_LANE_MARGINING) is not None
    base: dict = {
        "bdf": margin.bdf,
        "speed": dev.current_link_speed,
        "width": dev.current_link_width,
        "lmt_capable": bool(lmt_capable),
        # The remaining LMT cap-detail fields are placeholders until the cap
        # register-walk lands. Documented defaults — NOT device-read.
        "ind_error_sampler": False,
        "sample_reporting_method": 0,                # 0 = sample-count
        "ind_left_right_timing": False,
        "ind_up_down_voltage": False,
        "voltage_supported": False,
        "num_voltage_steps": DEFAULT_NUM_VOLTAGE_STEPS,
        "num_timing_steps": DEFAULT_NUM_TIMING_STEPS,
        "max_timing_offset": DEFAULT_MAX_TIMING_OFFSET,
        "max_voltage_offset": DEFAULT_MAX_VOLTAGE_OFFSET,
        "sampling_rate_voltage": 0,
        "sampling_rate_timing": 0,
        "max_lanes": dev.max_link_width,
    }
    out: list[LmtLaneRecord] = []
    # OCP pci_lmt: sample_count is the raw 7-bit MSampleCount register value
    # (0..127); sample_count_bits = 2**(sample_count/3) is the bits margined.
    sample_count_bits = int(2 ** (sample_count / 3))
    for lane in margin.lanes:
        # The toolkit returns the LAST passing margin — error_count is 0 there,
        # by definition. If we extend to a step-by-step sweep, this constructor
        # will gain records at higher steps with error_count > 0.
        step_timing = _step_from_timing_ui(
            lane.timing_ui, base["num_timing_steps"], base["max_timing_offset"])
        out.append(LmtLaneRecord(
            **base, lane=lane.lane, receiver_number=receiver_number,
            margin_type="TIMING", step=step_timing,
            sample_count=sample_count, sample_count_bits=sample_count_bits,
            error_count=0, ber=0.0,
        ))
        if lane.voltage_mv is not None:
            step_v = _step_from_voltage_mv(
                lane.voltage_mv, base["num_voltage_steps"],
                base["max_voltage_offset"])
            out.append(LmtLaneRecord(
                **{**base, "voltage_supported": True},
                lane=lane.lane, receiver_number=receiver_number,
                margin_type="VOLTAGE", step=step_v,
                sample_count=sample_count, sample_count_bits=sample_count_bits,
                error_count=0, ber=0.0,
            ))
    return out


# ----------------------------------------------------------------------------
# Output formatters
# ----------------------------------------------------------------------------
def to_json(records: list[LmtLaneRecord]) -> str:
    """Emit pci_lmt-style JSON: one top-level list of per-lane records."""
    return json.dumps([asdict(r) for r in records], indent=2)


def to_csv(records: list[LmtLaneRecord]) -> str:
    """Emit pci_lmt-style CSV with the canonical column order."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS))
    w.writeheader()
    for r in records:
        row = asdict(r)
        # Emit booleans as Python-friendly literals (pci_lmt uses True/False too).
        w.writerow(row)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# Drop-in subprocess: call the real ocptv-pci_lmt if installed
# ----------------------------------------------------------------------------
def is_pci_lmt_available() -> bool:
    """True iff the ``pci_lmt`` binary (from ``ocptv-pci_lmt`` on PyPI) is on
    PATH. Use this to decide between native margining + ``from_margin_result``
    and a true OCP-reference invocation."""
    return shutil.which("pci_lmt") is not None


def call_pci_lmt(config_path: str, *, output_format: str = "json",
                 error_count_limit: int = DEFAULT_ERROR_COUNT_LIMIT,
                 dwell_time_s: int = DEFAULT_DWELL_TIME_S,
                 annotation: str = "") -> str:
    """Shell out to the OCP reference ``pci_lmt`` and return its stdout.

    Raises ``RuntimeError`` if ``pci_lmt`` is not on PATH or its exit code is
    non-zero. ``config_path`` is the pci_lmt YAML config; the same flags are
    documented in the project README.
    """
    if not is_pci_lmt_available():
        raise RuntimeError(
            "pci_lmt not on PATH; install with `pip install ocptv-pci_lmt`")
    cmd = ["pci_lmt", "-o", output_format,
           "-e", str(error_count_limit), "-d", str(dwell_time_s)]
    if annotation:
        cmd += ["-a", annotation]
    cmd.append(config_path)
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pci_lmt failed (rc={proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout
