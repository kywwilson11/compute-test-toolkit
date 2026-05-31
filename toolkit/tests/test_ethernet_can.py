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
    f0 = h.cable_test["faults"][0]
    assert f0["code"] == "open"
    assert f0["distance_m"] > 0
    # pair is a str letter in BOTH mock and real (_real_cable_test emits 'A'..'D'); the mock
    # used to emit int 0, which the audit flagged as a mock/real type mismatch.
    assert f0["pair"] == "A" and isinstance(f0["pair"], str)


def test_parse_role_prefers_negotiated_status_over_configured_preference():
    # ethtool prints BOTH 'master-slave cfg:' (the preference) and 'master-slave status:'
    # (the NEGOTIATED role). A PHY may prefer master yet negotiate to slave; role must report
    # the status line. The old r"master-slave (?:cfg|status):" matched cfg first -> wrong role.
    et = "master-slave cfg: master preferred\nmaster-slave status: slave\n"
    assert ethernet._parse_role(et) == "slave"
    # status wins both ways
    et2 = "master-slave cfg: slave preferred\nmaster-slave status: master\n"
    assert ethernet._parse_role(et2) == "master"
    # fall back to cfg only when status is absent
    assert ethernet._parse_role("master-slave cfg: master preferred") == "master"
    # 'resolution error' / no master-slave line -> no role (not a false master/slave)
    assert ethernet._parse_role("master-slave status: resolution error") == ""
    assert ethernet._parse_role("Speed: 1000Mb/s\nLink detected: yes") == ""


def test_parse_cable_test_multiline_layout_and_fractional_metres():
    # Authoritative modern ethtool layout (kernel/ethtool netlink cable_test.c, confirmed by
    # real PHY captures): the pair RESULT CODE and the FAULT LENGTH are on SEPARATE lines.
    # A single-line `.` regex misses the fault entirely (false PASS); `(\d+)` alone reads
    # 16.80m as 80. The input is lowercased exactly as _real_cable_test lowercases stdout.
    modern = ("cable test tdr completed for device eth0.\n"
              "pair a code ok\n"
              "pair b code open circuit\n"
              "pair b, fault length: 16.80m\n")
    faults = ethernet._parse_cable_test(modern)
    assert faults == [{"pair": "B", "code": "open", "distance_m": 16.80}]
    # fractional-metre regression: a fault at 2.31m must report 2.31, NOT 31.
    frac = "pair b code open circuit\npair b, fault length: 2.31m\n"
    assert ethernet._parse_cable_test(frac)[0]["distance_m"] == 2.31
    # legacy SAME-line layout still parses (code sets the accumulator before fault-length).
    legacy = "pair b code open circuit, fault length: 2.31m\n"
    assert ethernet._parse_cable_test(legacy) == [{"pair": "B", "code": "open",
                                                   "distance_m": 2.31}]


def test_parse_cable_test_clean_cable_has_no_faults():
    # All pairs 'code OK' -> no faults, and a stray 'fault length' with no accumulated fault
    # code must NOT fabricate a fault (the OK line clears the accumulator).
    clean = ("pair a code ok\npair b code ok\npair c code ok\npair d code ok\n"
             "pair a, fault length: 0.00m\n")
    assert ethernet._parse_cable_test(clean) == []


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
