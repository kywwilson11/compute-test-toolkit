"""Sprint 4.4.5: CXL poison list + poison/viral containment."""
from __future__ import annotations

from computetest.cxl import (
    ContainmentHealth,
    PoisonHealth,
    PoisonList,
    PoisonSource,
    check_containment,
    check_poison_inject_clear,
)


class TestPoisonList:
    def test_inject_list_clear_round_trip(self):
        h = check_poison_inject_clear(PoisonList(), dpa=0x40000, length=64)
        assert isinstance(h, PoisonHealth) and h.ok
        assert h.checks == {"poison_listed_with_dpa_length": True,
                            "poison_cleared": True}

    def test_entry_records_source(self):
        plist = PoisonList()
        plist.inject(0x1000, 64, source=PoisonSource.INTERNAL)
        entry = plist.get_list()[0]
        assert entry.source == PoisonSource.INTERNAL
        assert entry.to_dict()["source"] == "INTERNAL"

    def test_clear_only_targeted_dpa(self):
        plist = PoisonList()
        plist.inject(0x1000, 64)
        plist.inject(0x2000, 64)
        plist.clear(0x1000)
        assert {e.dpa for e in plist.get_list()} == {0x2000}

    def test_summary_and_to_dict(self):
        h = check_poison_inject_clear(PoisonList(), dpa=0x500, length=32)
        assert "CXL poison" in h.summary()
        assert h.to_dict()["length"] == 32


class TestContainment:
    def test_viral_present_and_asserts(self):
        h = check_containment(viral_supported=True,
                              viral_asserts_on_uncorrectable=True,
                              dpc_fallback_available=False)
        assert isinstance(h, ContainmentHealth) and h.ok
        assert h.to_dict()["ok"] is True

    def test_viral_supported_but_not_asserting_fails(self):
        h = check_containment(viral_supported=True,
                              viral_asserts_on_uncorrectable=False,
                              dpc_fallback_available=True)
        assert not h.ok and h.checks["viral_asserts_when_supported"] is False

    def test_dpc_fallback_when_no_viral(self):
        h = check_containment(viral_supported=False,
                              viral_asserts_on_uncorrectable=False,
                              dpc_fallback_available=True)
        assert h.ok

    def test_no_containment_fails(self):
        h = check_containment(viral_supported=False,
                              viral_asserts_on_uncorrectable=False,
                              dpc_fallback_available=False)
        assert not h.ok and h.checks["containment_present"] is False
        assert "CXL containment" in h.summary()
