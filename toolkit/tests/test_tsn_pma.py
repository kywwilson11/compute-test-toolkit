"""Sprint 4.2.9: PHY PMA electrical conformance (TC8 L1 / Cl.96-97)."""
from __future__ import annotations

import pytest

from computetest.gmsl.channel import MaskKind, SParamMask
from computetest.instruments import SParamSweep, Vna
from computetest.tsn import (
    DEFAULT_PMA_MAX_LINKUP_MS,
    DEFAULT_PMA_SQI_MIN,
    MockPhy,
    PmaHealth,
    check_pma_electrical,
)


def _vna_with(trace: str) -> Vna:
    v = Vna(mock=True).open()
    v.transport.set_measurement("SENS:FREQ:DATA?", "1e6,2e6")
    v.transport.set_measurement("CALC:DATA? FDATA", trace)
    return v


def _vna_per_param(traces: dict[str, list[float]]) -> Vna:
    """A VNA whose ``measure_sparam`` returns a DISTINCT trace per parameter.

    The MockSCPI transport keys canned responses only on the query string
    ("CALC:DATA? FDATA"), so a single ``set_measurement`` makes Sdd11 and Scd21
    return the identical trace and a return-loss/mode-conversion param-or-mask
    swap goes unnoticed. Selecting per-param traces here lets a test drive
    return loss (Sdd11) and mode conversion (Scd21) to opposite verdicts.
    """
    v = Vna(mock=True).open()

    def measure(param: str) -> SParamSweep:
        return SParamSweep(param=param.upper(), freqs_hz=[1e6, 2e6],
                           magnitudes_db=list(traces[param.upper()]))

    v.measure_sparam = measure  # type: ignore[method-assign]
    return v


def _rl_mask(limit=-10.0):
    return SParamMask("rl", MaskKind.RETURN_LOSS, [(1e6, limit), (2e6, limit)])


def _mc_mask(limit=-20.0):
    return SParamMask("mc", MaskKind.RETURN_LOSS, [(1e6, limit), (2e6, limit)])


class TestPma:
    def test_conformant(self):
        v = _vna_with("-30.0,-31.0")                 # good RL/MC (<= -10 / -20)
        h = check_pma_electrical(MockPhy(injected_sqi=7, injected_linkup_ms=20.0), v,
                                 mdi_rl_mask=_rl_mask(), mode_conv_mask=_mc_mask())
        assert isinstance(h, PmaHealth) and h.ok

    def test_masks_required(self):
        with pytest.raises(ValueError, match="masks must be supplied"):
            check_pma_electrical(MockPhy(), Vna(mock=True).open())

    def test_return_loss_violation_fails(self):
        v = _vna_with("-5.0,-5.0")                    # -5 > -10 limit -> RL violation
        h = check_pma_electrical(MockPhy(), v, mdi_rl_mask=_rl_mask(),
                                 mode_conv_mask=_mc_mask())
        assert not h.ok and h.checks["mdi_return_loss_ok"] is False

    def test_slow_linkup_fails(self):
        v = _vna_with("-30.0,-31.0")
        h = check_pma_electrical(MockPhy(injected_linkup_ms=500.0), v,
                                 mdi_rl_mask=_rl_mask(), mode_conv_mask=_mc_mask())
        assert not h.ok and h.checks["link_up_time_ok"] is False

    def test_low_sqi_fails(self):
        v = _vna_with("-30.0,-31.0")
        h = check_pma_electrical(MockPhy(injected_sqi=2), v,
                                 mdi_rl_mask=_rl_mask(), mode_conv_mask=_mc_mask())
        assert not h.ok and h.checks["sqi_ok"] is False

    def test_summary_and_to_dict(self):
        v = _vna_with("-30.0,-31.0")
        h = check_pma_electrical(MockPhy(), v, mdi_rl_mask=_rl_mask(),
                                 mode_conv_mask=_mc_mask())
        assert "PMA" in h.summary() and "checks" in h.to_dict()

    def test_mode_conversion_can_fail_while_return_loss_passes(self):
        # Distinct Sdd11 (return loss) vs Scd21 (mode conversion) traces so the two
        # mask checks are not conflated. The trace values are chosen so BOTH a
        # parameter swap (mode conversion reading Sdd11) AND a mask swap (mode
        # conversion checked against the return-loss mask) would flip mode
        # conversion to a false pass: Sdd11=-20 clears RL(-10) but its -20 == the
        # MC(-20) limit, and Scd21=-10 violates MC(-20) yet clears RL(-10). So the
        # only wiring that yields rl-pass/mc-fail is Sdd11->RL-mask, Scd21->MC-mask.
        v = _vna_per_param({"SDD11": [-20.0, -20.0], "SCD21": [-10.0, -10.0]})
        h = check_pma_electrical(MockPhy(), v, mdi_rl_mask=_rl_mask(),
                                 mode_conv_mask=_mc_mask())
        assert h.checks["mdi_return_loss_ok"] is True
        assert h.checks["mode_conversion_ok"] is False
        assert not h.ok

    def test_return_loss_can_fail_while_mode_conversion_passes(self):
        # The reverse asymmetry: Scd21=-20 just clears the mode-conversion limit
        # while Sdd11=0 (total reflection) violates the return-loss limit. A swap of
        # the return-loss parameter to Scd21 would falsely pass it, so this pins the
        # return-loss check to Sdd11. Together with the test above, each parameter is
        # bound to its own trace and mask and neither check can shadow the other.
        v = _vna_per_param({"SDD11": [0.0, 0.0], "SCD21": [-20.0, -20.0]})
        h = check_pma_electrical(MockPhy(), v, mdi_rl_mask=_rl_mask(),
                                 mode_conv_mask=_mc_mask())
        assert h.checks["mdi_return_loss_ok"] is False
        assert h.checks["mode_conversion_ok"] is True
        assert not h.ok

    def test_boundary_linkup_and_sqi_pass(self):
        # Exact-boundary values: link-up == the budget and SQI == the floor must
        # both PASS (inclusive <= / >=). Pins the comparison operators so a
        # <=/>= -> </> off-by-one mutation in check_pma_electrical is caught.
        v = _vna_with("-30.0,-31.0")
        h = check_pma_electrical(
            MockPhy(injected_sqi=DEFAULT_PMA_SQI_MIN,
                    injected_linkup_ms=DEFAULT_PMA_MAX_LINKUP_MS),
            v, mdi_rl_mask=_rl_mask(), mode_conv_mask=_mc_mask())
        assert h.checks["link_up_time_ok"] is True
        assert h.checks["sqi_ok"] is True
        assert h.ok
