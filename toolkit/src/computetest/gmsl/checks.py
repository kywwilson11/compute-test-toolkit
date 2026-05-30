"""
GMSL SerDes conformance checks (Sprint 4.1).

The verdict/health layer on top of the ``SerDesLink`` device abstraction in
``serdes.py``. Each check consumes a ``SerDesLink`` and returns a Health-shaped
result (a ``checks`` dict + ``ok`` + ``summary``/``to_dict``) so it drops
straight into the generic ``io.ocpdiag.emit_health`` path, exactly like the
legacy ``GmslHealth``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..ber import BertVerdict, assess
from ..pcie.retimer.base import EyeMeasurement, eye_quality_verdict
from .serdes import (
    DEFAULT_EOM_MV_MIN,
    DEFAULT_EOM_UI_MIN,
    GmslMode,
    GmslPrbsPattern,
    LinkDirection,
    LinkLock,
    SerDesLink,
)

# Default bound on lock acquisition (cold or forced relock). Real programs tune
# this per part/channel from the AN-2585 / UG-2208 bring-up budget.
DEFAULT_MAX_LOCK_MS = 20.0


@dataclass
class SerDesLinkHealth:
    """Lock + negotiated-mode verdict for one SerDes device's links."""
    part: str
    expect_mode: GmslMode
    links: list[LinkLock]
    max_lock_ms: float
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        locked = sum(1 for li in self.links if li.locked)
        modes = sorted({li.mode.value for li in self.links})
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"GMSL serdes {self.part}: {locked}/{len(self.links)} locked, "
                f"mode={'/'.join(modes)} (want {self.expect_mode.value}) -> {state}")

    def to_dict(self) -> dict:
        return {"part": self.part, "expect_mode": self.expect_mode.value,
                "max_lock_ms": self.max_lock_ms,
                "links": [li.to_dict() for li in self.links],
                "checks": self.checks, "ok": self.ok}


def check_serdes_link(serdes: SerDesLink, *,
                      expect_mode: GmslMode = GmslMode.PAM4_12G,
                      max_lock_ms: float = DEFAULT_MAX_LOCK_MS) -> SerDesLinkHealth:
    """Verify every link is locked, in the *expected* negotiated mode, and locked
    within the time budget.

    A "locked" link that fell back to a lower mode (the core GMSL3 silent-degrade
    case) fails ``mode_ok`` even though ``all_locked`` passes — which is the whole
    point of carrying the negotiated mode rather than a bare boolean.
    """
    links = serdes.lock_status()
    info = serdes.info()
    all_locked = bool(links) and all(li.locked for li in links)
    mode_ok = bool(links) and all(li.mode_ok(expect_mode) for li in links)
    # Lock-time is only meaningful where the link locked AND reported a time;
    # an empty set can't fault the device (all([]) is True).
    lock_times = [li.lock_time_ms for li in links
                  if li.locked and li.lock_time_ms is not None]
    lock_time_ok = all(t <= max_lock_ms for t in lock_times)
    checks = {"all_locked": all_locked, "mode_ok": mode_ok,
              "lock_time_ok": lock_time_ok}
    return SerDesLinkHealth(part=info.part_number, expect_mode=expect_mode,
                            links=links, max_lock_ms=max_lock_ms, checks=checks)


# Pre-FEC link-margin stop condition from the GMSL3 channel spec: a FEC-block
# input (pre-FEC) BER above this, or any uncorrectable FEC block, ends the run.
DEFAULT_PRE_FEC_TARGET_BER = 1e-7


@dataclass
class PrbsBerHealth:
    """PRBS bit-error-rate verdict for one direction of one link."""
    link: int
    direction: str
    pattern: str
    kind: str                       # "link" or "video" PRBS generator
    errors: int
    bits: float
    target_ber: float
    confidence_target: float
    confidence_reached: float
    ber_upper: float
    status: str                     # ber.assess: pass | continue | fail
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"GMSL PRBS {self.kind}/{self.direction} link{self.link} "
                f"E={self.errors} n={self.bits:.2e} BER<={self.ber_upper:.2e} "
                f"(CL={self.confidence_reached:.4f}) -> {state}")

    def to_dict(self) -> dict:
        return {"link": self.link, "direction": self.direction,
                "pattern": self.pattern, "kind": self.kind, "errors": self.errors,
                "bits": self.bits, "target_ber": self.target_ber,
                "confidence_target": self.confidence_target,
                "confidence_reached": self.confidence_reached,
                "ber_upper": self.ber_upper, "status": self.status,
                "checks": self.checks, "ok": self.ok}


