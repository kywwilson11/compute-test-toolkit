"""
CXL coherency class + device-management lifecycle checks (Sprint 4.4).

Classifies the device (Type 1 cache-only / Type 2 cache+mem / Type 3 mem-only)
from IDENTIFY + DVSEC and asserts the claims are consistent — a Type-3 device
must not claim a cache, and a Type-2 device (cache + device-coherent memory)
must expose HDM-D[B] + BI-snoop coherency bridging. Also verifies the
device-management contract that a
long-running op (Sanitize / FW Activate) returns Background-Started and leaves
media not-ready until completion. Device serviceability view, not a
secure-erase-bypass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class CxlDeviceType(IntEnum):
    TYPE1 = 1                            # cache only (accelerator, no device memory)
    TYPE2 = 2                            # cache + device memory
    TYPE3 = 3                            # device memory only (memory expander)


@dataclass
class CoherencyHealth:
    """CXL coherency-class consistency verdict."""
    device_type: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"CXL Type{self.device_type} coherency -> {state}"

    def to_dict(self) -> dict:
        return {"device_type": self.device_type, "checks": self.checks, "ok": self.ok}


def check_coherency(*, device_type: CxlDeviceType, claims_cache: bool,
                    claims_mem: bool, hdm_d_capable: bool,
                    bi_snoop_capable: bool) -> CoherencyHealth:
    """Assert the device's cache/mem claims match its type and that a Type-2
    device (cache + device-coherent memory) exposes HDM-D[B] + BI-snoop.

    Coherency bridging is a property of device-coherent MEMORY, not of a cache:
    a Type-1 cache-only accelerator has no device memory (no HDM-D[B]/BISnoop),
    and a Type-3 expander using HDM-H needs none either — so the requirement is
    gated on Type-2 (a Type-3 under HDM-DB is not disambiguated by these inputs)."""
    cache_capable = device_type in (CxlDeviceType.TYPE1, CxlDeviceType.TYPE2)
    mem_capable = device_type in (CxlDeviceType.TYPE2, CxlDeviceType.TYPE3)
    checks = {
        "cache_claim_consistent": claims_cache == cache_capable,
        "mem_claim_consistent": claims_mem == mem_capable,
        "coherency_bridging": ((hdm_d_capable and bi_snoop_capable)
                               if device_type == CxlDeviceType.TYPE2 else True),
    }
    return CoherencyHealth(device_type=int(device_type), checks=checks)


@dataclass
class FwLifecycleHealth:
    """Device-management lifecycle (media-not-ready) verdict."""
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        fails = ",".join(k for k, v in self.checks.items() if not v)
        state = "OK" if self.ok else f"FAIL({fails})"
        return f"CXL fw/sanitize lifecycle -> {state}"

    def to_dict(self) -> dict:
        return {"checks": self.checks, "ok": self.ok}


def check_media_ready_contract(*, background_started: bool,
                               media_ready_during: bool,
                               media_ready_after: bool) -> FwLifecycleHealth:
    """A long-running op (Sanitize / FW Activate) must return Background-Started,
    leave media not-ready while it runs, and report media ready once complete."""
    checks = {
        "background_started": background_started,
        "media_not_ready_during": not media_ready_during,
        "media_ready_after_complete": media_ready_after,
    }
    return FwLifecycleHealth(checks=checks)
