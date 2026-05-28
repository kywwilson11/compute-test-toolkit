"""Tests for the Python conductor (engine='c'): it sequences idle->exercise->idle,
drives the C counter in increments, and applies the pass/reject/extend/timeout logic.
A fake c_runner stands in for the C binary so the decision logic is fully testable
without hardware; idle reads go through the mock backend."""
from computetest import bert
from computetest.backend import MockBackend, MockDevice, link_bits_per_second

BPS = link_bits_per_second(4, 16)


def _runner(rate, *, unc=0):
    """A fake C counter: returns errors ~ rate * bits for each window it's asked to run."""
    def run(bdf, secs):
        bits = BPS * secs
        errs = int(round(rate * bits))
        return {"source": "aer", "link_speed_code": 4, "link_width": 16,
                "link_unknown": False, "bits": bits, "correctable": errs,
                "uncorrectable": unc, "uncorrectable_bits": (1 << 14) if unc else 0,
                "per_correctable": {"BadTLP": errs} if errs else {}}
    return run


def _stray_then_clean(n_first):
    """First window reports n_first correctable errors, every later window reports 0."""
    state = {"calls": 0}

    def run(bdf, secs):
        bits = BPS * secs
        errs = n_first if state["calls"] == 0 else 0
        state["calls"] += 1
        return {"source": "aer", "link_speed_code": 4, "link_width": 16,
                "link_unknown": False, "bits": bits, "correctable": errs,
                "uncorrectable": 0, "uncorrectable_bits": 0,
                "per_correctable": {"BadTLP": errs} if errs else {}, "_calls": state}
    run.state = state
    return run


def _be():
    return MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16)])


def _run(be, runner, **kw):
    # Real 1e-12 target so window sizing isn't distorted by the 0.05s minimum window.
    # The fake runner is instant, so large "seconds" cost no real wall-clock time.
    kw.setdefault("target_ber", 1e-12)
    kw.setdefault("confidence", 0.95)
    kw.setdefault("max_seconds", 60)
    return bert.run_bert(be, "0000:03:00.0", engine="c", c_runner=runner, **kw)


def test_clean_link_passes_first_window():
    r = _run(_be(), _runner(0.0))
    assert r.status == "pass" and r.correctable == 0 and r.aer_source == "aer"


def test_high_rate_rejected_fast():
    # 3x the target rate: the lower BER bound exceeds target -> reject (fail fast).
    r = _run(_be(), _runner(3e-12))
    assert r.status == "fail" and r.correctable > 0


def test_stray_error_then_clean_extends_and_passes():
    runner = _stray_then_clean(1)   # one early error, then clean
    r = _run(_be(), runner)
    assert r.status == "pass"
    assert runner.state["calls"] > 1   # it extended past the first window


def test_uncorrectable_is_immediate_fail():
    r = _run(_be(), _runner(0.0, unc=1))
    assert r.status == "fail" and "CmplTO" in r.uncorrectable_decode


def test_at_target_rate_times_out_within_budget():
    # rate == target never converges; bounded by extend_budget -> fail (not infinite).
    r = _run(_be(), _runner(1e-12), extend_budget=3.0)
    assert r.status == "fail"


def test_idle_fault_fails_regardless_of_clean_exercise():
    be = _be()
    be.inject_stuck_correctable("0000:03:00.0")   # a bit set even at idle
    r = _run(be, _runner(0.0))                     # exercise window is clean
    assert r.status == "fail" and r.stuck is True and "idle" in r.note


def test_no_error_source_skips():
    from computetest.backend import ECAP_AER
    dev = MockDevice("0000:0b:00.0", has_pcie_cap=False)
    dev._ext_caps.pop(ECAP_AER)
    r = bert.run_bert(MockBackend([dev]), "0000:0b:00.0", engine="c",
                      c_runner=_runner(0.0), target_ber=1e-9)
    assert r.status == "skip" and not r.aer_available


# --- Gen5 calibration: C engine poll-rate-vs-saturation warning ------------- #
def _runner_with_poll_rate(rate, poll_rate_hz, *, unc=0):
    """Same as _runner but also reports poll_rate_hz so the conductor can flag
    a loaded host where W1C bit-counting saturates above this rate."""
    def run(bdf, secs):
        bits = BPS * secs
        errs = int(round(rate * bits))
        return {"source": "aer", "link_speed_code": 4, "link_width": 16,
                "link_unknown": False, "bits": bits, "correctable": errs,
                "uncorrectable": unc, "uncorrectable_bits": (1 << 14) if unc else 0,
                "poll_rate_hz": poll_rate_hz,
                "per_correctable": {"BadTLP": errs} if errs else {}}
    return run


def test_low_poll_rate_warns_when_below_target_saturation():
    """At Gen4 x16 ~252 Gb/s, target 1e-8, the calibration cutoff is 10x target_ber x
    bps ~= 25 kHz. A 1 kHz poll rate is below that, so the conductor must surface the
    calibration concern in the note."""
    runner = _runner_with_poll_rate(rate=0.0, poll_rate_hz=1000)
    r = _run(_be(), runner, target_ber=1e-8)
    assert "poll rate" in r.note and "saturate" in r.note


def test_healthy_poll_rate_is_silent():
    # A 100 kHz poll rate at the prod 1e-12 target is far above the saturation point;
    # nothing about poll_rate should appear in the note.
    runner = _runner_with_poll_rate(rate=0.0, poll_rate_hz=100_000)
    r = _run(_be(), runner, target_ber=1e-12)
    assert "poll rate" not in r.note


def test_missing_poll_rate_does_not_break_conductor():
    """Back-compat: the existing _runner fixture doesn't set poll_rate_hz, and the
    conductor must still produce a valid verdict (just no calibration note)."""
    r = _run(_be(), _runner(0.0))                    # legacy runner: no poll_rate_hz
    assert r.status == "pass" and "poll rate" not in r.note
