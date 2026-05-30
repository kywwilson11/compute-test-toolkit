"""Sprint 4.1.6: 4-port VNA HAL driver (mock-driven)."""
from __future__ import annotations

import pytest

from computetest.instruments import InstrumentError, SParamSweep, Vna


def _vna() -> Vna:
    return Vna(mock=True).open()  # type: ignore[return-value]


class TestVnaSweep:
    def test_set_sweep_writes_scpi(self):
        v = _vna()
        v.set_sweep(10e6, 6e9, points=401)
        assert "SENS:FREQ:STAR 1e+07" in v.transport.writes
        assert "SENS:FREQ:STOP 6e+09" in v.transport.writes
        assert "SENS:SWE:POIN 401" in v.transport.writes

    def test_set_sweep_rejects_bad_range(self):
        v = _vna()
        with pytest.raises(InstrumentError, match="must exceed start"):
            v.set_sweep(6e9, 10e6)
        with pytest.raises(InstrumentError, match=">= 2 points"):
            v.set_sweep(10e6, 6e9, points=1)


class TestVnaMeasure:
    def test_measure_sparam_zips_trace(self):
        v = _vna()
        v.transport.set_measurement("SENS:FREQ:DATA?", "1e6,2e6,3e6")
        v.transport.set_measurement("CALC:DATA? FDATA", "-0.1,-0.2,-0.4")
        sweep = v.measure_sparam("SDD21")
        assert isinstance(sweep, SParamSweep)
        assert sweep.param == "SDD21"
        assert sweep.points() == [(1e6, -0.1), (2e6, -0.2), (3e6, -0.4)]
        assert "CALC:PAR:DEF 'SDD21'" in v.transport.writes

    def test_unknown_param_rejected(self):
        v = _vna()
        with pytest.raises(InstrumentError, match="unknown S-parameter"):
            v.measure_sparam("SXX99")

    def test_length_mismatch_raises(self):
        v = _vna()
        v.transport.set_measurement("SENS:FREQ:DATA?", "1e6,2e6,3e6")
        v.transport.set_measurement("CALC:DATA? FDATA", "-0.1,-0.2")
        with pytest.raises(InstrumentError, match="length mismatch"):
            v.measure_sparam("SDD11")

    def test_to_dict(self):
        v = _vna()
        v.transport.set_measurement("SENS:FREQ:DATA?", "1e6,2e6")
        v.transport.set_measurement("CALC:DATA? FDATA", "-1.0,-2.0")
        d = v.measure_sparam("SDD11").to_dict()
        assert d["points"] == 2 and d["param"] == "SDD11"
