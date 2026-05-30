"""Sprint 4.4.3: CXL link bring-up / alternate-protocol negotiation."""
from __future__ import annotations

from computetest.cxl import CxlLinkHealth, FlitMode, check_cxl_link


def _good(**over):
    kw = dict(speed_gt=32, width=16, flit_mode=FlitMode.CXL_256B_STD,
              cxl_negotiated=True)
    kw.update(over)
    return check_cxl_link(**kw)


class TestCxlLink:
    def test_full_negotiation_passes(self):
        h = _good()
        assert isinstance(h, CxlLinkHealth) and h.ok

    def test_silent_pcie_fallback_fails(self):
        h = _good(cxl_negotiated=False)
        assert not h.ok and h.checks["cxl_alt_protocol_negotiated"] is False

    def test_silent_degrade_to_68b_fails(self):
        h = _good(flit_mode=FlitMode.PCIE_68B)
        assert not h.ok and h.checks["flit_mode_ok"] is False

    def test_low_speed_or_width_fails(self):
        assert not _good(speed_gt=16).ok
        assert not _good(width=8).ok

    def test_64gt_latency_optimized_ok(self):
        h = _good(speed_gt=64, flit_mode=FlitMode.CXL_256B_LATENCY_OPT,
                  expect_flit=FlitMode.CXL_256B_LATENCY_OPT)
        assert h.ok

    def test_summary_and_to_dict(self):
        assert "CXL link" in _good().summary()
        assert _good(cxl_negotiated=False).summary().count("PCIe-fallback") == 1
        assert _good().to_dict()["flit_mode"] == "256B-standard"
