"""
Sprint 2.1: instrument HAL — SMU, ExternalBERT, SwitchMatrix, FixtureMap.

Existing tests in test_instruments.py cover SCPIInstrument + PowerSupply +
DMM + ElectronicLoad + Scope. This file pins the new role-based surface so a
test sequence can ``fixtures.open('rx_eye_scope')`` and trust what it gets back.
"""
from __future__ import annotations

import json

import pytest

from computetest import fixtures, instruments
from computetest.fixtures import FixtureMap, InstrumentSpec, load_fixture_map
from computetest.instruments import (
    SMU,
    ExternalBERT,
    InstrumentError,
    SwitchMatrix,
)


# ----------------------------------------------------------------------------
# SMU (Source Measure Unit)
# ----------------------------------------------------------------------------
class TestSMU:
    def test_set_source_emits_sour_func(self):
        s = SMU(mock=True).open()
        s.set_source("VOLT")
        assert "SOUR:FUNC VOLT" in s.transport.writes
        s.set_source("CURR")
        assert "SOUR:FUNC CURR" in s.transport.writes

    def test_set_source_rejects_unknown_kind(self):
        s = SMU(mock=True).open()
        with pytest.raises(InstrumentError, match="unknown SMU source"):
            s.set_source("RESISTOR")

    def test_set_voltage_and_current(self):
        s = SMU(mock=True).open()
        s.set_voltage(3.3)
        s.set_current(0.5)
        assert "SOUR:VOLT 3.3" in s.transport.writes
        assert "SOUR:CURR 0.5" in s.transport.writes

    def test_compliance_emits_sens_prot(self):
        s = SMU(mock=True).open()
        s.set_compliance(0.1, "CURR")
        assert "SENS:CURR:PROT 0.1" in s.transport.writes

    def test_compliance_rejects_unknown_kind(self):
        s = SMU(mock=True).open()
        with pytest.raises(InstrumentError, match="unknown compliance kind"):
            s.set_compliance(1.0, "POWER")

    def test_enable_outp_on_off(self):
        s = SMU(mock=True).open()
        s.enable(True)
        s.enable(False)
        assert "OUTP ON" in s.transport.writes
        assert "OUTP OFF" in s.transport.writes

    def test_measure_voltage_parses_response(self):
        s = SMU(mock=True).open()
        s.transport.set_measurement("MEAS:VOLT?", 3.293)
        r = s.measure_voltage()
        assert r.value == pytest.approx(3.293)
        assert r.unit == "V"

    def test_measure_resistance_handles_zero_current(self):
        s = SMU(mock=True).open()
        s.transport.set_measurement("MEAS:VOLT?", 1.0)
        s.transport.set_measurement("MEAS:CURR?", 0.0)
        r = s.measure_resistance()
        assert r.value == float("inf")

    def test_measure_resistance_v_over_i(self):
        s = SMU(mock=True).open()
        s.transport.set_measurement("MEAS:VOLT?", 5.0)
        s.transport.set_measurement("MEAS:CURR?", 0.1)
        r = s.measure_resistance()
        assert r.value == pytest.approx(50.0)
        assert r.unit == "Ohm"

    def test_sweep_voltage_returns_one_reading_per_point(self):
        s = SMU(mock=True).open()
        s.transport.set_measurement("MEAS:CURR?", 0.123)
        readings = s.sweep_voltage(0.0, 3.3, points=5)
        assert len(readings) == 5
        # Each step had a SOUR:VOLT write; check the last is at the stop value.
        volt_writes = [w for w in s.transport.writes if w.startswith("SOUR:VOLT")]
        assert len(volt_writes) == 5
        assert volt_writes[-1] == "SOUR:VOLT 3.3"

    def test_sweep_requires_ge_2_points(self):
        s = SMU(mock=True).open()
        with pytest.raises(InstrumentError, match=">=2 points"):
            s.sweep_voltage(0.0, 3.3, points=1)


