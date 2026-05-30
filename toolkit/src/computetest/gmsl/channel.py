"""
GMSL3 channel (S-parameter) compliance framework (Sprint 4.1).

Post-processes a 4-port VNA sweep against the GMSL3 channel spec: applies the
100 MHz filter split (data used unfiltered below 50 MHz, filtered above) and
compares insertion-loss / return-loss traces to short/long-channel masks, with
a 2-10 MHz Power-over-Coax return-loss carve-out.

IMPORTANT — the numeric IL/RL limit lines (dB vs frequency, short & long
channel) live in ADI AN-2585 and are deliberately NOT reproduced here: they are
paywalled/PDF and must be transcribed from the official document. This module
ships the *framework* — the filter, the mask representation, and the comparison
— and takes the mask as an argument. ``AN2585_*_MASK`` are ``None`` placeholders
on purpose; populate them from the spec. Do NOT hardcode guessed limits, and
note that ``check_channel_compliance`` refuses to "pass" with no mask supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..instruments import SParamSweep, Vna

GMSL3_FILTER_HZ = 50e6              # AN-2585: data unfiltered below this, filtered above
POC_CARVEOUT_LOW_HZ = 2e6          # 2-10 MHz Power-over-Coax return-loss carve-out
POC_CARVEOUT_HIGH_HZ = 10e6


class MaskKind(str, Enum):
    """Comparison direction for a limit line.

    * ``INSERTION_LOSS`` — measured magnitude must be **>=** the limit (a deeper
      negative dB is *more* loss and fails).
    * ``RETURN_LOSS`` — measured magnitude must be **<=** the limit (a deeper
      negative dB is *better* reflection and passes).
    """
    INSERTION_LOSS = "insertion_loss"
    RETURN_LOSS = "return_loss"


@dataclass
class SParamMask:
    """A piecewise-linear limit line: ``(freq_hz, limit_db)`` breakpoints +
    a kind. Interpolated linearly between breakpoints, clamped at the ends. This
    is a *container* — the GMSL3 numbers come from AN-2585, not from here."""
    name: str
    kind: MaskKind
    breakpoints: list[tuple[float, float]]   # (freq_hz, limit_db), ascending in freq

    def limit_at(self, freq_hz: float) -> float:
        bps = self.breakpoints
        if not bps:
            raise ValueError(f"mask {self.name!r} has no breakpoints")
        if freq_hz <= bps[0][0]:
            return bps[0][1]
        if freq_hz >= bps[-1][0]:
            return bps[-1][1]
        for (f0, l0), (f1, l1) in zip(bps, bps[1:], strict=False):
            if f0 <= freq_hz <= f1:
                t = (freq_hz - f0) / (f1 - f0) if f1 > f0 else 0.0
                return l0 + t * (l1 - l0)
        return bps[-1][1]  # pragma: no cover - unreachable given the clamps above


@dataclass
class ChannelComplianceResult:
    """Outcome of comparing one S-parameter sweep to one mask."""
    sweep_param: str
    mask_name: str
    kind: str
    violations: list[tuple[float, float, float]]   # (freq_hz, measured_db, limit_db)
    points_checked: int

    @property
    def ok(self) -> bool:
        return self.points_checked > 0 and not self.violations

    def summary(self) -> str:
        state = "OK" if self.ok else f"FAIL({len(self.violations)} pts)"
        return (f"GMSL3 channel {self.sweep_param} vs {self.mask_name}: "
                f"{self.points_checked} pts -> {state}")

    def to_dict(self) -> dict:
        return {"sweep_param": self.sweep_param, "mask_name": self.mask_name,
                "kind": self.kind, "n_violations": len(self.violations),
                "points_checked": self.points_checked, "ok": self.ok}


def apply_gmsl3_filter(
        sweep: SParamSweep, *, filter_hz: float = GMSL3_FILTER_HZ
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Partition a sweep at the GMSL3 100 MHz filter split: ``(below, above)``
    where ``below`` is used unfiltered (< ``filter_hz``) and ``above`` is the
    filtered region (>= ``filter_hz``). The spec masks each region separately."""
    below = [(f, d) for f, d in sweep.points() if f < filter_hz]
    above = [(f, d) for f, d in sweep.points() if f >= filter_hz]
    return below, above


def check_against_mask(sweep: SParamSweep, mask: SParamMask) -> ChannelComplianceResult:
    """Compare an S-parameter sweep against a piecewise-linear mask."""
    violations: list[tuple[float, float, float]] = []
    for f, measured in sweep.points():
        limit = mask.limit_at(f)
        bad = (measured < limit if mask.kind == MaskKind.INSERTION_LOSS
               else measured > limit)
        if bad:
            violations.append((f, measured, limit))
    return ChannelComplianceResult(
        sweep_param=sweep.param, mask_name=mask.name, kind=mask.kind.value,
        violations=violations, points_checked=len(sweep.points()))


def check_channel_compliance(
        vna: Vna, *, il_param: str = "SDD21", rl_param: str = "SDD11",
        il_mask: SParamMask | None = None, rl_mask: SParamMask | None = None,
) -> tuple[ChannelComplianceResult, ChannelComplianceResult]:
    """Sweep the VNA and check insertion + return loss against supplied masks.

    ``il_mask`` / ``rl_mask`` MUST be provided — the AN-2585 numbers are not
    shipped (see the module docstring). Raises ``ValueError`` if a mask is
    missing so a run can't silently pass with no limits applied.
    """
    if il_mask is None or rl_mask is None:
        raise ValueError(
            "GMSL3 IL/RL masks must be supplied (AN-2585 limits are not shipped; "
            "transcribe them from the spec). Refusing to pass with no mask applied.")
    il = check_against_mask(vna.measure_sparam(il_param), il_mask)
    rl = check_against_mask(vna.measure_sparam(rl_param), rl_mask)
    return il, rl


# AN-2585 GMSL3 channel masks — BLOCKED on PDF transcription. Populate from the
# official spec (short & long channel IL/RL + the 2-10 MHz PoC carve-out). Do
# NOT guess these values; an empty mask is safer than a wrong one.
AN2585_IL_MASK_SHORT: SParamMask | None = None
AN2585_IL_MASK_LONG: SParamMask | None = None
AN2585_RL_MASK_SHORT: SParamMask | None = None
AN2585_RL_MASK_LONG: SParamMask | None = None
