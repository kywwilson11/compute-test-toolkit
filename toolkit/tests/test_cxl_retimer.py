"""Sprint 4.4.10: CXL link-extension retimer (reuses the Retimer ABC verbatim)."""
from __future__ import annotations

from computetest.cxl import CxlRetimerHealth, check_cxl_retimer
from computetest.pcie.retimer import MockRetimer


class TestCxlRetimer:
    def test_expected_part_good_eye_passes(self):
        rt = MockRetimer(part_number="PT6161L", injected_eye_ui=0.32,
                         injected_eye_mv=75.0, injected_temperature_c=60.0)
        h = check_cxl_retimer(rt, expect_part="PT6161L")
        assert isinstance(h, CxlRetimerHealth) and h.ok

    def test_wrong_part_fails(self):
        rt = MockRetimer(part_number="OTHER")
        h = check_cxl_retimer(rt, expect_part="PT6161L")
        assert not h.ok and h.checks["part_identity_ok"] is False

    def test_weak_eye_fails(self):
        rt = MockRetimer(part_number="PT6161L", injected_eye_ui=0.10,
                         injected_eye_mv=10.0)
        h = check_cxl_retimer(rt, expect_part="PT6161L")
        assert not h.ok and h.checks["all_lanes_eye_open"] is False

    def test_overtemp_fails(self):
        rt = MockRetimer(part_number="PT6161L", injected_temperature_c=120.0)
        h = check_cxl_retimer(rt, expect_part="PT6161L")
        assert not h.ok and h.checks["temperature_ok"] is False

    def test_lane_subset(self):
        rt = MockRetimer(part_number="PT6161L")
        h = check_cxl_retimer(rt, expect_part="PT6161L", lanes=[0, 1])
        assert h.ok

    def test_summary_and_to_dict(self):
        rt = MockRetimer(part_number="PT6161L")
        h = check_cxl_retimer(rt, expect_part="PT6161L")
        assert "CXL retimer PT6161L" in h.summary()
        assert h.to_dict()["part_number"] == "PT6161L"
