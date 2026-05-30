"""
802.1Qbu + 802.3br frame preemption (UNH-IOL Clause 99) (Sprint 4.2).

The frame-preemption mechanism behind the UNH-IOL Clause-99 conformance groups
(reception / rejection / transmission / Verify-Tx / Respond / AEC-TLV /
prioritization): the Verify/Respond handshake (verifyTime 1-128 ms, Respond
within 0.8x the window), the addFragSize -> minimum-fragment-size mapping
(0-3 -> 64/128/192/256 B), and fragment SMD sequencing + mCRC validity. The
Clause-99 test-ID list (99.1.1-99.7.1) is public and enumerates straight into
the coverage matrix (Sprint 4.2.12).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# addFragSize (0-3) -> minimum non-final fragment payload in bytes (802.3br).
ADD_FRAG_SIZE_BYTES = {0: 64, 1: 128, 2: 192, 3: 256}
VERIFY_TIME_MIN_MS = 1
VERIFY_TIME_MAX_MS = 128
RESPOND_WINDOW_FRACTION = 0.8


def min_fragment_bytes(add_frag_size: int) -> int:
    """Minimum non-final fragment payload for an addFragSize (0-3)."""
    if add_frag_size not in ADD_FRAG_SIZE_BYTES:
        raise ValueError(f"addFragSize must be 0-3; got {add_frag_size}")
    return ADD_FRAG_SIZE_BYTES[add_frag_size]


def verify_time_valid(ms: float) -> bool:
    """True iff verifyTime is within the 802.1Qbu 1-128 ms range."""
    return VERIFY_TIME_MIN_MS <= ms <= VERIFY_TIME_MAX_MS


@dataclass
class Fragment:
    """One mPacket fragment: its SMD delimiter, payload size, and mCRC validity."""
    smd: str                    # e.g. "SMD-S0" (start) / "SMD-C1" (continuation)
    payload_bytes: int
    mcrc_ok: bool = True

    @property
    def is_start(self) -> bool:
        return self.smd.startswith("SMD-S")

    @property
    def is_continuation(self) -> bool:
        return self.smd.startswith("SMD-C")


@dataclass
class PreemptionHealth:
    """Frame-preemption conformance verdict."""
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"Qbu preemption -> {state}"

    def to_dict(self) -> dict:
        return {"checks": self.checks, "notes": list(self.notes), "ok": self.ok}


def run_verify_respond(verify_time_ms: float, *, respond_delay_ms: float,
                       got_respond: bool) -> bool:
    """The Verify/Respond handshake succeeds iff verifyTime is valid, a Respond
    (SMD-R) arrived, and it arrived within ``RESPOND_WINDOW_FRACTION`` of the
    verifyTime window."""
    if not verify_time_valid(verify_time_ms):
        return False
    if not got_respond:
        return False
    return respond_delay_ms <= RESPOND_WINDOW_FRACTION * verify_time_ms


def check_fragmentation(fragments: Sequence[Fragment],
                        add_frag_size: int) -> tuple[bool, list[str]]:
    """Validate a fragmented frame: a single SMD-S start then SMD-C
    continuations, every non-final fragment >= the addFragSize minimum, and all
    mCRCs valid. Returns ``(ok, notes)``."""
    notes: list[str] = []
    if not fragments:
        return False, ["no fragments"]
    floor = min_fragment_bytes(add_frag_size)
    if not fragments[0].is_start:
        notes.append("first fragment is not SMD-S")
    if any(f.is_start for f in fragments[1:]):
        notes.append("multiple SMD-S start delimiters")
    if not all(f.is_continuation for f in fragments[1:]):
        notes.append("non-continuation fragment after start")
    for f in fragments[:-1]:                       # every fragment but the last
        if f.payload_bytes < floor:
            notes.append(f"fragment {f.smd} below {floor}B floor")
    if not all(f.mcrc_ok for f in fragments):
        notes.append("mCRC failure")
    return (not notes), notes


def check_preemption(*, verify_time_ms: float, respond_delay_ms: float,
                     got_respond: bool, fragments: Sequence[Fragment],
                     add_frag_size: int) -> PreemptionHealth:
    """Aggregate the preemption conformance checks: the Verify/Respond handshake
    + the fragmentation rules."""
    handshake = run_verify_respond(verify_time_ms,
                                   respond_delay_ms=respond_delay_ms,
                                   got_respond=got_respond)
    frag_ok, notes = check_fragmentation(fragments, add_frag_size)
    checks = {
        "verify_time_valid": verify_time_valid(verify_time_ms),
        "verify_respond_handshake": handshake,
        "fragmentation_valid": frag_ok,
    }
    return PreemptionHealth(checks=checks, notes=notes)
