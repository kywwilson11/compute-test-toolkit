"""Measurement-System-Analysis (MSA) harness.

A station never measures what you think it measures until you've quantified its
own measurement error. The three primitives here:

* ``gage_rr`` — crossed-ANOVA Gage R&R (operator × part × trial). Reports
  Equipment Variation, Appraiser Variation, and the %R&R AIAG verdict.
* ``station_correlation`` — Bland-Altman + Deming regression across N stations
  measuring the same golden DUT. Bias, limits of agreement, slope/intercept.
* ``calibration_registry`` — minimal traceability for who, what, when, next-due.

Named explicitly in the hyperscale Mfg-Test job postings this toolkit's
roadmap is aimed at (Stargate, Google MTE). No numpy: pure stdlib statistics
so the harness drops into the existing CI without extra deps.
"""

from .calibration_registry import (
    CalibrationEntry,
    CalibrationRegistry,
    load_registry,
)
from .gage_rr import (
    AIAG_MARGINAL_PERCENT,
    AIAG_PASS_PERCENT,
    GageRrResult,
    gage_rr_anova,
)
from .station_correlation import (
    BlandAltman,
    DemingFit,
    StationPair,
    bland_altman,
    deming_regression,
    station_correlation,
)

__all__ = [
    "AIAG_MARGINAL_PERCENT", "AIAG_PASS_PERCENT", "BlandAltman",
    "CalibrationEntry", "CalibrationRegistry", "DemingFit", "GageRrResult",
    "StationPair", "bland_altman", "deming_regression", "gage_rr_anova",
    "load_registry", "station_correlation",
]
