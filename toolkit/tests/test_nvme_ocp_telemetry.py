"""
Sprint 2.3: OCP nvme-cli internal-log telemetry adapter
(``computetest.nvme.ocp_telemetry``).

Tests cover the parser, the shell-out, the corpus snapshot, the mock decode,
and the ocp-diag emission.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from computetest.io import ocpdiag
from computetest.nvme import ocp_telemetry as ocp

os.environ.setdefault("COMPUTETEST_BACKEND", "mock")


CORPUS = Path(__file__).parent.parent / "corpus" / "nvme_ocp_internal_log"


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------
class TestParserTelemetry:
    def test_basic_decode_extracts_log_id_version_data_areas(self):
        raw = {
            "logId": 7,
            "header": {"version": 1, "host_initiated_data_generation_number": 5},
            "dataAreas": {"1": {"sizeBlocks": 64}, "2": {"sizeBlocks": 0}},
        }
        r = ocp._parse_telemetry("/dev/nvme0", raw)
        assert r.log_id == 7
        assert r.version == 1
        assert r.data_areas == {"1": 64, "2": 0}
        assert r.total_blocks == 64

    def test_missing_data_areas_is_empty_dict(self):
        r = ocp._parse_telemetry("/dev/nvme0", {"logId": 7, "header": {}})
        assert r.data_areas == {}
        assert r.total_blocks == 0

    def test_missing_header_version_is_none(self):
        r = ocp._parse_telemetry("/dev/nvme0", {"logId": 7})
        assert r.version is None

    def test_non_dict_data_area_body_treated_as_zero_blocks(self):
        # nvme-cli has historically returned plain ints for empty areas.
        r = ocp._parse_telemetry("/dev/nvme0", {"logId": 7,
                                                  "dataAreas": {"3": 0}})
        assert r.data_areas == {"3": 0}


class TestParserStrings:
    def test_strings_decode_round_trip(self):
        raw = {"logId": 201,
               "strings": {"model": "Mock", "boot_count": "42"}}
        r = ocp._parse_strings("/dev/nvme0", raw)
        assert r.log_id == 201
        assert r.strings == {"model": "Mock", "boot_count": "42"}
        assert r.get("model") == "Mock"
        assert r.get("missing", "default") == "default"

    def test_missing_strings_is_empty(self):
        r = ocp._parse_strings("/dev/nvme0", {"logId": 201})
        assert r.strings == {}

    def test_default_log_id_is_c9(self):
        r = ocp._parse_strings("/dev/nvme0", {"strings": {"k": "v"}})
        assert r.log_id == 0xC9


# ----------------------------------------------------------------------------
# Mock + public API
# ----------------------------------------------------------------------------
class TestPublicApi:
    def test_read_telemetry_mock_path(self):
        r = ocp.read_telemetry("/dev/nvme0", mock=True)
        assert r.log_id == 7
        assert r.data_areas["1"] == 32
        assert r.data_areas["2"] == 16

    def test_read_strings_mock_path(self):
        r = ocp.read_strings("/dev/nvme0", mock=True)
        assert r.log_id == 0xC9
        assert r.get("model") == "MockSSD-1TB"


# ----------------------------------------------------------------------------
# Shell-out error paths (kept off the real-hw `_shell_internal_log` so
# CI doesn't need nvme-cli; mocks the subprocess + shutil layer instead).
# ----------------------------------------------------------------------------
class TestRequireNvmeCli:
    def test_missing_nvme_cli_raises(self, monkeypatch):
        monkeypatch.setattr(ocp.shutil, "which", lambda _: None)
        with pytest.raises(ocp.OcpTelemetryError, match="not found on PATH"):
            ocp._require_nvme_cli()

    def test_present_nvme_cli_returns_path(self, monkeypatch):
        monkeypatch.setattr(ocp.shutil, "which", lambda _: "/usr/sbin/nvme")
        assert ocp._require_nvme_cli() == "/usr/sbin/nvme"


# ----------------------------------------------------------------------------
# Corpus snapshot
# ----------------------------------------------------------------------------
class TestCorpusSnapshot:
    def test_synthetic_corpus_telemetry_matches_expected(self):
        case = CORPUS / "synthetic-mock-1tb"
        raw_t = json.loads((case / "telemetry.json").read_text())
        expected = json.loads((case / "expected.json").read_text())
        r = ocp._parse_telemetry("/dev/nvme0", raw_t)
        assert r.log_id == expected["telemetry"]["log_id"]
        assert r.version == expected["telemetry"]["version"]
        assert r.total_blocks == expected["telemetry"]["total_blocks"]
        assert r.data_areas == expected["telemetry"]["data_areas"]

    def test_synthetic_corpus_strings_matches_expected(self):
        case = CORPUS / "synthetic-mock-1tb"
        raw_s = json.loads((case / "strings.json").read_text())
        expected = json.loads((case / "expected.json").read_text())["strings"]
        r = ocp._parse_strings("/dev/nvme0", raw_s)
        assert r.log_id == expected["log_id"]
        for k in ("model", "firmware", "boot_count", "build_signature"):
            assert r.get(k) == expected[k]


# ----------------------------------------------------------------------------
# ocp-diag emission
# ----------------------------------------------------------------------------
class TestEmitToOcpdiag:
    def _emit(self, telemetry=None, strings=None) -> list[dict]:
        buf = io.StringIO()
        em = ocpdiag.open_run(buf, program_version="0.1.0",
                               command_line="test", parameters={},
                               dut_serial="SN-T", station="S-T",
                               clock=lambda: "2026-05-29T00:00:00.000Z")
        ocp.emit_to_ocpdiag(em, telemetry=telemetry, strings=strings)
        em.run_end("pass")
        return [json.loads(line) for line in buf.getvalue().splitlines()]

    def test_emit_telemetry_only(self):
        tel = ocp.read_telemetry("/dev/nvme0", mock=True)
        arts = self._emit(telemetry=tel)
        names = [a["testStepArtifact"]["measurement"]["name"]
                 for a in arts
                 if "testStepArtifact" in a
                 and "measurement" in a["testStepArtifact"]]
        assert "ocp.telemetry.log_id" in names
        assert "ocp.telemetry.total_blocks" in names
        assert "ocp.telemetry.data_area_1.blocks" in names

    def test_emit_strings_only(self):
        s = ocp.read_strings("/dev/nvme0", mock=True)
        arts = self._emit(strings=s)
        names = [a["testStepArtifact"]["measurement"]["name"]
                 for a in arts
                 if "testStepArtifact" in a
                 and "measurement" in a["testStepArtifact"]]
        assert "ocp.strings.log_id" in names
        assert "ocp.strings.model" in names
        assert "ocp.strings.build_signature" in names

    def test_emit_telemetry_and_strings_together(self):
        tel = ocp.read_telemetry("/dev/nvme0", mock=True)
        s = ocp.read_strings("/dev/nvme0", mock=True)
        arts = self._emit(telemetry=tel, strings=s)
        # Both kinds present.
        names = {a["testStepArtifact"]["measurement"]["name"]
                 for a in arts
                 if "testStepArtifact" in a
                 and "measurement" in a["testStepArtifact"]}
        assert "ocp.telemetry.total_blocks" in names
        assert "ocp.strings.model" in names

    def test_emit_neither_raises(self):
        buf = io.StringIO()
        em = ocpdiag.open_run(buf, program_version="0.1.0",
                               command_line="test", parameters={},
                               dut_serial="SN-T")
        with pytest.raises(ocp.OcpTelemetryError, match="at least one"):
            ocp.emit_to_ocpdiag(em)


# ----------------------------------------------------------------------------
# Module surface
# ----------------------------------------------------------------------------
def test_public_exports():
    assert hasattr(ocp, "read_telemetry")
    assert hasattr(ocp, "read_strings")
    assert hasattr(ocp, "emit_to_ocpdiag")
    assert hasattr(ocp, "OcpTelemetryResult")
    assert hasattr(ocp, "OcpStringsResult")
    assert hasattr(ocp, "OcpTelemetryError")
