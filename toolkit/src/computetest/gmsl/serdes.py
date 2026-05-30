"""
Vendor-agnostic GMSL SerDes link abstraction (Sprint 4.1).

GMSL (Gigabit Multimedia Serial Link, Analog Devices/Maxim) carries automotive
camera and display traffic over a single coax/STP: a high-speed FORWARD channel
(GMSL3 = 12 Gbps PAM4 video) and a low-speed REVERSE control channel (187.5 Mbps
NRZ). ``computetest.gmsl.video`` already proves the *whole* sensor->SoC path is
live (deserializer lock + a v4l2 frame grab). This module adds the layer beneath
it: the **register/link device surface** a validation station reads to qualify
the SerDes itself — negotiated mode, pre/post-FEC link margin, eye-opening
monitor (EOM), and the safety error counters — without an external instrument.

It is the GMSL analogue of ``pcie/retimer/base.py``: one vendor-agnostic
contract (``SerDesLink``) with a deterministic in-memory ``MockSerDes`` for tests
and CI, and room for a real ADI-register backend (MAX96793 serializer /
MAX96792A deserializer) to drop in behind the same surface.

Why a *mode-aware* lock matters: GMSL3 silicon falls back to GMSL3-NRZ-6G, then
GMSL2, then GMSL1 if the channel can't sustain 12 Gbps PAM4. A link reporting
"locked" in a *degraded* mode is a defect the station must catch — so
``lock_status`` reports the negotiated ``GmslMode``, not just a boolean.

Scope: this is conformance/validation of the platform's own link health (the ADI
GMSL3 Channel Spec AN-2585 / Hardware Design & Validation Guide UG-2208 view).
The error-counter / ERRB surface here is the standards-defined diagnostic that a
safety mechanism *fires*; the RAS check that drives it lands later in Sprint 4.1.

Reference: ADI AN-2585 (GMSL3 Channel Specification), UG-2208 (GMSL3 Hardware
Design and Validation Guide), MAX96793/MAX96792A datasheets.
"""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass
from enum import Enum


class SerDesError(RuntimeError):
    """A GMSL SerDes fault — register-read failure, link not locked, BIST
    timeout. One exception type across every backend so callers ``except
    SerDesError`` regardless of which vendor/transport failed (mirrors
    ``RetimerError``)."""


class GmslMode(str, Enum):
    """Negotiated GMSL link mode. GMSL3 can fall back down this ladder when the
    channel can't sustain the top rate; a "locked" link in a lower mode than
    designed is a defect, so the mode is always reported alongside lock."""
    PAM4_12G = "GMSL3-PAM4-12G"      # GMSL3 top mode: 12 Gbps PAM4 forward
    NRZ_6G = "GMSL3-NRZ-6G"          # GMSL3 NRZ fallback
    GMSL2_6G = "GMSL2-6G"            # GMSL2 high rate
    GMSL2_3G = "GMSL2-3G"            # GMSL2 low rate
    GMSL1 = "GMSL1"                  # legacy GMSL1 fallback
    UNKNOWN = "unknown"


class LinkDirection(str, Enum):
    """Which half of the GMSL link a PRBS/EOM read targets.

    * ``FORWARD`` — high-speed serializer->deserializer video channel
      (GMSL3 = 12 Gbps PAM4).
    * ``REVERSE`` — low-speed deserializer->serializer control channel
      (187.5 Mbps NRZ).
    """
    FORWARD = "forward"
    REVERSE = "reverse"


class GmslPrbsPattern(str, Enum):
    """PRBS pattern for the in-system generator/checker. PRBS31 is the usual
    long-pattern stress; the GMSL on-die generator is commonly cited as PRBS24,
    which is intentionally *not* in the external-BERT pattern set
    (``instruments._BERT_PATTERNS``) — the correlation note lands in a later
    Sprint-4.1 commit."""
    PRBS7 = "PRBS7"
    PRBS9 = "PRBS9"
    PRBS15 = "PRBS15"
    PRBS23 = "PRBS23"
    PRBS24 = "PRBS24"
    PRBS31 = "PRBS31"


