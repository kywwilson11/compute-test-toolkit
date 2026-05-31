"""
OCP pci_lmt schema/CLI compatibility (computetest.lmt_adapter): per-lane record
shape, JSON/CSV output, mapping from MarginResult, and the `lmt` CLI subcommand.

The columns and defaults assert here MUST stay in lock-step with the OCP
pci_lmt CLI (https://github.com/opencomputeproject/ocp-diag-pci_lmt). If
upstream drifts, these tests fail loudly — that's the point.
"""
from __future__ import annotations

import csv
import io
import json
import os

import pytest

from computetest import lmt_adapter as lmt
from computetest.margining import LaneMargin, MarginResult

os.environ.setdefault("COMPUTETEST_BACKEND", "mock")


# ----------------------------------------------------------------------------
# Schema parity: columns and defaults must match the OCP CLI
# ----------------------------------------------------------------------------
class TestSchemaParity:
    def test_columns_match_pci_lmt_canonical_set(self):
        # Exact set + exact order matters for CSV consumers.
        expected = (
            "bdf", "speed", "width", "lmt_capable",
            "ind_error_sampler", "sample_reporting_method",
            "ind_left_right_timing", "ind_up_down_voltage",
            "voltage_supported", "num_voltage_steps", "num_timing_steps",
            "max_timing_offset", "max_voltage_offset",
            "sampling_rate_voltage", "sampling_rate_timing", "max_lanes",
            "lane", "receiver_number", "margin_type", "step",
            "sample_count", "sample_count_bits", "error_count", "ber",
        )
        assert lmt.COLUMNS == expected

    def test_record_dataclass_field_set_matches_columns(self):
        from dataclasses import fields
        record_fields = tuple(f.name for f in fields(lmt.LmtLaneRecord))
        assert record_fields == lmt.COLUMNS

    def test_ocp_cli_defaults(self):
        # pci_lmt CLI defaults; these are the values OCP consumers expect.
        assert lmt.DEFAULT_ERROR_COUNT_LIMIT == 63
        assert lmt.DEFAULT_DWELL_TIME_S == 5


# ----------------------------------------------------------------------------
# Mapping: MarginResult -> LmtLaneRecord
# ----------------------------------------------------------------------------
class TestFromMarginResult:
    def _backend(self):
        from computetest.backend import select_backend
        return select_backend()

    def test_one_record_per_lane_for_timing_only_margins(self):
        backend = self._backend()
        m = MarginResult(bdf="0000:03:00.0",
                          lanes=[LaneMargin(lane=i, timing_ui=0.30)
                                 for i in range(4)])
        recs = lmt.from_margin_result(m, backend=backend)
        assert len(recs) == 4
        for r in recs:
            assert r.margin_type == "TIMING"
            assert r.lane in (0, 1, 2, 3)
            assert r.error_count == 0                  # we report the LAST passing step
            assert r.bdf == "0000:03:00.0"
            assert r.lmt_capable is True               # Gen5 mock has the LMT ext cap

    def test_voltage_lane_produces_a_second_voltage_record(self):
        backend = self._backend()
        m = MarginResult(bdf="0000:03:00.0",
                          lanes=[LaneMargin(lane=0, timing_ui=0.30,
                                             voltage_mv=120.0)])
        recs = lmt.from_margin_result(m, backend=backend)
        assert {r.margin_type for r in recs} == {"TIMING", "VOLTAGE"}
        v = next(r for r in recs if r.margin_type == "VOLTAGE")
        assert v.voltage_supported is True
        assert v.step > 0                              # 120 mV maps to a positive step

    def test_step_maps_timing_ui_through_max_offset(self):
        backend = self._backend()
        # max_timing_offset=50 (0.50 UI) over num_timing_steps=32 => step at 0.5 UI = 32.
        m = MarginResult(bdf="0000:03:00.0",
                          lanes=[LaneMargin(lane=0, timing_ui=0.5)])
        recs = lmt.from_margin_result(m, backend=backend)
        assert recs[0].step == 32                       # full-range margin -> max step

    def test_step_is_clamped_to_num_timing_steps(self):
        backend = self._backend()
        m = MarginResult(bdf="0000:03:00.0",
                          lanes=[LaneMargin(lane=0, timing_ui=10.0)])  # absurd
        recs = lmt.from_margin_result(m, backend=backend)
        assert recs[0].step == lmt.DEFAULT_NUM_TIMING_STEPS

    def test_empty_margin_produces_empty_record_set(self):
        backend = self._backend()
        m = MarginResult(bdf="0000:03:00.0", lanes=[])
        assert lmt.from_margin_result(m, backend=backend) == []

    def test_sample_count_is_raw_7bit_register_value_and_bits_are_derived(self):
        # OCP pci_lmt (pcie_lane_margining.py): sample_count is the raw 7-bit
        # MSampleCount register field (0..127, "Value = 3*log2(bits)") and
        # sample_count_bits = int(2**(sample_count/3)) is the bits margined.
        # NOT the inverse (the old code emitted sample_count = 1<<bits).
        backend = self._backend()
        m = MarginResult(bdf="0000:03:00.0",
                         lanes=[LaneMargin(lane=0, timing_ui=0.30)])
        r = lmt.from_margin_result(m, backend=backend)[0]
        assert 0 <= r.sample_count <= 127
        assert r.sample_count_bits == int(2 ** (r.sample_count / 3))
        assert r.sample_count == 18 and r.sample_count_bits == 64  # default

    def test_sample_count_register_value_drives_bit_count(self):
        # 3*log2(bits) encoding: raw 21 -> 2**7 = 128 bits margined.
        backend = self._backend()
        m = MarginResult(bdf="0000:03:00.0",
                         lanes=[LaneMargin(lane=0, timing_ui=0.30)])
        r = lmt.from_margin_result(m, backend=backend, sample_count=21)[0]
        assert r.sample_count == 21
        assert r.sample_count_bits == 128

    def test_max_offsets_are_in_register_units_not_mv(self):
        # OCP/Google pci_lmt emit MaxTimingOffset/MaxVoltageOffset as raw
        # Margining Capability register values: MaxTimingOffset in 1%-UI units
        # (50 = 0.50 UI) and MaxVoltageOffset in 0.01-V units (49 = 0.49 V),
        # capped near ~0.5 V -- NOT millivolts (200 mV would be 2.0 V).
        backend = self._backend()
        m = MarginResult(bdf="0000:03:00.0",
                         lanes=[LaneMargin(lane=0, timing_ui=0.30)])
        r = lmt.from_margin_result(m, backend=backend)[0]
        assert r.max_timing_offset == 50
        assert r.max_voltage_offset == 49
        assert 0 < r.max_voltage_offset <= 63  # 7-bit field, physical <= ~0.5 V


