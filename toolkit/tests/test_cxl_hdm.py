"""Sprint 4.4.6: CXL HDM decoder programming + interleave."""
from __future__ import annotations

from computetest.cxl import HdmDecoder, HdmHealth, calc_interleave_pos, check_hdm_decoder


class TestInterleavePos:
    def test_nested_position(self):
        # root (pos 1, ways 2) then endpoint (pos 0, ways 2): 0*2+1 -> *2+0 = 2
        assert calc_interleave_pos([(1, 2), (0, 2)]) == 2

    def test_single_level(self):
        assert calc_interleave_pos([(3, 4)]) == 3

    def test_empty_chain_is_zero(self):
        assert calc_interleave_pos([]) == 0


class TestHpaToDpa:
    def _dec(self):
        return HdmDecoder(interleave_ways=2, interleave_granularity=256,
                          base_hpa=0x1000, size=0x1000, dpa_base=0)

    def test_position_0_handles_even_chunks(self):
        d = self._dec()
        assert d.hpa_to_dpa(0x1000, position=0) == 0          # chunk 0 -> dpa 0
        assert d.hpa_to_dpa(0x1200, position=0) == 256        # chunk 2 -> dpa chunk 1
        assert d.hpa_to_dpa(0x1100, position=0) is None       # chunk 1 -> position 1

    def test_position_1_handles_odd_chunks(self):
        d = self._dec()
        assert d.hpa_to_dpa(0x1100, position=1) == 0          # chunk 1 -> dpa 0
        assert d.hpa_to_dpa(0x1000, position=1) is None

    def test_out_of_window_is_none(self):
        assert self._dec().hpa_to_dpa(0x9999, position=0) is None


class TestCheckDecoder:
    def test_matching_decoder_passes(self):
        d = HdmDecoder(interleave_ways=4, interleave_granularity=512,
                       base_hpa=0, size=0x1000)
        h = check_hdm_decoder(d, expect_ways=4, expect_granularity=512)
        assert isinstance(h, HdmHealth) and h.ok

    def test_wrong_ways_or_uncommitted_fails(self):
        d = HdmDecoder(interleave_ways=2, interleave_granularity=512,
                       base_hpa=0, size=0x1000)
        assert not check_hdm_decoder(d, expect_ways=4, expect_granularity=512).ok
        assert not check_hdm_decoder(d, expect_ways=2, expect_granularity=512,
                                     committed=False).ok

    def test_summary_and_to_dict(self):
        d = HdmDecoder(interleave_ways=2, interleave_granularity=256,
                       base_hpa=0, size=0x1000)
        h = check_hdm_decoder(d, expect_ways=2, expect_granularity=256)
        assert "CXL HDM" in h.summary()
        assert h.to_dict()["interleave_ways"] == 2
