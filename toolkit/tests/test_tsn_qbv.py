"""Sprint 4.2.5: 802.1Qbv time-aware-shaper gate-timing check."""
from __future__ import annotations

import json
from io import StringIO

import pytest

from computetest.io.ocpdiag import Emitter
from computetest.tsn import (
    GclEntry,
    GclSchedule,
    QbvHealth,
    check_qbv_gate_timing,
    emit_qbv,
)

GBPS = 1e9


def _sched() -> GclSchedule:
    # TC6 open for 100 us, then TC0..5 open for 100 us (200 us cycle).
    return GclSchedule([GclEntry(gate_states=1 << 6, duration_ns=100_000),
                        GclEntry(gate_states=0b0011_1111, duration_ns=100_000)])


class TestGcl:
    def test_cycle_and_gate_open(self):
        s = _sched()
        assert s.cycle_ns == 200_000
        assert s.gate_open_at(10_000, 6)          # TC6 open in first window
        assert not s.gate_open_at(10_000, 0)      # TC0 closed there
        assert s.gate_open_at(150_000, 0)         # TC0 open in second window
        assert not s.gate_open_at(150_000, 6)

    def test_wraps_modulo_cycle(self):
        s = _sched()
        assert s.gate_open_at(210_000, 6)         # 210us % 200us = 10us -> TC6 window

    def test_zero_cycle_raises(self):
        with pytest.raises(ValueError, match="zero cycle"):
            GclSchedule([]).gate_open_at(0, 0)


class TestQbvCheck:
    def test_scheduled_frame_in_window_passes(self):
        # TC6 frame, 1000 bits @ 1 Gbps = 1000 ns, well within the TC6 window.
        h = check_qbv_gate_timing(_sched(), [(6, 10_000, 1000)], link_rate_bps=GBPS)
        assert isinstance(h, QbvHealth) and h.ok

    def test_gate_closed_violation(self):
        h = check_qbv_gate_timing(_sched(), [(0, 10_000, 1000)], link_rate_bps=GBPS)
        assert not h.ok and h.checks["frames_in_open_window"] is False

    def test_guard_band_violation(self):
        # TC6 frame starting 500 ns before close but needing 1000 ns to send.
        h = check_qbv_gate_timing(_sched(), [(6, 99_500, 1000)], link_rate_bps=GBPS)
        assert not h.ok and h.checks["guard_band_respected"] is False

    def test_gate_open_across_consecutive_entries_no_guard_violation(self):
        # TC6 gate open across entries 0 AND 1 (0..100us), closed in entry 2.
        sched = GclSchedule([GclEntry(1 << 6, 50_000), GclEntry(1 << 6, 50_000),
                             GclEntry(0b0011_1111, 100_000)])
        # 20_000 bits @ 1 Gbps = 20_000 ns: 40_000 -> 60_000 spans the entry0/1
        # boundary where the gate stays open. Legal (FAILs under the old per-entry check).
        h = check_qbv_gate_timing(sched, [(6, 40_000, 20_000)], link_rate_bps=GBPS)
        assert h.ok and h.checks["guard_band_respected"] is True

    def test_always_open_gate_no_guard_violation(self):
        # TC6 open in every entry -> the gate never closes within a cycle.
        sched = GclSchedule([GclEntry(1 << 6, 100_000), GclEntry(1 << 6, 100_000)])
        h = check_qbv_gate_timing(sched, [(6, 150_000, 50_000)], link_rate_bps=GBPS)
        assert h.ok and h.checks["guard_band_respected"] is True

    def test_edge_jitter_budget(self):
        ok = check_qbv_gate_timing(_sched(), [], link_rate_bps=GBPS,
                                   edge_jitter_ns=[10.0, 20.0])
        assert ok.checks["edge_jitter_ok"] is True
        bad = check_qbv_gate_timing(_sched(), [], link_rate_bps=GBPS,
                                    edge_jitter_ns=[10.0, 90.0])
        assert bad.checks["edge_jitter_ok"] is False

    def test_edge_jitter_bounds_negative_excursions(self):
        # A large EARLY (negative) gate transition must fail, not slip through max().
        h = check_qbv_gate_timing(_sched(), [], link_rate_bps=GBPS,
                                  edge_jitter_ns=[10.0, -90.0])
        assert h.checks["edge_jitter_ok"] is False

    def test_summary_and_to_dict(self):
        h = check_qbv_gate_timing(_sched(), [(0, 10_000, 1000)], link_rate_bps=GBPS)
        assert "Qbv gates" in h.summary()
        assert h.to_dict()["gate_violations"] == 1


class TestEmit:
    def test_emit_jitter_series_and_diagnosis(self):
        h = check_qbv_gate_timing(_sched(), [(6, 10_000, 1000)], link_rate_bps=GBPS,
                                  edge_jitter_ns=[10.0, 20.0])
        buf = StringIO()
        emit_qbv(Emitter(buf, clock=lambda: "T"), h)
        arts = [json.loads(li)["testStepArtifact"]
                for li in buf.getvalue().splitlines()
                if "testStepArtifact" in json.loads(li)]
        kinds = [k for a in arts for k in a if k != "testStepId"]
        assert "measurementSeriesStart" in kinds
        diag = next(a["diagnosis"] for a in arts if "diagnosis" in a)
        assert diag["type"] == "PASS"

    def test_emit_without_jitter_samples(self):
        h = check_qbv_gate_timing(_sched(), [(0, 10_000, 1000)], link_rate_bps=GBPS)
        buf = StringIO()
        emit_qbv(Emitter(buf, clock=lambda: "T"), h)
        arts = [json.loads(li)["testStepArtifact"]
                for li in buf.getvalue().splitlines()
                if "testStepArtifact" in json.loads(li)]
        kinds = [k for a in arts for k in a if k != "testStepId"]
        assert "measurementSeriesStart" not in kinds   # no jitter -> no series
        diag = next(a["diagnosis"] for a in arts if "diagnosis" in a)
        assert diag["type"] == "FAIL"                   # gate-closed frame