class SerDesRole(str, Enum):
    SERIALIZER = "serializer"        # camera-side (e.g. MAX96793)
    DESERIALIZER = "deserializer"    # SoC-side (e.g. MAX96792A)


# Verdict thresholds (overridable per-instance). PAM4 eyes are far tighter than
# NRZ, so these are GMSL-specific, not the PCIe retimer defaults.
DEFAULT_EOM_MV_MIN = 40.0            # worst PAM4 sub-eye vertical opening, mV
DEFAULT_EOM_UI_MIN = 0.20           # horizontal opening, UI
DEFAULT_BIST_DURATION_S = 1.0


@dataclass
class SerDesInfo:
    """Identity of the SerDes device (vendor/part/role/link count)."""
    vendor: str
    part_number: str
    role: SerDesRole
    serial: str = ""
    firmware: str = ""
    links: int = 1                  # MAX96793 ser = 1; MAX96792A deser = 2; quad = 4
    max_mode: GmslMode = GmslMode.PAM4_12G

    def to_dict(self) -> dict:
        return {"vendor": self.vendor, "part_number": self.part_number,
                "role": self.role.value, "serial": self.serial,
                "firmware": self.firmware, "links": self.links,
                "max_mode": self.max_mode.value}


@dataclass
class LinkLock:
    """Per-link lock state + the *negotiated* mode (not just a boolean)."""
    link: int
    locked: bool
    mode: GmslMode
    lock_time_ms: float | None = None    # time to lock from cold/relock, if measured

    def mode_ok(self, expected: GmslMode) -> bool:
        """True iff locked AND in the expected mode."""
        return self.locked and self.mode == expected

    def to_dict(self) -> dict:
        return {"link": self.link, "locked": self.locked, "mode": self.mode.value,
                "lock_time_ms": self.lock_time_ms}


@dataclass
class EomReading:
    """Eye-opening-monitor read after CTLE/DFE. PAM4 has THREE stacked eyes
    (upper/middle/lower); NRZ has one. ``vertical_mv`` holds one entry per eye."""
    link: int
    direction: LinkDirection
    mode: GmslMode
    vertical_mv: list[float]             # len 3 for PAM4, 1 for NRZ
    horizontal_ui: float

    @property
    def worst_vertical_mv(self) -> float:
        """The closing eye — the margin oracle for a PAM4 link."""
        return min(self.vertical_mv) if self.vertical_mv else 0.0

    def to_dict(self) -> dict:
        return {"link": self.link, "direction": self.direction.value,
                "mode": self.mode.value, "vertical_mv": list(self.vertical_mv),
                "worst_vertical_mv": self.worst_vertical_mv,
                "horizontal_ui": self.horizontal_ui}


@dataclass
class FecStats:
    """Reed-Solomon FEC counters. A rising ``corrected_symbols`` rate on a clean
    post-FEC link is the earliest degradation signal; any
    ``uncorrectable_blocks`` is an immediate fail."""
    link: int
    corrected_symbols: int
    corrected_codewords: int
    uncorrectable_blocks: int

    @property
    def clean(self) -> bool:
        return self.uncorrectable_blocks == 0

    def to_dict(self) -> dict:
        return {"link": self.link, "corrected_symbols": self.corrected_symbols,
                "corrected_codewords": self.corrected_codewords,
                "uncorrectable_blocks": self.uncorrectable_blocks,
                "clean": self.clean}


@dataclass
class PrbsResult:
    """One PRBS-BIST window on one direction. ``bits`` lets the BER-confidence
    math in ``computetest.ber`` turn (errors, bits) into a confidence bound in a
    later Sprint-4.1 commit."""
    direction: LinkDirection
    pattern: GmslPrbsPattern
    duration_s: float
    error_count: int
    bits: float
    locked: bool

    def to_dict(self) -> dict:
        return {"direction": self.direction.value, "pattern": self.pattern.value,
                "duration_s": self.duration_s, "error_count": self.error_count,
                "bits": self.bits, "locked": self.locked}


