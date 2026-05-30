"""
CXL Component Command Interface (CCI) mailbox abstraction (Sprint 4.4).

Mirrors the NVMe-MI ``MctpTransport`` / ``MiMessage`` pattern: a CCI is the
command/response mailbox a CXL device exposes (in-band over MMIO, or out-of-band
over MCTP / the CXL tunnel). This models the command framing — opcode + input
payload -> return code + output payload — with a ``MailboxTransport`` ABC and a
``MockMailbox`` test double; real in-band sysfs/MMIO and QEMU backends plug in by
subclassing.

NOTE: the opcode and return-code values are a representative subset; confirm the
exact per-command codes against the CXL spec / CTS before encoding as assertions.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import IntEnum


class MailboxError(RuntimeError):
    """A CXL CCI mailbox fault (no response, transport error)."""


class CciOpcode(IntEnum):
    """CXL CCI opcodes (command-set byte << 8 | command). Representative subset."""
    IDENTIFY = 0x0001
    GET_FW_INFO = 0x0200
    GET_TIMESTAMP = 0x0300
    GET_EVENT_RECORDS = 0x0100
    CLEAR_EVENT_RECORDS = 0x0101
    GET_FEATURES = 0x0501
    SET_FEATURES = 0x0502
    PERFORM_MAINTENANCE = 0x0600
    GET_PARTITION_INFO = 0x4000
    GET_HEALTH_INFO = 0x4200
    GET_POISON_LIST = 0x4300
    SANITIZE = 0x4400


class CciReturnCode(IntEnum):
    """CXL mailbox return codes (subset)."""
    SUCCESS = 0x0000
    BACKGROUND_STARTED = 0x0001
    INVALID_INPUT = 0x0002
    UNSUPPORTED = 0x0003
    BUSY = 0x0009


@dataclass
class CxlCommand:
    """One CCI command: an opcode + an input payload."""
    opcode: CciOpcode
    payload: bytes = b""


@dataclass
class CxlResponse:
    """One CCI response: a return code + an output payload."""
    return_code: CciReturnCode
    payload: bytes = b""

    @property
    def ok(self) -> bool:
        return self.return_code == CciReturnCode.SUCCESS

    @property
    def background_started(self) -> bool:
        return self.return_code == CciReturnCode.BACKGROUND_STARTED


class MailboxTransport(abc.ABC):
    """One CXL CCI mailbox binding (in-band MMIO, MCTP tunnel, QEMU)."""

    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def send_command(self, cmd: CxlCommand) -> CxlResponse: ...

    def __enter__(self) -> MailboxTransport:
        self.open()
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


class MockMailbox(MailboxTransport):
    """An in-memory CCI mailbox for tests.

    Queue responses with ``queue_response`` (FIFO), or pass a ``responder``
    callable for stateful (command -> response) mocking."""

    def __init__(self, *, responder=None) -> None:
        self.opened = False
        self.closed = False
        self.sent: list[CxlCommand] = []
        self._responses: list[CxlResponse] = []
        self._responder = responder

    def queue_response(self, resp: CxlResponse) -> None:
        self._responses.append(resp)

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def send_command(self, cmd: CxlCommand) -> CxlResponse:
        self.sent.append(cmd)
        if self._responder is not None:
            resp = self._responder(cmd)
            if resp is not None:
                return resp
        if not self._responses:
            raise MailboxError(f"no response queued for {cmd.opcode.name}")
        return self._responses.pop(0)
