"""Sprint 4.4.4: CXL Event Records + Get/Clear-by-handle flow + corpus."""
from __future__ import annotations

import json
from pathlib import Path

from computetest.cxl import (
    EventClearHealth,
    EventLog,
    EventLogStore,
    EventRecord,
    EventRecordType,
    check_event_get_clear,
)

CORPUS = Path(__file__).parent.parent / "corpus" / "cxl" / "event-records"


def _records() -> list[EventRecord]:
    data = json.loads((CORPUS / "expected.json").read_text())
    return [EventRecord.from_dict(d) for d in data]


class TestCorpus:
    def test_records_round_trip(self):
        data = json.loads((CORPUS / "expected.json").read_text())
        recs = [EventRecord.from_dict(d) for d in data]
        assert [r.to_dict() for r in recs] == data

    def test_all_four_record_types_present(self):
        types = {r.record_type for r in _records()}
        assert types == {EventRecordType.GENERAL_MEDIA, EventRecordType.DRAM,
                         EventRecordType.MEMORY_MODULE, EventRecordType.MEMORY_SPARING}


class TestStore:
    def _store(self) -> EventLogStore:
        store = EventLogStore()
        for h in (1, 2, 3):
            store.add(EventRecord(handle=h, record_type=EventRecordType.DRAM,
                                  log=EventLog.FAILURE))
        return store

    def test_get_returns_added(self):
        assert {r.handle for r in self._store().get_event_records(EventLog.FAILURE)} == {1, 2, 3}

    def test_clear_by_handle_removes_exactly(self):
        store = self._store()
        h = check_event_get_clear(store, EventLog.FAILURE, handles_to_clear=[1, 3])
        assert isinstance(h, EventClearHealth) and h.ok and h.n_cleared == 2
        assert {r.handle for r in store.get_event_records(EventLog.FAILURE)} == {2}

    def test_clear_nonexistent_handle_is_noop(self):
        store = self._store()
        h = check_event_get_clear(store, EventLog.FAILURE, handles_to_clear=[99])
        assert h.ok and h.n_cleared == 0
        assert len(store.get_event_records(EventLog.FAILURE)) == 3

    def test_summary_and_to_dict(self):
        store = self._store()
        h = check_event_get_clear(store, EventLog.FAILURE, handles_to_clear=[2])
        assert "CXL events" in h.summary()
        assert h.to_dict()["n_cleared"] == 1
