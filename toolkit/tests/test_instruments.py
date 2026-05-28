"""Mock-mode tests for the VISA/SCPI bench-instrument module.

These run with NO pyvisa installed and NO hardware: every instrument routes to the
deterministic in-memory SCPI simulator (``instr.transport``), so we can assert on the
*exact* SCPI strings sent (``transport.writes``), feed canned measurement responses
(``transport.set_measurement``), and inject faults (timeouts, malformed replies, a
non-empty error queue). This is the same mock/real discipline as test_nvme/test_memory:
prove the command formatting, the response parsing, and the error handling without a
bench attached.
"""
import math

import pytest

from computetest import instruments
from computetest.instruments import (
    DMM,
    ElectronicLoad,
    InstrumentError,
    PowerSupply,
    Reading,
    Scope,
    SCPIInstrument,
    parse_scpi_float,
)


# --- module imports and runs without pyvisa --------------------------------- #
def test_runs_without_pyvisa_and_defaults_to_mock():
    # The whole point: on a bare laptop pyvisa is absent and we fall back to the sim.
    assert instruments._HAVE_PYVISA is False
    instr = SCPIInstrument()
    assert instr.is_mock is True
    assert isinstance(instr.transport, instruments._MockSCPI)


# --- SCPI numeric parsing (incl. the 9.9E37 overflow sentinel) -------------- #
def test_parse_scpi_float_plain_and_scientific():
    assert parse_scpi_float("1.5") == 1.5
    assert parse_scpi_float("  12.000  ") == 12.0
    assert parse_scpi_float("3.3E-2") == pytest.approx(0.033)
    assert parse_scpi_float("-5") == -5.0
    assert parse_scpi_float(".5") == 0.5


def test_parse_scpi_float_strips_unit_and_list():
    assert parse_scpi_float("1.5V") == 1.5            # trailing unit suffix
    assert parse_scpi_float("2.0,1.0,0.5") == 2.0     # comma list -> first field
    assert parse_scpi_float("12.0;") == 12.0          # trailing terminator


def test_parse_scpi_float_overflow_sentinel_maps_to_inf():
    assert parse_scpi_float("9.9E37") == float("inf")
    assert parse_scpi_float("+9.9E37") == float("inf")
    assert parse_scpi_float("-9.9E37") == float("-inf")
    assert math.isinf(parse_scpi_float("9.91E37"))


def test_parse_scpi_float_malformed_raises():
    for bad in ("", "   ", None, "N/A", "overrange"):
        with pytest.raises(InstrumentError):
            parse_scpi_float(bad)


def test_reading_str_and_overflow_flag():
    assert str(Reading(1.5, "V", "1.5")) == "1.5 V"
    assert str(Reading(3.3, "")) == "3.3"               # no unit -> no trailing space
    assert Reading(1.0, "V").overflow is False
    assert Reading(float("inf"), "V").overflow is True
    assert Reading(float("-inf"), "A").overflow is True


# --- base SCPIInstrument: IDN / reset / CLS / OPC / raw I/O ------------------ #
def test_idn_query():
    instr = SCPIInstrument(idn="Keysight,N6705C,MY12345,1.2")
    assert instr.idn() == "Keysight,N6705C,MY12345,1.2"
    assert instr.transport.writes == ["*IDN?"]


def test_reset_and_clear_send_common_commands():
    instr = SCPIInstrument()
    instr.reset()
    instr.clear_status()
    assert instr.transport.writes == ["*RST", "*CLS"]


def test_wait_complete_uses_opc():
    instr = SCPIInstrument()
    assert instr.wait_complete() is True
    assert instr.transport.writes == ["*OPC?"]


def test_query_float_parses_response():
    instr = SCPIInstrument()
    instr.transport.set_measurement("READ?", 2.5)
    assert instr.query_float("READ?") == 2.5


def test_write_and_query_passthrough_and_strip():
    instr = SCPIInstrument()
    instr.write("SYST:BEEP")
    instr.transport.set_measurement("FOO?", "bar\n")
    assert instr.query("FOO?") == "bar"               # response is stripped
    assert instr.transport.writes == ["SYST:BEEP", "FOO?"]


def test_read_returns_last_response():
    instr = SCPIInstrument()
    instr.transport.set_measurement("*IDN?", "X,Y,Z,1")
    instr.query("*IDN?")
    assert instr.read() == "X,Y,Z,1"


# --- context manager + open/close lifecycle --------------------------------- #
def test_context_manager_opens_and_closes():
    instr = SCPIInstrument()
    with instr as handle:
        assert handle is instr
        assert instr._open is True
        assert instr.transport.opened is True
    assert instr._open is False
    assert instr.transport.closed is True


