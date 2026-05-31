"""
NVMe-MI NVM Subsystem Health Status Poll (NSHSP).

The subsystem-level cross-check to the controller-level
``poll_controller_health``: returns flags for the *subsystem*
(not the controller) — drive function, RAS capability, composite
temperature, percentage drive life used, smart-warning bitmask,
spare + capacity warnings.

Opcode 0x01 per NVMe-MI §5.1 Table 88.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Smart-warning bitmask bits per NVM Express base spec §5.16.1.2 (the same
# byte the in-band SMART Log Page 02h surfaces). Names mirror NVMe-cli's
# canonical labels so a single downstream consumer doesn't need to translate.
SMART_WARNING_AVAILABLE_SPARE_BELOW_THRESHOLD = 1 << 0
SMART_WARNING_TEMPERATURE_THRESHOLD = 1 << 1
SMART_WARNING_RELIABILITY_DEGRADED = 1 << 2
SMART_WARNING_READ_ONLY = 1 << 3
SMART_WARNING_VOLATILE_MEMORY_BACKUP_FAILED = 1 << 4
SMART_WARNING_PMR_RELIABILITY_DEGRADED = 1 << 5


# NSS (NVM Subsystem Status) byte from the NSHSP response.
#
# TODO(spec): the exact NSS bit indices below are UNVERIFIED and almost
# certainly wrong. The ratified NVMe-MI spec (NSHDS, NVM Subsystem Status
# byte) places Drive Functional at bit 5 (NOT bit 0); Reset Not Required and
# the per-port PCIe-link-active bits also need confirming. The ratified PDF
# returned HTTP 403 during this fix, so the exact byte map could not be
# transcribed. Do NOT trust these masks on real hardware until each bit is
# reconciled cell-by-cell against the targeted NVMe-MI revision's NSS table
# and a decode test is fed a real captured byte. (The real decode path is
# unimplemented, so no live verdict depends on these today.)
NSS_DRIVE_FUNCTIONING = 1 << 0       # UNVERIFIED placeholder (spec: bit 5)
NSS_RESET_NOT_REQUIRED = 1 << 1      # UNVERIFIED placeholder
NSS_PORT_0_PCI_FUNCTIONING = 1 << 2  # UNVERIFIED placeholder
NSS_PORT_1_PCI_FUNCTIONING = 1 << 3  # UNVERIFIED placeholder


@dataclass
class NvmSubsystemHealth:
    """Parsed NSHSP response."""
    nss: int                          # NVM Subsystem Status byte
    smart_warnings: int               # Critical Warning bitmask
    composite_temperature_c: int      # raw on-wire byte: signed int8 Celsius
    percentage_drive_life_used: int   # 0..255 % (>100 = past designed life)
    available_spare: int              # 0..100 %
    spare_below_threshold: bool
    capacity_below_threshold: bool
    raw: dict = field(default_factory=dict)

    @property
    def temperature_c(self) -> float:
        """Decode the NSHSP Composite Temperature byte (signed int8 Celsius,
        two's-complement) per NVMe-MI NSHDS (libnvme ``__u8 ctemp``). The
        Kelvin 2-byte form belongs to the *in-band* SMART Log 02h, not here."""
        byte = self.composite_temperature_c & 0xFF
        return float(byte - 256 if byte >= 128 else byte)

    @property
    def drive_functioning(self) -> bool:
        """True iff the NSS Drive Functional bit is set.

        WARNING: keys off NSS_DRIVE_FUNCTIONING, whose index is UNVERIFIED
        (see the TODO on the NSS_* constants; spec puts Drive Functional at
        bit 5, not bit 0). Reconcile against the ratified NSS table before
        trusting ``healthy`` on real hardware.
        """
        return bool(self.nss & NSS_DRIVE_FUNCTIONING)

    @property
    def healthy(self) -> bool:
        return (self.drive_functioning
                and self.smart_warnings == 0
                and not self.spare_below_threshold
                and not self.capacity_below_threshold
                and self.percentage_drive_life_used < 2)

    def warnings(self) -> list[str]:
        """Decode the smart-warning bitmask into spec-canonical names."""
        names: list[str] = []
        if self.smart_warnings & SMART_WARNING_AVAILABLE_SPARE_BELOW_THRESHOLD:
            names.append("available_spare_below_threshold")
        if self.smart_warnings & SMART_WARNING_TEMPERATURE_THRESHOLD:
            names.append("temperature_threshold")
        if self.smart_warnings & SMART_WARNING_RELIABILITY_DEGRADED:
            names.append("reliability_degraded")
        if self.smart_warnings & SMART_WARNING_READ_ONLY:
            names.append("read_only")
        if self.smart_warnings & SMART_WARNING_VOLATILE_MEMORY_BACKUP_FAILED:
            names.append("volatile_memory_backup_failed")
        if self.smart_warnings & SMART_WARNING_PMR_RELIABILITY_DEGRADED:
            names.append("pmr_reliability_degraded")
        return names

    def to_dict(self) -> dict:
        return {"nss": self.nss,
                "drive_functioning": self.drive_functioning,
                "smart_warnings": self.smart_warnings,
                "smart_warning_names": self.warnings(),
                "composite_temperature_c": self.composite_temperature_c,
                "temperature_c": self.temperature_c,
                "percentage_drive_life_used": self.percentage_drive_life_used,
                "available_spare": self.available_spare,
                "spare_below_threshold": self.spare_below_threshold,
                "capacity_below_threshold": self.capacity_below_threshold,
                "healthy": self.healthy,
                "raw": dict(self.raw)}


_MOCK_NSHSP: dict = {
    "nss": NSS_DRIVE_FUNCTIONING | NSS_RESET_NOT_REQUIRED
            | NSS_PORT_0_PCI_FUNCTIONING,
    "smart_warnings": 0,
    "composite_temperature_c": 41,             # raw signed-int8 byte = 41 °C
    "percentage_drive_life_used": 1,
    "available_spare": 100,
    "spare_below_threshold": False,
    "capacity_below_threshold": False,
}


def poll_nvm_subsystem_health(transport=None, *,
                               mock: bool = True) -> NvmSubsystemHealth:
    """Issue an NSHSP request and parse the response.

    Mock path (``transport`` None or ``mock=True``) returns a deterministic
    healthy snapshot. Real-bus path raises ``NotImplementedError`` until a
    station wires up libmctp + the request encoder.
    """
    if mock or transport is None:
        raw = dict(_MOCK_NSHSP)
        return NvmSubsystemHealth(
            nss=raw["nss"],
            smart_warnings=raw["smart_warnings"],
            composite_temperature_c=raw["composite_temperature_c"],
            percentage_drive_life_used=raw["percentage_drive_life_used"],
            available_spare=raw["available_spare"],
            spare_below_threshold=raw["spare_below_threshold"],
            capacity_below_threshold=raw["capacity_below_threshold"],
            raw=raw,
        )
    raise NotImplementedError(                              # pragma: no cover
        "Real NSHSP needs an open MctpTransport + an NVM_SUBSYSTEM_HEALTH_STATUS_POLL "
        "request encoder. Use mock=True for unit tests, or wire libmctp + "
        "the encoder once a station provides them.")
