"""Sprint 4.2.10: automotive single-pair cable TDR (OABR_CABLE_01/02)."""
from __future__ import annotations

from computetest.tsn import CableHealth, MockPhy, TdrResult, check_cable_tdr


class _NoTdrPhy:
    """A PHY/driver without cable-TDR support: ``run_tdr`` honestly returns
    ``status="skipped"`` (not a fabricated ok/fault). ``check_cable_tdr`` only
    calls ``run_tdr``, so a minimal duck-typed stub suffices -- MockPhy can only
    emit "ok"/"fault" and so cannot reach the skipped branch."""

    def run_tdr(self) -> TdrResult:
        return TdrResult(status="skipped", faults=[])


class TestCableTdr:
    def test_clean_cable_passes(self):
        h = check_cable_tdr(MockPhy())
        assert isinstance(h, CableHealth) and h.ok
        assert h.status == "ok" and h.fault_distance_m is None

    def test_open_fault_fails_with_distance(self):
        h = check_cable_tdr(MockPhy(injected_tdr_fault=True))
        assert not h.ok and h.status == "fault"
        assert h.fault_distance_m == 3.5

    def test_summary_and_to_dict(self):
        h = check_cable_tdr(MockPhy(injected_tdr_fault=True))
        assert "cable TDR" in h.summary()
        d = h.to_dict()
        assert d["status"] == "fault" and d["fault_distance_m"] == 3.5

    def test_skipped_tdr_is_honest_not_pass_or_fail(self):
        # A PHY/driver without TDR support reports "skipped". The honesty contract
        # (cable.py docstring) is that this does NOT false-FAIL a good link: ok is
        # True with no fault distance, and the verdict carries status="skipped"
        # rather than being recoloured as "ok" or "fault". A regression that gated
        # on `status == "ok"` would false-fail TDR-less hardware and trip this test.
        h = check_cable_tdr(_NoTdrPhy())
        assert isinstance(h, CableHealth)
        assert h.status == "skipped"
        assert h.ok is True
        assert h.fault_distance_m is None
        assert h.summary().endswith("-> OK")
        assert h.to_dict()["status"] == "skipped"
