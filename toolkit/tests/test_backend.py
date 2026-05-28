"""Backend mock-logic + pure-function coverage.

The RealBackend (sysfs/config-space reads) is a bench-only path, pragma'd out. This
file exercises everything that runs on a laptop: the link-rate physics helpers, the
PciDevice properties, the Backend base defaults, and the MockBackend's faithful
latch/undercount/device-status/downtrain behavior + the backend-selection policy.
"""

import pytest

from computetest import backend as bk
from computetest.backend import (
    _COR_BAD_TLP,
    AER_CORR_STATUS,
    DEVSTA_CORR,
    DEVSTA_FATAL,
    DEVSTA_NONFATAL,
    ECAP_AER,
    PORT_ENDPOINT,
    Backend,
    LinkStatus,
    MockBackend,
    MockDevice,
    PciDevice,
    RealBackend,
    link_bits_per_second,
    mock_mode,
    select_backend,
)


# --- Pure link-rate physics ------------------------------------------------- #
def test_encoding_efficiency_per_generation():
    # Gen1/2 = 8b/10b; Gen3-5 = 128b/130b; Gen6 = PAM4 FLIT/FEC payload approx.
    assert bk._encoding_efficiency(1) == 0.8
    assert bk._encoding_efficiency(2) == 0.8
    assert bk._encoding_efficiency(3) == pytest.approx(128 / 130)
    assert bk._encoding_efficiency(5) == pytest.approx(128 / 130)
    assert bk._encoding_efficiency(6) == pytest.approx(242 / 256)


def test_link_bits_per_second_known_and_unknown_speed():
    # Gen4 x16: 16 GT/s * 16 lanes * 128/130 payload efficiency.
    assert link_bits_per_second(4, 16) == pytest.approx(16e9 * 16 * 128 / 130)
    assert link_bits_per_second(0, 16) == 0.0          # unknown speed code -> 0 GT/s


# --- PciDevice properties --------------------------------------------------- #
def test_pcidevice_speed_str_known_and_unknown():
    d = PciDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)
    assert d.speed_str == "16 GT/s"
    unknown = PciDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 9, 16, 9, 16)
    assert unknown.speed_str == "code 9"               # not in the speed table


def test_pcidevice_vendor_name_known_and_fallback():
    known = PciDevice("0000:03:00.0", 0x10DE, 0, 0, 4, 16, 4, 16)
    assert known.vendor_name == "NVIDIA"
    unknown = PciDevice("0000:03:00.0", 0xABCD, 0, 0, 4, 16, 4, 16)
    assert unknown.vendor_name == "0xabcd"             # hex fallback for unknown vendors


# --- Backend base-class defaults (a minimal concrete subclass) -------------- #
class _BareBackend(Backend):
    """Implements only the abstract methods, to exercise Backend's defaults."""
    def list_devices(self): return ["0000:00:00.0"]
    def get_device(self, bdf): raise NotImplementedError
    def read_config(self, bdf, offset, size=4): return 0
    def write_config(self, bdf, offset, value, size=4): pass
    def find_ext_cap(self, bdf, cap_id): return None
    def read_link_status(self, bdf): return LinkStatus(4, 16)
    def read_device_status(self, bdf): return None
    def clear_device_status(self, bdf, mask=0xF): pass
    def clear_link_bw_status(self, bdf): pass


def test_backend_base_defaults():
    be = _BareBackend()
    assert be.link_chain("0000:00:00.0") == ["0000:00:00.0"]   # default: just itself
    assert be.read_port_type("0000:00:00.0") == PORT_ENDPOINT  # default: endpoint
    be.set_exercising("0000:00:00.0", True)                    # base no-op, no error
    assert be.is_mock() is False                               # not a MockBackend


# --- MockBackend error-accrual physics -------------------------------------- #
def test_accrue_latch_undercounts_when_polled_slowly():
    # Many physical errors in one window still latch only ONE status bit (real AER
    # undercount), but the ground-truth physical count tracks all of them. A
    # backdated accrual baseline makes the window (and so the error count) large and
    # deterministic instead of wall-clock-dependent.
    import time as _t
    be = MockBackend([MockDevice("0000:01:00.0", injected_ber=1e-6, link_speed=4,
                                 link_width=16)])
    d = be._dev("0000:01:00.0")
    d._exercising = True
    d._accrual_t = _t.monotonic() - 1.0              # a full 1s window of accrual
    aer = d._ext_caps[ECAP_AER]
    val = be.read_config("0000:01:00.0", aer + AER_CORR_STATUS, 4)
    assert val == _COR_BAD_TLP                       # exactly one representative bit
    assert be.true_error_count("0000:01:00.0") > 1   # but many physical errors accrued


