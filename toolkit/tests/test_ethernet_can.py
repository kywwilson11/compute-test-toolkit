from computetest import ethernet


def test_cable_test_ok_on_good_link():
    h = ethernet.check_ethernet("eth0", cable_test=True)
    assert h.cable_test["status"] == "ok" and h.checks["cable_ok"] is True and h.ok


def test_cable_test_fault_locates_open():
    h = ethernet.check_ethernet("ethBAD", cable_test=True)
    assert h.cable_test["status"] == "fault" and h.checks["cable_ok"] is False
    assert h.cable_test["faults"][0]["code"] == "open"
    assert h.cable_test["faults"][0]["distance_m"] > 0


def test_speed_parse_handles_mb_and_g():
    assert ethernet._parse_speed("Speed: 1000Mb/s") == 1000
    assert ethernet._parse_speed("Speed: 2.5GBASE-T1") == 2500     # was broken (gave 25)
    assert ethernet._parse_speed("Speed: 10000Mb/s") == 10000
    assert ethernet._parse_speed("no speed here") == 0


def test_can_fd_detected_on_clean_bus():
    h = ethernet.check_can("can0fd")
    assert h.fd is True and h.ok and h.state == "ERROR-ACTIVE"


def test_can_bus_off_fails():
    h = ethernet.check_can("canBAD")
    assert h.state == "BUS-OFF" and not h.ok and h.checks["not_bus_off"] is False
