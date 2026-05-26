"""
PCIe Advanced Error Reporting (AER): decode the status registers into named errors,
and clear them correctly (write-1-to-clear, only the set bits, with verify).

The decode tables turn "errors=5" into which *layer* failed — the first fork in any
PCIe debug. The clear primitive is the tool's core building block: you must arm
(clear) the latches before a measurement, and to *count* errors you clear fast and
re-read so each new error can latch (see backend.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from .backend import (AER_CORR_STATUS, AER_UNCORR_STATUS, DEVSTA_CORR, DEVSTA_FATAL,
                      DEVSTA_NONFATAL, DEVSTA_UR, ECAP_AER, Backend)

# bit -> (short name, what it usually means)
CORRECTABLE_BITS: dict[int, tuple[str, str]] = {
    0:  ("RxErr", "Receiver Error — raw physical-layer symbol error (SI)"),
    6:  ("BadTLP", "Bad TLP — LCRC/sequence error; TLP was NAK'd and replayed"),
    7:  ("BadDLLP", "Bad DLLP — corrupted ACK/NAK/flow-control packet"),
    8:  ("RollOver", "REPLAY_NUM Rollover — replay counter wrapped (many retries)"),
    12: ("ReplayTO", "Replay Timer Timeout — no ACK in time; classic marginal link"),
    13: ("AdvNonFatal", "Advisory Non-Fatal — an uncorrectable error demoted to advisory"),
    14: ("CorrIntErr", "Corrected Internal Error — device-internal corrected error"),
    15: ("HdrLogOvf", "Header Log Overflow — more errors than the log could hold"),
}

UNCORRECTABLE_BITS: dict[int, tuple[str, str]] = {
    4:  ("DLP", "Data Link Protocol Error — sequence/ACK protocol violation"),
    5:  ("SurpriseDown", "Surprise Down — link dropped unexpectedly (device/power lost)"),
    12: ("PoisonedTLP", "Poisoned TLP Received — upstream sent data marked bad"),
    13: ("FlowCtrl", "Flow Control Protocol Error — credit/flow-control violation"),
    14: ("CmplTO", "Completion Timeout — a read never got its completion"),
    15: ("CmplAbort", "Completer Abort — target refused the request"),
    16: ("UnexpCmpl", "Unexpected Completion — completion with no matching request"),
    17: ("RxOverflow", "Receiver Overflow — receiver buffer overran"),
    18: ("MalformedTLP", "Malformed TLP — structurally invalid packet"),
    19: ("ECRC", "ECRC Error — end-to-end CRC mismatch"),
    20: ("UnsupReq", "Unsupported Request — target doesn't support that request"),
    21: ("ACSViol", "ACS Violation — access-control blocked a peer-to-peer TLP"),
    22: ("UncorrInt", "Uncorrectable Internal Error — device-internal uncorrectable error"),
}


def decode(value: int, table: dict[int, tuple[str, str]]) -> list[tuple[int, str, str]]:
    """Return [(bit, name, meaning), ...] for each bit set in ``value``."""
    out = []
    for bit, (name, meaning) in sorted(table.items()):
        if value & (1 << bit):
            out.append((bit, name, meaning))
    return out


def decode_correctable(value: int) -> list[tuple[int, str, str]]:
    return decode(value, CORRECTABLE_BITS)


def decode_uncorrectable(value: int) -> list[tuple[int, str, str]]:
    return decode(value, UNCORRECTABLE_BITS)


@dataclass
class AerSnapshot:
    bdf: str
    aer_base: int | None
    correctable_raw: int
    uncorrectable_raw: int

    @property
    def correctable(self) -> list[tuple[int, str, str]]:
        return decode_correctable(self.correctable_raw)

    @property
    def uncorrectable(self) -> list[tuple[int, str, str]]:
        return decode_uncorrectable(self.uncorrectable_raw)

    @property
    def has_uncorrectable(self) -> bool:
        return self.uncorrectable_raw != 0

    @property
    def has_correctable(self) -> bool:
        return self.correctable_raw != 0


def aer_base(backend: Backend, bdf: str) -> int | None:
    return backend.find_ext_cap(bdf, ECAP_AER)


def snapshot(backend: Backend, bdf: str) -> AerSnapshot:
    """Read the AER correctable + uncorrectable status registers (no clearing)."""
    base = aer_base(backend, bdf)
    if base is None:
        return AerSnapshot(bdf, None, 0, 0)
    cor = backend.read_config(bdf, base + AER_CORR_STATUS, 4)
    unc = backend.read_config(bdf, base + AER_UNCORR_STATUS, 4)
    return AerSnapshot(bdf, base, cor, unc)


@dataclass
class ClearResult:
    cleared: int        # bits that were set and got cleared
    stuck: int          # bits that were set and refused to clear (persistent/sticky)

    @property
    def ok(self) -> bool:
        return self.stuck == 0


def _clear_one(backend: Backend, bdf: str, offset: int) -> ClearResult:
    """Write-1-to-clear *only the set bits*, then verify. The elegant W1C trick:
    read the status and write that same value back — because only set bits are 1,
    it clears exactly those and touches nothing else. A bit that won't clear is
    itself a finding (a stuck/persistent hardware error)."""
    status = backend.read_config(bdf, offset, 4)
    if status == 0:
        return ClearResult(cleared=0, stuck=0)           # nothing set -> do nothing
    backend.write_config(bdf, offset, status, 4)          # W1C: write back the set bits
    after = backend.read_config(bdf, offset, 4)
    stuck = after & status
    return ClearResult(cleared=status & ~stuck, stuck=stuck)


def clear(backend: Backend, bdf: str, correctable: bool = True,
          uncorrectable: bool = True) -> dict[str, ClearResult]:
    """Clear (arm) the AER status latches. Returns what was cleared / what stuck."""
    base = aer_base(backend, bdf)
    if base is None:
        return {}
    res: dict[str, ClearResult] = {}
    if correctable:
        res["correctable"] = _clear_one(backend, bdf, base + AER_CORR_STATUS)
    if uncorrectable:
        res["uncorrectable"] = _clear_one(backend, bdf, base + AER_UNCORR_STATUS)
    return res


# --- Unified error source: AER (rich) preferred, else Device Status (coarse) ---- #
# Coarse Device-Status decode (the no-AER fallback; no per-type breakdown).
DEVSTATUS_COR_BITS: dict[int, tuple[str, str]] = {
    0: ("CorrErrDetected", "Device Status: a correctable error was detected (no breakdown)"),
}
DEVSTATUS_UNC_BITS: dict[int, tuple[str, str]] = {
    1: ("NonFatalDetected", "Device Status: a non-fatal uncorrectable error was detected"),
    2: ("FatalDetected", "Device Status: a fatal uncorrectable error was detected"),
}


@dataclass
class ErrorReading:
    """A reading from whichever error source the device exposes."""
    correctable_raw: int
    uncorrectable_raw: int
    source: str           # "aer" | "devstatus" | "none"

    @property
    def correctable(self) -> list[tuple[int, str, str]]:
        tbl = CORRECTABLE_BITS if self.source == "aer" else DEVSTATUS_COR_BITS
        return decode(self.correctable_raw, tbl)

    @property
    def uncorrectable(self) -> list[tuple[int, str, str]]:
        tbl = UNCORRECTABLE_BITS if self.source == "aer" else DEVSTATUS_UNC_BITS
        return decode(self.uncorrectable_raw, tbl)

    @property
    def has_correctable(self) -> bool:
        return self.correctable_raw != 0

    @property
    def has_uncorrectable(self) -> bool:
        return self.uncorrectable_raw != 0


def error_source(backend: Backend, bdf: str) -> str:
    """Which error source this device exposes: 'aer' (preferred), 'devstatus', or 'none'."""
    if aer_base(backend, bdf) is not None:
        return "aer"
    if backend.read_device_status(bdf) is not None:
        return "devstatus"
    return "none"


def read_errors(backend: Backend, bdf: str, source: str | None = None) -> ErrorReading:
    """Read correctable/uncorrectable status from AER if present, else Device Status.
    AER is preferred (per-type breakdown); Device Status is the universal fallback."""
    src = source or error_source(backend, bdf)
    if src == "aer":
        base = aer_base(backend, bdf)
        if base is not None:
            cor = backend.read_config(bdf, base + AER_CORR_STATUS, 4)
            unc = backend.read_config(bdf, base + AER_UNCORR_STATUS, 4)
            return ErrorReading(cor, unc, "aer")
        # A caller forced source="aer" but this device has no AER capability; fall back to
        # the universal source instead of crashing on (None + offset). (clear() guards the
        # same way.)
        src = "devstatus" if backend.read_device_status(bdf) is not None else "none"
    if src == "devstatus":
        ds = backend.read_device_status(bdf) or 0
        cor = DEVSTA_CORR if (ds & DEVSTA_CORR) else 0
        unc = ds & (DEVSTA_NONFATAL | DEVSTA_FATAL)   # UR (bit 3) reported separately, not auto-fail
        return ErrorReading(cor, unc, "devstatus")
    return ErrorReading(0, 0, "none")


def clear_errors(backend: Backend, bdf: str, source: str) -> None:
    """Clear (arm) whichever error source is in use."""
    if source == "aer":
        clear(backend, bdf)
    elif source == "devstatus":
        backend.clear_device_status(bdf)
