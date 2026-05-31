"""
DDR5 training-margin -> OCP pci_lmt schema adapter (Sprint 4.3).

Projects per-DQ / per-byte-lane DDR5 training margins (from MRC / AGESA) onto the
same ``LmtLaneRecord`` column set the PCIe margining uses — TIMING + VOLTAGE per
DQ — so DDR5 read/write margin lands in the same BigQuery / CSV pipeline as PCIe
lane margin via the existing ``lmt_adapter.to_json`` / ``to_csv`` emitters.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..lmt_adapter import (
    DEFAULT_MAX_TIMING_OFFSET,
    DEFAULT_MAX_VOLTAGE_OFFSET,
    DEFAULT_NUM_TIMING_STEPS,
    DEFAULT_NUM_VOLTAGE_STEPS,
    LmtLaneRecord,
    _step_from_timing_ui,
    _step_from_voltage_mv,
)


@dataclass
class DqMargin:
    """One DQ's trained margin: the timing (UI) and voltage (mV) eye to failure."""
    byte_lane: int
    dq: int
    timing_ui: float
    voltage_mv: float


def _base(part: str, channel: int, byte_lane: int) -> dict:
    return {
        "bdf": f"{part}:ch{channel}:bl{byte_lane}",
        "speed": 0, "width": 1, "lmt_capable": False,
        "ind_error_sampler": False, "sample_reporting_method": 0,
        # A single combined eye-to-failure per axis is emitted (one TIMING and
        # one VOLTAGE record per DQ), so the directional-split flags are False,
        # matching from_margin_result / gmsl.
        "ind_left_right_timing": False, "ind_up_down_voltage": False,
        "voltage_supported": True,
        "num_voltage_steps": DEFAULT_NUM_VOLTAGE_STEPS,
        "num_timing_steps": DEFAULT_NUM_TIMING_STEPS,
        "max_timing_offset": DEFAULT_MAX_TIMING_OFFSET,
        "max_voltage_offset": DEFAULT_MAX_VOLTAGE_OFFSET,
        "sampling_rate_voltage": 0, "sampling_rate_timing": 0, "max_lanes": 1,
    }


def training_to_lmt_records(margins: Sequence[DqMargin], *, part: str = "ddr5",
                            channel: int = 0) -> list[LmtLaneRecord]:
    """Project per-DQ DDR5 training margins onto pci_lmt records: a TIMING record
    and a VOLTAGE record per DQ. ``lane`` carries the DQ index; the byte lane and
    channel are encoded in the bdf, and ``step`` reuses the PCIe adapter's
    step-mapping."""
    out: list[LmtLaneRecord] = []
    for m in margins:
        base = _base(part, channel, m.byte_lane)
        out.append(LmtLaneRecord(
            **base, lane=m.dq, receiver_number=1, margin_type="TIMING",
            step=_step_from_timing_ui(m.timing_ui, base["num_timing_steps"],
                                      base["max_timing_offset"]),
            sample_count=0, sample_count_bits=0, error_count=0, ber=0.0))
        out.append(LmtLaneRecord(
            **base, lane=m.dq, receiver_number=1, margin_type="VOLTAGE",
            step=_step_from_voltage_mv(m.voltage_mv, base["num_voltage_steps"],
                                       base["max_voltage_offset"]),
            sample_count=0, sample_count_bits=0, error_count=0, ber=0.0))
    return out
