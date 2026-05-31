"""Sprint 4.2.6: 802.1Qbu + 802.3br frame preemption (UNH-IOL Clause 99)."""
from __future__ import annotations

import pytest

from computetest.tsn import (
    Fragment,
    PreemptionHealth,
    check_fragmentation,
    check_preemption,
    min_fragment_bytes,
    run_verify_respond,
    verify_time_valid,
)


class TestAddFragSize:
    def test_mapping(self):
        assert min_fragment_bytes(0) == 64 and min_fragment_bytes(3) == 256

    def test_invalid_rejected(self):
        with pytest.raises(ValueError, match="0-3"):
            min_fragment_bytes(4)


class TestVerifyTime:
    def test_range(self):
        assert verify_time_valid(1) and verify_time_valid(128)
        assert not verify_time_valid(0) and not verify_time_valid(129)


class TestVerifyRespond:
    def test_success(self):
        assert run_verify_respond(10.0, respond_delay_ms=5.0, got_respond=True)

    def test_no_respond_fails(self):
        assert not run_verify_respond(10.0, respond_delay_ms=5.0, got_respond=False)

    def test_late_respond_fails(self):
        # Respond after the verifyTime window expires -> fail.
        assert not run_verify_respond(10.0, respond_delay_ms=11.0, got_respond=True)

    def test_respond_within_full_window_passes(self):
        # Anywhere within the verifyTime window is valid (not just 0.8x of it):
        # 9 ms < 10 ms passes. (FAILed under the old 0.8x rule.)
        assert run_verify_respond(10.0, respond_delay_ms=9.0, got_respond=True)

    def test_negative_respond_delay_fails(self):
        assert not run_verify_respond(10.0, respond_delay_ms=-1.0, got_respond=True)

    def test_invalid_verify_time_fails(self):
        assert not run_verify_respond(200.0, respond_delay_ms=1.0, got_respond=True)


class TestFragmentation:
    def _frags(self):
        return [Fragment("SMD-S0", 128), Fragment("SMD-C1", 128), Fragment("SMD-C2", 40)]

    def test_valid(self):
        ok, notes = check_fragmentation(self._frags(), add_frag_size=1)  # floor 128
        assert ok and notes == []

    def test_below_floor(self):
        frags = [Fragment("SMD-S0", 64), Fragment("SMD-C1", 200)]   # first < 128 floor
        ok, notes = check_fragmentation(frags, add_frag_size=1)
        assert not ok and any("floor" in n for n in notes)

    def test_first_not_start(self):
        ok, notes = check_fragmentation([Fragment("SMD-C1", 200)], add_frag_size=0)
        assert not ok and any("not SMD-S" in n for n in notes)

    def test_multiple_starts(self):
        frags = [Fragment("SMD-S0", 128), Fragment("SMD-S1", 128)]
        ok, notes = check_fragmentation(frags, add_frag_size=0)
        assert not ok and any("multiple SMD-S" in n for n in notes)

    def test_mcrc_failure(self):
        frags = [Fragment("SMD-S0", 128), Fragment("SMD-C1", 40, mcrc_ok=False)]
        ok, notes = check_fragmentation(frags, add_frag_size=0)
        assert not ok and any("mCRC" in n for n in notes)

    def test_empty(self):
        ok, notes = check_fragmentation([], add_frag_size=0)
        assert not ok and notes == ["no fragments"]

    def test_single_fragment_is_not_fragmented(self):
        ok, notes = check_fragmentation([Fragment("SMD-S0", 128)], add_frag_size=0)
        assert not ok and any(">= 2 fragments" in n for n in notes)


class TestCheckPreemption:
    def test_conformant(self):
        h = check_preemption(verify_time_ms=10.0, respond_delay_ms=5.0,
                             got_respond=True,
                             fragments=[Fragment("SMD-S0", 64), Fragment("SMD-C1", 40)],
                             add_frag_size=0)
        assert isinstance(h, PreemptionHealth) and h.ok

    def test_handshake_failure_fails(self):
        # Respond past the verifyTime window -> handshake fails (2 valid fragments
        # so the failure is unambiguously the handshake).
        h = check_preemption(verify_time_ms=10.0, respond_delay_ms=11.0,
                             got_respond=True,
                             fragments=[Fragment("SMD-S0", 64), Fragment("SMD-C1", 40)],
                             add_frag_size=0)
        assert not h.ok and h.checks["verify_respond_handshake"] is False

    def test_summary_and_to_dict(self):
        h = check_preemption(verify_time_ms=10.0, respond_delay_ms=5.0,
                             got_respond=True, fragments=[Fragment("SMD-S0", 64)],
                             add_frag_size=0)
        assert "Qbu preemption" in h.summary()
        assert "checks" in h.to_dict()
