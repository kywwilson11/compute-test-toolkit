"""CXL (Compute Express Link) conformance + RAS subpackage (Sprint 4.4).

* ``mailbox`` — the CCI command/response mailbox abstraction, mirroring the
  NVMe-MI MctpTransport/MiMessage pattern.
* ``ras`` — CXL.cachemem UE/CE error decode + write-1-to-clear (mirrors aer.py).
"""
from .device import (  # noqa: F401
    CoherencyHealth,
    CxlDeviceType,
    FwLifecycleHealth,
    check_coherency,
    check_media_ready_contract,
)
from .emit import (  # noqa: F401
    emit_cxl_events,
    emit_cxl_link,
    emit_cxl_ras,
)
from .events import (  # noqa: F401
    EventClearHealth,
    EventLog,
    EventLogStore,
    EventRecord,
    EventRecordType,
    check_event_get_clear,
)
from .hdm import (  # noqa: F401
    HdmDecoder,
    HdmHealth,
    calc_interleave_pos,
    check_hdm_decoder,
)
from .link import (  # noqa: F401
    CxlLinkHealth,
    FlitMode,
    check_cxl_link,
)
from .mailbox import (  # noqa: F401
    CciOpcode,
    CciReturnCode,
    CxlCommand,
    CxlResponse,
    MailboxError,
    MailboxTransport,
    MockMailbox,
)
from .maintenance import (  # noqa: F401
    PERFORM_MAINTENANCE_OPCODE,
    MaintenanceClass,
    MaintenanceDevice,
    MaintenanceHealth,
    check_maintenance,
)
from .poison import (  # noqa: F401
    ContainmentHealth,
    PoisonEntry,
    PoisonHealth,
    PoisonList,
    PoisonSource,
    check_containment,
    check_poison_inject_clear,
)
from .ras import (  # noqa: F401
    CXL_CE_BITS,
    CXL_UE_BITS,
    ClearResult,
    CxlRasRegisters,
    CxlRasSnapshot,
    clear_ras,
    decode_ce,
    decode_ue,
    snapshot,
)

__all__ = [
    # mailbox (Sprint 4.4)
    "MailboxTransport", "MockMailbox", "MailboxError", "CxlCommand",
    "CxlResponse", "CciOpcode", "CciReturnCode",
    # ras (Sprint 4.4)
    "CxlRasSnapshot", "CxlRasRegisters", "ClearResult", "clear_ras", "snapshot",
    "decode_ue", "decode_ce", "CXL_UE_BITS", "CXL_CE_BITS",
    # link (Sprint 4.4)
    "CxlLinkHealth", "FlitMode", "check_cxl_link",
    # events (Sprint 4.4)
    "EventLog", "EventRecordType", "EventRecord", "EventLogStore",
    "EventClearHealth", "check_event_get_clear",
    # poison / containment (Sprint 4.4)
    "PoisonSource", "PoisonEntry", "PoisonList", "PoisonHealth",
    "ContainmentHealth", "check_poison_inject_clear", "check_containment",
    # hdm (Sprint 4.4)
    "HdmDecoder", "HdmHealth", "calc_interleave_pos", "check_hdm_decoder",
    # ocp-diag projection (Sprint 4.4)
    "emit_cxl_link", "emit_cxl_ras", "emit_cxl_events",
    # device / coherency (Sprint 4.4)
    "CxlDeviceType", "CoherencyHealth", "FwLifecycleHealth", "check_coherency",
    "check_media_ready_contract",
    # maintenance / 0600h (Sprint 4.4)
    "MaintenanceClass", "MaintenanceDevice", "MaintenanceHealth",
    "check_maintenance", "PERFORM_MAINTENANCE_OPCODE",
]
