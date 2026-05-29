"""
Vendor-agnostic PCIe retimer abstraction.

A PCIe retimer is a small signal-conditioning ASIC sitting in the lane between
the host port and the endpoint. Modern compute platforms (NVIDIA HGX H100/B100
GPU boards, OCP HBM accelerator carriers) ship with retimers from Astera Labs
(Aries), Microchip (XpressConnect), or Texas Instruments. The toolkit's BERT
proves the *link* is good post-retimer; the retimer itself exposes a far richer
**telemetry surface** (eye opening per lane, equalization levels, junction
temperature, host/line-side loopback, PRBS generator + checker) that an MT
station should read on every build.

This module defines the vendor-agnostic contract — what every retimer surfaces.
``aries.py`` carries the Astera SDK boundary stub (SDK-gated). New vendors plug
in by subclassing ``Retimer`` and implementing the same surface.

What the contract gives you:

* **Eye telemetry** — UI-margin, voltage-margin, eye height/width per lane.
* **EQ telemetry** — DFE tap weights, CTLE gain, AGC level per lane.
* **Tj telemetry** — die-junction temperature (with operator-configurable alert
  thresholds).
* **Loopback** — host-side and line-side, for isolating retimer-vs-link faults.
* **PRBS BIST** — pattern selection + error counters, for in-system stress
  without an external BERT instrument.

A reading-only `info()` snapshot identifies the retimer (vendor, part, FW,
lane count) so a fixture map can validate "the right retimer is here".

Reference: Astera Aries product brief (asteralabs.com), COMET USB-I2C dongle +
Python SDK; OCP DC SFF retimer telemetry profile.
"""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field
from enum import Enum


class RetimerError(RuntimeError):
    """A retimer fault — register-read failure, link not trained, BIST
    timeout. One exception type for every layer so callers ``except
    RetimerError`` regardless of which vendor backend failed."""


class LoopbackSide(str, Enum):
    """Which side of the retimer is looped back.

    * ``HOST`` — host-side loopback. Useful for proving the upstream link is
      clean independently of the endpoint.
    * ``LINE`` — line-side loopback. Tests the downstream link without an
      endpoint device responding.
    """
    HOST = "host"
    LINE = "line"


class PRBSPattern(str, Enum):
    """PRBS pattern for the retimer's BIST generator. PRBS31 is the PCIe
    compliance pattern for Gen5/Gen6 measurement."""
    PRBS7 = "PRBS7"
    PRBS9 = "PRBS9"
    PRBS11 = "PRBS11"
    PRBS15 = "PRBS15"
    PRBS23 = "PRBS23"
    PRBS31 = "PRBS31"


# Eye-quality verdict thresholds. UI margin (fraction of a unit interval) and
# voltage margin (mV) below these are flagged. Tunable per program via
# ``Retimer.set_eye_thresholds`` (override per-instance, not module-level).
DEFAULT_EYE_UI_MIN = 0.25            # 0.25 UI ~ the AIAG-style "marginal" floor
DEFAULT_EYE_MV_MIN = 30.0            # 30 mV vertical opening
DEFAULT_TJ_MAX_C = 105.0             # most retimers spec Tj_max ~ 125 C; alert at 105
DEFAULT_BIST_DURATION_S = 1.0


@dataclass
class EyeMeasurement:
    """Per-lane eye telemetry."""
    lane: int
    eye_ui: float                    # horizontal opening, UI
    eye_mv: float                    # vertical opening, mV
    height_mv: float | None = None   # full eye height (sometimes distinct)
    width_ui: float | None = None    # full eye width (sometimes distinct)
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"lane": self.lane, "eye_ui": self.eye_ui, "eye_mv": self.eye_mv,
                "height_mv": self.height_mv, "width_ui": self.width_ui,
                "raw": dict(self.raw)}


@dataclass
class EqLevels:
    """Per-lane equalization-loop state. DFE taps capture the equalizer
    "fingerprint" — useful for trend / drift detection across a fleet."""
    lane: int
    ctle_gain_db: float | None = None    # continuous-time linear equalizer gain
    dfe_taps: list[float] = field(default_factory=list)  # decision-feedback equalizer taps
    agc_level: float | None = None       # automatic gain control level
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"lane": self.lane, "ctle_gain_db": self.ctle_gain_db,
                "dfe_taps": list(self.dfe_taps), "agc_level": self.agc_level,
                "raw": dict(self.raw)}


