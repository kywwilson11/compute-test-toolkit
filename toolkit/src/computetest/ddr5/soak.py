"""
DDR5 ECC reporting-plumbing soak snapshot (Sprint 4.3).

Captures the RAS state (EDAC CE/UE counters, ECS threshold, advertised hPPR
resources) so a caller can snapshot it across a stressapptest soak — run HOT via
``memory.stress_memory`` at a T/V corner — and diff before/after to catch a
rising corrected-error rate.
"""
from __future__ import annotations

from dataclasses import dataclass

from .ras import MockDdr5Ras


@dataclass
class SoakSnapshot:
    """A point-in-time RAS state snapshot for soak diffing."""
    total_ce: int
    total_ue: int
    ecs_threshold: int
    hppr_resources: int

    def to_dict(self) -> dict:
        return {"total_ce": self.total_ce, "total_ue": self.total_ue,
                "ecs_threshold": self.ecs_threshold,
                "hppr_resources": self.hppr_resources}


def soak_snapshot(ras: MockDdr5Ras) -> SoakSnapshot:
    """Snapshot the DDR5 RAS state (EDAC counters + ECS threshold + hPPR
    resources) for a before/after soak diff."""
    ce, ue, _ = ras.read_edac()
    return SoakSnapshot(total_ce=ce, total_ue=ue,
                        ecs_threshold=ras.read_ecs().threshold,
                        hppr_resources=ras.hppr_resources_available())