@dataclass
class ErrorCounters:
    """GMSL safety/diagnostic error counters (the inputs the ERRB pin NORs
    together). All are latched counters cleared by a register write on real
    silicon."""
    link: int
    decoding_errors: int = 0
    idle_errors: int = 0
    line_fault: bool = False
    video_crc_errors: int = 0

    @property
    def any_error(self) -> bool:
        return bool(self.decoding_errors or self.idle_errors
                    or self.line_fault or self.video_crc_errors)

    def to_dict(self) -> dict:
        return {"link": self.link, "decoding_errors": self.decoding_errors,
                "idle_errors": self.idle_errors, "line_fault": self.line_fault,
                "video_crc_errors": self.video_crc_errors,
                "any_error": self.any_error}


# ---------------------------------------------------------------------------
# SerDesLink ABC
# ---------------------------------------------------------------------------
class SerDesLink(abc.ABC):
    """Vendor-agnostic GMSL SerDes surface.

    Implementations: ``MockSerDes`` (deterministic, in-memory) and a future
    ADI-register backend. New parts subclass this and implement the surface.

    Subclass invariants:

    * Link indices are 0-based and bounded by ``info().links``.
    * Readers raise ``SerDesError`` on an unreadable register / unlocked link —
      never silently return zeros.
    * ``read_eom`` returns one ``vertical_mv`` entry per PAM4 sub-eye (3) or 1
      for NRZ.
    """

    def __init__(self) -> None:
        self._eom_mv_min = DEFAULT_EOM_MV_MIN
        self._eom_ui_min = DEFAULT_EOM_UI_MIN

    # --- identity / config ------------------------------------------------
    @abc.abstractmethod
    def info(self) -> SerDesInfo:
        """Vendor / part / role / link count."""

    def set_eom_thresholds(self, *, mv_min: float | None = None,
                           ui_min: float | None = None) -> None:
        """Override per-instance EOM verdict thresholds; skip a kwarg to keep
        the module default."""
        if mv_min is not None:
            self._eom_mv_min = mv_min
        if ui_min is not None:
            self._eom_ui_min = ui_min

    # --- lock / mode ------------------------------------------------------
    @abc.abstractmethod
    def lock_status(self) -> list[LinkLock]:
        """Per-link lock + negotiated mode (one entry per ``info().links``)."""

    def negotiated_mode(self, link: int) -> GmslMode:
        """The negotiated mode of one link (UNKNOWN if not locked)."""
        for ll in self.lock_status():
            if ll.link == link:
                return ll.mode if ll.locked else GmslMode.UNKNOWN
        raise SerDesError(f"link {link} not present")

    @abc.abstractmethod
    def force_relock(self, link: int | None = None) -> None:
        """Force a re-train of one link (or all links if ``None``)."""

    # --- margin / FEC / EOM ----------------------------------------------
    @abc.abstractmethod
    def read_eom(self, link: int,
                 direction: LinkDirection = LinkDirection.FORWARD) -> EomReading:
        """Eye-opening-monitor read for one link/direction (post CTLE/DFE)."""

    @abc.abstractmethod
    def run_prbs_bist(self, direction: LinkDirection = LinkDirection.FORWARD,
                      pattern: GmslPrbsPattern = GmslPrbsPattern.PRBS31,
                      duration_s: float = DEFAULT_BIST_DURATION_S,
                      link: int = 0) -> PrbsResult:
        """Run an in-system PRBS window in one direction; return errors + bits."""

    @abc.abstractmethod
    def read_fec_stats(self, link: int) -> FecStats:
        """Read the Reed-Solomon FEC counters for one link."""

    # --- safety / loopback -----------------------------------------------
    @abc.abstractmethod
    def read_error_counters(self, link: int) -> ErrorCounters:
        """Read the latched safety/diagnostic error counters for one link."""

    @abc.abstractmethod
    def errb_asserted(self) -> bool:
        """True iff the ERRB pin is currently asserted (any monitored fault)."""

    @abc.abstractmethod
    def clear_errors(self, link: int | None = None) -> None:
        """Clear the latched safety/diagnostic error counters (de-asserts ERRB)."""

    @abc.abstractmethod
    def set_loopback(self, enable: bool, *,
                     direction: LinkDirection = LinkDirection.FORWARD) -> None:
        """Enable/disable internal loopback for fault isolation."""

    # --- concrete helpers -------------------------------------------------
    def eom_verdict(self, eom: EomReading) -> bool:
        """True iff an EOM read clears the program's mV/UI thresholds."""
        return (eom.worst_vertical_mv >= self._eom_mv_min
                and eom.horizontal_ui >= self._eom_ui_min)

    def all_locked(self) -> bool:
        """True iff every link is locked."""
        locks = self.lock_status()
        return bool(locks) and all(ll.locked for ll in locks)

    def all_in_mode(self, expected: GmslMode) -> bool:
        """True iff every link is locked AND in the expected mode."""
        locks = self.lock_status()
        return bool(locks) and all(ll.mode_ok(expected) for ll in locks)


