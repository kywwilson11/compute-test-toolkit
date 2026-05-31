from computetest import gpu


def test_clean_gpu_passes_with_no_history():
    h = gpu.check_gpu(0)
    assert h.ok and not h.history


def test_bad_gpu_reports_rma_history_separately_from_failures():
    h = gpu.check_gpu(99)
    assert not h.ok
    # lifetime aggregate ECC and already-remapped rows are HISTORY, not the fail reason
    assert any("aggregate DBE" in x for x in h.history)
    assert any("remapped_rows" in x for x in h.history)


def test_xid_scrape_fails_on_critical_code():
    log = "[10.0] NVRM: Xid (PCI:0000:65:00): 79, pid=1234, GPU has fallen off the bus\n"
    h = gpu.check_gpu(0, dmesg_reader=lambda: log)
    assert h.metrics["xid_errors"] == {79: 1}
    assert h.checks["no_critical_xid"] is False and not h.ok


def test_xid_benign_code_does_not_fail():
    log = "[10.0] NVRM: Xid (PCI:0000:65:00): 13, pid=1234, graphics exception\n"
    h = gpu.check_gpu(0, dmesg_reader=lambda: log)
    assert h.metrics["xid_errors"] == {13: 1}
    assert h.checks["no_critical_xid"] is True   # XID 13 isn't a hardware-fault code


def test_clean_gpu_volatile_vs_aggregate():
    h = gpu.check_gpu(0)
    # passes on volatile (this test) even though aggregate lifetime counters can be >0
    assert h.metrics["ecc_uncorrected_volatile"] == 0


def test_xid_92_label_is_high_single_bit_not_contained():
    # NVIDIA's Xid catalog: 92 = "High single-bit ECC error rate" (a correctable
    # SBE-RATE signal), 94 = "Contained ECC error", 95 = "Uncontained ECC error".
    # Regression: 92 used to duplicate 94's "contained ECC" label.
    labels = gpu._XID_CRITICAL
    assert "single-bit" in labels[92]
    assert labels[92] != labels[94]            # 92 is NOT the contained-ECC code
    assert labels[94] == "contained ECC"
    assert labels[95] == "uncontained ECC"
