# OCP ocp-diag-core emitter API reference

Module: `computetest.io.ocpdiag`

For task-oriented usage see [howto/emit-ocp-diag.md](../howto/emit-ocp-diag.md).
This page is the dry reference.

## Constants

| Symbol | Value | Notes |
|--------|-------|-------|
| `SCHEMA_VERSION` | `{"major": 2, "minor": 0}` | The major/minor the emitter pins. |
| `EQUAL`, `NOT_EQUAL` | `"EQUAL"`, `"NOT_EQUAL"` | Validator type strings. |
| `LESS_THAN`, `LESS_THAN_OR_EQUAL` | — | — |
| `GREATER_THAN`, `GREATER_THAN_OR_EQUAL` | — | — |
| `REGEX_MATCH`, `REGEX_NO_MATCH` | — | — |
| `IN_SET`, `NOT_IN_SET` | — | — |

## Helpers

### `validator(type_, value, *, name="") -> dict`

Build a `Validator` object. Used to attach pass criteria to a measurement.

```python
ocpdiag.validator(ocpdiag.LESS_THAN_OR_EQUAL, 1e-12, name="target_ber")
# -> {"type": "LESS_THAN_OR_EQUAL", "value": 1e-12, "name": "target_ber"}
```

Raises `ValueError` if `value` is not a scalar (string/bool/number).

### `open_run(stream, *, program_version, command_line, parameters, dut_serial="UNKNOWN", station="station-1", hardware_infos=None, clock=_now_iso) -> Emitter`

Convenience: emit `schemaVersion` + `testRunStart` and return a ready-to-use
`Emitter`. Caller still owns `em.run_end(status)`.

## `Emitter`

```python
em = ocpdiag.Emitter(stream, *, clock=_now_iso)
```

Owns the sequence number, the timestamp clock, and an open-step stack so
callers can omit `step_id` for the most recently opened step.

### Run lifecycle

