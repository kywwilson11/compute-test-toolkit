"""
CXL poison list + poison/viral containment check (Sprint 4.4).

Models the cxl-cli / ndctl poison flow: inject via the debugfs hook, read the
poison list by memdev/region, assert the DPA + length, clear, and assert the
list is empty. Plus the containment posture — viral (via the DVSEC bits) or a
DPC fallback where poison containment is absent. Standards-defined containment /
recovery view (OCP "Using Poison to Contain and Recover"), not an attack.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class PoisonSource(IntEnum):
    UNKNOWN = 0
    EXTERNAL = 1
    INTERNAL = 2
    INJECTED = 3


@dataclass
class PoisonEntry:
    """One poison-list entry (DPA + length + source)."""
    dpa: int
    length: int
    source: PoisonSource

    def to_dict(self) -> dict:
        return {"dpa": self.dpa, "length": self.length, "source": self.source.name}


class PoisonList:
    """In-memory CXL poison list: inject (debugfs) -> get -> clear."""

    def __init__(self) -> None:
        self._entries: list[PoisonEntry] = []

    def inject(self, dpa: int, length: int,
               source: PoisonSource = PoisonSource.INJECTED) -> None:
        self._entries.append(PoisonEntry(dpa=dpa, length=length, source=source))

    def get_list(self) -> list[PoisonEntry]:
        return list(self._entries)

    def clear(self, dpa: int) -> None:
        self._entries = [e for e in self._entries if e.dpa != dpa]


@dataclass
class PoisonHealth:
    """Poison inject/list/clear round-trip verdict."""
    dpa: int
    length: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"CXL poison dpa=0x{self.dpa:X} len={self.length} -> {state}"

    def to_dict(self) -> dict:
        return {"dpa": self.dpa, "length": self.length, "checks": self.checks,
                "ok": self.ok}


def check_poison_inject_clear(plist: PoisonList, *, dpa: int,
                              length: int) -> PoisonHealth:
    """Inject a poison entry, confirm it appears on the list with the right
    DPA + length, clear it, and confirm the list no longer holds that DPA."""
    plist.inject(dpa, length)
    listed = plist.get_list()
    found = any(e.dpa == dpa and e.length == length for e in listed)
    plist.clear(dpa)
    cleared = not any(e.dpa == dpa for e in plist.get_list())
    checks = {"poison_listed_with_dpa_length": found, "poison_cleared": cleared}
    return PoisonHealth(dpa=dpa, length=length, checks=checks)


@dataclass
class ContainmentHealth:
    """Poison/viral containment posture verdict."""
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"CXL containment -> {state}"

    def to_dict(self) -> dict:
        return {"checks": self.checks, "ok": self.ok}


def check_containment(*, viral_supported: bool,
                      viral_asserts_on_uncorrectable: bool,
                      dpc_fallback_available: bool) -> ContainmentHealth:
    """Verify a containment mechanism exists — viral (when supported it must
    assert on an uncorrectable error) or a DPC fallback when poison containment
    is absent."""
    checks = {
        "containment_present": viral_supported or dpc_fallback_available,
        "viral_asserts_when_supported": (viral_asserts_on_uncorrectable
                                         if viral_supported else True),
    }
    return ContainmentHealth(checks=checks)
