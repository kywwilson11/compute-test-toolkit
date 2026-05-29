"""
MCTP (Management Component Transport Protocol) transport for NVMe-MI.

MCTP (DSP0236) is the OOB wire NVMe-MI travels on. The base spec defines a
small fixed header (version/source-EID/dest-EID/message-tag), an optional
fragmentation+reassembly layer, and per-binding bus packet framing. NVMe-MI
binding-specific bits live in DSP0240 (SMBus/I²C) and DSP0241 (PCIe-VDM);
DSP0233 covers the I3C binding added in NVMe-MI 2.0.

This module models the framing layer (encode + decode of headers, EIDs,
control vs message-type discrimination) and exposes a ``MctpTransport`` ABC
+ a ``MockMctpTransport`` test double. Real Aardvark / I3C bindings plug in
by subclassing.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import IntEnum


class MctpError(RuntimeError):
    """An MCTP framing or transport fault."""


# Per DSP0239 — MCTP Endpoint IDs (8-bit):
# * 0x00 — Null endpoint
# * 0x01–0x07 — Reserved
# * 0x08–0xEF — Routable endpoint addresses
# * 0xF0–0xFE — Reserved
# * 0xFF — Broadcast
@dataclass(frozen=True)
class MctpEndpointId:
    """One MCTP endpoint address."""
    value: int

    def __post_init__(self) -> None:
        if not 0x00 <= self.value <= 0xFF:
            raise MctpError(f"MCTP EID {self.value:#x} out of 8-bit range")

    @property
    def is_null(self) -> bool:
        return self.value == 0x00

    @property
    def is_broadcast(self) -> bool:
        return self.value == 0xFF

    @property
    def is_reserved(self) -> bool:
        return (1 <= self.value <= 7) or (0xF0 <= self.value <= 0xFE)

    @property
    def is_routable(self) -> bool:
        return 0x08 <= self.value <= 0xEF


class MctpMessageType(IntEnum):
    """MCTP message-type byte (the first message-body byte after the IC bit).

    Only the types NVMe-MI actually uses are enumerated. Per DSP0239 §6.5.
    """
    MCTP_CONTROL = 0x00              # MCTP control messages (Get Endpoint ID, ...)
    PLDM = 0x01
    NCSI = 0x02
    ETHERNET = 0x03
    NVME_MI = 0x04                   # NVM Express Management Messages over MCTP
    SPDM = 0x05                      # Security Protocol & Data Model
    VENDOR_DEFINED = 0x7E


@dataclass
class MctpMessage:
    """One MCTP message, post-reassembly.

    The ``body`` payload is the spec-defined "message data" after the
    8-bit MCTP message type byte. Code that produces or consumes this
    *never* sees per-packet framing — reassembly is the transport's job.
    """
    source: MctpEndpointId
    destination: MctpEndpointId
    message_type: MctpMessageType
    body: bytes = field(default=b"")
    message_tag: int = 0             # 0..7, owner-rotated for paired req/resp
    integrity_check: bool = False    # IC bit per DSP0239 §6.4

    def __post_init__(self) -> None:
        if not 0 <= self.message_tag <= 7:
            raise MctpError(f"MCTP message tag {self.message_tag} out of 0..7")

    def __len__(self) -> int:
        return len(self.body) + 1    # +1 for the type byte


# ----------------------------------------------------------------------------
# Transport ABC
# ----------------------------------------------------------------------------
class MctpTransport(abc.ABC):
    """One MCTP transport binding (SMBus/I²C, I3C, PCIe-VDM, USB).

    Subclasses implement ``send`` / ``recv`` against their physical bus; the
    framing layer (this module's other types) sits above the transport so the
    NVMe-MI message layer never touches the bus directly.
    """

    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def send(self, msg: MctpMessage) -> None: ...

    @abc.abstractmethod
    def recv(self, *, timeout_s: float = 5.0) -> MctpMessage: ...

    def __enter__(self) -> MctpTransport:
        self.open()
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


# ----------------------------------------------------------------------------
# Mock transport (deterministic, in-memory)
# ----------------------------------------------------------------------------
class MockMctpTransport(MctpTransport):
    """An in-memory MCTP transport for tests.

    A test queues responses ahead of time with ``queue_response``; ``send``
    captures the request, and ``recv`` returns the next queued response. Use
    a custom ``responder`` for stateful (request -> response) mocking.
    """

    def __init__(self, *, our_eid: int = 0x08,
                 responder=None) -> None:
        self.our_eid = MctpEndpointId(our_eid)
        self.opened = False
        self.closed = False
        self.sent: list[MctpMessage] = []
        self._responses: list[MctpMessage] = []
        self._responder = responder

    def queue_response(self, msg: MctpMessage) -> None:
        """Enqueue a message that the next ``recv`` will return."""
        self._responses.append(msg)

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def send(self, msg: MctpMessage) -> None:
        self.sent.append(msg)
        if self._responder is not None:
            resp = self._responder(msg)
            if resp is not None:
                self._responses.append(resp)

    def recv(self, *, timeout_s: float = 5.0) -> MctpMessage:
        if not self._responses:
            raise MctpError("no MCTP response queued (mock recv timeout)")
        return self._responses.pop(0)
