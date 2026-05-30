"""Sprint 4.3.10: DDR5 ECC reporting-plumbing soak snapshot."""
from __future__ import annotations

from computetest.ddr5 import MockDdr5Ras, SoakSnapshot, soak_snapshot


class TestSoakSnapshot:
    def test_snapshot_captures_state(self):
        ras = MockDdr5Ras()
        ras.inject_ce("DIMM_A1", 4)
        snap = soak_snapshot(ras)
        assert isinstance(snap, SoakSnapshot)
        assert snap.total_ce == 4 and snap.ecs_threshold == 1024
        assert snap.hppr_resources == 4
        assert snap.to_dict()["total_ce"] == 4

    def test_diff_across_soak(self):
        ras = MockDdr5Ras()
        before = soak_snapshot(ras)
        ras.inject_ce("DIMM_A1", 2)
        after = soak_snapshot(ras)
        assert after.total_ce - before.total_ce == 2
