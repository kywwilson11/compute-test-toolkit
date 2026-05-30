"""CXL (Compute Express Link) conformance + RAS subpackage (Sprint 4.4).

* ``mailbox`` — the CCI command/response mailbox abstraction, mirroring the
  NVMe-MI MctpTransport/MiMessage pattern.
* ``ras`` — CXL.cachemem UE/CE error decode + write-1-to-clear (mirrors aer.py).
"""
from .mailbox import (  # noqa: F401
    CciOpcode,
    CciReturnCode,
    CxlCommand,
    CxlResponse,
    MailboxError,
    MailboxTransport,
    MockMailbox,
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
]
