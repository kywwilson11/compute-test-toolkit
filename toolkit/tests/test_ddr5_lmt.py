"""Sprint 4.3.5: DDR5 training-margin -> pci_lmt LmtLaneRecord adapter."""
from __future__ import annotations

from computetest.ddr5.lmt import DqMargin, training_to_lmt_records
from computetest.lmt_adapter import COLUMNS, LmtLaneRecord, to_csv, to_json


class TestTrainingToLmt:
    def test_two_records_per_dq(self):
        recs = training_to_lmt_records([DqMargin(byte_lane=0, dq=0,
                                                 timing_ui=0.3, voltage_mv=120)])
        assert len(recs) == 2
        assert all(isinstance(r, LmtLaneRecord) for r in recs)
        assert {r.margin_type for r in recs} == {"TIMING", "VOLTAGE"}
        assert all(r.lane == 0 for r in recs)

    def test_bdf_encodes_byte_lane_and_channel(self):
        r = training_to_lmt_records([DqMargin(1, 3, 0.3, 120)], channel=2)[0]
        assert r.bdf == "ddr5:ch2:bl1" and r.lane == 3

    def test_step_reflects_margin(self):
        strong = training_to_lmt_records([DqMargin(0, 0, 0.45, 190)])
        weak = training_to_lmt_records([DqMargin(0, 0, 0.05, 20)])
        t_strong = next(r.step for r in strong if r.margin_type == "TIMING")
        t_weak = next(r.step for r in weak if r.margin_type == "TIMING")
        assert t_strong > t_weak

    def test_flows_through_pci_lmt_formatters(self):
        recs = training_to_lmt_records([DqMargin(0, 0, 0.3, 120)])
        assert "TIMING" in to_json(recs) and "VOLTAGE" in to_json(recs)
        assert to_csv(recs).splitlines()[0] == ",".join(COLUMNS)
