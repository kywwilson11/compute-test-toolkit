"""Edge cases, input validation, and the pure-Python gamma branch guards for ber.py.

Complements test_ber.py: that file proves the fallback math is *correct* (matches scipy);
this one proves the *guards* (negative inputs, empty data, boundary y) behave, so a
bare-station run can't silently return a wrong verdict. Mutation testing leans on these.
"""
import math

import pytest

from computetest import ber


# --- Public-API input validation (independent of scipy) --------------------- #
def test_public_validation_raises():
    with pytest.raises(ValueError):
        ber.poisson_cdf(0, -1.0)                       # lam < 0
    with pytest.raises(ValueError):
        ber.confidence_le(0, 1e12, 0.0)                # target_ber <= 0
    with pytest.raises(ValueError):
        ber.bits_for_confidence(1e-12, 1.5)            # confidence not in (0, 1)
    with pytest.raises(ValueError):
        ber.bits_for_confidence(1e-12, 0.95, errors=-1)
    with pytest.raises(ValueError):
        ber.ber_upper_bound(1e12, 0, 1.0)              # confidence == 1
    with pytest.raises(ValueError):
        ber.ber_lower_bound(1e12, -1, 0.95)            # errors < 0
    with pytest.raises(ValueError):
        ber.ber_lower_bound(1e12, 1, 0.0)              # confidence not in (0, 1)
    with pytest.raises(ValueError):
        ber.assess(0, 1e12, 1e-12, uncorrectable=-1)
    with pytest.raises(ValueError):
        ber.assess(0, 1e12, target_ber=-1.0)


def test_public_edge_returns():
    assert ber.poisson_cdf(-1, 5.0) == 0.0             # k < 0
    assert ber.confidence_le(0, 0.0, 1e-12) == 0.0     # no bits -> no confidence
    assert ber.ber_upper_bound(0.0, 0, 0.95) == float("inf")   # no bits -> unbounded
    assert ber.ber_lower_bound(0.0, 5, 0.95) == 0.0    # no bits -> can't reject
    assert ber.ber_lower_bound(1e12, 0, 0.95) == 0.0   # zero errors -> never reject


def test_bits_remaining_property():
    cont = ber.assess(0, 1e11, 1e-12, 0.95)            # well short of the ~3e12 needed
    assert cont.status == "continue" and cont.bits_remaining > 0
    done = ber.assess(0, 5e12, 1e-12, 0.95)
    assert done.status == "pass" and done.bits_remaining == 0.0


def test_sequential_decision_pass_continue_reject():
    assert ber.sequential_decision(0, 5e12, 1e-12, 0.95) == "pass"
    assert ber.sequential_decision(0, 1e9, 1e-12, 0.95) == "continue"
    # many errors in few bits: even the optimistic lower bound on BER exceeds target -> reject
    assert ber.sequential_decision(50, 1e12, 1e-12, 0.95) == "reject"


# --- Pure-Python gamma branch guards (force the no-scipy path) --------------- #
@pytest.fixture
def pure(monkeypatch):
    monkeypatch.setattr(ber, "_HAVE_SCIPY", False)


def test_gamma_validation_pure(pure):
    with pytest.raises(ValueError):
        ber.reg_lower_gamma(0.0, 1.0)                  # a <= 0
    with pytest.raises(ValueError):
        ber.reg_lower_gamma(2.0, -1.0)                 # x < 0
    with pytest.raises(ValueError):
        ber.reg_upper_gamma(0.0, 1.0)
    assert ber.reg_lower_gamma(2.0, 0.0) == 0.0        # x == 0 -> P = 0
    assert ber.reg_upper_gamma(2.0, 0.0) == 1.0        # x == 0 -> Q = 1
    assert ber._gser(3.0, 0.0) == 0.0                  # series guard at x <= 0


def test_gamma_inv_edges_pure(pure):
    assert ber.reg_lower_gamma_inv(3.0, 0.0) == 0.0
    assert ber.reg_lower_gamma_inv(3.0, 1.0) == float("inf")
    with pytest.raises(ValueError):
        ber.reg_lower_gamma_inv(3.0, -0.1)
    with pytest.raises(ValueError):
        ber.reg_lower_gamma_inv(3.0, 1.5)


def test_gser_gcf_match_scipy_across_the_boundary_pure(pure):
    """Both algorithms (series for x<a+1, continued-fraction for x>=a+1) match scipy."""
    sp = pytest.importorskip("scipy.special")
    for a, x in [(5.0, 5.9), (5.0, 6.1), (20.0, 21.0), (2.0, 10.0)]:
        assert ber.reg_lower_gamma(a, x) == pytest.approx(float(sp.gammainc(a, x)), rel=1e-9)
        assert ber.reg_upper_gamma(a, x) == pytest.approx(float(sp.gammaincc(a, x)), rel=1e-9)


# --- Pin the exact chi-squared bound formulas (catch E vs E+1 off-by-ones) --- #
def test_ber_lower_bound_exact_value():
    """Reject bound = chi2inv(1-CL, 2E)/(2n) = gammaincinv(E, 1-CL)/n. Pinning the value
    catches an E-vs-E+1 slip in the fail-fast reject path (the verdict alone doesn't)."""
    sp = pytest.importorskip("scipy.special")
    n, E, CL = 1e12, 10, 0.90
    assert ber.ber_lower_bound(n, E, CL) == pytest.approx(float(sp.gammaincinv(E, 1.0 - CL)) / n, rel=1e-9)
    wrong = float(sp.gammaincinv(E + 1, 1.0 - CL)) / n            # the off-by-one form
    assert abs(ber.ber_lower_bound(n, E, CL) - wrong) > 1e-15


def test_ber_upper_bound_exact_value():
    """Upper bound = chi2inv(CL, 2E+2)/(2n) = gammaincinv(E+1, CL)/n; pin it for E > 0."""
    sp = pytest.importorskip("scipy.special")
    n, E, CL = 3e12, 5, 0.95
    assert ber.ber_upper_bound(n, E, CL) == pytest.approx(float(sp.gammaincinv(E + 1, CL)) / n, rel=1e-9)
    wrong = float(sp.gammaincinv(E, CL)) / n
    assert abs(ber.ber_upper_bound(n, E, CL) - wrong) > 1e-15
