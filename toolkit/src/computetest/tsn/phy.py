"""
Automotive-Ethernet PHY abstraction (Sprint 4.2).

Generalizes the retimer / GMSL-SerDes telemetry pattern to single-pair
automotive Ethernet PHYs (100BASE-T1 / 1000BASE-T1, e.g. TI DP83TG721, Marvell
88Q2221): the signal-quality index (SQI), link-up time, master/slave role,
cable TDR, and a PRBS BIST. The TSN feature checks (gPTP, Qbv, Clause-99
preemption, FRER) sit on top in the other ``tsn`` modules; ``ethernet.py``'s
``check_ethernet`` remains the OS-level (ethtool) liveness check.

One vendor-agnostic contract (``EthPhy``) with a deterministic ``MockPhy`` for
tests/CI, and room for a real MDIO/register backend behind the same surface.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum


class PhyError(RuntimeError):
    """An automotive-Ethernet PHY fault — register read, link not up, BIST
    timeout. One exception type so callers ``except PhyError`` regardless of
    which vendor backend failed (mirrors ``RetimerError`` / ``SerDesError``)."""


class MasterSlave(str, Enum):
    """1000BASE-T1 / 100BASE-T1 master-slave timing role. A mismatched pair
    (both master or both slave) never links, so the role is asserted."""
    MASTER = "master"
    SLAVE = "slave"
    UNKNOWN = "unknown"


class PhyPrbsPattern(str, Enum):
    PRBS7 = "PRBS7"
    PRBS9 = "PRBS9"
    PRBS15 = "PRBS15"
    PRBS31 = "PRBS31"


# SQI is a 0-7 index (OPEN Alliance); >= 5 is the usual "good link" floor.
DEFAULT_SQI_MIN = 5
DEFAULT_MAX_LINKUP_MS = 100.0       # automotive link-up time budget
DEFAULT_PHY_BIST_DURATION_S = 1.0


@dataclass
class PhyInfo:
    """Identity of the PHY (vendor / part / speed / role)."""
    vendor: str
    part_number: str
    speed_mbps: int                 # 100 or 1000 (single-pair automotive)
    role: MasterSlave
    serial: str = ""

    def to_dict(self) -> dict:
        return {"vendor": self.vendor, "part_number": self.part_number,
                "speed_mbps": self.speed_mbps, "role": self.role.value,
                "serial": self.serial}


@dataclass
class TdrResult:
    """Cable TDR: status + per-pair faults (open/short + distance), the same
    shape ``ethernet.py``'s ethtool TDR emits."""
    status: str                     # "ok" | "fault" | "skipped"
    faults: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status != "fault"

    def to_dict(self) -> dict:
        return {"status": self.status, "faults": list(self.faults), "ok": self.ok}


@dataclass
class PhyPrbsResult:
    """One PHY PRBS-BIST window."""
    pattern: PhyPrbsPattern
    duration_s: float
    error_count: int
    bits: float
    locked: bool

    def to_dict(self) -> dict:
        return {"pattern": self.pattern.value, "duration_s": self.duration_s,
                "error_count": self.error_count, "bits": self.bits,
                "locked": self.locked}


class EthPhy(abc.ABC):
    """Vendor-agnostic automotive-Ethernet PHY surface.

    Implementations: ``MockPhy`` (deterministic) and a future MDIO/register
    backend. Readers raise ``PhyError`` on an unreadable register — never
    silently return zeros.
    """

    def __init__(self) -> None:
        self._sqi_min = DEFAULT_SQI_MIN
        self._max_linkup_ms = DEFAULT_MAX_LINKUP_MS

    @abc.abstractmethod
    def info(self) -> PhyInfo:
        """Vendor / part / speed / role."""

    @abc.abstractmethod
    def read_sqi(self) -> int:
        """Signal-quality index, 0-7 (OPEN Alliance)."""

    @abc.abstractmethod
    def read_link_up_time(self) -> float:
        """Time from PHY enable to link-up, in milliseconds."""

    @abc.abstractmethod
    def master_slave_role(self) -> MasterSlave:
        """The negotiated/configured master-slave timing role."""

    @abc.abstractmethod
    def run_tdr(self) -> TdrResult:
        """Run a cable TDR (open/short + distance-to-fault)."""

    @abc.abstractmethod
    def run_prbs_bist(self, pattern: PhyPrbsPattern = PhyPrbsPattern.PRBS31,
                      duration_s: float = DEFAULT_PHY_BIST_DURATION_S) -> PhyPrbsResult:
        """Run a PRBS BIST window; return errors + bits."""

    # --- concrete helpers -------------------------------------------------
    def set_thresholds(self, *, sqi_min: int | None = None,
                       max_linkup_ms: float | None = None) -> None:
        """Override per-instance SQI / link-up-time thresholds."""
        if sqi_min is not None:
            self._sqi_min = sqi_min
        if max_linkup_ms is not None:
            self._max_linkup_ms = max_linkup_ms

    def sqi_ok(self) -> bool:
        """True iff SQI clears the program's floor."""
        return self.read_sqi() >= self._sqi_min

    def linkup_ok(self) -> bool:
        """True iff link-up time is within the budget."""
        return self.read_link_up_time() <= self._max_linkup_ms


class MockPhy(EthPhy):
    """Deterministic in-memory automotive-Ethernet PHY for tests/CI."""

    def __init__(self, *, vendor: str = "Texas Instruments",
                 part_number: str = "DP83TG721", speed_mbps: int = 1000,
                 role: MasterSlave = MasterSlave.MASTER, serial: str = "MOCKPHY01",
                 injected_sqi: int = 7, injected_linkup_ms: float = 20.0,
                 injected_prbs_errors: int = 0,
                 injected_tdr_fault: bool = False) -> None:
        super().__init__()
        self._info = PhyInfo(vendor=vendor, part_number=part_number,
                             speed_mbps=speed_mbps, role=role, serial=serial)
        self._sqi = injected_sqi
        self._linkup_ms = injected_linkup_ms
        self._prbs_errors = injected_prbs_errors
        self._tdr_fault = injected_tdr_fault

    def info(self) -> PhyInfo:
        return self._info

    def read_sqi(self) -> int:
        if not 0 <= self._sqi <= 7:
            raise PhyError(f"SQI out of range [0, 7]: {self._sqi}")
        return self._sqi

    def read_link_up_time(self) -> float:
        return self._linkup_ms

    def master_slave_role(self) -> MasterSlave:
        return self._info.role

    def run_tdr(self) -> TdrResult:
        if self._tdr_fault:
            return TdrResult(status="fault",
                             faults=[{"pair": "A", "code": "open", "distance_m": 3.5}])
        return TdrResult(status="ok", faults=[])

    def run_prbs_bist(self, pattern: PhyPrbsPattern = PhyPrbsPattern.PRBS31,
                      duration_s: float = DEFAULT_PHY_BIST_DURATION_S) -> PhyPrbsResult:
        if duration_s <= 0:
            raise PhyError(f"BIST duration must be > 0; got {duration_s}")
        bits = self._info.speed_mbps * 1e6 * duration_s
        return PhyPrbsResult(pattern=pattern, duration_s=duration_s,
                             error_count=self._prbs_errors, bits=bits,
                             locked=self._prbs_errors < 1_000_000)
