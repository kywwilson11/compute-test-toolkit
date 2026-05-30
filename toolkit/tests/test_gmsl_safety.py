"""
Sprint 4.1.8: GMSL functional-safety (RAS) mechanism verification.

Inject the fault a safety mechanism is designed to detect, then assert the
platform's OWN reporting fires: ERRB asserts, the right counter latches, and
clearing de-asserts ERRB. Standards-defined diagnostic-coverage view only.
"""
from __future__ import annotations

import pytest

from computetest.gmsl import (
    ErrorCounters,
    MockSerDes,
    SafetyHealth,
    SerDesError,
    bench_line_fault,
    verify_safety_mechanism,
)
from computetest.instruments import PowerSupply, SwitchMatrix


def _line_fault_injector(serdes, switch):
    bench = bench_line_fault(switch, [1, 2])

    def inject():
        bench()                                  # drive the bench relay open
        serdes.inject_error_counters(ErrorCounters(link=0, line_fault=True))
    return inject


class TestSafetyMechanism:
    def test_full_assert_latch_clear_cycle(self):
        serdes = MockSerDes()
        switch = SwitchMatrix(mock=True).open()
        h = verify_safety_mechanism(
            serdes, _line_fault_injector(serdes, switch),
            fault_name="line_fault", expect_field="line_fault")
        assert isinstance(h, SafetyHealth)
        assert h.ok
        assert h.checks == {
            "errb_clear_before": True, "errb_asserts_on_fault": True,
            "line_fault_latched": True, "errb_clears_after": True}
        # the bench stimulus actually drove the relay open
        assert ":ROUT:OPEN (@1,2)" in switch.transport.writes

    def test_mechanism_that_never_fires_fails(self):
        # Inject nothing -> ERRB never asserts -> the mechanism check fails.
        h = verify_safety_mechanism(MockSerDes(), lambda: None,
                                    fault_name="line_fault", expect_field="line_fault")
        assert not h.ok
        assert h.checks["errb_asserts_on_fault"] is False

    def test_video_crc_field(self):
        serdes = MockSerDes()

        def inject():
            serdes.inject_error_counters(ErrorCounters(link=0, video_crc_errors=3))
        h = verify_safety_mechanism(serdes, inject, fault_name="video_crc",
                                    expect_field="video_crc_errors")
        assert h.ok and h.checks["video_crc_errors_latched"] is True

    def test_unknown_field_rejected(self):
        with pytest.raises(ValueError, match="unknown safety field"):
            verify_safety_mechanism(MockSerDes(), lambda: None,
                                    fault_name="x", expect_field="bogus")

    def test_clear_errors_resets_errb(self):
        serdes = MockSerDes(injected_error_counters=ErrorCounters(link=0, line_fault=True))
        assert serdes.errb_asserted()
        serdes.clear_errors()
        assert not serdes.errb_asserted()

    def test_clear_errors_per_link_validates_index(self):
        serdes = MockSerDes(links=2,
                            injected_error_counters=ErrorCounters(link=0, line_fault=True))
        serdes.clear_errors(link=1)              # valid link is bounds-checked then cleared
        assert not serdes.errb_asserted()
        with pytest.raises(SerDesError, match="out of range"):
            serdes.clear_errors(link=9)

    def test_bench_line_fault_cuts_power(self):
        switch = SwitchMatrix(mock=True).open()
        psu = PowerSupply(mock=True).open()
        bench_line_fault(switch, "5", psu=psu)()
        assert ":ROUT:OPEN (@5)" in switch.transport.writes
        assert "OUTP OFF" in psu.transport.writes

    def test_summary_and_to_dict(self):
        h = verify_safety_mechanism(MockSerDes(), lambda: None,
                                    fault_name="line_fault", expect_field="line_fault")
        assert "line_fault" in h.summary()
        assert h.to_dict()["fault_name"] == "line_fault"
