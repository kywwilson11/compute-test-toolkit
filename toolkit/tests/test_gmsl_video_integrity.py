"""
Sprint 4.1.9: GMSL CSI-2 video + tunneled control-channel integrity.

A locked GMSL link can still deliver corrupt or mis-mapped CSI-2, so video-CRC
and the VC/data-type map are checked separately from lock; the control channel
is checked for CRC errors, sequence-number gaps, and ARQ retransmits.
"""
from __future__ import annotations

from computetest.gmsl import (
    ControlChannelStats,
    ControlHealth,
    MockSerDes,
    VideoHealth,
    VideoStats,
    check_control_channel,
    check_video_integrity,
)


class TestVideoIntegrity:
    def test_clean_video_passes(self):
        h = check_video_integrity(MockSerDes())          # default DT 0x2C, VC 0
        assert isinstance(h, VideoHealth)
        assert h.ok
        assert h.checks == {"no_video_crc_errors": True, "vc_ok": True,
                            "data_type_ok": True, "frames_flowing": True}

    def test_video_crc_errors_fail(self):
        h = check_video_integrity(MockSerDes(injected_video_crc_errors=4))
        assert not h.ok and h.checks["no_video_crc_errors"] is False

    def test_wrong_vc_or_data_type_fail(self):
        h = check_video_integrity(MockSerDes(injected_video_vc=1))
        assert not h.ok and h.checks["vc_ok"] is False
        h2 = check_video_integrity(MockSerDes(injected_video_dt=0x1E))
        assert not h2.ok and h2.checks["data_type_ok"] is False

    def test_no_frames_fail(self):
        h = check_video_integrity(MockSerDes(injected_frames=0))
        assert not h.ok and h.checks["frames_flowing"] is False

    def test_expect_overrides_match_sensor(self):
        h = check_video_integrity(
            MockSerDes(injected_video_vc=2, injected_video_dt=0x1E),
            expect_vc=2, expect_data_type=0x1E)
        assert h.ok

    def test_video_summary_to_dict_and_raw_stats(self):
        h = check_video_integrity(MockSerDes(injected_video_crc_errors=1))
        assert "GMSL video" in h.summary()
        assert h.to_dict()["video_crc_errors"] == 1
        vs = MockSerDes().read_video_stats(0)
        assert isinstance(vs, VideoStats) and vs.data_type == 0x2C
        assert vs.to_dict()["data_type"] == 0x2C


class TestControlChannel:
    def test_clean_control_passes(self):
        h = check_control_channel(MockSerDes())
        assert isinstance(h, ControlHealth)
        assert h.ok
        assert h.checks == {"no_control_crc_errors": True, "no_sequence_gaps": True,
                            "arq_within_budget": True}

    def test_crc_or_seq_gap_fail(self):
        assert not check_control_channel(MockSerDes(injected_ctrl_crc_errors=2)).ok
        assert not check_control_channel(MockSerDes(injected_seq_gaps=1)).ok

    def test_arq_budget(self):
        s = MockSerDes(injected_arq_retransmits=3)
        assert not check_control_channel(s, max_arq=0).ok        # over budget
        assert check_control_channel(s, max_arq=5).ok            # within budget

    def test_control_summary_to_dict_and_raw_stats(self):
        h = check_control_channel(MockSerDes(injected_arq_retransmits=2), max_arq=1)
        assert "GMSL control" in h.summary()
        d = h.to_dict()
        assert d["arq_retransmits"] == 2 and d["max_arq"] == 1
        cs = MockSerDes().read_control_channel_stats()
        assert isinstance(cs, ControlChannelStats) and cs.clean
        assert cs.to_dict()["clean"] is True