# ----------------------------------------------------------------------------
# Step-mapping helper guards (degenerate device-cap inputs -> step 0)
# ----------------------------------------------------------------------------
class TestStepHelperGuards:
    def test_voltage_step_guards_on_nonpositive_inputs(self):
        # 0 voltage-steps or a 0 max-offset register value must yield step 0,
        # never a divide-by-zero.
        assert lmt._step_from_voltage_mv(120.0, 0, 49) == 0
        assert lmt._step_from_voltage_mv(120.0, 64, 0) == 0

    def test_timing_step_guards_on_nonpositive_inputs(self):
        assert lmt._step_from_timing_ui(0.3, 0, 50) == 0
        assert lmt._step_from_timing_ui(0.3, 32, 0) == 0


# ----------------------------------------------------------------------------
# Formatters
# ----------------------------------------------------------------------------
class TestFormatters:
    def _rec(self, **kw):
        defaults = dict(
            bdf="0000:03:00.0", speed=5, width=16, lmt_capable=True,
            ind_error_sampler=False, sample_reporting_method=0,
            ind_left_right_timing=False, ind_up_down_voltage=False,
            voltage_supported=False, num_voltage_steps=64,
            num_timing_steps=32, max_timing_offset=50,
            max_voltage_offset=200, sampling_rate_voltage=0,
            sampling_rate_timing=0, max_lanes=16, lane=0,
            receiver_number=1, margin_type="TIMING", step=30,
            sample_count=18, sample_count_bits=int(2 ** (18 / 3)),
            error_count=0, ber=0.0,
        )
        defaults.update(kw)
        return lmt.LmtLaneRecord(**defaults)

    def test_to_json_is_a_list_of_objects_with_every_column(self):
        recs = [self._rec(lane=i) for i in range(2)]
        data = json.loads(lmt.to_json(recs))
        assert isinstance(data, list) and len(data) == 2
        for row in data:
            assert set(row.keys()) == set(lmt.COLUMNS)

    def test_to_csv_header_is_canonical_column_order(self):
        recs = [self._rec(lane=i) for i in range(2)]
        out = lmt.to_csv(recs)
        reader = csv.reader(io.StringIO(out))
        header = next(reader)
        assert tuple(header) == lmt.COLUMNS

    def test_to_csv_row_count_matches_input(self):
        recs = [self._rec(lane=i) for i in range(16)]
        out = lmt.to_csv(recs)
        data_rows = list(csv.DictReader(io.StringIO(out)))
        assert len(data_rows) == 16


