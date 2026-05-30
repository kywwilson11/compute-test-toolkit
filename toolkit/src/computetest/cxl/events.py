"""
CXL Event Records + Get/Clear flow (Sprint 4.4).

Models the CXL device event logs (Informational / Warning / Failure / Fatal) and
the four event-record types (General Media / DRAM / Memory Module / Memory
Sparing), with a Get + Clear-by-handle flow that removes exactly the cleared
records. A corpus under ``corpus/cxl/`` carries known-good records + expected.json
(the parser tier needs no hardware), mirroring ``corpus/nvme_ocp_internal_log``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum


class EventLog(IntEnum):
    """The four CXL device event logs (by severity)."""
    INFORMATIONAL = 0
    WARNING = 1
    FAILURE = 2
    FATAL = 3


class EventRecordType(str, Enum):
    GENERAL_MEDIA = "general_media"
    DRAM = "dram"
    MEMORY_MODULE = "memory_module"
    MEMORY_SPARING = "memory_sparing"


@dataclass
class EventRecord:
    """One CXL event-log record."""
    handle: int
    record_type: EventRecordType
    log: EventLog
    fields: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"handle": self.handle, "record_type": self.record_type.value,
                "log": int(self.log), "fields": dict(self.fields)}

    @classmethod
    def from_dict(cls, d: dict) -> EventRecord:
        return cls(handle=d["handle"],
                   record_type=EventRecordType(d["record_type"]),
                   log=EventLog(d["log"]), fields=dict(d.get("fields", {})))


class EventLogStore:
    """In-memory CXL event-log store: Get + Clear-by-handle across the 4 logs."""

    def __init__(self) -> None:
        self._logs: dict[EventLog, list[EventRecord]] = {lg: [] for lg in EventLog}

    def add(self, rec: EventRecord) -> None:
        self._logs[rec.log].append(rec)

    def get_event_records(self, log: EventLog) -> list[EventRecord]:
        return list(self._logs[log])

    def clear_event_records(self, log: EventLog, handles: list[int]) -> int:
        """Clear records by handle; return how many were removed."""
        drop = set(handles)
        before = len(self._logs[log])
        self._logs[log] = [r for r in self._logs[log] if r.handle not in drop]
        return before - len(self._logs[log])


@dataclass
class EventClearHealth:
    """Verdict for a Get + Clear-by-handle round."""
    log: int
    n_cleared: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"CXL events log={self.log} cleared={self.n_cleared} -> {state}"

    def to_dict(self) -> dict:
        return {"log": self.log, "n_cleared": self.n_cleared,
                "checks": self.checks, "ok": self.ok}


def check_event_get_clear(store: EventLogStore, log: EventLog, *,
                          handles_to_clear: list[int]) -> EventClearHealth:
    """Clear records by handle and assert exactly the requested (present) handles
    were removed and every other handle remains."""
    before = {r.handle for r in store.get_event_records(log)}
    requested_present = before & set(handles_to_clear)
    n_cleared = store.clear_event_records(log, handles_to_clear)
    after = {r.handle for r in store.get_event_records(log)}
    checks = {
        "requested_handles_removed": not (after & requested_present),
        "untouched_handles_remain": (before - set(handles_to_clear)) <= after,
        "cleared_count_matches": n_cleared == len(requested_present),
    }
    return EventClearHealth(log=int(log), n_cleared=n_cleared, checks=checks)
