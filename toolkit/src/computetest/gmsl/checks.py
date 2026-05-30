"""
GMSL SerDes conformance checks (Sprint 4.1).

The verdict/health layer on top of the ``SerDesLink`` device abstraction in
``serdes.py``. Each check consumes a ``SerDesLink`` and returns a Health-shaped
result (a ``checks`` dict + ``ok`` + ``summary``/``to_dict``) so it drops
straight into the generic ``io.ocpdiag.emit_health`` path, exactly like the
legacy ``GmslHealth``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .serdes import GmslMode, LinkLock, SerDesLink

# Default bound on lock acquisition (cold or forced relock). Real programs tune
# this per part/channel from the AN-2585 / UG-2208 bring-up budget.
DEFAULT_MAX_LOCK_MS = 20.0


@dataclass
class SerDesLinkHealth:
    """Lock + negotiated-mode verdict for one SerDes device's links."""
    part: str
    expect_mode: GmslMode
    links: list[LinkLock]
    max_lock_ms: float
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        locked = sum(1 for li in self.links if li.locked)
        modes = sorted({li.mode.value for li in self.links})
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"GMSL serdes {self.part}: {locked}/{len(self.links)} locked, "
                f"mode={'/'.join(modes)} (want {self.expect_mode.value}) -> {state}")

    def to_dict(self) -> dict:
        return {"part": self.part, "expect_mode": self.expect_mode.value,
                "max_lock_ms": self.max_lock_ms,
                "links": [li.to_dict() for li in self.links],
                "checks": self.checks, "ok": self.ok}


def check_serdes_link(serdes: SerDesLink, *,
                      expect_mode: GmslMode = GmslMode.PAM4_12G,
                      max_lock_ms: float = DEFAULT_MAX_LOCK_MS) -> SerDesLinkHealth:
    """Verify every link is locked, in the *expected* negotiated mode, and locked
    within the time budget.

    A "locked" link that fell back to a lower mode (the core GMSL3 silent-degrade
    case) fails ``mode_ok`` even though ``all_locked`` passes — which is the whole
    point of carrying the negotiated mode rather than a bare boolean.
    """
    links = serdes.lock_status()
    info = serdes.info()
    all_locked = bool(links) and all(li.locked for li in links)
    mode_ok = bool(links) and all(li.mode_ok(expect_mode) for li in links)
    # Lock-time is only meaningful where the link locked AND reported a time;
    # an empty set can't fault the device (all([]) is True).
    lock_times = [li.lock_time_ms for li in links
                  if li.locked and li.lock_time_ms is not None]
    lock_time_ok = all(t <= max_lock_ms for t in lock_times)
    checks = {"all_locked": all_locked, "mode_ok": mode_ok,
              "lock_time_ok": lock_time_ok}
    return SerDesLinkHealth(part=info.part_number, expect_mode=expect_mode,
                            links=links, max_lock_ms=max_lock_ms, checks=checks)
