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
import re
import time
from dataclasses import dataclass, field

_BDF_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]$")

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

# Legacy PCIe capability (holds Device + Link status/control).
CAP_PCIE = 0x10
PCIE_DEV_CONTROL = 0x08   # relative to the PCIe capability base
PCIE_DEV_STATUS = 0x0A    # Device Status: error-detected bits, present on EVERY PCIe fn
PCIE_LINK_CONTROL = 0x10
PCIE_LINK_STATUS = 0x12   # current speed/width + training + bandwidth-change latches
PCIE_CAP_FLAGS = 0x02     # PCI Express Capabilities Register; Device/Port Type in bits 7:4

# PCIe Device/Port Type — tells which side a port's receiver faces, i.e. which link its
# AER reports, and which ports are the link-owning downstream ports (for downgrade checks).
PORT_ENDPOINT = 0x0
PORT_ROOT = 0x4
PORT_SWITCH_UPSTREAM = 0x5
PORT_SWITCH_DOWNSTREAM = 0x6
# Receiver faces the leaf; these ports own the link below them.
DOWNSTREAM_PORTS = (PORT_ROOT, PORT_SWITCH_DOWNSTREAM)

# Device Status error bits (W1C) — the no-AER fallback error source.
DEVSTA_CORR = 1 << 0      # Correctable Error Detected
DEVSTA_NONFATAL = 1 << 1  # Non-Fatal Error Detected
DEVSTA_FATAL = 1 << 2     # Fatal Error Detected
DEVSTA_UR = 1 << 3        # Unsupported Request Detected

# Link Status bits.
LNKSTA_TRAINING = 1 << 11  # link is retraining (entered Recovery)
LNKSTA_DLLLA = 1 << 13     # Data Link Layer Link Active
LNKSTA_LBMS = 1 << 14      # Link Bandwidth Management Status (W1C) — a speed/width change
LNKSTA_LABS = 1 << 15      # Link Autonomous Bandwidth Status (W1C) — reliability downgrade


@dataclass
class LinkStatus:
    """A decoded PCIe Link Status read. ``bw_changed``/``autonomous_bw`` are W1C latches
    that fire on any speed/width change since the last clear — they catch a transient
    downgrade that recovered before the final snapshot."""
    speed: int
    width: int
    training: bool = False        # bit 11 — entered Recovery
    bw_changed: bool = False      # bit 14 LBMS — bandwidth (speed/width) changed
    autonomous_bw: bool = False   # bit 15 LABS — link downgraded itself for reliability
    dl_active: bool = True        # bit 13 DLLLA


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

    def to_dict(self) -> dict:
        """Serializable view that matches the human listing: includes the derived
        ``vendor_name``/``speed_str`` (plain ``vars()`` would drop these properties)."""
        return {
            "bdf": self.bdf, "vendor_id": self.vendor_id, "device_id": self.device_id,
            "vendor_name": self.vendor_name, "class_code": self.class_code,
            "current_link_speed": self.current_link_speed,
            "current_link_width": self.current_link_width, "speed_str": self.speed_str,
            "max_link_speed": self.max_link_speed, "max_link_width": self.max_link_width,
            "driver": self.driver,
        }


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
    def read_link_status(self, bdf: str) -> LinkStatus:
        """Read and decode the PCIe Link Status register."""

    @abc.abstractmethod
    def read_device_status(self, bdf: str) -> int | None:
        """Raw Device Status register (PCIe cap +0x0A), or None if no PCIe capability.
        The no-AER fallback error source: bit0 correctable, bits1/2 non-fatal/fatal."""

    @abc.abstractmethod
    def clear_device_status(self, bdf: str, mask: int = 0xF) -> None:
        """Write-1-to-clear the Device Status error bits."""

    @abc.abstractmethod
    def clear_link_bw_status(self, bdf: str) -> None:
        """Write-1-to-clear the LBMS/LABS bandwidth-change latches in Link Status."""

    def set_exercising(self, bdf: str, active: bool) -> None:  # noqa: B027
        """Tell the backend whether the link is being exercised (driving traffic).
        On real hardware the stress workload is external, so this is a deliberate
        no-op default; the mock overrides it to gate error generation, enabling a
        true idle baseline (begin/end). Intentionally non-abstract."""

    def link_chain(self, bdf: str) -> list[str]:
        """Return the ordered BDFs in the PCIe path to ``bdf`` (root port ... endpoint).
        Every BDF is one end of a link; reading AER on all of them covers both
        directions of every link in the path. Base default: just the device itself."""
        return [bdf]

    def read_port_type(self, bdf: str) -> int:
        """PCIe Device/Port Type (PORT_* ): endpoint / root / switch up / switch down.
        Determines which link a BDF's AER reports and which ports own a link's downgrade
        state. Base default: endpoint."""
        return PORT_ENDPOINT

    def is_mock(self) -> bool:
        return isinstance(self, MockBackend)


