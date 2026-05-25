from computetest import diagnostics, dmesg
from computetest.backend import (MockBackend, MockDevice, PORT_ENDPOINT, PORT_ROOT,
                                  PORT_SWITCH_DOWNSTREAM, PORT_SWITCH_UPSTREAM)

SAMPLE = """\
[  12.3] pcieport 0000:00:1c.0: AER: Corrected error received: 0000:04:00.0
[  12.4] nvme 0000:04:00.0: PCIe Bus Error: severity=Corrected, type=Physical Layer
[  99.9] pcieport 0000:03:00.0: AER: Uncorrected (Non-Fatal) error received: 0000:04:00.0
[ 100.0] cpu0: some unrelated kernel line
"""


def test_parse_events_classifies_and_ignores_noise():
    evs = dmesg.parse_events(SAMPLE)
    sev = {e.severity for e in evs}
    assert "corrected" in sev and "non_fatal" in sev
    assert all("unrelated" not in e.text for e in evs)        # noise dropped
    unc = [e for e in evs if e.uncorrectable]
    assert unc and "0000:04:00.0" in unc[0].bdfs


def test_parse_events_filters_by_bdf():
    assert dmesg.parse_events(SAMPLE, only_bdfs=["0000:99:00.0"]) == []


def test_monitor_diffs_window():
    before = "[1.0] boot line\n"
    after = before + ("[2.0] pcieport 0000:00:1c.0: AER: Uncorrected (Fatal) "
                      "error received: 0000:04:00.0\n")
    state = {"n": 0}

    def reader():
        state["n"] += 1
        return before if state["n"] == 1 else after

    mon = dmesg.DmesgMonitor(reader=reader)
    mon.start()
    evs = mon.collect()
    assert len(evs) == 1 and evs[0].severity == "fatal"


def _switch_be():
    return MockBackend([
        MockDevice("0000:00:1c.0", parent=None, port_type=PORT_ROOT),
        MockDevice("0000:02:00.0", parent="0000:00:1c.0", port_type=PORT_SWITCH_UPSTREAM),
        MockDevice("0000:03:00.0", parent="0000:02:00.0", port_type=PORT_SWITCH_DOWNSTREAM),
        MockDevice("0000:04:00.0", parent="0000:03:00.0", port_type=PORT_ENDPOINT),
    ])


def test_chain_fails_on_kernel_uncorrectable_even_if_registers_clean():
    log = ("[5.0] pcieport 0000:03:00.0: AER: Uncorrected (Non-Fatal) error "
           "received: 0000:04:00.0\n")
    state = {"n": 0}

    def reader():
        state["n"] += 1
        return "" if state["n"] == 1 else log     # the event appears during the window

    d = diagnostics.diagnose_chain(_switch_be(), "0000:04:00.0", target_ber=1e-9,
                                   max_seconds=2, expected_speed=4, expected_width=16,
                                   dmesg_reader=reader)
    assert d.bert.status == "pass"        # AER registers were clean
    assert d.status == "fail"             # but the kernel logged an uncorrectable
    assert d.bad_kernel_events
