"""
Sprint 4.4.12: CI gate for ``docs/cxl/cxl_cts_coverage_matrix.md``.

Every CXL ``Mandatory`` row's ``pytest_node`` must resolve to a real test OR be
explicitly ``_scheduled_`` debt. Unlike the other Sprint-4 gates, CXL's
scheduled-debt budget starts non-zero (the CTS is member-distributed) and pays
down per sprint -- the Sprint 3.3 NVMe-MI playbook. Cloned from the v25 gate.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).parent.parent
MATRIX = TESTS_ROOT / "docs" / "cxl" / "cxl_cts_coverage_matrix.md"
NODE_RE = re.compile(r"^(?P<path>[\w./-]+\.py)::(?:(?P<cls>\w+)::)?(?P<func>\w+)$")
SCHEDULED_MARKERS = {"_scheduled_"}

# Start high, pay down: lower this by one each time a _scheduled_ row gets a real
# pytest_node. Never raise it -- a regression in coverage debt is a real signal.
EXPECTED_SCHEDULED_BUDGET = 2


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


class TestCxlCtsGate:
    def test_parses_rows(self, matrix_rows):
        assert len(matrix_rows) > 0

    def test_mandatory_nodes_non_empty(self, matrix_rows):
        bad = [r for r in matrix_rows
               if r["type"] == "Mandatory" and not r["pytest_node"]]
        assert not bad, [r["test_id"] for r in bad]

    def test_mandatory_nodes_resolve_or_scheduled(self, matrix_rows):
        failures = [f"{r['test_id']} -> {r['pytest_node']}"
                    for r in matrix_rows
                    if r["type"] == "Mandatory"
                    and not any(r["pytest_node"].startswith(m) for m in SCHEDULED_MARKERS)
                    and not _node_exists(r["pytest_node"])]
        assert not failures, "unresolvable:\n  " + "\n  ".join(failures)

    def test_scheduled_within_budget(self, matrix_rows):
        scheduled = [r for r in matrix_rows
                     if r["type"] == "Mandatory"
                     and any(r["pytest_node"].startswith(m) for m in SCHEDULED_MARKERS)]
        assert len(scheduled) <= EXPECTED_SCHEDULED_BUDGET, (
            f"CXL coverage debt grew: {len(scheduled)} scheduled rows "
            f"exceeds budget {EXPECTED_SCHEDULED_BUDGET}")


class TestCxlCtsParser:
    def test_resolves_a_real_node(self):
        assert _node_exists(
            "tests/test_cxl_mailbox.py::TestMockMailbox::test_queued_response_round_trip")

    def test_rejects_garbage(self):
        assert not _node_exists("tests/test_cxl_mailbox.py::Nope::test_x")
        assert not _node_exists("tests/x.py::")
