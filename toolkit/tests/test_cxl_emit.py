"""Sprint 4.4.7: ocp-diag projection for CXL results."""
from __future__ import annotations

import json
from io import StringIO

from computetest.cxl import (
    CxlRasRegisters,
    EventLog,
    EventRecord,
    EventRecordType,
    FlitMode,
    check_cxl_link,
    emit_cxl_events,
    emit_cxl_link,
    emit_cxl_ras,
    snapshot,
)
from computetest.io.ocpdiag import Emitter


def _arts(fn, *args):
    buf = StringIO()
    fn(Emitter(buf, clock=lambda: "T"), *args)
    return [json.loads(li)["testStepArtifact"] for li in buf.getvalue().splitlines()
            if "testStepArtifact" in json.loads(li)]


def _diag(arts):
    return next(a["diagnosis"] for a in arts if "diagnosis" in a)


class TestEmitLink:
    def test_pass_link(self):
        h = check_cxl_link(speed_gt=32, width=16, flit_mode=FlitMode.CXL_256B_STD,
                           cxl_negotiated=True)
        assert _diag(_arts(emit_cxl_link, h))["type"] == "PASS"

    def test_fail_link(self):
        h = check_cxl_link(speed_gt=32, width=16, flit_mode=FlitMode.PCIE_68B,
                           cxl_negotiated=False)
        assert _diag(_arts(emit_cxl_link, h))["type"] == "FAIL"


class TestEmitRas:
    def test_uncorrectable_fails(self):
        snap = snapshot(CxlRasRegisters(ue_status=(1 << 14)))   # bit 14 = InternalError
        arts = _arts(emit_cxl_ras, snap)
        names = [a["measurement"]["name"] for a in arts if "measurement" in a]
        assert "cxl.ue.InternalError" in names
        assert _diag(arts)["type"] == "FAIL"

    def test_clean_passes(self):
        assert _diag(_arts(emit_cxl_ras, snapshot(CxlRasRegisters())))["type"] == "PASS"

    def test_correctable_emitted_but_passes(self):
        snap = snapshot(CxlRasRegisters(ce_status=(1 << 6)))   # PhysicalLayerError CE
        arts = _arts(emit_cxl_ras, snap)
        names = [a["measurement"]["name"] for a in arts if "measurement" in a]
        assert "cxl.ce.PhysicalLayerError" in names
        assert _diag(arts)["type"] == "PASS"                   # CE alone doesn't fail


class TestEmitEvents:
    def test_events_series(self):
        recs = [EventRecord(handle=1, record_type=EventRecordType.DRAM,
                            log=EventLog.WARNING)]
        arts = _arts(emit_cxl_events, recs)
        kinds = [k for a in arts for k in a if k != "testStepId"]
        assert "measurementSeriesStart" in kinds
        assert _diag(arts)["type"] == "PASS"
