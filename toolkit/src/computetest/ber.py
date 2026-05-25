"""
Bit-error-rate confidence statistics — the Python half of the BERT, exactly the
split used at X-ES (the C engine counts errors and bits; this computes whether you
have proven the link good to a confidence level).

The model: errors are Poisson with mean lambda = n * p, where n = bits transferred
and p = the target BER. The confidence that the *true* BER is below p, given E
errors observed, is

    CL = 1 - PoissonCDF(E; n*p) = P(E+1, n*p)            # P = lower regularized gamma

from which two practical results follow:

    bits needed (E=0):  n = -ln(1 - CL) / p              # the "3 / BER" rule at 95%
    BER upper bound:    BER_upper(CL) = chi2inv(CL, 2E+2) / (2n) = Pinv(E+1, CL) / n

Everything reduces to the regularized lower incomplete gamma P(a,x) and its inverse.
We use scipy when present (fast, accurate) and fall back to a dependency-free pure
Python implementation so this runs on a bare laptop. This is also why the original
tool put the math in Python: large n*p products and factorials need care.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# --- Fast path: scipy if available ------------------------------------------- #
try:  # pragma: no cover - exercised only where scipy is installed
    from scipy.special import gammainc as _gammainc          # lower regularized P(a,x)
    from scipy.special import gammaincinv as _gammaincinv     # inverse in x
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _HAVE_SCIPY = False


# --- Pure-Python regularized lower incomplete gamma P(a, x) ------------------- #
def _gser(a: float, x: float) -> float:
    """Series expansion for P(a,x), good for x < a+1."""
    if x <= 0:
        return 0.0
    ap, total, term = a, 1.0 / a, 1.0 / a
    for _ in range(1000):
        ap += 1
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-15:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gcf(a: float, x: float) -> float:
    """Continued-fraction expansion for Q(a,x)=1-P(a,x), good for x >= a+1."""
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def reg_lower_gamma(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) in [0, 1]."""
    if _HAVE_SCIPY:
        return float(_gammainc(a, x))
    if x < 0 or a <= 0:
        raise ValueError("require a > 0 and x >= 0")
    if x == 0:
        return 0.0
    if x < a + 1.0:
        return _gser(a, x)
    return 1.0 - _gcf(a, x)


def reg_lower_gamma_inv(a: float, y: float) -> float:
    """Inverse of P(a, x) in x: return x such that reg_lower_gamma(a, x) = y."""
    if _HAVE_SCIPY:
        return float(_gammaincinv(a, y))
    if not 0.0 <= y < 1.0:
        if y == 1.0:
            return float("inf")
        raise ValueError("y must be in [0, 1)")
    if y == 0.0:
        return 0.0
    # Bracket then bisect — P is monotincreasing in x.
    lo, hi = 0.0, max(a, 1.0)
    while reg_lower_gamma(a, hi) < y:
        hi *= 2.0
        if hi > 1e300:
            return hi
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if reg_lower_gamma(a, mid) < y:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --- Public API --------------------------------------------------------------- #
def poisson_cdf(k: int, lam: float) -> float:
    """P(X <= k) for X ~ Poisson(lam)."""
    if k < 0:
        return 0.0
    return 1.0 - reg_lower_gamma(k + 1, lam)


def confidence_le(errors: int, bits: float, target_ber: float) -> float:
    """Confidence that the true BER is <= target_ber, given ``errors`` in ``bits``."""
    if bits <= 0:
        return 0.0
    lam = bits * target_ber
    return reg_lower_gamma(errors + 1, lam)


def bits_for_confidence(target_ber: float, confidence: float, errors: int = 0) -> float:
    """Bits that must be transferred to reach ``confidence`` at ``target_ber``.

    For the common zero-error case this is the classic n = -ln(1-CL)/p.
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    if errors == 0:
        return -math.log(1.0 - confidence) / target_ber
    return reg_lower_gamma_inv(errors + 1, confidence) / target_ber


def ber_upper_bound(bits: float, errors: int, confidence: float) -> float:
    """Upper bound on the true BER you can claim at ``confidence`` after a run.

    Equivalent to chi2inv(CL, 2*errors+2) / (2*bits).
    """
    if bits <= 0:
        return float("inf")
    return reg_lower_gamma_inv(errors + 1, confidence) / bits


@dataclass
class BertVerdict:
    """Result of assessing a (errors, bits) measurement against a target."""

    errors: int
    bits: float
    target_ber: float
    confidence_target: float
    confidence_reached: float   # CL that true BER <= target_ber, given the data
    ber_upper: float            # BER upper bound at the target confidence
    bits_needed: float          # bits required to reach the target (given errors)
    status: str                 # "pass" | "continue" | "fail"

    @property
    def bits_remaining(self) -> float:
        return max(0.0, self.bits_needed - self.bits)

    def summary(self) -> str:
        return (f"E={self.errors} n={self.bits:.3e} | CL={self.confidence_reached:.4f} "
                f"(target {self.confidence_target:.2f}) | BER<={self.ber_upper:.2e} "
                f"| {self.status.upper()}")


def assess(errors: int, bits: float, target_ber: float = 1e-12,
           confidence_target: float = 0.95,
           uncorrectable: int = 0) -> BertVerdict:
    """Decide pass / continue / fail for a BERT measurement.

    * Any uncorrectable error -> immediate FAIL (correctable math doesn't apply).
    * Else PASS once the confidence that BER <= target reaches the target CL.
    * Else CONTINUE (keep transferring bits).
    """
    cl = confidence_le(errors, bits, target_ber)
    ber_ub = ber_upper_bound(bits, errors, confidence_target)
    needed = bits_for_confidence(target_ber, confidence_target, errors)
    if uncorrectable > 0:
        status = "fail"
    elif cl >= confidence_target:
        status = "pass"
    else:
        status = "continue"
    return BertVerdict(errors, bits, target_ber, confidence_target, cl,
                       ber_ub, needed, status)


if __name__ == "__main__":  # quick sanity demo: `python -m computetest.ber`
    for cl in (0.90, 0.95, 0.99):
        n = bits_for_confidence(1e-12, cl, 0)
        print(f"zero-error, target 1e-12 @ {cl:.0%} confidence -> {n:.3e} bits "
              f"(~{n / (31.5e9 * 8):.1f}s at Gen4 x16)")
    print(assess(errors=0, bits=3.0e12, target_ber=1e-12).summary())
    print(assess(errors=5, bits=3.0e12, target_ber=1e-12).summary())
