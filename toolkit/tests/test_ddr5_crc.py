"""Sprint 4.3.7: DDR5 link CRC + C/A parity (ALERT_n) plumbing."""
from __future__ import annotations

from computetest.ddr5 import CrcHealth, check_crc_parity


def _good(**over):
    kw = dict(write_crc_enabled=True, read_crc_enabled=True, ca_parity_enabled=True,
              alert_n_wired=True, crc_retry_count=0)
    kw.update(over)
    return check_crc_parity(**kw)


class TestCrcParity:
    def test_full_plumbing_passes(self):
        h = _good()
        assert isinstance(h, CrcHealth) and h.ok

    def test_disabled_crc_fails(self):
        assert not _good(write_crc_enabled=False).ok

    def test_alert_n_not_wired_fails(self):
        h = _good(alert_n_wired=False)
        assert not h.ok and h.checks["alert_n_wired"] is False

    def test_retries_over_budget_fails(self):
        h = _good(crc_retry_count=5)
        assert not h.ok and h.checks["crc_retries_within_budget"] is False

    def test_negative_retry_count_fails_closed(self):
        # A negative count is a garbage/sentinel read from the unsigned counter;
        # it must FAIL the budget, not pass via (-1 <= max_retries).
        h = _good(crc_retry_count=-1)
        assert not h.ok and h.checks["crc_retries_within_budget"] is False

    def test_within_nondefault_budget_passes(self):
        # Pin the verdict (not just the echoed count) for a retry_count>0 that is
        # within a non-default budget, so a budget-comparison regression is caught.
        h = _good(crc_retry_count=2, max_retries=10)
        assert h.ok and h.checks["crc_retries_within_budget"] is True

    def test_summary_and_to_dict(self):
        assert "DDR5 CRC/parity" in _good().summary()
        assert _good(crc_retry_count=2, max_retries=10).to_dict()["crc_retry_count"] == 2
