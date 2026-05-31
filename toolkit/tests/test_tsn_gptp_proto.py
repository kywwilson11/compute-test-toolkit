"""Sprint 4.2.4: 802.1AS gPTP protocol conformance."""
from __future__ import annotations

import pytest

from computetest.tsn import (
    AnnounceMsg,
    GptpProtocolHealth,
    PdelayExchange,
    as_capable,
    bmca_elect,
    check_gptp_protocol,
    correction_field_ok,
    rate_ratio_valid,
)


def _announce(source, p1, cc=248, p2=128, cid=1):
    return AnnounceMsg(source=source, priority1=p1, clock_class=cc,
                       clock_accuracy=0x21, priority2=p2, clock_identity=cid)


class TestPdelay:
    def test_mean_link_delay_and_turnaround(self):
        # ((1100-0) - 1*(600-500)) / 2 = 500
        pd = PdelayExchange(t1=0, t2=500, t3=600, t4=1100)
        assert pd.mean_link_delay_ns == 500.0
        assert pd.turnaround_ns == 100.0

    def test_rate_ratio_scales_responder_turnaround(self):
        # 802.1AS / linuxptp: neighborRateRatio scales (t3 - t2), NOT (t4 - t1):
        # ((3000-0) - 1.0002*(2000-1000)) / 2 = (3000 - 1000.2)/2 = 999.9 ns.
        # The (rr*(t4-t1) - (t3-t2))/2 arrangement would give 1000.3 -- guard against it.
        pd = PdelayExchange(t1=0.0, t2=1000.0, t3=2000.0, t4=3000.0, rate_ratio=1.0002)
        assert pd.mean_link_delay_ns == pytest.approx(999.9, abs=1e-9)


class TestBmca:
    def test_lowest_priority1_wins(self):
        assert bmca_elect([_announce("GM-A", p1=128),
                           _announce("GM-B", p1=100)]).source == "GM-B"

    def test_empty_is_none(self):
        assert bmca_elect([]) is None

    def test_failover_reelects_after_gm_loss(self):
        a, b = _announce("GM-A", p1=100), _announce("GM-B", p1=128)
        assert bmca_elect([a, b]).source == "GM-A"
        assert bmca_elect([b]).source == "GM-B"           # GM-A lost -> GM-B


class TestCorrectionAndRateRatio:
    def test_correction_field_identity(self):
        assert correction_field_ok(150.0, upstream_delay_ns=100.0, residence_ns=50.0)
        assert not correction_field_ok(200.0, upstream_delay_ns=100.0, residence_ns=50.0)

    def test_rate_ratio_tolerance(self):
        assert rate_ratio_valid(1.0 + 100e-6)             # 100 ppm < 200
        assert not rate_ratio_valid(1.0 + 500e-6)         # 500 ppm > 200


class TestAsCapable:
    def test_requires_all_three(self):
        assert as_capable(True, True, True)
        assert not as_capable(True, True, False)          # absent Follow_Up clears it
        assert not as_capable(False, True, True)
        assert not as_capable(True, False, True)


class TestCheckAggregator:
    def _good(self, **over):
        kw = dict(pdelay=PdelayExchange(0, 500, 600, 1100),
                  announces=[_announce("GM-A", p1=100)],
                  correction_ns=150.0, upstream_delay_ns=100.0, residence_ns=50.0,
                  follow_up_present=True)
        kw.update(over)
        return check_gptp_protocol(**kw)

    def test_conformant_link_passes(self):
        h = self._good()
        assert isinstance(h, GptpProtocolHealth)
        assert h.ok and h.elected_gm == "GM-A"
        assert h.mean_link_delay_ns == 500.0

    def test_absent_follow_up_fails_as_capable(self):
        h = self._good(follow_up_present=False)
        assert not h.ok
        assert h.checks["as_capable"] is False
        assert h.checks["follow_up_present"] is False

    def test_no_master_fails(self):
        h = self._good(announces=[])
        assert not h.ok and h.checks["gm_elected"] is False

    def test_summary_and_to_dict(self):
        h = self._good()
        assert "gPTP proto" in h.summary()
        assert h.to_dict()["elected_gm"] == "GM-A"
