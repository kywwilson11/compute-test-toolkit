from computetest import nvme


def test_clean_nvme_passes_with_no_history():
    h = nvme.check_nvme("/dev/nvme0")
    assert h.ok and not h.history


def test_high_power_on_hours_flags_used_stock():
    h = nvme.check_nvme("/dev/nvme_BAD")     # mock 'BAD' drive: 6200 power-on hours
    assert h.checks["power_on_hours<=50"] is False
    assert any("power_cycles" in x for x in h.history)
    assert any("unsafe_shutdowns" in x for x in h.history)


def test_self_test_runs_and_passes_on_clean_drive():
    h = nvme.check_nvme("/dev/nvme0", run_self_test=True)
    assert h.self_test is not None
    assert h.self_test["passed"] is True and h.checks["self_test_passed"] is True


def test_self_test_fails_on_bad_drive():
    h = nvme.check_nvme("/dev/nvme_BAD", run_self_test=True)
    assert h.self_test["passed"] is False and h.checks["self_test_passed"] is False


def test_poll_self_test_completes_on_mock():
    nvme.start_self_test("/dev/nvme0", mock=True)
    log = nvme.poll_self_test("/dev/nvme0", mock=True)
    assert log["in_progress"] is False and log["passed"] is True


def test_normalize_smart_keys_maps_nvme_cli_abbreviations():
    # nvme-cli's JSON uses avail_spare / spare_thresh / percent_used; the checks read
    # the canonical names. Without normalization a healthy drive false-FAILs.
    raw = {"critical_warning": 0, "temperature": 314, "avail_spare": 100,
           "spare_thresh": 10, "percent_used": 0, "media_errors": 0,
           "num_err_log_entries": 0, "power_on_hours": 1}
    s = nvme._normalize_smart_keys(dict(raw))
    assert s["available_spare"] == 100
    assert s["available_spare_threshold"] == 10
    assert s["percentage_used"] == 0
    # and the limits now pass for a healthy drive instead of failing on the missing key
    checks = nvme._apply_limits(s, max_temp_c=70, max_power_on_hours=50)
    assert checks["available_spare>=100"] is True
    assert checks["percentage_used<2"] is True
