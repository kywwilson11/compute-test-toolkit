"""
Sprint 4.1.7: GMSL margining -> pci_lmt LmtLaneRecord adapter.

GMSL EOM eye margin is projected onto the PCIe pci_lmt column set (new
margin_type values EYE_V/EYE_H) so it rides the existing JSON/CSV emitters.
"""
from __future__ import annotations

from computetest.gmsl import GmslMode, MockSerDes
from computetest.gmsl.lmt import MARGIN_EYE_H, MARGIN_EYE_V, eom_to_lmt_records
from computetest.lmt_adapter import COLUMNS, LmtLaneRecord, to_csv, to_json


class TestEomToLmt:
    def test_two_records_per_link(self):
        recs = eom_to_lmt_records(MockSerDes(links=2))
        assert len(recs) == 4                       # EYE_V + EYE_H per link
        assert all(isinstance(r, LmtLaneRecord) for r in recs)
        assert {r.margin_type for r in recs} == {MARGIN_EYE_V, MARGIN_EYE_H}

    def test_lane_carries_link_index(self):
        recs = eom_to_lmt_records(MockSerDes(links=2))
        assert {r.lane for r in recs} == {0, 1}

    def test_bdf_and_speed_encode_gmsl(self):
        r = eom_to_lmt_records(MockSerDes(part_number="MAX96792A", serial="SN9",
                                          injected_mode=GmslMode.PAM4_12G))[0]
        assert r.bdf == "gmsl:MAX96792A:SN9"
        assert r.speed == 12000 and r.width == 1

    def test_step_reflects_eye_opening(self):
        strong = eom_to_lmt_records(MockSerDes(injected_eye_mv=180.0))
        weak = eom_to_lmt_records(MockSerDes(injected_eye_mv=20.0))
        v_strong = next(r.step for r in strong if r.margin_type == MARGIN_EYE_V)
        v_weak = next(r.step for r in weak if r.margin_type == MARGIN_EYE_V)
        assert v_strong > v_weak                    # bigger eye -> higher step

    def test_records_flow_through_pci_lmt_formatters(self):
        recs = eom_to_lmt_records(MockSerDes(links=1))
        # The whole point: GMSL rows ride the existing pci_lmt JSON/CSV emitters.
        js = to_json(recs)
        assert "EYE_V" in js and "EYE_H" in js
        header = to_csv(recs).splitlines()[0]
        assert header == ",".join(COLUMNS)          # canonical pci_lmt columns
