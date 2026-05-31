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
    a history window; eliminate duplicates and stale (out-of-window) frames.

    The R-TAG SequenceNumber is a ``seq_width``-bit field (default 16), so the
    window/staleness comparisons use signed modular distance (mod 2**seq_width)
    and wrap correctly across the 2**seq_width - 1 -> 0 rollover, instead of
    misreading a wrapped frame (e.g. 0 after 65535) as stale."""

    def __init__(self, history_len: int = 32, *, seq_width: int = 16) -> None:
        if history_len < 1:
            raise ValueError("history_len must be >= 1")
        if seq_width < 1:
            raise ValueError("seq_width must be >= 1")
        self.history_len = history_len
        self._mod = 1 << seq_width
        self._mask = self._mod - 1
        self._seen: set[int] = set()
        self._max: int | None = None

    def _ahead(self, a: int, b: int) -> int:
        """Signed modular distance: how far ``a`` is ahead of ``b`` (negative =
        behind ``b``), in [-mod/2, mod/2). Wraps across the R-TAG rollover."""
        return ((a - b + self._mod // 2) % self._mod) - self._mod // 2

    def receive(self, seq: int) -> bool:
        """Return True to accept (pass), False to eliminate (a duplicate or a
        stale frame behind the recovery window)."""
        seq &= self._mask
        if seq in self._seen:
            return False                                  # duplicate -> eliminate
        if self._max is not None and self._ahead(seq, self._max) <= -self.history_len:
            return False                                  # stale (behind window) -> eliminate
        self._seen.add(seq)
        if self._max is None or self._ahead(seq, self._max) > 0:
            self._max = seq                               # advance high-water (modular)
        self._seen = {s for s in self._seen                # prune entries behind the window
                      if self._ahead(s, self._max) > -self.history_len}
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
               history_len: int = 32, seq_width: int = 16) -> FrerHealth:
    """Run a stream of replicated arrivals through sequence recovery and verify
    fail-operational exactly-once delivery: no loss, and the delivered set equals
    the distinct sent set (which also implies no delivered duplicates — a repeat
    in ``delivered`` makes ``exactly_once`` false)."""
    rec = SequenceRecovery(history_len, seq_width=seq_width)
    delivered = [seq for seq in arrivals if rec.receive(seq)]
    distinct_sent = sorted(set(sent_seqs))
    checks = {
        "no_loss": set(distinct_sent) <= set(delivered),
        "exactly_once": sorted(delivered) == distinct_sent,
    }
    return FrerHealth(sent_seqs=distinct_sent, delivered=delivered, checks=checks)