# ----------------------------------------------------------------------------- #
# Real Linux backend
# ----------------------------------------------------------------------------- #
class RealBackend(Backend):
    """Reads real hardware via /sys/bus/pci. Requires root for config-space writes.

    The sysfs root is injectable so this real path can run off-hardware against a fixture
    tree (see tests/sysfs_fixture.py): pass ``sys_root=`` or set ``$COMPUTETEST_SYSROOT``;
    it defaults to the real ``/sys/bus/pci/devices``."""

    SYS = "/sys/bus/pci/devices"

    def __init__(self, sys_root: str | None = None):
        self._sys_root = sys_root or os.environ.get("COMPUTETEST_SYSROOT") or RealBackend.SYS

    @staticmethod
    def _check_bdf(bdf: str) -> None:
        """Reject any BDF that doesn't match the canonical DDDD:BB:DD.F shape.

        Every public method that interpolates ``bdf`` into a sysfs path goes through
        here. Without it, an attacker-controlled BDF from a plan file or library caller
        (e.g. ``"../../etc/passwd"``) would escape ``sys_root`` — and with root, the
        ``write_config`` path would be an arbitrary-write primitive.
        """
        if not isinstance(bdf, str) or not _BDF_RE.match(bdf):
            raise ValueError(f"invalid BDF: {bdf!r}")

    def list_devices(self) -> list[str]:
        return sorted(os.path.basename(p) for p in glob.glob(f"{self._sys_root}/*"))

    def _attr(self, bdf: str, name: str) -> str | None:
        try:
            with open(f"{self._sys_root}/{bdf}/{name}") as fh:
                return fh.read().strip()
        except OSError:
            return None

    def get_device(self, bdf: str) -> PciDevice:
        self._check_bdf(bdf)

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
        link = os.path.join(self._sys_root, bdf, "driver")
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
        self._check_bdf(bdf)
        with open(f"{self._sys_root}/{bdf}/config", "rb") as fh:
            fh.seek(offset)
            data = fh.read(size)
        return int.from_bytes(data, "little")

    def write_config(self, bdf: str, offset: int, value: int, size: int = 4) -> None:
        self._check_bdf(bdf)
        data = value.to_bytes(size, "little")
        # Opening config O_WRONLY/RDWR requires CAP_SYS_ADMIN (root) on Linux.
        fd = os.open(f"{self._sys_root}/{bdf}/config", os.O_RDWR)
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

    def read_link_status(self, bdf: str) -> LinkStatus:
        cap = self.find_cap(bdf, CAP_PCIE)
        if cap is None:
            d = self.get_device(bdf)  # fall back to sysfs
            return LinkStatus(d.current_link_speed, d.current_link_width)
        ls = self.read_config(bdf, cap + PCIE_LINK_STATUS, 2)
        return LinkStatus(ls & 0xF, (ls >> 4) & 0x3F, bool(ls & LNKSTA_TRAINING),
                          bool(ls & LNKSTA_LBMS), bool(ls & LNKSTA_LABS),
                          bool(ls & LNKSTA_DLLLA))

    def read_device_status(self, bdf: str) -> int | None:
        cap = self.find_cap(bdf, CAP_PCIE)
        return None if cap is None else self.read_config(bdf, cap + PCIE_DEV_STATUS, 2)

    def clear_device_status(self, bdf: str, mask: int = 0xF) -> None:
        cap = self.find_cap(bdf, CAP_PCIE)
        if cap is not None:
            self.write_config(bdf, cap + PCIE_DEV_STATUS, mask, 2)

    def clear_link_bw_status(self, bdf: str) -> None:
        cap = self.find_cap(bdf, CAP_PCIE)
        if cap is not None:
            self.write_config(bdf, cap + PCIE_LINK_STATUS, LNKSTA_LBMS | LNKSTA_LABS, 2)

    def link_chain(self, bdf: str) -> list[str]:
        self._check_bdf(bdf)
        # The sysfs realpath nests each upstream bridge: the BDF-shaped path
        # components ARE the chain, ordered root-port -> ... -> endpoint.
        real = os.path.realpath(f"{self._sys_root}/{bdf}")
        chain = [p for p in real.split(os.sep) if _BDF_RE.match(p)]
        return chain or [bdf]

    def read_port_type(self, bdf: str) -> int:
        cap = self.find_cap(bdf, CAP_PCIE)
        if cap is None:
            return PORT_ENDPOINT
        return (self.read_config(bdf, cap + PCIE_CAP_FLAGS, 2) >> 4) & 0xF


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
    injected_ber: float = 0.0    # set > 0 to simulate a marginal link (rate errors under load)
    inject_uncorr_bit: int = 0   # >0 = a persistent uncorrectable fault (present even at idle)
    inject_stuck_cor: int = 0    # correctable bit mask that's ALWAYS set, even at idle (a fault)
    inject_retrains_per_sec: float = 0.0   # >0 = link keeps re-entering Recovery
    inject_downtrain_per_sec: float = 0.0  # >0 = link transiently downgrades speed under load
    has_pcie_cap: bool = True    # False simulates a (legacy) device with no PCIe capability
    parent: str | None = None    # BDF of the upstream port (for chain/topology modeling)
    port_type: int = PORT_ENDPOINT  # PCIe Device/Port Type (root/switch-up/switch-down/endpoint)
    optimal_preset: int = 4      # TX preset that minimizes errors (for the eq sweep)
    # Internal latch/accounting state:
    _cor_status: int = 0
    _uncor_status: int = 0
    _exercising: bool = False     # gated by set_exercising(); rate errors accrue only when True
    _bw_latched: bool = False     # LBMS/LABS sticky latch (a speed/width change occurred)
    _min_speed_seen: int = 99
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
        """A representative compute board: 2 Gen5 GPUs, 2 Gen4 NVMe, a custom card.

        Mirrors the Zoox compute-platform shape: SoCs/GPUs are Gen5 (32 GT/s), NVMe
        SSDs commonly stay at Gen4 (16 GT/s) since few consumer NVMe parts hit Gen5
        train rate today. The custom card is intentionally Gen3 x8 to exercise the
        degrade/downtrain path on a Gen4-capable slot."""
        return [
            MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 5, 16, 5, 16, "nvidia"),
            MockDevice("0000:04:00.0", 0x10DE, 0x2204, 0x030000, 5, 16, 5, 16, "nvidia",
                       injected_ber=5e-9),  # one marginal Gen5 GPU link (fails the BERT in demos)
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

    def read_link_status(self, bdf: str) -> LinkStatus:
        d = self._dev(bdf)
        training = False
        speed = d.link_speed
        if d.inject_retrains_per_sec > 0 or d.inject_downtrain_per_sec > 0:
            now = time.monotonic()
            dt = now - d._last_ls_poll
            d._last_ls_poll = now
            if d.inject_retrains_per_sec > 0:
                training = _poisson(self._rng, d.inject_retrains_per_sec * dt) > 0
            if d.inject_downtrain_per_sec > 0 and \
                    _poisson(self._rng, d.inject_downtrain_per_sec * dt) > 0:
                d._bw_latched = True             # a speed/width change occurred
                speed = max(1, d.link_speed - 2)  # transient dip (may recover)
        d._min_speed_seen = min(d._min_speed_seen, speed)
        return LinkStatus(speed, d.link_width, training,
                          bw_changed=d._bw_latched, autonomous_bw=d._bw_latched)

    def read_config(self, bdf: str, offset: int, size: int = 4) -> int:
        d = self._dev(bdf)
        aer = d._ext_caps.get(ECAP_AER)
        if aer is not None and offset == aer + AER_CORR_STATUS:
            if d.inject_stuck_cor:                # constant fault, present even at idle
                d._cor_status |= d.inject_stuck_cor
            if d._exercising:                     # rate errors only while exercised
                d._accrue_and_latch(self._rng)
            return d._cor_status & _mask(size)
        if aer is not None and offset == aer + AER_UNCORR_STATUS:
            if d.inject_uncorr_bit:               # a persistent fault re-latches each read
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

    # Device Status (no-AER fallback error source) ------------------------------
    def read_device_status(self, bdf: str) -> int | None:
        d = self._dev(bdf)
        if not d.has_pcie_cap:
            return None
        if d.inject_stuck_cor:
            d._cor_status |= d.inject_stuck_cor
        if d.inject_uncorr_bit:
            d._uncor_status |= (1 << d.inject_uncorr_bit)
        if d._exercising:
            d._accrue_and_latch(self._rng)
        val = 0
        if d._cor_status:
            val |= DEVSTA_CORR
        if d._uncor_status:
            val |= DEVSTA_NONFATAL
        return val

    def clear_device_status(self, bdf: str, mask: int = 0xF) -> None:
        d = self._dev(bdf)
        if mask & DEVSTA_CORR:
            d._cor_status = 0
            d._accrual_t = time.monotonic()
        if mask & (DEVSTA_NONFATAL | DEVSTA_FATAL):
            d._uncor_status = 0

    def clear_link_bw_status(self, bdf: str) -> None:
        self._dev(bdf)._bw_latched = False

    def set_exercising(self, bdf: str, active: bool) -> None:
        d = self._dev(bdf)
        d._exercising = active
        d._accrual_t = time.monotonic()          # reset the accrual window on state change

    def link_chain(self, bdf: str) -> list[str]:
        chain: list[str] = []
        seen: set[str] = set()
        cur: str | None = bdf      # parent walk terminates at the root port (parent = None)
        while cur and cur in self._devs and cur not in seen:
            chain.append(cur)
            seen.add(cur)
            cur = self._dev(cur).parent
        return list(reversed(chain)) or [bdf]      # root port ... endpoint

    def read_port_type(self, bdf: str) -> int:
        return self._dev(bdf).port_type

    # Test helpers (not part of the Backend interface) --------------------------
    def true_error_count(self, bdf: str) -> int:
        return self._dev(bdf)._true_errors

    def inject_uncorrectable(self, bdf: str, bit: int) -> None:
        """Simulate a persistent uncorrectable fault (present even at idle)."""
        self._dev(bdf).inject_uncorr_bit = bit

    def inject_stuck_correctable(self, bdf: str, mask: int = _COR_BAD_TLP) -> None:
        """Simulate a genuinely stuck correctable bit (set even at idle)."""
        self._dev(bdf).inject_stuck_cor = mask

    def inject_downtrain(self, bdf: str, per_sec: float = 50.0) -> None:
        """Simulate a link that transiently downgrades speed under load."""
        self._dev(bdf).inject_downtrain_per_sec = per_sec


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
def _effective_sys_root() -> str:
    """The sysfs root RealBackend will use: $COMPUTETEST_SYSROOT or the real default.
    Auto-selection keys on this, so pointing the env at a fixture tree picks Real."""
    return os.environ.get("COMPUTETEST_SYSROOT") or RealBackend.SYS


def select_backend() -> Backend:
    """Pick Real on a capable Linux box (or against a fixture root), Mock otherwise.
    Override via COMPUTETEST_BACKEND; redirect the sysfs root via COMPUTETEST_SYSROOT."""
    forced = os.environ.get("COMPUTETEST_BACKEND", "").lower()
    if forced == "mock":
        return MockBackend()
    if forced == "real":
        return RealBackend()
    if os.path.isdir(_effective_sys_root()):
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
    return not os.path.isdir(_effective_sys_root())
