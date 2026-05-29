"""
Sprint 3.2: CI gate for ``docs/nvme/unh_iol_v25_coverage_matrix.md``.

For every UNH-IOL v25 ``Mandatory`` row in the coverage matrix, the
``pytest_node`` column MUST either:

* Reference a real pytest node that resolves to an existing test (file +
  class + method), OR
* Be explicitly marked ``_scheduled_`` (tracked as known debt; logged but
  not failed) — Sprint 3.3 work converts these to real nodes.

``FYI`` rows are aspirational and ignored by the gate.

This test is the gate. CI runs the full suite; this test failing fails CI.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

MATRIX = (Path(__file__).parent.parent / "docs" / "nvme"
          / "unh_iol_v25_coverage_matrix.md")
TESTS_ROOT = Path(__file__).parent.parent

# A pytest node is ``path/to/file.py::ClassName::test_name`` or
# ``path/to/file.py::test_name``.
NODE_RE = re.compile(r"^(?P<path>[\w./-]+\.py)::(?:(?P<cls>\w+)::)?(?P<func>\w+)$")
SCHEDULED_MARKERS = {"_scheduled_", "_scheduled_ (Sprint 3)",
                       "_scheduled_ (Sprint 3 — needs real-bus path)"}


# ----------------------------------------------------------------------------
# Matrix parsing
# ----------------------------------------------------------------------------
def _parse_matrix(text: str) -> list[dict]:
    """Extract the spine table rows from the matrix.

    The spine table has these column headers (in order):
        Group | Test ID | Title | Type | pytest_node

    Returns a list of dicts keyed by column name. Skips header + separator
    rows and the introductory category-summary table.
    """
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
        # Header row: contains 'Test ID'; separator row: starts with |---.
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) != 5:
            continue
        if cells[0].startswith("---") or cells[1] == "Test ID":
            continue
        rows.append({
            "group": cells[0],
            "test_id": cells[1],
            "title": cells[2],
            "type": cells[3],
            "pytest_node": cells[4],
        })
    return rows


def _resolve_node(node: str) -> tuple[Path, str | None, str]:
    """Parse ``tests/test_x.py::Cls::test_y`` into (path, class_name, func)."""
    m = NODE_RE.match(node)
    if not m:
        raise ValueError(f"unparseable pytest node {node!r}")
    return (TESTS_ROOT / m["path"], m["cls"], m["func"])


def _module_for_path(path: Path) -> str:
    """Convert ``tests/test_x.py`` to the importable module name ``test_x``."""
    rel = path.relative_to(TESTS_ROOT / "tests")
    return ".".join(rel.with_suffix("").parts)


def _node_exists(node: str) -> bool:
    """Verify a pytest node resolves to an actual test function.

    Imports the module, looks up the class (if any) and the method. Returns
    True iff every layer resolves; False otherwise.
    """
    try:
        path, cls_name, func_name = _resolve_node(node)
    except ValueError:
        return False
    if not path.exists():
        return False
    mod_name = _module_for_path(path)
    try:
        mod = importlib.import_module(mod_name)
    except Exception:
        return False
    if cls_name is not None:
        cls = getattr(mod, cls_name, None)
        if cls is None:
            return False
        return callable(getattr(cls, func_name, None))
    return callable(getattr(mod, func_name, None))


# ----------------------------------------------------------------------------
# The gate
# ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def matrix_rows() -> list[dict]:
    assert MATRIX.exists(), f"matrix doc missing at {MATRIX}"
    return _parse_matrix(MATRIX.read_text())


class TestCoverageMatrixGate:
    def test_matrix_parses_at_least_some_rows(self, matrix_rows):
        # If we got 0 rows the parser broke or the doc was wiped; either way fail.
        assert len(matrix_rows) > 0, "no spine rows parsed from matrix"

    def test_mandatory_pytest_node_is_non_empty(self, matrix_rows):
        """Every Mandatory row MUST have a non-empty pytest_node column."""
        bad = [r for r in matrix_rows
                if r["type"] == "Mandatory" and not r["pytest_node"]]
        assert not bad, (
            "Mandatory rows with empty pytest_node:\n  "
            + "\n  ".join(f"{r['test_id']} {r['title']}" for r in bad))

    def test_mandatory_pytest_node_resolves_or_is_explicitly_scheduled(
            self, matrix_rows):
        """A Mandatory row's pytest_node MUST either resolve to a real test
        OR be explicitly marked ``_scheduled_*``. Anything else is
        a typo / a deleted test / drift."""
        failures: list[str] = []
        for row in matrix_rows:
            if row["type"] != "Mandatory":
                continue
            node = row["pytest_node"]
            if any(node.startswith(m) for m in SCHEDULED_MARKERS):
                continue
            if not _node_exists(node):
                failures.append(
                    f"{row['test_id']} {row['title']!r} -> {node!r}")
        assert not failures, (
            "Mandatory rows with unresolvable pytest_node:\n  "
            + "\n  ".join(failures))

    def test_known_scheduled_count_does_not_grow(self, matrix_rows):
        """Track the number of Mandatory rows still marked ``_scheduled_*``
        as a coverage-debt budget. The cap drops every time a sprint
        replaces a scheduled row with a real pytest_node — never raises.

        Update the ``EXPECTED_SCHEDULED_BUDGET`` below DOWN when you ship a
        Sprint that covers one of these rows. Never raise it: a regression in
        coverage debt is a real signal.
        """
        EXPECTED_SCHEDULED_BUDGET = 3        # Sprint 3.3 brings this to 0
        scheduled = [r for r in matrix_rows
                      if r["type"] == "Mandatory"
                      and any(r["pytest_node"].startswith(m)
                              for m in SCHEDULED_MARKERS)]
        assert len(scheduled) <= EXPECTED_SCHEDULED_BUDGET, (
            f"coverage debt grew: {len(scheduled)} scheduled rows now "
            f"exceeds budget of {EXPECTED_SCHEDULED_BUDGET}. Either ship "
            f"the tests or lower the budget honestly.")


# ----------------------------------------------------------------------------
# Parser invariants
# ----------------------------------------------------------------------------
class TestParser:
    def test_node_re_accepts_class_form(self):
        m = NODE_RE.match("tests/test_x.py::TestY::test_z")
        assert m is not None
        assert m["cls"] == "TestY" and m["func"] == "test_z"

    def test_node_re_accepts_function_form(self):
        m = NODE_RE.match("tests/test_x.py::test_z")
        assert m is not None
        assert m["cls"] is None and m["func"] == "test_z"

    def test_node_re_rejects_garbage(self):
        for bad in ("tests/x", "tests/test_x.py", "::test_y",
                    "tests/x.py::Cls::test::extra",
                    "tests/x.py::"):
            assert NODE_RE.match(bad) is None, bad

    def test_resolve_node_for_real_test(self):
        path, cls, fn = _resolve_node(
            "tests/test_nvme_mi.py::TestMctpEndpointId::test_eid_range_validation")
        assert path.exists()
        assert cls == "TestMctpEndpointId"
        assert fn == "test_eid_range_validation"

    def test_node_exists_returns_true_for_known_test(self):
        assert _node_exists(
            "tests/test_nvme_mi.py::TestMctpEndpointId::test_eid_range_validation")

    def test_node_exists_returns_false_for_typo(self):
        assert not _node_exists(
            "tests/test_nvme_mi.py::TestMctpEndpointId::test_does_not_exist")

    def test_node_exists_returns_false_for_missing_file(self):
        assert not _node_exists("tests/test_does_not_exist.py::test_x")
