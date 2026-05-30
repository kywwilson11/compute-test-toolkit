"""
802.1CB Frame Replication and Elimination for Reliability (FRER) (Sprint 4.2).

Models the sequence-recovery function: each R-TAG sequence number is accepted
exactly once within a recovery window, eliminating the replicated copies that
arrive over the redundant path. ``check_frer`` runs a stream of arrivals (which
models replication, loss, duplication, reorder, and a mid-stream path failure)
through the recovery function and verifies fail-operational exactly-once
delivery — the platform's own redundancy validation, not an attack.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


class SequenceRecovery:
    """802.1CB vector/sequence recovery: accept each sequence number once within
    a history window; eliminate duplicates and stale (out-of-window) frames."""

    def __init__(self, history_len: int = 32) -> None:
        if history_len < 1:
            raise ValueError("history_len must be >= 1")
        self.history_len = history_len
        self._seen: set[int] = set()
        self._max: int | None = None

    def receive(self, seq: int) -> bool:
        """Return True to accept (pass), False to eliminate (a duplicate or a
        stale frame outside the recovery window)."""
        if seq in self._seen:
            return False                                  # duplicate -> eliminate
        if self._max is not None and seq <= self._max - self.history_len:
            return False                                  # stale -> eliminate
        self._seen.add(seq)
        self._max = seq if self._max is None else max(self._max, seq)
        lo = self._max - self.history_len
        self._seen = {s for s in self._seen if s > lo}    # prune below the window
        return True


@dataclass
class FrerHealth:
    """FRER exactly-once-delivery verdict for one replicated stream."""
    sent_seqs: list[int]
    delivered: list[int]
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"FRER {len(self.delivered)}/{len(self.sent_seqs)} delivered "
                f"-> {state}")

    def to_dict(self) -> dict:
        return {"n_sent": len(self.sent_seqs), "n_delivered": len(self.delivered),
                "checks": self.checks, "ok": self.ok}


def check_frer(*, sent_seqs: Sequence[int], arrivals: Sequence[int],
               history_len: int = 32) -> FrerHealth:
    """Run a stream of replicated arrivals through sequence recovery and verify
    fail-operational exactly-once delivery: no loss, no delivered duplicates,
    and the delivered set equals the distinct sent set."""
    rec = SequenceRecovery(history_len)
    delivered = [seq for seq in arrivals if rec.receive(seq)]
    distinct_sent = sorted(set(sent_seqs))
    checks = {
        "no_loss": set(distinct_sent) <= set(delivered),
        "no_duplicates_delivered": len(delivered) == len(set(delivered)),
        "exactly_once": sorted(delivered) == distinct_sent,
    }
    return FrerHealth(sent_seqs=distinct_sent, delivered=delivered, checks=checks)
