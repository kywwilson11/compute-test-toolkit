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
