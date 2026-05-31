"""
DDR5 SPD (JESD400-5) parser + module-conformance check (Sprint 4.3).

Parses the SPD EEPROM (read over SMBus / I3C-Basic from the SPD5118 hub) for the
key module-identity / RAS fields and verdicts them against an expected profile
(from the fixture map). A corpus under ``corpus/ddr5_spd/`` carries a known-good
SPD + ``expected.json``, mirroring ``corpus/nvme_ocp_internal_log``.

NOTE: this decodes the handful of JESD400-5 fields the conformance check needs
(DRAM type, module type, density, ECC) — a scaffold, not a full SPD decode.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DDR5_SPD_KEY = 0x12                  # byte 2: DRAM device type = DDR5 SDRAM
MODULE_TYPES = {0x01: "RDIMM", 0x02: "UDIMM", 0x03: "SODIMM", 0x04: "LRDIMM"}
DENSITY_GBIT = {0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 5: 24, 6: 32, 7: 48, 8: 64}


@dataclass
class SpdInfo:
    """The decoded SPD identity/RAS fields."""
    memory_type: int
    module_type: str
    density_gbit: int
    ecc: bool

    def to_dict(self) -> dict:
        return {"memory_type": self.memory_type, "module_type": self.module_type,
                "density_gbit": self.density_gbit, "ecc": self.ecc}


def parse_spd(data: bytes) -> SpdInfo:
    """Parse the key DDR5 SPD identity/RAS fields (JESD400-5 bytes 2/3/4/235)."""
    if len(data) < 236:
        raise ValueError(f"SPD too short: {len(data)} bytes "
                         "(need >= 236; DDR5 ECC/bus-width is at byte 235)")
    return SpdInfo(
        memory_type=data[2],
        module_type=MODULE_TYPES.get(data[3] & 0x0F, "unknown"),
        density_gbit=DENSITY_GBIT.get(data[4] & 0x1F, 0),
        # JESD400-5 §11.11 "Memory Channel Bus Width" byte 235: bits[4:3] are the
        # bus-width extension per sub-channel (00=none, 01=4-bit ECC) -> nonzero
        # means ECC. DDR4 carried this at byte 13; on DDR5 byte 13 is thermal/
        # refresh options, so reading it would decode an unrelated field.
        ecc=bool((data[235] >> 3) & 0x03),
    )


@dataclass
class SpdHealth:
    """SPD module-conformance verdict."""
    info: SpdInfo
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return (f"DDR5 SPD {self.info.module_type} {self.info.density_gbit}Gb "
                f"ecc={self.info.ecc} -> {state}")

    def to_dict(self) -> dict:
        return {"info": self.info.to_dict(), "checks": self.checks, "ok": self.ok}


def check_spd(data: bytes, *, expected: dict) -> SpdHealth:
    """Parse SPD and verdict it: it must be a DDR5 SPD and every ``expected``
    field (from the fixture map) must match the parsed value."""
    info = parse_spd(data)
    parsed = info.to_dict()
    checks = {"is_ddr5": info.memory_type == DDR5_SPD_KEY}
    for k, v in expected.items():
        checks[f"{k}_matches"] = parsed.get(k) == v
    return SpdHealth(info=info, checks=checks)
