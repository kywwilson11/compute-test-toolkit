"""
DDR5 ECS (Error Check and Scrub) control + counter-readback check (Sprint 4.3).

Drives the ECS control plane (``ecs_fruX``): program the threshold and confirm
the settings stick, trigger a scrub cycle and read back the accumulated
corrected-error count + the row with the most errors (MR16-20), and assert the
full-array scrub completes within the 24-hour budget. Conformance of the
scrub-reporting contract — not fault injection.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ras import MockDdr5Ras

DEFAULT_MAX_SCRUB_TIME_S = 86_400        # full-array scrub must finish within 24 h


@dataclass
class EcsHealth:
    """ECS control + scrub-reporting verdict."""
    threshold: int
    corrected_count: int
    max_error_row: int
    scrub_time_s: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"DDR5 ECS thr={self.threshold} corrected={self.corrected_count} "
                f"max_row={self.max_error_row} scrub={self.scrub_time_s}s -> {state}")

    def to_dict(self) -> dict:
        return {"threshold": self.threshold, "corrected_count": self.corrected_count,
                "max_error_row": self.max_error_row, "scrub_time_s": self.scrub_time_s,
                "checks": self.checks, "ok": self.ok}


def check_ecs(ras: MockDdr5Ras, *, threshold: int = 1024,
              max_scrub_time_s: int = DEFAULT_MAX_SCRUB_TIME_S) -> EcsHealth:
    """Program ECS, confirm the settings stick, run a scrub cycle, read the
    corrected-error count + max-error row, and assert the full-array scrub fits
    the 24-hour budget."""
    ras.set_ecs(threshold=threshold, enabled=True)
    ecs = ras.read_ecs()
    corrected, max_row = ras.trigger_scrub_cycle()
    scrub_time_s = ras.read_scrub().cycle_duration_s
    checks = {
        "ecs_settings_stick": ecs.threshold == threshold and ecs.enabled,
        "scrub_time_within_24h": scrub_time_s <= max_scrub_time_s,
    }
    return EcsHealth(threshold=ecs.threshold, corrected_count=corrected,
                     max_error_row=max_row, scrub_time_s=scrub_time_s, checks=checks)
