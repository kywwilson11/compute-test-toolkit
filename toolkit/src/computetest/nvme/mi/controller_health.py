"""
NVMe-MI Controller Health Status Poll — the MI-side cross-check against the
in-band SMART Log.

The Controller Health Status Poll (CHSP) is a single OOB command that returns
the controller's health flags + a tally of pending event categories. Hitting
both this and the in-band ``nvme smart-log`` is the *only* way to detect a
drive whose in-band path silently drifts from its OOB-reported state — which
is exactly the failure mode the research roadmap's NVMe-MI 2.1 finding
(verified) flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ControllerHealthStatus:
    """One CHSP response, parsed.

    Field names follow the NVMe-MI spec table where reasonable; the toolkit
    keeps the same names downstream consumers already index on.
    """
    controller_id: int
    composite_temperature_k: int     # Kelvin per the spec (we expose °C via property)
    available_spare: int             # 0..100 %
    percentage_used: int             # 0..255 % (>100 = past designed life)
    critical_warning: int            # bitmask per NVM Express base spec table
    nvm_subsystem_reset_count: int
    pending_event_categories: list[int] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def temperature_c(self) -> float:
        return float(self.composite_temperature_k) - 273.0

    @property
    def healthy(self) -> bool:
        return (self.critical_warning == 0
                and self.available_spare >= 100
                and self.percentage_used < 2)

    def to_dict(self) -> dict:
        return {"controller_id": self.controller_id,
                "composite_temperature_k": self.composite_temperature_k,
                "temperature_c": self.temperature_c,
                "available_spare": self.available_spare,
                "percentage_used": self.percentage_used,
                "critical_warning": self.critical_warning,
                "nvm_subsystem_reset_count": self.nvm_subsystem_reset_count,
                "pending_event_categories": list(self.pending_event_categories),
                "healthy": self.healthy,
                "raw": dict(self.raw)}


# Deterministic mock CHSP for off-hardware unit tests.
_MOCK_CHSP: dict = {
    "controller_id": 1,
    "composite_temperature_k": 314,       # 41 °C
    "available_spare": 100,
    "percentage_used": 1,
    "critical_warning": 0,
    "nvm_subsystem_reset_count": 0,
    "pending_event_categories": [],
}


def poll_controller_health(transport=None, *, controller_id: int = 1,
                            mock: bool = True) -> ControllerHealthStatus:
    """Send a CHSP and parse the response.

    With ``mock=True`` (or ``transport=None``) returns the deterministic
    mock above. Real-bus path needs the ``transport`` (an opened
    ``MctpTransport``) + an issued NvmeMiCommandOpcode.CONTROLLER_HEALTH_STATUS_POLL
    request; left as NotImplementedError until libmctp lands on a station.
    """
    if mock or transport is None:
        raw = dict(_MOCK_CHSP)
        raw["controller_id"] = controller_id
        return ControllerHealthStatus(
            controller_id=raw["controller_id"],
            composite_temperature_k=raw["composite_temperature_k"],
            available_spare=raw["available_spare"],
            percentage_used=raw["percentage_used"],
            critical_warning=raw["critical_warning"],
            nvm_subsystem_reset_count=raw["nvm_subsystem_reset_count"],
            pending_event_categories=list(raw["pending_event_categories"]),
            raw=raw,
        )
    raise NotImplementedError(                              # pragma: no cover
        "Real CHSP needs an open MctpTransport + a library that frames the "
        "NVMe-MI CONTROLLER_HEALTH_STATUS_POLL request. Use mock=True for "
        "unit tests, or wire libmctp + the request encoder once a station "
        "provides them.")