| Method | Returns | Notes |
|--------|---------|-------|
| `schema_version()` | seq | Always the first call. |
| `run_start(*, name, version, command_line, parameters, dut_info)` | seq | All five fields required by spec. |
| `run_end(status)` | seq | `status` in `pass`/`fail`/* maps to `PASS`/`FAIL`/`NOT_APPLICABLE`. `testStatus` is always `COMPLETE`. |
| `run_log(severity, message)` | seq | Run-scoped log. `severity` in `INFO`/`DEBUG`/`WARNING`/`ERROR`/`FATAL`. |
| `run_error(symptom, message="")` | seq | Run-scoped error. |

### Step lifecycle

| Method | Returns | Notes |
|--------|---------|-------|
| `step_start(name, *, step_id=None)` | str (step_id) | Push the new step onto the open-step stack. |
| `step_end(status, *, step_id=None)` | seq | `status` maps `pass`/`fail` → `COMPLETE`, `skip`/`unavailable`/`incomplete` → `SKIP`, else `ERROR`. Pops the stack. |

### Step body

All require an open step (default: top of stack). Each raises
`RuntimeError("no open step; ...")` if the stack is empty and no `step_id`
is passed.

| Method | Notes |
|--------|-------|
| `measurement(*, name, value, unit=None, validators=None, hardware_info_id=None, metadata=None, step_id=None)` | `value` must be string/bool/number — raises `ValueError` otherwise. |
| `series_start(*, name, unit=None, series_id=None, validators=None, hardware_info_id=None, metadata=None, step_id=None) -> str` | Returns the series_id; generate it yourself or let the emitter mint a UUID. |
| `series_element(*, series_id, index, value, metadata=None, step_id=None)` | `value` is the scalar reading; `index` is the position; `timestamp` is added automatically. |
| `series_end(*, series_id, total_count, step_id=None)` | Close a series. |
| `diagnosis(*, verdict, type_, message="", hardware_info_id=None, step_id=None)` | `type_` in `PASS`/`FAIL`/`UNKNOWN`. |
| `step_log(severity, message, *, step_id=None)` | |
| `step_error(symptom, message="", *, step_id=None)` | |

## Adapters

Each adapter wraps one `computetest` result type into a complete
`testStep` (start → measurements → diagnosis → end).

### `emit_bert(em, result, *, target_ber=None, hardware_info_id=None) -> str`

Emits a `BertResult` as one step. Measurements:

* `seconds` (s), `bits` (bit)
* `correctable_errors`, `uncorrectable_errors` (count)
* `link_speed_gen` (gen), `link_width` (lane)
* `ber_upper_bound` (errors_per_bit) — with a
  `LESS_THAN_OR_EQUAL target_ber` validator when `target_ber` is set
* `confidence_reached` (probability)
* `aer.correctable.<name>` and `aer.uncorrectable.<name>` — one per
  observed type
* `fec.pre_fec_symbol_errors`, `fec.post_fec_flit_errors`, `fec.fber_estimate`
  (Gen6+ only) — `fec.fber_estimate` carries a
  `LESS_THAN_OR_EQUAL 1e-6 fber_target` validator
* `fec.burst_len_<N>` (Gen6+ only) — one per burst-length bucket

Diagnosis: `pcie.bert.<status>` with type `PASS`/`FAIL`/`UNKNOWN`.

Returns the step_id.

### `emit_pcie_diagnostic(em, diag, *, hardware_info_id=None) -> str`

Wraps a `PcieDiagnostic`. Measurements:

* `link.speed_gen`, `link.width`, `link.retrains`, `link.bw_changed`
* `aer.correctable.<name>`, `aer.uncorrectable.<name>`

Plus a nested `emit_margin_series` when margining produced lanes, plus the
BERT diagnosis (nested), plus a final `pcie.<status>` diagnosis.

### `emit_chain(em, chain) -> str`

Wraps a `ChainDiagnostic`. Per-link `link.<name>.{speed_gen,width,bw_changed}`
measurements, per-segment AER measurements, kernel-event logs, BERT
diagnosis, chain diagnosis.

### `emit_health(em, h, *, label, hardware_info_id=None) -> str`

Generic Health adapter for NVMe / GPU / GMSL / Ethernet / CAN. Promotes
scalar fields (incl. one level of nested dict like `smart.<key>`) to
measurements. Diagnosis is `<label>.{pass|fail}`.

### `emit_test_record(em, record) -> str`

Wraps a `TestRecord` (plan output). Scalars in `record.measured` become
measurements, with one level of nested-dict flattening. Diagnosis is
`<subsystem>.<test_name>.<status>`.

### `emit_margin_series(em, margin, *, hardware_info_id=None) -> str | None`

Wraps a `MarginResult` as a `measurementSeries`. `name=pcie.margin.timing_ui`,
unit `UI`, with a `GREATER_THAN_OR_EQUAL min_timing_ui` validator from
`margin.limit_ui`. One element per lane (metadata `{lane: <N>}`). Returns
`None` if there are no lanes.

## Vendored schemas

`computetest/io/ocpdiag_schemas/` ships verbatim copies of the v2.0 JSON
schemas from
[opencomputeproject/ocp-diag-core](https://github.com/opencomputeproject/ocp-diag-core)
as of 2026-05-29. Refresh recipe is in `ocpdiag_schemas/README.md`.

The schemas are bundled as package data (`pyproject.toml`
`[tool.setuptools.package-data]`) so a `pip install` ships them.
`tests/test_ocpdiag.py::TestSchemaConformance::test_every_emitted_artifact_validates_against_root_schema`
validates every artifact the emitter produces against them — drift fails
the build.

## Status mapping

| `computetest` verdict | OCP `testStatus` | OCP `testResult` | Diagnosis `type` |
|----------------------|------------------|------------------|------------------|
| `pass` | `COMPLETE` | `PASS` | `PASS` |
| `fail` | `COMPLETE` | `FAIL` | `FAIL` |
| `skip`, `unavailable`, `incomplete` | `SKIP` | `NOT_APPLICABLE` | `UNKNOWN` |
| anything else (a test-program error) | `ERROR` | `NOT_APPLICABLE` | `UNKNOWN` |

## On-wire shape

Every artifact is one JSON object per line:

```json
{"sequenceNumber": 0, "timestamp": "2026-05-29T08:12:12.094Z", "schemaVersion": {"major": 2, "minor": 0}}
{"sequenceNumber": 1, "timestamp": "...", "testRunArtifact": {"testRunStart": {...}}}
{"sequenceNumber": 2, "timestamp": "...", "testStepArtifact": {"testStepId": "<uuid>", "testStepStart": {"name": "pcie.bert 0000:03:00.0"}}}
{"sequenceNumber": 3, "timestamp": "...", "testStepArtifact": {"testStepId": "<uuid>", "measurement": {"name": "ber_upper_bound", "value": 1.19e-12, "unit": "errors_per_bit", "validators": [{"type": "LESS_THAN_OR_EQUAL", "value": 1e-12, "name": "target_ber"}]}}}
...
{"sequenceNumber": N, "timestamp": "...", "testRunArtifact": {"testRunEnd": {"status": "COMPLETE", "result": "PASS"}}}
```