# ---------------------------------------------------------------------------
# MockSerDes (in-memory, deterministic)
# ---------------------------------------------------------------------------
class MockSerDes(SerDesLink):
    """In-memory GMSL SerDes for tests/CI.

    EOM/FEC/PRBS telemetry are deterministic functions of (serial, link) so a
    fixture can stand in for a real part. The ``injected_*`` knobs drive the
    error paths; ``injected_mode`` models a degraded-but-locked link — the GMSL3
    defect the mode-aware lock check is built to catch.
    """

    def __init__(self, *, vendor: str = "Analog Devices",
                 part_number: str = "MAX96792A",
                 role: SerDesRole = SerDesRole.DESERIALIZER,
                 serial: str = "MOCKGMSL01", firmware: str = "0.1.0",
                 links: int = 2, max_mode: GmslMode = GmslMode.PAM4_12G,
                 injected_locked: bool = True,
                 injected_mode: GmslMode | None = None,
                 injected_eye_mv: float = 70.0,
                 injected_eye_ui: float = 0.30,
                 injected_prbs_errors: int = 0,
                 injected_fec_corrected: int = 0,
                 injected_fec_uncorrectable: int = 0,
                 injected_error_counters: ErrorCounters | None = None) -> None:
        super().__init__()
        self._info = SerDesInfo(vendor=vendor, part_number=part_number, role=role,
                                serial=serial, firmware=firmware, links=links,
                                max_mode=max_mode)
        self._locked = injected_locked
        self._mode = injected_mode if injected_mode is not None else max_mode
        self._eye_mv = injected_eye_mv
        self._eye_ui = injected_eye_ui
        self._prbs_errors = injected_prbs_errors
        self._fec_corrected = injected_fec_corrected
        self._fec_uncorrectable = injected_fec_uncorrectable
        self._injected_counters = injected_error_counters
        self._loopback: dict[LinkDirection, bool] = {
            LinkDirection.FORWARD: False, LinkDirection.REVERSE: False}
        self._relocks = 0

    def info(self) -> SerDesInfo:
        return self._info

    def _check_link(self, link: int) -> None:
        if link < 0 or link >= self._info.links:
            raise SerDesError(
                f"link {link} out of range [0, {self._info.links - 1}]")

    def _jitter(self, link: int) -> float:
        """Deterministic per-link jitter in [-0.06, +0.06] seeded by (serial, link)."""
        h = hashlib.sha256(f"{self._info.serial}:{link}".encode()).digest()
        return ((h[0] / 255.0) - 0.5) * 0.12

    def _is_pam4(self) -> bool:
        return self._mode == GmslMode.PAM4_12G

    def lock_status(self) -> list[LinkLock]:
        out: list[LinkLock] = []
        for link in range(self._info.links):
            mode = self._mode if self._locked else GmslMode.UNKNOWN
            lock_time = round(8.0 + self._jitter(link) * 10.0, 2) if self._locked else None
            out.append(LinkLock(link=link, locked=self._locked, mode=mode,
                                lock_time_ms=lock_time))
        return out

    def force_relock(self, link: int | None = None) -> None:
        if link is not None:
            self._check_link(link)
        self._relocks += 1

    @property
    def relock_count(self) -> int:
        return self._relocks

    def read_eom(self, link: int,
                 direction: LinkDirection = LinkDirection.FORWARD) -> EomReading:
        self._check_link(link)
        if not self._locked:
            raise SerDesError(f"link {link} not locked; EOM unavailable")
        jitter = self._jitter(link)
        base = max(0.0, self._eye_mv + jitter * 25.0)
        # PAM4 forward => 3 stacked sub-eyes (outer eyes open wider than the
        # middle); NRZ reverse / GMSL2 forward => 1 eye.
        if direction == LinkDirection.FORWARD and self._is_pam4():
            vertical = [round(base * 1.05, 2), round(base * 0.9, 2),
                        round(base * 1.05, 2)]
        else:
            vertical = [round(base, 2)]
        return EomReading(link=link, direction=direction, mode=self._mode,
                          vertical_mv=vertical,
                          horizontal_ui=round(max(0.0, self._eye_ui + jitter * 0.2), 4))

    def run_prbs_bist(self, direction: LinkDirection = LinkDirection.FORWARD,
                      pattern: GmslPrbsPattern = GmslPrbsPattern.PRBS31,
                      duration_s: float = DEFAULT_BIST_DURATION_S,
                      link: int = 0) -> PrbsResult:
        self._check_link(link)
        if duration_s <= 0:
            raise SerDesError(f"BIST duration must be > 0; got {duration_s}")
        # Forward 12 Gbps PAM4 (NRZ fallback 6 Gbps) vs reverse 187.5 Mbps NRZ.
        if direction == LinkDirection.REVERSE:
            rate = 187.5e6
        else:
            rate = 12.0e9 if self._is_pam4() else 6.0e9
        bits = rate * duration_s
        return PrbsResult(direction=direction, pattern=pattern,
                          duration_s=duration_s, error_count=self._prbs_errors,
                          bits=bits, locked=self._prbs_errors < 1_000_000)

    def read_fec_stats(self, link: int) -> FecStats:
        self._check_link(link)
        # The mock models one corrected symbol per codeword; a real backend
        # reports the device's own corrected-symbol and codeword tallies.
        return FecStats(link=link, corrected_symbols=self._fec_corrected,
                        corrected_codewords=self._fec_corrected,
                        uncorrectable_blocks=self._fec_uncorrectable)

    def read_error_counters(self, link: int) -> ErrorCounters:
        self._check_link(link)
        if self._injected_counters is not None:
            # Re-stamp the link index so callers always get a self-consistent record.
            c = self._injected_counters
            return ErrorCounters(link=link, decoding_errors=c.decoding_errors,
                                 idle_errors=c.idle_errors, line_fault=c.line_fault,
                                 video_crc_errors=c.video_crc_errors)
        return ErrorCounters(link=link)

    def errb_asserted(self) -> bool:
        # ERRB is the NOR of the monitored faults across all links.
        return any(self.read_error_counters(link).any_error
                   for link in range(self._info.links))

    def clear_errors(self, link: int | None = None) -> None:
        if link is not None:
            self._check_link(link)
        self._injected_counters = None

    def inject_error_counters(self, counters: ErrorCounters) -> None:
        """Test/stimulus hook (mock only): make the device report these latched
        counters, modelling a physical fault tripping the safety mechanism."""
        self._injected_counters = counters

    def set_loopback(self, enable: bool, *,
                     direction: LinkDirection = LinkDirection.FORWARD) -> None:
        self._loopback[direction] = enable

    def loopback_state(self, direction: LinkDirection = LinkDirection.FORWARD) -> bool:
        return self._loopback[direction]
