"""Sprint 4.1.10: GMSL V/T margining shmoo (PowerSupply x ThermalChamber)."""
from __future__ import annotations

import pytest

from computetest.gmsl import MockSerDes, ShmooPoint, ShmooResult, vt_shmoo
from computetest.gmsl.serdes import GmslMode as _GmslMode
from computetest.gmsl.serdes import LinkLock, SerDesError
from computetest.instruments import PowerSupply, ThermalChamber


def _bench():
    return PowerSupply(mock=True).open(), ThermalChamber(mock=True).open()


class TestVtShmoo:
    def test_healthy_link_passes_all_corners(self):
        psu, chamber = _bench()
        r = vt_shmoo(MockSerDes(injected_eye_mv=80.0, injected_temperature_c=70.0),
                     psu=psu, chamber=chamber,
                     voltages=[0.95, 1.0, 1.05], temperatures=[-40, 25, 105])
        assert isinstance(r, ShmooResult)
        assert len(r.points) == 9                       # 3 V x 3 T
        assert all(isinstance(p, ShmooPoint) for p in r.points)
        assert r.ok
        assert r.points[0].die_temp_c == 70.0           # on-die readback
        # the bench was actually driven across both axes
        assert "SOUR:TEMP -40" in chamber.transport.writes
        assert "VOLT 1.05" in psu.transport.writes

    def test_weak_eye_fails_all_corners(self):
        psu, chamber = _bench()
        r = vt_shmoo(MockSerDes(injected_eye_mv=10.0), psu=psu, chamber=chamber,
                     voltages=[1.0], temperatures=[25])
        assert not r.ok
        assert r.checks["all_corners_eye_open"] is False
        assert r.points[0].eye_pass is False

    def test_unlocked_fails_aeq_reconverge(self):
        psu, chamber = _bench()
        r = vt_shmoo(MockSerDes(injected_locked=False), psu=psu, chamber=chamber,
                     voltages=[1.0], temperatures=[25])
        assert not r.ok
        assert r.checks["aeq_reconverged"] is False
        assert r.points[0].relocked is False
        assert r.points[0].worst_eye_mv == 0.0          # no eye read when unlocked

    def test_summary_and_to_dict(self):
        psu, chamber = _bench()
        r = vt_shmoo(MockSerDes(), psu=psu, chamber=chamber,
                     voltages=[1.0], temperatures=[25])
        assert "V/T shmoo" in r.summary()
        d = r.to_dict()
        assert len(d["points"]) == 1 and "checks" in d

    def test_lock_looked_up_by_link_not_position(self):
        # A backend may return lock_status() in any order. vt_shmoo must read the
        # requested link by its .link field, not by list position. Build a deser
        # whose lock list is REVERSED so position 0 holds link 1 (unlocked) and
        # the requested link 0 is actually locked.
        psu, chamber = _bench()

        class _ReorderedSerDes(MockSerDes):
            def lock_status(self):
                return [
                    LinkLock(link=1, locked=False, mode=_GmslMode.UNKNOWN),
                    LinkLock(link=0, locked=True, mode=_GmslMode.PAM4_12G,
                             lock_time_ms=8.0),
                ]

        r = vt_shmoo(_ReorderedSerDes(injected_eye_mv=80.0), psu=psu,
                     chamber=chamber, voltages=[1.0], temperatures=[25], link=0)
        # Positional indexing would have read link 1 (unlocked) and failed AEQ.
        assert r.points[0].relocked is True
        assert r.checks["aeq_reconverged"] is True

    def test_missing_link_raises(self):
        psu, chamber = _bench()

        class _NoLinkSerDes(MockSerDes):
            def lock_status(self):
                return [LinkLock(link=1, locked=True, mode=_GmslMode.PAM4_12G)]

        with pytest.raises(SerDesError):
            vt_shmoo(_NoLinkSerDes(), psu=psu, chamber=chamber,
                     voltages=[1.0], temperatures=[25], link=0)

    def test_chamber_settled_polled_at_each_corner(self):
        # The chamber must be confirmed settled before the eye is read, so eye
        # margin is attributed to the corner the chamber actually reached. The
        # mock reflects the setpoint instantly, so settled() is True at once and
        # the run completes without delay; assert the actual temp matches the
        # setpoint at read time.
        psu, chamber = _bench()
        r = vt_shmoo(MockSerDes(injected_eye_mv=80.0, injected_temperature_c=70.0),
                     psu=psu, chamber=chamber, voltages=[1.0],
                     temperatures=[-40, 105])
        assert r.ok
        assert chamber.settled(tolerance_c=2.0) is True
        assert chamber.setpoint().value == 105.0

    def test_unsettled_chamber_raises(self):
        # A chamber whose actual temperature never reaches the setpoint must
        # raise (fail-closed) rather than read the eye at the wrong corner.
        psu, chamber = _bench()
        # Force MEAS:TEMP? to a fixed far-off actual so settled() is always False.
        chamber.transport.set_measurement("MEAS:TEMP?", "25.0")
        with pytest.raises(SerDesError):
            vt_shmoo(MockSerDes(), psu=psu, chamber=chamber,
                     voltages=[1.0], temperatures=[105],
                     settle_timeout_s=0.0)
