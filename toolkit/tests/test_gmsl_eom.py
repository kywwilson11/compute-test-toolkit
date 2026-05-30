"""
Sprint 4.1.5: GMSL EOM eye-margin check, mapped onto the retimer EyeMeasurement.

The PAM4 worst sub-eye becomes eye_mv, and the verdict reuses the retimer's
eye_quality_verdict with GMSL thresholds — proving GMSL eye data rides the same
portable shape as PCIe retimer eye data.
"""
from __future__ import annotations

from computetest.gmsl import (
    EomHealth,
    GmslMode,
    LinkDirection,
    MockSerDes,
    check_eom,
)
from computetest.pcie.retimer.base import EyeMeasurement


class TestEomCheck:
    def test_pam4_eye_maps_to_eyemeasurement_worst_subeye(self):
        h = check_eom(MockSerDes(injected_mode=GmslMode.PAM4_12G, injected_eye_mv=70.0))
        assert isinstance(h, EomHealth)
        assert isinstance(h.eye, EyeMeasurement)
        assert len(h.subeyes_mv) == 3
        assert h.eye.eye_mv == min(h.subeyes_mv)        # worst sub-eye is the margin
        assert h.eye.height_mv == max(h.subeyes_mv)
        assert h.ok

    def test_weak_eye_fails_verdict(self):
        h = check_eom(MockSerDes(injected_eye_mv=10.0))
        assert not h.ok
        assert h.checks["eye_open"] is False

    def test_reverse_nrz_single_eye(self):
        h = check_eom(MockSerDes(), direction=LinkDirection.REVERSE)
        assert len(h.subeyes_mv) == 1
        assert h.direction == "reverse"

    def test_summary_and_to_dict(self):
        h = check_eom(MockSerDes())
        assert "GMSL EOM" in h.summary()
        d = h.to_dict()
        assert d["eye"]["eye_mv"] == h.eye.eye_mv and "subeyes_mv" in d
