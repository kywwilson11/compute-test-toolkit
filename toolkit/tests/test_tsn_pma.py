"""Sprint 4.2.9: PHY PMA electrical conformance (TC8 L1 / Cl.96-97)."""
from __future__ import annotations

import pytest

from computetest.gmsl.channel import MaskKind, SParamMask
from computetest.instruments import Vna
from computetest.tsn import MockPhy, PmaHealth, check_pma_electrical


def _vna_with(trace: str) -> Vna:
    v = Vna(mock=True).open()
    v.transport.set_measurement("SENS:FREQ:DATA?", "1e6,2e6")
    v.transport.set_measurement("CALC:DATA? FDATA", trace)
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
