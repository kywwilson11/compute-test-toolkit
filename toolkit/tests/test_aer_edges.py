"""AER decode/source/clear edge branches driven through the mock backend.

Complements test_backend_aer.py (the happy-path W1C). This proves the no-capability
fallbacks: snapshot/clear/read with no AER, the Device-Status (coarse) source, and the
'none' source — so a device that exposes neither can't be silently misread.
"""
from computetest import aer
from computetest.backend import (DEVSTA_CORR, DEVSTA_NONFATAL, ECAP_AER, MockBackend,
                                  MockDevice)


def _no_aer(bdf="0000:01:00.0", **kw):
    d = MockDevice(bdf, **kw)
    d._ext_caps.pop(ECAP_AER)
    return d


# --- AER absent: snapshot/clear degrade safely ------------------------------ #
def test_snapshot_without_aer_is_empty():
    be = MockBackend([_no_aer(has_pcie_cap=False)])
    snap = aer.snapshot(be, "0000:01:00.0")
    assert snap.aer_base is None
    assert snap.correctable_raw == 0 and snap.uncorrectable_raw == 0
    assert not snap.has_correctable and not snap.has_uncorrectable


def test_clear_without_aer_returns_empty_dict():
    be = MockBackend([_no_aer(has_pcie_cap=False)])
    assert aer.clear(be, "0000:01:00.0") == {}


def test_clear_correctable_only_skips_uncorrectable():
    be = MockBackend([MockDevice("0000:01:00.0")])
    res = aer.clear(be, "0000:01:00.0", correctable=True, uncorrectable=False)
    assert "correctable" in res and "uncorrectable" not in res


def test_clear_uncorrectable_only_skips_correctable():
    be = MockBackend([MockDevice("0000:01:00.0")])
    res = aer.clear(be, "0000:01:00.0", correctable=False, uncorrectable=True)
    assert "uncorrectable" in res and "correctable" not in res


# --- Error source selection: aer > devstatus > none ------------------------- #
def test_error_source_aer():
    be = MockBackend([MockDevice("0000:01:00.0")])
    assert aer.error_source(be, "0000:01:00.0") == "aer"


def test_error_source_devstatus():
    be = MockBackend([_no_aer()])           # no AER, but has a PCIe cap
    assert aer.error_source(be, "0000:01:00.0") == "devstatus"


def test_error_source_none():
    be = MockBackend([_no_aer(has_pcie_cap=False)])
    assert aer.error_source(be, "0000:01:00.0") == "none"


# --- read_errors across all three sources ----------------------------------- #
def test_read_errors_devstatus_decodes_coarse_bits():
    be = MockBackend([_no_aer()])
    be.inject_stuck_correctable("0000:01:00.0")
    be.inject_uncorrectable("0000:01:00.0", 14)
    r = aer.read_errors(be, "0000:01:00.0")
    assert r.source == "devstatus"
    assert r.has_correctable and r.has_uncorrectable
    # Coarse decode: a single 'detected' name per class, no per-type breakdown.
    assert [n for _, n, _ in r.correctable] == ["CorrErrDetected"]
    assert [n for _, n, _ in r.uncorrectable] == ["NonFatalDetected"]
    assert r.correctable_raw == DEVSTA_CORR
    assert r.uncorrectable_raw & DEVSTA_NONFATAL


def test_read_errors_none_source_is_empty():
    be = MockBackend([_no_aer(has_pcie_cap=False)])
    r = aer.read_errors(be, "0000:01:00.0")
    assert r.source == "none" and not r.has_correctable and not r.has_uncorrectable
    assert r.correctable == [] and r.uncorrectable == []


def test_read_errors_forced_aer_source_without_aer_falls_back():
    """A caller forcing source='aer' on a device with no AER capability must fall back to
    the universal source, not crash on (None + offset)."""
    be = MockBackend([_no_aer()])                           # device status present, no AER
    assert aer.read_errors(be, "0000:01:00.0", source="aer").source == "devstatus"
    be2 = MockBackend([_no_aer(has_pcie_cap=False)])        # neither AER nor device status
    assert aer.read_errors(be2, "0000:01:00.0", source="aer").source == "none"


# --- clear_errors dispatch -------------------------------------------------- #
def test_clear_errors_devstatus_path_clears_device_status():
    be = MockBackend([_no_aer()])
    be.inject_stuck_correctable("0000:01:00.0")
    be.read_device_status("0000:01:00.0")                # latch the stuck cor bit
    aer.clear_errors(be, "0000:01:00.0", "devstatus")
    assert be._dev("0000:01:00.0")._cor_status == 0


def test_clear_errors_none_source_is_noop():
    be = MockBackend([_no_aer(has_pcie_cap=False)])
    aer.clear_errors(be, "0000:01:00.0", "none")         # must not raise
