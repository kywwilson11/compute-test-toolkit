"""
Sprint 2.2: PCIe retimer abstraction — vendor-agnostic contract + Mock + Aries stub.

These tests pin the public surface so future Synopsys / TI / Marvell vendor
implementations can drop in without breaking downstream consumers (the
station's per-build retimer telemetry sweep).
"""
from __future__ import annotations

import pytest

from computetest.pcie.retimer import (
    AriesRetimer,
    BISTResult,
    EqLevels,
    EyeMeasurement,
    LoopbackSide,
    MockRetimer,
    PRBSPattern,
    Retimer,
    RetimerError,
    RetimerInfo,
    eye_quality_verdict,
)


# ----------------------------------------------------------------------------
# Public-surface verifications
# ----------------------------------------------------------------------------
class TestContract:
    def test_retimer_is_abstract(self):
        # Cannot instantiate Retimer directly.
        with pytest.raises(TypeError):
            Retimer()                                       # type: ignore[abstract]

    def test_loopback_side_enum_values(self):
        assert LoopbackSide.HOST.value == "host"
        assert LoopbackSide.LINE.value == "line"

    def test_prbs_pattern_enum_includes_prbs31(self):
        assert PRBSPattern.PRBS31.value == "PRBS31"

    def test_eye_quality_verdict_thresholds(self):
        good = EyeMeasurement(lane=0, eye_ui=0.30, eye_mv=80.0)
        bad_ui = EyeMeasurement(lane=0, eye_ui=0.20, eye_mv=80.0)
        bad_mv = EyeMeasurement(lane=0, eye_ui=0.30, eye_mv=20.0)
        assert eye_quality_verdict(good)
        assert not eye_quality_verdict(bad_ui)
        assert not eye_quality_verdict(bad_mv)


# ----------------------------------------------------------------------------
# MockRetimer
# ----------------------------------------------------------------------------
class TestMockRetimerIdentity:
    def test_info_default_shape(self):
        rt = MockRetimer()
        info = rt.info()
        assert isinstance(info, RetimerInfo)
        assert info.vendor == "MockCorp"
        assert info.lanes == 16

    def test_info_round_trip_to_dict(self):
        rt = MockRetimer(vendor="V", part_number="X", serial="S", firmware="F",
                          lanes=8, pcie_gen=6)
        d = rt.info().to_dict()
        assert d["vendor"] == "V" and d["pcie_gen"] == 6 and d["lanes"] == 8


class TestMockRetimerEye:
    def test_read_eye_per_lane_within_bounds(self):
        rt = MockRetimer(injected_eye_ui=0.32, injected_eye_mv=75.0)
        for lane in range(rt.info().lanes):
            m = rt.read_eye(lane)
            assert isinstance(m, EyeMeasurement)
            assert m.lane == lane
            assert m.eye_ui >= 0.0
            assert m.eye_mv >= 0.0

    def test_read_eye_rejects_out_of_range(self):
        rt = MockRetimer()
        with pytest.raises(RetimerError, match="out of range"):
            rt.read_eye(99)
        with pytest.raises(RetimerError, match="out of range"):
            rt.read_eye(-1)

    def test_per_lane_jitter_is_deterministic(self):
        rt1 = MockRetimer(serial="SN-A")
        rt2 = MockRetimer(serial="SN-A")
        for lane in range(4):
            assert rt1.read_eye(lane).eye_ui == rt2.read_eye(lane).eye_ui

    def test_different_serials_jitter_differently(self):
        rt_a = MockRetimer(serial="SN-A")
        rt_b = MockRetimer(serial="SN-B")
        # Across 16 lanes we expect at least one diverge.
        diffs = sum(
            rt_a.read_eye(lane).eye_ui != rt_b.read_eye(lane).eye_ui
            for lane in range(16))
        assert diffs > 0


class TestMockRetimerEq:
    def test_eq_has_dfe_taps(self):
        rt = MockRetimer()
        eq = rt.read_eq(0)
        assert isinstance(eq, EqLevels)
        assert eq.lane == 0
        assert len(eq.dfe_taps) == 4                        # typical 4-tap DFE


class TestMockRetimerTemperature:
    def test_temperature_returns_injected_value(self):
        rt = MockRetimer(injected_temperature_c=72.5)
        assert rt.read_temperature() == 72.5

    def test_temperature_ok_threshold(self):
        rt = MockRetimer(injected_temperature_c=90.0)
        assert rt.temperature_ok()                           # default Tj_max 105
        rt.set_tj_max(80.0)
        assert not rt.temperature_ok()


class TestMockRetimerLoopback:
    def test_set_loopback_round_trip(self):
        rt = MockRetimer()
        assert not rt.loopback_state(LoopbackSide.HOST)
        rt.set_loopback(LoopbackSide.HOST, True)
        assert rt.loopback_state(LoopbackSide.HOST)
        assert not rt.loopback_state(LoopbackSide.LINE)

    def test_loopback_side_independent(self):
        rt = MockRetimer()
        rt.set_loopback(LoopbackSide.HOST, True)
        rt.set_loopback(LoopbackSide.LINE, False)
        assert rt.loopback_state(LoopbackSide.HOST)
        assert not rt.loopback_state(LoopbackSide.LINE)


