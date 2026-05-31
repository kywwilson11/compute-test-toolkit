"""
CXL HDM decoder programming + interleave check (Sprint 4.4).

Verifies a Host-managed Device Memory (HDM) decoder: the committed interleave
ways + granularity and the HPA->DPA translation for an interleaved region. Also
provides ``calc_interleave_pos`` — the kernel ``cxl_calc_interleave_pos`` helper
(``pos = pos*parent_ways + parent_pos``) that folds an endpoint->root chain
(endpoint level first, ascending to the root decoder, matching the kernel loop)
into the endpoint's interleave position for nested (switch/MLD) topologies.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


def calc_interleave_pos(chain: Sequence[tuple[int, int]]) -> int:
    """Fold an endpoint->root chain of ``(interleave_position, interleave_ways)``
    -- endpoint level first, then each parent up to the root -- into the
    endpoint's interleave position (kernel ``cxl_calc_interleave_pos`` iterates
    endpoint->root, so the endpoint level is the most-significant digit)."""
    pos = 0
    for parent_pos, parent_ways in chain:
        pos = pos * parent_ways + parent_pos
    return pos


@dataclass
class HdmDecoder:
    """A committed HDM decoder: an HPA window interleaved ``interleave_ways`` ways
    at ``interleave_granularity`` bytes, mapping to DPA from ``dpa_base``."""
    interleave_ways: int
    interleave_granularity: int          # bytes
    base_hpa: int
    size: int
    dpa_base: int = 0

    def hpa_to_dpa(self, hpa: int, position: int) -> int | None:
        """Translate an HPA to this endpoint's DPA, or ``None`` if the HPA is
        outside the window or handled by a different interleave position."""
        if not (self.base_hpa <= hpa < self.base_hpa + self.size):
            return None
        offset = hpa - self.base_hpa
        chunk = offset // self.interleave_granularity
        if chunk % self.interleave_ways != position:
            return None
        dpa_chunk = chunk // self.interleave_ways
        return (self.dpa_base + dpa_chunk * self.interleave_granularity
                + (offset % self.interleave_granularity))


@dataclass
class HdmHealth:
    """HDM decoder programming verdict."""
    interleave_ways: int
    interleave_granularity: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"CXL HDM iw={self.interleave_ways} "
                f"ig={self.interleave_granularity}B -> {state}")

    def to_dict(self) -> dict:
        return {"interleave_ways": self.interleave_ways,
                "interleave_granularity": self.interleave_granularity,
                "checks": self.checks, "ok": self.ok}


def check_hdm_decoder(decoder: HdmDecoder, *, expect_ways: int,
                      expect_granularity: int, committed: bool = True) -> HdmHealth:
    """Verify a committed HDM decoder's interleave ways + granularity."""
    checks = {
        "committed": committed,
        "interleave_ways_ok": decoder.interleave_ways == expect_ways,
        "interleave_granularity_ok": (decoder.interleave_granularity
                                      == expect_granularity),
    }
    return HdmHealth(interleave_ways=decoder.interleave_ways,
                     interleave_granularity=decoder.interleave_granularity,
                     checks=checks)
