"""Sprint 4.3.4: DDR5 Post-Package Repair (PPR) self-heal."""
from __future__ import annotations

import pytest

from computetest.ddr5 import Ddr5RasError, MockDdr5Ras, PprHealth, check_ppr


class TestPpr:
    def test_sppr_reverts_on_power_cycle(self):
        h = check_ppr(MockDdr5Ras(), addr=0x1000, persist_mode=0)
        assert isinstance(h, PprHealth) and h.ok and h.mode == "sPPR"
        assert h.checks == {"repaired": True, "reverts_on_power_cycle": True}

    def test_hppr_survives_power_cycle(self):
        h = check_ppr(MockDdr5Ras(), addr=0x2000, persist_mode=1)
        assert h.ok and h.mode == "hPPR"
        assert h.checks == {"repaired": True, "survives_power_cycle": True,
                            "hppr_resource_advertised": True}

    def test_hppr_without_resources_fails(self):
        h = check_ppr(MockDdr5Ras(injected_hppr_resources=0), addr=0x3000,
                      persist_mode=1)
        assert not h.ok and h.checks["hppr_resource_advertised"] is False
        assert "FAIL(" in h.summary()

    def test_summary_and_to_dict(self):
        h = check_ppr(MockDdr5Ras(), addr=0x4000, persist_mode=1)
        assert "DDR5 PPR" in h.summary() and "0x4000" in h.summary()
        assert h.to_dict()["mode"] == "hPPR"

    def test_invalid_persist_mode_rejected(self):
        # Linux mem_repairX persist_mode is strictly 0/1; an out-of-domain value
        # must raise, not be silently treated as sPPR and PASS.
        with pytest.raises(Ddr5RasError, match="persist_mode"):
            check_ppr(MockDdr5Ras(), addr=0x1000, persist_mode=2)
