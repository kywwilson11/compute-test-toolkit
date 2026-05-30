"""
GMSL margining -> OCP pci_lmt schema adapter (Sprint 4.1).

Projects GMSL SerDes EOM eye margin onto the same per-lane ``LmtLaneRecord``
shape the PCIe margining uses, so GMSL eye/voltage/temperature margining lands
in the same BigQuery / CSV / Looker pipeline as PCIe lane margining. The pci_lmt
``margin_type`` field is a string, so it extends naturally beyond TIMING|VOLTAGE
to EYE_V, EYE_H, VOLTAGE, and TEMP — no schema change, and the records flow
through the existing ``lmt_adapter.to_json`` / ``to_csv`` emitters unchanged.
"""
from __future__ import annotations

from ..lmt_adapter import (
    DEFAULT_MAX_TIMING_OFFSET,
    DEFAULT_MAX_VOLTAGE_OFFSET,
    DEFAULT_NUM_TIMING_STEPS,
    DEFAULT_NUM_VOLTAGE_STEPS,
    LmtLaneRecord,
    _step_from_timing_ui,
    _step_from_voltage_mv,
)
from .serdes import GmslMode, LinkDirection, SerDesLink

# GMSL-specific margin_type values layered onto the pci_lmt string field.
MARGIN_EYE_V = "EYE_V"
MARGIN_EYE_H = "EYE_H"
MARGIN_VOLTAGE = "VOLTAGE"
MARGIN_TEMP = "TEMP"

# Negotiated mode -> a synthetic per-row "speed" (Mbit/s) so the link rate is
# queryable alongside the margin in the same column.
_MODE_SPEED = {
    GmslMode.PAM4_12G: 12000, GmslMode.NRZ_6G: 6000,
    GmslMode.GMSL2_6G: 6000, GmslMode.GMSL2_3G: 3000,
    GmslMode.GMSL1: 1500, GmslMode.UNKNOWN: 0,
}


def _base_fields(part: str, serial: str, mode: GmslMode) -> dict:
    """The pci_lmt link-level fields for a GMSL link. GMSL has no PCIe Lane
    Margining capability, so the LMT cap-detail fields are honest defaults; the
    voltage/timing step ranges are reused to encode the eye openings."""
    return {
        "bdf": f"gmsl:{part}:{serial}",
        "speed": _MODE_SPEED.get(mode, 0),
        "width": 1,                         # one differential pair per GMSL link
        "lmt_capable": False,               # GMSL has no PCIe LMT capability
        "ind_error_sampler": False,
        "sample_reporting_method": 0,
        "ind_left_right_timing": False,
        "ind_up_down_voltage": False,
        "voltage_supported": True,
        "num_voltage_steps": DEFAULT_NUM_VOLTAGE_STEPS,
        "num_timing_steps": DEFAULT_NUM_TIMING_STEPS,
        "max_timing_offset": DEFAULT_MAX_TIMING_OFFSET,
        "max_voltage_offset": DEFAULT_MAX_VOLTAGE_OFFSET,
        "sampling_rate_voltage": 0,
        "sampling_rate_timing": 0,
        "max_lanes": 1,
    }


def eom_to_lmt_records(serdes: SerDesLink, *,
                       direction: LinkDirection = LinkDirection.FORWARD
                       ) -> list[LmtLaneRecord]:
    """Read EOM for every link and emit EYE_V + EYE_H per-link records in the
    pci_lmt column set. ``lane`` carries the GMSL link index; ``step`` encodes
    the eye opening via the same step-mapping the PCIe adapter uses (the worst
    PAM4 sub-eye for EYE_V, the horizontal opening for EYE_H)."""
    info = serdes.info()
    out: list[LmtLaneRecord] = []
    for link in range(info.links):
        eom = serdes.read_eom(link, direction)
        base = _base_fields(info.part_number, info.serial, eom.mode)
        step_v = _step_from_voltage_mv(eom.worst_vertical_mv,
                                       base["num_voltage_steps"],
                                       base["max_voltage_offset"])
        out.append(LmtLaneRecord(
            **base, lane=link, receiver_number=1, margin_type=MARGIN_EYE_V,
            step=step_v, sample_count=0, sample_count_bits=0,
            error_count=0, ber=0.0))
        step_h = _step_from_timing_ui(eom.horizontal_ui,
                                      base["num_timing_steps"],
                                      base["max_timing_offset"])
        out.append(LmtLaneRecord(
            **base, lane=link, receiver_number=1, margin_type=MARGIN_EYE_H,
            step=step_h, sample_count=0, sample_count_bits=0,
            error_count=0, ber=0.0))
    return out
