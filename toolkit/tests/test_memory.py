from computetest import memory


def test_clean_memory_passes():
    h = memory.check_memory()
    assert h.ok and h.total_ue == 0 and not h.history


def test_uncorrectable_fails():
    h = memory.check_memory(fault="ue")
    assert h.total_ue == 2 and h.checks["no_uncorrectable"] is False and not h.ok


def test_ce_concentration_fails_and_attributes_to_dimm():
    h = memory.check_memory(fault="ce")
    assert not h.ok
    assert h.worst_dimm == "DIMM_A1"
    assert any("DIMM_A1" in x for x in h.history)   # actionable: swap that stick
