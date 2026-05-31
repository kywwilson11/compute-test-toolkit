"""
PCIe link health: speed/width vs maximum (and vs expectation), plus monitoring for
changes *during* the soak — retrains (LTSSM Recovery), the lowest speed/width seen,
and the LBMS/LABS bandwidth-change latches. A link that ends at the right speed/width
but dipped or retrained mid-soak is a failing unit a single snapshot would pass.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .backend import DOWNSTREAM_PORTS, LINK_SPEED_GTPS, Backend, link_bits_per_second

# Re-exported so callers needing the link payload rate have one obvious home
# (the rate is a link property). E.g. `link_bits_per_second(5, 16)` = Gen5 x16 (the
# Zoox compute-platform target, ~504 Gb/s payload); the helper covers Gen1-Gen6.
__all__ = ["LinkHealth", "check_link", "link_bits_per_second"]


@dataclass
class LinkHealth:
    bdf: str
    speed: int
    width: int
    max_speed: int
    max_width: int
    expected_speed: int | None = None
    expected_width: int | None = None
    retrains: int = 0
    poll_count: int = 0
    min_speed: int = 0          # lowest current speed seen during the watch
    min_width: int = 0          # lowest current width seen during the watch
    bw_changed: bool = False    # LBMS/LABS latched: a speed/width change occurred

    def __post_init__(self):
        if self.min_speed == 0:
            self.min_speed = self.speed
        if self.min_width == 0:
            self.min_width = self.width

    @property
    def speed_unknown(self) -> bool:
        """A current/max speed of 0 means sysfs gave us nothing (enumeration or parse
        failure) — that is NOT a healthy link, even with no expectation set."""
        return self.speed <= 0 or self.max_speed <= 0

    @property
    def width_unknown(self) -> bool:
        return self.width <= 0 or self.max_width <= 0

    @property
    def speed_degraded(self) -> bool:
        # Unknown (0) speed reads as degraded, never a silent pass.
        if self.speed_unknown:
            return True
        target = self.expected_speed or self.max_speed
        return self.speed < target or self.min_speed < target

    @property
    def width_degraded(self) -> bool:
        if self.width_unknown:
            return True
        target = self.expected_width or self.max_width
        return self.width < target or self.min_width < target

    @property
    def ok(self) -> bool:
        return (not self.speed_degraded and not self.width_degraded
                and self.retrains == 0 and not self.bw_changed)

    def summary(self) -> str:
        gt = LINK_SPEED_GTPS.get(self.speed, 0)
        flags = []
        if self.speed_unknown:
            flags.append("SPEED unknown (sysfs/enumeration)")
        elif self.speed_degraded:
            tgt = self.expected_speed or self.max_speed
            lo = f"/min Gen{self.min_speed}" if self.min_speed < self.speed else ""
            flags.append(f"SPEED Gen{self.speed}{lo}<Gen{tgt}")
        if self.width_unknown:
            flags.append("WIDTH unknown (sysfs/enumeration)")
        elif self.width_degraded:
            tgt = self.expected_width or self.max_width
            lo = f"/min x{self.min_width}" if self.min_width < self.width else ""
            flags.append(f"WIDTH x{self.width}{lo}<x{tgt}")
        if self.retrains:
            flags.append(f"RETRAINS={self.retrains}")
        if self.bw_changed:
            flags.append("BW-CHANGE(LBMS/LABS)")
        state = "OK" if self.ok else "DEGRADED(" + ",".join(flags) + ")"
        return f"{self.bdf} Gen{self.speed}({gt:g}GT/s) x{self.width} -> {state}"

    def to_dict(self) -> dict:
        return {
            "bdf": self.bdf, "speed": self.speed, "width": self.width,
            "max_speed": self.max_speed, "max_width": self.max_width,
            "min_speed": self.min_speed, "min_width": self.min_width,
            "speed_degraded": self.speed_degraded, "width_degraded": self.width_degraded,
            "speed_unknown": self.speed_unknown, "width_unknown": self.width_unknown,
            "retrains": self.retrains, "bw_changed": self.bw_changed, "ok": self.ok,
        }


def check_link(backend: Backend, bdf: str, *, expected_speed: int | None = None,
               expected_width: int | None = None, watch_s: float = 0.0,
               poll_s: float = 0.005) -> LinkHealth:
    """Check link speed/width against max (or expectation). If ``watch_s`` > 0, poll
    Link Status for that long, counting retrains, tracking the minimum speed/width
    seen, and latching any LBMS/LABS bandwidth-change event.

    LBMS/LABS limitation: per the PCIe base spec those latches are RsvdZ on Endpoints
    and Upstream Ports -- only a link-owning Downstream Port (Root / Switch-Downstream)
    implements them. We therefore arm/read them only when ``bdf`` is such a port; on an
    endpoint they stay clear (the chain diagnostic reads them at the downstream port).
    The retrain count and the min speed/width polling still run for every port, so a
    sampled dip is caught regardless."""
    dev = backend.get_device(bdf)
    health = LinkHealth(bdf, dev.current_link_speed, dev.current_link_width,
                        dev.max_link_speed, dev.max_link_width,
                        expected_speed, expected_width)
    if watch_s > 0:
        owns_bw_latch = backend.read_port_type(bdf) in DOWNSTREAM_PORTS
        if owns_bw_latch:
            backend.clear_link_bw_status(bdf)   # arm the LBMS/LABS latches (RsvdZ on endpoints)
        t0 = time.monotonic()
        was_training = False
        while time.monotonic() - t0 < watch_s:
            ls = backend.read_link_status(bdf)
            health.poll_count += 1
            if ls.training and not was_training:   # rising edge = a retrain event
                health.retrains += 1
            was_training = ls.training
            health.min_speed = min(health.min_speed, ls.speed)
            health.min_width = min(health.min_width, ls.width)
            if owns_bw_latch and (ls.bw_changed or ls.autonomous_bw):
                health.bw_changed = True
            if poll_s:
                time.sleep(poll_s)
    return health
