"""
802.1AS gPTP recovered-clock time-error check (Sprint 4.2).

Turns a window of recovered-1PPS time-error (TE) samples — collected from a
``TimeIntervalAnalyzer`` over the observation window — into the Avnu gPTP
verdict: the recovered clock must lock within ~6 s and hold |TE| within ±80 ns
(per-hop budget) across the window. Emits the TE series + max/mean/peak-to-peak
TE + lock-acquisition time to ocp-diag with LESS_THAN_OR_EQUAL validators.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..io.ocpdiag import LESS_THAN_OR_EQUAL, Emitter, validator

# Avnu gPTP defaults: per-hop |TE| <= 80 ns, lock within 6 s, held over a
# 5-minute observation window. Programs tighten these via a CDS.
DEFAULT_MAX_TE_NS = 80.0
DEFAULT_LOCK_LIMIT_S = 6.0


@dataclass
class GptpTeHealth:
    """802.1AS gPTP recovered-clock time-error verdict for one window."""
    samples_ns: list[float]
    max_te_ns: float
    mean_te_ns: float
    pp_te_ns: float                  # peak-to-peak
    lock_acq_s: float
    max_te_limit_ns: float
    lock_limit_s: float
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"gPTP TE max={self.max_te_ns:.1f}ns (limit {self.max_te_limit_ns:.0f}) "
                f"lock={self.lock_acq_s:.2f}s -> {state}")

    def to_dict(self) -> dict:
        return {"max_te_ns": self.max_te_ns, "mean_te_ns": self.mean_te_ns,
                "pp_te_ns": self.pp_te_ns, "lock_acq_s": self.lock_acq_s,
                "max_te_limit_ns": self.max_te_limit_ns,
                "lock_limit_s": self.lock_limit_s, "n_samples": len(self.samples_ns),
                "checks": self.checks, "ok": self.ok}


def check_gptp_time_error(samples_ns: Sequence[float], *, lock_acq_s: float,
                          max_te_limit_ns: float = DEFAULT_MAX_TE_NS,
                          lock_limit_s: float = DEFAULT_LOCK_LIMIT_S) -> GptpTeHealth:
    """Verdict a window of signed TE samples (ns) + the lock-acquisition time
    against the Avnu gPTP criteria: |TE| within the per-hop limit AND the clock
    locked within the time budget."""
    if not samples_ns:
        raise ValueError("need at least one TE sample")
    abs_samples = [abs(s) for s in samples_ns]
    max_te = max(abs_samples)
    mean_te = sum(abs_samples) / len(abs_samples)
    pp = max(samples_ns) - min(samples_ns)
    checks = {
        "max_te_within_limit": max_te <= max_te_limit_ns,
        "lock_acquired_in_time": lock_acq_s <= lock_limit_s,
    }
    return GptpTeHealth(samples_ns=list(samples_ns), max_te_ns=max_te,
                        mean_te_ns=mean_te, pp_te_ns=pp, lock_acq_s=lock_acq_s,
                        max_te_limit_ns=max_te_limit_ns, lock_limit_s=lock_limit_s,
                        checks=checks)


def emit_gptp_te(em: Emitter, health: GptpTeHealth, *,
                 hardware_info_id: str | None = None) -> str:
    """Emit a gPTP TE verdict: the TE samples as a measurementSeries with a
    |TE| <= limit validator, plus scalar max/mean/pp TE and lock-acq time."""
    sid = em.step_start("tsn.gptp.max_te")
    te_validator = [validator(LESS_THAN_OR_EQUAL, health.max_te_limit_ns,
                              name="max_te_ns")]
    series = em.series_start(name="tsn.gptp.te", unit="ns", validators=te_validator,
                             hardware_info_id=hardware_info_id)
    for i, sample in enumerate(health.samples_ns):
        em.series_element(series_id=series, index=i, value=sample)
    em.series_end(series_id=series, total_count=len(health.samples_ns))
    em.measurement(name="max_te_ns", value=health.max_te_ns, unit="ns",
                   validators=te_validator)
    em.measurement(name="mean_te_ns", value=health.mean_te_ns, unit="ns")
    em.measurement(name="pp_te_ns", value=health.pp_te_ns, unit="ns")
    em.measurement(name="lock_acq_s", value=health.lock_acq_s, unit="s",
                   validators=[validator(LESS_THAN_OR_EQUAL, health.lock_limit_s,
                                         name="lock_limit_s")])
    status = "pass" if health.ok else "fail"
    em.diagnosis(verdict=f"tsn.gptp.{status}",
                 type_="PASS" if health.ok else "FAIL", message=health.summary())
    em.step_end(status, step_id=sid)
    return sid
