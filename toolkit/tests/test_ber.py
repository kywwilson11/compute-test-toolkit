import math

import pytest

from computetest import ber


def test_zero_error_three_over_ber():
    # 95% confidence at 1e-12 with zero errors -> ~3 / BER bits.
    n = ber.bits_for_confidence(1e-12, 0.95, errors=0)
    assert n == pytest.approx(-math.log(0.05) / 1e-12, rel=1e-9)
    assert n == pytest.approx(2.996e12, rel=1e-3)


def test_confidence_consistency():
    # Assessing at exactly the required bits should land on the target confidence.
    for cl in (0.90, 0.95, 0.99):
        n = ber.bits_for_confidence(1e-12, cl, 0)
        v = ber.assess(0, n, 1e-12, cl)
        assert v.confidence_reached == pytest.approx(cl, abs=1e-3)
        assert v.status == "pass"


def test_more_bits_more_confidence():
    a = ber.confidence_le(0, 1e12, 1e-12)
    b = ber.confidence_le(0, 3e12, 1e-12)
    assert b > a


def test_ber_upper_bound_zero_error():
    n = 3e12
    assert ber.ber_upper_bound(n, 0, 0.95) == pytest.approx(-math.log(0.05) / n, rel=1e-9)


def test_errors_consistency_with_chi2_form():
    # bits_for_confidence with E errors should make assess reach the target CL.
    for e in (0, 1, 5, 20):
        n = ber.bits_for_confidence(1e-12, 0.95, errors=e)
        v = ber.assess(e, n, 1e-12, 0.95)
        assert v.confidence_reached == pytest.approx(0.95, abs=2e-3)


def test_uncorrectable_is_immediate_fail():
    v = ber.assess(errors=0, bits=1e13, target_ber=1e-12, uncorrectable=1)
    assert v.status == "fail"


def test_poisson_cdf_matches_manual():
    # P(X<=2; lam=1) = e^-1 (1 + 1 + 1/2)
    expected = math.exp(-1) * (1 + 1 + 0.5)
    assert ber.poisson_cdf(2, 1.0) == pytest.approx(expected, rel=1e-9)


def test_pure_python_fallback_matches_scipy(monkeypatch):
    """Force the dependency-free path and confirm it matches scipy to ~1e-9."""
    scipy = pytest.importorskip("scipy.special")
    monkeypatch.setattr(ber, "_HAVE_SCIPY", False)   # use the pure-python gamma
    for a, x in [(1, 0.5), (3, 2.0), (10, 8.0), (1, 3.0), (50, 40.0)]:
        assert ber.reg_lower_gamma(a, x) == pytest.approx(float(scipy.gammainc(a, x)), rel=1e-9)
    for a, y in [(1, 0.95), (6, 0.95), (21, 0.99)]:
        assert ber.reg_lower_gamma_inv(a, y) == pytest.approx(
            float(scipy.gammaincinv(a, y)), rel=1e-6)


def test_poisson_tail_no_cancellation():
    # Regression: poisson_cdf(0, 100) must be ~e^-100, not 0.0 (the 1-gcf cancellation).
    expected = math.exp(-100)
    assert ber.poisson_cdf(0, 100) == pytest.approx(expected, rel=1e-9)


def test_poisson_tail_no_cancellation_pure(monkeypatch):
    monkeypatch.setattr(ber, "_HAVE_SCIPY", False)
    assert ber.poisson_cdf(0, 100) == pytest.approx(math.exp(-100), rel=1e-9)


def test_large_a_gamma_converges(monkeypatch):
    # Regression: the pure-Python series must converge (not truncate) for large a.
    sp = pytest.importorskip("scipy.special")
    monkeypatch.setattr(ber, "_HAVE_SCIPY", False)
    for a in (1e3, 1e5):
        assert ber.reg_lower_gamma(a, a) == pytest.approx(float(sp.gammainc(a, a)), rel=1e-6)


def test_gen4_x16_bps_pinned():
    """Pin the Gen4 x16 BPS constant. Without this, mutmut mutations to either arg
    (link_bits_per_second(4, 16) -> (5, 16) or (4, 17)) survive: the constant flows
    only into a human-readable "~Xs at Gen4 x16" estimate that no test cares about."""
    from computetest.backend import link_bits_per_second
    assert ber.GEN4_X16_BPS == link_bits_per_second(4, 16)
    # Spec value: Gen4 = 16 GT/s, 16 lanes, 128/130 NRZ encoding -> ~252.06 Gb/s payload.
    assert ber.GEN4_X16_BPS == pytest.approx(252_061_538_461.5385, rel=1e-9)


def test_gen4_x16_distinguished_from_neighbours():
    # Detects (4,16)->(5,16) and (4,16)->(4,17) drift: both produce visibly different rates.
    from computetest.backend import link_bits_per_second
    assert ber.GEN4_X16_BPS != link_bits_per_second(5, 16)
    assert ber.GEN4_X16_BPS != link_bits_per_second(4, 17)


def test_gen5_x16_bps_pinned():
    """Pin the Gen5 x16 BPS constant -- the Zoox compute-platform target. Gen5 = 32 GT/s
    NRZ with 128b/130b encoding -> ~504.12 Gb/s payload at x16."""
    from computetest.backend import link_bits_per_second
    assert ber.GEN5_X16_BPS == link_bits_per_second(5, 16)
    assert ber.GEN5_X16_BPS == pytest.approx(504_123_076_923.07697, rel=1e-9)
    # Gen5 is exactly 2x Gen4 (same encoding, double GT/s).
    assert ber.GEN5_X16_BPS == pytest.approx(2 * ber.GEN4_X16_BPS, rel=1e-12)