# ----------------------------------------------------------------------------
# ExternalBERT
# ----------------------------------------------------------------------------
class TestExternalBert:
    def test_pattern_accepts_known_set(self):
        b = ExternalBERT(mock=True).open()
        b.set_pattern("PRBS31")
        assert ":PGEN:PATT PRBS31" in b.transport.writes

    def test_pattern_rejects_unknown(self):
        b = ExternalBERT(mock=True).open()
        with pytest.raises(InstrumentError, match="unknown BERT pattern"):
            b.set_pattern("PRBSX")

    def test_rate_writes_hz(self):
        b = ExternalBERT(mock=True).open()
        b.set_rate(32.0)
        assert any(w.startswith(":PGEN:RATE 3.2e+10") or
                   w.startswith(":PGEN:RATE 32000000000")
                   for w in b.transport.writes), b.transport.writes

    def test_rate_rejects_nonpositive(self):
        b = ExternalBERT(mock=True).open()
        with pytest.raises(InstrumentError, match="> 0"):
            b.set_rate(0)

    def test_run_window_rejects_nonpositive(self):
        b = ExternalBERT(mock=True).open()
        with pytest.raises(InstrumentError, match="must be > 0"):
            b.run_window(0)

    def test_read_result_derives_ber(self):
        b = ExternalBERT(mock=True).open()
        b.transport.set_measurement(":READ:ERR?", 5)
        b.transport.set_measurement(":READ:BITS?", 1e11)
        b.transport.set_measurement(":READ:TIME?", 12.5)
        r = b.read_result()
        assert r.errors == 5
        assert r.bits == pytest.approx(1e11)
        assert r.ber == pytest.approx(5e-11)
        assert r.elapsed_s == pytest.approx(12.5)

    def test_read_result_zero_bits_yields_zero_ber(self):
        b = ExternalBERT(mock=True).open()
        b.transport.set_measurement(":READ:ERR?", 0)
        b.transport.set_measurement(":READ:BITS?", 0)
        b.transport.set_measurement(":READ:TIME?", 0)
        r = b.read_result()
        assert r.ber == 0.0

    def test_read_result_overflow_error_count_raises_instrument_error(self):
        # A saturated/unset error counter returns the IEEE 488.2 9.9E37 sentinel
        # (-> +inf); that must surface as InstrumentError, not a bare OverflowError.
        b = ExternalBERT(mock=True).open()
        b.transport.set_measurement(":READ:ERR?", "9.9E37")
        b.transport.set_measurement(":READ:BITS?", "1e11")
        b.transport.set_measurement(":READ:TIME?", "1.0")
        with pytest.raises(InstrumentError, match="non-finite"):
            b.read_result()

    def test_read_result_overflow_bit_count_raises_not_silent_zero_ber(self):
        # A saturated bit counter (9.9E37 -> +inf) previously made 500/inf == 0.0 BER,
        # hiding real errors. It must now raise InstrumentError instead.
        b = ExternalBERT(mock=True).open()
        b.transport.set_measurement(":READ:ERR?", "500")
        b.transport.set_measurement(":READ:BITS?", "9.9E37")
        b.transport.set_measurement(":READ:TIME?", "1.0")
        with pytest.raises(InstrumentError, match="non-finite"):
            b.read_result()


# ----------------------------------------------------------------------------
# SwitchMatrix
# ----------------------------------------------------------------------------
class TestSwitchMatrix:
    def test_close_channel_accepts_list_of_channels(self):
        m = SwitchMatrix(mock=True).open()
        m.close_channel([101, 102, 103])
        assert ":ROUT:CLOS (@101,102,103)" in m.transport.writes

    def test_close_channel_accepts_raw_channel_string(self):
        m = SwitchMatrix(mock=True).open()
        m.close_channel("101:108")                          # spec allows ranges
        assert ":ROUT:CLOS (@101:108)" in m.transport.writes

    def test_open_channel_writes_open(self):
        m = SwitchMatrix(mock=True).open()
        m.open_channel([101])
        assert ":ROUT:OPEN (@101)" in m.transport.writes

    def test_open_all_writes_open_all(self):
        m = SwitchMatrix(mock=True).open()
        m.open_all()
        assert ":ROUT:OPEN:ALL" in m.transport.writes


