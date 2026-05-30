"""Sprint 4.3.9: DDR5 SPD (JESD400-5) parser + module conformance + corpus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from computetest.ddr5 import SpdHealth, check_spd, parse_spd

CORPUS = Path(__file__).parent.parent / "corpus" / "ddr5_spd" / "synthetic-ddr5-32gb"


def _spd_bytes() -> bytes:
    return bytes.fromhex((CORPUS / "spd.hex").read_text().strip())


def _expected() -> dict:
    return json.loads((CORPUS / "expected.json").read_text())


class TestParse:
    def test_too_short_rejected(self):
        with pytest.raises(ValueError, match="too short"):
            parse_spd(b"\x00" * 4)


class TestCorpus:
    def test_parses_to_expected(self):
        assert parse_spd(_spd_bytes()).to_dict() == _expected()

    def test_check_passes_against_expected(self):
        h = check_spd(_spd_bytes(), expected=_expected())
        assert isinstance(h, SpdHealth) and h.ok
        assert h.checks["is_ddr5"] is True


class TestCheck:
    def test_mismatch_fails(self):
        h = check_spd(_spd_bytes(), expected={"density_gbit": 64})   # wrong density
        assert not h.ok and h.checks["density_gbit_matches"] is False

    def test_summary_and_to_dict(self):
        h = check_spd(_spd_bytes(), expected={"ecc": True})
        assert "DDR5 SPD" in h.summary()
        assert h.to_dict()["info"]["module_type"] == "RDIMM"
