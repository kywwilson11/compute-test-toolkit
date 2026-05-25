"""
Results store: a tiny SQLite-backed record of every test (status + the *measured
values*, not just pass/fail — that's what makes SPC, limit-setting, and yield
analysis possible later; see Guide A, §"Capture parameters"). Also a heartbeat
table the dashboard uses to show which stations are alive.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    station TEXT, dut_serial TEXT, program_version TEXT,
    subsystem TEXT, target TEXT, test_name TEXT, status TEXT,
    measured TEXT, message TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_ts ON results(ts);
CREATE TABLE IF NOT EXISTS heartbeats (
    station TEXT PRIMARY KEY, ts REAL, status TEXT, detail TEXT
);
"""


@dataclass
class TestRecord:
    subsystem: str          # "pcie" | "nvme" | "gpu" | "gmsl" | "ethernet" | "can" | "enum"
    target: str             # bdf / device / iface
    test_name: str
    status: str             # "pass" | "fail" | "skip"
    measured: dict = field(default_factory=dict)
    message: str = ""


class ResultStore:
    def __init__(self, path: str = ":memory:", *, station: str = "station-1",
                 dut_serial: str = "UNKNOWN", program_version: str = "0.1.0"):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)
        self.station, self.dut_serial, self.program_version = station, dut_serial, program_version

    def __enter__(self) -> "ResultStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def record(self, rec: TestRecord) -> None:
        self.conn.execute(
            "INSERT INTO results (ts, station, dut_serial, program_version, subsystem, "
            "target, test_name, status, measured, message) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (time.time(), self.station, self.dut_serial, self.program_version,
             rec.subsystem, rec.target, rec.test_name, rec.status,
             json.dumps(rec.measured), rec.message))
        self.conn.commit()

    def heartbeat(self, status: str = "idle", detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO heartbeats (station, ts, status, detail) VALUES (?,?,?,?) "
            "ON CONFLICT(station) DO UPDATE SET ts=excluded.ts, status=excluded.status, "
            "detail=excluded.detail",
            (self.station, time.time(), status, detail))
        self.conn.commit()

    def recent(self, limit: int = 100) -> list[dict]:
        cur = self.conn.execute(
            "SELECT ts, station, dut_serial, subsystem, target, test_name, status, "
            "measured, message FROM results ORDER BY id DESC LIMIT ?", (limit,))
        cols = [c[0] for c in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        for r in rows:
            r["measured"] = json.loads(r["measured"] or "{}")
        return rows

    def summary(self) -> dict:
        cur = self.conn.execute("SELECT status, COUNT(*) FROM results GROUP BY status")
        counts = dict(cur.fetchall())
        total = sum(counts.values())
        passed = counts.get("pass", 0)
        return {"total": total, "passed": passed, "failed": counts.get("fail", 0),
                "skipped": counts.get("skip", 0),
                "yield": round(passed / total, 4) if total else 0.0}

    def yield_by_test(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT subsystem, test_name, "
            "SUM(status='pass') AS p, COUNT(*) AS n "
            "FROM results GROUP BY subsystem, test_name ORDER BY (1.0*p/n)")
        return [{"subsystem": s, "test": t, "pass": p, "n": n,
                 "yield": round(p / n, 4) if n else 0.0}
                for s, t, p, n in cur.fetchall()]

    def stations(self) -> list[dict]:
        cur = self.conn.execute("SELECT station, ts, status, detail FROM heartbeats")
        now = time.time()
        return [{"station": s, "age_s": round(now - ts, 1), "status": st, "detail": d}
                for s, ts, st, d in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()
