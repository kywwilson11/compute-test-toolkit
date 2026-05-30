"""Sprint 4.2.8: 802.1ASdm hot-standby / redundant-domain gPTP failover."""
from __future__ import annotations

from computetest.tsn import AnnounceMsg, HotStandbyHealth, check_hot_standby_failover


def _gm(source, p1=100):
    return AnnounceMsg(source=source, priority1=p1, clock_class=248,
                       clock_accuracy=0x21, priority2=128, clock_identity=2)


class TestHotStandbyFailover:
    def test_clean_failover_passes(self):
        h = check_hot_standby_failover(
            standby_announces=[_gm("GM-standby")],
            te_samples_ns=[100.0, -200.0, 300.0], as_capable_held=True)
        assert isinstance(h, HotStandbyHealth) and h.ok
        assert h.surviving_domain == "GM-standby"
        assert h.failover_te_transient_ns == 300.0

    def test_no_standby_gm_fails(self):
        h = check_hot_standby_failover(standby_announces=[], te_samples_ns=[10.0],
                                       as_capable_held=True)
        assert not h.ok and h.checks["standby_gm_available"] is False

    def test_te_transient_exceeds_holdover_fails(self):
        h = check_hot_standby_failover(standby_announces=[_gm("GM-s")],
                                       te_samples_ns=[1500.0], as_capable_held=True)
        assert not h.ok and h.checks["te_within_holdover"] is False

    def test_as_capable_drop_fails(self):
        h = check_hot_standby_failover(standby_announces=[_gm("GM-s")],
                                       te_samples_ns=[50.0], as_capable_held=False)
        assert not h.ok and h.checks["as_capable_held"] is False

    def test_summary_and_to_dict(self):
        h = check_hot_standby_failover(standby_announces=[_gm("GM-s")],
                                       te_samples_ns=[50.0], as_capable_held=True)
        assert "gPTP failover" in h.summary()
        assert h.to_dict()["surviving_domain"] == "GM-s"
