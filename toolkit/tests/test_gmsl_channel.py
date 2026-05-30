"""
Sprint 4.1.6: GMSL3 S-parameter channel-compliance framework.

The numeric AN-2585 masks are intentionally NOT shipped; these tests exercise
the filter + mask + comparison framework with synthetic masks, and pin that a
run refuses to pass with no mask supplied.
"""
from __future__ import annotations

import pytest

from computetest.gmsl.channel import (
    AN2585_IL_MASK_SHORT,
    ChannelComplianceResult,
    MaskKind,
    SParamMask,
    apply_gmsl3_filter,
    check_against_mask,
    check_channel_compliance,
)
from computetest.instruments import SParamSweep, Vna


class TestMask:
    def test_limit_interpolates_linearly(self):
        m = SParamMask("m", MaskKind.INSERTION_LOSS, [(1e6, -1.0), (3e6, -3.0)])
        assert m.limit_at(2e6) == -2.0          # midpoint
        assert m.limit_at(0.0) == -1.0          # clamp low
        assert m.limit_at(9e9) == -3.0          # clamp high

    def test_limit_interpolates_middle_interval(self):
        # 3 breakpoints: a freq in the SECOND interval exercises the loop's
        # "not this interval, keep scanning" path.
        m = SParamMask("m", MaskKind.INSERTION_LOSS,
                       [(1e6, -1.0), (2e6, -2.0), (4e6, -4.0)])
        assert m.limit_at(3e6) == -3.0

    def test_empty_mask_raises(self):
        with pytest.raises(ValueError, match="no breakpoints"):
            SParamMask("m", MaskKind.RETURN_LOSS, []).limit_at(1e6)


class TestComparison:
    def test_insertion_loss_violation_below_limit(self):
        # IL: measured magnitude must be >= limit (less attenuation).
        sweep = SParamSweep("SDD21", [1e6, 2e6], [-0.5, -5.0])
        mask = SParamMask("il", MaskKind.INSERTION_LOSS, [(1e6, -1.0), (2e6, -1.0)])
        res = check_against_mask(sweep, mask)
        assert isinstance(res, ChannelComplianceResult)
        assert not res.ok                        # -5.0 < -1.0 limit -> violation
        assert len(res.violations) == 1
        assert res.violations[0][0] == 2e6

    def test_return_loss_violation_above_limit(self):
        # RL: measured magnitude must be <= limit (more negative = better).
        sweep = SParamSweep("SDD11", [1e6, 2e6], [-20.0, -5.0])
        mask = SParamMask("rl", MaskKind.RETURN_LOSS, [(1e6, -10.0), (2e6, -10.0)])
        res = check_against_mask(sweep, mask)
        assert not res.ok                        # -5.0 > -10.0 limit -> violation
        assert len(res.violations) == 1

    def test_clean_sweep_passes(self):
        sweep = SParamSweep("SDD21", [1e6, 2e6], [-0.2, -0.3])
        mask = SParamMask("il", MaskKind.INSERTION_LOSS, [(1e6, -1.0), (2e6, -1.0)])
        assert check_against_mask(sweep, mask).ok


class TestFilterAndCompliance:
    def test_filter_partitions_at_50mhz(self):
        sweep = SParamSweep("SDD21", [1e6, 49e6, 50e6, 100e6], [-1, -1, -1, -1])
        below, above = apply_gmsl3_filter(sweep)
        assert [f for f, _ in below] == [1e6, 49e6]
        assert [f for f, _ in above] == [50e6, 100e6]

    def test_compliance_refuses_without_masks(self):
        v = Vna(mock=True).open()
        with pytest.raises(ValueError, match="masks must be supplied"):
            check_channel_compliance(v)  # type: ignore[arg-type]

    def test_compliance_with_supplied_masks(self):
        v = Vna(mock=True).open()
        # One primed trace serves both param reads; pick values that clear both
        # an IL floor (>= -20) and an RL ceiling (<= -10).
        v.transport.set_measurement("SENS:FREQ:DATA?", "1e6,2e6")
        v.transport.set_measurement("CALC:DATA? FDATA", "-15.0,-16.0")
        il = SParamMask("il", MaskKind.INSERTION_LOSS, [(1e6, -20.0), (2e6, -20.0)])
        rl = SParamMask("rl", MaskKind.RETURN_LOSS, [(1e6, -10.0), (2e6, -10.0)])
        il_res, rl_res = check_channel_compliance(v, il_mask=il, rl_mask=rl)
        assert il_res.ok and rl_res.ok

    def test_an2585_masks_are_unset_placeholders(self):
        # Documented gap: the real AN-2585 numbers are not shipped.
        assert AN2585_IL_MASK_SHORT is None

    def test_result_summary_and_to_dict(self):
        sweep = SParamSweep("SDD21", [1e6], [-5.0])
        mask = SParamMask("il", MaskKind.INSERTION_LOSS, [(1e6, -1.0)])
        res = check_against_mask(sweep, mask)
        assert "GMSL3 channel" in res.summary() and "FAIL" in res.summary()
        assert res.to_dict()["n_violations"] == 1
