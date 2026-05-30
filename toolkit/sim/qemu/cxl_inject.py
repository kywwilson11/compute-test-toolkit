"""
QEMU cxl_type3 RAS injection client (Sprint 4.4), mirroring sim/qemu/inject.py.

Drives a running qemu-system over QMP to inject CXL RAS errors, poison, and
events into an emulated ``cxl-type3`` device, so the *unmodified* CXL RAS
read/clear path (``cxl/ras.py`` W1C, ``cxl/poison.py``, ``cxl/events.py``) reads
real QEMU-generated state for hardware-free CI. Uses QEMU's ``cxl-inject-*`` QMP
commands.

AUDIT NOTE: confirm QEMU's cxl_type3 actually emulates the UE/CE bits, event
types, and maintenance commands this toolkit asserts — the Sprint-2 x86-TCG AER
bug showed emulator fidelity cannot be assumed.

Pure Python; the command construction is exercised against a fake QMP server in
tests/test_cxl_qmp_inject.py. The QMP transport is the ``QMPClient`` from
``sim/qemu/inject.py`` (any object exposing ``execute(command, **arguments)``).
"""
from __future__ import annotations


class CxlInjector:
    """Builds the ``cxl-inject-*`` QMP commands against a QMP-execute client.

    ``qmp`` is any object exposing ``execute(command, **arguments)`` — e.g. the
    ``QMPClient`` from ``sim/qemu/inject.py`` connected to a running guest.
    """

    def __init__(self, qmp) -> None:
        self._qmp = qmp

    def inject_poison(self, path: str, *, start: int, length: int):
        """Inject a poison record at device ``path`` over [start, start+length)."""
        return self._qmp.execute("cxl-inject-poison", path=path, start=start,
                                 length=length)

    def inject_uncorrectable(self, path: str, *, errors: list[dict]):
        """Inject one or more CXL uncorrectable RAS errors (each ``{type, ...}``)."""
        return self._qmp.execute("cxl-inject-uncorrectable-errors", path=path,
                                 errors=errors)

    def inject_correctable(self, path: str, *, error_type: str):
        """Inject a CXL correctable RAS error of ``error_type``."""
        return self._qmp.execute("cxl-inject-correctable-error", path=path,
                                 type=error_type)

    def inject_general_media_event(self, path: str, *, log: str, flags: int,
                                   dpa: int):
        """Inject a General Media event record into the given event ``log``."""
        return self._qmp.execute("cxl-inject-general-media-event", path=path,
                                 log=log, flags=flags, dpa=dpa)