# ----------------------------------------------------------------------------
# Subprocess: call_pci_lmt
# ----------------------------------------------------------------------------
class TestCallPciLmt:
    def test_raises_when_pci_lmt_not_on_path(self, monkeypatch):
        monkeypatch.setattr(lmt, "is_pci_lmt_available", lambda: False)
        with pytest.raises(RuntimeError, match="not on PATH"):
            lmt.call_pci_lmt("/tmp/cfg.yaml")

    def test_constructs_expected_cli_invocation(self, monkeypatch):
        captured: dict = {}

        class _FakeProc:
            returncode = 0
            stdout = "[]\n"
            stderr = ""

        def _fake_run(cmd, **kw):
            captured["cmd"] = list(cmd)
            return _FakeProc()

        monkeypatch.setattr(lmt, "is_pci_lmt_available", lambda: True)
        monkeypatch.setattr(lmt.subprocess, "run", _fake_run)
        out = lmt.call_pci_lmt("/tmp/cfg.yaml", output_format="csv",
                               error_count_limit=128, dwell_time_s=10,
                               annotation="my-station-run")
        assert out == "[]\n"
        assert captured["cmd"] == [
            "pci_lmt", "-o", "csv", "-e", "128", "-d", "10",
            "-a", "my-station-run", "/tmp/cfg.yaml",
        ]

    def test_raises_on_nonzero_exit(self, monkeypatch):
        class _FakeProc:
            returncode = 2
            stdout = ""
            stderr = "config not found"

        monkeypatch.setattr(lmt, "is_pci_lmt_available", lambda: True)
        monkeypatch.setattr(lmt.subprocess, "run", lambda *a, **kw: _FakeProc())
        with pytest.raises(RuntimeError, match="rc=2.*config not found"):
            lmt.call_pci_lmt("/tmp/cfg.yaml")


# ----------------------------------------------------------------------------
# CLI: the `lmt` subcommand
# ----------------------------------------------------------------------------
class TestCliLmt:
    def test_json_format_emits_pci_lmt_record_list(self, capsys):
        from computetest import cli
        rc = cli.main(["lmt", "-d", "0000:03:00.0", "--format", "json"])
        assert rc in (cli.EXIT_PASS, cli.EXIT_FAIL)
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list) and data
        for row in data:
            assert set(row.keys()) == set(lmt.COLUMNS)

    def test_csv_format_has_canonical_header(self, capsys):
        from computetest import cli
        cli.main(["lmt", "-d", "0000:03:00.0", "--format", "csv"])
        out = capsys.readouterr().out
        header = next(csv.reader(io.StringIO(out)))
        assert tuple(header) == lmt.COLUMNS

    def test_json_flag_overrides_csv_format(self, capsys):
        from computetest import cli
        cli.main(["lmt", "-d", "0000:03:00.0", "--format", "csv", "--json"])
        # --json wins; the body must parse as JSON.
        json.loads(capsys.readouterr().out)

    def test_ocpdiag_emits_lmt_step_with_per_lane_measurements(self, tmp_path):
        from computetest import cli
        path = tmp_path / "out.jsonl"
        cli.main(["lmt", "-d", "0000:03:00.0", "--ocpdiag", str(path)])
        artifacts = [json.loads(line) for line in path.read_text().splitlines()]
        # The lmt step emits one testStepStart, many measurements, one diagnosis,
        # and one testStepEnd. Confirm the measurements name pattern is lmt.*.
        names = [a["testStepArtifact"]["measurement"]["name"]
                 for a in artifacts
                 if "testStepArtifact" in a
                 and "measurement" in a["testStepArtifact"]]
        assert any(n.startswith("lmt.timing.lane0.") for n in names), names
        assert any(n.endswith(".step") for n in names), names

    def test_unavailable_returns_exit_unavail(self, monkeypatch, capsys):
        # Force margining unavailable on the mock backend.
        from computetest import cli, margining
        monkeypatch.setattr(
            margining, "margin_link",
            lambda backend, bdf, limit_ui=None: MarginResult(
                bdf=bdf, lanes=[], note="not Gen4+"))
        rc = cli.main(["lmt", "-d", "0000:03:00.0"])
        assert rc == cli.EXIT_UNAVAIL                   # 5

    def test_via_pci_lmt_invokes_subprocess(self, monkeypatch, tmp_path, capsys):
        from computetest import cli
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("dummy: 1\n")
        called: dict = {}

        def _fake(config_path, **kw):
            called["config_path"] = config_path
            called["kw"] = kw
            return '[{"bdf": "0000:03:00.0"}]\n'

        monkeypatch.setattr(cli.lmt_adapter, "call_pci_lmt", _fake)
        rc = cli.main(["lmt", "-d", "0000:03:00.0",
                       "--via-pci-lmt", str(cfg),
                       "--error-count-limit", "42",
                       "--dwell-time", "3"])
        # Re-anchored: --via-pci-lmt must NOT emit a gate-trustable PASS. pci_lmt's
        # records carry no explicit pass/fail column and computetest does not compute
        # the verdict, so the honest exit code is EXIT_UNAVAIL ("the tool ran; no
        # verdict from us"), not EXIT_PASS.
        assert rc == cli.EXIT_UNAVAIL
        assert called["config_path"] == str(cfg)
        assert called["kw"]["error_count_limit"] == 42
        assert called["kw"]["dwell_time_s"] == 3
        # And the subprocess output flows through to stdout verbatim.
        assert "0000:03:00.0" in capsys.readouterr().out
