"""
802.1Qbv time-aware-shaper (TAS) gate-timing check (Sprint 4.2).

Verifies scheduled traffic against a Gate Control List (GCL): a frame must
egress while its traffic-class gate is open, and the guard band must suppress a
frame that can't finish before the gate closes. Window-edge jitter (the spread
between scheduled and actual gate transitions) is emitted as a measurementSeries
with a jitter <= limit validator.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..io.ocpdiag import LESS_THAN_OR_EQUAL, Emitter, validator

DEFAULT_MAX_EDGE_JITTER_NS = 50.0


@dataclass
class GclEntry:
    """One Gate Control List entry: a gate-state bitmask (bit i = traffic class i
    gate open) held for ``duration_ns``."""
    gate_states: int
    duration_ns: int


@dataclass
class GclSchedule:
    """A cyclic Gate Control List."""
    entries: list[GclEntry]

    @property
    def cycle_ns(self) -> int:
        return sum(e.duration_ns for e in self.entries)

    def window_at(self, t_ns: float) -> tuple[int, int, int]:
        """Return (entry_index, window_start_ns, window_end_ns) for the entry
        covering ``t_ns`` (modulo the cycle)."""
        cycle = self.cycle_ns
        if cycle <= 0:
            raise ValueError("GCL has zero cycle time")
        t = t_ns % cycle
        acc = 0
        for i, e in enumerate(self.entries):
            if acc <= t < acc + e.duration_ns:
                return i, acc, acc + e.duration_ns
            acc += e.duration_ns
        raise AssertionError("unreachable: t within cycle")  # pragma: no cover

    def gate_open_at(self, t_ns: float, tc: int) -> bool:
        """True iff traffic class ``tc``'s gate is open at ``t_ns``."""
        i, _, _ = self.window_at(t_ns)
        return bool(self.entries[i].gate_states & (1 << tc))

    def gate_close_after(self, t_ns: float, tc: int) -> float:
        """Absolute time >= ``t_ns`` at which ``tc``'s gate next closes, assuming
        it is open at ``t_ns``. Walks the contiguous run of consecutive entries
        whose ``tc`` gate stays open (across the cycle boundary), so a gate held
        open over several GCL entries is one open interval — not one per entry.
        Returns a full cycle ahead if ``tc`` is open for the entire cycle."""
        i, _, w_end = self.window_at(t_ns)              # raises on zero cycle
        cycle = self.cycle_ns
        close = t_ns - (t_ns % cycle) + w_end           # absolute end of entry i
        n = len(self.entries)
        for step in range(1, n):
            nxt = (i + step) % n
            if not (self.entries[nxt].gate_states & (1 << tc)):
                return close                            # gate closes at this boundary
            close += self.entries[nxt].duration_ns
        return close                                    # tc open across the whole cycle


@dataclass
class QbvHealth:
    """802.1Qbv gate-timing verdict for a set of scheduled-frame egress events."""
    n_frames: int
    gate_violations: list[tuple[int, float]]
    guard_violations: list[tuple[int, float]]
    edge_jitter_ns: list[float]
    max_jitter_ns: float
    jitter_limit_ns: float
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"Qbv gates: {self.n_frames} frames, {len(self.gate_violations)} "
                f"gate + {len(self.guard_violations)} guard-band violations -> {state}")

    def to_dict(self) -> dict:
        return {"n_frames": self.n_frames,
                "gate_violations": len(self.gate_violations),
                "guard_violations": len(self.guard_violations),
                "max_jitter_ns": self.max_jitter_ns,
                "jitter_limit_ns": self.jitter_limit_ns,
                "n_jitter_samples": len(self.edge_jitter_ns),
                "checks": self.checks, "ok": self.ok}


def check_qbv_gate_timing(schedule: GclSchedule,
                          frames: Sequence[tuple[int, float, int]], *,
                          link_rate_bps: float,
                          edge_jitter_ns: Sequence[float] = (),
                          max_jitter_ns: float = DEFAULT_MAX_EDGE_JITTER_NS
                          ) -> QbvHealth:
    """Check scheduled-frame egress events against a GCL.

    ``frames`` are ``(traffic_class, start_ns, length_bits)``. A frame whose gate
    is closed at ``start`` is a gate violation; a frame that can't finish before
    its open window closes is a guard-band violation (the guard band exists to
    suppress it). ``edge_jitter_ns`` are per-transition window-edge jitter samples.
    """
    gate_violations: list[tuple[int, float]] = []
    guard_violations: list[tuple[int, float]] = []
    for tc, start, length_bits in frames:
        if not schedule.gate_open_at(start, tc):
            gate_violations.append((tc, start))
            continue
        tx_ns = length_bits / link_rate_bps * 1e9
        # Compare against when the gate actually closes for this TC — the end of
        # the contiguous open run, not just the entry covering ``start`` (a gate
        # held open across adjacent GCL entries is one window).
        if start + tx_ns > schedule.gate_close_after(start, tc):
            guard_violations.append((tc, start))
    # Bound BOTH early (negative) and late (positive) window-edge excursions; a
    # raw max() of signed samples lets a large early transition slip through.
    max_jitter = max((abs(j) for j in edge_jitter_ns), default=0.0)
    checks = {
        "frames_in_open_window": not gate_violations,
        "guard_band_respected": not guard_violations,
        "edge_jitter_ok": max_jitter <= max_jitter_ns,
    }
    return QbvHealth(n_frames=len(frames), gate_violations=gate_violations,
                     guard_violations=guard_violations,
                     edge_jitter_ns=list(edge_jitter_ns), max_jitter_ns=max_jitter,
                     jitter_limit_ns=max_jitter_ns, checks=checks)


def emit_qbv(em: Emitter, health: QbvHealth, *,
             hardware_info_id: str | None = None) -> str:
    """Emit a Qbv verdict: window-edge jitter as a measurementSeries with a
    jitter <= limit validator, plus the violation counts."""
    sid = em.step_start("tsn.qbv.gate_timing")
    if health.edge_jitter_ns:
        series = em.series_start(
            name="tsn.qbv.edge_jitter", unit="ns",
            validators=[validator(LESS_THAN_OR_EQUAL, health.jitter_limit_ns,
                                  name="max_edge_jitter_ns")],
            hardware_info_id=hardware_info_id)
        for i, jit in enumerate(health.edge_jitter_ns):
            em.series_element(series_id=series, index=i, value=jit)
        em.series_end(series_id=series, total_count=len(health.edge_jitter_ns))
    em.measurement(name="gate_violations", value=len(health.gate_violations),
                   unit="count")
    em.measurement(name="guard_violations", value=len(health.guard_violations),
                   unit="count")
    status = "pass" if health.ok else "fail"
    em.diagnosis(verdict=f"tsn.qbv.{status}",
                 type_="PASS" if health.ok else "FAIL", message=health.summary())
    em.step_end(status, step_id=sid)
    return sid
