from computetest import ethernet


def test_stat_matches_exact_counter_not_superstring():
    # Real-hw _stat: a superstring counter (rx_errors_phy) must not shadow the exact one.
    blob = "NIC statistics:\n     rx_errors_phy: 99\n     rx_errors: 5\n     tx_errors: 0\n"
    assert ethernet._stat(blob, "rx_errors") == 5
    assert ethernet._stat(blob, "tx_errors") == 0
    assert ethernet._stat(blob, "absent") == 0


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


def test_link_only_check_does_not_gate_on_unmeasured_throughput():
    # Default (no iperf): a good link passes and there is NO throughput_ok check to
    # false-fail on a throughput that was never measured (real-hw path returns 0.0).
    h = ethernet.check_ethernet("eth0")
    assert h.ok and "throughput_ok" not in h.checks


def test_iperf_opt_in_adds_throughput_gate():
    h = ethernet.check_ethernet("eth0", iperf=True)
    assert "throughput_ok" in h.checks and h.checks["throughput_ok"] is True
    assert h.throughput_mbps > 0


def test_can_fd_detected_on_clean_bus():
    h = ethernet.check_can("can0fd")
    assert h.fd is True and h.ok and h.state == "ERROR-ACTIVE"


def test_can_bus_off_fails():
    h = ethernet.check_can("canBAD")
    assert h.state == "BUS-OFF" and not h.ok and h.checks["not_bus_off"] is False


def test_can_fd_detection_keys_on_fd_on_not_loose_match():
    # 'fd on' is the real CAN-FD marker; 'fd off' / a stray 'fd' token must NOT match.
    sample = ("2: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 72 ... \n"
              "    can state ERROR-ACTIVE ... bitrate 500000 ... fd on dbitrate 2000000\n")
    assert ethernet._can_fd_enabled(sample) is True
    assert ethernet._can_fd_enabled(sample.replace("fd on", "fd off")) is False
    # A loose \bfd\b would have matched these; the tightened check must not.
    assert ethernet._can_fd_enabled("    can state ERROR-ACTIVE fd off restart-ms 0") is False
    assert ethernet._can_fd_enabled("    link/can promiscuity 0 minmtu 0 maxmtu 0") is False
