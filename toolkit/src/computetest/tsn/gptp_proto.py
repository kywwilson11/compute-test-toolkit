"""
802.1AS gPTP protocol conformance (Sprint 4.2).

The protocol-logic layer above the Max|TE| measurement: the peer-delay
4-timestamp exchange (mean link delay + turnaround), the correction-field
identity (upstream delay + residence time), neighborRateRatio / syntonization,
asCapable gating, and BMCA grandmaster election + failover. Pure functions over
representative message datasets, so they unit-test offline (a real run feeds
them from a linuxptp ``pmc`` dump or a Wireshark gPTP dissection).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

# neighborRateRatio must sit within +/-200 ppm of 1.0 for a syntonized link.
DEFAULT_RATE_RATIO_TOL = 200e-6
# Pdelay responder turnaround budget (ns); a slow responder breaks the delay calc.
DEFAULT_MAX_TURNAROUND_NS = 10e6
# Correction-field match tolerance (ns).
DEFAULT_CORRECTION_TOL_NS = 1.0


@dataclass
class PdelayExchange:
    """A peer-delay 4-timestamp exchange (ns): t1 req-tx, t2 req-rx (responder),
    t3 resp-tx (responder), t4 resp-rx (requestor)."""
    t1: float
    t2: float
    t3: float
    t4: float
    rate_ratio: float = 1.0

    @property
    def mean_link_delay_ns(self) -> float:
        """((t4 - t1) - rateRatio*(t3 - t2)) / 2 — the 802.1AS mean link delay.

        neighborRateRatio scales the responder turnaround (t3 - t2) into the
        requestor's timebase; it is NOT applied to the (t4 - t1) round-trip. This
        matches linuxptp tsproc: delay = ((t2 - t3)*rr + (t4 - t1)) / 2."""
        return ((self.t4 - self.t1) - self.rate_ratio * (self.t3 - self.t2)) / 2.0

    @property
    def turnaround_ns(self) -> float:
        """Responder turnaround (t3 - t2)."""
        return self.t3 - self.t2


@dataclass
class AnnounceMsg:
    """The gPTP Announce dataset used for BMCA comparison."""
    source: str
    priority1: int
    clock_class: int
    clock_accuracy: int
    priority2: int
    clock_identity: int


def bmca_elect(announces: Sequence[AnnounceMsg]) -> AnnounceMsg | None:
    """IEEE 1588 dataset comparison: the best master is the lexicographically
    smallest (priority1, clockClass, clockAccuracy, priority2, clockIdentity).
    Returns ``None`` for an empty set (no master visible).

    Simplification: the full 1588/802.1AS comparison also includes
    offsetScaledLogVariance (between clockAccuracy and priority2); it is omitted
    here, so two clocks differing only in variance fall through to priority2 /
    clockIdentity. clockIdentity is unique, so the election stays deterministic."""
    if not announces:
        return None
    return min(announces, key=lambda a: (a.priority1, a.clock_class,
                                         a.clock_accuracy, a.priority2,
                                         a.clock_identity))


def correction_field_ok(correction_ns: float, upstream_delay_ns: float,
                        residence_ns: float, *,
                        tol_ns: float = DEFAULT_CORRECTION_TOL_NS) -> bool:
    """The Sync correctionField must equal upstream delay + residence time."""
    return abs(correction_ns - (upstream_delay_ns + residence_ns)) <= tol_ns


def rate_ratio_valid(rate_ratio: float, *,
                     tol: float = DEFAULT_RATE_RATIO_TOL) -> bool:
    """True iff neighborRateRatio is within tolerance of 1.0 (syntonized)."""
    return abs(rate_ratio - 1.0) <= tol


def as_capable(pdelay_ok: bool, rate_ratio_ok: bool,
               follow_up_present: bool) -> bool:
    """asCapable is set only when Pdelay succeeded, neighborRateRatio is valid,
    and the (two-step) Follow_Up arrived — an absent/malformed Follow_Up clears
    it."""
    return pdelay_ok and rate_ratio_ok and follow_up_present


@dataclass
class GptpProtocolHealth:
    """802.1AS protocol-conformance verdict for one port/link."""
    elected_gm: str
    mean_link_delay_ns: float
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"gPTP proto GM={self.elected_gm or '-'} "
                f"link_delay={self.mean_link_delay_ns:.1f}ns -> {state}")

    def to_dict(self) -> dict:
        return {"elected_gm": self.elected_gm,
                "mean_link_delay_ns": self.mean_link_delay_ns,
                "checks": self.checks, "ok": self.ok}


def check_gptp_protocol(*, pdelay: PdelayExchange,
                        announces: Sequence[AnnounceMsg],
                        correction_ns: float, upstream_delay_ns: float,
                        residence_ns: float, follow_up_present: bool,
                        max_turnaround_ns: float = DEFAULT_MAX_TURNAROUND_NS
                        ) -> GptpProtocolHealth:
    """Aggregate the 802.1AS protocol-conformance checks for one link."""
    gm = bmca_elect(announces)
    rr_ok = rate_ratio_valid(pdelay.rate_ratio)
    pdelay_ok = (pdelay.mean_link_delay_ns >= 0.0
                 and 0.0 <= pdelay.turnaround_ns <= max_turnaround_ns)
    cf_ok = correction_field_ok(correction_ns, upstream_delay_ns, residence_ns)
    asc = as_capable(pdelay_ok, rr_ok, follow_up_present)
    checks = {
        "gm_elected": gm is not None,
        "pdelay_ok": pdelay_ok,
        "correction_field_ok": cf_ok,
        "rate_ratio_valid": rr_ok,
        "follow_up_present": follow_up_present,
        "as_capable": asc,
    }
    return GptpProtocolHealth(elected_gm=gm.source if gm else "",
                              mean_link_delay_ns=pdelay.mean_link_delay_ns,
                              checks=checks)