@dataclass
class RetimerInfo:
    """Identity of the retimer (vendor/part/FW/lane count)."""
    vendor: str
    part_number: str
    serial: str = ""
    firmware: str = ""
    lanes: int = 16
    pcie_gen: int = 5

    def to_dict(self) -> dict:
        return {"vendor": self.vendor, "part_number": self.part_number,
                "serial": self.serial, "firmware": self.firmware,
                "lanes": self.lanes, "pcie_gen": self.pcie_gen}


@dataclass
class LaneStatus:
    """Compact per-lane summary: link state, error count, eye verdict."""
    lane: int
    linked: bool
    error_count: int
    eye_pass: bool
    note: str = ""


@dataclass
class BISTResult:
    """One PRBS-BIST window's result."""
    pattern: PRBSPattern
    duration_s: float
    per_lane_errors: dict[int, int]
    locked: bool                                # True if every lane locked the PRBS

    @property
    def total_errors(self) -> int:
        return sum(self.per_lane_errors.values())

    def to_dict(self) -> dict:
        return {"pattern": self.pattern.value, "duration_s": self.duration_s,
                "per_lane_errors": dict(self.per_lane_errors),
                "total_errors": self.total_errors, "locked": self.locked}


def eye_quality_verdict(eye: EyeMeasurement, *, ui_min: float = DEFAULT_EYE_UI_MIN,
                         mv_min: float = DEFAULT_EYE_MV_MIN) -> bool:
    """True iff a per-lane eye opening passes the program's thresholds."""
    return eye.eye_ui >= ui_min and eye.eye_mv >= mv_min


# ----------------------------------------------------------------------------
# Retimer ABC
# ----------------------------------------------------------------------------
class Retimer(abc.ABC):
    """Vendor-agnostic retimer surface.

    Implementations: ``MockRetimer`` (deterministic, in-memory) and
    ``AriesRetimer`` (Astera SDK boundary). New vendors subclass this.

    Subclass invariants:

    * Lane indices are 0-based and bounded by ``info().lanes``.
    * Eye / EQ readers may raise ``RetimerError`` (link not trained, register
      unreadable) — never silently return zeros.
    * ``read_temperature`` returns °C.
    """

    def __init__(self) -> None:
        self._eye_ui_min = DEFAULT_EYE_UI_MIN
        self._eye_mv_min = DEFAULT_EYE_MV_MIN
        self._tj_max = DEFAULT_TJ_MAX_C

    # --- identity / config ------------------------------------------------
    @abc.abstractmethod
    def info(self) -> RetimerInfo:
        """Return the retimer's vendor/part/FW/lane count."""

    def set_eye_thresholds(self, *, ui_min: float | None = None,
                            mv_min: float | None = None) -> None:
        """Override the per-instance eye verdict thresholds. Skip a kwarg to
        leave it at the module default."""
        if ui_min is not None:
            self._eye_ui_min = ui_min
        if mv_min is not None:
            self._eye_mv_min = mv_min

    def set_tj_max(self, c: float) -> None:
        """Override the per-instance Tj alert threshold (°C)."""
        self._tj_max = c

    # --- telemetry -------------------------------------------------------
    @abc.abstractmethod
    def read_eye(self, lane: int) -> EyeMeasurement:
        """Eye telemetry for one lane."""

    @abc.abstractmethod
    def read_eq(self, lane: int) -> EqLevels:
        """Equalizer state for one lane (CTLE/DFE/AGC)."""

    @abc.abstractmethod
    def read_temperature(self) -> float:
        """Die junction temperature in °C."""

    # --- loopback / BIST -------------------------------------------------
    @abc.abstractmethod
    def set_loopback(self, side: LoopbackSide, enable: bool) -> None:
        """Enable/disable host- or line-side loopback."""

    @abc.abstractmethod
    def run_prbs_bist(self, pattern: PRBSPattern = PRBSPattern.PRBS31,
                      duration_s: float = DEFAULT_BIST_DURATION_S) -> BISTResult:
        """Run a PRBS BIST window and return per-lane error counts."""

    # --- summary --------------------------------------------------------
    def lane_status(self, lane: int) -> LaneStatus:
        """Per-lane health summary combining eye + a 0-duration error read."""
        eye = self.read_eye(lane)
        eye_ok = eye_quality_verdict(eye, ui_min=self._eye_ui_min,
                                       mv_min=self._eye_mv_min)
        return LaneStatus(
            lane=lane,
            linked=eye.eye_ui > 0 and eye.eye_mv > 0,
            error_count=0,
            eye_pass=eye_ok,
            note="" if eye_ok else
            f"eye {eye.eye_ui:.3f}UI/{eye.eye_mv:.1f}mV below "
            f"{self._eye_ui_min:.2f}UI/{self._eye_mv_min:.0f}mV",
        )

    def temperature_ok(self) -> bool:
        """True iff Tj is below the configured alert threshold."""
        return self.read_temperature() <= self._tj_max


