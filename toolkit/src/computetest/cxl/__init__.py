"""CXL (Compute Express Link) conformance + RAS subpackage (Sprint 4.4).

* ``mailbox`` — the CCI command/response mailbox abstraction, mirroring the
  NVMe-MI MctpTransport/MiMessage pattern.
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

__all__ = [
    "MailboxTransport", "MockMailbox", "MailboxError", "CxlCommand",
    "CxlResponse", "CciOpcode", "CciReturnCode",
]
