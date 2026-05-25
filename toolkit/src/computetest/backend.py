"""
Hardware-access backend for the compute-test toolkit.

Everything above this module (aer, ber, bert, diagnostics, the interface checks)
talks ONLY to the ``Backend`` interface defined here, so the exact same code runs:

  * ``RealBackend`` — reads real PCIe sysfs + config space on a Linux test station.
  * ``MockBackend`` — simulates one or more PCIe devices with an *injectable* bit
    error rate, so the whole toolkit runs and unit-tests on a macOS laptop with no
    hardware attached. This is what lets you demo the BERT on day one.

``select_backend()`` auto-picks Real on a Linux box that actually has ``/sys/bus/pci``
and Mock everywhere else; ``COMPUTETEST_BACKEND=mock|real`` forces the choice.

Design note on counting PCIe errors
------------------------------------
The AER *status* registers are latches: a bit means "at least one error of this
type happened since you last cleared it" — not a count. To turn that into a count
you must, in a tight loop, read the status, and the instant a bit is set, record it
and **write-1-to-clear** it so the next error can latch. If you clear too slowly,
multiple errors collapse into one latched bit and you undercount. That is exactly
why clearing must be fast and must clear only the set bits. The MockBackend models
this latch-and-undercount behavior faithfully, so tests exercise the real dynamics.
"""
from __future__ import annotations

import abc
import glob
import math
import os
import random
import struct
import time
from dataclasses import dataclass, field

# --- PCIe physical-layer facts (used by the mock to accrue bits over time) ------

# Raw transfer rate per lane, in GT/s, indexed by the LnkSta speed code (1..6).
LINK_SPEED_GTPS: dict[int, float] = {1: 2.5, 2: 5.0, 3: 8.0, 4: 16.0, 5: 32.0, 6: 64.0}

# Line-coding efficiency: 8b/10b for Gen1/2, 128b/130b for Gen3-5.
def _encoding_efficiency(speed_code: int) -> float:
    if speed_code <= 2:
        return 8.0 / 10.0
    if speed_code <= 5:
        return 128.0 / 130.0
    return 242.0 / 256.0  # Gen6 (PAM4 + FLIT/FEC), approximate payload efficiency


def link_bits_per_second(speed_code: int, width: int) -> float:
    """Approximate *payload* bits/s for a link at the given speed code and width."""
    gtps = LINK_SPEED_GTPS.get(speed_code, 0.0)
    return gtps * 1e9 * width * _encoding_efficiency(speed_code)


# --- AER register offsets (relative to the AER extended-capability base) --------
AER_UNCORR_STATUS = 0x04
AER_UNCORR_MASK = 0x08
AER_UNCORR_SEVERITY = 0x0C
AER_CORR_STATUS = 0x10
AER_CORR_MASK = 0x14
AER_CAP_CONTROL = 0x18
AER_HEADER_LOG = 0x1C

# Extended capability IDs we care about.
ECAP_AER = 0x0001
ECAP_SECONDARY_PCIE = 0x0019  # Gen3+ lane equalization control
ECAP_LANE_MARGINING = 0x0027  # receiver lane margining (Gen4+)

# Legacy PCIe capability (holds Link Control / Link Status).
CAP_PCIE = 0x10
PCIE_LINK_CONTROL = 0x10  # relative to the PCIe capability base
PCIE_LINK_STATUS = 0x12   # relative to the PCIe capability base; bit 11 = link training


@dataclass
class PciDevice:
    """A snapshot of one PCIe function as the backend sees it."""

    bdf: str
    vendor_id: int
    device_id: int
    class_code: int
    current_link_speed: int  # LnkSta speed code (1..6)
    current_link_width: int
    max_link_speed: int
    max_link_width: int
    driver: str | None = None

    @property
    def speed_str(self) -> str:
        gtps = LINK_SPEED_GTPS.get(self.current_link_speed)
        return f"{gtps:g} GT/s" if gtps else f"code {self.current_link_speed}"

    @property
    def vendor_name(self) -> str:
        return {0x10DE: "NVIDIA", 0x144D: "Samsung", 0x8086: "Intel",
                0x1B36: "Zoox-custom(example)"}.get(self.vendor_id, f"{self.vendor_id:#06x}")


