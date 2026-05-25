from computetest import diagnostics, margining
from computetest.backend import MockBackend, MockDevice


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
