"""
Receiver Lane Margining (PCIe Gen4+) and TX-preset equalization characterization.

Lane margining is the spec-standard, scope-free way to measure the eye margin of
each lane on-die: command the receiver to step its sampling point in time (and
voltage, if supported) until errors appear. It turns "the link trained" into a
per-lane margin number you can set a data-driven limit on — the modern successor to
the X-ES pre-emphasis sweep.

Two backends, as everywhere:
  * Mock: derives a believable per-lane margin from the device's injected_ber, with
    seeded per-lane variation so a marginal link shows one weak lane.
  * Real (Gen4+ hardware): the register-sequencing path through the Lane Margining
    extended capability is sketched in `_real_margin_lane` with the spec steps; it
    is intentionally guarded because kernel/vendor support varies and it can perturb
    a live link. Enable explicitly once validated on your hardware.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .backend import Backend, ECAP_LANE_MARGINING, MockBackend

# A typical manufacturing limit: each lane must have at least this timing margin,
# expressed as a fraction of the unit interval (UI). 0.25 UI is a reasonable bar.
DEFAULT_MIN_TIMING_UI = 0.25


@dataclass
class LaneMargin:
    lane: int
    timing_ui: float          # timing margin to failure, in fractions of a UI
    voltage_mv: float | None = None  # voltage margin in mV, if the RX supports it


@dataclass
class MarginResult:
    bdf: str
    lanes: list[LaneMargin]
    min_timing_ui: float = field(default=0.0)
    limit_ui: float = DEFAULT_MIN_TIMING_UI
    note: str = ""            # why margining produced no lanes (unsupported / errored)

    def __post_init__(self):
        if self.lanes:
            self.min_timing_ui = min(l.timing_ui for l in self.lanes)

    @property
    def available(self) -> bool:
        """True iff margining actually measured at least one lane."""
        return bool(self.lanes)

    @property
    def worst_lane(self) -> LaneMargin | None:
        return min(self.lanes, key=lambda l: l.timing_ui) if self.lanes else None

    @property
    def ok(self) -> bool:
        return bool(self.lanes) and self.min_timing_ui >= self.limit_ui

    def summary(self) -> str:
        if not self.lanes:
            reason = f" ({self.note})" if self.note else ""
            return f"{self.bdf}: margining unavailable{reason}"
        w = self.worst_lane
        state = "OK" if self.ok else f"FAIL(lane {w.lane}={w.timing_ui:.3f}UI<{self.limit_ui}UI)"
        return (f"{self.bdf}: min margin {self.min_timing_ui:.3f} UI "
                f"across {len(self.lanes)} lanes -> {state}")

    def to_dict(self) -> dict:
        return {"bdf": self.bdf, "min_timing_ui": round(self.min_timing_ui, 4),
                "limit_ui": self.limit_ui, "ok": self.ok, "available": self.available,
                "note": self.note,
                "lanes": {l.lane: round(l.timing_ui, 4) for l in self.lanes}}


def _mock_lane_margin(bdf: str, lane: int, injected_ber: float) -> float:
    """A deterministic, believable per-lane timing margin for simulation."""
    # Base margin shrinks with injected BER; per-lane jitter is seeded by bdf+lane.
    h = hashlib.sha256(f"{bdf}:{lane}".encode()).digest()
    jitter = (h[0] / 255.0) * 0.12               # 0..0.12 UI of lane-to-lane spread
    base = 0.45 if injected_ber <= 1e-15 else max(0.05, 0.45 - 6e7 * injected_ber)
    # Make one lane clearly worst on a marginal link (the realistic failure shape).
    worst_lane = h[1] % 16
    penalty = 0.18 if (injected_ber > 1e-12 and lane == worst_lane) else 0.0
    return round(max(0.02, base - jitter * 0.5 - penalty), 4)


def margin_link(backend: Backend, bdf: str, lanes: int | None = None,
                limit_ui: float = DEFAULT_MIN_TIMING_UI) -> MarginResult:
    """Measure per-lane receiver timing margin across the link."""
    dev = backend.get_device(bdf)
    n = lanes if lanes is not None else dev.current_link_width
    if backend.find_ext_cap(bdf, ECAP_LANE_MARGINING) is None:
        return MarginResult(bdf, [], limit_ui=limit_ui)  # not Gen4+ / not supported

    results: list[LaneMargin] = []
    if isinstance(backend, MockBackend):
        ber = backend._dev(bdf).injected_ber
        for lane in range(n):
            results.append(LaneMargin(lane, _mock_lane_margin(bdf, lane, ber)))
    else:  # pragma: no cover - real-hw path
        for lane in range(n):
            results.append(_real_margin_lane(backend, bdf, lane))
    return MarginResult(bdf, results, limit_ui=limit_ui)


def _real_margin_lane(backend: Backend, bdf: str, lane: int) -> LaneMargin:
    """Real-hardware margining via the Lane Margining capability (Gen4+).

    The spec sequence per lane: select the lane in the Margining Lane Control
    register, issue a 'set timing offset = N steps' command, wait the margining
    dwell, read the Margining Lane Status (error count / 'too many errors'), and
    step the offset outward until errors exceed the limit. The largest passing
    offset, scaled by the device's reported MaxTimingOffset and step count, is the
    timing margin in UI.

    This perturbs a live link and depends on uneven kernel/vendor support, so it is
    guarded until validated on your specific hardware.
    """
    raise NotImplementedError(
        "Real lane margining needs per-hardware validation; run with the mock "
        "backend, or wire this to your kernel/vendor margining path once verified.")


# --- TX-preset equalization characterization (the X-ES sweep, generalized) ---- #
@dataclass
class EqPoint:
    preset: int
    correctable: int
    min_margin_ui: float
    link_up: bool


@dataclass
class EqSweep:
    bdf: str
    points: list[EqPoint]

    @property
    def best(self) -> EqPoint | None:
        ok = [p for p in self.points if p.link_up]
        # Prefer the preset with the largest eye margin, tie-broken by fewest errors.
        return max(ok, key=lambda p: (p.min_margin_ui, -p.correctable)) if ok else None

    def summary(self) -> str:
        b = self.best
        head = f"{self.bdf} eq sweep:" + "".join(
            f"\n    P{p.preset:<2} {'up' if p.link_up else 'DOWN':<4} "
            f"cor={p.correctable:<5} margin={p.min_margin_ui:.3f}UI" for p in self.points)
        tail = f"\n  best preset = P{b.preset} (margin {b.min_margin_ui:.3f} UI)" if b else \
               "\n  no preset produced a usable link"
        return head + tail


def characterize_equalization(backend: Backend, bdf: str,
                              presets=range(0, 11)) -> EqSweep:
    """Sweep TX presets, (mock) retrain, and record errors + eye margin per preset.

    On real hardware each step would set the preset via the Secondary PCIe
    capability's lane-equalization registers and toggle Retrain Link, then run a
    short BERT + margining. The mock models a device whose error/eye quality is
    best at its ``optimal_preset`` and degrades with distance from it.
    """
    points: list[EqPoint] = []
    if isinstance(backend, MockBackend):
        d = backend._dev(bdf)
        for p in presets:
            dist = abs(p - d.optimal_preset)
            link_up = dist <= 6                       # far-off presets fail to train
            cor = int(dist * dist * 12)               # errors grow with distance
            margin = round(max(0.05, 0.45 - 0.06 * dist), 4)
            points.append(EqPoint(p, cor if link_up else 0, margin if link_up else 0.0,
                                  link_up))
    else:  # pragma: no cover - real hardware path
        raise NotImplementedError(
            "Real eq characterization sets presets via Secondary PCIe cap + retrain; "
            "validate on hardware before enabling.")
    return EqSweep(bdf, points)
