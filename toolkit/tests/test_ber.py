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