def test_gen3_gen6_constants_match_spec():
    """Pin Gen3 + Gen6 constants too -- the toolkit supports the full Gen1-Gen6 spectrum."""
    assert ber.GEN3_X16_BPS == pytest.approx(126_030_769_230.77, rel=1e-3)
    # Gen6 is nominal at 64 GT/s PAM4 with 242/256 FLIT efficiency.
    assert ber.GEN6_X16_BPS == pytest.approx(968_000_000_000.0, rel=1e-3)


def test_time_estimate_covers_gen3_to_gen6():
    """The CLI ber-time estimate must show all four reference rates the tool supports,
    not just Gen4. Drift to 'Gen4 only' or missing a Gen number would silently regress
    the human-readable output for a Zoox (Gen5) user."""
    s = ber.time_estimate(1e12)
    for label in ("Gen3", "Gen4", "Gen5", "Gen6"):
        assert label in s, f"time_estimate dropped {label}: {s!r}"
    # And the actual seconds must be ordered (faster gen -> fewer seconds).
    import re
    secs = [float(m) for m in re.findall(r"~?(\d+\.\d+)s", s)]
    # The presentation order is Gen5, Gen4, Gen3, Gen6 -- Gen5 fastest of NRZ; Gen6 is
    # nominally faster again. The asserts are just on each pair we know is ordered:
    g5, g4, g3, g6 = secs
    assert g5 < g4 < g3, "NRZ ordering wrong: Gen5 must be fastest, Gen3 slowest"
    assert g6 < g5, "Gen6 nominal payload (PAM4) is higher than Gen5 -> fewer seconds"


# --- Fallback-path validation (force _HAVE_SCIPY=False so the fallback branches run) -- #
def _force_fallback(monkeypatch):
    monkeypatch.setattr(ber, "_HAVE_SCIPY", False)


def test_reg_lower_gamma_fallback_validates_inputs(monkeypatch):
    """Kills mutations to ber.py:93 (`a <= 0 or x < 0` -> `a < 0 or x < 0` etc.):
    without these tests the fallback validation isn't exercised when scipy is present."""
    _force_fallback(monkeypatch)
    with pytest.raises(ValueError):
        ber.reg_lower_gamma(0.0, 1.0)        # a == 0
    with pytest.raises(ValueError):
        ber.reg_lower_gamma(-0.5, 1.0)       # a < 0
    with pytest.raises(ValueError):
        ber.reg_lower_gamma(1.0, -0.5)       # x < 0
    assert ber.reg_lower_gamma(1.0, 0.0) == 0.0   # x == 0 is the special case


def test_reg_upper_gamma_fallback_validates_inputs(monkeypatch):
    """Kills mutations on ber.py:106."""
    _force_fallback(monkeypatch)
    with pytest.raises(ValueError):
        ber.reg_upper_gamma(0.0, 1.0)
    with pytest.raises(ValueError):
        ber.reg_upper_gamma(-1.0, 1.0)
    with pytest.raises(ValueError):
        ber.reg_upper_gamma(1.0, -1.0)
    assert ber.reg_upper_gamma(1.0, 0.0) == 1.0   # x == 0 -> Q = 1


def test_reg_lower_gamma_inv_fallback_validates_inputs(monkeypatch):
    """Kills mutations on ber.py:117-120 (y boundary checks)."""
    _force_fallback(monkeypatch)
    with pytest.raises(ValueError):
        ber.reg_lower_gamma_inv(1.0, -0.1)   # y < 0
    with pytest.raises(ValueError):
        ber.reg_lower_gamma_inv(1.0, 1.5)    # y > 1
    assert ber.reg_lower_gamma_inv(1.0, 1.0) == float("inf")
    assert ber.reg_lower_gamma_inv(1.0, 0.0) == 0.0


def test_poisson_cdf_validates_lambda():
    """Kills mutations on ber.py:144."""
    with pytest.raises(ValueError):
        ber.poisson_cdf(0, -1.0)
    # k < 0 returns 0 (line 143), regardless of lam.
    assert ber.poisson_cdf(-1, 5.0) == 0.0


def test_fallback_matches_scipy_at_multiple_points(monkeypatch):
    """Lock in scipy<->fallback equivalence at several (a, x) points. Mutations to the
    fallback tolerances/iteration cap that bias the result by more than ~1e-9 are caught."""
    sp = pytest.importorskip("scipy.special")
    # Capture scipy values BEFORE forcing fallback, then disable.
    points = [(2.0, 1.0), (5.0, 3.0), (10.0, 15.0), (1e3, 800.0)]
    expected = [float(sp.gammainc(a, x)) for a, x in points]
    _force_fallback(monkeypatch)
    for (a, x), want in zip(points, expected, strict=True):
        assert ber.reg_lower_gamma(a, x) == pytest.approx(want, rel=1e-8)


def test_invalid_inputs_raise():
    with pytest.raises(ValueError):
        ber.confidence_le(-1, 1e12, 1e-12)          # negative errors
    with pytest.raises(ValueError):
        ber.bits_for_confidence(0.0, 0.95)          # target_ber == 0
    with pytest.raises(ValueError):
        ber.ber_upper_bound(1e12, -1, 0.95)         # negative errors
    with pytest.raises(ValueError):
        ber.assess(0, 1e13, 1e-12, confidence_target=1.0)   # unattainable confidence
