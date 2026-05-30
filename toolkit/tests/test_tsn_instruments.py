"""Sprint 4.2.2: TSN bench HAL -- TimeIntervalAnalyzer + TSNTrafficGenerator."""
from __future__ import annotations

from computetest.instruments import TimeIntervalAnalyzer, TSNTrafficGenerator


class TestTimeIntervalAnalyzer:
    def test_time_error_ns_is_absolute(self):
        tia = TimeIntervalAnalyzer(mock=True).open()
        tia.transport.set_measurement("MEAS:TINT? (@1),(@2)", -80e-9)
        assert tia.measure_time_error_ns() == 80.0       # |-80 ns|
        assert "MEAS:TINT? (@1),(@2)" in tia.transport.writes

    def test_time_interval_reading(self):
        tia = TimeIntervalAnalyzer(mock=True).open()
        tia.transport.set_measurement("MEAS:TINT? (@1),(@2)", 5e-9)
        r = tia.measure_time_interval()
        assert r.unit == "s" and r.value == 5e-9


class TestTsnTrafficGenerator:
    def test_configure_and_run(self):
        g = TSNTrafficGenerator(mock=True).open()
        g.configure_stream(1, priority=6, rate_mbps=100.0)
        g.start()
        g.stop()
        w = g.transport.writes
        assert ":STREAM1:PRIO 6" in w and ":STREAM1:RATE 100" in w
        assert ":TRAF:STAR" in w and ":TRAF:STOP" in w

    def test_read_counters(self):
        g = TSNTrafficGenerator(mock=True).open()
        g.transport.set_measurement(":STREAM1:TX:COUN?", 1000)
        g.transport.set_measurement(":STREAM1:RX:COUN?", 998)
        g.transport.set_measurement(":STREAM1:DROP:COUN?", 2)
        assert g.read_counters(1) == {"tx": 1000, "rx": 998, "dropped": 2}

    def test_set_impairment(self):
        g = TSNTrafficGenerator(mock=True).open()
        g.set_impairment(loss_pct=1.5, reorder=True)
        w = g.transport.writes
        assert ":IMP:LOSS 1.5" in w and ":IMP:REOR ON" in w
