"""
Station-to-station correlation: are N test stations measuring the same DUT
consistently?

Two complementary methods (the two an MT team usually runs back-to-back):

1. **Bland-Altman** — for each pair of stations, plot (A-B) vs (A+B)/2 and
   report the *bias* (mean difference) and the *95% limits of agreement*
   (bias ± 1.96·SD). Cheap visual check; tells you whether the disagreement
   is constant or scales with measurand magnitude.

2. **Deming regression** — errors-in-variables linear fit (both X and Y are
   noisy, unlike OLS). On a (B, A) scatter, slope=1 + intercept=0 ⇒ stations
   agree. The 95% CI on slope and intercept gives a hard pass/fail.

Together they answer "do these stations agree to within tolerance?" both
visually and statistically. The ``station_correlation`` helper runs both
across every station pair given measurements of the same golden DUTs.

Reference: Bland & Altman, Lancet 1986; Deming, *Statistical Adjustment of
Data*, 1943.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# Two-sided 1.96σ — exact value of the 0.975 normal quantile (standard SciPy
# returns 1.959964; pinning the truncated 1.96 used by the original Bland-Altman
# paper so the limits-of-agreement report matches every textbook reference).
_LOA_Z = 1.96


@dataclass
class BlandAltman:
    """One Bland-Altman result between two methods/stations."""
    n: int
    mean_a: float                     # mean of A
    mean_b: float                     # mean of B
    bias: float                       # mean(A - B); a constant offset between stations
    sd_diff: float                    # SD of (A - B)
    loa_lower: float                  # bias - 1.96 * sd_diff
    loa_upper: float                  # bias + 1.96 * sd_diff
    se_bias: float                    # standard error of the bias
    ci95_bias: tuple[float, float]    # 95% CI for the bias (paired-t form)

    @property
    def proportional_bias_warning(self) -> bool:
        """True if |bias| > sd_diff: the disagreement might be proportional
        to magnitude (an OLS slope check via Deming is the next step)."""
        return abs(self.bias) > self.sd_diff if self.sd_diff > 0 else False

    def to_dict(self) -> dict:
        return {
            "n": self.n, "mean_a": self.mean_a, "mean_b": self.mean_b,
            "bias": self.bias, "sd_diff": self.sd_diff,
            "loa_lower": self.loa_lower, "loa_upper": self.loa_upper,
            "se_bias": self.se_bias, "ci95_bias": list(self.ci95_bias),
            "proportional_bias_warning": self.proportional_bias_warning,
        }


@dataclass
class DemingFit:
    """Deming regression (errors-in-variables linear fit) of y on x.

    Assumes both x and y have noise. ``lambda_ratio = σ²_y / σ²_x`` (default 1
    when the two measurement systems have comparable precision). Agreement
    test: the slope's 95% CI contains 1 AND the intercept's contains 0.
    """
    n: int
    lambda_ratio: float
    slope: float
    intercept: float
    se_slope: float                   # standard error of the slope
    se_intercept: float
    ci95_slope: tuple[float, float]
    ci95_intercept: tuple[float, float]

    @property
    def agrees(self) -> bool:
        """True if slope CI contains 1 AND intercept CI contains 0."""
        return (self.ci95_slope[0] <= 1.0 <= self.ci95_slope[1]
                and self.ci95_intercept[0] <= 0.0 <= self.ci95_intercept[1])

    def to_dict(self) -> dict:
        return {"n": self.n, "lambda_ratio": self.lambda_ratio,
                "slope": self.slope, "intercept": self.intercept,
                "se_slope": self.se_slope, "se_intercept": self.se_intercept,
                "ci95_slope": list(self.ci95_slope),
                "ci95_intercept": list(self.ci95_intercept),
                "agrees": self.agrees}


@dataclass
class StationPair:
    """Pair-wise station-correlation result (Bland-Altman + Deming)."""
    a: str                            # station A label
    b: str                            # station B label
    bland_altman: BlandAltman
    deming: DemingFit

    @property
    def ok(self) -> bool:
        return self.deming.agrees

    def summary(self) -> str:
        ba = self.bland_altman
        de = self.deming
        return (f"{self.a} vs {self.b}: bias={ba.bias:+.4f} "
                f"(LoA {ba.loa_lower:+.4f}..{ba.loa_upper:+.4f}); "
                f"Deming slope={de.slope:.4f} int={de.intercept:+.4f} "
                f"-> {'OK' if self.ok else 'DISAGREE'}")

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "ok": self.ok,
                "bland_altman": self.bland_altman.to_dict(),
                "deming": self.deming.to_dict()}


# ----------------------------------------------------------------------------
# Bland-Altman
# ----------------------------------------------------------------------------
def bland_altman(a: list[float], b: list[float]) -> BlandAltman:
    """Bland-Altman analysis of paired measurements ``a`` and ``b`` on the
    same golden DUTs."""
    if len(a) != len(b):
        raise ValueError(f"length mismatch: len(a)={len(a)} len(b)={len(b)}")
    if len(a) < 2:
        raise ValueError("need >=2 paired observations for SD/CI")
    n = len(a)
    diffs = [a[i] - b[i] for i in range(n)]
    bias = sum(diffs) / n
    var = sum((d - bias) ** 2 for d in diffs) / (n - 1)         # sample variance
    sd = math.sqrt(var)
    se_bias = sd / math.sqrt(n)
    # Paired-t 95% CI for the bias. We approximate t_{0.975, n-1} via the normal
    # for simplicity (the toolkit's other math uses the same chi-squared/normal
    # approximations); for small n add a t-table lookup later.
    ci_lo, ci_hi = bias - _LOA_Z * se_bias, bias + _LOA_Z * se_bias
    return BlandAltman(
        n=n,
        mean_a=sum(a) / n, mean_b=sum(b) / n,
        bias=bias, sd_diff=sd,
        loa_lower=bias - _LOA_Z * sd, loa_upper=bias + _LOA_Z * sd,
        se_bias=se_bias, ci95_bias=(ci_lo, ci_hi),
    )


# ----------------------------------------------------------------------------
# Deming regression
# ----------------------------------------------------------------------------
def deming_regression(x: list[float], y: list[float],
                       lambda_ratio: float = 1.0) -> DemingFit:
    """Deming regression of y on x.

    ``lambda_ratio = σ²_y / σ²_x`` (1.0 = both methods equally noisy).
    Returns slope, intercept, and 95% CIs via the bootstrap (1000 iterations,
    seeded for reproducibility). No SciPy dependency.
    """
    if len(x) != len(y):
        raise ValueError(f"length mismatch: len(x)={len(x)} len(y)={len(y)}")
    if len(x) < 3:
        raise ValueError("Deming regression needs >=3 paired observations")

    try:
        slope, intercept = _deming_point(x, y, lambda_ratio)
    except ZeroDivisionError:
        raise ValueError("Deming fit undefined: x and y have zero covariance "
                         "(degenerate input)") from None

    # Bootstrap 95% CI on slope and intercept. Resample paired (x, y) WITH
    # replacement N_BOOT times; report 2.5/97.5 percentiles.
    import random
    rng = random.Random(0xDEC011)
    N_BOOT = 1000
    n = len(x)
    slopes: list[float] = []
    intercepts: list[float] = []
    for _ in range(N_BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        bx = [x[i] for i in idx]
        by = [y[i] for i in idx]
        try:
            s, i0 = _deming_point(bx, by, lambda_ratio)
        except ZeroDivisionError:
            continue                                         # degenerate resample, skip
        slopes.append(s)
        intercepts.append(i0)
    slopes.sort()
    intercepts.sort()

    def _q(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        # Linear interpolation between the two nearest order statistics.
        pos = q * (len(xs) - 1)
        lo, hi = int(pos), int(pos) + 1
        if hi >= len(xs):
            return xs[-1]
        return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

    ci_slope = (_q(slopes, 0.025), _q(slopes, 0.975))
    ci_int = (_q(intercepts, 0.025), _q(intercepts, 0.975))

    n_s = len(slopes)
    mean_s = sum(slopes) / n_s if n_s else 0.0
    se_s = math.sqrt(sum((s - mean_s) ** 2 for s in slopes) / max(1, n_s - 1)) \
        if n_s > 1 else 0.0
    n_i = len(intercepts)
    mean_i = sum(intercepts) / n_i if n_i else 0.0
    se_i = math.sqrt(sum((v - mean_i) ** 2 for v in intercepts) / max(1, n_i - 1)) \
        if n_i > 1 else 0.0

    return DemingFit(
        n=len(x), lambda_ratio=lambda_ratio,
        slope=slope, intercept=intercept,
        se_slope=se_s, se_intercept=se_i,
        ci95_slope=ci_slope, ci95_intercept=ci_int,
    )


def _deming_point(x: list[float], y: list[float],
                   lambda_ratio: float) -> tuple[float, float]:
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((xi - mx) ** 2 for xi in x) / (n - 1) if n > 1 else 0.0
    syy = sum((yi - my) ** 2 for yi in y) / (n - 1) if n > 1 else 0.0
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / (n - 1) \
        if n > 1 else 0.0
    # No guard for sxy == 0: a zero-covariance sample has no defined Deming slope,
    # so 2*sxy == 0 raises ZeroDivisionError. The bootstrap skips that resample
    # rather than counting a fake slope-0 fit (which would drag the slope CI down
    # to 0 and false-pass a disagreeing pair); the point estimate maps it to a
    # clear ValueError.
    radicand = (syy - lambda_ratio * sxx) ** 2 + 4 * lambda_ratio * sxy * sxy
    slope = (syy - lambda_ratio * sxx + math.sqrt(radicand)) / (2 * sxy)
    intercept = my - slope * mx
    return slope, intercept


# ----------------------------------------------------------------------------
# Cross-station correlation
# ----------------------------------------------------------------------------
def station_correlation(stations: dict[str, list[float]]) -> list[StationPair]:
    """Run Bland-Altman + Deming for every ordered pair of stations.

    ``stations`` maps station name -> measurements of the same N golden DUTs
    in the same order. Returns one ``StationPair`` per unordered pair (so
    A vs B is reported once, not twice).
    """
    names = sorted(stations.keys())
    if len(names) < 2:
        return []
    n_expected = len(stations[names[0]])
    for n in names:
        if len(stations[n]) != n_expected:
            raise ValueError(
                f"station {n!r} has {len(stations[n])} measurements; "
                f"expected {n_expected} (same golden DUTs across stations)")
    out: list[StationPair] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = names[i], names[j]
            ba = bland_altman(stations[a], stations[b])
            de = deming_regression(stations[b], stations[a])      # y=a on x=b
            out.append(StationPair(a=a, b=b, bland_altman=ba, deming=de))
    return out
