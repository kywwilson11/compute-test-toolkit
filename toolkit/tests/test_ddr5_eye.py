"""Sprint 4.3.6: DDR5 per-DQ eye + DFE (reuses retimer EyeMeasurement)."""
from __future__ import annotations

from computetest.ddr5 import Ddr5EyeHealth, check_dq_eye
from computetest.pcie.retimer.base import EqLevels, EyeMeasurement


class TestDqEye:
    def test_good_eye_passes(self):
        h = check_dq_eye(dq=0, eye_ui=0.30, eye_mv=80.0, dfe_taps=[0.4, -0.2, 0.05])
        assert isinstance(h, Ddr5EyeHealth) and h.ok
        assert isinstance(h.eye, EyeMeasurement) and isinstance(h.eq, EqLevels)
        assert h.eq.dfe_taps == [0.4, -0.2, 0.05]

    def test_weak_eye_fails(self):
        h = check_dq_eye(dq=0, eye_ui=0.10, eye_mv=20.0, dfe_taps=[])
        assert not h.ok and h.checks["eye_open"] is False

    def test_summary_and_to_dict(self):
        h = check_dq_eye(dq=3, eye_ui=0.30, eye_mv=80.0, dfe_taps=[0.4])
        assert "DDR5 DQ3" in h.summary()
        assert h.to_dict()["eye"]["eye_mv"] == 80.0