class Backend(abc.ABC):
    """Abstract hardware access. Higher layers depend only on this."""

    @abc.abstractmethod
    def list_devices(self) -> list[str]:
        """Return all PCIe BDFs the backend can see (e.g. '0000:03:00.0')."""

    @abc.abstractmethod
    def get_device(self, bdf: str) -> PciDevice:
        """Return a PciDevice snapshot for one BDF."""

    @abc.abstractmethod
    def read_config(self, bdf: str, offset: int, size: int = 4) -> int:
        """Read 1/2/4 bytes of config space at ``offset`` (little-endian int)."""

    @abc.abstractmethod
    def write_config(self, bdf: str, offset: int, value: int, size: int = 4) -> None:
        """Write 1/2/4 bytes of config space at ``offset``."""

    @abc.abstractmethod
    def find_ext_cap(self, bdf: str, cap_id: int) -> int | None:
        """Return the config-space offset of an extended capability, or None."""

    @abc.abstractmethod
    def read_link_status(self, bdf: str) -> tuple[int, int, bool]:
        """Return (speed_code, width, link_training_active) from the PCIe cap.

        ``link_training_active`` (Link Status bit 11) flicking true during a run
        means the link entered Recovery and retrained — frequent = marginal SI.
        """

    def is_mock(self) -> bool:
        return isinstance(self, MockBackend)


# ----------------------------------------------------------------------------- #
# Real Linux backend
# ----------------------------------------------------------------------------- #
class RealBackend(Backend):
    """Reads real hardware via /sys/bus/pci. Requires root for config-space writes."""

    SYS = "/sys/bus/pci/devices"

    def list_devices(self) -> list[str]:
        return sorted(os.path.basename(p) for p in glob.glob(f"{self.SYS}/*"))

    def _attr(self, bdf: str, name: str) -> str | None:
        try:
            with open(f"{self.SYS}/{bdf}/{name}") as fh:
                return fh.read().strip()
        except OSError:
            return None

    def get_device(self, bdf: str) -> PciDevice:
        def hexattr(name: str) -> int:
            v = self._attr(bdf, name)
            return int(v, 16) if v else 0

        def speed_code(name: str) -> int:
            # sysfs gives e.g. "16.0 GT/s"; map back to a code.
            v = self._attr(bdf, name) or ""
            try:
                gtps = float(v.split()[0])
            except (ValueError, IndexError):
                return 0
            for code, g in LINK_SPEED_GTPS.items():
                if abs(g - gtps) < 0.01:
                    return code
            return 0

        driver = None
        link = os.path.join(self.SYS, bdf, "driver")
        if os.path.islink(link):
            driver = os.path.basename(os.readlink(link))

        return PciDevice(
            bdf=bdf,
            vendor_id=hexattr("vendor"),
            device_id=hexattr("device"),
            class_code=hexattr("class"),
            current_link_speed=speed_code("current_link_speed"),
            current_link_width=int(self._attr(bdf, "current_link_width") or 0),
            max_link_speed=speed_code("max_link_speed"),
            max_link_width=int(self._attr(bdf, "max_link_width") or 0),
            driver=driver,
        )

    def read_config(self, bdf: str, offset: int, size: int = 4) -> int:
        with open(f"{self.SYS}/{bdf}/config", "rb") as fh:
            fh.seek(offset)
            data = fh.read(size)
        return int.from_bytes(data, "little")

    def write_config(self, bdf: str, offset: int, value: int, size: int = 4) -> None:
        data = value.to_bytes(size, "little")
        # Opening config O_WRONLY/RDWR requires CAP_SYS_ADMIN (root) on Linux.
        fd = os.open(f"{self.SYS}/{bdf}/config", os.O_RDWR)
        try:
            os.lseek(fd, offset, os.SEEK_SET)
            os.write(fd, data)
        finally:
            os.close(fd)

    def find_ext_cap(self, bdf: str, cap_id: int) -> int | None:
        # Extended capabilities form a linked list starting at 0x100.
        offset = 0x100
        seen = set()
        while offset and offset not in seen:
            seen.add(offset)
            header = self.read_config(bdf, offset, 4)
            if header == 0 or header == 0xFFFFFFFF:
                return None
            this_id = header & 0xFFFF
            if this_id == cap_id:
                return offset
            offset = (header >> 20) & 0xFFF  # next-capability offset
        return None

    def find_cap(self, bdf: str, cap_id: int) -> int | None:
        """Walk the legacy capability list (from the 0x34 pointer) for cap_id."""
        ptr = self.read_config(bdf, 0x34, 1) & 0xFC
        seen = set()
        while ptr and ptr not in seen:
            seen.add(ptr)
            this_id = self.read_config(bdf, ptr, 1)
            if this_id == cap_id:
                return ptr
            ptr = self.read_config(bdf, ptr + 1, 1) & 0xFC
        return None

    def read_link_status(self, bdf: str) -> tuple[int, int, bool]:
        cap = self.find_cap(bdf, CAP_PCIE)
        if cap is None:
            d = self.get_device(bdf)  # fall back to sysfs
            return d.current_link_speed, d.current_link_width, False
        ls = self.read_config(bdf, cap + PCIE_LINK_STATUS, 2)
        return ls & 0xF, (ls >> 4) & 0x3F, bool((ls >> 11) & 1)


