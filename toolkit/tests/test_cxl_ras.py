"""Sprint 4.4.2: CXL RAS UE/CE decode + write-1-to-clear (mirrors aer.py)."""
from __future__ import annotations

from computetest.cxl import (
    CxlRasRegisters,
    CxlRasSnapshot,
    clear_ras,
    decode_ce,
    decode_ue,
    snapshot,
)


class TestDecode:
    def test_ue_bits(self):
        names = [n for _, n, _ in decode_ue((1 << 10) | (1 << 7))]
        assert "PoisonReceived" in names and "MemDataECC" in names

    def test_ce_bits(self):
        names = [n for _, n, _ in decode_ce(1 << 6)]
        assert names == ["PhysicalLayerError"]

    def test_no_bits(self):
        assert decode_ue(0) == [] and decode_ce(0) == []


class TestSnapshot:
    def test_snapshot_decodes(self):
        regs = CxlRasRegisters(ue_status=(1 << 12), ce_status=(1 << 2))
        snap = snapshot(regs)
        assert isinstance(snap, CxlRasSnapshot)
        assert snap.has_uncorrectable and snap.has_correctable
        assert "InternalError" in [n for _, n, _ in snap.uncorrectable]
        assert "InternalError" in snap.to_dict()["uncorrectable"]


class TestW1C:
    def test_clear_clears_set_bits(self):
        regs = CxlRasRegisters(ue_status=(1 << 12) | (1 << 10), ce_status=(1 << 0))
        res = clear_ras(regs)
        assert res["uncorrectable"].ok and res["correctable"].ok
        assert res["uncorrectable"].cleared == (1 << 12) | (1 << 10)
        # after clearing, the registers read clean
        assert not snapshot(regs).has_uncorrectable

    def test_stuck_bit_reported(self):
        regs = CxlRasRegisters(ue_status=(1 << 12) | (1 << 13))
        regs.set_sticky(CxlRasRegisters.UE_STATUS, 1 << 13)   # IDETxError won't clear
        res = clear_ras(regs)
        assert not res["uncorrectable"].ok
        assert res["uncorrectable"].stuck == (1 << 13)

    def test_clear_clean_register_is_noop(self):
        res = clear_ras(CxlRasRegisters())
        assert res["uncorrectable"].cleared == 0 and res["uncorrectable"].ok