class TestMockRetimerBist:
    def test_bist_returns_per_lane_errors(self):
        rt = MockRetimer(injected_bist_errors=3)
        result = rt.run_prbs_bist(pattern=PRBSPattern.PRBS31, duration_s=0.5)
        assert isinstance(result, BISTResult)
        assert result.pattern == PRBSPattern.PRBS31
        assert result.duration_s == 0.5
        assert all(v == 3 for v in result.per_lane_errors.values())
        assert result.total_errors == 3 * rt.info().lanes
        assert result.locked is True

    def test_bist_zero_duration_rejected(self):
        rt = MockRetimer()
        with pytest.raises(RetimerError, match="duration must be > 0"):
            rt.run_prbs_bist(duration_s=0.0)

    def test_bist_high_errors_marks_unlocked(self):
        rt = MockRetimer(injected_bist_errors=2_000_000)
        result = rt.run_prbs_bist()
        assert result.locked is False


class TestMockRetimerLaneStatus:
    def test_lane_status_pass_with_good_eye(self):
        rt = MockRetimer(injected_eye_ui=0.40, injected_eye_mv=100.0)
        s = rt.lane_status(0)
        assert s.eye_pass
        assert s.note == ""

    def test_lane_status_fail_with_bad_eye(self):
        rt = MockRetimer(injected_eye_ui=0.10, injected_eye_mv=10.0)
        s = rt.lane_status(0)
        assert not s.eye_pass
        assert "below" in s.note


class TestEyeThresholds:
    def test_set_eye_thresholds_overrides_defaults(self):
        rt = MockRetimer(injected_eye_ui=0.20, injected_eye_mv=50.0)
        # Default thresholds (0.25 UI / 30 mV) -> eye_ui fails.
        assert not rt.lane_status(0).eye_pass
        rt.set_eye_thresholds(ui_min=0.10)
        assert rt.lane_status(0).eye_pass


# ----------------------------------------------------------------------------
# AriesRetimer (stub: every call raises NotImplementedError)
# ----------------------------------------------------------------------------
class TestAriesStub:
    def test_unknown_part_rejected(self):
        with pytest.raises(RetimerError, match="unknown Aries part"):
            AriesRetimer("UNKNOWN-PART")

    def test_info_for_known_pt5161l(self):
        a = AriesRetimer("PT5161L")
        info = a.info()
        assert info.vendor == "Astera Labs"
        assert info.part_number == "PT5161L"
        assert info.lanes == 16
        assert info.pcie_gen == 5

    def test_info_for_known_pt6161l_gen6(self):
        a = AriesRetimer("PT6161L")
        assert a.info().pcie_gen == 6

    def test_read_eye_raises_until_sdk_wired(self):
        a = AriesRetimer("PT5161L")
        with pytest.raises(NotImplementedError):
            a.read_eye(0)

    def test_lane_check_runs_before_sdk(self):
        # Out-of-range lane should raise RetimerError BEFORE the SDK NotImplementedError.
        a = AriesRetimer("PT5161L")
        with pytest.raises(RetimerError, match="out of range"):
            a.read_eye(99)

    def test_read_temperature_raises_until_sdk_wired(self):
        a = AriesRetimer("PT5161L")
        with pytest.raises(NotImplementedError):
            a.read_temperature()

    def test_set_loopback_raises_until_sdk_wired(self):
        a = AriesRetimer("PT5161L")
        with pytest.raises(NotImplementedError):
            a.set_loopback(LoopbackSide.HOST, True)

    def test_run_prbs_bist_raises_until_sdk_wired(self):
        a = AriesRetimer("PT5161L")
        with pytest.raises(NotImplementedError):
            a.run_prbs_bist()


# ----------------------------------------------------------------------------
# Adversarial: dict round-trip / serialization
# ----------------------------------------------------------------------------
class TestSerialization:
    def test_eye_to_dict_round_trip(self):
        m = EyeMeasurement(lane=3, eye_ui=0.30, eye_mv=80.0,
                            height_mv=80.0, width_ui=0.30)
        d = m.to_dict()
        assert d["lane"] == 3 and d["eye_ui"] == 0.30 and d["eye_mv"] == 80.0

    def test_eq_to_dict_round_trip(self):
        e = EqLevels(lane=1, ctle_gain_db=10.0, dfe_taps=[0.4, -0.2],
                      agc_level=0.5)
        d = e.to_dict()
        assert d["dfe_taps"] == [0.4, -0.2]

    def test_bist_to_dict_includes_total(self):
        r = BISTResult(pattern=PRBSPattern.PRBS31, duration_s=1.0,
                       per_lane_errors={0: 1, 1: 2, 2: 3}, locked=True)
        d = r.to_dict()
        assert d["total_errors"] == 6
