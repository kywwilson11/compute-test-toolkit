"""Sprint 4.3.3: DDR5 ECS control + counter readback."""
from __future__ import annotations

from computetest.ddr5 import EcsHealth, MockDdr5Ras, check_ecs


class TestEcs:
    def test_settings_stick_and_scrub_in_budget(self):
        h = check_ecs(MockDdr5Ras(injected_scrub_corrected=12, injected_scrub_max_row=5),
                      threshold=4096)
        assert isinstance(h, EcsHealth) and h.ok
        assert h.threshold == 4096
        assert h.corrected_count == 12 and h.max_error_row == 5

    def test_scrub_time_over_24h_fails(self):
        ras = MockDdr5Ras()
        ras.set_scrub(cycle_duration_s=100_000)               # > 86400
        h = check_ecs(ras)
        assert not h.ok and h.checks["scrub_time_within_24h"] is False
        assert "FAIL(" in h.summary()

    def test_summary_and_to_dict(self):
        h = check_ecs(MockDdr5Ras())
        assert "DDR5 ECS" in h.summary()
        assert h.to_dict()["threshold"] == 1024
