"""
NVMe-MI Reset command (opcode 0x07).

NVMe-MI Reset (Figure 104; §8.3) takes a single 1-byte **Reset Type** field
in NVMe Management Dword0 bits 31:24. The spec defines exactly ONE value:

* ``00h`` — **Reset NVM Subsystem**. ``01h``–``FFh`` are Reserved.

Blast radius (operator-critical): value 00h initiates a full **NVM Subsystem
Reset** — it resets the *entire* subsystem (all controllers, all ports), so
the host kernel ``nvme`` driver loses its controllers and must re-enable
them via CC.EN. This is NOT a quiet MI-endpoint-only reset and it DOES
disrupt host I/O. (The transport-triggered "Management Endpoint Reset" of
§8.3.3 — a PCIe/SMBus reset — is a different mechanism, not this command.)

The toolkit exposes the single defined Reset Type behind an enum so callers
can't pass a magic number; the Reserved 01h–FFh values are intentionally
absent so a future encoder can never put a Reserved byte on the wire.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ResetFunction(IntEnum):
    """Reset Type field per NVMe-MI Reset command (Figure 104; §8.3).

    Only ``00h`` is defined; ``01h``–``FFh`` are Reserved, so this enum has a
    single member by design (a Reserved value must never reach the wire).
    """
    NVM_SUBSYSTEM_RESET = 0   # 00h = Reset NVM Subsystem (resets all controllers)


@dataclass
class ResetRequest:
    """One Management Endpoint Reset request."""
    function: ResetFunction = ResetFunction.NVM_SUBSYSTEM_RESET


@dataclass
class ResetResult:
    """One Management Endpoint Reset response — just an ack with the
    function code echoed."""
    function: ResetFunction
    accepted: bool


def reset_management_endpoint(transport=None, *,
                               function: ResetFunction = ResetFunction.NVM_SUBSYSTEM_RESET,
                               mock: bool = True) -> ResetResult:
    """Issue a Management Endpoint Reset.

    Mock path returns an accepted response. Real-bus path raises
    ``NotImplementedError`` until libmctp + a request encoder are wired up.

    **Operator note (DISRUPTIVE):** on a real station this issues a full NVM
    Subsystem Reset (Reset Type 00h). It resets *every* controller in the
    subsystem, so the host kernel ``nvme`` driver loses its controllers and
    must re-enable them (CC.EN) — host I/O IS interrupted, not just MI
    traffic. Quiesce the data plane before calling on a live system.
    """
    if mock or transport is None:
        return ResetResult(function=function, accepted=True)
    raise NotImplementedError(                              # pragma: no cover
        "Real Management Endpoint Reset needs an open MctpTransport + an "
        "NVMe-MI RESET request encoder. Use mock=True for unit tests, or "
        "wire libmctp + the encoder once a station provides them.")