def test_open_is_idempotent_and_close_is_safe():
    instr = SCPIInstrument()
    instr.open()
    instr.open()                      # second open must not re-open the transport
    instr.close()
    instr.close()                     # closing twice must not blow up
    assert instr._open is False


# --- error queue handling --------------------------------------------------- #
def test_error_queue_empty_reports_no_error():
    instr = SCPIInstrument()
    code, message = instr.error()
    assert code == 0 and message == "No error"
    assert instr.check_errors() == []                 # clean queue: no raise, empty list


def test_check_errors_raises_and_drains_queue():
    instr = SCPIInstrument()
    instr.transport.push_error(-113, "Undefined header")
    instr.transport.push_error(-222, "Data out of range")
    with pytest.raises(InstrumentError) as exc:
        instr.check_errors()
    assert "Undefined header" in str(exc.value)
    assert "Data out of range" in str(exc.value)
    # queue was drained; a follow-up check is clean
    assert instr.check_errors() == []


def test_error_malformed_response_raises():
    instr = SCPIInstrument()
    instr.transport.set_measurement("SYST:ERR?", "not-a-code,oops")
    with pytest.raises(InstrumentError):
        instr.error()


# --- timeout / I/O fault propagation ---------------------------------------- #
def test_query_timeout_becomes_instrument_error():
    instr = SCPIInstrument()
    instr.transport.timeout_on = {"MEAS:VOLT?"}
    with pytest.raises(InstrumentError) as exc:
        instr.query("MEAS:VOLT?")
    assert "timeout" in str(exc.value).lower()


def test_query_visa_io_error_becomes_instrument_error():
    instr = SCPIInstrument()
    instr.transport.raise_on_query = ValueError("VI_ERROR_IO")
    with pytest.raises(InstrumentError):
        instr.query("*IDN?")


def test_timeout_attribute_threads_through_to_transport():
    instr = SCPIInstrument(timeout_ms=2500)
    assert instr.transport.timeout == 2500


# --- PowerSupply: exact SCPI command formatting + read-back ----------------- #
def test_power_supply_set_commands_formatting():
    ps = PowerSupply()
    ps.select_channel(2)
    ps.set_voltage(12.0)
    ps.set_current(2.5)
    ps.enable(True)
    assert ps.transport.writes == ["INST:NSEL 2", "VOLT 12", "CURR 2.5", "OUTP ON"]


def test_power_supply_disable_and_enable_off():
    ps = PowerSupply()
    ps.enable(False)
    ps.disable()
    assert ps.transport.writes == ["OUTP OFF", "OUTP OFF"]


def test_power_supply_measure_readback():
    ps = PowerSupply()
    ps.transport.set_measurement("MEAS:VOLT?", 11.97)
    ps.transport.set_measurement("MEAS:CURR?", 1.83)
    v = ps.measure_voltage()
    i = ps.measure_current()
    assert (v.value, v.unit, v.raw) == (11.97, "V", "11.97")
    assert i.value == 1.83 and i.unit == "A"
    assert ps.transport.writes == ["MEAS:VOLT?", "MEAS:CURR?"]


def test_power_supply_state_readback_and_is_enabled():
    ps = PowerSupply()
    ps.set_voltage(3.3)
    # the mock reflects the last VOLT set when queried with VOLT?
    assert ps.query_float("VOLT?") == 3.3
    ps.enable(True)
    assert ps.is_enabled() is True
    ps.disable()
    assert ps.is_enabled() is False


def test_power_supply_overflow_reading_flagged():
    ps = PowerSupply()
    ps.transport.set_measurement("MEAS:VOLT?", "9.9E37")
    v = ps.measure_voltage()
    assert v.overflow is True and math.isinf(v.value)


# --- DMM: function selection encoded in the query --------------------------- #
def test_dmm_dc_measurements():
    dmm = DMM()
    dmm.transport.set_measurement("MEAS:VOLT:DC?", 3.301)
    dmm.transport.set_measurement("MEAS:CURR:DC?", 0.12)
    dmm.transport.set_measurement("MEAS:RES?", 99.8)
    assert dmm.measure_voltage().value == 3.301
    assert dmm.measure_current().value == 0.12
    r = dmm.measure_resistance()
    assert r.value == 99.8 and r.unit == "Ohm"
    assert dmm.transport.writes == ["MEAS:VOLT:DC?", "MEAS:CURR:DC?", "MEAS:RES?"]