# ----------------------------------------------------------------------------- #
# Mock backend — simulates a board with injectable errors
# ----------------------------------------------------------------------------- #
# Correctable-status bit positions the mock can latch (see aer.py for the full map).
_COR_RECEIVER_ERROR = 1 << 0
_COR_BAD_TLP = 1 << 6
_COR_REPLAY_TIMER = 1 << 12


@dataclass
class MockDevice:
    """A simulated PCIe device. ``injected_ber`` drives correctable-error latching."""

    bdf: str
    vendor_id: int = 0x10DE
    device_id: int = 0x2204
    class_code: int = 0x030000
    link_speed: int = 4          # Gen4
    link_width: int = 16
    max_link_speed: int = 4
    max_link_width: int = 16
    driver: str | None = "nvidia"
    injected_ber: float = 0.0    # set > 0 to simulate a marginal link
    inject_uncorr_bit: int = 0   # >0 = a persistent uncorrectable fault that re-latches
    inject_retrains_per_sec: float = 0.0   # >0 = link keeps re-entering Recovery
    optimal_preset: int = 4      # TX preset that minimizes errors (for the eq sweep)
    # Internal latch/accounting state:
    _cor_status: int = 0
    _uncor_status: int = 0
    _accrual_t: float = field(default_factory=time.monotonic)   # window-baseline for errors
    _last_ls_poll: float = field(default_factory=time.monotonic)
    _true_errors: int = 0        # ground-truth PHYSICAL error count (>= observable events)
    _ext_caps: dict[int, int] = field(default_factory=lambda: {
        ECAP_AER: 0x100, ECAP_SECONDARY_PCIE: 0x140, ECAP_LANE_MARGINING: 0x180})

    def _accrue_and_latch(self, rng: random.Random) -> None:
        """Model real PCIe AER physics: errors arrive as a Poisson process; the status
        register is a LATCH (set if >=1 error occurred), not a counter. Each read
        consumes its window [last read, now] so windows never overlap (no double count).
        A fast poller observes ~every error (windows hold 0/1); a slow poller undercounts
        (windows hold >1 but still latch one bit) — exactly the real undercounting."""
        if self.injected_ber <= 0:
            return
        now = time.monotonic()
        dt = now - self._accrual_t
        self._accrual_t = now                       # consume this window
        lam = link_bits_per_second(self.link_speed, self.link_width) * dt * self.injected_ber
        if lam <= 0:
            return
        n = _poisson(rng, lam)
        if n > 0:
            self._true_errors += n                  # physical errors in this window
            self._cor_status |= _COR_BAD_TLP        # latch ONE representative bit


