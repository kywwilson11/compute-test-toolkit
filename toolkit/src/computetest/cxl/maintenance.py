"""
CXL memory RAS maintenance (Perform Maintenance, Opcode 0600h) (Sprint 4.4).

Verifies the Perform Maintenance flow (class 06h "Maintenance" / sub 0h
"Perform"): the operation-class selects the feature (PPR = 01h, with sPPR/hPPR
sub-modes), a query-mode call does not mutate, a perform returns Background-
Started, and a second maintenance command while one is running returns Busy.

NOTE: only Opcode 0600h and PPR=01h are confirmed; the memory-sparing / ECS /
scrub / BIST operation-class codes must be taken from the CXL spec / CTS (the
research brief's "03h/00h" for sparing was wrong) before encoding as assertions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

PERFORM_MAINTENANCE_OPCODE = 0x0600      # CCI opcode (class 06h, command 00h)


class MaintenanceClass(IntEnum):
    """Perform-Maintenance operation classes (confirmed subset)."""
    PPR = 0x01                           # Post-Package Repair (sPPR/hPPR sub-modes)
    # SPARING / ECS / SCRUB / BIST classes: codes unconfirmed -- take from the CTS.


class MaintenanceDevice:
    """Models the CXL Perform Maintenance background-command flow."""

    def __init__(self) -> None:
        self._busy = False
        self._performed: list[int] = []

    @property
    def performed(self) -> list[int]:
        return list(self._performed)

    def perform(self, op_class: MaintenanceClass, *, query: bool = False) -> str:
        """Return "busy" / "success" (query) / "background" (perform)."""
        if self._busy:
            return "busy"
        if query:
            return "success"                          # query mode does not mutate
        self._busy = True
        self._performed.append(int(op_class))
        return "background"

    def complete(self) -> None:
        """Mark the running background command complete."""
        self._busy = False


@dataclass
class MaintenanceHealth:
    """Perform-Maintenance flow verdict."""
    op_class: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"CXL maintenance op_class=0x{self.op_class:02X} -> {state}"

    def to_dict(self) -> dict:
        return {"op_class": self.op_class, "checks": self.checks, "ok": self.ok}


def check_maintenance(dev: MaintenanceDevice, *,
                      op_class: MaintenanceClass) -> MaintenanceHealth:
    """Exercise the Perform Maintenance flow: a query must not mutate, a perform
    must return Background-Started, and a second command while running must
    return Busy."""
    before = len(dev.performed)
    q = dev.perform(op_class, query=True)
    query_no_mutation = (q == "success") and (len(dev.performed) == before)
    r1 = dev.perform(op_class)
    r2 = dev.perform(op_class)                         # while the first is running
    dev.complete()
    checks = {
        "query_no_mutation": query_no_mutation,
        "perform_background_started": r1 == "background",
        "second_returns_busy": r2 == "busy",
    }
    return MaintenanceHealth(op_class=int(op_class), checks=checks)
