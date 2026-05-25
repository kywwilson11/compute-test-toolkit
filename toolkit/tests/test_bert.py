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
