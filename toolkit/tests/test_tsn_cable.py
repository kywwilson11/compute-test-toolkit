"""Sprint 4.2.10: automotive single-pair cable TDR (OABR_CABLE_01/02)."""
from __future__ import annotations

from computetest.tsn import CableHealth, MockPhy, check_cable_tdr


class TestCableTdr:
    def test_clean_cable_passes(self):
        h = check_cable_tdr(MockPhy())
        assert isinstance(h, CableHealth) and h.ok
        assert h.status == "ok" and h.fault_distance_m is None

    def test_open_fault_fails_with_distance(self):
        h = check_cable_tdr(MockPhy(injected_tdr_fault=True))
        assert not h.ok and h.status == "fault"
        assert h.fault_distance_m == 3.5

    def test_summary_and_to_dict(self):
        h = check_cable_tdr(MockPhy(injected_tdr_fault=True))
        assert "cable TDR" in h.summary()
        d = h.to_dict()
        assert d["status"] == "fault" and d["fault_distance_m"] == 3.5
