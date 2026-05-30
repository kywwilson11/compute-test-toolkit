"""
ocp-diag projection for CXL results (Sprint 4.4).

Projects the CXL conformance/RAS results onto the schema-v2 ocp-diag emitter
(``io/ocpdiag.py``): the link state and RAS decode as a testStep with
measurements + a diagnosis, and the event records as a measurementSeries — so
CXL results flow into the same hyperscale MT pipeline as the PCIe/NVMe results.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..io.ocpdiag import Emitter
from .events import EventRecord
from .link import CxlLinkHealth
from .ras import CxlRasSnapshot


def emit_cxl_link(em: Emitter, health: CxlLinkHealth, *,
                  hardware_info_id: str | None = None) -> str:
    """Emit a CXL link verdict as a testStep with speed/width/flit measurements."""
    sid = em.step_start("cxl.link")
    em.measurement(name="speed_gt", value=int(health.speed_gt), unit="GT/s",
                   hardware_info_id=hardware_info_id)
    em.measurement(name="width", value=int(health.width), unit="lane",
                   hardware_info_id=hardware_info_id)
    em.measurement(name="flit_mode", value=health.flit_mode)
    em.measurement(name="cxl_negotiated", value=bool(health.cxl_negotiated))
    status = "pass" if health.ok else "fail"
    em.diagnosis(verdict=f"cxl.link.{status}",
                 type_="PASS" if health.ok else "FAIL", message=health.summary())
    em.step_end(status, step_id=sid)
    return sid


def emit_cxl_ras(em: Emitter, snapshot: CxlRasSnapshot, *,
                 hardware_info_id: str | None = None) -> str:
    """Emit a CXL RAS snapshot: the raw status words + one measurement per
    decoded UE/CE bit, then a diagnosis (any uncorrectable -> fail)."""
    sid = em.step_start("cxl.ras")
    em.measurement(name="ue_status", value=int(snapshot.ue_raw), unit="bitmask",
                   hardware_info_id=hardware_info_id)
    em.measurement(name="ce_status", value=int(snapshot.ce_raw), unit="bitmask")
    for _, name, _ in snapshot.uncorrectable:
        em.measurement(name=f"cxl.ue.{name}", value=1, unit="count")
    for _, name, _ in snapshot.correctable:
        em.measurement(name=f"cxl.ce.{name}", value=1, unit="count")
    status = "fail" if snapshot.has_uncorrectable else "pass"
    msg = ("uncorrectable: " + ",".join(n for _, n, _ in snapshot.uncorrectable)
           if snapshot.has_uncorrectable else "no uncorrectable errors")
    em.diagnosis(verdict=f"cxl.ras.{status}",
                 type_="FAIL" if snapshot.has_uncorrectable else "PASS",
                 message=msg, hardware_info_id=hardware_info_id)
    em.step_end(status, step_id=sid)
    return sid


def emit_cxl_events(em: Emitter, records: Sequence[EventRecord], *,
                    hardware_info_id: str | None = None) -> str:
    """Emit CXL event records as a measurementSeries of handles (type + log in
    each element's metadata)."""
    sid = em.step_start("cxl.events")
    series = em.series_start(name="cxl.event.handle", unit="handle",
                             hardware_info_id=hardware_info_id)
    for i, rec in enumerate(records):
        em.series_element(series_id=series, index=i, value=int(rec.handle),
                          metadata={"type": rec.record_type.value,
                                    "log": int(rec.log)})
    em.series_end(series_id=series, total_count=len(records))
    em.diagnosis(verdict="cxl.events", type_="PASS",
                 message=f"{len(records)} event records")
    em.step_end("pass", step_id=sid)
    return sid
