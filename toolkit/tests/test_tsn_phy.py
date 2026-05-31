"""Sprint 4.2.1: automotive-Ethernet PHY abstraction (EthPhy + MockPhy)."""
from __future__ import annotations

import pytest

from computetest.tsn import (
    EthPhy,
    MasterSlave,
    MockPhy,
    PhyError,
    PhyInfo,
    PhyPrbsPattern,
    PhyPrbsResult,
    TdrResult,
)


class TestContract:
    def test_ethphy_is_abstract(self):
        with pytest.raises(TypeError):
            EthPhy()                                       # type: ignore[abstract]

    def test_role_enum(self):
        assert MasterSlave.MASTER.value == "master"
        assert MasterSlave.SLAVE.value == "slave"


class TestMockPhy:
    def test_default_info(self):
        info = MockPhy().info()
        assert isinstance(info, PhyInfo)
        assert info.speed_mbps == 1000 and info.role == MasterSlave.MASTER
        assert info.to_dict()["role"] == "master"

    def test_sqi_readback_and_floor(self):
        assert MockPhy(injected_sqi=7).sqi_ok()
        assert not MockPhy(injected_sqi=3).sqi_ok()       # below default floor 5

    def test_sqi_out_of_range_raises(self):
        with pytest.raises(PhyError, match="out of range"):
            MockPhy(injected_sqi=9).read_sqi()

    def test_linkup_budget(self):
        assert MockPhy(injected_linkup_ms=20.0).linkup_ok()
        assert not MockPhy(injected_linkup_ms=500.0).linkup_ok()

    def test_set_thresholds_override(self):
        p = MockPhy(injected_sqi=3, injected_linkup_ms=500.0)
        assert not p.sqi_ok() and not p.linkup_ok()
        p.set_thresholds(sqi_min=2, max_linkup_ms=600.0)
        assert p.sqi_ok() and p.linkup_ok()

    def test_set_thresholds_partial(self):
        p = MockPhy(injected_sqi=3, injected_linkup_ms=500.0)
        p.set_thresholds(sqi_min=2)                       # only sqi_min
        assert p.sqi_ok() and not p.linkup_ok()
        p.set_thresholds(max_linkup_ms=600.0)             # only max_linkup_ms
        assert p.linkup_ok()

    def test_role(self):
        assert MockPhy(role=MasterSlave.SLAVE).master_slave_role() == MasterSlave.SLAVE

    def test_tdr_clean_and_fault(self):
        clean = MockPhy().run_tdr()
        assert isinstance(clean, TdrResult) and clean.ok and clean.status == "ok"
        fault = MockPhy(injected_tdr_fault=True).run_tdr()
        assert not fault.ok and fault.faults[0]["code"] == "open"
        assert fault.to_dict()["ok"] is False

    def test_tdr_ok_is_failclosed_whitelist(self):
        # 'skipped' is a deliberate non-failure; any unrecognized status from a
        # real backend must fail closed (ok is a whitelist, not !="fault").
        assert TdrResult(status="skipped").ok is True
        assert TdrResult(status="error").ok is False
        assert TdrResult(status="").ok is False

    def test_prbs_bist(self):
        r = MockPhy(speed_mbps=1000).run_prbs_bist(duration_s=1.0)
        assert isinstance(r, PhyPrbsResult)
        assert r.bits == 1000 * 1e6 and r.locked
        assert r.to_dict()["pattern"] == "PRBS31"

    def test_prbs_explicit_pattern(self):
        r = MockPhy().run_prbs_bist(pattern=PhyPrbsPattern.PRBS15)
        assert r.pattern == PhyPrbsPattern.PRBS15

    def test_prbs_high_errors_unlock(self):
        assert not MockPhy(injected_prbs_errors=2_000_000).run_prbs_bist().locked

    def test_prbs_zero_duration_rejected(self):
        with pytest.raises(PhyError, match="duration must be > 0"):
            MockPhy().run_prbs_bist(duration_s=0.0)
