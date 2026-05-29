"""
MSA harness (computetest.msa): crossed-ANOVA Gage R&R, Bland-Altman, Deming
regression, and the calibration registry. Tests pin numbers to textbook
references so the implementation can't silently drift.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from computetest import msa
from computetest.msa import (
    CalibrationEntry,
    CalibrationRegistry,
    bland_altman,
    deming_regression,
    gage_rr_anova,
    load_registry,
    station_correlation,
)


# ----------------------------------------------------------------------------
# Gage R&R: crossed ANOVA
# ----------------------------------------------------------------------------
class TestGageRrAnova:
    def test_zero_variance_yields_capable_verdict(self):
        # Every measurement identical -> no R&R signal -> capable.
        m = [[[10.0, 10.0] for _ in range(3)] for _ in range(5)]
        r = gage_rr_anova(m)
        assert r.var_total == 0.0
        assert r.pct_rr == 0.0
        assert r.verdict == "capable"

    def test_pure_part_variation_pct_rr_is_zero(self):
        # Every operator measures each part with no noise; only part means vary.
        # ⇒ %R&R = 0 (gage is perfect).
        parts = [10.0, 20.0, 30.0, 40.0, 50.0]
        m = [[[p, p, p] for _ in range(3)] for p in parts]
        r = gage_rr_anova(m)
        assert r.pct_rr == pytest.approx(0.0, abs=1e-9)
        assert r.pct_pv == pytest.approx(100.0, abs=1e-9)
        assert r.verdict == "capable"

    def test_pure_equipment_variation_dominates_pct_rr(self):
        # Same nominal value for every (part, operator); only replicate noise
        # — so all variance is EV.
        rng = [0.1, -0.1, 0.05, -0.05, 0.02]
        m = [[[10.0 + rng[(i + j + k) % len(rng)] for k in range(3)]
              for j in range(3)] for i in range(4)]
        r = gage_rr_anova(m)
        assert r.var_part == pytest.approx(0.0, abs=1e-9)
        assert r.var_operator == pytest.approx(0.0, abs=1e-9)
        assert r.pct_rr == pytest.approx(100.0, abs=1e-9)
        assert r.verdict == "not_acceptable"

    def test_anova_sums_of_squares_decompose_total_ss(self):
        # The four SS components must sum to the total SS by ANOVA identity.
        rng_ = __import__("random").Random(2024)
        m = []
        for i in range(5):
            row = []
            for j in range(3):
                row.append([10.0 + i * 2.0 + j * 0.5 + rng_.gauss(0, 0.3)
                            for _ in range(3)])
            m.append(row)
        r = gage_rr_anova(m)
        # SS_total = ΣΣΣ (yᵢⱼₖ − ȳ...)²
        flat = [y for op in m for trial in op for y in trial]
        gm = sum(flat) / len(flat)
        ss_total = sum((y - gm) ** 2 for y in flat)
        assert (r.ss_part + r.ss_operator + r.ss_interaction
                + r.ss_error) == pytest.approx(ss_total, rel=1e-9)

    def test_verdict_thresholds(self):
        # %R&R < 10  -> capable
        # 10 <= %R&R < 30 -> marginal
        # %R&R >= 30 -> not_acceptable
        # 8 parts in equal steps of part_sd; replicate noise ~ N(0, ev_sd).
        # PV SD ≈ part_sd × √(8²-1)/12 ≈ part_sd × 2.06.
        def _study(part_sd: float, ev_sd: float) -> str:
            rng_ = __import__("random").Random(99)
            m = []
            for i in range(8):
                part_val = i * part_sd
                row = []
                for _ in range(3):
                    row.append([part_val + rng_.gauss(0, ev_sd) for _ in range(4)])
                m.append(row)
            return gage_rr_anova(m).verdict
        # ratio ≈ 0.5 / 20.6 ≈ 2.4% -> capable
        assert _study(part_sd=10.0, ev_sd=0.5) == "capable"
        # ratio ≈ 0.5 / 2.06 ≈ 24% -> marginal
        assert _study(part_sd=1.0, ev_sd=0.5) == "marginal"
        # ratio ≈ 1.5 / 1.03 ≈ 100%+ -> not_acceptable
        assert _study(part_sd=0.5, ev_sd=1.5) == "not_acceptable"

    def test_validation_rejects_inconsistent_shape(self):
        with pytest.raises(ValueError, match="empty"):
            gage_rr_anova([])
        with pytest.raises(ValueError, match=">=2 trials"):
            gage_rr_anova([[[1.0]]])
        with pytest.raises(ValueError, match="crossed design"):
            gage_rr_anova([[[1, 2], [3, 4]], [[5, 6]]])      # ragged operators


# ----------------------------------------------------------------------------
# Bland-Altman
# ----------------------------------------------------------------------------
class TestBlandAltman:
    def test_identical_methods_have_zero_bias_and_zero_sd(self):
        a = [1.0, 2.0, 3.0, 4.0, 5.0]
        b = list(a)
        r = bland_altman(a, b)
        assert r.bias == 0.0
        assert r.sd_diff == 0.0
        assert r.loa_lower == 0.0 and r.loa_upper == 0.0

    def test_constant_offset_shows_up_as_bias(self):
        a = [10.0, 11.0, 12.0, 13.0, 14.0]
        b = [10.5, 11.5, 12.5, 13.5, 14.5]                  # A - B = -0.5 always
        r = bland_altman(a, b)
        assert r.bias == pytest.approx(-0.5)
        assert r.sd_diff == pytest.approx(0.0, abs=1e-9)

    def test_loa_width_is_2_x_1_96_x_sd(self):
        # By definition: limits of agreement span 2 * 1.96 * SD.
        a = [1.0, 3.0, 5.0, 7.0, 9.0]
        b = [1.1, 2.9, 5.2, 6.7, 9.4]
        r = bland_altman(a, b)
        width = r.loa_upper - r.loa_lower
        assert width == pytest.approx(2 * 1.96 * r.sd_diff)

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="length mismatch"):
            bland_altman([1.0, 2.0], [1.0])

    def test_too_few_points_raises(self):
        with pytest.raises(ValueError, match=">=2"):
            bland_altman([1.0], [1.0])


# ----------------------------------------------------------------------------
# Deming regression
# ----------------------------------------------------------------------------
class TestDemingRegression:
    def test_perfect_agreement_gives_slope_1_intercept_0(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        y = list(x)
        r = deming_regression(x, y)
        assert r.slope == pytest.approx(1.0, abs=1e-9)
        assert r.intercept == pytest.approx(0.0, abs=1e-9)
        assert r.agrees                                       # CI contains 1, 0

    def test_systematic_offset_detected_in_intercept(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        y = [v + 2.0 for v in x]                              # B = A + 2 always
        r = deming_regression(x, y)
        assert r.slope == pytest.approx(1.0, abs=1e-6)
        assert r.intercept == pytest.approx(2.0, abs=1e-6)
        # Intercept's 95% CI must NOT contain 0 here — the systematic offset is
        # detectable.
        assert not (r.ci95_intercept[0] <= 0.0 <= r.ci95_intercept[1])
        assert not r.agrees

    def test_pure_scale_factor_detected_in_slope(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
        y = [v * 1.5 for v in x]
        r = deming_regression(x, y)
        assert r.slope == pytest.approx(1.5, abs=1e-6)
        assert r.intercept == pytest.approx(0.0, abs=1e-6)
        assert not r.agrees

    def test_length_mismatch_raises(self):
        with pytest.raises(ValueError, match="length mismatch"):
            deming_regression([1.0, 2.0, 3.0], [1.0, 2.0])

    def test_too_few_points_raises(self):
        with pytest.raises(ValueError, match=">=3"):
            deming_regression([1.0, 2.0], [1.0, 2.0])


# ----------------------------------------------------------------------------
# Station correlation
# ----------------------------------------------------------------------------
class TestStationCorrelation:
    def test_three_identical_stations_all_pairs_ok(self):
        # All stations measure the same 10 golden DUTs identically.
        gold = [float(i) for i in range(10)]
        stations = {"S1": gold, "S2": gold, "S3": gold}
        pairs = station_correlation(stations)
        # C(3, 2) = 3 pairs.
        assert len(pairs) == 3
        for p in pairs:
            assert p.ok
            assert p.bland_altman.bias == 0.0

    def test_one_station_systematically_biased_flagged(self):
        gold = [float(i) for i in range(10)]
        stations = {
            "good_A": gold,
            "good_B": gold,
            "bad":    [v + 2.0 for v in gold],
        }
        pairs = station_correlation(stations)
        # good_A vs good_B passes, bad vs both others fails.
        labelled = {(p.a, p.b): p for p in pairs}
        assert labelled[("good_A", "good_B")].ok
        assert not labelled[("bad", "good_A")].ok
        assert not labelled[("bad", "good_B")].ok

    def test_single_station_returns_empty(self):
        assert station_correlation({"alone": [1.0, 2.0]}) == []

    def test_uneven_measurement_counts_raise(self):
        with pytest.raises(ValueError, match="same golden DUTs"):
            station_correlation({"A": [1.0, 2.0, 3.0], "B": [1.0, 2.0]})


# ----------------------------------------------------------------------------
# Calibration registry
# ----------------------------------------------------------------------------
class TestCalibrationRegistry:
    def _entry(self, role="rx_eye_scope", due=date(2027, 1, 15)):
        return CalibrationEntry(
            role=role, manufacturer="Tek", model="MSO64", serial="C1",
            last_calibrated=date(2026, 1, 15), next_due=due,
            certificate="NIST-1")

    def test_by_role_and_missing(self):
        reg = CalibrationRegistry(entries=[self._entry()])
        assert reg.by_role("rx_eye_scope") is not None
        assert reg.by_role("absent") is None

    def test_require_returns_missing_role(self):
        reg = CalibrationRegistry(entries=[self._entry()])
        reasons = reg.require(date(2026, 5, 29), ["rx_eye_scope", "power_smu"])
        assert reasons == ["missing role: power_smu"]

    def test_require_returns_expired_role(self):
        reg = CalibrationRegistry(entries=[
            self._entry(due=date(2026, 5, 1)),                # already expired
        ])
        reasons = reg.require(date(2026, 5, 29), ["rx_eye_scope"])
        assert reasons == [
            "calibration expired for role 'rx_eye_scope' (due 2026-05-01)"]

    def test_require_passes_when_all_present_and_current(self):
        reg = CalibrationRegistry(entries=[self._entry()])
        assert reg.require(date(2026, 5, 29), ["rx_eye_scope"]) == []

    def test_expired_and_due_within(self):
        today = date(2026, 5, 29)
        reg = CalibrationRegistry(entries=[
            self._entry(role="a", due=date(2026, 1, 1)),       # expired
            self._entry(role="b", due=date(2026, 6, 5)),       # due in 7 days
            self._entry(role="c", due=date(2027, 1, 1)),       # not due soon
        ])
        assert [e.role for e in reg.expired(today)] == ["a"]
        assert [e.role for e in reg.due_within(today, 14)] == ["b"]

    def test_load_registry_from_yaml(self, tmp_path):
        # The bundled example registry must load and validate against a known role.
        reg = load_registry(
            "configs/calibration_registry.example.yaml")
        assert reg.by_role("rx_eye_scope") is not None
        assert reg.by_role("power_smu") is not None

    def test_load_registry_from_json_fallback(self, tmp_path):
        # JSON is a strict subset of YAML 1.2; load_registry must accept it
        # even without PyYAML installed.
        import json
        p = tmp_path / "registry.json"
        p.write_text(json.dumps({"entries": [{
            "role": "x", "manufacturer": "M", "model": "Mo", "serial": "S",
            "last_calibrated": "2026-01-01", "next_due": "2027-01-01",
        }]}))
        reg = load_registry(str(p))
        assert reg.by_role("x") is not None


# ----------------------------------------------------------------------------
# Public API surface
# ----------------------------------------------------------------------------
class TestApiSurface:
    def test_module_exports_named_symbols(self):
        expected = {"GageRrResult", "gage_rr_anova", "AIAG_PASS_PERCENT",
                    "AIAG_MARGINAL_PERCENT", "BlandAltman", "DemingFit",
                    "StationPair", "bland_altman", "deming_regression",
                    "station_correlation", "CalibrationEntry",
                    "CalibrationRegistry", "load_registry"}
        for sym in expected:
            assert hasattr(msa, sym), f"missing public export: {sym}"


# ----------------------------------------------------------------------------
# Property-style sanity (not a property test framework — just invariant assertions)
# ----------------------------------------------------------------------------
class TestInvariants:
    def test_gage_rr_variances_are_nonnegative(self):
        rng_ = __import__("random").Random(7)
        m = [[[10 * i + 0.5 * j + rng_.gauss(0, 0.5) for _ in range(3)]
              for j in range(3)] for i in range(6)]
        r = gage_rr_anova(m)
        for v in (r.var_equipment, r.var_operator, r.var_interaction,
                  r.var_appraiser, r.var_part, r.var_rr, r.var_total):
            assert v >= 0.0

    def test_bland_altman_bias_is_mean_diff(self):
        a = [10.0, 20.0, 30.0, 40.0]
        b = [11.0, 19.0, 33.0, 37.0]
        r = bland_altman(a, b)
        expected = sum(a[i] - b[i] for i in range(4)) / 4
        assert r.bias == pytest.approx(expected)

    def test_deming_slope_invariant_under_scale(self):
        # Scaling both x and y by the same factor must leave the slope unchanged.
        x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        y = [v + 1.0 for v in x]
        r1 = deming_regression(x, y)
        r2 = deming_regression([v * 10 for v in x], [v * 10 for v in y])
        assert r1.slope == pytest.approx(r2.slope, rel=1e-9)
        # Intercept scales linearly.
        assert r2.intercept == pytest.approx(r1.intercept * 10, rel=1e-9)

    def test_distinct_parts_force_part_variance_positive(self):
        m = [[[100.0, 100.0]], [[110.0, 110.0]], [[120.0, 120.0]]]
        r = gage_rr_anova(m)
        # n_operators=1 -> AV cannot be computed reliably, but PV must dominate.
        assert r.var_part > 0
        assert r.pct_pv > 0.0
        # sanity: a perfectly noiseless n_operators=1 study gives some math.nan-
        # free, finite percentages.
        assert math.isfinite(r.pct_pv) and math.isfinite(r.pct_rr)