# ----------------------------------------------------------------------------
# FixtureMap
# ----------------------------------------------------------------------------
class TestFixtureMap:
    def _spec(self, role="rx_eye_scope", kind="Scope"):
        return InstrumentSpec(role=role, kind=kind, resource="MOCK::INSTR")

    def test_roles_in_declaration_order(self):
        fm = FixtureMap(
            station="bay-3",
            instruments=[
                self._spec("a", "Scope"),
                self._spec("b", "SMU"),
                self._spec("c", "DMM"),
            ])
        assert fm.roles == ["a", "b", "c"]

    def test_duplicate_role_rejected(self):
        with pytest.raises(InstrumentError, match="duplicate role"):
            FixtureMap(instruments=[
                self._spec("dup", "Scope"),
                self._spec("dup", "DMM"),
            ])

    def test_unknown_kind_rejected(self):
        with pytest.raises(InstrumentError, match="unknown instrument kind"):
            FixtureMap(instruments=[
                InstrumentSpec(role="x", kind="Quantum", resource="MOCK::INSTR")])

    def test_spec_lookup_round_trips(self):
        fm = FixtureMap(instruments=[self._spec("rx_eye_scope", "Scope")])
        assert fm.spec("rx_eye_scope").kind == "Scope"

    def test_spec_lookup_missing_role(self):
        fm = FixtureMap(instruments=[self._spec("rx_eye_scope", "Scope")])
        with pytest.raises(InstrumentError, match="no instrument with role"):
            fm.spec("absent")

    def test_open_returns_an_open_instrument(self):
        fm = FixtureMap(instruments=[self._spec("scope", "Scope")])
        inst = fm.open("scope", mock=True)
        # IDN query works (the mock returns a canned IDN).
        assert "Mock" in inst.idn() or "," in inst.idn()
        inst.close()

    def test_open_idn_mismatch_raises_and_closes(self):
        # Force a specific IDN expectation; the mock supplies the default
        # "MockCorp,Model-1,..." so a "Keysight" prefix won't match.
        fm = FixtureMap(instruments=[
            InstrumentSpec(role="scope", kind="Scope", resource="MOCK::INSTR",
                            idn="Keysight Technologies,MSO-X")])
        with pytest.raises(InstrumentError, match="IDN .* does not match"):
            fm.open("scope", mock=True)

    def test_open_all_returns_role_map(self):
        fm = FixtureMap(instruments=[
            self._spec("a", "Scope"), self._spec("b", "DMM"),
        ])
        opened = fm.open_all(mock=True)
        assert set(opened.keys()) == {"a", "b"}
        for inst in opened.values():
            inst.close()

    def test_open_all_closes_already_opened_on_failure(self):
        # First role opens fine; second fails its IDN assertion. open_all must close
        # the first before re-raising so no session leaks on a partial failure.
        fm = FixtureMap(instruments=[
            InstrumentSpec(role="good", kind="Scope", resource="MOCK::INSTR"),
            InstrumentSpec(role="bad", kind="Scope", resource="MOCK::INSTR",
                           idn="Keysight Technologies,MSO-X"),
        ])
        built: list = []
        real_open = fm.open

        def _tracking_open(role, *, mock=None):
            inst = real_open(role, mock=mock)
            built.append(inst)
            return inst

        fm.open = _tracking_open  # type: ignore[method-assign]
        with pytest.raises(InstrumentError, match="does not match"):
            fm.open_all(mock=True)
        # The good instrument was opened then closed before the error propagated.
        assert built and built[0].transport.closed   # _MockSCPI sets closed=True on close()

    def test_blank_idn_prefix_rejected(self):
        with pytest.raises(InstrumentError, match="non-blank prefix"):
            FixtureMap(instruments=[
                InstrumentSpec(role="x", kind="Scope", resource="MOCK::INSTR",
                               idn="   ")])


# ----------------------------------------------------------------------------
# load_fixture_map (YAML/JSON)
# ----------------------------------------------------------------------------
class TestLoadFixtureMap:
    def test_example_yaml_loads_and_validates(self):
        fm = load_fixture_map("configs/fixture_map.example.yaml")
        assert "rx_eye_scope" in fm.roles
        assert "power_smu" in fm.roles
        assert "external_bert" in fm.roles
        assert fm.station == "bay-3"

    def test_yaml_file_without_pyyaml_raises_actionable_error(self, tmp_path, monkeypatch):
        # When PyYAML is missing and the file is real YAML, load_fixture_map must
        # raise an actionable 'install pyyaml' error, not an opaque JSONDecodeError.
        import builtins
        p = tmp_path / "fm.yaml"
        p.write_text("station: bay-9\ninstruments: []\n")
        real_import = builtins.__import__

        def _no_yaml(name, *a, **k):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _no_yaml)
        with pytest.raises(InstrumentError, match="install pyyaml"):
            load_fixture_map(str(p))

    def test_json_loads_when_yaml_extension_holds_json(self, tmp_path):
        p = tmp_path / "fm.json"
        p.write_text(json.dumps({
            "station": "bay-x",
            "instruments": [{
                "role": "scope", "kind": "Scope",
                "resource": "MOCK::INSTR",
            }],
        }))
        fm = load_fixture_map(str(p))
        assert fm.station == "bay-x"
        assert fm.roles == ["scope"]


# ----------------------------------------------------------------------------
# register_instrument_kind extension point
# ----------------------------------------------------------------------------
class TestRegisterInstrumentKind:
    def test_extra_class_registered_and_resolvable(self):
        class CustomSCPI(instruments.SCPIInstrument):
            pass

        fixtures.register_instrument_kind("CustomSCPI", CustomSCPI)
        fm = FixtureMap(instruments=[
            InstrumentSpec(role="x", kind="CustomSCPI", resource="MOCK::INSTR")])
        inst = fm.open("x", mock=True)
        assert isinstance(inst, CustomSCPI)
        inst.close()


# ----------------------------------------------------------------------------
# Module surface
# ----------------------------------------------------------------------------
def test_public_exports():
    assert hasattr(fixtures, "FixtureMap")
    assert hasattr(fixtures, "load_fixture_map")
    assert hasattr(instruments, "SMU")
    assert hasattr(instruments, "ExternalBERT")
    assert hasattr(instruments, "SwitchMatrix")
