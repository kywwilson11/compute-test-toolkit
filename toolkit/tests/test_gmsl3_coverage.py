"""
Sprint 4.1.11: CI gate for ``docs/gmsl/gmsl3_channel_validation_matrix.md``.

For every GMSL3 ``Mandatory`` row in the coverage matrix, the ``pytest_node``
column MUST either resolve to a real pytest node (file + class + method) or be
explicitly marked ``_scheduled_`` (tracked debt). ``FYI`` rows are ignored.

GMSL is a single-vendor ecosystem, so ``Mandatory`` here means ADI-spec-mandatory
(AN-2585 / UG-2208), not plugfest-mandatory. This test is the gate; it failing
fails CI. (Cloned from the UNH-IOL v25 coverage gate.)
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

MATRIX = (Path(__file__).parent.parent / "docs" / "gmsl"
          / "gmsl3_channel_validation_matrix.md")
TESTS_ROOT = Path(__file__).parent.parent

NODE_RE = re.compile(r"^(?P<path>[\w./-]+\.py)::(?:(?P<cls>\w+)::)?(?P<func>\w+)$")
SCHEDULED_MARKERS = {"_scheduled_", "_scheduled_ (Sprint 4.1 follow-on)"}


def _parse_matrix(text: str) -> list[dict]:
    """Extract the spine table rows (Group | Test ID | Title | Type | pytest_node)."""
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
        if len(cells) != 5:
            continue
        if cells[0].startswith("---") or cells[1] == "Test ID":
            continue
        rows.append({"group": cells[0], "test_id": cells[1], "title": cells[2],
                     "type": cells[3], "pytest_node": cells[4]})
    return rows


def _resolve_node(node: str) -> tuple[Path, str | None, str]:
    m = NODE_RE.match(node)
    if not m:
        raise ValueError(f"unparseable pytest node {node!r}")
    return (TESTS_ROOT / m["path"], m["cls"], m["func"])


def _module_for_path(path: Path) -> str:
    rel = path.relative_to(TESTS_ROOT / "tests")
    return ".".join(rel.with_suffix("").parts)


def _node_exists(node: str) -> bool:
    try:
        path, cls_name, func_name = _resolve_node(node)
    except ValueError:
        return False
    if not path.exists():
        return False
    try:
        mod = importlib.import_module(_module_for_path(path))
    except Exception:
        return False
    if cls_name is not None:
        cls = getattr(mod, cls_name, None)
        if cls is None:
            return False
        return callable(getattr(cls, func_name, None))
    return callable(getattr(mod, func_name, None))


@pytest.fixture(scope="module")
def matrix_rows() -> list[dict]:
    assert MATRIX.exists(), f"matrix doc missing at {MATRIX}"
    return _parse_matrix(MATRIX.read_text())


class TestGmsl3CoverageGate:
    def test_matrix_parses_rows(self, matrix_rows):
        assert len(matrix_rows) > 0, "no spine rows parsed from GMSL3 matrix"

    def test_mandatory_pytest_node_non_empty(self, matrix_rows):
        bad = [r for r in matrix_rows
               if r["type"] == "Mandatory" and not r["pytest_node"]]
        assert not bad, ("Mandatory rows with empty pytest_node:\n  "
                         + "\n  ".join(f"{r['test_id']} {r['title']}" for r in bad))

    def test_mandatory_pytest_node_resolves(self, matrix_rows):
        failures: list[str] = []
        for row in matrix_rows:
            if row["type"] != "Mandatory":
                continue
            node = row["pytest_node"]
            if any(node.startswith(m) for m in SCHEDULED_MARKERS):
                continue
            if not _node_exists(node):
                failures.append(f"{row['test_id']} {row['title']!r} -> {node!r}")
        assert not failures, ("Mandatory rows with unresolvable pytest_node:\n  "
                              + "\n  ".join(failures))

    def test_scheduled_budget_is_zero(self, matrix_rows):
        expected_scheduled_budget = 0
        scheduled = [r for r in matrix_rows
                     if r["type"] == "Mandatory"
                     and any(r["pytest_node"].startswith(m) for m in SCHEDULED_MARKERS)]
        assert len(scheduled) <= expected_scheduled_budget, (
            f"GMSL3 coverage debt grew: {len(scheduled)} scheduled Mandatory rows "
            f"exceed budget {expected_scheduled_budget}.")


class TestGmsl3MatrixParser:
    def test_resolves_a_real_gmsl_node(self):
        assert _node_exists(
            "tests/test_gmsl_eom.py::TestEomCheck::test_reverse_nrz_single_eye")

    def test_rejects_typo_node(self):
        assert not _node_exists("tests/test_gmsl_eom.py::TestEomCheck::test_nope")

    def test_node_re_rejects_garbage(self):
        for bad in ("tests/x", "tests/test_x.py", "::test_y", "tests/x.py::"):
            assert NODE_RE.match(bad) is None, bad
