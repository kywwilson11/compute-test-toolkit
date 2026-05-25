from computetest import aer
from computetest.backend import (AER_CORR_STATUS, ECAP_AER, MockBackend, MockDevice)


def test_enumerate_and_device_fields():
    be = MockBackend()
    bdfs = be.list_devices()
    assert len(bdfs) == 5
    d = be.get_device(bdfs[0])
    assert d.vendor_id == 0x10DE and d.current_link_width == 16


def test_find_ext_cap_and_link_status():
    be = MockBackend()
    bdf = be.list_devices()[0]
    assert be.find_ext_cap(bdf, ECAP_AER) == 0x100
    speed, width, training = be.read_link_status(bdf)
    assert speed == 4 and width == 16 and training is False


def test_decode_correctable_bits():
    names = [n for _, n, _ in aer.decode_correctable((1 << 6) | (1 << 12))]
    assert names == ["BadTLP", "ReplayTO"]


def test_decode_uncorrectable_bits():
    names = [n for _, n, _ in aer.decode_uncorrectable(1 << 14)]
    assert names == ["CmplTO"]


def test_w1c_clear_only_set_bits_and_verify():
    be = MockBackend([MockDevice("0000:01:00.0", injected_ber=0.0)])
    dev = be._dev("0000:01:00.0")
    dev._cor_status = (1 << 6) | (1 << 7)        # BadTLP + BadDLLP latched
    snap = aer.snapshot(be, "0000:01:00.0")
    assert snap.has_correctable
    res = aer.clear(be, "0000:01:00.0")
    assert res["correctable"].cleared == (1 << 6) | (1 << 7)
    assert res["correctable"].ok                  # nothing stuck
    assert aer.snapshot(be, "0000:01:00.0").correctable_raw == 0   # verified cleared


def test_clear_is_noop_when_nothing_set():
    be = MockBackend([MockDevice("0000:01:00.0", injected_ber=0.0)])
    res = aer.clear(be, "0000:01:00.0")
    assert res["correctable"].cleared == 0 and res["correctable"].ok


def test_persistent_uncorrectable_relatches_after_clear():
    be = MockBackend([MockDevice("0000:01:00.0")])
    be.inject_uncorrectable("0000:01:00.0", 14)
    aer.clear(be, "0000:01:00.0", correctable=False)
    # A persistent fault re-latches, so the next read still shows it.
    assert aer.snapshot(be, "0000:01:00.0").has_uncorrectable
