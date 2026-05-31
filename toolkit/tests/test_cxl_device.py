"""Sprint 4.4.8: CXL coherency class + device-management lifecycle."""
from __future__ import annotations

from computetest.cxl import (
    CoherencyHealth,
    CxlDeviceType,
    FwLifecycleHealth,
    check_coherency,
    check_media_ready_contract,
)


class TestCoherency:
    def test_type3_mem_only_consistent(self):
        h = check_coherency(device_type=CxlDeviceType.TYPE3, claims_cache=False,
                            claims_mem=True, hdm_d_capable=False,
                            bi_snoop_capable=False)
        assert isinstance(h, CoherencyHealth) and h.ok

    def test_type3_claiming_cache_fails(self):
        h = check_coherency(device_type=CxlDeviceType.TYPE3, claims_cache=True,
                            claims_mem=True, hdm_d_capable=False,
                            bi_snoop_capable=False)
        assert not h.ok and h.checks["cache_claim_consistent"] is False

    def test_type2_needs_coherency_bridging(self):
        ok = check_coherency(device_type=CxlDeviceType.TYPE2, claims_cache=True,
                             claims_mem=True, hdm_d_capable=True,
                             bi_snoop_capable=True)
        assert ok.ok
        bad = check_coherency(device_type=CxlDeviceType.TYPE2, claims_cache=True,
                              claims_mem=True, hdm_d_capable=False,
                              bi_snoop_capable=True)
        assert not bad.ok and bad.checks["coherency_bridging"] is False

    def test_type1_cache_only(self):
        # A conformant Type-1 (cache only, no device memory) has NO HDM-D[B]/BISnoop;
        # it must still pass. (This would FAIL under the old cache-gated logic.)
        h = check_coherency(device_type=CxlDeviceType.TYPE1, claims_cache=True,
                            claims_mem=False, hdm_d_capable=False,
                            bi_snoop_capable=False)
        assert h.ok and h.checks["coherency_bridging"] is True

    def test_summary_and_to_dict(self):
        h = check_coherency(device_type=CxlDeviceType.TYPE3, claims_cache=False,
                            claims_mem=True, hdm_d_capable=False,
                            bi_snoop_capable=False)
        assert "Type3" in h.summary()
        assert h.to_dict()["device_type"] == 3


class TestMediaReadyContract:
    def test_well_behaved_lifecycle(self):
        h = check_media_ready_contract(background_started=True,
                                       media_ready_during=False,
                                       media_ready_after=True)
        assert isinstance(h, FwLifecycleHealth) and h.ok

    def test_media_ready_during_fails(self):
        h = check_media_ready_contract(background_started=True,
                                       media_ready_during=True,
                                       media_ready_after=True)
        assert not h.ok and h.checks["media_not_ready_during"] is False

    def test_summary(self):
        h = check_media_ready_contract(background_started=False,
                                       media_ready_during=False,
                                       media_ready_after=True)
        assert "lifecycle" in h.summary() and not h.ok
        assert h.to_dict()["ok"] is False
