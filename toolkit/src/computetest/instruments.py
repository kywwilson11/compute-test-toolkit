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

import re
from dataclasses import dataclass
from typing import Any

from .backend import mock_mode

# --- Optional dependency: pyvisa (present only on a real test station) -------- #
try:  # pragma: no cover - exercised only where pyvisa is installed
    import pyvisa  # type: ignore
    _HAVE_PYVISA = True
except Exception:  # pragma: no cover - the bare-laptop path this module is built for
    pyvisa = None  # type: ignore
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
    try:
        value = float(match.group(0))
    except ValueError:  # pragma: no cover - regex already guarantees a float-parseable match
        raise InstrumentError(f"malformed SCPI numeric response: {raw!r}")
    if abs(value) >= SCPI_OVERFLOW:
        return float("inf") if value > 0 else float("-inf")
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
    def open(self) -> "SCPIInstrument":
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

    def __enter__(self) -> "SCPIInstrument":
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
        except ValueError:
            raise InstrumentError(f"malformed SYST:ERR? response: {raw!r}")
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
