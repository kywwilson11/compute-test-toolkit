"""
PCIe link health: speed/width vs maximum (and vs expectation), plus retrain
monitoring. A link that ends at the right speed/width but retrained 40 times during
the soak is a failing unit; a single snapshot would pass it (see Guide A, §4.5).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .backend import Backend, LINK_SPEED_GTPS


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

    @property
    def speed_degraded(self) -> bool:
        target = self.expected_speed or self.max_speed
        return self.speed < target

    @property
    def width_degraded(self) -> bool:
        target = self.expected_width or self.max_width
        return self.width < target

    @property
    def ok(self) -> bool:
        return not (self.speed_degraded or self.width_degraded) and self.retrains == 0

    def summary(self) -> str:
        gt = LINK_SPEED_GTPS.get(self.speed, 0)
        flags = []
        if self.speed_degraded:
            tgt = self.expected_speed or self.max_speed
            flags.append(f"SPEED Gen{self.speed}<Gen{tgt}")
        if self.width_degraded:
            tgt = self.expected_width or self.max_width
            flags.append(f"WIDTH x{self.width}<x{tgt}")
        if self.retrains:
            flags.append(f"RETRAINS={self.retrains}")
        state = "OK" if self.ok else "DEGRADED(" + ",".join(flags) + ")"
        return f"{self.bdf} Gen{self.speed}({gt:g}GT/s) x{self.width} -> {state}"

    def to_dict(self) -> dict:
        return {
            "bdf": self.bdf, "speed": self.speed, "width": self.width,
            "max_speed": self.max_speed, "max_width": self.max_width,
            "speed_degraded": self.speed_degraded, "width_degraded": self.width_degraded,
            "retrains": self.retrains, "ok": self.ok,
        }


def check_link(backend: Backend, bdf: str, *, expected_speed: int | None = None,
               expected_width: int | None = None, watch_s: float = 0.0,
               poll_s: float = 0.005) -> LinkHealth:
    """Check link speed/width against max (or expectation). If ``watch_s`` > 0,
    poll the link-training bit for that long and count retrain events."""
    dev = backend.get_device(bdf)
    health = LinkHealth(bdf, dev.current_link_speed, dev.current_link_width,
                        dev.max_link_speed, dev.max_link_width,
                        expected_speed, expected_width)
    if watch_s > 0:
        t0 = time.monotonic()
        was_training = False
        while time.monotonic() - t0 < watch_s:
            _, _, training = backend.read_link_status(bdf)
            health.poll_count += 1
            if training and not was_training:  # count rising edges = retrain events
                health.retrains += 1
            was_training = training
            if poll_s:
                time.sleep(poll_s)
    return health
