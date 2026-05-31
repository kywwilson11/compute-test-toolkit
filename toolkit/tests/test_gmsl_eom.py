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

    def test_tightened_threshold_fails_eye_above_default(self):
        # An operator-tightened EOM floor must be honored: a 70 mV eye that
        # clears the 40 mV default FAILS at mv_min=100, and check_eom must agree
        # with the device's own eom_verdict (the regression for check_eom
        # ignoring set_eom_thresholds).
        s = MockSerDes(injected_eye_mv=70.0)
        s.set_eom_thresholds(mv_min=100.0)
        assert s.eom_verdict(s.read_eom(0)) is False
        h = check_eom(s)
        assert h.checks["eye_open"] is False
        assert not h.ok

    def test_reverse_nrz_single_eye(self):
        h = check_eom(MockSerDes(), direction=LinkDirection.REVERSE)
        assert len(h.subeyes_mv) == 1
        assert h.direction == "reverse"

    def test_summary_and_to_dict(self):
        h = check_eom(MockSerDes())
        assert "GMSL EOM" in h.summary()
        d = h.to_dict()
        assert d["eye"]["eye_mv"] == h.eye.eye_mv and "subeyes_mv" in d

    def test_empty_vertical_mv_does_not_crash(self):
        # A real backend can return an empty vertical_mv (register misparse / N/A);
        # check_eom must degrade like worst_vertical_mv (0.0), not raise ValueError.
        from computetest.gmsl import EomReading
        from computetest.gmsl.serdes import GmslMode as _GmslMode
        from computetest.gmsl.serdes import LinkDirection as _LinkDirection

        class _EmptyEyeSerDes(MockSerDes):
            def read_eom(self, link, direction=_LinkDirection.FORWARD):
                return EomReading(link=link, direction=direction,
                                  mode=_GmslMode.PAM4_12G, vertical_mv=[],
                                  horizontal_ui=0.30)

        h = check_eom(_EmptyEyeSerDes())
        assert h.eye.height_mv == 0.0
        assert h.eye.eye_mv == 0.0
        assert h.checks["eye_open"] is False        # 0 mV fails mv_min