# ----------------------------------------------------------------------------
# MockRetimer (in-memory, deterministic)
# ----------------------------------------------------------------------------
class MockRetimer(Retimer):
    """In-memory retimer for tests.

    Eye/EQ telemetry are deterministic functions of the (serial, lane) pair so a
    test board can stand in for a real one. ``injected_eye_ui``,
    ``injected_eye_mv``, ``injected_temperature_c`` are knobs for asserting
    error paths.
    """

    def __init__(self, *, vendor: str = "MockCorp",
                 part_number: str = "MK-RT-16", serial: str = "MOCKSN0001",
                 firmware: str = "0.1.0", lanes: int = 16,
                 pcie_gen: int = 5,
                 injected_eye_ui: float = 0.32,
                 injected_eye_mv: float = 75.0,
                 injected_temperature_c: float = 62.0,
                 injected_bist_errors: int = 0) -> None:
        super().__init__()
        self._info = RetimerInfo(vendor=vendor, part_number=part_number,
                                  serial=serial, firmware=firmware,
                                  lanes=lanes, pcie_gen=pcie_gen)
        self._injected_eye_ui = injected_eye_ui
        self._injected_eye_mv = injected_eye_mv
        self._injected_tj = injected_temperature_c
        self._injected_bist_errors = injected_bist_errors
        self._loopback: dict[LoopbackSide, bool] = {LoopbackSide.HOST: False,
                                                     LoopbackSide.LINE: False}

    def info(self) -> RetimerInfo:
        return self._info

    def _check_lane(self, lane: int) -> None:
        if lane < 0 or lane >= self._info.lanes:
            raise RetimerError(
                f"lane {lane} out of range [0, {self._info.lanes - 1}]")

    def _jitter(self, lane: int) -> float:
        """Deterministic per-lane jitter ∈ [-0.06, +0.06] seeded by (serial, lane)."""
        h = hashlib.sha256(f"{self._info.serial}:{lane}".encode()).digest()
        return ((h[0] / 255.0) - 0.5) * 0.12

    def read_eye(self, lane: int) -> EyeMeasurement:
        self._check_lane(lane)
        jitter = self._jitter(lane)
        return EyeMeasurement(
            lane=lane,
            eye_ui=max(0.0, round(self._injected_eye_ui + jitter * 0.5, 4)),
            eye_mv=max(0.0, round(self._injected_eye_mv + jitter * 30.0, 2)),
            height_mv=max(0.0, round(self._injected_eye_mv + jitter * 30.0, 2)),
            width_ui=max(0.0, round(self._injected_eye_ui + jitter * 0.5, 4)),
        )

    def read_eq(self, lane: int) -> EqLevels:
        self._check_lane(lane)
        jitter = self._jitter(lane)
        # A 4-tap DFE is the common modern shape (Gen5+/PAM-4).
        return EqLevels(
            lane=lane,
            ctle_gain_db=round(12.0 + jitter * 3.0, 2),
            dfe_taps=[round(0.40 + jitter, 3),
                       round(-0.18 + jitter * 0.5, 3),
                       round(0.05 + jitter * 0.2, 3),
                       round(-0.02 + jitter * 0.1, 3)],
            agc_level=round(0.55 + jitter * 0.1, 3),
        )

    def read_temperature(self) -> float:
        return self._injected_tj

    def set_loopback(self, side: LoopbackSide, enable: bool) -> None:
        self._loopback[side] = enable

    def loopback_state(self, side: LoopbackSide) -> bool:
        return self._loopback[side]

    def run_prbs_bist(self, pattern: PRBSPattern = PRBSPattern.PRBS31,
                      duration_s: float = DEFAULT_BIST_DURATION_S) -> BISTResult:
        if duration_s <= 0:
            raise RetimerError(f"BIST duration must be > 0; got {duration_s}")
        # All lanes accumulate the SAME injected error count (the mock is the
        # cleanest model; per-lane variation is left to the real impl). The
        # locked flag flips False if BIST errors exceed a per-lane threshold.
        per = {lane: self._injected_bist_errors for lane in range(self._info.lanes)}
        return BISTResult(pattern=pattern, duration_s=duration_s,
                          per_lane_errors=per,
                          locked=self._injected_bist_errors < 1_000_000)
