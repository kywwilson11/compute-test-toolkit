"""
GMSL V/T margining shmoo (Sprint 4.1).

Two-axis voltage x temperature margining: a ``PowerSupply`` sets the rail at each
voltage corner, a ``ThermalChamber`` sets each temperature corner (AEC-Q100
Grade-2 -40..+105 C), and at every corner the link is re-trained (AEQ
re-convergence) and the EOM eye is read. Captures the on-die junction
temperature at each corner and verdicts whether the eye stays open across the
whole shmoo. The per-corner eye margin can be projected onto the pci_lmt schema
via ``gmsl.eom_to_lmt_records`` (Sprint 4.1.7).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..instruments import PowerSupply, ThermalChamber
from .serdes import LinkDirection, SerDesLink


@dataclass
class ShmooPoint:
    """One V/T corner: programmed rail + chamber temperature, the on-die temp
    readback, the worst eye opening, and whether the link re-locked there."""
    voltage_v: float
    temperature_c: float
    die_temp_c: float
    worst_eye_mv: float
    eye_pass: bool
    relocked: bool

    def to_dict(self) -> dict:
        return {"voltage_v": self.voltage_v, "temperature_c": self.temperature_c,
                "die_temp_c": self.die_temp_c, "worst_eye_mv": self.worst_eye_mv,
                "eye_pass": self.eye_pass, "relocked": self.relocked}


@dataclass
class ShmooResult:
    """A full V/T shmoo for one link."""
    link: int
    points: list[ShmooPoint]
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        passed = sum(1 for p in self.points if p.eye_pass)
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"GMSL V/T shmoo link{self.link}: {passed}/{len(self.points)} "
                f"corners eye-open -> {state}")

    def to_dict(self) -> dict:
        return {"link": self.link, "points": [p.to_dict() for p in self.points],
                "checks": self.checks, "ok": self.ok}


def vt_shmoo(serdes: SerDesLink, *, psu: PowerSupply, chamber: ThermalChamber,
             voltages: Sequence[float], temperatures: Sequence[float],
             link: int = 0,
             direction: LinkDirection = LinkDirection.FORWARD) -> ShmooResult:
    """Run a two-axis voltage x temperature margining shmoo on one link.

    At each (temperature, voltage) corner: set the chamber, set the rail, force a
    re-train (AEQ re-convergence), then read the on-die temperature and the EOM
    eye. A corner where the link doesn't re-lock fails ``aeq_reconverged``; a
    corner whose eye closes fails ``all_corners_eye_open``."""
    points: list[ShmooPoint] = []
    for temp in temperatures:
        chamber.set_temperature(temp)
        for volt in voltages:
            psu.set_voltage(volt)
            serdes.force_relock(link)            # AEQ re-converge after the V/T step
            relocked = serdes.lock_status()[link].locked
            die = serdes.read_temperature()
            if relocked:
                eom = serdes.read_eom(link, direction)
                worst, eye_ok = eom.worst_vertical_mv, serdes.eom_verdict(eom)
            else:
                worst, eye_ok = 0.0, False
            points.append(ShmooPoint(voltage_v=volt, temperature_c=temp,
                                     die_temp_c=die, worst_eye_mv=worst,
                                     eye_pass=eye_ok, relocked=relocked))
    checks = {
        "all_corners_eye_open": bool(points) and all(p.eye_pass for p in points),
        "aeq_reconverged": bool(points) and all(p.relocked for p in points),
    }
    return ShmooResult(link=link, points=points, checks=checks)