def test_accrue_no_op_when_zero_ber():
    # A clean link (injected_ber=0) latches nothing even while exercised (ber<=0 guard).
    be = MockBackend([MockDevice("0000:01:00.0", injected_ber=0.0)])
    be.set_exercising("0000:01:00.0", True)
    aer = be._dev("0000:01:00.0")._ext_caps[ECAP_AER]
    assert be.read_config("0000:01:00.0", aer + AER_CORR_STATUS, 4) == 0
    assert be.true_error_count("0000:01:00.0") == 0


def test_accrue_no_op_on_nonpositive_window():
    # A zero/negative-elapsed accrual window (lam<=0) must latch nothing — the guard
    # against a clock that didn't advance between reads.
    import time as _t
    be = MockBackend([MockDevice("0000:01:00.0", injected_ber=1e-6)])
    d = be._dev("0000:01:00.0")
    d._exercising = True
    d._accrual_t = _t.monotonic() + 100.0     # baseline in the future -> dt < 0 -> lam < 0
    aer = d._ext_caps[ECAP_AER]
    assert be.read_config("0000:01:00.0", aer + AER_CORR_STATUS, 4) == 0
    assert be.true_error_count("0000:01:00.0") == 0


def test_read_config_aer_header_and_unknown_offset():
    be = MockBackend([MockDevice("0000:01:00.0")])
    aer = be._dev("0000:01:00.0")._ext_caps[ECAP_AER]
    assert be.read_config("0000:01:00.0", aer, 4) == ECAP_AER   # capability header
    assert be.read_config("0000:01:00.0", 0x1234, 4) == 0       # unmodeled offset -> 0


def test_write_config_to_unrelated_offset_is_harmless():
    be = MockBackend([MockDevice("0000:01:00.0")])
    be.write_config("0000:01:00.0", 0x1234, 0xFFFF, 4)          # not an AER status reg
    assert be.read_config("0000:01:00.0", 0x1234, 4) == 0


def test_unknown_device_raises_keyerror():
    be = MockBackend([MockDevice("0000:01:00.0")])
    with pytest.raises(KeyError):
        be.get_device("9999:99:99.9")


# --- Downtrain modeling (LBMS/LABS latch + transient speed dip) ------------- #
def test_inject_downtrain_latches_bw_and_dips_speed():
    be = MockBackend([MockDevice("0000:01:00.0", link_speed=4, link_width=16)])
    be.inject_downtrain("0000:01:00.0", per_sec=1e6)   # essentially certain to fire
    dipped = False
    for _ in range(20):
        ls = be.read_link_status("0000:01:00.0")
        if ls.speed < 4:
            dipped = True
        if ls.bw_changed:
            break
    last = be.read_link_status("0000:01:00.0")
    assert last.bw_changed and last.autonomous_bw     # LBMS/LABS latched sticky
    assert dipped                                      # speed transiently dropped (Gen4->Gen2)
    be.clear_link_bw_status("0000:01:00.0")
    assert be._dev("0000:01:00.0")._bw_latched is False


# --- Device Status fallback (no-AER error source) --------------------------- #
def _no_aer_dev(bdf="0000:01:00.0", **kw):
    d = MockDevice(bdf, **kw)
    d._ext_caps.pop(ECAP_AER)        # force the Device-Status fallback path
    return d


def test_device_status_none_without_pcie_cap():
    be = MockBackend([_no_aer_dev(has_pcie_cap=False)])
    assert be.read_device_status("0000:01:00.0") is None


def test_device_status_reports_correctable_and_uncorrectable_bits():
    be = MockBackend([_no_aer_dev()])
    be.inject_stuck_correctable("0000:01:00.0")        # sets a cor bit even at idle
    be.inject_uncorrectable("0000:01:00.0", 14)        # sets an uncor bit
    ds = be.read_device_status("0000:01:00.0")
    assert ds & DEVSTA_CORR and ds & DEVSTA_NONFATAL


