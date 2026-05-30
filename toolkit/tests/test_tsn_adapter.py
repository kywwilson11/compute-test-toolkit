"""Sprint 4.2.11: TSN result schema + certified-CTT drop-in."""
from __future__ import annotations

import json

import pytest

from computetest.tsn.adapter import (
    TSN_COLUMNS,
    TsnTestRecord,
    call_linuxptp_pmc,
    call_ttworkbench,
    is_linuxptp_available,
    is_ttworkbench_available,
    to_csv,
    to_json,
)


def _rec():
    return TsnTestRecord(test_id="99.1.1", group="preemption", title="reception",
                         result="pass", metric="verify_time", value=10.0, unit="ms",
                         limit=128.0)


class TestSchema:
    def test_to_json_round_trip(self):
        data = json.loads(to_json([_rec()]))
        assert data[0]["test_id"] == "99.1.1" and data[0]["result"] == "pass"
        assert data[0]["limit"] == 128.0

    def test_to_csv_header_is_stable_columns(self):
        out = to_csv([_rec()])
        assert out.splitlines()[0] == ",".join(TSN_COLUMNS)
        assert "99.1.1" in out


class TestCttDropIn:
    def test_availability_returns_bool(self):
        assert isinstance(is_ttworkbench_available(), bool)
        assert isinstance(is_linuxptp_available(), bool)

    def test_call_ttworkbench_raises_when_absent(self, monkeypatch):
        monkeypatch.setattr("computetest.tsn.adapter.shutil.which", lambda _: None)
        with pytest.raises(RuntimeError, match="ttworkbench not on PATH"):
            call_ttworkbench("config.yaml")

    def test_call_linuxptp_raises_when_absent(self, monkeypatch):
        monkeypatch.setattr("computetest.tsn.adapter.shutil.which", lambda _: None)
        with pytest.raises(RuntimeError, match="pmc not on PATH"):
            call_linuxptp_pmc()
