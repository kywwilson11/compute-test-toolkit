from computetest import bert
from computetest.backend import MockBackend, MockDevice


def _be():
    return MockBackend([
        MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, injected_ber=0.0),
        MockDevice("0000:04:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, injected_ber=5e-9),
        MockDevice("0000:05:00.0", 0x144D, 0xA80A, 0x010802, 4, 4, 4, 4, injected_ber=0.0),
    ])


def test_clean_link_passes():
    r = bert.run_bert(_be(), "0000:03:00.0", target_ber=1e-9, confidence=0.95, max_seconds=3)
    assert r.status == "pass"
    assert r.uncorrectable == 0
    assert r.verdict.confidence_reached >= 0.95


def test_marginal_link_fails_confidence():
    r = bert.run_bert(_be(), "0000:04:00.0", target_ber=1e-9, confidence=0.95, max_seconds=1.5)
    assert r.status == "fail"
    assert r.correctable > 0


def test_uncorrectable_fails_immediately_with_decode():
    be = _be()
    be.inject_uncorrectable("0000:05:00.0", 14)  # persistent Completion Timeout
    r = bert.run_bert(be, "0000:05:00.0", target_ber=1e-9, confidence=0.95, max_seconds=1.5)
    assert r.status == "fail"
    assert "CmplTO" in r.uncorrectable_decode


def test_deterministic_clock_injection():
    # An injected clock makes the clean-link run deterministic (no wall-clock dependence).
    be = MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)])
    t = [0.0]

    def clock():
        t[0] += 0.01
        return t[0]

    r = bert.run_bert(be, "0000:03:00.0", target_ber=1e-9, confidence=0.95,
                      max_seconds=5, clock=clock, sleep=lambda _s: None)
    assert r.ok and r.seconds > 0
    # Reported bits equal the bits the verdict was decided on (P0-2 consistency).
    assert r.bits == r.verdict.bits


def test_no_aer_falls_back_to_device_status():
    # No AER but a PCIe cap is present -> measure via Device Status (coarse), don't skip.
    from computetest.backend import ECAP_AER
    dev = MockDevice("0000:0a:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)
    dev._ext_caps.pop(ECAP_AER)
    r = bert.run_bert(MockBackend([dev]), "0000:0a:00.0", target_ber=1e-9, max_seconds=2)
    assert r.aer_source == "devstatus" and r.status == "pass"


def test_no_error_source_is_skipped_not_passed():
    # No AER AND no PCIe cap -> truly cannot measure -> skip (never a false PASS).
    from computetest.backend import ECAP_AER
    dev = MockDevice("0000:0b:00.0", has_pcie_cap=False)
    dev._ext_caps.pop(ECAP_AER)
    r = bert.run_bert(MockBackend([dev]), "0000:0b:00.0", target_ber=1e-9, max_seconds=1)
    assert r.status == "skip" and r.aer_available is False and not r.ok


def test_stuck_at_idle_fails_without_runaway():
    # A correctable bit set even at idle = constant fault: FAIL, flagged, not counted as a rate.
    be = _be()
    be.inject_stuck_correctable("0000:03:00.0")   # BadTLP stuck, present at idle
    r = bert.run_bert(be, "0000:03:00.0", target_ber=1e-9, max_seconds=1)
    assert r.status == "fail" and r.stuck is True
    assert "idle" in r.note


def test_persistent_uncorrectable_counted_once():
    be = _be()
    be.inject_uncorrectable("0000:05:00.0", 14)  # re-latches every poll
    r = bert.run_bert(be, "0000:05:00.0", target_ber=1e-9, max_seconds=1.5)
    assert r.status == "fail" and r.uncorrectable == 1  # not inflated by re-latching
