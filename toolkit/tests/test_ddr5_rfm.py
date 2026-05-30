"""Sprint 4.3.8: DDR5 RFM/PRAC enablement + alert-path (enablement only)."""
from __future__ import annotations

from computetest.ddr5 import RfmPracHealth, check_rfm_prac


class TestRfmPrac:
    def test_supported_and_enabled_passes(self):
        h = check_rfm_prac(capability_supported=True, rfm_enabled=True,
                           prac_enabled=True, alert_path_wired=True)
        assert isinstance(h, RfmPracHealth) and h.ok

    def test_supported_but_disabled_fails(self):
        h = check_rfm_prac(capability_supported=True, rfm_enabled=False,
                           prac_enabled=True, alert_path_wired=True)
        assert not h.ok and h.checks["rfm_enabled"] is False

    def test_unsupported_does_not_require_enable(self):
        # No RFM/PRAC capability -> enablement is not required, but the alert
        # path is still checked.
        h = check_rfm_prac(capability_supported=False, rfm_enabled=False,
                           prac_enabled=False, alert_path_wired=True)
        assert h.ok
        assert h.checks["rfm_enabled"] is True and h.checks["prac_enabled"] is True

    def test_alert_path_not_wired_fails(self):
        h = check_rfm_prac(capability_supported=False, rfm_enabled=False,
                           prac_enabled=False, alert_path_wired=False)
        assert not h.ok and h.checks["alert_path_wired"] is False

    def test_summary_and_to_dict(self):
        h = check_rfm_prac(capability_supported=True, rfm_enabled=True,
                           prac_enabled=True, alert_path_wired=True)
        assert "RFM/PRAC" in h.summary() and "supported" in h.summary()
        assert h.to_dict()["capability_supported"] is True
