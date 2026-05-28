"""EthHealth summary()/to_dict() incl. cable-test formatting, and the _stat parser
(pure). Complements test_ethernet_can.py (the pass/fail + speed-parse cases)."""
import pytest

from computetest import ethernet


def test_summary_clean_link_no_cable_test():
    h = ethernet.check_ethernet("eth0")
    s = h.summary()
    assert "eth0" in s and "up=True" in s and "1000Mb" in s and "-> OK" in s
    assert "cable=" not in s                              # cable_test not requested


def test_summary_with_clean_cable_test():
    h = ethernet.check_ethernet("eth0", cable_test=True)
    s = h.summary()
    assert "cable=ok" in s and "-> OK" in s and "master" in s


def test_summary_cable_fault_locates_open():
    h = ethernet.check_ethernet("ethBAD", cable_test=True)
    s = h.summary()
    assert "cable=fault" in s and "(open@2.3m)" in s      # decoded distance-to-fault
    assert "-> FAIL(" in s


def test_to_dict_shape():
    h = ethernet.check_ethernet("eth0", cable_test=True)
    d = h.to_dict()
    assert d["iface"] == "eth0" and d["ok"] is True and d["role"] == "master"
    assert d["cable_test"]["status"] == "ok"


def test_stat_parser_extracts_digits():
    stats = "     rx_errors: 1,234\n     tx_errors: 0\n     other: 99\n"
    assert ethernet._stat(stats, "rx_errors") == 1234     # commas stripped
    assert ethernet._stat(stats, "tx_errors") == 0
    assert ethernet._stat(stats, "absent_key") == 0       # missing key -> 0


def test_can_to_dict_and_summary():
    h = ethernet.check_can("can0fd")
    s = h.summary()
    assert "can0fd FD" in s and "ERROR-ACTIVE" in s and "-> OK" in s
    d = h.to_dict()
    assert d["fd"] is True and d["state"] == "ERROR-ACTIVE" and d["ok"] is True


def test_can_bus_off_summary_fails():
    s = ethernet.check_can("canBAD").summary()
    assert "BUS-OFF" in s and "-> FAIL" in s


# --- iface validation: blocks hostile iface names from flowing into argv ----- #
@pytest.mark.parametrize("evil", [
    "../etc/passwd",     # path traversal attempt
    "--help",            # an argv-option attempt
    "eth 0",             # whitespace
    "x" * 16,            # > IFNAMSIZ-1 (15)
    "",                  # empty
    "eth0\x00",          # null byte
])
def test_validate_iface_rejects_hostile_names(evil):
    with pytest.raises(ValueError, match="invalid network interface name"):
        ethernet.check_ethernet(evil)
    with pytest.raises(ValueError, match="invalid network interface name"):
        ethernet.check_can(evil)


def test_validate_iface_accepts_canonical_names():
    # Real-shape names that pass the regex (mock backend handles 'BAD' substring).
    for iface in ("eth0", "ens3", "enp0s2", "vcan0", "can0fd"):
        # Should not raise; mock yields a CanHealth/EthHealth regardless of state.
        assert ethernet.check_ethernet(iface).iface == iface
        assert ethernet.check_can(iface).iface == iface
