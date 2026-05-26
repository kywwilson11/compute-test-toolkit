"""NvmeHealth summary()/to_dict() + the _history 'used-stock' branches (mock-driven)."""
from computetest import nvme


def test_summary_clean_drive():
    h = nvme.check_nvme("/dev/nvme0")
    s = h.summary()
    assert "/dev/nvme0" in s and "MockSSD-1TB" in s and "-> OK" in s
    assert "temp=41C" in s and "media_err=0" in s
    assert "history[" not in s and "dst=" not in s       # no DST run, no history


def test_summary_bad_drive_lists_fail_and_history():
    h = nvme.check_nvme("/dev/nvme_BAD")
    s = h.summary()
    assert "FAIL(" in s and "history[" in s
    assert "power_cycles" in s and "unsafe_shutdowns" in s


def test_summary_includes_dst_result():
    ok = nvme.check_nvme("/dev/nvme0", run_self_test=True).summary()
    assert "dst=pass" in ok
    bad = nvme.check_nvme("/dev/nvme_BAD", run_self_test=True).summary()
    assert "dst=FAIL" in bad


def test_to_dict_shape():
    h = nvme.check_nvme("/dev/nvme0")
    d = h.to_dict()
    assert d["device"] == "/dev/nvme0" and d["ok"] is True
    assert d["self_test"] is None and "smart" in d and "checks" in d


def test_history_flags_significant_lifetime_writes():
    # data_units_written over the threshold is reported as used-stock history.
    h = nvme._history({"data_units_written": 200000})
    assert any("significant lifetime writes" in x for x in h)


def test_history_empty_on_fresh_drive():
    assert nvme._history({"power_cycles": 1, "unsafe_shutdowns": 0,
                          "data_units_written": 10}) == []


def test_self_test_log_in_progress_shape():
    # The mock self-test log always reports completed; assert its decoded shape.
    log = nvme.self_test_log("/dev/nvme0", mock=True)
    assert log["in_progress"] is False and log["result"] == 0 and log["passed"] is True
