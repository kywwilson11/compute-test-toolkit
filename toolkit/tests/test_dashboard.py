"""Dashboard FIX 5: with the default COMPUTETEST_DB=':memory:' each request opens a
fresh, empty in-memory DB, so the dashboard shows nothing. The app must warn clearly
(log + an API field) rather than silently appear broken — and stay importable for tests."""
import logging
import os
import sys

import pytest

# The dashboard package lives at the repo root (not under src/), so make it importable.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("fastapi")

from dashboard import app as dash  # noqa: E402


def test_module_is_importable_with_default_db():
    # Importing must never crash (tests, --help): it warns, it doesn't refuse.
    assert dash.app is not None
    assert hasattr(dash, "_warn_if_in_memory")


def test_warn_helper_flags_in_memory(caplog):
    with caplog.at_level(logging.WARNING, logger="computetest.dashboard"):
        flagged = dash._warn_if_in_memory(":memory:")
    assert flagged is True
    assert any(":memory:" in r.message and "COMPUTETEST_DB" in r.message
               for r in caplog.records)


def test_warn_helper_silent_for_file_path(caplog):
    with caplog.at_level(logging.WARNING, logger="computetest.dashboard"):
        flagged = dash._warn_if_in_memory("results.db")
    assert flagged is False
    assert caplog.records == []          # a real file path produces no warning


def test_summary_api_surfaces_db_warning_when_in_memory(monkeypatch):
    # When the configured DB is in-memory, /api/summary carries a db_warning so a user
    # staring at an empty dashboard learns why (not just whoever reads the server logs).
    monkeypatch.setattr(dash, "_DB_IN_MEMORY", True)
    out = dash.summary()
    assert "db_warning" in out and ":memory:" in out["db_warning"]


def test_summary_api_has_no_warning_for_file_db(monkeypatch):
    monkeypatch.setattr(dash, "_DB_IN_MEMORY", False)
    out = dash.summary()
    assert "db_warning" not in out
