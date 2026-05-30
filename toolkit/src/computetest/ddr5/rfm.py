"""
DDR5 RFM / PRAC enablement + alert-path verification (Sprint 4.3).

STRICTLY enablement + alert-path. Assert that Refresh Management (RFM) and
Per-Row Activation Counting (PRAC) are enabled per the part's capability + OEM
policy, and that the activation-count alert path is wired. This deliberately
does NOT inject any activation pattern or run a disturbance stress — it is a
configuration / reporting-plumbing check of the platform's own anti-disturbance
posture, the defensive inverse of the disturbance-attack literature.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RfmPracHealth:
    """DDR5 RFM/PRAC enablement + alert-path verdict."""
    capability_supported: bool
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        cap = "supported" if self.capability_supported else "unsupported"
        return f"DDR5 RFM/PRAC ({cap}) -> {state}"

    def to_dict(self) -> dict:
        return {"capability_supported": self.capability_supported,
                "checks": self.checks, "ok": self.ok}


def check_rfm_prac(*, capability_supported: bool, rfm_enabled: bool,
                   prac_enabled: bool, alert_path_wired: bool,
                   oem_policy_requires: bool = True) -> RfmPracHealth:
    """Verify RFM/PRAC are enabled — when the part supports them and OEM policy
    requires — and that the activation-count alert path is wired. Enablement +
    alert only; no activation-pattern or disturbance stress is performed."""
    required = capability_supported and oem_policy_requires
    checks = {
        "rfm_enabled": rfm_enabled if required else True,
        "prac_enabled": prac_enabled if required else True,
        "alert_path_wired": alert_path_wired,
    }
    return RfmPracHealth(capability_supported=capability_supported, checks=checks)
