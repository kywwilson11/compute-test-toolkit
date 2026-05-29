"""
NVMe-MI VPD Read (Vital Product Data).

NVMe-MI §5.2 defines two VPD commands, both addressing the device's VPD
EEPROM (an IPMI FRU-format blob holding manufacturer, part number, board
serial, lot, MAC addresses, etc.):

* **VPD Read** (opcode 0x05) — reads a span [offset, offset+length)
* **VPD Write** (opcode 0x06) — writes a span; this module surfaces only
  the read path

Both commands take a 32-bit offset + 32-bit length. The spec caps
``offset + length`` at ``VPD size`` (per VPD Capacity register read
out-of-band first); the toolkit enforces the bound at request build time
so a buggy caller can't issue a request that the controller will reject.
"""
from __future__ import annotations

from dataclasses import dataclass

# Maximum-allowed VPD size per the NVMe-MI spec — the addressable space
# bounded by the 32-bit offset + length fields. Real devices report a
# smaller size via VPD Capacity; the toolkit defaults to the spec max so
# bounds checking works even before that read.
MAX_VPD_SIZE = 0xFFFF_FFFF


class VpdBoundsError(ValueError):
    """VPD Read request would overflow the device's reported VPD size."""


@dataclass
class VpdReadRequest:
    """One VPD Read request.

    ``vpd_capacity_bytes`` is the device-reported VPD EEPROM size, read
    out-of-band before the first VPD Read on a station. ``offset + length``
    must not exceed it (UNH-IOL v25 test 10.1).
    """
    offset: int
    length: int
    vpd_capacity_bytes: int = MAX_VPD_SIZE

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise VpdBoundsError(f"VPD offset must be >= 0; got {self.offset}")
        if self.length <= 0:
            raise VpdBoundsError(f"VPD length must be > 0; got {self.length}")
        if self.offset + self.length > self.vpd_capacity_bytes:
            raise VpdBoundsError(
                f"VPD read overflows: offset {self.offset} + "
                f"length {self.length} > capacity {self.vpd_capacity_bytes}")


@dataclass
class VpdReadResult:
    """One VPD Read response, ``bytes`` of length ``request.length``."""
    request: VpdReadRequest
    data: bytes


# Deterministic mock VPD blob — a tiny IPMI FRU-style header so unit tests
# can read a recognizable slice. NB: the trailing padding is on its own line
# joined with `+` (NOT adjacent-literal concatenation) because Python's
# literal-concatenation has higher precedence than `*` — without the
# explicit `+`, the entire preceding blob would be repeated 256 times.
_MOCK_VPD = (
    b"\x01\x00\x00\x05\x00\x00\x00\xFA"                     # FRU header
    b"NVME-MI-MOCK-001\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    b"manufacturer:MockCorp\x00"
    b"product:MockSSD-1TB\x00"
    b"serial:SN-MOCK-0001\x00"
) + b"\x00" * 256                                            # padding


def vpd_read(request: VpdReadRequest, transport=None, *,
              mock: bool = True) -> VpdReadResult:
    """Issue a VPD Read and return ``length`` bytes from ``offset``.

    Mock path returns a slice of the in-memory FRU blob — useful for
    asserting bounds-check behaviour. Real-bus path raises
    ``NotImplementedError`` until libmctp + a request encoder are wired up.
    """
    if mock or transport is None:
        # The mock blob is bounded by len(_MOCK_VPD); slice safely.
        end = min(request.offset + request.length, len(_MOCK_VPD))
        data = _MOCK_VPD[request.offset:end]
        # Zero-pad if the slice was shorter than length (mimics a real
        # device returning the requested length, padding past EEPROM end).
        if len(data) < request.length:
            data = data + b"\x00" * (request.length - len(data))
        return VpdReadResult(request=request, data=data)
    raise NotImplementedError(                              # pragma: no cover
        "Real VPD Read needs an open MctpTransport + an NVMe-MI VPD_READ "
        "request encoder. Use mock=True for unit tests, or wire libmctp + "
        "the encoder once a station provides them.")
