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

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..instruments import PowerSupply, ThermalChamber
from .serdes import LinkDirection, SerDesError, SerDesLink

# Chamber settle budget at each temperature corner: a real chamber slews over
# minutes across the AEC-Q100 Grade-2 -40..+105 C range, so the eye/die-temp
# must not be read until the actual temperature is within tolerance of the
# setpoint. The mock reflects the setpoint instantly, so it settles on poll #1
# (zero sleeps). Real programs tune the timeout to their chamber's slew rate.
DEFAULT_SETTLE_TIMEOUT_S = 600.0
DEFAULT_SETTLE_POLL_S = 1.0
DEFAULT_SETTLE_TOLERANCE_C = 2.0


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


def _wait_settled(chamber: ThermalChamber, *, timeout_s: float,
                  tolerance_c: float) -> None:
    """Poll ``chamber.settled()`` until the actual temperature is within
    ``tolerance_c`` of the setpoint or ``timeout_s`` elapses. The mock reflects
    the setpoint instantly so this returns on the first poll (no sleep); a real
    chamber slews, so a never-settling chamber raises ``SerDesError`` rather than
    letting the eye/die-temp be read at the wrong corner."""
    deadline = time.monotonic() + timeout_s
    while True:
        if chamber.settled(tolerance_c=tolerance_c):
            return
        if time.monotonic() >= deadline:
            raise SerDesError(
                f"thermal chamber did not settle within {timeout_s:g}s "
                f"(tolerance {tolerance_c:g} C)")
        time.sleep(DEFAULT_SETTLE_POLL_S)


def vt_shmoo(serdes: SerDesLink, *, psu: PowerSupply, chamber: ThermalChamber,
             voltages: Sequence[float], temperatures: Sequence[float],
             link: int = 0,
             direction: LinkDirection = LinkDirection.FORWARD,
             settle_timeout_s: float = DEFAULT_SETTLE_TIMEOUT_S,
             settle_tolerance_c: float = DEFAULT_SETTLE_TOLERANCE_C) -> ShmooResult:
    """Run a two-axis voltage x temperature margining shmoo on one link.

    At each (temperature, voltage) corner: set the chamber, **wait for the
    chamber to settle within ``settle_tolerance_c`` of the setpoint** (bounded by
    ``settle_timeout_s``), set the rail, force a re-train (AEQ re-convergence),
    then read the on-die temperature and the EOM eye. A corner where the link
    doesn't re-lock fails ``aeq_reconverged``; a corner whose eye closes fails
    ``all_corners_eye_open``. Raises ``SerDesError`` if the chamber never settles
    so a slewing chamber can never misattribute eye margin to the wrong corner."""
    points: list[ShmooPoint] = []
    for temp in temperatures:
        chamber.set_temperature(temp)
        _wait_settled(chamber, timeout_s=settle_timeout_s,
                      tolerance_c=settle_tolerance_c)
        for volt in voltages:
            psu.set_voltage(volt)
            serdes.force_relock(link)            # AEQ re-converge after the V/T step
            locks = serdes.lock_status()         # one read per corner
            this_link = next((ll for ll in locks if ll.link == link), None)
            if this_link is None:
                raise SerDesError(f"link {link} not present in lock_status()")
            relocked = this_link.locked
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
