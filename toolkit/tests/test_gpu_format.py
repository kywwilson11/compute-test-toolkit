"""GpuHealth summary()/to_dict() formatting, history, and the _int/_float parse
helpers (pure). Complements test_gpu.py (which covers the pass/fail checks)."""
from computetest import gpu


def test_summary_clean_gpu():
    h = gpu.check_gpu(0)
    s = h.summary()
    assert "GPU0" in s and "Gen4x16" in s and "temp=62C" in s and "-> OK" in s
    assert "throttle=-" in s and "xid=-" in s            # no throttle / no xid -> dashes
    assert "history[" not in s                           # clean part has no history


def test_summary_bad_gpu_lists_fails_throttle_and_history():
    h = gpu.check_gpu(99)
    s = h.summary()
    assert "FAIL(" in s
    assert "throttle=hw_thermal" in s                    # decoded throttle reason shown
    assert "history[" in s and "aggregate DBE" in s      # RMA history appended


def test_summary_includes_xid_codes():
    log = "[1] NVRM: Xid (PCI:0000:65:00): 79, GPU fell off bus\n"
    h = gpu.check_gpu(0, dmesg_reader=lambda: log)
    s = h.summary()
    assert "xid=79" in s and "-> FAIL" in s


def test_to_dict_shape():
    h = gpu.check_gpu(0)
    d = h.to_dict()
    assert d["index"] == 0 and d["link"] == "Gen4x16" and d["ok"] is True
    assert d["history"] == [] and "metrics" in d and "checks" in d


def test_apply_limits_temp_zero_is_a_fail():
    # A temp reading of 0 (sensor unread) must fail the 0<temp<=max check, not pass it.
    checks = gpu._apply_limits({"temp": 0}, 85, 4, 16, 4, 16, 100)
    assert checks["temp<=85"] is False


def test_history_empty_when_no_aggregate_or_remap():
    assert gpu._history({"ecc_uncorrected_aggregate": 0, "row_remap_count": 0}) == []


def test_scan_xids_ignores_non_xid_lines():
    # A log mixing XID and unrelated lines: only the XID line is bucketed.
    log = ("[1] some unrelated kernel message\n"
           "[2] NVRM: Xid (PCI:0000:65:00): 48, double-bit ECC\n"
           "[3] another boring line\n")
    h = gpu.check_gpu(0, dmesg_reader=lambda: log)
    assert h.metrics["xid_errors"] == {48: 1}            # noise lines skipped
    assert h.checks["no_critical_xid"] is False          # XID 48 (DBE ECC) is critical


def test_int_float_parse_helpers():
    assert gpu._int("42") == 42
    assert gpu._int("N/A") == 0 and gpu._int(None) == 0   # non-numeric -> 0
    assert gpu._float("3.5") == 3.5
    assert gpu._float("") == 0.0 and gpu._float("x") == 0.0
