"""
Sprint 4.1.3: GMSL PRBS pre-FEC BER check.

Reuses computetest.ber (Poisson/chi-squared) to turn a PRBS window's
(errors, bits) into a confidence-bounded BER verdict — forward (12 Gbps PAM4)
and reverse (187.5 Mbps NRZ).
"""
from __future__ import annotations

from computetest.gmsl import (
    GmslPrbsPattern,
    LinkDirection,
    MockSerDes,
    PrbsBerHealth,
    check_prbs_ber,
)


class TestPrbsBer:
    def test_clean_forward_link_proves_ber(self):
        h = check_prbs_ber(MockSerDes(injected_prbs_errors=0), duration_s=1.0)
        assert isinstance(h, PrbsBerHealth)
        assert h.ok and h.status == "pass"
        assert h.checks == {"prbs_locked": True, "ber_proven": True}
        assert h.ber_upper <= h.target_ber          # proved below the 1e-7 floor

    def test_clean_reverse_link_proves_ber(self):
        h = check_prbs_ber(MockSerDes(injected_prbs_errors=0),
                           direction=LinkDirection.REVERSE, duration_s=1.0)
        assert h.ok and h.direction == "reverse"

    def test_errored_link_not_proven(self):
        # 5e5 errors in ~1.2e10 bits -> BER ~4e-5, far above the 1e-7 target,
        # but still "locked" (under the 1e6 lock threshold).
        h = check_prbs_ber(MockSerDes(injected_prbs_errors=500_000), duration_s=1.0)
        assert not h.ok
        assert h.checks["prbs_locked"] is True
        assert h.checks["ber_proven"] is False
        assert h.ber_upper > h.target_ber

    def test_unlocked_prbs_fails(self):
        h = check_prbs_ber(MockSerDes(injected_prbs_errors=2_000_000), duration_s=1.0)
        assert h.checks["prbs_locked"] is False
        assert not h.ok

    def test_video_kind_and_pattern_label(self):
        h = check_prbs_ber(MockSerDes(), kind="video", pattern=GmslPrbsPattern.PRBS24)
        assert h.kind == "video" and h.pattern == "PRBS24"

    def test_summary_and_to_dict(self):
        h = check_prbs_ber(MockSerDes(injected_prbs_errors=0))
        assert "GMSL PRBS" in h.summary() and "OK" in h.summary()
        d = h.to_dict()
        assert d["kind"] == "link" and d["status"] == "pass" and "ber_upper" in d
