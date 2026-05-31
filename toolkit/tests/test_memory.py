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


def test_concentration_check_not_silent_noop_when_per_dimm_empty():
    # Controller reports CE but no per-DIMM attribution (e.g. legacy-EDAC csrow
    # counters we couldn't map): the concentration gate must FAIL, not silently pass.
    key = "no_ce_concentration(<=20/dimm)"
    checks = memory._limits(total_ce=5, total_ue=0, per_dimm={},
                            max_ce_total=100, max_ce_per_dimm=20)
    assert checks[key] is False
    # Sanity: zero CE with empty per_dimm is still a pass (nothing to attribute).
    clean = memory._limits(total_ce=0, total_ue=0, per_dimm={},
                           max_ce_total=100, max_ce_per_dimm=20)
    assert clean[key] is True