def test_dmm_ac_and_four_wire_variants():
    dmm = DMM()
    dmm.transport.set_measurement("MEAS:VOLT:AC?", 0.05)
    dmm.transport.set_measurement("MEAS:CURR:AC?", 0.01)
    dmm.transport.set_measurement("MEAS:FRES?", 0.052)
    assert dmm.measure_voltage(ac=True).unit == "VAC"
    assert dmm.measure_current(ac=True).unit == "AAC"
    assert dmm.measure_resistance(four_wire=True).value == 0.052
    assert dmm.transport.writes == ["MEAS:VOLT:AC?", "MEAS:CURR:AC?", "MEAS:FRES?"]


# --- ElectronicLoad: modes, setpoints, input, measurements ------------------ #
def test_load_modes_map_to_func_mnemonic():
    load = ElectronicLoad()
    for mode, func in [("CC", "CURR"), ("CV", "VOLT"), ("CR", "RES"), ("CP", "POW")]:
        load.set_mode(mode)
        assert load.transport.writes[-1] == f"FUNC {func}"
    assert load.set_mode and load._mode == "CP"


def test_load_set_mode_case_insensitive():
    load = ElectronicLoad()
    load.set_mode("cc")
    assert load.transport.writes[-1] == "FUNC CURR"


def test_load_unknown_mode_raises():
    load = ElectronicLoad()
    with pytest.raises(InstrumentError):
        load.set_mode("CX")


def test_load_setpoints_and_input_formatting():
    load = ElectronicLoad()
    load.set_current(5.0)
    load.set_voltage(12.0)
    load.set_resistance(2.5)
    load.set_power(50.0)
    load.input(True)
    load.input(False)
    assert load.transport.writes == [
        "CURR 5", "VOLT 12", "RES 2.5", "POW 50", "INP ON", "INP OFF"]


def test_load_measurements():
    load = ElectronicLoad()
    load.transport.set_measurement("MEAS:VOLT?", 11.9)
    load.transport.set_measurement("MEAS:CURR?", 5.01)
    load.transport.set_measurement("MEAS:POW?", 59.6)
    assert load.measure_voltage().value == 11.9
    assert load.measure_current().value == 5.01
    p = load.measure_power()
    assert p.value == 59.6 and p.unit == "W"


# --- Scope: MEASure subsystem operators ------------------------------------- #
def test_scope_measure_builds_meas_query_per_channel():
    scope = Scope()
    scope.transport.set_measurement("MEAS:VPP? CHAN1", 0.025)
    scope.transport.set_measurement("MEAS:FREQUENCY? CHAN2", 1.0e8)
    assert scope.measure(1, "vpp").value == 0.025
    f = scope.measure(2, "freq")
    assert f.value == 1.0e8 and f.unit == "Hz"
    # the mnemonic is sent literally with the SCPI short-form capitals preserved
    assert scope.transport.writes == ["MEAS:VPP? CHAN1", "MEAS:FREQuency? CHAN2"]


def test_scope_convenience_helpers_and_autoscale():
    scope = Scope()
    scope.autoscale()
    scope.transport.set_measurement("MEAS:VPP? CHAN3", 0.04)
    scope.transport.set_measurement("MEAS:FREQUENCY? CHAN3", 250.0e6)
    assert scope.measure_vpp(3).value == 0.04
    assert scope.measure_frequency(3).value == 250.0e6
    assert scope.transport.writes[0] == "AUToscale"


def test_scope_all_known_kinds_have_units():
    scope = Scope()
    for kind in ("vpp", "vavg", "vrms", "vmax", "vmin", "vamp",
                 "freq", "period", "duty", "rise", "fall"):
        op, unit = instruments._SCOPE_MEAS[kind]
        scope.transport.set_measurement(f"MEAS:{op}? CHAN1", 1.0)
        assert scope.measure(1, kind).unit == unit


def test_scope_unknown_kind_raises():
    scope = Scope()
    with pytest.raises(InstrumentError):
        scope.measure(1, "bogus")


# --- a measurement that overflows propagates through an instrument class ----- #
def test_dmm_overrange_reading_is_inf():
    dmm = DMM()
    dmm.transport.set_measurement("MEAS:RES?", "9.9E37")   # open leads -> overrange
    r = dmm.measure_resistance()
    assert r.overflow is True


# --- end-to-end through the context manager (the realistic call shape) ------- #
def test_power_test_flow_under_context_manager():
    with PowerSupply(idn="Rigol,DP832,DP8A1,1.0") as ps:
        assert ps.idn().startswith("Rigol")
        ps.reset()
        ps.set_voltage(12.0)
        ps.set_current(3.0)
        ps.enable(True)
        ps.transport.set_measurement("MEAS:VOLT?", 12.01)
        assert ps.measure_voltage().value == 12.01
        ps.check_errors()                 # clean queue -> no raise
    assert ps.transport.closed is True
