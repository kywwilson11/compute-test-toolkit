"""Sprint 4.2.3: 802.1AS gPTP recovered-clock Max|TE| check (Avnu criteria)."""
from __future__ import annotations

import json
from io import StringIO

import pytest

from computetest.io.ocpdiag import Emitter
from computetest.tsn import GptpTeHealth, check_gptp_time_error, emit_gptp_te


class TestGptpTimeError:
    def test_within_limits_passes(self):
        h = check_gptp_time_error([10.0, -20.0, 30.0, -15.0], lock_acq_s=2.5)
        assert isinstance(h, GptpTeHealth)
        assert h.ok
        assert h.max_te_ns == 30.0
        assert h.checks == {"max_te_within_limit": True, "lock_acquired_in_time": True}

    def test_excess_time_error_fails(self):
        h = check_gptp_time_error([10.0, 120.0], lock_acq_s=2.0)   # 120 ns > 80
        assert not h.ok and h.checks["max_te_within_limit"] is False

    def test_slow_lock_fails(self):
        h = check_gptp_time_error([5.0, -5.0], lock_acq_s=9.0)     # 9 s > 6
        assert not h.ok and h.checks["lock_acquired_in_time"] is False

    def test_peak_to_peak_and_mean(self):
        h = check_gptp_time_error([-40.0, 40.0], lock_acq_s=1.0)
        assert h.pp_te_ns == 80.0 and h.mean_te_ns == 40.0

    def test_empty_samples_rejected(self):
        with pytest.raises(ValueError, match="at least one TE sample"):
            check_gptp_time_error([], lock_acq_s=1.0)

    def test_summary_and_to_dict(self):
        h = check_gptp_time_error([10.0], lock_acq_s=1.0)
        assert "gPTP TE" in h.summary()
        assert h.to_dict()["n_samples"] == 1


class TestEmit:
    def _arts(self, health):
        buf = StringIO()
        emit_gptp_te(Emitter(buf, clock=lambda: "T"), health)
        return [json.loads(li)["testStepArtifact"]
                for li in buf.getvalue().splitlines()
                if "testStepArtifact" in json.loads(li)]

    def test_emits_series_validator_and_diagnosis(self):
        arts = self._arts(check_gptp_time_error([10.0, -20.0, 30.0], lock_acq_s=2.0))
        kinds = [k for a in arts for k in a if k != "testStepId"]
        assert "measurementSeriesStart" in kinds and "diagnosis" in kinds
        series = next(a["measurementSeriesStart"] for a in arts
                      if "measurementSeriesStart" in a)
        assert series["validators"][0]["type"] == "LESS_THAN_OR_EQUAL"
        diag = next(a["diagnosis"] for a in arts if "diagnosis" in a)
        assert diag["type"] == "PASS"

    def test_failing_health_emits_fail_diagnosis(self):
        arts = self._arts(check_gptp_time_error([200.0], lock_acq_s=1.0))
        diag = next(a["diagnosis"] for a in arts if "diagnosis" in a)
        assert diag["type"] == "FAIL"
