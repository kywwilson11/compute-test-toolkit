"""
DDR5 Post-Package Repair (PPR) self-heal check (Sprint 4.3).

Verifies the memory-repair control plane: a soft-PPR (sPPR, persist_mode 0)
repair must take effect but revert on a power cycle, while a hard-PPR (hPPR,
persist_mode 1) repair must survive it; hPPR also requires advertised repair
resources (MR54-57). Reliability self-heal validation, not fault injection.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .ras import Ddr5RasError, MemRepairRequest, MockDdr5Ras


@dataclass
class PprHealth:
    """Post-Package Repair verdict for one repaired address."""
    addr: int
    persist_mode: int                # 0 = sPPR, 1 = hPPR
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def mode(self) -> str:
        return "hPPR" if self.persist_mode == 1 else "sPPR"

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"DDR5 PPR addr=0x{self.addr:X} {self.mode} -> {state}"

    def to_dict(self) -> dict:
        return {"addr": self.addr, "persist_mode": self.persist_mode,
                "mode": self.mode, "checks": self.checks, "ok": self.ok}


def check_ppr(ras: MockDdr5Ras, *, addr: int, persist_mode: int) -> PprHealth:
    """Repair ``addr`` and verify the persistence semantics: sPPR takes effect
    but reverts on a power cycle; hPPR takes effect and survives it (and requires
    an advertised hPPR resource)."""
    if persist_mode not in (0, 1):
        raise Ddr5RasError(
            f"persist_mode must be 0 (sPPR) or 1 (hPPR); got {persist_mode} "
            "(Linux mem_repairX persist_mode is strictly 0/1)")
    res_before = ras.hppr_resources_available()
    ras.perform_repair(MemRepairRequest(repair_type="ppr",
                                        persist_mode=persist_mode, hpa=addr))
    repaired = ras.is_repaired(addr)
    ras.power_cycle()
    survives = ras.is_repaired(addr)
    if persist_mode == 1:
        checks = {
            "repaired": repaired,
            "survives_power_cycle": survives,
            "hppr_resource_advertised": res_before > 0,
        }
    else:
        checks = {
            "repaired": repaired,
            "reverts_on_power_cycle": not survives,
        }
    return PprHealth(addr=addr, persist_mode=persist_mode, checks=checks)
