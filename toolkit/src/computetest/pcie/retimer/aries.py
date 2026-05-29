"""
Astera Labs Aries retimer — concrete ``Retimer`` implementation, SDK-gated.

The Aries Smart Cable / Smart Retimer family (asteralabs.com/products) is the
hyperscaler-default PCIe Gen5 / Gen6 retimer (NVIDIA HGX H100, OCP DC-SFF,
Blackwell HGX). Aries telemetry is read via the Aries Smart Retimer SDK
(`pip install ariesSDK`) on a host with the COMET USB-I2C dongle attached, or
via the in-band Aries shim Linux driver.

The SDK and the I2C transport are vendor-NDA; this module is the **boundary**:
the right shape, the right method names, the right return types, but the
actual register reads raise ``NotImplementedError`` until the SDK is wired up.

To enable on a real station:

1. ``pip install ariesSDK`` (or check it into the lab's SDK repo).
2. Set ``COMET_I2C_DEVICE`` env var to the COMET USB-I2C dongle path.
3. Replace the ``_read_register`` body with the SDK call. The rest of the
   methods already shape the returned data into the vendor-agnostic
   ``EyeMeasurement`` / ``EqLevels`` / ``BISTResult`` dataclasses defined in
   ``base.py``; downstream code never sees vendor specifics.

Reference: Astera Labs FAQ + COMET SDK quickstart (NDA, available to
qualified compute-platform programs).
"""
from __future__ import annotations

import importlib.util
from typing import Any

from .base import (
    DEFAULT_BIST_DURATION_S,
    BISTResult,
    EqLevels,
    EyeMeasurement,
    LoopbackSide,
    PRBSPattern,
    Retimer,
    RetimerError,
    RetimerInfo,
)

# Aries part numbers we recognize (PCIe Gen5/Gen6 family).
_KNOWN_PARTS = {
    "PT5161L": {"lanes": 16, "pcie_gen": 5},        # 16-lane Gen5 cable retimer
    "PT5081L": {"lanes": 8,  "pcie_gen": 5},        # 8-lane Gen5
    "PT6161L": {"lanes": 16, "pcie_gen": 6},        # 16-lane Gen6 SCM
}


def is_aries_sdk_available() -> bool:
    """True iff the (NDA) ``ariesSDK`` Python package is importable."""
    return importlib.util.find_spec("ariesSDK") is not None


class AriesRetimer(Retimer):
    """Astera Aries retimer adapter.

    Every telemetry path is a thin wrapper over the Aries SDK: open the COMET
    transport, read the documented register, decode into the vendor-agnostic
    dataclass. Until the SDK is wired up, every call raises
    ``NotImplementedError`` — the boundary stays useful as a typed contract
    that downstream code can already program against.
    """

    def __init__(self, part_number: str = "PT5161L", *, address: int = 0x55,
                 transport: str = "comet", serial: str = "",
                 firmware: str = "") -> None:
        super().__init__()
        if part_number not in _KNOWN_PARTS:
            raise RetimerError(
                f"unknown Aries part {part_number!r}; known: {sorted(_KNOWN_PARTS)}")
        meta = _KNOWN_PARTS[part_number]
        self._info = RetimerInfo(
            vendor="Astera Labs", part_number=part_number,
            serial=serial, firmware=firmware,
            lanes=meta["lanes"], pcie_gen=meta["pcie_gen"])
        self.address = address
        self.transport = transport
        self._sdk: Any = None        # ariesSDK.PT5161L() handle, when wired up

    # --- identity --------------------------------------------------------
    def info(self) -> RetimerInfo:
        return self._info

    # --- private register access (SDK boundary) --------------------------
    def _open_sdk(self) -> None:
        """Open the SDK + COMET transport. Idempotent.

        Replace this with the documented Aries SDK initialization (``aries =
        ariesSDK.PT5161L(comet=..., addr=self.address); aries.init()``) when
        you have access to the SDK.
        """
        if self._sdk is not None:
            return
        if not is_aries_sdk_available():
            raise NotImplementedError(
                "Aries SDK not installed (`pip install ariesSDK` — NDA package). "
                "Use MockRetimer for tests, or wire up _open_sdk() once the SDK "
                "is provisioned on this station.")
        # When the SDK is available, the canonical bringup is documented in the
        # Aries COMET SDK quickstart. Left as NotImplementedError so the
        # boundary remains explicit:
        raise NotImplementedError(
            "Aries SDK present, but _open_sdk() is a documented stub. "
            "Replace with ariesSDK.PT5161L(comet=..., addr=...).init()")

    def _check_lane(self, lane: int) -> None:
        if lane < 0 or lane >= self._info.lanes:
            raise RetimerError(
                f"lane {lane} out of range [0, {self._info.lanes - 1}]")

    # --- telemetry (all stubs until SDK is wired) ----------------------
    def read_eye(self, lane: int) -> EyeMeasurement:
        self._check_lane(lane)
        self._open_sdk()                                # always raises today
        raise NotImplementedError                       # for static analysis

    def read_eq(self, lane: int) -> EqLevels:
        self._check_lane(lane)
        self._open_sdk()
        raise NotImplementedError

    def read_temperature(self) -> float:
        self._open_sdk()
        raise NotImplementedError

    def set_loopback(self, side: LoopbackSide, enable: bool) -> None:
        self._open_sdk()
        raise NotImplementedError

    def run_prbs_bist(self, pattern: PRBSPattern = PRBSPattern.PRBS31,
                      duration_s: float = DEFAULT_BIST_DURATION_S) -> BISTResult:
        self._open_sdk()
        raise NotImplementedError
