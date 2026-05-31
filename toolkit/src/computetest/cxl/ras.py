"""
CXL RAS error decode + write-1-to-clear (Sprint 4.4).

The CXL.cachemem RAS capability surfaces uncorrectable (UE) and correctable (CE)
error-status registers. This mirrors ``aer.py``: decode tables turn a status word
into the named errors (bit names verbatim from rasdaemon ``ras-cxl-handler.c``),
and the write-1-to-clear primitive — read status, write back the set bits, verify
stuck — transfers unchanged from the PCIe AER clear.
"""
from __future__ import annotations

from dataclasses import dataclass

# Uncorrectable error bits — CXL RAS UE Status. Names from rasdaemon
# ras-cxl-handler.c; bit POSITIONS per the CXL spec UE Status register (kernel
# CXL_RAS_UC_* in drivers/cxl/core/trace.h): bits 12-13 are reserved, and
# Internal Error / IDE Tx / IDE Rx are bits 14/15/16 (not 12/13/14).
CXL_UE_BITS: dict[int, tuple[str, str]] = {
    0: ("CacheDataParity", "CXL.cache data parity error"),
    1: ("CacheAddressParity", "CXL.cache address parity error"),
    2: ("CacheBEParity", "CXL.cache byte-enable parity error"),
    3: ("CacheDataECC", "CXL.cache data ECC error"),
    4: ("MemDataParity", "CXL.mem data parity error"),
    5: ("MemAddressParity", "CXL.mem address parity error"),
    6: ("MemBEParity", "CXL.mem byte-enable parity error"),
    7: ("MemDataECC", "CXL.mem data ECC error"),
    8: ("ReinitThreshold", "REINIT threshold hit"),
    9: ("RsvdEncoding", "received unrecognized encoding"),
    10: ("PoisonReceived", "poison received"),
    11: ("ReceiverOverflow", "receiver overflow"),
    # bits 12-13 reserved
    14: ("InternalError", "device internal error"),
    15: ("IDETxError", "CXL IDE Tx error"),
    16: ("IDERxError", "CXL IDE Rx error"),
}

# Correctable error bits — CXL RAS CE Status.
CXL_CE_BITS: dict[int, tuple[str, str]] = {
    0: ("CacheDataECC", "CXL.cache corrected data ECC"),
    1: ("MemDataECC", "CXL.mem corrected data ECC"),
    2: ("CRCThreshold", "CRC threshold hit"),
    3: ("RetryThreshold", "retry threshold hit"),
    4: ("CachePoisonReceived", "CXL.cache poison received"),
    5: ("MemPoisonReceived", "CXL.mem poison received"),
    6: ("PhysicalLayerError", "physical-layer error"),
}


def decode(value: int, table: dict[int, tuple[str, str]]) -> list[tuple[int, str, str]]:
    """Return [(bit, name, meaning), ...] for each set bit in ``value``."""
    return [(bit, name, meaning) for bit, (name, meaning) in sorted(table.items())
            if value & (1 << bit)]


def decode_ue(value: int) -> list[tuple[int, str, str]]:
    return decode(value, CXL_UE_BITS)


def decode_ce(value: int) -> list[tuple[int, str, str]]:
    return decode(value, CXL_CE_BITS)


@dataclass
class CxlRasSnapshot:
    """A read of the CXL RAS UE/CE status registers (no clearing)."""
    ue_raw: int
    ce_raw: int

    @property
    def uncorrectable(self) -> list[tuple[int, str, str]]:
        return decode_ue(self.ue_raw)

    @property
    def correctable(self) -> list[tuple[int, str, str]]:
        return decode_ce(self.ce_raw)

    @property
    def has_uncorrectable(self) -> bool:
        return self.ue_raw != 0

    @property
    def has_correctable(self) -> bool:
        return self.ce_raw != 0

    def to_dict(self) -> dict:
        return {"ue_raw": self.ue_raw, "ce_raw": self.ce_raw,
                "uncorrectable": [n for _, n, _ in self.uncorrectable],
                "correctable": [n for _, n, _ in self.correctable],
                "has_uncorrectable": self.has_uncorrectable}


@dataclass
class ClearResult:
    """Result of a W1C clear: bits cleared vs bits that refused to clear."""
    cleared: int
    stuck: int

    @property
    def ok(self) -> bool:
        return self.stuck == 0


class CxlRasRegisters:
    """A minimal CXL RAS-capability register file (UE/CE status), W1C-clearable.

    ``set_sticky`` marks bits that refuse to clear (a stuck/persistent hardware
    error — itself a finding, exactly as in PCIe AER)."""
    UE_STATUS = 0x00
    CE_STATUS = 0x0C

    def __init__(self, *, ue_status: int = 0, ce_status: int = 0) -> None:
        self._regs = {self.UE_STATUS: ue_status, self.CE_STATUS: ce_status}
        self._sticky: dict[int, int] = {}

    def set_sticky(self, offset: int, mask: int) -> None:
        self._sticky[offset] = mask

    def read(self, offset: int) -> int:
        return self._regs.get(offset, 0)

    def write_w1c(self, offset: int, value: int) -> None:
        cur = self._regs.get(offset, 0)
        sticky = self._sticky.get(offset, 0)
        self._regs[offset] = cur & ~(value & ~sticky)


def _clear_one(regs: CxlRasRegisters, offset: int) -> ClearResult:
    """Write-1-to-clear only the set bits, then verify (the aer.py primitive)."""
    status = regs.read(offset)
    if status == 0:
        return ClearResult(cleared=0, stuck=0)
    regs.write_w1c(offset, status)
    after = regs.read(offset)
    stuck = after & status
    return ClearResult(cleared=status & ~stuck, stuck=stuck)


def snapshot(regs: CxlRasRegisters) -> CxlRasSnapshot:
    """Read the UE + CE status registers without clearing."""
    return CxlRasSnapshot(ue_raw=regs.read(CxlRasRegisters.UE_STATUS),
                          ce_raw=regs.read(CxlRasRegisters.CE_STATUS))


def clear_ras(regs: CxlRasRegisters) -> dict[str, ClearResult]:
    """Clear (arm) the UE + CE status latches; report what cleared / stuck."""
    return {"uncorrectable": _clear_one(regs, CxlRasRegisters.UE_STATUS),
            "correctable": _clear_one(regs, CxlRasRegisters.CE_STATUS)}
