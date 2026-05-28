from computetest import diagnostics, margining
from computetest.backend import ECAP_AER, MockBackend, MockDevice


def test_clean_device_passes():
    be = MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)])
    d = diagnostics.diagnose(be, "0000:03:00.0", target_ber=1e-9, bert_max_s=2,
                             watch_retrains_s=0.05)
    assert d.status == "pass"


def test_speed_degraded_is_flagged_even_when_bert_passes():
    # Trains Gen3 but max is Gen4 -> degraded, though it has no errors.
    be = MockBackend([MockDevice("0000:07:00.0", 0x1B36, 0x10, 0x088000, 3, 8, 4, 8)])
    d = diagnostics.diagnose(be, "0000:07:00.0", target_ber=1e-9, bert_max_s=2,
                             watch_retrains_s=0.05)
    assert d.status == "fail"
    assert any("speed degraded" in r for r in d.reasons())


def test_retrains_flagged():
    be = MockBackend([MockDevice("0000:08:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16,
                                 inject_retrains_per_sec=80)])
    d = diagnostics.diagnose(be, "0000:08:00.0", target_ber=1e-9, do_bert=False,
                             do_margin=False, watch_retrains_s=0.4)
    assert d.link.retrains > 0
    assert d.status == "fail"


def test_margining_finds_weak_lane_on_marginal_link():
    be = MockBackend([MockDevice("0000:04:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16,
                                 injected_ber=1e-9)])
    m = margining.margin_link(be, "0000:04:00.0")
    assert len(m.lanes) == 16
    assert m.min_timing_ui < m.limit_ui      # a weak lane below the limit
    assert not m.ok


def test_eq_sweep_finds_optimal_preset():
    be = MockBackend([MockDevice("0000:09:00.0", 0x1B36, 0x10, 0x088000, 4, 8, 4, 8,
                                 optimal_preset=7)])
    sweep = margining.characterize_equalization(be, "0000:09:00.0")
    assert sweep.best is not None
    assert sweep.best.preset == 7


# --- FIX 1: a margining failure must not abort the rest of the diagnostic ------ #
def test_margining_exception_does_not_abort_diagnostic(monkeypatch):
    # On real Gen4+ hardware margining raises NotImplementedError (guarded path).
    # That must NOT lose the link/AER/BERT evidence we already gathered.
    be = MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)])

    def boom(*a, **k):
        raise NotImplementedError("real lane margining needs per-hardware validation")
    monkeypatch.setattr(diagnostics.margining, "margin_link", boom)

    d = diagnostics.diagnose(be, "0000:03:00.0", target_ber=1e-9, bert_max_s=2,
                             watch_retrains_s=0.05)
    # The rest of the diagnostic completed: link/AER/BERT all produced results.
    assert d.link is not None and d.link.ok
    assert d.aer_snapshot is not None
    assert d.bert is not None and d.bert.status == "pass"
    # Margining is recorded as unavailable (no lanes) with the reason, not a fail.
    assert d.margin is not None and not d.margin.available
    assert "validation" in d.margin.note
    assert d.status == "pass"                         # margining-unavailable is not a fail
    assert "margin:" in d.summary() and "unavailable" in d.summary()


def test_margining_generic_exception_is_caught(monkeypatch):
    # Any margining fault (a live-margining perturbation), not just NotImplementedError.
    be = MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)])
    monkeypatch.setattr(diagnostics.margining, "margin_link",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("link faulted")))
    d = diagnostics.diagnose(be, "0000:03:00.0", do_bert=False, watch_retrains_s=0.02)
    assert d.margin is not None and not d.margin.available
    assert "RuntimeError" in d.margin.note and "link faulted" in d.margin.note
    assert d.link.ok                                  # diagnostic still completed


# --- FIX 3: a skipped endpoint BERT is "skip" overall, never a silent "pass" --- #
def _no_error_source_be(bdf="0000:0b:00.0"):
    dev = MockDevice(bdf, 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, has_pcie_cap=False)
    dev._ext_caps.pop(ECAP_AER)        # no AER AND no Device Status -> cannot measure
    return MockBackend([dev]), bdf


def test_skipped_bert_makes_overall_skip_not_pass():
    be, bdf = _no_error_source_be()
    d = diagnostics.diagnose(be, bdf, target_ber=1e-9, bert_max_s=1,
                             do_margin=False, watch_retrains_s=0.02)
    assert d.bert is not None and d.bert.status == "skip"   # never error-tested
    assert d.link.ok                                        # link itself trained fine
    assert d.status == "skip"                               # NOT "pass"
    assert d.status != "pass"
    assert any("BERT skipped" in r for r in d.reasons())
    assert d.to_dict()["status"] == "skip"


def test_skip_does_not_mask_a_real_fail():
    # A real fault still wins over an incomplete measurement: degraded link => fail.
    dev = MockDevice("0000:0c:00.0", 0x10DE, 0x2204, 0x030000, 3, 16, 4, 16,
                     has_pcie_cap=False)            # Gen3 < max Gen4 (degraded)
    dev._ext_caps.pop(ECAP_AER)                     # BERT will be "skip"
    be = MockBackend([dev])
    d = diagnostics.diagnose(be, "0000:0c:00.0", target_ber=1e-9, bert_max_s=1,
                             do_margin=False, watch_retrains_s=0.02)
    assert d.bert.status == "skip"
    assert d.status == "fail"                       # the degrade dominates the skip


# --- audit Major fixes: --no-bert must NOT false-PASS  -------------------- #
def test_no_bert_returns_skip_not_pass():
    """Audit Major: with --no-bert, PcieDiagnostic.status used to fall through to
    'pass' because the bert-skip check was `bert and bert.status=='skip'`. With bert
    being None it skipped the check and returned 'pass' from an unmeasured device.
    Now: bert is None => 'skip' (we never claim PASS from a measurement we didn't make)."""
    be = MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)])
    d = diagnostics.diagnose(be, "0000:03:00.0", do_bert=False, do_margin=False,
                             watch_retrains_s=0.02)
    assert d.bert is None
    assert d.status == "skip"                       # NOT 'pass'
    assert any("--no-bert" in r for r in d.reasons())


def test_no_bert_with_devstatus_uncorrectable_fails():
    """Audit Critical: with --no-bert AND a no-AER device that has a Device Status
    uncorrectable, the old snapshot() returned (None, 0, 0) so PcieDiagnostic showed
    PASS. With the snapshot Device-Status fallback, this now correctly FAILS."""
    dev = MockDevice("0000:0d:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)
    dev._ext_caps.pop(ECAP_AER)                     # no AER -> snapshot falls back
    be = MockBackend([dev])
    be.inject_uncorrectable("0000:0d:00.0", 4)      # DLP error (uncorrectable)
    d = diagnostics.diagnose(be, "0000:0d:00.0", do_bert=False, do_margin=False,
                             watch_retrains_s=0.02)
    assert d.bert is None
    assert d.aer_snapshot.has_uncorrectable, "Device-Status uncorrectable must surface"
    assert d.status == "fail"                       # NOT a false PASS
