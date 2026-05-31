"""
Bench-instrument control over VISA/SCPI — the other half of a real test station.

The PCIe BERT proves a *link* is good; this module drives the *box around it*: the
programmable supplies that set the rail voltages (and let you margin them +/-5%), the
DMMs that audit those rails, the electronic loads that pull current for a power test,
and the scopes that capture an eye or a ripple measurement. Manufacturing test,
board bring-up, and the BERT/power setups all lean on exactly these four instrument
classes, all speaking SCPI (Standard Commands for Programmable Instruments) over a
VISA transport (USB-TMC / LXI / GPIB / serial).

Same mock/real split as backend.py and the interface checks: on a real station we
talk to `pyvisa` (an optional dependency); on a laptop with no instruments and no
pyvisa installed, ``mock_mode()`` (or a missing pyvisa) routes every call to a small
deterministic in-memory SCPI simulator. So the whole module imports, runs, and
unit-tests on macOS with nothing attached — set a "measurement" with
``instr.transport.set_measurement(...)`` and assert on the exact SCPI you sent.

SCPI notes worth remembering
----------------------------
* Commands end in '?' are *queries* (they return a response); the rest are setters.
* A reading that overflows or is "not a number" comes back as ``9.9E37`` — the IEEE
  488.2 sentinel. ``parse_scpi_float`` maps that (and ``+9.9E37``/``-9.9E37``) to
  +/-inf so a saturated DMM doesn't look like a real 9.9e37-volt reading.
* ``SYST:ERR?`` pops one entry off the instrument's error queue as ``<code>,"<msg>"``;
  code 0 means "No error". After any command you distrust, drain that queue — a
  command the instrument rejected fails *silently* on the bus otherwise.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .backend import mock_mode

# --- Optional dependency: pyvisa (present only on a real test station) -------- #
try:  # pragma: no cover - exercised only where pyvisa is installed
    import pyvisa  # type: ignore
    _HAVE_PYVISA = True
except Exception:  # pragma: no cover - the bare-laptop path this module is built for
    pyvisa = None
    _HAVE_PYVISA = False


# IEEE 488.2 overflow / not-a-number sentinel returned by a saturated measurement.
SCPI_OVERFLOW = 9.9e37


class InstrumentError(RuntimeError):
    """A bench-instrument fault: a VISA I/O error, a timeout, a malformed response,
    or a non-empty SCPI error queue. One exception type so callers (the harness, the
    power-test step) can ``except InstrumentError`` regardless of which layer failed."""


@dataclass
class Reading:
    """One parsed measurement: the numeric ``value`` in ``unit``, plus the ``raw``
    SCPI string it was parsed from (kept for logging/SPC — the bench wants the exact
    bytes the instrument returned, not just the rounded float)."""

    value: float
    unit: str = ""
    raw: str = ""

    @property
    def overflow(self) -> bool:
        """True if the instrument reported overflow / no-reading (9.9E37 -> +/-inf)."""
        return self.value in (float("inf"), float("-inf"))

    def __str__(self) -> str:
        return f"{self.value:g} {self.unit}".strip()


def parse_scpi_float(raw: str) -> float:
    """Parse a SCPI numeric response to a float, mapping the 9.9E37 overflow sentinel
    to +/-inf. Raises ``InstrumentError`` on a malformed (non-numeric) response so a
    garbled read can't masquerade as a measurement."""
    if raw is None:
        raise InstrumentError("empty SCPI response (expected a number)")
    text = raw.strip().rstrip(";")
    if not text:
        raise InstrumentError("empty SCPI response (expected a number)")
    # A reading may arrive with a unit suffix ("1.5V") or in a comma list ("1.5,2.0");
    # take the first field and strip a trailing non-numeric tail.
    first = text.split(",")[0].strip()
    match = re.match(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", first)
    if not match:
        raise InstrumentError(f"malformed SCPI numeric response: {raw!r}")
    # The regex above only matches valid float literals, so float() cannot raise here.
    value = float(match.group(0))
    if abs(value) >= SCPI_OVERFLOW:
        return float("inf") if value > 0 else float("-inf")
    return value


def _require_finite(value: float, what: str) -> float:
    """Return ``value`` if finite, else raise ``InstrumentError``. A SCPI counter that
    saturates / is uninitialized returns the 9.9E37 overflow sentinel, which
    ``parse_scpi_float`` maps to +/-inf; ``int()`` of that raises a bare ``OverflowError``
    and a non-finite bit count silently collapses BER to 0.0. Funnel every count through
    here so such a fault surfaces as the documented ``InstrumentError`` instead."""
    if not math.isfinite(value):
        raise InstrumentError(f"{what} returned a non-finite value ({value:g})")
    return value


# ----------------------------------------------------------------------------- #
# Transports — the byte pipe to the instrument
# ----------------------------------------------------------------------------- #
class _MockSCPI:
    """A deterministic in-memory SCPI instrument, good enough that the whole module
    runs and unit-tests with no hardware and no pyvisa. It records every command
    written (so a test can assert on the exact SCPI string sent), answers the IEEE
    488.2 common queries (*IDN?, *RST, *CLS, *OPC?), maintains a settable register of
    "state" (the last VOLT/CURR/FUNC/etc. you sent), and returns test-settable values
    for measurement queries.

    Tests drive it through the owning instrument's ``transport`` handle:
    ``ps.transport.set_measurement("MEAS:VOLT?", 12.0)`` then assert on
    ``ps.transport.writes``.
    """

    def __init__(self, idn: str = "MockCorp,Model-1,SN0001,1.0", *,
                 fail_on_open: bool = False, raise_on_query: Exception | None = None,
                 timeout_on: set[str] | None = None):
        self.idn_string = idn
        self.fail_on_open = fail_on_open      # simulate open_resource() failing
        self.raise_on_query = raise_on_query  # simulate a VISA I/O error on the next query
        self.timeout_on = timeout_on or set() # query strings that should time out
        self.timeout = 5000                   # ms, mirrors the pyvisa resource attribute
        self.opened = False
        self.closed = False
        self.writes: list[str] = []           # every command/query string written, in order
        self.state: dict[str, str] = {}       # last value seen for each settable command stem
        self._meas: dict[str, str] = {}       # query -> canned response string
        self._errors: list[str] = []          # the SCPI error queue (FIFO)

    # -- test-side configuration (not part of any instrument protocol) ---------
    def set_measurement(self, query: str, value: Any) -> None:
        """Make ``query`` (e.g. 'MEAS:VOLT:DC?') return ``value`` (number or raw str)."""
        self._meas[query.upper()] = value if isinstance(value, str) else repr(float(value))

    def push_error(self, code: int, message: str) -> None:
        """Queue a SCPI error so the next SYST:ERR? pops it (simulates a rejected cmd)."""
        self._errors.append(f'{code},"{message}"')

    # -- transport interface (what SCPIInstrument calls) -----------------------
    def open(self) -> None:
        if self.fail_on_open:
            raise InstrumentError("mock: failed to open resource")
        self.opened = True

    def close(self) -> None:
        self.closed = True

    def write(self, command: str) -> None:
        self.writes.append(command)
        self._apply(command)

    def query(self, command: str) -> str:
        self.writes.append(command)
        if self.raise_on_query is not None:
            raise self.raise_on_query
        if command in self.timeout_on:
            raise TimeoutError(f"mock: query timed out: {command}")
        return self._respond(command)

    def read(self) -> str:
        # Echo the most recent query-style answer; rarely used directly.
        return self._respond(self.writes[-1]) if self.writes else ""

    # -- internal SCPI behaviour ----------------------------------------------
    def _apply(self, command: str) -> None:
        """Record state for setter commands so a later query can reflect it."""
        cmd = command.strip()
        if cmd == "*CLS":
            self._errors.clear()
            return
        if cmd == "*RST":
            self.state.clear()
            self._errors.clear()
            return
        if " " in cmd:
            stem, _, arg = cmd.partition(" ")
            self.state[stem.upper()] = arg.strip()

    def _respond(self, command: str) -> str:
        cmd = command.strip().upper()
        # An explicit set_measurement() override wins over every built-in default, so a
        # test can force any response (a custom *IDN? read, a malformed SYST:ERR?, ...).
        if cmd in self._meas:
            return self._meas[cmd]
        if cmd == "*IDN?":
            return self.idn_string
        if cmd == "*OPC?":
            return "1"
        if cmd in ("SYST:ERR?", "SYSTEM:ERROR?"):
            return self._errors.pop(0) if self._errors else '0,"No error"'
        # Reflect a previously-set value for the matching query (VOLT? -> last VOLT).
        stem = cmd.rstrip("?")
        if stem in self.state:
            return self.state[stem]
        # A mock thermal chamber's actual temperature tracks its programmed setpoint,
        # so the shmoo settle-poll (MEAS:TEMP? vs SOUR:TEMP?) converges on the first
        # poll. An explicit set_measurement("MEAS:TEMP?", ...) override wins (above).
        if cmd == "MEAS:TEMP?" and "SOUR:TEMP" in self.state:
            return self.state["SOUR:TEMP"]
        return "0"


class _PyVisaTransport:  # pragma: no cover - real-hw path (needs pyvisa + an instrument)
    """Thin wrapper over a pyvisa resource: ResourceManager -> open_resource, then
    write/query/read with a configurable timeout. Isolated here so SCPIInstrument
    never touches pyvisa directly and the rest of the module stays import-clean
    without it."""

    def __init__(self, resource: str, *, timeout_ms: int = 5000,
                 resource_manager: Any = None, read_termination: str | None = None,
                 write_termination: str | None = None):
        self.resource = resource
        self.timeout_ms = timeout_ms
        self._rm = resource_manager
        self._read_term = read_termination
        self._write_term = write_termination
        self._inst: Any = None

    def open(self) -> None:
        try:
            rm = self._rm or pyvisa.ResourceManager()
            self._inst = rm.open_resource(self.resource)
            self._inst.timeout = self.timeout_ms
            if self._read_term is not None:
                self._inst.read_termination = self._read_term
            if self._write_term is not None:
                self._inst.write_termination = self._write_term
        except Exception as exc:  # pyvisa.VisaIOError and friends
            raise InstrumentError(f"failed to open {self.resource}: {exc}") from exc

    def close(self) -> None:
        if self._inst is not None:
            try:
                self._inst.close()
            finally:
                self._inst = None

    @property
    def timeout(self) -> int:
        return self._inst.timeout if self._inst is not None else self.timeout_ms

    @timeout.setter
    def timeout(self, value: int) -> None:
        self.timeout_ms = value
        if self._inst is not None:
            self._inst.timeout = value

    def write(self, command: str) -> None:
        try:
            self._inst.write(command)
        except Exception as exc:
            raise InstrumentError(f"write failed ({command!r}): {exc}") from exc

    def query(self, command: str) -> str:
        try:
            return self._inst.query(command)
        except Exception as exc:
            raise InstrumentError(f"query failed ({command!r}): {exc}") from exc

    def read(self) -> str:
        try:
            return self._inst.read()
        except Exception as exc:
            raise InstrumentError(f"read failed: {exc}") from exc


# ----------------------------------------------------------------------------- #
# Base instrument
# ----------------------------------------------------------------------------- #
class SCPIInstrument:
    """A SCPI instrument over a VISA transport — the common base for the supply, DMM,
    load, and scope. Handles connect/disconnect (and the ``with`` form), the raw
    write/query/read, the IEEE 488.2 common commands (*IDN?, *RST, *CLS, *OPC?), the
    configurable timeout, and the error-queue check that turns a silently-rejected
    command into a raised ``InstrumentError``.

    Pass ``resource`` (e.g. ``'USB0::0x0957::0x1234::SN::INSTR'`` or
    ``'TCPIP::192.168.0.5::INSTR'``) for the real path. With ``mock=True`` (the default
    off a real station, or whenever pyvisa is missing) a deterministic simulator is
    used instead — reach it via ``self.transport`` in tests.
    """

    def __init__(self, resource: str = "MOCK::INSTR", *, mock: bool | None = None,
                 timeout_ms: int = 5000, idn: str = "MockCorp,Model-1,SN0001,1.0",
                 resource_manager: Any = None, transport: Any = None,
                 read_termination: str | None = None,
                 write_termination: str | None = None):
        use_mock = (mock_mode() if mock is None else mock) or not _HAVE_PYVISA
        self.resource = resource
        self.timeout_ms = timeout_ms
        self.is_mock = use_mock if transport is None else isinstance(transport, _MockSCPI)
        if transport is not None:
            self.transport = transport
        elif use_mock:
            self.transport = _MockSCPI(idn=idn)
            self.transport.timeout = timeout_ms
        else:  # pragma: no cover - real-hw path
            self.transport = _PyVisaTransport(
                resource, timeout_ms=timeout_ms, resource_manager=resource_manager,
                read_termination=read_termination, write_termination=write_termination)
        self._open = False

    # -- connection lifecycle --------------------------------------------------
    def open(self) -> SCPIInstrument:
        """Open the VISA resource (idempotent). Returns self for chaining."""
        if not self._open:
            self.transport.open()
            self._open = True
        return self

    def close(self) -> None:
        """Close the VISA resource (safe to call more than once)."""
        if self._open:
            self.transport.close()
            self._open = False

    def __enter__(self) -> SCPIInstrument:
        return self.open()

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- raw I/O ---------------------------------------------------------------
    def write(self, command: str) -> None:
        """Send a SCPI command (no response expected)."""
        self.transport.write(command)

    def query(self, command: str) -> str:
        """Send a SCPI query and return the raw response (stripped). Wraps a transport
        timeout / I/O error as ``InstrumentError``."""
        try:
            response = self.transport.query(command)
        except InstrumentError:
            raise
        except TimeoutError as exc:
            raise InstrumentError(f"timeout waiting for response to {command!r}") from exc
        except Exception as exc:
            raise InstrumentError(f"query failed ({command!r}): {exc}") from exc
        if response is None:
            raise InstrumentError(f"no response to query {command!r}")
        return response.strip()

    def query_float(self, command: str) -> float:
        """Query and parse a single SCPI numeric response (overflow -> +/-inf)."""
        return parse_scpi_float(self.query(command))

    def read(self) -> str:
        """Read a pending response from the transport (stripped)."""
        try:
            return self.transport.read().strip()
        except InstrumentError:
            raise
        except TimeoutError as exc:
            raise InstrumentError("timeout on read") from exc
        except Exception as exc:
            raise InstrumentError(f"read failed: {exc}") from exc

    # -- IEEE 488.2 common commands -------------------------------------------
    def idn(self) -> str:
        """Identify the instrument (*IDN? -> 'maker,model,serial,firmware')."""
        return self.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument to its power-on defaults (*RST)."""
        self.write("*RST")

    def clear_status(self) -> None:
        """Clear the status byte and the error queue (*CLS)."""
        self.write("*CLS")

    def wait_complete(self) -> bool:
        """Block until the prior operations complete (*OPC? returns '1')."""
        return self.query("*OPC?").strip() == "1"

    # -- error queue -----------------------------------------------------------
    def error(self) -> tuple[int, str]:
        """Pop one entry off the error queue (SYST:ERR?): ``(code, message)``.
        Code 0 / 'No error' means the queue was empty."""
        raw = self.query("SYST:ERR?")
        code_text, _, message = raw.partition(",")
        try:
            code = int(float(code_text.strip()))
        except ValueError as e:
            raise InstrumentError(f"malformed SYST:ERR? response: {raw!r}") from e
        return code, message.strip().strip('"')

    def check_errors(self) -> list[str]:
        """Drain the SCPI error queue and raise ``InstrumentError`` if it held anything.
        Call after a command you distrust — a rejected SCPI command fails silently on
        the bus, and this is the only way to notice. Returns an empty list on a clean
        queue; raises (carrying every queued entry) otherwise."""
        collected: list[str] = []
        for _ in range(64):  # bounded so a stuck instrument can't spin forever
            code, message = self.error()
            if code == 0:
                break
            collected.append(f"{code}: {message}")
        if collected:
            raise InstrumentError(
                f"{self.resource}: instrument error queue: " + "; ".join(collected))
        return collected


# ----------------------------------------------------------------------------- #
# Programmable power supply
# ----------------------------------------------------------------------------- #
class PowerSupply(SCPIInstrument):
    """A programmable DC supply: set the rail voltage and a current limit, enable the
    output, then read back what the supply actually delivers. This is what margins a
    rail (e.g. nominal +/-5%) for a BERT or a power-on test and audits droop under an
    electronic load.

    SCPI: ``VOLT <v>`` / ``CURR <a>`` to program; ``OUTP ON|OFF`` to enable;
    ``MEAS:VOLT?`` / ``MEAS:CURR?`` to read back. Multi-output supplies select a
    channel first with ``INST:NSEL <n>``.
    """

    def select_channel(self, channel: int) -> None:
        """Select the output channel on a multi-output supply (INST:NSEL <n>)."""
        self.write(f"INST:NSEL {channel:d}")

    def set_voltage(self, volts: float) -> None:
        """Program the output voltage setpoint (VOLT <v>)."""
        self.write(f"VOLT {volts:g}")

    def set_current(self, amps: float) -> None:
        """Program the current limit (CURR <a>) — the supply goes constant-current here."""
        self.write(f"CURR {amps:g}")

    def enable(self, on: bool = True) -> None:
        """Turn the output on or off (OUTP ON|OFF)."""
        self.write(f"OUTP {'ON' if on else 'OFF'}")

    def disable(self) -> None:
        """Turn the output off (OUTP OFF)."""
        self.enable(False)

    def is_enabled(self) -> bool:
        """Query whether the output is on (OUTP?). Accepts '1'/'ON'."""
        return self.query("OUTP?").strip().upper() in ("1", "ON")

    def measure_voltage(self) -> Reading:
        """Measure the actual output voltage (MEAS:VOLT?)."""
        raw = self.query("MEAS:VOLT?")
        return Reading(parse_scpi_float(raw), "V", raw)

    def measure_current(self) -> Reading:
        """Measure the actual output current (MEAS:CURR?)."""
        raw = self.query("MEAS:CURR?")
        return Reading(parse_scpi_float(raw), "A", raw)


# ----------------------------------------------------------------------------- #
# Digital multimeter
# ----------------------------------------------------------------------------- #
class DMM(SCPIInstrument):
    """A digital multimeter — the independent audit of a rail or a continuity/ESD-strap
    check on the line. ``MEAS:<fn>?`` configures the function and triggers a single
    reading in one shot, which is all a go/no-go test needs.

    SCPI: ``MEAS:VOLT:DC?`` / ``MEAS:CURR:DC?`` / ``MEAS:RES?`` (and the AC variants).
    """

    def measure_voltage(self, ac: bool = False) -> Reading:
        """Measure DC (default) or AC voltage (MEAS:VOLT:DC? / MEAS:VOLT:AC?)."""
        raw = self.query("MEAS:VOLT:AC?" if ac else "MEAS:VOLT:DC?")
        return Reading(parse_scpi_float(raw), "VAC" if ac else "VDC", raw)

    def measure_current(self, ac: bool = False) -> Reading:
        """Measure DC (default) or AC current (MEAS:CURR:DC? / MEAS:CURR:AC?)."""
        raw = self.query("MEAS:CURR:AC?" if ac else "MEAS:CURR:DC?")
        return Reading(parse_scpi_float(raw), "AAC" if ac else "ADC", raw)

    def measure_resistance(self, four_wire: bool = False) -> Reading:
        """Measure resistance, 2-wire (MEAS:RES?) or 4-wire/Kelvin (MEAS:FRES?)."""
        raw = self.query("MEAS:FRES?" if four_wire else "MEAS:RES?")
        return Reading(parse_scpi_float(raw), "Ohm", raw)


# ----------------------------------------------------------------------------- #
# Programmable electronic load
# ----------------------------------------------------------------------------- #
# Valid input-regulation modes and their SCPI mnemonics.
_LOAD_MODES = {"CC": "CURR", "CV": "VOLT", "CR": "RES", "CP": "POW"}


class ElectronicLoad(SCPIInstrument):
    """A programmable DC electronic load: it sinks current to exercise a supply or a
    PSU under test. Pick a regulation mode (constant current / voltage / resistance /
    power), set the operating point, enable the input, then read back what it actually
    pulls — that's a power-delivery / droop test for the board's rails.

    SCPI: ``FUNC CURR|VOLT|RES|POW`` for the mode; the per-mode setpoint
    (``CURR <a>`` etc.); ``INP ON|OFF`` to enable; ``MEAS:VOLT?`` / ``MEAS:CURR?`` /
    ``MEAS:POW?`` to read.
    """

    def set_mode(self, mode: str) -> None:
        """Set the regulation mode: one of CC, CV, CR, CP (FUNC CURR|VOLT|RES|POW)."""
        key = mode.upper()
        if key not in _LOAD_MODES:
            raise InstrumentError(
                f"unknown load mode {mode!r} (expected one of {', '.join(_LOAD_MODES)})")
        self._mode = key
        self.write(f"FUNC {_LOAD_MODES[key]}")

    def set_current(self, amps: float) -> None:
        """Set the constant-current sink level (CURR <a>)."""
        self.write(f"CURR {amps:g}")

    def set_voltage(self, volts: float) -> None:
        """Set the constant-voltage operating point (VOLT <v>)."""
        self.write(f"VOLT {volts:g}")

    def set_resistance(self, ohms: float) -> None:
        """Set the constant-resistance operating point (RES <r>)."""
        self.write(f"RES {ohms:g}")

    def set_power(self, watts: float) -> None:
        """Set the constant-power operating point (POW <w>)."""
        self.write(f"POW {watts:g}")

    def input(self, on: bool = True) -> None:
        """Turn the load input on or off (INP ON|OFF)."""
        self.write(f"INP {'ON' if on else 'OFF'}")

    def measure_voltage(self) -> Reading:
        """Measure the voltage at the load terminals (MEAS:VOLT?)."""
        raw = self.query("MEAS:VOLT?")
        return Reading(parse_scpi_float(raw), "V", raw)

    def measure_current(self) -> Reading:
        """Measure the current the load is sinking (MEAS:CURR?)."""
        raw = self.query("MEAS:CURR?")
        return Reading(parse_scpi_float(raw), "A", raw)

    def measure_power(self) -> Reading:
        """Measure the power the load is dissipating (MEAS:POW?)."""
        raw = self.query("MEAS:POW?")
        return Reading(parse_scpi_float(raw), "W", raw)


# ----------------------------------------------------------------------------- #
# Oscilloscope
# ----------------------------------------------------------------------------- #
# Measurement kinds we expose -> the scope's MEASure operator and the result's unit.
_SCOPE_MEAS = {
    "vpp": ("VPP", "V"),        # peak-to-peak (ripple, eye height)
    "vavg": ("VAVerage", "V"),  # average (a rail's DC level)
    "vrms": ("VRMS", "V"),
    "vmax": ("VMAX", "V"),
    "vmin": ("VMIN", "V"),
    "vamp": ("VAMPlitude", "V"),
    "freq": ("FREQuency", "Hz"),
    "period": ("PERiod", "s"),
    "duty": ("DUTYcycle", "%"),
    "rise": ("RISetime", "s"),
    "fall": ("FALLtime", "s"),
}


class Scope(SCPIInstrument):
    """An oscilloscope, driven through the ``MEASure`` subsystem for automated readings:
    rail ripple (``vpp``), a clock's frequency, an eye's amplitude, an edge's rise time.
    A go/no-go station rarely needs the waveform itself — just the scalar the scope
    already computes — so this exposes ``measure(channel, kind)`` and leaves raw-capture
    out.

    SCPI: ``MEAS:<op>? CHAN<n>`` (e.g. ``MEAS:VPP? CHAN1``). Use ``autoscale()`` to let
    the scope find the signal first.
    """

    def autoscale(self) -> None:
        """Auto-scale all channels (AUToscale)."""
        self.write("AUToscale")

    def measure(self, channel: int, kind: str = "vpp") -> Reading:
        """Take an automated measurement on a channel. ``kind`` is one of the keys in
        ``_SCOPE_MEAS`` (vpp, vavg, vrms, vmax, vmin, vamp, freq, period, duty, rise,
        fall). Raises ``InstrumentError`` for an unknown kind."""
        key = kind.lower()
        if key not in _SCOPE_MEAS:
            raise InstrumentError(
                f"unknown scope measurement {kind!r} "
                f"(expected one of {', '.join(sorted(_SCOPE_MEAS))})")
        op, unit = _SCOPE_MEAS[key]
        raw = self.query(f"MEAS:{op}? CHAN{channel:d}")
        return Reading(parse_scpi_float(raw), unit, raw)

    def measure_vpp(self, channel: int) -> Reading:
        """Peak-to-peak voltage on a channel — the usual ripple/eye-height number."""
        return self.measure(channel, "vpp")

    def measure_frequency(self, channel: int) -> Reading:
        """Frequency on a channel (MEAS:FREQ? CHAN<n>)."""
        return self.measure(channel, "freq")


# ----------------------------------------------------------------------------- #
# Source-Measure Unit (SMU)
# ----------------------------------------------------------------------------- #
# What an SMU sources / what it measures back. Used to validate set_source +
# measure pairs at runtime so e.g. ``smu.measure("VOLT")`` is rejected when the
# unit is configured to FIMV (force I, measure V) — that mode reads current.
_SMU_FUNCS = {"VOLT", "CURR"}


class SMU(SCPIInstrument):
    """A four-quadrant source-measure unit (Keysight B2902 family, Keithley 24xx etc.).

    An SMU *sources* one of voltage/current AND *measures* the other (or both)
    simultaneously to high precision. The two main MT modes:

    * **Force-V / Measure-I (FVMI)** — sweep V across a device, read I (typical
      diode IV curve, gate-leakage test).
    * **Force-I / Measure-V (FIMV)** — push a known I, read V drop (cable IR drop
      check, contact-resistance audit).

    Beyond MT, it's the standard cal-lab gage for power-rail droop and leakage —
    named ``power_smu`` in ``configs/calibration_registry.example.yaml``.

    SCPI shape (manufacturer-specific in detail; common SCPI 1999 channel-style
    here is what Keysight B2900-series accepts and what the Mock simulates):
    ``SOUR:FUNC VOLT|CURR`` to pick the source kind, ``SOUR:VOLT|CURR <v>`` to
    program it, ``SENS:FUNC "VOLT,CURR"`` to enable both sense readbacks,
    ``OUTP ON|OFF`` to enable, ``MEAS:VOLT?`` / ``MEAS:CURR?`` to read.
    """

    def set_source(self, kind: str) -> None:
        """Configure what the SMU sources: ``VOLT`` or ``CURR``."""
        key = kind.upper()
        if key not in _SMU_FUNCS:
            raise InstrumentError(
                f"unknown SMU source {kind!r} (expected one of {sorted(_SMU_FUNCS)})")
        self.write(f"SOUR:FUNC {key}")

    def set_voltage(self, volts: float) -> None:
        """Program the sourced voltage (``SOUR:VOLT <v>``)."""
        self.write(f"SOUR:VOLT {volts:g}")

    def set_current(self, amps: float) -> None:
        """Program the sourced current (``SOUR:CURR <a>``)."""
        self.write(f"SOUR:CURR {amps:g}")

    def set_compliance(self, value: float, kind: str = "CURR") -> None:
        """Set the compliance (output limit) on the OTHER channel — the SMU will
        regulate the source so the measured channel never crosses this. Forces
        FIMV → ``CURR`` compliance on the V side; FVMI → ``VOLT`` compliance.
        """
        key = kind.upper()
        if key not in _SMU_FUNCS:
            raise InstrumentError(
                f"unknown compliance kind {kind!r} (expected one of {sorted(_SMU_FUNCS)})")
        self.write(f"SENS:{key}:PROT {value:g}")

    def enable(self, on: bool = True) -> None:
        """Turn the SMU output on or off (``OUTP ON|OFF``)."""
        self.write(f"OUTP {'ON' if on else 'OFF'}")

    def disable(self) -> None:
        self.enable(False)

    def measure_voltage(self) -> Reading:
        """Read the measured voltage (``MEAS:VOLT?``)."""
        raw = self.query("MEAS:VOLT?")
        return Reading(parse_scpi_float(raw), "V", raw)

    def measure_current(self) -> Reading:
        """Read the measured current (``MEAS:CURR?``)."""
        raw = self.query("MEAS:CURR?")
        return Reading(parse_scpi_float(raw), "A", raw)

    def measure_resistance(self) -> Reading:
        """Derived resistance V/I, computed from the two MEAS reads. Returns
        ``+inf`` when the current reads at-or-near the overflow sentinel."""
        v = self.measure_voltage()
        i = self.measure_current()
        if i.overflow or i.value == 0.0:
            return Reading(float("inf"), "Ohm", f"{v.raw};{i.raw}")
        return Reading(v.value / i.value, "Ohm", f"{v.raw};{i.raw}")

    def sweep_voltage(self, start: float, stop: float, points: int) -> list[Reading]:
        """Discrete linear V sweep with one MEAS:CURR? read per point. Returns
        a list of current readings (length ``points``). For Keysight LXI sweep
        triggering call the channel-specific SCPI directly; this helper is the
        common-case go/no-go shape.
        """
        if points < 2:
            raise InstrumentError("sweep needs >=2 points")
        step = (stop - start) / (points - 1)
        out: list[Reading] = []
        self.set_source("VOLT")
        for i in range(points):
            v = start + step * i
            self.set_voltage(v)
            out.append(self.measure_current())
        return out


# ----------------------------------------------------------------------------- #
# External hardware BERT
# ----------------------------------------------------------------------------- #
_BERT_PATTERNS = {"PRBS7", "PRBS9", "PRBS15", "PRBS23", "PRBS24", "PRBS31", "USER"}


@dataclass
class BertReading:
    """A BERT measurement window: error count, bits transferred, ratio."""
    errors: int
    bits: float
    ber: float                       # errors / bits (0.0 when bits <= 0)
    elapsed_s: float = 0.0
    raw: str = ""


class ExternalBERT(SCPIInstrument):
    """A bench-top BERT (Keysight M8040, Anritsu MP1900, Tektronix BSAVx40 family).

    Where the in-toolkit `bert.run_bert` proves a PCIe link's AER count, an
    external BERT proves the **PHY itself** — symbol-error rates at the SerDes
    level, jitter tolerance, eye opening. This is the instrument you reach for
    when retimer/PHY characterization beyond AER is needed (Sprint 2.2 retimer
    abstraction reads these results, doesn't replace them).

    Shape modeled here: a pattern generator (set pattern + rate + amplitude),
    an arm-then-run window with a deterministic duration, and a poll-result
    returning ``(errors, bits, BER)``. Real BERTs expose much more (jitter
    decomposition, eye contour, BIST stop conditions); this is the
    common-denominator surface that fits SCPI/MT use.

    SCPI: ``:PGEN:PATT PRBS31`` / ``:PGEN:RATE 64e9`` / ``:ARM`` / ``:RUN`` /
    ``:READ:ERR?`` / ``:READ:BITS?``.
    """

    def set_pattern(self, pattern: str = "PRBS31") -> None:
        """Set the pattern: PRBS7/9/15/23/24/31 or USER (PRBS24 added so a bench
        BERT can correlate against the GMSL3 on-die PRBS generator)."""
        key = pattern.upper()
        if key not in _BERT_PATTERNS:
            raise InstrumentError(
                f"unknown BERT pattern {pattern!r} (expected one of {sorted(_BERT_PATTERNS)})")
        self.write(f":PGEN:PATT {key}")

    def set_rate(self, gbps: float) -> None:
        """Set the line rate in Gbps (``:PGEN:RATE``). Common values: 8.0, 16.0,
        32.0, 64.0 (PCIe Gen3/4/5/6 NRZ-equivalent / PAM-4 rates)."""
        if gbps <= 0:
            raise InstrumentError(f"line rate must be > 0; got {gbps}")
        self.write(f":PGEN:RATE {gbps * 1e9:g}")

    def set_amplitude(self, mv: float) -> None:
        """Set the differential output amplitude in mV."""
        self.write(f":PGEN:AMPL {mv:g}")

    def arm(self) -> None:
        """Arm the BERT to begin counting on the next ``:RUN``."""
        self.write(":ARM")

    def run_window(self, seconds: float) -> None:
        """Run for ``seconds`` then halt (``:RUN <s>``). Polling completion is
        the caller's responsibility — typically ``wait_complete()``.
        """
        if seconds <= 0:
            raise InstrumentError(f"window must be > 0 s; got {seconds}")
        self.write(f":RUN {seconds:g}")

    def stop(self) -> None:
        self.write(":STOP")

    def read_result(self) -> BertReading:
        """Read the most recent ``(errors, bits, elapsed)`` window — the BERT
        derives BER itself."""
        err = self.query(":READ:ERR?")
        bits = self.query(":READ:BITS?")
        elapsed = self.query(":READ:TIME?")
        n_err = int(_require_finite(parse_scpi_float(err), ":READ:ERR? error count"))
        n_bits = _require_finite(parse_scpi_float(bits), ":READ:BITS? bit count")
        elapsed_s = parse_scpi_float(elapsed)
        ber = (n_err / n_bits) if n_bits > 0 else 0.0
        return BertReading(errors=n_err, bits=n_bits, ber=ber,
                            elapsed_s=elapsed_s,
                            raw=f"err={err!r};bits={bits!r};elapsed={elapsed!r}")


# ----------------------------------------------------------------------------- #
# Switch matrix
# ----------------------------------------------------------------------------- #
class SwitchMatrix(SCPIInstrument):
    """A relay/SCPI-controlled matrix that routes a DUT pin to one of several
    instruments (or grounds it).

    Why it exists: a single station drives N DUTs without paralleling every
    instrument cable; the matrix swaps the active path between fixtures. The
    canonical SCPI surface is ``:ROUT:CLOS (@<channels>)`` to connect and
    ``:ROUT:OPEN (@<channels>)`` to disconnect; ``:ROUT:CLOS:STAT?`` reads
    the currently-closed channel list.
    """

    def close_channel(self, channels: list[int] | str) -> None:
        """Close (connect) a channel or comma-separated list (``:ROUT:CLOS``).

        ``close_channel`` (not ``close``) so the relay-close API doesn't shadow
        the inherited ``SCPIInstrument.close()`` connection-close.
        """
        spec = (channels if isinstance(channels, str)
                else ",".join(str(c) for c in channels))
        self.write(f":ROUT:CLOS (@{spec})")

    def open_channel(self, channels: list[int] | str) -> None:
        """Open (disconnect) a channel or list (``:ROUT:OPEN``)."""
        spec = (channels if isinstance(channels, str)
                else ",".join(str(c) for c in channels))
        self.write(f":ROUT:OPEN (@{spec})")

    def open_all(self) -> None:
        """Open every relay in the matrix (``:ROUT:OPEN:ALL``)."""
        self.write(":ROUT:OPEN:ALL")

    def closed_channels(self) -> str:
        """Return the SCPI string describing which channels are currently closed."""
        return self.query(":ROUT:CLOS:STAT?")


# ----------------------------------------------------------------------------- #
# Vector network analyzer (4-port, differential S-parameters)
# ----------------------------------------------------------------------------- #
# Mixed-mode S-parameters a differential-channel compliance run reads: Sdd =
# differential-in/out (insertion/return loss), Scd/Sdc = mode conversion.
# Validated so a typo can't silently sweep the wrong parameter.
_VNA_PARAMS = {"SDD11", "SDD21", "SDD12", "SDD22", "SCD21", "SDC21", "SCC11"}


@dataclass
class SParamSweep:
    """A magnitude(dB)-vs-frequency sweep of one mixed-mode S-parameter.

    ``freqs_hz`` and ``magnitudes_db`` are parallel arrays (one dB value per
    frequency point) — the shape a channel-compliance mask is checked against.
    """

    param: str
    freqs_hz: list[float]
    magnitudes_db: list[float]

    def points(self) -> list[tuple[float, float]]:
        return list(zip(self.freqs_hz, self.magnitudes_db, strict=True))

    def to_dict(self) -> dict:
        return {"param": self.param, "freqs_hz": list(self.freqs_hz),
                "magnitudes_db": list(self.magnitudes_db),
                "points": len(self.freqs_hz)}


class Vna(SCPIInstrument):
    """A 4-port vector network analyzer for differential channel compliance —
    the instrument that qualifies a GMSL3 (or automotive-Ethernet) channel's
    insertion/return loss and mode conversion against a spec mask.

    SCPI: ``SENS:FREQ:STAR`` / ``SENS:FREQ:STOP`` / ``SENS:SWE:POIN`` set the
    sweep; ``CALC:PAR:DEF '<param>'`` selects a mixed-mode trace; ``CALC:DATA?
    FDATA`` returns the formatted (dB) trace and ``SENS:FREQ:DATA?`` the
    frequency axis (both comma-separated). The two traces zip into an
    ``SParamSweep``.
    """

    def set_sweep(self, start_hz: float, stop_hz: float, points: int = 201) -> None:
        """Program the frequency sweep (start/stop in Hz, number of points)."""
        if stop_hz <= start_hz:
            raise InstrumentError(
                f"sweep stop ({stop_hz:g}) must exceed start ({start_hz:g})")
        if points < 2:
            raise InstrumentError(f"sweep needs >= 2 points; got {points}")
        self.write(f"SENS:FREQ:STAR {start_hz:g}")
        self.write(f"SENS:FREQ:STOP {stop_hz:g}")
        self.write(f"SENS:SWE:POIN {points:d}")

    def measure_sparam(self, param: str) -> SParamSweep:
        """Select a mixed-mode parameter and read its dB trace + frequency axis."""
        key = param.upper()
        if key not in _VNA_PARAMS:
            raise InstrumentError(
                f"unknown S-parameter {param!r} (expected one of {sorted(_VNA_PARAMS)})")
        self.write(f"CALC:PAR:DEF '{key}'")
        freqs = self._parse_trace(self.query("SENS:FREQ:DATA?"))
        mags = self._parse_trace(self.query("CALC:DATA? FDATA"))
        if len(freqs) != len(mags):
            raise InstrumentError(
                f"{key}: freq axis ({len(freqs)}) and trace ({len(mags)}) length mismatch")
        return SParamSweep(param=key, freqs_hz=freqs, magnitudes_db=mags)

    @staticmethod
    def _parse_trace(raw: str) -> list[float]:
        """Parse a comma-separated SCPI trace into floats (overflow -> +/-inf)."""
        return [parse_scpi_float(field) for field in raw.split(",") if field.strip()]


# ----------------------------------------------------------------------------- #
# Thermal chamber / thermostream
# ----------------------------------------------------------------------------- #
class ThermalChamber(SCPIInstrument):
    """A thermal chamber or thermostream: program a temperature setpoint and read
    the actual chamber temperature. Drives the temperature axis of a V/T
    margining shmoo — AEC-Q100 Grade-2 -40..+105 C for automotive parts.

    SCPI: ``SOUR:TEMP <c>`` programs the setpoint; ``MEAS:TEMP?`` reads the
    actual and ``SOUR:TEMP?`` the setpoint. ``settled()`` checks the actual is
    within tolerance of the setpoint before a measurement at a new corner is
    trusted.
    """

    def set_temperature(self, celsius: float) -> None:
        """Program the temperature setpoint (SOUR:TEMP <c>)."""
        self.write(f"SOUR:TEMP {celsius:g}")

    def measure_temperature(self) -> Reading:
        """Read the actual chamber temperature (MEAS:TEMP?)."""
        raw = self.query("MEAS:TEMP?")
        return Reading(parse_scpi_float(raw), "C", raw)

    def setpoint(self) -> Reading:
        """Read back the programmed setpoint (SOUR:TEMP?)."""
        raw = self.query("SOUR:TEMP?")
        return Reading(parse_scpi_float(raw), "C", raw)

    def settled(self, *, tolerance_c: float = 2.0) -> bool:
        """True iff the actual temperature is within ``tolerance_c`` of the
        setpoint (poll before trusting a measurement at a new corner)."""
        return abs(self.measure_temperature().value - self.setpoint().value) <= tolerance_c


# ----------------------------------------------------------------------------- #
# Time-interval analyzer (TSN gPTP Max|TE|)
# ----------------------------------------------------------------------------- #
class TimeIntervalAnalyzer(SCPIInstrument):
    """A time-interval analyzer / counter (Keysight 53230A class) measuring the
    phase of a recovered 1PPS against a reference 1PPS — the Max|TE| (maximum
    time error) metric an 802.1AS gPTP slave must hold (Avnu's 1PPS method).

    SCPI: ``MEAS:TINT? (@1),(@2)`` returns the time interval (seconds) between
    the reference (ch 1) and DUT (ch 2) 1PPS edges; helpers convert to ns.
    """

    def measure_time_interval(self) -> Reading:
        """Time interval between the reference and DUT 1PPS edges (seconds)."""
        raw = self.query("MEAS:TINT? (@1),(@2)")
        return Reading(parse_scpi_float(raw), "s", raw)

    def measure_time_error_ns(self) -> float:
        """Absolute time error |TE| in nanoseconds (the gPTP slave metric)."""
        return abs(self.measure_time_interval().value) * 1e9


# ----------------------------------------------------------------------------- #
# TSN traffic generator / analyzer (scheduled traffic, preemption, FRER)
# ----------------------------------------------------------------------------- #
class TSNTrafficGenerator(SCPIInstrument):
    """A TSN traffic generator/analyzer facade (VIAVI TTworkbench+M1, Spirent,
    Keysight) for scheduled-traffic (Qbv), frame-preemption (Clause 99), and
    FRER tests. Configures per-stream priority/rate, runs traffic, reads
    per-stream counters, and applies link impairments.

    Modeled as a SCPI facade so it unit-tests on macOS behind the same
    _MockSCPI/_PyVisaTransport split; a real backend swaps in unchanged.
    """

    def configure_stream(self, stream_id: int, *, priority: int,
                         rate_mbps: float) -> None:
        """Configure a stream's 802.1Q priority and offered rate."""
        self.write(f":STREAM{stream_id:d}:PRIO {priority:d}")
        self.write(f":STREAM{stream_id:d}:RATE {rate_mbps:g}")

    def start(self) -> None:
        """Start offering traffic on all configured streams (:TRAF:STAR)."""
        self.write(":TRAF:STAR")

    def stop(self) -> None:
        """Stop traffic (:TRAF:STOP)."""
        self.write(":TRAF:STOP")

    def read_counters(self, stream_id: int) -> dict[str, int]:
        """Read tx/rx/dropped frame counters for one stream."""
        tx = int(_require_finite(
            self.query_float(f":STREAM{stream_id:d}:TX:COUN?"), f"stream {stream_id} tx count"))
        rx = int(_require_finite(
            self.query_float(f":STREAM{stream_id:d}:RX:COUN?"), f"stream {stream_id} rx count"))
        dropped = int(_require_finite(
            self.query_float(f":STREAM{stream_id:d}:DROP:COUN?"),
            f"stream {stream_id} dropped count"))
        return {"tx": tx, "rx": rx, "dropped": dropped}

    def set_impairment(self, *, loss_pct: float = 0.0,
                       reorder: bool = False) -> None:
        """Apply a link impairment (frame loss %, reordering) for FRER tests."""
        self.write(f":IMP:LOSS {loss_pct:g}")
        self.write(f":IMP:REOR {'ON' if reorder else 'OFF'}")
