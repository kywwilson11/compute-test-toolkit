"""Sprint 4.3.1: DDR5 RAS control-plane adapter (ECS / scrub / mem_repair / EDAC)."""
from __future__ import annotations

import pytest

from computetest.ddr5 import (
    Ddr5RasError,
    EcsConfig,
    MemRepairRequest,
    MockDdr5Ras,
    ScrubConfig,
)


class TestEcs:
    def test_read_default(self):
        ecs = MockDdr5Ras().read_ecs()
        assert isinstance(ecs, EcsConfig) and ecs.threshold == 1024

    def test_set_mode_and_threshold(self):
        d = MockDdr5Ras()
        d.set_ecs(mode="counts_rows", threshold=4096)
        e = d.read_ecs()
        assert e.mode == "counts_rows" and e.threshold == 4096
        assert e.enabled is True                          # unchanged
        assert "threshold" in e.to_dict()

    def test_set_log_and_enabled(self):
        d = MockDdr5Ras()
        d.set_ecs(log_entry_type=1, enabled=False)
        e = d.read_ecs()
        assert e.log_entry_type == 1 and e.enabled is False
        assert e.threshold == 1024                        # unchanged

    def test_bad_threshold_rejected(self):
        with pytest.raises(Ddr5RasError, match="threshold"):
            MockDdr5Ras().set_ecs(threshold=999)

    def test_scrub_cycle_reports_counts(self):
        d = MockDdr5Ras(injected_scrub_corrected=42, injected_scrub_max_row=7)
        assert d.trigger_scrub_cycle() == (42, 7)


class TestScrub:
    def test_set_enable_background(self):
        d = MockDdr5Ras()
        d.set_scrub(enable_background=False)
        s = d.read_scrub()
        assert isinstance(s, ScrubConfig) and s.enable_background is False
        assert s.cycle_duration_s == 3600                 # unchanged

    def test_set_cycle_duration(self):
        d = MockDdr5Ras()
        d.set_scrub(cycle_duration_s=7200)
        assert d.read_scrub().cycle_duration_s == 7200
        assert d.read_scrub().to_dict()["cycle_duration_s"] == 7200


class TestEdacAndEinj:
    def test_inject_and_read(self):
        d = MockDdr5Ras(dimms=("DIMM_A1", "DIMM_A2"))
        d.inject_ce("DIMM_A1", 3)
        d.inject_ue(1)
        ce, ue, per = d.read_edac()
        assert ce == 3 and ue == 1 and per["DIMM_A1"] == 3

    def test_inject_bad_dimm_rejected(self):
        with pytest.raises(Ddr5RasError, match="unknown DIMM"):
            MockDdr5Ras().inject_ce("DIMM_X9", 1)


class TestRepair:
    def test_sppr_reverts_hppr_survives_power_cycle(self):
        d = MockDdr5Ras()
        d.perform_repair(MemRepairRequest(persist_mode=0, hpa=0x1000))   # sPPR
        d.perform_repair(MemRepairRequest(persist_mode=1, hpa=0x2000))   # hPPR
        assert d.is_repaired(0x1000) and d.is_repaired(0x2000)
        d.power_cycle()
        assert not d.is_repaired(0x1000)                  # sPPR reverted
        assert d.is_repaired(0x2000)                      # hPPR survived

    def test_bad_repair_type_rejected(self):
        with pytest.raises(Ddr5RasError, match="repair_type"):
            MockDdr5Ras().perform_repair(MemRepairRequest(repair_type="bogus"))

    def test_power_cycle_resets_counters(self):
        d = MockDdr5Ras()
        d.inject_ce("DIMM_A1", 5)
        d.power_cycle()
        ce, ue, _ = d.read_edac()
        assert ce == 0 and ue == 0

    def test_repair_request_to_dict(self):
        d = MemRepairRequest(hpa=0x4000, bank=2, row=10, column=3).to_dict()
        assert d["hpa"] == 0x4000 and d["repair_type"] == "ppr"
