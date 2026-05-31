"""
802.1ASdm hot-standby / redundant-domain gPTP failover (Sprint 4.2).

Automotive fail-operational time-sync: when the primary grandmaster is lost
mid-run, the slave must fail over to a hot-standby gPTP domain without the
recovered-clock time error exceeding the holdover budget, and without ever
dropping asCapable on the surviving domain. Reuses the BMCA election from
``gptp_proto`` to confirm a standby GM is electable.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from .gptp_proto import AnnounceMsg, bmca_elect

# Failover TE transient budget (ns), as a one-sided max|TE| bound (consistent
# with gptp.py's |TE| convention) — the ~1 us automotive fail-operational design
# target, looser than the 80 ns steady-state but bounded.
DEFAULT_HOLDOVER_NS = 1000.0


@dataclass
class HotStandbyHealth:
    """Redundant-domain failover verdict."""
    failover_te_transient_ns: float
    holdover_budget_ns: float
    surviving_domain: str
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"gPTP failover -> {self.surviving_domain or '-'} "
                f"TE_transient={self.failover_te_transient_ns:.1f}ns "
                f"(holdover {self.holdover_budget_ns:.0f}) -> {state}")

    def to_dict(self) -> dict:
        return {"failover_te_transient_ns": self.failover_te_transient_ns,
                "holdover_budget_ns": self.holdover_budget_ns,
                "surviving_domain": self.surviving_domain,
                "checks": self.checks, "ok": self.ok}


def check_hot_standby_failover(*, standby_announces: Sequence[AnnounceMsg],
                               te_samples_ns: Sequence[float],
                               as_capable_held: bool,
                               holdover_budget_ns: float = DEFAULT_HOLDOVER_NS
                               ) -> HotStandbyHealth:
    """Verify a primary-GM-loss failover: a standby GM must be electable on the
    surviving domain, the recovered-clock TE transient (one-sided max|TE|) over
    the failover window must stay within the holdover budget, and asCapable must
    not drop. An empty TE window (no measurement) fails rather than passing 0 ns."""
    gm = bmca_elect(standby_announces)
    have_samples = len(te_samples_ns) > 0
    max_te = max((abs(s) for s in te_samples_ns), default=0.0)
    checks = {
        "standby_gm_available": gm is not None,
        # An empty TE window means the failover transient was never measured —
        # that is not a pass.
        "te_window_measured": have_samples,
        "te_within_holdover": have_samples and max_te <= holdover_budget_ns,
        "as_capable_held": as_capable_held,
    }
    return HotStandbyHealth(failover_te_transient_ns=max_te,
                            holdover_budget_ns=holdover_budget_ns,
                            surviving_domain=gm.source if gm else "",
                            checks=checks)
