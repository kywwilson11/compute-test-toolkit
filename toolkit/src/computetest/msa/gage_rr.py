"""
Gage Repeatability & Reproducibility (Gage R&R) — crossed ANOVA.

Decomposes total measurement variance into:

* **Equipment Variation (EV)** — σ² of repeat measurements by the SAME
  operator on the SAME part (the gage's own noise floor).
* **Appraiser Variation (AV)** — σ² introduced by the *operator* effect plus
  the *operator×part* interaction.
* **Part Variation (PV)** — σ² actually attributable to the parts themselves.

The AIAG rule of thumb on the resulting **%R&R = (σ_R&R / σ_total) × 100**:

* < 10 %  : measurement system is *capable* — go ahead, the data tells you
            about the parts.
* 10–30 % : *marginal* — acceptable if reworking the gage is expensive AND
            the parts vary much more than the gage. Document the choice.
* > 30 %  : not acceptable — the gage swamps the signal you're trying to
            measure. Find and fix the noise source before the data is used
            for limit-setting or process control.

Notes:

* Crossed design only: every operator measures every part, every replicate.
  Nested designs (different operators on different parts) need a different
  ANOVA — out of scope.
* No numpy: pure stdlib. The arithmetic is small (≤ a few dozen FLOPs per
  cell). Tests pin the answer to known textbook values.

Reference: AIAG MSA Manual 4th Ed., §IV/D — Gage R&R Crossed ANOVA Method.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# AIAG-recommended thresholds. Override in a measurement_plan.yaml if your
# program owns a different limit.
AIAG_PASS_PERCENT = 10.0
AIAG_MARGINAL_PERCENT = 30.0


@dataclass
class GageRrResult:
    """One Gage-R&R study summary.

    All variances are in the squared units of the measurand (e.g. UI² for a
    timing margin study, °C² for a temperature study). Percentages are the
    AIAG study-variation form: σ_<component> / σ_total × 100.
    """
    n_parts: int
    n_operators: int
    n_trials: int
    # Sum of squares
    ss_part: float
    ss_operator: float
    ss_interaction: float
    ss_error: float
    # Mean squares (= SS / df)
    ms_part: float
    ms_operator: float
    ms_interaction: float
    ms_error: float
    # Variance components (negative components clipped to 0 per AIAG)
    var_equipment: float                  # EV — repeatability
    var_operator: float                   # operator effect
    var_interaction: float                # operator × part interaction
    var_appraiser: float                  # AV = var_operator + var_interaction
    var_part: float                       # PV — part-to-part
    var_rr: float                         # R&R = EV + AV
    var_total: float                      # PV + R&R
    # Percentages (σ ratio × 100)
    pct_ev: float
    pct_av: float
    pct_rr: float
    pct_pv: float
    # AIAG verdict
    verdict: str                          # "capable" | "marginal" | "not_acceptable"

    @property
    def ok(self) -> bool:
        return self.verdict == "capable"

    def summary(self) -> str:
        return (f"Gage R&R: p={self.n_parts} o={self.n_operators} "
                f"r={self.n_trials}  "
                f"%EV={self.pct_ev:.1f} %AV={self.pct_av:.1f} "
                f"%R&R={self.pct_rr:.1f}  -> {self.verdict.upper()}")

    def to_dict(self) -> dict:
        return {
            "n_parts": self.n_parts, "n_operators": self.n_operators,
            "n_trials": self.n_trials,
            "ss": {"part": self.ss_part, "operator": self.ss_operator,
                   "interaction": self.ss_interaction, "error": self.ss_error},
            "ms": {"part": self.ms_part, "operator": self.ms_operator,
                   "interaction": self.ms_interaction, "error": self.ms_error},
            "var": {"equipment": self.var_equipment, "operator": self.var_operator,
                    "interaction": self.var_interaction,
                    "appraiser": self.var_appraiser, "part": self.var_part,
                    "rr": self.var_rr, "total": self.var_total},
            "pct": {"ev": self.pct_ev, "av": self.pct_av,
                    "rr": self.pct_rr, "pv": self.pct_pv},
            "verdict": self.verdict, "ok": self.ok,
        }


def _validate(measurements) -> tuple[int, int, int]:
    """Return (n_parts, n_operators, n_trials) after shape and value checks.

    Shape: ``measurements[i][j][k]`` is the k-th replicate of operator j on
    part i. All cells must have the same number of trials (crossed design).
    """
    if not measurements:
        raise ValueError("measurements is empty")
    n_parts = len(measurements)
    n_operators = len(measurements[0])
    if n_operators == 0:
        raise ValueError("each part needs >=1 operator's rows")
    n_trials = len(measurements[0][0])
    if n_trials < 2:
        raise ValueError("Gage R&R needs >=2 trials per (part, operator) cell")
    for i in range(n_parts):
        if len(measurements[i]) != n_operators:
            raise ValueError(
                f"part {i}: expected {n_operators} operators, got "
                f"{len(measurements[i])} (crossed design only)")
        for j in range(n_operators):
            if len(measurements[i][j]) != n_trials:
                raise ValueError(
                    f"cell (part={i}, op={j}): expected {n_trials} trials, "
                    f"got {len(measurements[i][j])}")
    return n_parts, n_operators, n_trials


def gage_rr_anova(measurements) -> GageRrResult:
    """Crossed-ANOVA Gage R&R.

    ``measurements`` is a 3-D nested sequence indexed
    ``measurements[part][operator][trial]``.

    Returns the variance-component decomposition + AIAG verdict.

    The math is the standard two-way crossed ANOVA with replication:

    * SS_part        = o·r · Σᵢ (ȳᵢ.. − ȳ...)²
    * SS_operator    = p·r · Σⱼ (ȳ.ⱼ. − ȳ...)²
    * SS_interaction = r   · Σᵢⱼ (ȳᵢⱼ. − ȳᵢ.. − ȳ.ⱼ. + ȳ...)²
    * SS_error       = ΣΣΣ (yᵢⱼₖ − ȳᵢⱼ.)²
    * df:  p−1,  o−1,  (p−1)(o−1),  p·o·(r−1)

    Variance components (negatives clipped to 0 per AIAG):

    * σ²_ε         = MS_error
    * σ²_op×part   = (MS_int − MS_ε) / r
    * σ²_op        = (MS_op  − MS_int) / (p·r)
    * σ²_part      = (MS_part − MS_int) / (o·r)
    """
    p, o, r = _validate(measurements)
    grand_sum = 0.0
    n_obs = p * o * r
    for i in range(p):
        for j in range(o):
            for k in range(r):
                grand_sum += measurements[i][j][k]
    grand_mean = grand_sum / n_obs

    part_mean = [0.0] * p
    op_mean = [0.0] * o
    cell_mean = [[0.0] * o for _ in range(p)]
    for i in range(p):
        for j in range(o):
            s = sum(measurements[i][j])
            cell_mean[i][j] = s / r
        part_mean[i] = sum(cell_mean[i]) / o
    for j in range(o):
        op_mean[j] = sum(cell_mean[i][j] for i in range(p)) / p

    ss_part = o * r * sum((m - grand_mean) ** 2 for m in part_mean)
    ss_op = p * r * sum((m - grand_mean) ** 2 for m in op_mean)
    ss_int = r * sum(
        (cell_mean[i][j] - part_mean[i] - op_mean[j] + grand_mean) ** 2
        for i in range(p) for j in range(o))
    ss_err = sum(
        (measurements[i][j][k] - cell_mean[i][j]) ** 2
        for i in range(p) for j in range(o) for k in range(r))

    df_part, df_op, df_int, df_err = p - 1, o - 1, (p - 1) * (o - 1), p * o * (r - 1)
    ms_part = ss_part / df_part if df_part else 0.0
    ms_op = ss_op / df_op if df_op else 0.0
    ms_int = ss_int / df_int if df_int else 0.0
    ms_err = ss_err / df_err if df_err else 0.0

    var_ev = max(0.0, ms_err)
    var_int = max(0.0, (ms_int - ms_err) / r) if r else 0.0
    var_op = max(0.0, (ms_op - ms_int) / (p * r)) if (p * r) else 0.0
    var_av = var_op + var_int
    var_part = max(0.0, (ms_part - ms_int) / (o * r)) if (o * r) else 0.0
    var_rr = var_ev + var_av
    var_total = var_part + var_rr

    if var_total <= 0:
        # Degenerate: every measurement was identical. By convention %R&R is 0
        # (no measurement noise relative to itself); the system is capable.
        pct_ev = pct_av = pct_rr = pct_pv = 0.0
    else:
        s_total = math.sqrt(var_total)
        pct_ev = math.sqrt(var_ev) / s_total * 100.0
        pct_av = math.sqrt(var_av) / s_total * 100.0
        pct_rr = math.sqrt(var_rr) / s_total * 100.0
        pct_pv = math.sqrt(var_part) / s_total * 100.0

    verdict = ("capable" if pct_rr < AIAG_PASS_PERCENT
               else "marginal" if pct_rr < AIAG_MARGINAL_PERCENT
               else "not_acceptable")

    return GageRrResult(
        n_parts=p, n_operators=o, n_trials=r,
        ss_part=ss_part, ss_operator=ss_op, ss_interaction=ss_int,
        ss_error=ss_err,
        ms_part=ms_part, ms_operator=ms_op, ms_interaction=ms_int,
        ms_error=ms_err,
        var_equipment=var_ev, var_operator=var_op, var_interaction=var_int,
        var_appraiser=var_av, var_part=var_part,
        var_rr=var_rr, var_total=var_total,
        pct_ev=pct_ev, pct_av=pct_av, pct_rr=pct_rr, pct_pv=pct_pv,
        verdict=verdict,
    )
