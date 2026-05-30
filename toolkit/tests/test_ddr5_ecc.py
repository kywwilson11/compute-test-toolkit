"""Sprint 4.3.2: DDR5 on-die ECC + EDAC reporting (EINJ CE/UE)."""
from __future__ import annotations

from computetest.ddr5 import (
    EccReportHealth,
    MockDdr5Ras,
    check_ce_reporting,
    check_ue_reporting,
)


class TestCeReporting:
    def test_ce_reported_on_correct_dimm(self):
        h = check_ce_reporting(MockDdr5Ras(dimms=("DIMM_A1", "DIMM_A2")),
                               dimm="DIMM_A1")
        assert isinstance(h, EccReportHealth) and h.ok
        assert h.checks == {"ce_counter_incremented": True,
                            "correct_dimm_attribution": True, "no_spurious_ue": True}
        assert h.ce_delta == 1 and h.ue_delta == 0


class TestUeReporting:
    def test_ue_reported_not_corrected(self):
        h = check_ue_reporting(MockDdr5Ras())
        assert h.ok and h.ue_delta == 1
        assert h.checks["ue_reported"] is True
        assert h.checks["not_silently_corrected"] is True

    def test_summary_and_to_dict(self):
        h = check_ue_reporting(MockDdr5Ras())
        assert "DDR5 ECC UE" in h.summary()
        assert h.to_dict()["error_class"] == "UE"


class TestHealthShape:
    def test_fail_summary_branch(self):
        h = EccReportHealth(error_class="CE", ce_delta=0, ue_delta=0,
                            checks={"ce_counter_incremented": False})
        assert not h.ok and "FAIL(" in h.summary()
