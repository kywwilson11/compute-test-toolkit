"""Sprint 4.1.10: ThermalChamber HAL + PRBS24 BERT pattern."""
from __future__ import annotations

from computetest.instruments import _BERT_PATTERNS, ExternalBERT, ThermalChamber


class TestThermalChamber:
    def test_set_temperature_writes_scpi(self):
        c = ThermalChamber(mock=True).open()
        c.set_temperature(-40.0)
        assert "SOUR:TEMP -40" in c.transport.writes

    def test_measure_and_setpoint(self):
        c = ThermalChamber(mock=True).open()
        c.transport.set_measurement("MEAS:TEMP?", 24.5)
        c.transport.set_measurement("SOUR:TEMP?", 25.0)
        assert c.measure_temperature().value == 24.5
        assert c.setpoint().value == 25.0

    def test_settled_within_tolerance(self):
        c = ThermalChamber(mock=True).open()
        c.transport.set_measurement("MEAS:TEMP?", 24.0)
        c.transport.set_measurement("SOUR:TEMP?", 25.0)
        assert c.settled(tolerance_c=2.0)            # |24-25| <= 2
        assert not c.settled(tolerance_c=0.5)        # |24-25| > 0.5


class TestPrbs24Pattern:
    def test_prbs24_accepted_by_external_bert(self):
        assert "PRBS24" in _BERT_PATTERNS
        b = ExternalBERT(mock=True).open()
        b.set_pattern("PRBS24")
        assert ":PGEN:PATT PRBS24" in b.transport.writes
