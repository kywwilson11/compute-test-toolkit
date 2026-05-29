"""
NVMe-MI Management Endpoint Reset.

NVMe-MI §5.7 defines the Reset command (opcode 0x07). Unlike a controller
reset (which the in-band path issues via CC.EN), the management endpoint
reset re-initializes ONLY the MI endpoint state — useful for clearing a
stuck MCTP transaction without disturbing the data path the host kernel is
driving.

Two operations the spec allows:

* **NVM Subsystem Reset** (RSF = 0): resets every controller in the
  subsystem.
* **NVM Subsystem Reset Inhibit** (RSF = 1): suppress the next NSS reset.

The toolkit exposes both behind a small enum so callers can't pass a
magic number.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class ResetFunction(IntEnum):
    """RSF field per NVMe-MI §5.7 Table 95."""
    NVM_SUBSYSTEM_RESET = 0
    SUBSYSTEM_RESET_INHIBIT = 1


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

    **Operator note:** on a real station, issuing this command stops
    in-flight MI transactions on the management endpoint. The data-plane
    NVMe traffic (the kernel `nvme` driver) is unaffected, but a BMC
    polling SMART via MI will see one round of timeouts.
    """
    if mock or transport is None:
        return ResetResult(function=function, accepted=True)
    raise NotImplementedError(                              # pragma: no cover
        "Real Management Endpoint Reset needs an open MctpTransport + an "
        "NVMe-MI RESET request encoder. Use mock=True for unit tests, or "
        "wire libmctp + the encoder once a station provides them.")
