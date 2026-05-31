"""ResultStore queries (yield_by_test, stations, summary, recent) + context-manager,
and TestReport.summary()/counts. Complements test_topology_harness (the full-plan run)."""
from computetest.harness import TestReport as Report
from computetest.results import ResultStore
from computetest.results import TestRecord as Record


# --- TestReport ------------------------------------------------------------- #
def test_report_counts_and_summary():
    rep = Report([
        Record("pcie", "0000:03:00.0", "diagnose", "pass"),
        Record("nvme", "/dev/nvme0", "smart", "fail", message="media errors"),
        Record("gpu", "0", "health", "skip"),
    ])
    assert rep.counts == {"pass": 1, "fail": 1, "skip": 1}
    assert rep.ok is False                                # a fail present
    s = rep.summary()
    assert "[PASS]" in s and "[FAIL]" in s and "[SKIP]" in s
    assert "media errors" in s                            # message appended
    assert "1 pass, 1 fail, 1 skip  [FAIL]" in s


def test_report_ok_when_no_fail():
    rep = Report([Record("pcie", "x", "t", "pass"),
                      Record("pcie", "y", "t", "skip")])
    assert rep.ok is True and "[PASS]" in rep.summary()


# --- ResultStore ------------------------------------------------------------ #
def test_context_manager_opens_and_closes():
    with ResultStore(":memory:", dut_serial="SN1") as store:
        store.record(Record("pcie", "0000:03:00.0", "diagnose", "pass",
                                measured={"ber": 1e-13}, message="ok"))
        assert store.recent(10)[0]["measured"]["ber"] == 1e-13   # measured round-trips
    # __exit__ closed the connection.
    import sqlite3

    import pytest
    with pytest.raises(sqlite3.ProgrammingError):
        store.recent(1)


def test_record_serializes_non_finite_floats_as_null():
    # RFC 8259 Section 6 forbids Infinity/NaN as JSON numbers; a real-DUT BERT skip
    # produces ber_upper_bound=inf. record() must persist it as null so the stored
    # text is strict-JSON parseable (the dashboard browser / Go / jq reject 'Infinity').
    import json
    import math

    store = ResultStore(":memory:")
    store.record(Record("pcie", "0000:03:00.0", "diagnose", "skip",
                        measured={"ber_upper_bound": float("inf"),
                                  "nested": {"x": float("nan"), "ok": 1.5},
                                  "lst": [float("-inf"), 2]}))
    # The raw stored text must contain no Infinity/NaN tokens and must reparse strictly.
    raw = store.conn.execute("SELECT measured FROM results").fetchone()[0]
    assert "Infinity" not in raw and "NaN" not in raw
    strict = json.loads(raw, parse_constant=lambda c: (_ for _ in ()).throw(
        ValueError(c)))                                    # raises on Infinity/NaN
    assert strict["ber_upper_bound"] is None
    assert strict["nested"]["x"] is None and strict["nested"]["ok"] == 1.5
    assert strict["lst"] == [None, 2]
    # And recent() (Python-side) still round-trips, finite values intact.
    m = store.recent(1)[0]["measured"]
    assert m["ber_upper_bound"] is None and math.isfinite(m["nested"]["ok"])
    store.close()


def test_summary_and_yield_by_test():
    store = ResultStore(":memory:")
    for st in ("pass", "pass", "fail"):
        store.record(Record("nvme", "/dev/nvme0", "smart", st))
    store.record(Record("gpu", "0", "health", "pass"))
    summ = store.summary()
    assert summ == {"total": 4, "passed": 3, "failed": 1, "skipped": 0, "yield": 0.75}
    rows = store.yield_by_test()
    # Ordered by ascending yield: nvme/smart (2/3) before gpu/health (1/1).
    nvme = next(r for r in rows if r["subsystem"] == "nvme")
    assert nvme["pass"] == 2 and nvme["n"] == 3 and nvme["yield"] == 0.6667
    assert rows[0]["subsystem"] == "nvme"                 # worst yield first
    store.close()


def test_summary_empty_store_zero_yield():
    store = ResultStore(":memory:")
    assert store.summary() == {"total": 0, "passed": 0, "failed": 0,
                               "skipped": 0, "yield": 0.0}
    store.close()


def test_heartbeat_upsert_and_stations():
    store = ResultStore(":memory:", station="bench-7")
    store.heartbeat("running", "executing")
    store.heartbeat("idle", "PASS")                       # upsert: same station row updated
    rows = store.stations()
    assert len(rows) == 1
    row = rows[0]
    assert row["station"] == "bench-7" and row["status"] == "idle"
    assert row["detail"] == "PASS" and row["age_s"] >= 0
    store.close()
