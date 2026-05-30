"""
Sprint 4.1.2: GMSL SerDes lock + mode-negotiation check.

A locked link that silently fell back to a lower mode (GMSL3 -> NRZ-6G ->
GMSL2) is the defect this layer exists to catch — so ``mode_ok`` is independent
of ``all_locked``. Also pins that the Health shape plugs into the generic
ocp-diag ``emit_health`` path.
"""
from __future__ import annotations

import json
from io import StringIO

from computetest.gmsl import (
    GmslMode,
    MockSerDes,
    SerDesLinkHealth,
    check_serdes_link,
)
from computetest.io.ocpdiag import Emitter, emit_health


class TestCheckSerdesLink:
    def test_healthy_top_mode_passes(self):
        h = check_serdes_link(MockSerDes(links=4))
        assert isinstance(h, SerDesLinkHealth)
        assert h.ok
        assert h.checks == {"all_locked": True, "mode_ok": True, "lock_time_ok": True}

    def test_degraded_mode_fails_mode_only(self):
        # Locked, but negotiated DOWN to NRZ-6G when PAM4-12G was expected.
        h = check_serdes_link(MockSerDes(injected_mode=GmslMode.NRZ_6G))
        assert not h.ok
        assert h.checks["all_locked"] is True       # link is up...
        assert h.checks["mode_ok"] is False          # ...but not at design rate

    def test_unlocked_fails(self):
        h = check_serdes_link(MockSerDes(injected_locked=False))
        assert not h.ok
        assert h.checks["all_locked"] is False

    def test_expected_mode_override_passes(self):
        # If the program *designs* for NRZ-6G, that mode passes.
        h = check_serdes_link(MockSerDes(injected_mode=GmslMode.NRZ_6G),
                              expect_mode=GmslMode.NRZ_6G)
        assert h.ok

    def test_lock_time_budget_enforced(self):
        # Mock lock times sit near ~8 ms; a 1 ms budget fails lock_time_ok.
        h = check_serdes_link(MockSerDes(), max_lock_ms=1.0)
        assert h.checks["lock_time_ok"] is False
        assert not h.ok

    def test_summary_and_to_dict(self):
        h = check_serdes_link(MockSerDes(injected_mode=GmslMode.NRZ_6G))
        s = h.summary()
        assert "GMSL serdes" in s and "FAIL(" in s and "mode_ok" in s
        d = h.to_dict()
        assert d["expect_mode"] == "GMSL3-PAM4-12G" and d["ok"] is False
        assert len(d["links"]) == 2


class TestOcpDiagIntegration:
    def _emit(self, h) -> list[dict]:
        buf = StringIO()
        em = Emitter(buf, clock=lambda: "2026-05-29T00:00:00.000Z")
        emit_health(em, h, label="gmsl.serdes")
        return [json.loads(line) for line in buf.getvalue().splitlines()]

    def test_degraded_link_emits_fail_diagnosis(self):
        h = check_serdes_link(MockSerDes(injected_mode=GmslMode.NRZ_6G))
        lines = self._emit(h)
        diags = [li["testStepArtifact"]["diagnosis"] for li in lines
                 if "testStepArtifact" in li and "diagnosis" in li["testStepArtifact"]]
        assert len(diags) == 1
        assert diags[0]["type"] == "FAIL"
        assert diags[0]["verdict"] == "gmsl.serdes.fail"

    def test_healthy_link_emits_pass_and_check_measurements(self):
        h = check_serdes_link(MockSerDes())
        lines = self._emit(h)
        measurements = {li["testStepArtifact"]["measurement"]["name"]:
                        li["testStepArtifact"]["measurement"]["value"]
                        for li in lines
                        if "testStepArtifact" in li
                        and "measurement" in li["testStepArtifact"]}
        # The checks dict is flattened one level into measurements.
        assert measurements["checks.all_locked"] is True
        assert measurements["checks.mode_ok"] is True
