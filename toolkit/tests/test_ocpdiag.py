"""
ocp-diag-core JSON emitter (computetest.io.ocpdiag): structural conformance to
the OCP spec (https://github.com/opencomputeproject/ocp-diag-core/tree/main/json_spec/output)
and the result-type adapters that wrap computetest result objects.

Tests in this file MUST be strict — they're what stops schema drift from sliding
through unnoticed and breaking downstream OCP consumers.
"""
from __future__ import annotations

import io
import json
import os
import re

import pytest

from computetest.io import ocpdiag as oc

os.environ.setdefault("COMPUTETEST_BACKEND", "mock")

# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)
RUN_VARIANTS = {"testRunStart", "testRunEnd", "log", "error"}
STEP_VARIANTS = {
    "testStepStart", "testStepEnd", "measurement",
    "measurementSeriesStart", "measurementSeriesElement", "measurementSeriesEnd",
    "diagnosis", "log", "error", "file", "extension",
}


def _emit_lines(buf: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def _assert_artifact_shape(artifact: dict) -> str:
    """Assert one OutputArtifact has exactly the required keys + one variant.
    Returns the variant kind ('schemaVersion' / 'testRunArtifact' / 'testStepArtifact')."""
    assert isinstance(artifact["sequenceNumber"], int)
    assert artifact["sequenceNumber"] >= 0
    assert RFC3339_RE.match(artifact["timestamp"]), (
        f"timestamp {artifact['timestamp']!r} is not RFC-3339 with ms precision")
    kinds = set(artifact.keys()) - {"sequenceNumber", "timestamp"}
    assert len(kinds) == 1, f"expected exactly one variant key, got {kinds}"
    kind = next(iter(kinds))
    assert kind in {"schemaVersion", "testRunArtifact", "testStepArtifact"}, kind
    return kind


# ----------------------------------------------------------------------------
# Emitter: low-level invariants
# ----------------------------------------------------------------------------
class TestEmitterCore:
    def test_first_line_is_schema_version_two_zero(self):
        buf = io.StringIO()
        em = oc.Emitter(buf, clock=lambda: "2026-05-29T00:00:00.000Z")
        em.schema_version()
        line = json.loads(buf.getvalue())
        assert line["sequenceNumber"] == 0
        assert line["schemaVersion"] == {"major": 2, "minor": 0}

    def test_sequence_numbers_are_monotonic_starting_at_zero(self):
        buf = io.StringIO()
        em = oc.Emitter(buf, clock=lambda: "2026-05-29T00:00:00.000Z")
        em.schema_version()
        em.run_start(name="t", version="0", command_line="t",
                     parameters={}, dut_info={"dutInfoId": "D"})
        em.run_end("pass")
        seqs = [a["sequenceNumber"] for a in _emit_lines(buf)]
        assert seqs == [0, 1, 2]

    def test_every_artifact_has_required_top_level_fields(self):
        buf = io.StringIO()
        em = oc.open_run(buf, program_version="0.1", command_line="t",
                         parameters={}, dut_serial="D", station="S",
                         clock=lambda: "2026-05-29T00:00:00.000Z")
        sid = em.step_start("noop")
        em.measurement(name="x", value=1)
        em.diagnosis(verdict="t.pass", type_="PASS")
        em.step_end("pass", step_id=sid)
        em.run_end("pass")
        for a in _emit_lines(buf):
            _assert_artifact_shape(a)

    def test_timestamp_format_is_rfc3339_with_ms(self):
        buf = io.StringIO()
        em = oc.Emitter(buf)
        em.schema_version()                                # uses real clock
        ts = json.loads(buf.getvalue())["timestamp"]
        assert RFC3339_RE.match(ts), f"bad timestamp: {ts!r}"

    def test_step_id_stack_pop_on_end_and_default_to_top(self):
        buf = io.StringIO()
        em = oc.Emitter(buf, clock=lambda: "t")
        s1 = em.step_start("outer")
        em.measurement(name="x", value=1)                  # no explicit step_id
        last = json.loads(buf.getvalue().splitlines()[-1])
        assert last["testStepArtifact"]["testStepId"] == s1
        em.step_end("pass")                                # pops without explicit id
        with pytest.raises(RuntimeError, match="no open step"):
            em.measurement(name="y", value=1)              # stack empty

    def test_measurement_rejects_non_scalar_value(self):
        em = oc.Emitter(io.StringIO(), clock=lambda: "t")
        em.step_start("x")
        with pytest.raises(ValueError, match="must be string\\|bool\\|number"):
            em.measurement(name="bad", value={"nested": "dict"})

    def test_series_element_rejects_non_scalar_value(self):
        em = oc.Emitter(io.StringIO(), clock=lambda: "t")
        em.step_start("x")
        sid = em.series_start(name="s")
        with pytest.raises(ValueError, match="must be string\\|bool\\|number"):
            em.series_element(series_id=sid, index=0, value=[1, 2, 3])


# ----------------------------------------------------------------------------
# Emitter: lifecycle field validation
# ----------------------------------------------------------------------------
class TestRunArtifactFields:
    def _run_artifact(self, fn) -> dict:
        buf = io.StringIO()
        em = oc.Emitter(buf, clock=lambda: "2026-05-29T00:00:00.000Z")
        fn(em)
        return json.loads(buf.getvalue().splitlines()[0])["testRunArtifact"]

    def test_run_start_has_all_required_fields(self):
        art = self._run_artifact(lambda em: em.run_start(
            name="computetest", version="0.2.0",
            command_line="computetest bert -d 0000:03:00.0",
            parameters={"bdf": "0000:03:00.0", "target_ber": 1e-12},
            dut_info={"dutInfoId": "SN1", "name": "SN1"},
        ))
        start = art["testRunStart"]
        for key in ("name", "version", "commandLine", "parameters", "dutInfo"):
            assert key in start, f"testRunStart missing required {key!r}"
        assert start["dutInfo"]["dutInfoId"] == "SN1"

    @pytest.mark.parametrize("status, expected_result", [
        ("pass", "PASS"), ("fail", "FAIL"),
        ("skip", "NOT_APPLICABLE"), ("unknown", "NOT_APPLICABLE"),
    ])
    def test_run_end_status_mapping(self, status, expected_result):
        art = self._run_artifact(lambda em: em.run_end(status))
        end = art["testRunEnd"]
        assert end["status"] == "COMPLETE"                 # testStatus
        assert end["result"] == expected_result            # testResult


class TestStepArtifactFields:
    def _step_lines(self, fn) -> list[dict]:
        buf = io.StringIO()
        em = oc.Emitter(buf, clock=lambda: "2026-05-29T00:00:00.000Z")
        fn(em)
        return [json.loads(l)["testStepArtifact"]
                for l in buf.getvalue().splitlines() if "testStepArtifact" in l]

    def test_every_step_artifact_carries_test_step_id(self):
        def f(em):
            sid = em.step_start("inner")
            em.measurement(name="x", value=1)
            em.diagnosis(verdict="x.pass", type_="PASS")
            em.step_end("pass", step_id=sid)
        for a in self._step_lines(f):
            assert "testStepId" in a, a

    @pytest.mark.parametrize("status, expected_step_status", [
        ("pass", "COMPLETE"), ("fail", "COMPLETE"),
        ("skip", "SKIP"), ("unavailable", "SKIP"), ("incomplete", "SKIP"),
        ("error", "ERROR"),
    ])
    def test_step_end_status_mapping(self, status, expected_step_status):
        def f(em):
            sid = em.step_start("s")
            em.step_end(status, step_id=sid)
        ends = [a["testStepEnd"]["status"] for a in self._step_lines(f)
                if "testStepEnd" in a]
        assert ends == [expected_step_status]

    def test_measurement_validator_round_trips_correctly(self):
        def f(em):
            sid = em.step_start("m")
            em.measurement(
                name="ber_upper_bound", value=1e-13, unit="errors_per_bit",
                validators=[oc.validator(oc.LESS_THAN_OR_EQUAL, 1e-12,
                                          name="target_ber")])
            em.step_end("pass", step_id=sid)
        m = next(a["measurement"] for a in self._step_lines(f)
                 if "measurement" in a)
        assert m["name"] == "ber_upper_bound"
        assert m["unit"] == "errors_per_bit"
        v = m["validators"][0]
        assert v == {"type": "LESS_THAN_OR_EQUAL", "value": 1e-12,
                     "name": "target_ber"}

    def test_diagnosis_type_enum_values(self):
        def f(em):
            sid = em.step_start("d")
            em.diagnosis(verdict="ok", type_="PASS")
            em.diagnosis(verdict="bad", type_="FAIL")
            em.diagnosis(verdict="?",   type_="UNKNOWN")
            em.step_end("pass", step_id=sid)
        types = [a["diagnosis"]["type"] for a in self._step_lines(f)
                 if "diagnosis" in a]
        assert types == ["PASS", "FAIL", "UNKNOWN"]

    def test_measurement_series_lifecycle(self):
        def f(em):
            em.step_start("series")
            sid = em.series_start(name="s", unit="UI")
            em.series_element(series_id=sid, index=0, value=0.5)
            em.series_element(series_id=sid, index=1, value=0.4)
            em.series_end(series_id=sid, total_count=2)
            em.step_end("pass")
        arts = self._step_lines(f)
        events = []
        for a in arts:
            for k in ("measurementSeriesStart", "measurementSeriesElement",
                      "measurementSeriesEnd"):
                if k in a:
                    events.append((k, a[k]))
        kinds = [k for k, _ in events]
        assert kinds == ["measurementSeriesStart",
                          "measurementSeriesElement",
                          "measurementSeriesElement",
                          "measurementSeriesEnd"]
        # Every element references the same series id and is indexed 0,1,...
        ids = {p["measurementSeriesId"] for _, p in events}
        assert len(ids) == 1
        elements = [p for k, p in events if k == "measurementSeriesElement"]
        assert [e["index"] for e in elements] == [0, 1]
        end = next(p for k, p in events if k == "measurementSeriesEnd")
        assert end["totalCount"] == 2


# ----------------------------------------------------------------------------
# Adapters: result-type → ocp-diag artifacts
# ----------------------------------------------------------------------------
def _run() -> tuple[io.StringIO, oc.Emitter]:
    buf = io.StringIO()
    em = oc.open_run(buf, program_version="0.2.0",
                      command_line="computetest test", parameters={},
                      dut_serial="SN-T", station="S-T",
                      clock=lambda: "2026-05-29T00:00:00.000Z")
    return buf, em


class TestEmitBert:
    def test_emits_step_with_measurements_and_diagnosis(self):
        from computetest.backend import select_backend
        from computetest.bert import run_bert
        buf, em = _run()
        backend = select_backend()
        r = run_bert(backend, "0000:03:00.0", target_ber=1e-12, max_seconds=1.0)
        oc.emit_bert(em, r, target_ber=1e-12)
        em.run_end(r.status)
        arts = _emit_lines(buf)
        # 0: schemaVersion, 1: testRunStart, ..., last: testRunEnd
        assert "schemaVersion" in arts[0]
        assert "testRunStart" in arts[1]["testRunArtifact"]
        assert "testRunEnd" in arts[-1]["testRunArtifact"]
        # Steps between run_start and run_end carry testStepId
        step_arts = [a["testStepArtifact"] for a in arts
                      if "testStepArtifact" in a]
        ids = {a["testStepId"] for a in step_arts}
        assert len(ids) == 1
        kinds = [next(iter(set(a.keys()) - {"testStepId"})) for a in step_arts]
        assert kinds[0] == "testStepStart"
        assert kinds[-1] == "testStepEnd"
        assert "diagnosis" in kinds                        # final verdict emitted
        assert "measurement" in kinds                      # BER + counts emitted
        # The diagnosis type is PASS/FAIL/UNKNOWN per spec.
        for a in step_arts:
            if "diagnosis" in a:
                assert a["diagnosis"]["type"] in {"PASS", "FAIL", "UNKNOWN"}

    def test_ber_upper_bound_has_validator_with_target_ber(self):
        from computetest.backend import select_backend
        from computetest.bert import run_bert
        buf, em = _run()
        backend = select_backend()
        r = run_bert(backend, "0000:03:00.0", target_ber=1e-12, max_seconds=1.0)
        oc.emit_bert(em, r, target_ber=1e-12)
        em.run_end(r.status)
        measurements = [
            a["testStepArtifact"]["measurement"]
            for a in _emit_lines(buf)
            if "testStepArtifact" in a and "measurement" in a["testStepArtifact"]
        ]
        ber = next(m for m in measurements if m["name"] == "ber_upper_bound")
        assert ber["unit"] == "errors_per_bit"
        assert ber["validators"] == [{
            "type": "LESS_THAN_OR_EQUAL", "value": 1e-12, "name": "target_ber",
        }]


class TestEmitHealth:
    def test_nvme_health_promotes_smart_scalars_to_measurements(self):
        from computetest.nvme import check_nvme
        buf, em = _run()
        h = check_nvme("/dev/nvme0", mock=True)
        oc.emit_health(em, h, label="nvme")
        em.run_end("pass" if h.ok else "fail")
        arts = _emit_lines(buf)
        names = [a["testStepArtifact"]["measurement"]["name"]
                 for a in arts
                 if "testStepArtifact" in a
                 and "measurement" in a["testStepArtifact"]]
        # SMART scalars get one level of dict-key flattening.
        assert "smart.temperature" in names
        assert "smart.media_errors" in names
        assert "smart.power_on_hours" in names
        # bool checks (one per pass/fail gate) survive the bool-int separation.
        check_names = [n for n in names if n.startswith("checks.")]
        assert check_names, "no checks.* measurements were emitted"


class TestEmitPcieDiagnostic:
    def test_diagnose_step_carries_link_aer_diagnosis(self):
        from computetest.backend import select_backend
        from computetest.diagnostics import diagnose
        buf, em = _run()
        backend = select_backend()
        d = diagnose(backend, "0000:03:00.0", do_bert=False, do_margin=False)
        oc.emit_pcie_diagnostic(em, d)
        em.run_end(d.status)
        arts = _emit_lines(buf)
        names = [a["testStepArtifact"]["measurement"]["name"]
                 for a in arts
                 if "testStepArtifact" in a
                 and "measurement" in a["testStepArtifact"]]
        assert "link.speed_gen" in names
        assert "link.width" in names


class TestEmitMarginSeries:
    def test_lane_margin_emits_series_with_validator(self):
        from computetest.margining import LaneMargin, MarginResult
        buf, em = _run()
        em.step_start("pcie.diagnose 0000:03:00.0")
        m = MarginResult(bdf="0000:03:00.0",
                          lanes=[LaneMargin(lane=0, timing_ui=0.30),
                                 LaneMargin(lane=1, timing_ui=0.28)],
                          limit_ui=0.25)
        sid = oc.emit_margin_series(em, m)
        em.step_end("pass")
        em.run_end("pass")
        assert sid is not None
        arts = _emit_lines(buf)
        starts = [a["testStepArtifact"]["measurementSeriesStart"]
                  for a in arts
                  if "testStepArtifact" in a
                  and "measurementSeriesStart" in a["testStepArtifact"]]
        elems = [a["testStepArtifact"]["measurementSeriesElement"]
                 for a in arts
                 if "testStepArtifact" in a
                 and "measurementSeriesElement" in a["testStepArtifact"]]
        ends = [a["testStepArtifact"]["measurementSeriesEnd"]
                for a in arts
                if "testStepArtifact" in a
                and "measurementSeriesEnd" in a["testStepArtifact"]]
        assert len(starts) == 1 and len(elems) == 2 and len(ends) == 1
        assert starts[0]["validators"] == [{
            "type": "GREATER_THAN_OR_EQUAL", "value": 0.25,
            "name": "min_timing_ui",
        }]
        assert ends[0]["totalCount"] == 2
        # Lane index is in metadata; series element index is the position.
        assert [e["index"] for e in elems] == [0, 1]
        assert [e["metadata"]["lane"] for e in elems] == [0, 1]

    def test_no_lanes_returns_none_and_emits_nothing(self):
        from computetest.margining import MarginResult
        buf, em = _run()
        em.step_start("pcie.diagnose x")
        out = oc.emit_margin_series(em, MarginResult(bdf="x", lanes=[],
                                                       limit_ui=0.25))
        em.step_end("pass")
        assert out is None
        # No series-* artifacts in the stream.
        for a in _emit_lines(buf):
            for k in ("measurementSeriesStart", "measurementSeriesElement",
                      "measurementSeriesEnd"):
                if "testStepArtifact" in a:
                    assert k not in a["testStepArtifact"], k


class TestEmitChain:
    def test_chain_emits_step_with_terminal_diagnosis(self):
        from computetest.backend import select_backend
        from computetest.diagnostics import diagnose_chain
        buf, em = _run()
        backend = select_backend()
        bdfs = backend.list_devices()
        assert bdfs, "mock backend has no devices"
        d = diagnose_chain(backend, bdfs[0], max_seconds=1.0)
        oc.emit_chain(em, d)
        em.run_end(d.status)
        step_arts = [a["testStepArtifact"] for a in _emit_lines(buf)
                     if "testStepArtifact" in a]
        kinds = [next(iter(set(a.keys()) - {"testStepId"})) for a in step_arts]
        assert kinds[0] == "testStepStart"
        assert kinds[-1] == "testStepEnd"
        diagnoses = [a["diagnosis"] for a in step_arts if "diagnosis" in a]
        verdicts = [d["verdict"] for d in diagnoses]
        # The chain emitter always emits one BERT-summary + one chain-summary diagnosis.
        assert any(v.startswith("pcie.chain.bert.") for v in verdicts), verdicts
        assert any(v.startswith("pcie.chain.") and not v.startswith("pcie.chain.bert.")
                   for v in verdicts), verdicts

    def test_chain_with_topology_emits_per_link_measurements(self):
        # If the topology has more than one member, the emitter MUST surface per-link
        # speed/width/bw_changed measurements; this test stands ready to catch a
        # regression once the mock backend gains a non-trivial chain.
        from computetest.backend import select_backend
        from computetest.diagnostics import diagnose_chain
        from computetest.topology import analyze_chain
        backend = select_backend()
        candidate = next((b for b in backend.list_devices()
                          if len(analyze_chain(backend, b)[1]) > 0), None)
        if candidate is None:
            pytest.skip("mock topology is flat (no chain links)")
        buf, em = _run()
        d = diagnose_chain(backend, candidate, max_seconds=1.0)
        oc.emit_chain(em, d)
        em.run_end(d.status)
        names = [a["testStepArtifact"]["measurement"]["name"]
                 for a in _emit_lines(buf)
                 if "testStepArtifact" in a
                 and "measurement" in a["testStepArtifact"]]
        assert any(n.startswith("link.") for n in names), names


# ----------------------------------------------------------------------------
# CLI integration: --ocpdiag wires the emitter into every command branch
# ----------------------------------------------------------------------------
class TestCliOcpdiagFlag:
    def test_ocpdiag_to_file_writes_well_formed_stream(self, tmp_path):
        from computetest import cli
        path = tmp_path / "stream.jsonl"
        rc = cli.main(["bert", "-d", "0000:03:00.0", "--target-ber", "1e-12",
                       "--max-seconds", "1.0", "--ocpdiag", str(path)])
        assert rc in (cli.EXIT_PASS, cli.EXIT_FAIL)         # mock can return either
        lines = path.read_text().splitlines()
        assert len(lines) >= 4                              # schema + run_start + step(s) + run_end
        first = json.loads(lines[0])
        last = json.loads(lines[-1])
        assert first["schemaVersion"] == {"major": 2, "minor": 0}
        assert "testRunEnd" in last["testRunArtifact"]
        # Every artifact is structurally valid.
        for line in lines:
            _assert_artifact_shape(json.loads(line))

    def test_ocpdiag_stdout_routes_human_to_stderr(self, tmp_path, capsys):
        from computetest import cli
        rc = cli.main(["bert", "-d", "0000:03:00.0", "--target-ber", "1e-12",
                       "--max-seconds", "1.0", "--ocpdiag", "-"])
        assert rc in (cli.EXIT_PASS, cli.EXIT_FAIL)
        out = capsys.readouterr()
        # stdout: only the ocp-diag JSONL stream (every line parses as JSON)
        for line in out.out.splitlines():
            if line.strip():
                json.loads(line)                            # raises if human leaked
        # stderr: backend banner + human BERT summary (NOT JSONL)
        assert "# backend:" in out.err
        assert "BER<=" in out.err                            # the human summary marker

    def test_ocpdiag_run_end_status_matches_exit_code(self, tmp_path):
        from computetest import cli
        path = tmp_path / "x.jsonl"
        rc = cli.main(["nvme", "/dev/nvme0", "--ocpdiag", str(path)])
        last = json.loads(path.read_text().splitlines()[-1])
        end = last["testRunArtifact"]["testRunEnd"]
        expected = {cli.EXIT_PASS: "PASS", cli.EXIT_FAIL: "FAIL",
                    cli.EXIT_UNAVAIL: "NOT_APPLICABLE"}[rc]
        assert end["result"] == expected

    def test_ocpdiag_emits_run_error_on_bad_device(self, tmp_path):
        from computetest import cli
        path = tmp_path / "x.jsonl"
        rc = cli.main(["bert", "-d", "9999:99:99.9", "--ocpdiag", str(path)])
        assert rc == cli.EXIT_NOTFOUND
        artifacts = [json.loads(line) for line in path.read_text().splitlines()]
        # An error testRunArtifact MUST be present (KeyError mapped to run_error).
        errs = [a["testRunArtifact"]["error"] for a in artifacts
                if "testRunArtifact" in a and "error" in a["testRunArtifact"]]
        assert errs and errs[0]["symptom"] == "device-not-found"
        # The run still ends cleanly with a result.
        assert "testRunEnd" in artifacts[-1]["testRunArtifact"]


# ----------------------------------------------------------------------------
# Schema conformance: validate every emitted artifact against the vendored
# OCP v2.0 JSON Schemas (src/computetest/io/ocpdiag_schemas/).
# ----------------------------------------------------------------------------
jsonschema = pytest.importorskip("jsonschema")
referencing = pytest.importorskip("referencing")


def _build_schema_registry():
    """Load every vendored OCP schema and register it under its ``$id``.

    The schemas $ref each other via absolute paths
    (``/opencomputeproject/ocp-diag-core/<name>``); the resolver walks those
    refs through the registry.
    """
    import pathlib

    from referencing import Registry, Resource
    schema_dir = (pathlib.Path(__file__).parent.parent
                  / "src" / "computetest" / "io" / "ocpdiag_schemas")
    resources = []
    for path in schema_dir.glob("*.json"):
        schema = json.loads(path.read_text())
        if "$id" in schema:
            resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


class TestSchemaConformance:
    def test_every_emitted_artifact_validates_against_root_schema(self):
        from computetest.backend import select_backend
        from computetest.bert import run_bert
        from computetest.nvme import check_nvme
        buf, em = _run()
        # Emit one of every artifact shape we exercise in practice.
        backend = select_backend()
        r = run_bert(backend, "0000:03:00.0", target_ber=1e-12, max_seconds=1.0)
        oc.emit_bert(em, r, target_ber=1e-12)
        h = check_nvme("/dev/nvme0", mock=True)
        oc.emit_health(em, h, label="nvme")
        em.run_log("INFO", "smoke")
        em.run_end(r.status)

        registry = _build_schema_registry()
        root_uri = "https://github.com/opencomputeproject/ocp-diag-core/output"
        root = registry.contents(root_uri)
        validator = jsonschema.Draft202012Validator(root, registry=registry)
        for line in buf.getvalue().splitlines():
            if not line.strip():
                continue
            artifact = json.loads(line)
            errors = list(validator.iter_errors(artifact))
            assert not errors, (
                f"artifact failed schema:\n  {artifact}\n  errors:\n  "
                + "\n  ".join(e.message for e in errors))
