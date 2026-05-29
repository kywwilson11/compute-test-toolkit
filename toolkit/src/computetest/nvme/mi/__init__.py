"""
NVMe Management Interface (NVMe-MI) v2.1 — out-of-band management surface.

NVMe-MI 2.1 (ratified 2025-08-01, nvmexpress.org) defines the BMC-facing
management path that exists ALONGSIDE the kernel ``nvme-cli`` in-band path
— and is the ONLY surface on drives that implement OOB-only (the v2.1 spec
explicitly permits OOB-only, in-band-tunneling-only, or both, so an in-band
SMART read is NOT a substitute for an MI Controller Health Status Poll).

Subpackage layout:

* ``mctp``           — MCTP transport (DSP0236) over SMBus/I2C, I3C, or PCIe-VDM
* ``messages``       — NVMe-MI message framing (Control Primitives, Admin
                       Command Set, NVMe Management Command Set)
* ``aem``            — Asynchronous Event Messages (health, temperature,
                       inventory, security-state) with subscription + coalescing
* ``controller_health`` — Controller Health Status Poll (the MI-side
                       cross-check against the in-band SMART Log)

Real-host implementation lives off the hot-path until a station provides a
``libmctp`` install + Aardvark or I3C adapter; the boundary types and
helpers are useful today for compile-time integration and unit-tested
framing logic.

See ``docs/nvme/unh_iol_v25_coverage_matrix.md`` for the planned mapping of
the UNH-IOL NVMe-MI Conformance Test Suite v25 (Feb 2026) to pytest nodes.
"""
from .aem import (
    AsyncEventMessage,
    AsyncEventType,
    EventSeverity,
    EventSubscription,
    Subscription,
)
from .controller_health import (
    ControllerHealthStatus,
    poll_controller_health,
)
from .mctp import (
    MctpEndpointId,
    MctpError,
    MctpMessage,
    MctpTransport,
    MockMctpTransport,
)
from .messages import (
    ControlPrimitive,
    MessageType,
    MiMessage,
    MiMessageError,
    encode_mi_request,
)
from .reset import (
    ResetFunction,
    ResetRequest,
    ResetResult,
    reset_management_endpoint,
)
from .subsystem_health import (
    NvmSubsystemHealth,
    poll_nvm_subsystem_health,
)
from .vpd import (
    VpdBoundsError,
    VpdReadRequest,
    VpdReadResult,
    vpd_read,
)

__all__ = [
    # mctp
    "MctpEndpointId", "MctpError", "MctpMessage", "MctpTransport",
    "MockMctpTransport",
    # messages
    "ControlPrimitive", "MessageType", "MiMessage", "MiMessageError",
    "encode_mi_request",
    # aem
    "AsyncEventMessage", "AsyncEventType", "EventSeverity",
    "EventSubscription", "Subscription",
    # controller_health
    "ControllerHealthStatus", "poll_controller_health",
    # subsystem_health (NVMe-MI 5.1)
    "NvmSubsystemHealth", "poll_nvm_subsystem_health",
    # vpd (NVMe-MI 5.2)
    "VpdBoundsError", "VpdReadRequest", "VpdReadResult", "vpd_read",
    # reset (NVMe-MI 5.7)
    "ResetFunction", "ResetRequest", "ResetResult",
    "reset_management_endpoint",
]
