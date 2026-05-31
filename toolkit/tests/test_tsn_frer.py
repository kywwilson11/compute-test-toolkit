"""Sprint 4.2.7: 802.1CB Frame Replication and Elimination for Reliability."""
from __future__ import annotations

import pytest

from computetest.tsn import FrerHealth, SequenceRecovery, check_frer


class TestSequenceRecovery:
    def test_accepts_new_eliminates_duplicate(self):
        rec = SequenceRecovery()
        assert rec.receive(0)
        assert not rec.receive(0)                         # duplicate -> eliminate
        assert rec.receive(1)

    def test_eliminates_stale_outside_window(self):
        rec = SequenceRecovery(history_len=4)
        for s in [10, 11, 12, 13, 14]:
            assert rec.receive(s)
        assert not rec.receive(9)                         # 9 <= 14-4 -> stale

    def test_bad_history_len_rejected(self):
        with pytest.raises(ValueError, match="history_len"):
            SequenceRecovery(history_len=0)

    def test_bad_seq_width_rejected(self):
        with pytest.raises(ValueError, match="seq_width"):
            SequenceRecovery(seq_width=0)

    def test_wrap_around_not_treated_as_stale(self):
        # 16-bit R-TAG rolls over: 0 after 65535 is the NEXT frame, not stale.
        rec = SequenceRecovery(history_len=8)
        assert rec.receive(65534) and rec.receive(65535)
        assert rec.receive(0) and rec.receive(1)          # wrapped -> accepted
        assert not rec.receive(65535)                     # still in window -> duplicate


class TestCheckFrer:
    def test_clean_replication_delivers_each_once(self):
        # Both paths deliver everything, interleaved.
        arrivals = [0, 0, 1, 1, 2, 2, 3, 3]
        h = check_frer(sent_seqs=[0, 1, 2, 3], arrivals=arrivals)
        assert isinstance(h, FrerHealth) and h.ok
        assert h.delivered == [0, 1, 2, 3]

    def test_survives_mid_stream_path_failure(self):
        # Path A delivers 0,1 then fails; path B delivers everything.
        arrivals = [0, 1, 0, 1, 2, 3]
        h = check_frer(sent_seqs=[0, 1, 2, 3], arrivals=arrivals)
        assert h.ok                                       # fail-operational, exactly-once

    def test_reorder_within_window_ok(self):
        h = check_frer(sent_seqs=[0, 1, 2, 3], arrivals=[0, 2, 1, 3])
        assert h.ok

    def test_total_loss_fails(self):
        # Sequence 2 lost on both paths.
        h = check_frer(sent_seqs=[0, 1, 2, 3], arrivals=[0, 1, 3])
        assert not h.ok and h.checks["no_loss"] is False

    def test_wrapping_replicated_stream_exactly_once(self):
        # Replicated copies across the 65535 -> 0 rollover; exactly-once delivery.
        sent = [65534, 65535, 0, 1]
        arrivals = [65534, 65534, 65535, 65535, 0, 0, 1, 1]
        h = check_frer(sent_seqs=sent, arrivals=arrivals)
        assert h.ok and sorted(h.delivered) == [0, 1, 65534, 65535]

    def test_summary_and_to_dict(self):
        h = check_frer(sent_seqs=[0, 1], arrivals=[0, 0, 1])
        assert "FRER" in h.summary()
        assert h.to_dict()["n_delivered"] == 2
