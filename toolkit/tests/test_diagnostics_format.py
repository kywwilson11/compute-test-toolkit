"""PcieDiagnostic + ChainDiagnostic status/reasons/summary/to_dict, driven through
the mock backend with injected faults. Complements test_diagnostics.py (clean/degraded
single device) and test_chain.py (chain status)."""
from computetest import diagnostics
from computetest.backend import (MockBackend, MockDevice, PORT_ENDPOINT, PORT_ROOT,
                                  PORT_SWITCH_DOWNSTREAM, PORT_SWITCH_UPSTREAM)


def _dev_be(bdf="0000:03:00.0", **kw):
    return MockBackend([MockDevice(bdf, 0x10DE, 0x2204, 0x030000,
                                   link_speed=4, link_width=16, max_link_speed=4,
                                   max_link_width=16, **kw)])


# --- PcieDiagnostic: uncorrectable dominates -------------------------------- #
def test_uncorrectable_fails_with_reason_and_to_dict():
    be = _dev_be()
    be.inject_uncorrectable("0000:03:00.0", 14)          # Completion Timeout
    d = diagnostics.diagnose(be, "0000:03:00.0", target_ber=1e-9, bert_max_s=1,
                             watch_retrains_s=0.02)
    assert d.status == "fail"
    assert any(r.startswith("uncorrectable:") and "CmplTO" in r for r in d.reasons())
    dd = d.to_dict()
    assert dd["status"] == "fail" and "CmplTO" in dd["aer"]["uncorrectable"]


# --- PcieDiagnostic: retrains flagged in reasons + summary ------------------ #
def test_retrains_reason_and_summary_text():
    be = _dev_be(inject_retrains_per_sec=200)
    d = diagnostics.diagnose(be, "0000:03:00.0", do_bert=False, do_margin=False,
                             watch_retrains_s=0.1)
    assert d.status == "fail"
    assert any("retrains during soak" in r for r in d.reasons())
    s = d.summary()
    assert s.startswith("[FAIL]") and "reasons:" in s


# --- PcieDiagnostic: failing margin is a reason ----------------------------- #
def test_margin_failure_is_a_reason():
    # 0000:04:00.0 + injected BER -> one lane below the margining limit.
    be = MockBackend([MockDevice("0000:04:00.0", 0x10DE, 0x2204, 0x030000,
                                 link_speed=4, link_width=16, max_link_speed=4,
                                 max_link_width=16, injected_ber=1e-9)])
    d = diagnostics.diagnose(be, "0000:04:00.0", target_ber=1e-9, do_bert=False,
                             watch_retrains_s=0.02)
    assert d.status == "fail"
    assert any("margin" in r and "UI <" in r for r in d.reasons())
    s = d.summary()
    assert "margin:" in s                                 # margin block appended


# --- PcieDiagnostic: clean device summary + bert block ---------------------- #
def test_clean_device_summary_has_bert_no_reasons():
    d = diagnostics.diagnose(_dev_be(), "0000:03:00.0", target_ber=1e-9, bert_max_s=1,
                             watch_retrains_s=0.02)
    assert d.status == "pass"
    assert d.reasons() == []
    s = d.summary()
    assert s.startswith("[PASS]") and "bert:" in s and "reasons:" not in s


def test_width_degraded_is_a_reason():
    # Trained x8 but max is x16 -> width degraded reason (no errors needed).
    be = MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000,
                                 link_speed=4, link_width=8, max_link_speed=4,
                                 max_link_width=16)])
    d = diagnostics.diagnose(be, "0000:03:00.0", do_bert=False, do_margin=False,
                             watch_retrains_s=0.02)
    assert d.status == "fail"
    assert any("width degraded (x8)" in r for r in d.reasons())


# --- diagnose_all over the whole board -------------------------------------- #
def test_diagnose_all_covers_every_device():
    be = MockBackend()                                    # the sample board (5 devices)
    results = diagnostics.diagnose_all(be, do_bert=False, do_margin=False,
                                       watch_retrains_s=0.02)
    assert len(results) == 5
    assert {r.bdf for r in results} == set(be.list_devices())


# --- ChainDiagnostic formatting --------------------------------------------- #
def _switch_be(**overrides):
    spec = {
        "0000:00:1c.0": dict(parent=None, port_type=PORT_ROOT),
        "0000:02:00.0": dict(parent="0000:00:1c.0", port_type=PORT_SWITCH_UPSTREAM),
        "0000:03:00.0": dict(parent="0000:02:00.0", port_type=PORT_SWITCH_DOWNSTREAM),
        "0000:04:00.0": dict(parent="0000:03:00.0", port_type=PORT_ENDPOINT),
    }
    for bdf, kw in overrides.items():
        spec[bdf].update(kw)
    return MockBackend([MockDevice(bdf, **kw) for bdf, kw in spec.items()])


def _chain(be):
    return diagnostics.diagnose_chain(be, "0000:04:00.0", target_ber=1e-9, max_seconds=2,
                                      expected_speed=4, expected_width=16)


def test_chain_clean_summary_lists_segments_and_links():
    d = _chain(_switch_be())
    assert d.status == "pass" and d.reasons() == []
    s = d.summary()
    assert s.startswith("[PASS] chain to 0000:04:00.0")
    assert "endpoint" in s and s.count("seg ") == 3 and s.count("link ") == 2


def test_chain_segment_error_reason_and_summary():
    d = _chain(_switch_be(**{"0000:02:00.0": dict(injected_ber=1e-8)}))
    assert d.status == "fail"
    assert any(r.startswith("errors ") for r in d.reasons())
    assert "FAIL" in d.summary()                          # the failing seg line


def test_chain_downgrade_reason_text():
    d = _chain(_switch_be(**{"0000:03:00.0": dict(link_speed=3)}))   # Gen3 < expected 4
    assert d.status == "fail"
    assert any(r.startswith("downgrade ") and "Gen3" in r for r in d.reasons())
    assert "DOWNGRADE" in d.summary()


def test_chain_endpoint_bert_failure_is_a_reason():
    # The endpoint link itself fails the BERT (uncorrectable) -> a reason pinned to it.
    be = _switch_be()
    be.inject_uncorrectable("0000:04:00.0", 14)          # Completion Timeout on the endpoint
    d = _chain(be)
    assert d.bert.status == "fail" and d.status == "fail"
    assert any(r.startswith("endpoint 0000:04:00.0:") for r in d.reasons())


def test_chain_kernel_event_reason_and_to_dict():
    log = ("[5.0] pcieport 0000:03:00.0: AER: Uncorrected (Non-Fatal) error "
           "received: 0000:04:00.0\n")
    state = {"n": 0}

    def reader():
        state["n"] += 1
        return "" if state["n"] == 1 else log

    d = diagnostics.diagnose_chain(_switch_be(), "0000:04:00.0", target_ber=1e-9,
                                   max_seconds=2, expected_speed=4, expected_width=16,
                                   dmesg_reader=reader)
    assert d.status == "fail" and d.bad_kernel_events
    assert any(r.startswith("kernel:") for r in d.reasons())
    dd = d.to_dict()
    assert dd["status"] == "fail" and len(dd["kernel_events"]) >= 1
    assert dd["segments"] and dd["links"]