def check_prbs_ber(serdes: SerDesLink, *, link: int = 0,
                   direction: LinkDirection = LinkDirection.FORWARD,
                   pattern: GmslPrbsPattern = GmslPrbsPattern.PRBS31,
                   duration_s: float = 1.0, kind: str = "link",
                   target_ber: float = DEFAULT_PRE_FEC_TARGET_BER,
                   confidence: float = 0.95) -> PrbsBerHealth:
    """Run a PRBS window in one direction and turn (errors, bits) into a
    confidence-bounded BER verdict via ``computetest.ber``.

    ``target_ber`` is the *pre-FEC* (FEC-block-input) stop condition from the
    GMSL3 channel spec — the real link-margin oracle, since a clean post-FEC
    output can still be living on FEC headroom. ``kind`` distinguishes the
    link-layer PRBS from the video-pattern PRBS generator.
    """
    r = serdes.run_prbs_bist(direction=direction, pattern=pattern,
                             duration_s=duration_s, link=link)
    v: BertVerdict = assess(errors=r.error_count, bits=r.bits,
                            target_ber=target_ber, confidence_target=confidence)
    checks = {"prbs_locked": r.locked, "ber_proven": v.status == "pass"}
    return PrbsBerHealth(
        link=link, direction=direction.value, pattern=pattern.value, kind=kind,
        errors=r.error_count, bits=r.bits, target_ber=target_ber,
        confidence_target=confidence, confidence_reached=v.confidence_reached,
        ber_upper=v.ber_upper, status=v.status, checks=checks)


@dataclass
class FecHealth:
    """Reed-Solomon FEC verdict for one link. The hard fail is any uncorrectable
    block; the corrected-symbol count is a trend/soak signal — a rising corrected
    rate at clean post-FEC output is the earliest degradation sign."""
    link: int
    corrected_symbols: int
    corrected_codewords: int
    uncorrectable_blocks: int
    max_corrected: int | None
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"GMSL FEC link{self.link} corrected={self.corrected_symbols} "
                f"uncorrectable={self.uncorrectable_blocks} -> {state}")

    def to_dict(self) -> dict:
        return {"link": self.link, "corrected_symbols": self.corrected_symbols,
                "corrected_codewords": self.corrected_codewords,
                "uncorrectable_blocks": self.uncorrectable_blocks,
                "max_corrected": self.max_corrected,
                "checks": self.checks, "ok": self.ok}


def check_fec(serdes: SerDesLink, *, link: int = 0,
              max_corrected_symbols: int | None = None) -> FecHealth:
    """Read the Reed-Solomon FEC counters for one link. Any uncorrectable block
    is an immediate fail; if ``max_corrected_symbols`` is given, the corrected
    count is also gated against that budget (the real path samples this over a
    soak window to catch a rising corrected-bit rate)."""
    f = serdes.read_fec_stats(link)
    checks = {"no_uncorrectable": f.uncorrectable_blocks == 0}
    if max_corrected_symbols is not None:
        checks["corrected_within_budget"] = (
            f.corrected_symbols <= max_corrected_symbols)
    return FecHealth(link=link, corrected_symbols=f.corrected_symbols,
                     corrected_codewords=f.corrected_codewords,
                     uncorrectable_blocks=f.uncorrectable_blocks,
                     max_corrected=max_corrected_symbols, checks=checks)


@dataclass
class EomHealth:
    """EOM eye-margin verdict for one link/direction, mapped onto the portable
    retimer ``EyeMeasurement`` so GMSL eye data flows through the same consumers
    (BigQuery/Looker, eye_quality_verdict) as PCIe retimer eye data."""
    link: int
    direction: str
    mode: str
    eye: EyeMeasurement
    subeyes_mv: list[float]
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        state = "OK" if self.ok else "FAIL(eye)"
        return (f"GMSL EOM link{self.link}/{self.direction} "
                f"eye {self.eye.eye_ui:.3f}UI/{self.eye.eye_mv:.1f}mV "
                f"subeyes={self.subeyes_mv} -> {state}")

    def to_dict(self) -> dict:
        return {"link": self.link, "direction": self.direction, "mode": self.mode,
                "eye": self.eye.to_dict(), "subeyes_mv": list(self.subeyes_mv),
                "checks": self.checks, "ok": self.ok}


def check_eom(serdes: SerDesLink, *, link: int = 0,
              direction: LinkDirection = LinkDirection.FORWARD) -> EomHealth:
    """Read the eye-opening monitor for one link/direction and map it onto the
    retimer ``EyeMeasurement``. The PAM4 *worst* sub-eye becomes ``eye_mv`` (the
    closing eye is the margin oracle); the verdict reuses the retimer's
    ``eye_quality_verdict`` with GMSL-specific thresholds."""
    eom = serdes.read_eom(link, direction)
    eye = EyeMeasurement(
        lane=link, eye_ui=eom.horizontal_ui, eye_mv=eom.worst_vertical_mv,
        height_mv=max(eom.vertical_mv), width_ui=eom.horizontal_ui)
    passed = eye_quality_verdict(eye, ui_min=DEFAULT_EOM_UI_MIN,
                                 mv_min=DEFAULT_EOM_MV_MIN)
    return EomHealth(link=link, direction=direction.value, mode=eom.mode.value,
                     eye=eye, subeyes_mv=list(eom.vertical_mv),
                     checks={"eye_open": passed})
