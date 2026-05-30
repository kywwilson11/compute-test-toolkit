"""Sprint 4.1.10: GMSL V/T margining shmoo (PowerSupply x ThermalChamber)."""
from __future__ import annotations

from computetest.gmsl import MockSerDes, ShmooPoint, ShmooResult, vt_shmoo
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
