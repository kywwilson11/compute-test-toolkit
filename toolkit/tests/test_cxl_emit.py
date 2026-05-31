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

    def test_undecoded_ue_bit_fails_with_raw_cause(self):
        # Bit 31 is not in CXL_UE_BITS (reserved/future), so has_uncorrectable is
        # True but the decoded list is empty -> the FAIL must name the raw mask,
        # never an empty 'uncorrectable: '.
        snap = snapshot(CxlRasRegisters(ue_status=(1 << 31)))
        diag = _diag(_arts(emit_cxl_ras, snap))
        assert diag["type"] == "FAIL"
        assert "0x80000000" in diag["message"]

    def test_correctable_emitted_but_passes(self):
        snap = snapshot(CxlRasRegisters(ce_status=(1 << 6)))   # PhysicalLayerError CE
        arts = _arts(emit_cxl_ras, snap)
        names = [a["measurement"]["name"] for a in arts if "measurement" in a]
        assert "cxl.ce.PhysicalLayerError" in names
        assert _diag(arts)["type"] == "PASS"                   # CE alone doesn't fail


class TestEmitEvents:
    def test_events_series(self):
        # WARNING is BELOW the FAILURE threshold, so this genuinely passes the
        # severity gate (not a hardcoded PASS).
        recs = [EventRecord(handle=1, record_type=EventRecordType.DRAM,
                            log=EventLog.WARNING)]
        arts = _arts(emit_cxl_events, recs)
        kinds = [k for a in arts for k in a if k != "testStepId"]
        assert "measurementSeriesStart" in kinds
        assert _diag(arts)["type"] == "PASS"

    def test_fatal_record_fails(self):
        recs = [EventRecord(handle=2, record_type=EventRecordType.DRAM,
                            log=EventLog.FATAL)]
        assert _diag(_arts(emit_cxl_events, recs))["type"] == "FAIL"

    def test_failure_record_fails(self):
        # A FAILURE-severity uncorrectable media event (mirrors corpus log=2)
        # must fail even when mixed with an INFORMATIONAL record.
        recs = [EventRecord(handle=3, record_type=EventRecordType.GENERAL_MEDIA,
                            log=EventLog.INFORMATIONAL),
                EventRecord(handle=4, record_type=EventRecordType.GENERAL_MEDIA,
                            log=EventLog.FAILURE)]
        assert _diag(_arts(emit_cxl_events, recs))["type"] == "FAIL"