class MockBackend(Backend):
    """In-memory simulation. Construct with devices, or use the default sample board."""

    def __init__(self, devices: list[MockDevice] | None = None, seed: int = 1234):
        self._rng = random.Random(seed)
        if devices is None:
            devices = self.sample_board()
        self._devs: dict[str, MockDevice] = {d.bdf: d for d in devices}

    @staticmethod
    def sample_board() -> list[MockDevice]:
        """A representative compute board: 2 GPUs, 2 NVMe, a custom card."""
        return [
            MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, "nvidia"),
            MockDevice("0000:04:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, "nvidia",
                       injected_ber=5e-9),  # one marginal GPU link (fails the BERT in demos)
            MockDevice("0000:05:00.0", 0x144D, 0xA80A, 0x010802, 4, 4, 4, 4, "nvme"),
            MockDevice("0000:06:00.0", 0x144D, 0xA80A, 0x010802, 4, 4, 4, 4, "nvme"),
            MockDevice("0000:07:00.0", 0x1B36, 0x0010, 0x088000, 3, 8, 4, 8, "zoox_custom"),
        ]

    def _dev(self, bdf: str) -> MockDevice:
        if bdf not in self._devs:
            raise KeyError(f"no such mock device: {bdf}")
        return self._devs[bdf]

    def list_devices(self) -> list[str]:
        return sorted(self._devs)

    def get_device(self, bdf: str) -> PciDevice:
        d = self._dev(bdf)
        return PciDevice(d.bdf, d.vendor_id, d.device_id, d.class_code,
                         d.link_speed, d.link_width, d.max_link_speed, d.max_link_width,
                         d.driver)

    def find_ext_cap(self, bdf: str, cap_id: int) -> int | None:
        return self._dev(bdf)._ext_caps.get(cap_id)

    def read_link_status(self, bdf: str) -> tuple[int, int, bool]:
        d = self._dev(bdf)
        training = False
        if d.inject_retrains_per_sec > 0:
            now = time.monotonic()
            lam = d.inject_retrains_per_sec * (now - d._last_ls_poll)
            d._last_ls_poll = now
            training = _poisson(self._rng, lam) > 0
        return d.link_speed, d.link_width, training

    def read_config(self, bdf: str, offset: int, size: int = 4) -> int:
        d = self._dev(bdf)
        aer = d._ext_caps.get(ECAP_AER)
        if aer is not None and offset == aer + AER_CORR_STATUS:
            d._accrue_and_latch(self._rng)
            return d._cor_status & _mask(size)
        if aer is not None and offset == aer + AER_UNCORR_STATUS:
            if d.inject_uncorr_bit:           # a persistent fault re-latches each read
                d._uncor_status |= (1 << d.inject_uncorr_bit)
            return d._uncor_status & _mask(size)
        if aer is not None and offset == aer:
            return ECAP_AER  # capability header (id in low bits; next=0)
        return 0

    def write_config(self, bdf: str, offset: int, value: int, size: int = 4) -> None:
        d = self._dev(bdf)
        aer = d._ext_caps.get(ECAP_AER)
        if aer is not None and offset == aer + AER_CORR_STATUS:
            # Write-1-to-clear: clear exactly the bits set in ``value``.
            d._cor_status &= ~(value & _mask(size))
            d._accrual_t = time.monotonic()      # restart the error-accrual window
        elif aer is not None and offset == aer + AER_UNCORR_STATUS:
            d._uncor_status &= ~(value & _mask(size))

    # Test helpers (not part of the Backend interface) --------------------------
    def true_error_count(self, bdf: str) -> int:
        return self._dev(bdf)._true_errors

    def inject_uncorrectable(self, bdf: str, bit: int) -> None:
        """Simulate a persistent uncorrectable fault (re-latches after each clear)."""
        self._dev(bdf).inject_uncorr_bit = bit


def _mask(size: int) -> int:
    return (1 << (8 * size)) - 1


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler (fine for the modest lambdas the mock produces)."""
    if lam < 30:
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            k += 1
            p *= rng.random()
            if p <= L:
                return k - 1
    # Normal approximation for large lambda.
    return max(0, round(rng.gauss(lam, math.sqrt(lam))))


# ----------------------------------------------------------------------------- #
def select_backend() -> Backend:
    """Pick Real on a capable Linux box, Mock otherwise. Override via env var."""
    forced = os.environ.get("COMPUTETEST_BACKEND", "").lower()
    if forced == "mock":
        return MockBackend()
    if forced == "real":
        return RealBackend()
    if os.path.isdir(RealBackend.SYS):
        return RealBackend()
    return MockBackend()


def mock_mode() -> bool:
    """Whether the CLI-wrapping interface checks (nvme/gpu/gmsl/eth/can) should use
    simulated data. Same policy as select_backend(): real on a capable Linux box,
    mock otherwise; force with COMPUTETEST_BACKEND=mock|real."""
    forced = os.environ.get("COMPUTETEST_BACKEND", "").lower()
    if forced == "mock":
        return True
    if forced == "real":
        return False
    return not os.path.isdir(RealBackend.SYS)
