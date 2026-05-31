"""
NVMe-MI v2.1 message framing.

NVMe-MI defines three command queues sitting above MCTP message type 0x04:

1. **Control Primitives** — pause/resume/abort the message processing
   (NMI-MJD bit set). Per-spec: Pause, Resume, Abort, Get State, Replay.
2. **NVMe-MI Command Set** — out-of-band management commands directed at the
   management endpoint (Configuration Get/Set, VPD Read, NVM Subsystem
   Health Status Poll, etc.).
3. **NVMe Admin Command Set** — the standard NVMe admin commands tunneled
   over MI for OOB access (Identify, Get Log Page, Get Features, ...).

The on-wire framing this module encodes is the NVMe-MI Message body the MCTP
transport carries (after the MCTP message-type byte, which ``mctp.py`` adds):

  byte 0: NMP — ROR (bit 7) | NMIMT message type (bits 5:3) | CSI (bit 0)
  byte 1: NVMe-MI / NVMe-Admin opcode
  bytes 2..5: NVMe Management Request Header (NMHDR)
  bytes 6..n: Request/response data
  bytes n+1..n+4: MIC (Message Integrity Check, CRC-32C over bytes 0..n)

The NMP byte layout is verified against the NVMe-MI spec / libnvme
(``mi.c``: ``nmp = (ROR << 7) | (NMIMT << 3) | (csi & 1)``).

This module encodes/decodes that frame as ``MiMessage`` dataclasses. Real
transport runs over ``mctp.MctpTransport``; tests can synthesize MI messages
without a transport.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntEnum


class MiMessageError(RuntimeError):
    """An NVMe-MI framing or CRC fault."""


class MessageType(IntEnum):
    """NVMe-MI Message Type (NMIMT), the 3-bit field at NMP byte bits 5..3."""
    CONTROL_PRIMITIVE = 0x00
    NVME_MI_COMMAND = 0x01
    NVME_ADMIN_COMMAND = 0x02
    PCIE_COMMAND = 0x04
    AEM = 0x05                       # Async Event Messages


class ControlPrimitive(IntEnum):
    """NVMe-MI Control Primitive opcodes (NVMe-MI §4.1).

    Pause + Resume gate the queue of pending NVMe-MI commands so a BMC can
    coordinate quiescence around a controller reset.
    """
    PAUSE = 0x00
    RESUME = 0x01
    ABORT = 0x02
    GET_STATE = 0x03
    REPLAY = 0x04


class NvmeMiCommandOpcode(IntEnum):
    """NVMe-MI Management Command Set opcodes (NVMe-MI §5)."""
    READ_NVME_MI_DATA_STRUCTURE = 0x00
    NVM_SUBSYSTEM_HEALTH_STATUS_POLL = 0x01
    CONTROLLER_HEALTH_STATUS_POLL = 0x02
    CONFIGURATION_SET = 0x03
    CONFIGURATION_GET = 0x04
    VPD_READ = 0x05
    VPD_WRITE = 0x06
    RESET = 0x07
    SES_RECEIVE = 0x08
    SES_SEND = 0x09
    MANAGEMENT_ENDPOINT_BUFFER_READ = 0x0A
    MANAGEMENT_ENDPOINT_BUFFER_WRITE = 0x0B
    SHUTDOWN = 0x0C


@dataclass
class MiMessage:
    """One NVMe-MI message (request or response).

    Encodes the frame the spec calls "NVMe-MI Message". ``mic`` is a CRC-32C
    over (NMP + NMHDR + data); a zero-valued ``mic`` on a request signals
    "compute it for me" in ``encode_mi_request``.
    """
    message_type: MessageType
    opcode: int                      # ControlPrimitive | NvmeMiCommandOpcode
    csi: int = 0                     # Command Slot Identifier (NMP bit 0): 0 or 1
    ror: int = 0                     # Request-or-Response (NMP bit 7): 0 req, 1 resp
    nmhdr: bytes = b""               # request header (4 bytes per spec)
    data: bytes = b""
    mic: int = 0                     # CRC-32C; 0 = compute

    def __post_init__(self) -> None:
        if not isinstance(self.message_type, MessageType):
            try:
                self.message_type = MessageType(int(self.message_type))
            except ValueError as e:
                raise MiMessageError(
                    f"unknown MI message type {self.message_type!r}") from e
        if not 0 <= self.opcode <= 0xFF:
            raise MiMessageError(f"opcode {self.opcode} out of 0..255")
        if len(self.nmhdr) != 4:
            raise MiMessageError(
                f"NMHDR must be exactly 4 bytes; got {len(self.nmhdr)}")


def _crc32c(data: bytes) -> int:
    """CRC-32C (Castagnoli polynomial, 0x1EDC6F41) — what NVMe-MI MIC uses.

    The stdlib ``binascii.crc32`` is the IEEE 802.3 polynomial; the toolkit
    implements 32C in pure Python here so we don't add another dependency.
    """
    poly = 0x82F63B78                                       # bit-reversed 0x1EDC6F41
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ poly if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFFFFFF


def encode_mi_request(msg: MiMessage) -> bytes:
    """Encode an ``MiMessage`` as the on-wire byte string the MCTP transport
    will send. A zero ``msg.mic`` is replaced with the computed CRC-32C.
    """
    # NMP byte: ROR (bit 7) | NMIMT message type (bits 5:3) | CSI (bit 0).
    if not 0 <= msg.csi <= 1:
        raise MiMessageError("CSI must be 0 or 1")
    if not 0 <= msg.ror <= 1:
        raise MiMessageError("ROR must be 0 (request) or 1 (response)")
    nmp = (msg.ror << 7) | ((int(msg.message_type) & 0x07) << 3) | (msg.csi & 0x01)
    header = bytes([nmp, msg.opcode]) + msg.nmhdr
    body = header + msg.data
    mic = msg.mic if msg.mic else _crc32c(body)
    return body + struct.pack("<I", mic)


def decode_mi_response(buf: bytes) -> MiMessage:
    """Decode a wire-format NVMe-MI message. Raises ``MiMessageError`` on
    bad length, unknown message type, or MIC mismatch."""
    if len(buf) < 1 + 1 + 4 + 4:                            # NMP + OPCODE + NMHDR + MIC
        raise MiMessageError(f"MI message too short: {len(buf)} bytes")
    nmp = buf[0]
    opcode = buf[1]
    nmhdr = buf[2:6]
    body = buf[:-4]
    mic_bytes = buf[-4:]
    mic = struct.unpack("<I", mic_bytes)[0]
    if mic != _crc32c(body):
        raise MiMessageError(
            f"MI MIC mismatch: expected {_crc32c(body):#010x}, got {mic:#010x}")
    message_type = MessageType((nmp >> 3) & 0x07)
    csi = nmp & 0x01
    ror = (nmp >> 7) & 0x01
    return MiMessage(
        message_type=message_type, opcode=opcode, csi=csi, ror=ror,
        nmhdr=nmhdr, data=buf[6:-4], mic=mic,
    )
