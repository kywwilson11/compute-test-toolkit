"""MemoryHealth summary()/to_dict()/worst_dimm + stress_memory mock (EDAC analog)."""
from computetest import memory


def test_summary_clean_memory():
    h = memory.check_memory()
    s = h.summary()
    assert "memory: 1 mc" in s and "CE=0 UE=0" in s and "-> OK" in s
    assert "worst=" not in s                              # no errors -> no worst-DIMM note


def test_summary_ce_concentration_shows_worst_dimm():
    h = memory.check_memory(fault="ce")
    s = h.summary()
    assert "-> FAIL(" in s and "worst=DIMM_A1(150)" in s  # the hot stick, named + count


def test_summary_uncorrectable_fails():
    h = memory.check_memory(fault="ue")
    s = h.summary()
    assert "UE=2" in s and "-> FAIL(" in s and "no_uncorrectable" in s


def test_to_dict_shape_and_worst_dimm_property():
    h = memory.check_memory(fault="ce")
    assert h.worst_dimm == "DIMM_A1"
    d = h.to_dict()
    assert d["total_ce"] == 150 and d["ok"] is False and d["per_dimm"]["DIMM_A1"] == 150


def test_worst_dimm_none_when_no_dimms():
    h = memory.MemoryHealth(0, 0, 0, {})
    assert h.worst_dimm is None
    assert "worst=" not in h.summary()


def test_stress_memory_mock_returns_true():
    assert memory.stress_memory(seconds=1, mock=True) is True
