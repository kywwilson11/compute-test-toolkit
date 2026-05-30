"""
DDR5 link CRC + C/A parity (ALERT_n) plumbing check (Sprint 4.3).

Config-and-reporting-plumbing verification: write/read CRC and C/A parity must
be enabled, the ALERT_n recovery path must be wired, and any host-readable
CRC-retry counter must stay within budget. Where the IMC lacks host-readable CRC
counters this is config-only — the physical CRC stimulus needs an interposer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_MAX_CRC_RETRIES = 0


@dataclass
class CrcHealth:
    """DDR5 CRC / C/A parity plumbing verdict."""
    crc_retry_count: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"DDR5 CRC/parity retries={self.crc_retry_count} -> {state}"

    def to_dict(self) -> dict:
        return {"crc_retry_count": self.crc_retry_count, "checks": self.checks,
                "ok": self.ok}


def check_crc_parity(*, write_crc_enabled: bool, read_crc_enabled: bool,
                     ca_parity_enabled: bool, alert_n_wired: bool,
                     crc_retry_count: int = 0,
                     max_retries: int = DEFAULT_MAX_CRC_RETRIES) -> CrcHealth:
    """Verify the DDR5 CRC / C/A parity config + ALERT_n plumbing. The enable
    bits come from mode-register reads; the retry count from a host-readable
    counter where the IMC exposes one (else pass 0 for the config-only case)."""
    checks = {
        "write_crc_enabled": write_crc_enabled,
        "read_crc_enabled": read_crc_enabled,
        "ca_parity_enabled": ca_parity_enabled,
        "alert_n_wired": alert_n_wired,
        "crc_retries_within_budget": crc_retry_count <= max_retries,
    }
    return CrcHealth(crc_retry_count=crc_retry_count, checks=checks)
