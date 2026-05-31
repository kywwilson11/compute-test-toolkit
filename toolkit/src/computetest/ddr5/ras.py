"""
DDR5 RAS control-plane adapter (Sprint 4.3).

Extends the ``memory.py`` EDAC CE/UE reader to the DDR5-specific RAS control
plane exposed by the Linux 6.15 EDAC sysfs: Error Check and Scrub (``ecs_fruX``),
background patrol scrub (``scrubX``), and memory repair (``mem_repairX`` — sPPR /
hPPR / sparing). Modeled as a deterministic in-memory device (``MockDdr5Ras``) so
ECS/PPR are unit-testable on macOS exactly like the CE/UE counters, with room for
a real EDAC sysfs backend to drop in behind the same surface. Only the mock is
implemented today; no real-hardware ecs_fruX/scrubX/mem_repairX path exists yet.
"""
from __future__ import annotations

from dataclasses import dataclass

# ECS error-count thresholds exposed by the Linux 6.15 EDAC ECS ABI.
ECS_THRESHOLDS = (256, 1024, 4096)
# ECS count mode — the enumerated Linux EDAC ``ecs_fruX`` 'mode' values.
ECS_MODES = ("counts_codewords", "counts_rows")


@dataclass
class EcsConfig:
    """Error Check and Scrub config (Linux ``ecs_fruX`` sysfs)."""
    mode: str = "counts_codewords"       # counts_codewords | counts_rows
    log_entry_type: int = 0
    threshold: int = 1024                # one of ECS_THRESHOLDS
    enabled: bool = True

    def to_dict(self) -> dict:
        return {"mode": self.mode, "log_entry_type": self.log_entry_type,
                "threshold": self.threshold, "enabled": self.enabled}


@dataclass
class ScrubConfig:
    """Background patrol-scrub config (Linux ``scrubX`` sysfs)."""
    addr: int = 0
    size: int = 0
    enable_background: bool = True
    cycle_duration_s: int = 3600

    def to_dict(self) -> dict:
        return {"addr": self.addr, "size": self.size,
                "enable_background": self.enable_background,
                "cycle_duration_s": self.cycle_duration_s}


@dataclass
class MemRepairRequest:
    """A memory-repair request (Linux ``mem_repairX`` sysfs)."""
    repair_type: str = "ppr"             # ppr | sparing
    persist_mode: int = 0                # 0 = sPPR (temporary), 1 = hPPR (permanent)
    hpa: int = 0
    bank: int = 0
    row: int = 0
    column: int = 0

    def to_dict(self) -> dict:
        return {"repair_type": self.repair_type, "persist_mode": self.persist_mode,
                "hpa": self.hpa, "bank": self.bank, "row": self.row,
                "column": self.column}


class Ddr5RasError(RuntimeError):
    """A DDR5 RAS control-plane fault (bad ECS threshold, unknown DIMM/repair)."""


class MockDdr5Ras:
    """Deterministic in-memory model of the DDR5 RAS control plane (Linux 6.15
    EDAC ``ecs_fruX`` / ``scrubX`` / ``mem_repairX`` sysfs)."""

    def __init__(self, *, dimms: tuple[str, ...] = ("DIMM_A1", "DIMM_A2"),
                 injected_ue: int = 0, injected_scrub_corrected: int = 0,
                 injected_scrub_max_row: int = 0,
                 injected_hppr_resources: int = 4) -> None:
        self._ecs = EcsConfig()
        self._scrub = ScrubConfig()
        self._ce: dict[str, int] = {d: 0 for d in dimms}
        self._ue = injected_ue
        self._scrub_corrected = injected_scrub_corrected
        self._scrub_max_row = injected_scrub_max_row
        self._repaired: set[int] = set()
        self._persisted: set[int] = set()
        self._hppr_resources = injected_hppr_resources

    # --- ECS --------------------------------------------------------------
    def read_ecs(self) -> EcsConfig:
        return self._ecs

    def set_ecs(self, *, mode: str | None = None, log_entry_type: int | None = None,
                threshold: int | None = None, enabled: bool | None = None) -> None:
        """Program ECS settings; they stick on the next ``read_ecs``."""
        if threshold is not None and threshold not in ECS_THRESHOLDS:
            raise Ddr5RasError(
                f"ECS threshold must be one of {ECS_THRESHOLDS}; got {threshold}")
        if mode is not None and mode not in ECS_MODES:
            raise Ddr5RasError(
                f"ECS mode must be one of {ECS_MODES}; got {mode!r}")
        if mode is not None:
            self._ecs.mode = mode
        if log_entry_type is not None:
            self._ecs.log_entry_type = log_entry_type
        if threshold is not None:
            self._ecs.threshold = threshold
        if enabled is not None:
            self._ecs.enabled = enabled

    def trigger_scrub_cycle(self) -> tuple[int, int]:
        """Run a scrub cycle; return (accumulated_corrected_count, max_error_row)."""
        return self._scrub_corrected, self._scrub_max_row

    # --- background scrub -------------------------------------------------
    def read_scrub(self) -> ScrubConfig:
        return self._scrub

    def set_scrub(self, *, enable_background: bool | None = None,
                  cycle_duration_s: int | None = None) -> None:
        if enable_background is not None:
            self._scrub.enable_background = enable_background
        if cycle_duration_s is not None:
            self._scrub.cycle_duration_s = cycle_duration_s

    # --- EDAC counters / EINJ stimulus ------------------------------------
    def read_edac(self) -> tuple[int, int, dict[str, int]]:
        """Return (total_ce, total_ue, per_dimm_ce)."""
        return sum(self._ce.values()), self._ue, dict(self._ce)

    def inject_ce(self, dimm: str, count: int = 1) -> None:
        """Stimulus hook: add corrected errors on a DIMM (models ACPI-EINJ)."""
        if dimm not in self._ce:
            raise Ddr5RasError(f"unknown DIMM {dimm!r}; have {sorted(self._ce)}")
        self._ce[dimm] += count

    def inject_ue(self, count: int = 1) -> None:
        """Stimulus hook: add uncorrectable errors."""
        self._ue += count

    # --- memory repair (PPR / sparing) ------------------------------------
    def perform_repair(self, req: MemRepairRequest) -> None:
        """Apply a PPR/sparing repair. ``persist_mode=1`` (hPPR) survives a power
        cycle; ``persist_mode=0`` (sPPR) reverts."""
        if req.repair_type not in ("ppr", "sparing"):
            raise Ddr5RasError(f"repair_type must be ppr|sparing; got {req.repair_type!r}")
        self._repaired.add(req.hpa)
        if req.persist_mode == 1:
            self._persisted.add(req.hpa)

    def is_repaired(self, hpa: int) -> bool:
        return hpa in self._repaired

    def hppr_resources_available(self) -> int:
        """Advertised available hard-PPR resources (DDR5 MR54-57)."""
        return self._hppr_resources

    def power_cycle(self) -> None:
        """Model a power cycle: sPPR repairs revert, hPPR persist, EDAC counters
        reset."""
        self._repaired = set(self._persisted)
        self._ce = {d: 0 for d in self._ce}
        self._ue = 0
