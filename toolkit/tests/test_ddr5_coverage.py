"""
Sprint 4.3.10: CI gate for ``docs/memory/ddr5_jesd79_5_coverage_matrix.md``.

Every DDR5 ``Mandatory`` row's ``pytest_node`` must resolve to a real test;
scheduled-debt budget 0. Cloned from the UNH-IOL v25 coverage gate. The numeric
JEDEC limits are paywalled, so the rows assert against the Linux ABI + ACPI
mechanisms; the JESD79-5 rev is an explicit axis (see the matrix header).
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).parent.parent
MATRIX = TESTS_ROOT / "docs" / "memory" / "ddr5_jesd79_5_coverage_matrix.md"
NODE_RE = re.compile(r"^(?P<path>[\w./-]+\.py)::(?:(?P<cls>\w+)::)?(?P<func>\w+)$")
SCHEDULED_MARKERS = {"_scheduled_"}


def _parse_matrix(text: str) -> list[dict]:
    rows: list[dict] = []
    in_spine = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## Spine"):
            in_spine = True
            continue
        if in_spine and stripped.startswith("##"):
            break
        if not in_spine or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 5 or cells[0].startswith("---") or cells[1] == "Test ID":
            continue
        rows.append({"group": cells[0], "test_id": cells[1], "title": cells[2],
                     "type": cells[3], "pytest_node": cells[4]})
    return rows


def _node_exists(node: str) -> bool:
    m = NODE_RE.match(node)
    if not m:
        return False
    path = TESTS_ROOT / m["path"]
    if not path.exists():
        return False
    mod_name = ".".join(path.relative_to(TESTS_ROOT / "tests").with_suffix("").parts)
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        return False
    if m["cls"] is not None:
        cls = getattr(mod, m["cls"], None)
        return cls is not None and callable(getattr(cls, m["func"], None))
    return callable(getattr(mod, m["func"], None))


@pytest.fixture(scope="module")
def matrix_rows() -> list[dict]:
    assert MATRIX.exists(), f"matrix missing: {MATRIX}"
    return _parse_matrix(MATRIX.read_text())


class TestDdr5CoverageGate:
    def test_parses_rows(self, matrix_rows):
        assert len(matrix_rows) > 0

    def test_mandatory_nodes_non_empty(self, matrix_rows):
        bad = [r for r in matrix_rows
               if r["type"] == "Mandatory" and not r["pytest_node"]]
        assert not bad, [r["test_id"] for r in bad]

    def test_mandatory_nodes_resolve(self, matrix_rows):
        failures = [f"{r['test_id']} -> {r['pytest_node']}"
                    for r in matrix_rows
                    if r["type"] == "Mandatory"
                    and not any(r["pytest_node"].startswith(m) for m in SCHEDULED_MARKERS)
                    and not _node_exists(r["pytest_node"])]
        assert not failures, "unresolvable:\n  " + "\n  ".join(failures)

    def test_scheduled_budget_zero(self, matrix_rows):
        scheduled = [r for r in matrix_rows
                     if r["type"] == "Mandatory"
                     and any(r["pytest_node"].startswith(m) for m in SCHEDULED_MARKERS)]
        assert len(scheduled) == 0


class TestDdr5MatrixParser:
    def test_resolves_a_real_node(self):
        assert _node_exists("tests/test_ddr5_ras.py::TestEcs::test_read_default")

    def test_rejects_garbage(self):
        assert not _node_exists("tests/test_ddr5_ras.py::TestEcs::test_nope")
        assert not _node_exists("tests/x.py::")