def test_device_status_accrues_correctable_under_exercise():
    be = MockBackend([_no_aer_dev(injected_ber=1e-6)])
    be.set_exercising("0000:01:00.0", True)
    assert be.read_device_status("0000:01:00.0") & DEVSTA_CORR


def test_clear_device_status_selective_mask():
    be = MockBackend([_no_aer_dev()])
    d = be._dev("0000:01:00.0")
    d._cor_status = _COR_BAD_TLP       # a latched correctable bit
    d._uncor_status = 0xF              # latched uncorrectable bits
    be.clear_device_status("0000:01:00.0", mask=DEVSTA_NONFATAL | DEVSTA_FATAL)
    assert d._uncor_status == 0 and d._cor_status != 0   # only uncorrectable cleared
    be.clear_device_status("0000:01:00.0", mask=DEVSTA_CORR)
    assert d._cor_status == 0                            # now correctable cleared too


# --- Backend selection policy ----------------------------------------------- #
def test_select_backend_forced(monkeypatch):
    monkeypatch.setenv("COMPUTETEST_BACKEND", "mock")
    assert isinstance(select_backend(), MockBackend)
    monkeypatch.setenv("COMPUTETEST_BACKEND", "real")
    assert isinstance(select_backend(), RealBackend)   # construction touches no hardware


def test_select_backend_auto_uses_sysfs_presence(monkeypatch):
    monkeypatch.setenv("COMPUTETEST_BACKEND", "")
    monkeypatch.setattr(bk.os.path, "isdir", lambda p: True)
    assert isinstance(select_backend(), RealBackend)   # a Linux PCI host
    monkeypatch.setattr(bk.os.path, "isdir", lambda p: False)
    assert isinstance(select_backend(), MockBackend)   # a laptop


# --- RealBackend BDF validation (security: no path traversal via sys_root) ---- #
@pytest.mark.parametrize("evil_bdf", [
    "../../etc/passwd",          # the classic traversal
    "../0000:01:00.0",           # one level up still escapes sys_root
    "0000:01:00.0/../foo",       # mid-path traversal
    "/etc/passwd",                # absolute path
    "0000:01:00.0\x00",          # null-byte path-truncation attempt
    "0000:01:00.8",               # function > 7 is invalid PCIe
    "abcd:ef:gh.0",               # non-hex
    "",                           # empty
    "0000:01:00.0 with spaces",   # whitespace
])
def test_realbackend_rejects_invalid_bdf(tmp_path, evil_bdf):
    # Every public method that touches the filesystem must validate the BDF first,
    # so a hostile plan-file or library caller can't escape sys_root or, worse,
    # write to arbitrary config-space paths when running as root.
    be = RealBackend(sys_root=str(tmp_path))
    for op in (lambda: be.get_device(evil_bdf),
               lambda: be.read_config(evil_bdf, 0, 4),
               lambda: be.write_config(evil_bdf, 0, 0, 4),
               lambda: be.link_chain(evil_bdf)):
        with pytest.raises(ValueError, match="invalid BDF"):
            op()


def test_realbackend_accepts_valid_bdf(tmp_path):
    # Sanity: a well-formed BDF passes validation (file may not exist; we only check
    # validation does not raise — the I/O can OSError afterwards).
    be = RealBackend(sys_root=str(tmp_path))
    try:
        be.get_device("0000:01:00.0")        # may OSError below; that's fine
    except (OSError, ValueError) as e:
        assert not isinstance(e, ValueError), "valid BDF must not be rejected"


def test_mock_mode_policy(monkeypatch):
    monkeypatch.setenv("COMPUTETEST_BACKEND", "mock")
    assert mock_mode() is True
    monkeypatch.setenv("COMPUTETEST_BACKEND", "real")
    assert mock_mode() is False
    monkeypatch.setenv("COMPUTETEST_BACKEND", "")
    monkeypatch.setattr(bk.os.path, "isdir", lambda p: True)
    assert mock_mode() is False                        # real hardware available
    monkeypatch.setattr(bk.os.path, "isdir", lambda p: False)
    assert mock_mode() is True                         # no sysfs -> simulate
