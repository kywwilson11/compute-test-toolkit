"""
OCP ocp-diag-core JSON output (https://github.com/opencomputeproject/ocp-diag-core).

The spec defines a newline-delimited stream of *OutputArtifact* objects, each with
a monotonically-increasing ``sequenceNumber``, an RFC-3339 ``timestamp``, and
exactly one of:

* ``schemaVersion``   — always the first line, pins {major:2, minor:0}
* ``testRunArtifact`` — run lifecycle (start/end), or a run-scoped log/error
* ``testStepArtifact``— per-step lifecycle, measurement(Series), diagnosis,
                        log/error, file, extension

This module wraps `computetest`'s native result objects (BertResult,
PcieDiagnostic, ChainDiagnostic, the *Health dataclasses, TestRecord) into that
stream so the toolkit plugs into hyperscale MT pipelines that already consume
ocp-diag-core output (Google, Meta, OCP datacenter NVMe lane).

Why a separate module: the existing `--json` flag emits human-shaped JSON
(``BertResult.to_dict()`` etc.) for operator-friendly piping. ocp-diag-core is a
*portable schema* — a different audience and a different contract. Mixing the
two would constrain both.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any, TextIO

SCHEMA_VERSION = {"major": 2, "minor": 0}

# computetest internal verdict -> OCP enums.
# Run result: only PASS/FAIL/NOT_APPLICABLE per testRunEnd.testResult schema.
_RUN_RESULT = {"pass": "PASS", "fail": "FAIL"}                    # else NOT_APPLICABLE
# Step status: COMPLETE/ERROR/SKIP per testStatus schema. We treat skip/unavailable
# as SKIP (we couldn't measure) and reserve ERROR for an internal/test-program fault.
_STEP_STATUS = {"pass": "COMPLETE", "fail": "COMPLETE"}            # else SKIP
# Diagnosis type: PASS/FAIL/UNKNOWN per diagnosis.type schema.
_DIAGNOSIS_TYPE = {"pass": "PASS", "fail": "FAIL"}                 # else UNKNOWN

# Validator enum values (per validator.json) — re-exported for adapter callers.
EQUAL, NOT_EQUAL = "EQUAL", "NOT_EQUAL"
LESS_THAN, LESS_THAN_OR_EQUAL = "LESS_THAN", "LESS_THAN_OR_EQUAL"
GREATER_THAN, GREATER_THAN_OR_EQUAL = "GREATER_THAN", "GREATER_THAN_OR_EQUAL"
REGEX_MATCH, REGEX_NO_MATCH = "REGEX_MATCH", "REGEX_NO_MATCH"
IN_SET, NOT_IN_SET = "IN_SET", "NOT_IN_SET"


def _now_iso() -> str:
    """RFC-3339 / ISO-8601 UTC timestamp, millisecond precision, 'Z' suffix."""
    now = datetime.now(timezone.utc)
    return f"{now.strftime('%Y-%m-%dT%H:%M:%S')}.{now.microsecond // 1000:03d}Z"


def _scalar(v: Any) -> bool:
    """OCP measurement/measurementSeriesElement.value must be string|bool|number."""
    return isinstance(v, (str, bool, int, float))


def validator(type_: str, value: Any, *, name: str = "") -> dict:
    """Build a Validator object (validator.json). ``type_`` is one of the
    EQUAL / LESS_THAN / etc. enum constants exposed by this module."""
    if not _scalar(value):
        raise ValueError(f"validator value must be a scalar, got {type(value).__name__}")
    v: dict[str, Any] = {"type": type_, "value": value}
    if name:
        v["name"] = name
    return v


# ----------------------------------------------------------------------------
# Emitter
# ----------------------------------------------------------------------------
class Emitter:
    """Streams OCP ocp-diag-core OutputArtifact lines to a writer.

    ``sequenceNumber`` and ``timestamp`` are owned here; callers never touch them
    directly. ``step_start``/``step_end`` maintain an internal stack so callers
    can omit ``step_id`` for the most recently opened step.

    The writer must be a text-mode stream (``sys.stdout`` or a file opened with
    ``"w"``). One JSON object per ``flush``-safe line.
    """
    def __init__(self, stream: TextIO, *, clock: Callable[[], str] = _now_iso) -> None:
        self._stream = stream
        self._seq = 0
        self._clock = clock
        self._open_steps: list[str] = []

    # --- internal ----------------------------------------------------------
    def _write(self, payload: Mapping[str, Any]) -> int:
        """Wrap ``payload`` with sequenceNumber + timestamp and write one line."""
        seq = self._seq
        self._seq += 1
        obj = {"sequenceNumber": seq, "timestamp": self._clock(), **payload}
        self._stream.write(json.dumps(obj, default=str) + "\n")
        return seq

    def _step_id(self, step_id: str | None) -> str:
        if step_id is not None:
            return step_id
        if not self._open_steps:
            raise RuntimeError("no open step; pass step_id= or call step_start() first")
        return self._open_steps[-1]

    # --- run lifecycle -----------------------------------------------------
    def schema_version(self) -> int:
        return self._write({"schemaVersion": SCHEMA_VERSION})

    def run_start(self, *, name: str, version: str, command_line: str,
                  parameters: Mapping[str, Any],
                  dut_info: Mapping[str, Any]) -> int:
        return self._write({"testRunArtifact": {"testRunStart": {
            "name": name, "version": version, "commandLine": command_line,
            "parameters": dict(parameters), "dutInfo": dict(dut_info),
        }}})

    def run_end(self, status: str) -> int:
        return self._write({"testRunArtifact": {"testRunEnd": {
            "status": "COMPLETE",
            "result": _RUN_RESULT.get(status, "NOT_APPLICABLE"),
        }}})

    def run_log(self, severity: str, message: str) -> int:
        return self._write({"testRunArtifact": {"log": {
            "severity": severity.upper(), "message": message,
        }}})

    def run_error(self, symptom: str, message: str = "") -> int:
        err: dict[str, Any] = {"symptom": symptom}
        if message:
            err["message"] = message
        return self._write({"testRunArtifact": {"error": err}})

    # --- step lifecycle ----------------------------------------------------
    def step_start(self, name: str, *, step_id: str | None = None) -> str:
        sid = step_id or str(uuid.uuid4())
        self._open_steps.append(sid)
        self._write({"testStepArtifact": {
            "testStepId": sid, "testStepStart": {"name": name}}})
        return sid

    def step_end(self, status: str, *, step_id: str | None = None) -> int:
        sid = self._step_id(step_id)
        try:
            self._open_steps.remove(sid)
        except ValueError:
            pass
        ocp = _STEP_STATUS.get(status, "SKIP" if status in ("skip", "unavailable",
                                                              "incomplete") else "ERROR")
        return self._write({"testStepArtifact": {
            "testStepId": sid, "testStepEnd": {"status": ocp}}})

    # --- step body (measurement / diagnosis / log / error / series / file) -
    def measurement(self, *, name: str, value: Any, unit: str | None = None,
                    validators: list[dict] | None = None,
                    hardware_info_id: str | None = None,
                    metadata: Mapping[str, Any] | None = None,
                    step_id: str | None = None) -> int:
        if not _scalar(value):
            raise ValueError(f"measurement {name!r} value must be string|bool|number, "
                             f"got {type(value).__name__}")
        m: dict[str, Any] = {"name": name, "value": value}
        if unit:
            m["unit"] = unit
        if validators:
            m["validators"] = list(validators)
        if hardware_info_id:
            m["hardwareInfoId"] = hardware_info_id
        if metadata:
            m["metadata"] = dict(metadata)
        return self._write({"testStepArtifact": {
            "testStepId": self._step_id(step_id), "measurement": m}})

    def series_start(self, *, name: str, unit: str | None = None,
                     series_id: str | None = None,
                     validators: list[dict] | None = None,
                     hardware_info_id: str | None = None,
                     metadata: Mapping[str, Any] | None = None,
                     step_id: str | None = None) -> str:
        sid = series_id or str(uuid.uuid4())
        s: dict[str, Any] = {"name": name, "measurementSeriesId": sid}
        if unit:
            s["unit"] = unit
        if validators:
            s["validators"] = list(validators)
        if hardware_info_id:
            s["hardwareInfoId"] = hardware_info_id
        if metadata:
            s["metadata"] = dict(metadata)
        self._write({"testStepArtifact": {
            "testStepId": self._step_id(step_id), "measurementSeriesStart": s}})
        return sid

    def series_element(self, *, series_id: str, index: int, value: Any,
                       metadata: Mapping[str, Any] | None = None,
                       step_id: str | None = None) -> int:
        if not _scalar(value):
            raise ValueError(f"series element value must be string|bool|number, "
                             f"got {type(value).__name__}")
        e: dict[str, Any] = {
            "index": index, "measurementSeriesId": series_id,
            "value": value, "timestamp": self._clock(),
        }
        if metadata:
            e["metadata"] = dict(metadata)
        return self._write({"testStepArtifact": {
            "testStepId": self._step_id(step_id), "measurementSeriesElement": e}})

    def series_end(self, *, series_id: str, total_count: int,
                   step_id: str | None = None) -> int:
        return self._write({"testStepArtifact": {
            "testStepId": self._step_id(step_id),
            "measurementSeriesEnd": {
                "measurementSeriesId": series_id, "totalCount": total_count,
            }}})

    def diagnosis(self, *, verdict: str, type_: str, message: str = "",
                  hardware_info_id: str | None = None,
                  step_id: str | None = None) -> int:
        d: dict[str, Any] = {"verdict": verdict, "type": type_}
        if message:
            d["message"] = message
        if hardware_info_id:
            d["hardwareInfoId"] = hardware_info_id
        return self._write({"testStepArtifact": {
            "testStepId": self._step_id(step_id), "diagnosis": d}})

    def step_log(self, severity: str, message: str, *,
                 step_id: str | None = None) -> int:
        return self._write({"testStepArtifact": {
            "testStepId": self._step_id(step_id),
            "log": {"severity": severity.upper(), "message": message}}})

    def step_error(self, symptom: str, message: str = "", *,
                   step_id: str | None = None) -> int:
        err: dict[str, Any] = {"symptom": symptom}
        if message:
            err["message"] = message
        return self._write({"testStepArtifact": {
            "testStepId": self._step_id(step_id), "error": err}})


# ----------------------------------------------------------------------------
# Result-type adapters
# ----------------------------------------------------------------------------
def _diag_type(status: str) -> str:
    return _DIAGNOSIS_TYPE.get(status, "UNKNOWN")


def emit_bert(em: Emitter, result, *, target_ber: float | None = None,
              hardware_info_id: str | None = None) -> str:
    """Emit a ``BertResult`` as one testStep with measurements + diagnosis."""
    sid = em.step_start(f"pcie.bert {result.bdf}")
    em.measurement(name="seconds", value=float(result.seconds), unit="s")
    em.measurement(name="bits", value=float(result.bits), unit="bit")
    em.measurement(name="correctable_errors", value=int(result.correctable),
                   unit="count")
    em.measurement(name="uncorrectable_errors", value=int(result.uncorrectable),
                   unit="count")
    em.measurement(name="link_speed_gen", value=int(result.link_speed_code),
                   unit="gen", hardware_info_id=hardware_info_id)
    em.measurement(name="link_width", value=int(result.link_width),
                   unit="lane", hardware_info_id=hardware_info_id)
    # BER upper bound with the target as a validator: BER must be <= target.
    vlist = ([validator(LESS_THAN_OR_EQUAL, float(target_ber), name="target_ber")]
             if target_ber is not None else None)
    em.measurement(name="ber_upper_bound", value=float(result.verdict.ber_upper),
                   unit="errors_per_bit", validators=vlist)
    em.measurement(name="confidence_reached",
                   value=round(float(result.verdict.confidence_reached), 4),
                   unit="probability")
    # Per-correctable distribution and per-uncorrectable decode (one measurement each).
    for nm, count in (result.per_correctable or {}).items():
        em.measurement(name=f"aer.correctable.{nm}", value=int(count), unit="count")
    for nm in (result.uncorrectable_decode or []):
        em.measurement(name=f"aer.uncorrectable.{nm}", value=1, unit="count")
    # PCIe 6.0+ FEC counters (None on Gen<=5; the real reliability signal on Gen6+).
    if result.pre_fec_symbol_errors is not None:
        em.measurement(name="fec.pre_fec_symbol_errors",
                       value=int(result.pre_fec_symbol_errors), unit="count")
        em.measurement(name="fec.post_fec_flit_errors",
                       value=int(result.post_fec_flit_errors or 0), unit="count")
        em.measurement(name="fec.fber_estimate",
                       value=float(result.fber_estimate or 0.0),
                       unit="errors_per_flit",
                       validators=[validator(LESS_THAN_OR_EQUAL, 1e-6,
                                              name="fber_target")])
        for length, count in (result.burst_length_histogram or {}).items():
            em.measurement(name=f"fec.burst_len_{length}", value=int(count),
                           unit="count")
    if result.note:
        em.step_log("WARNING", result.note)
    if result.stuck:
        em.step_log("ERROR", "constant fault: errors latched at IDLE (no traffic)")
    em.diagnosis(verdict=f"pcie.bert.{result.status}", type_=_diag_type(result.status),
                 message=result.verdict.summary(), hardware_info_id=hardware_info_id)
    em.step_end(result.status, step_id=sid)
    return sid


def emit_margin_series(em: Emitter, margin, *,
                       hardware_info_id: str | None = None) -> str | None:
    """Emit a per-lane margining result as a measurementSeries. Returns the
    series id (or ``None`` if there are no lanes to emit)."""
    if not margin.lanes:
        return None
    vlist = ([validator(GREATER_THAN_OR_EQUAL, float(margin.limit_ui),
                         name="min_timing_ui")]
             if margin.limit_ui is not None else None)
    sid = em.series_start(name="pcie.margin.timing_ui", unit="UI",
                          validators=vlist, hardware_info_id=hardware_info_id)
    for idx, lane in enumerate(margin.lanes):
        em.series_element(series_id=sid, index=idx, value=float(lane.timing_ui),
                          metadata={"lane": int(lane.lane)})
    em.series_end(series_id=sid, total_count=len(margin.lanes))
    return sid


def emit_pcie_diagnostic(em: Emitter, diag,
                         *, hardware_info_id: str | None = None) -> str:
    """Emit a ``PcieDiagnostic`` as one testStep with link/AER measurements,
    nested BERT/margin series, and a final diagnosis."""
    sid = em.step_start(f"pcie.diagnose {diag.bdf}")
    link = diag.link
    em.measurement(name="link.speed_gen", value=int(link.speed), unit="gen",
                   hardware_info_id=hardware_info_id)
    em.measurement(name="link.width", value=int(link.width), unit="lane",
                   hardware_info_id=hardware_info_id)
    em.measurement(name="link.retrains", value=int(link.retrains), unit="count")
    em.measurement(name="link.bw_changed", value=bool(link.bw_changed))
    if link.speed_degraded:
        em.step_log("ERROR", f"speed degraded: Gen{link.speed}")
    if link.width_degraded:
        em.step_log("ERROR", f"width degraded: x{link.width}")
    for _, nm, _ in diag.aer_snapshot.correctable:
        em.measurement(name=f"aer.correctable.{nm}", value=1, unit="count")
    for _, nm, _ in diag.aer_snapshot.uncorrectable:
        em.measurement(name=f"aer.uncorrectable.{nm}", value=1, unit="count")
    if diag.margin and diag.margin.lanes:
        emit_margin_series(em, diag.margin, hardware_info_id=hardware_info_id)
    elif diag.margin and diag.margin.note:
        em.step_log("WARNING", f"margining unavailable: {diag.margin.note}")
    if diag.bert is not None:
        # The BERT verdict is informational here — the outer diagnosis is the verdict.
        em.diagnosis(verdict=f"pcie.bert.{diag.bert.status}",
                     type_=_diag_type(diag.bert.status),
                     message=diag.bert.verdict.summary(),
                     hardware_info_id=hardware_info_id)
    reasons = diag.reasons()
    em.diagnosis(verdict=f"pcie.{diag.status}", type_=_diag_type(diag.status),
                 message="; ".join(reasons) if reasons else diag.summary(),
                 hardware_info_id=hardware_info_id)
    em.step_end(diag.status, step_id=sid)
    return sid


def emit_chain(em: Emitter, chain) -> str:
    """Emit a ``ChainDiagnostic`` as one testStep summarizing every link in
    an endpoint's path."""
    sid = em.step_start(f"pcie.chain {chain.endpoint}")
    # Per-link downgrade results as measurements; per-segment AER as measurements.
    for li in chain.links:
        em.measurement(name=f"link.{li.name}.speed_gen", value=int(li.speed),
                       unit="gen", metadata={"downstream_bdf": li.downstream_bdf})
        em.measurement(name=f"link.{li.name}.width", value=int(li.width),
                       unit="lane", metadata={"downstream_bdf": li.downstream_bdf})
        em.measurement(name=f"link.{li.name}.bw_changed",
                       value=bool(li.bw_changed),
                       metadata={"downstream_bdf": li.downstream_bdf})
    for seg in chain.segments:
        for nm in seg.correctable_types:
            em.measurement(name=f"aer.{seg.direction}.correctable.{nm}",
                           value=1, unit="count")
        for nm in seg.uncorrectable_types:
            em.measurement(name=f"aer.{seg.direction}.uncorrectable.{nm}",
                           value=1, unit="count")
    for ev in chain.kernel_events:
        em.step_log("ERROR" if ev.uncorrectable else "WARNING",
                    f"dmesg: {ev.text}")
    em.diagnosis(verdict=f"pcie.chain.bert.{chain.bert.status}",
                 type_=_diag_type(chain.bert.status),
                 message=chain.bert.verdict.summary())
    reasons = chain.reasons()
    em.diagnosis(verdict=f"pcie.chain.{chain.status}",
                 type_=_diag_type(chain.status),
                 message="; ".join(reasons) if reasons else chain.summary())
    em.step_end(chain.status, step_id=sid)
    return sid


