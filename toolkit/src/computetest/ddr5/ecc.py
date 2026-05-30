"""
DDR5 on-die ECC + EDAC reporting verification (Sprint 4.3).

Standards-defined diagnostic-coverage view: inject a controlled error via
ACPI-EINJ and assert the platform's OWN reporting fires correctly — a 1-bit
error must surface as a CE on the right DIMM (and not be silently dropped), and
a 2-bit error must surface as a UE (on-die ECC is SEC-only, so it cannot be
silently corrected). This is reliability validation, not an attack: the
injection exercises the very error a safety mechanism is designed to detect.

Caveat (real path): confirm the injector reaches the reportable IMC CE path
rather than being absorbed by on-die ECC first — see the Sprint-4 roadmap.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ras import MockDdr5Ras


@dataclass
class EccReportHealth:
    """EDAC error-reporting verdict for one injected error."""
    error_class: str                 # "CE" | "UE"
    ce_delta: int
    ue_delta: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"DDR5 ECC {self.error_class} ce_d={self.ce_delta} "
                f"ue_d={self.ue_delta} -> {state}")

    def to_dict(self) -> dict:
        return {"error_class": self.error_class, "ce_delta": self.ce_delta,
                "ue_delta": self.ue_delta, "checks": self.checks, "ok": self.ok}


def check_ce_reporting(ras: MockDdr5Ras, *, dimm: str) -> EccReportHealth:
    """Inject a 1-bit (correctable) error on ``dimm`` and assert it is reported
    as a CE on the right DIMM, with no spurious UE."""
    before_ce, before_ue, before_per = ras.read_edac()
    ras.inject_ce(dimm, 1)
    after_ce, after_ue, after_per = ras.read_edac()
    checks = {
        "ce_counter_incremented": after_ce == before_ce + 1,
        "correct_dimm_attribution": after_per[dimm] == before_per.get(dimm, 0) + 1,
        "no_spurious_ue": after_ue == before_ue,
    }
    return EccReportHealth(error_class="CE", ce_delta=after_ce - before_ce,
                           ue_delta=after_ue - before_ue, checks=checks)


def check_ue_reporting(ras: MockDdr5Ras) -> EccReportHealth:
    """Inject a 2-bit (uncorrectable) error and assert it is reported as a UE —
    on-die ECC is SEC-only, so it must not be silently corrected to a CE."""
    before_ce, before_ue, _ = ras.read_edac()
    ras.inject_ue(1)
    after_ce, after_ue, _ = ras.read_edac()
    checks = {
        "ue_reported": after_ue == before_ue + 1,
        "not_silently_corrected": after_ce == before_ce,
    }
    return EccReportHealth(error_class="UE", ce_delta=after_ce - before_ce,
                           ue_delta=after_ue - before_ue, checks=checks)
