"""
PHY PMA electrical conformance — OPEN Alliance TC8 L1 / UNH-IOL Cl.96-97
(Sprint 4.2).

Cross-subsystem reuse: the same 4-port VNA HAL (``instruments.Vna``) and GMSL
S-parameter mask framework (``gmsl.channel``) built in Sprint 4.1.6 serve the
automotive-Ethernet MDI return loss (Sdd11) and mode conversion (Scd21) here,
alongside the EthPhy link-up time (LINKUP_01..03) and SQI coherence
(SIGNAL_01/02).

The OPEN Alliance TC8 numeric limit lines are paywalled, so the masks are
SUPPLIED as arguments (same blocked-but-honest pattern as the GMSL AN-2585
masks): ``check_pma_electrical`` refuses to run without them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..gmsl.channel import SParamMask, check_against_mask
from ..instruments import Vna
from .phy import EthPhy

DEFAULT_PMA_MAX_LINKUP_MS = 100.0
DEFAULT_PMA_SQI_MIN = 5


@dataclass
class PmaHealth:
    """PHY PMA electrical-conformance verdict (TC8 L1 / Cl.96-97)."""
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"PHY PMA electrical -> {state}"

    def to_dict(self) -> dict:
        return {"checks": self.checks, "ok": self.ok}


def check_pma_electrical(phy: EthPhy, vna: Vna, *,
                         mdi_rl_mask: SParamMask | None = None,
                         mode_conv_mask: SParamMask | None = None,
                         max_linkup_ms: float = DEFAULT_PMA_MAX_LINKUP_MS,
                         sqi_min: int = DEFAULT_PMA_SQI_MIN) -> PmaHealth:
    """Check PHY PMA electrical conformance: MDI return loss (Sdd11) and mode
    conversion (Scd21) against supplied masks via the shared VNA, plus link-up
    time and SQI from the PHY. Masks MUST be supplied (TC8 limits are paywalled);
    raises otherwise so a run can't silently pass with no limits applied."""
    if mdi_rl_mask is None or mode_conv_mask is None:
        raise ValueError(
            "PMA MDI return-loss + mode-conversion masks must be supplied "
            "(OPEN Alliance TC8 limits are paywalled; transcribe them).")
    rl = check_against_mask(vna.measure_sparam("SDD11"), mdi_rl_mask)
    mc = check_against_mask(vna.measure_sparam("SCD21"), mode_conv_mask)
    checks = {
        "link_up_time_ok": phy.read_link_up_time() <= max_linkup_ms,   # LINKUP_01..03
        "sqi_ok": phy.read_sqi() >= sqi_min,                            # SIGNAL_01/02
        "mdi_return_loss_ok": rl.ok,                                    # OABR_PMA Sdd11
        "mode_conversion_ok": mc.ok,                                    # OABR_PMA Scd21
    }
    return PmaHealth(checks=checks)