def emit_health(em: Emitter, h, *, label: str,
                hardware_info_id: str | None = None) -> str:
    """Emit a generic Health-shaped result (NVMe/GPU/GMSL/Ethernet/CAN).

    Promotes scalar (bool/int/float) fields from ``h.to_dict()`` (incl. nested
    one-level dicts like ``smart``/``checks``) to measurements; emits a final
    diagnosis from ``h.ok`` + ``h.summary()``.
    """
    target = (getattr(h, "device", None) or getattr(h, "iface", None)
              or getattr(h, "link", None) or "")
    sid = em.step_start(f"{label} {target}".rstrip())
    for k, v in h.to_dict().items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, (bool, int, float, str)):
                    em.measurement(name=f"{k}.{kk}", value=vv,
                                   hardware_info_id=hardware_info_id)
        elif isinstance(v, (bool, int, float, str)):
            em.measurement(name=k, value=v, hardware_info_id=hardware_info_id)
    ok = bool(getattr(h, "ok", False))
    status = "pass" if ok else "fail"
    em.diagnosis(verdict=f"{label}.{status}", type_=_diag_type(status),
                 message=h.summary(), hardware_info_id=hardware_info_id)
    em.step_end(status, step_id=sid)
    return sid


def emit_test_record(em: Emitter, record) -> str:
    """Emit a ``TestRecord`` (plan output) as one testStep. ``record.measured``
    scalars are surfaced as measurements; nested dict scalars get one level of
    key-flattening."""
    sid = em.step_start(f"{record.subsystem}.{record.test_name} {record.target}")
    for k, v in (record.measured or {}).items():
        if isinstance(v, dict):
            for kk, vv in v.items():
                if isinstance(vv, (bool, int, float, str)):
                    em.measurement(name=f"{k}.{kk}", value=vv)
        elif isinstance(v, (bool, int, float, str)):
            em.measurement(name=k, value=v)
    if record.message:
        em.step_log("INFO", record.message)
    em.diagnosis(verdict=f"{record.subsystem}.{record.test_name}.{record.status}",
                 type_=_diag_type(record.status), message=record.message)
    em.step_end(record.status, step_id=sid)
    return sid


# ----------------------------------------------------------------------------
# One-shot helpers
# ----------------------------------------------------------------------------
def open_run(stream: TextIO, *, program_version: str, command_line: str,
             parameters: Mapping[str, Any],
             dut_serial: str = "UNKNOWN", station: str = "station-1",
             hardware_infos: list[Mapping[str, Any]] | None = None,
             clock: Callable[[], str] = _now_iso) -> Emitter:
    """Build an ``Emitter``, write schemaVersion + testRunStart, return the emitter.

    The caller is responsible for ``em.run_end(status)``.
    """
    em = Emitter(stream, clock=clock)
    em.schema_version()
    em.run_start(
        name="computetest", version=program_version, command_line=command_line,
        parameters=dict(parameters),
        dut_info={
            "dutInfoId": dut_serial,
            "name": dut_serial,
            "platformInfos": [{"info": f"station={station}"}],
            "softwareInfos": [{
                "name": "computetest", "version": program_version,
                "softwareInfoId": "computetest",
                "softwareType": "APPLICATION",
            }],
            "hardwareInfos": list(hardware_infos or []),
        },
    )
    return em
