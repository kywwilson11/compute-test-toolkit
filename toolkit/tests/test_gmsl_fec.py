"""
Sprint 4.1.4: GMSL Reed-Solomon FEC diagnostics.

Any uncorrectable block is an immediate fail; the corrected-symbol count is a
trend/soak signal that only fails when a budget is supplied.
"""
from __future__ import annotations

from computetest.gmsl import FecHealth, MockSerDes, check_fec


class TestFecCheck:
    def test_clean_fec_passes(self):
        h = check_fec(MockSerDes(injected_fec_corrected=5))
        assert isinstance(h, FecHealth)
        assert h.ok
        assert h.checks == {"no_uncorrectable": True}
        assert h.corrected_symbols == 5

    def test_uncorrectable_block_fails(self):
        h = check_fec(MockSerDes(injected_fec_uncorrectable=2))
        assert not h.ok
        assert h.checks["no_uncorrectable"] is False
        assert h.uncorrectable_blocks == 2

    def test_corrected_budget_enforced(self):
        s = MockSerDes(injected_fec_corrected=1000)
        assert not check_fec(s, max_corrected_symbols=100).ok      # over budget
        assert check_fec(s, max_corrected_symbols=5000).ok         # within budget

    def test_no_budget_only_checks_uncorrectable(self):
        h = check_fec(MockSerDes(injected_fec_corrected=10_000))
        assert "corrected_within_budget" not in h.checks
        assert h.ok                                                # corrections alone don't fail

    def test_summary_and_to_dict(self):
        h = check_fec(MockSerDes(injected_fec_uncorrectable=1))
        assert "GMSL FEC" in h.summary() and "FAIL(" in h.summary()
        d = h.to_dict()
        assert d["uncorrectable_blocks"] == 1 and d["ok"] is False
