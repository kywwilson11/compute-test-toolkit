"""
Sprint 4.1.1: GMSL3 SerDes link abstraction — vendor-agnostic contract + Mock.

Pins the public surface (``SerDesLink`` ABC + ``MockSerDes``) so a real
ADI-register backend (MAX96793/MAX96792A) can drop in behind it, and locks the
GMSL-specific behaviour the higher checks rely on: mode-aware lock, PAM4
three-eye EOM, forward-vs-reverse PRBS, FEC counters, and the safety
error/ERRB surface.
"""
from __future__ import annotations

import pytest

from computetest.gmsl import (
    EomReading,
    ErrorCounters,
    FecStats,
    GmslMode,
    GmslPrbsPattern,
    LinkDirection,
    LinkLock,
    MockSerDes,
    PrbsResult,
    SerDesError,
    SerDesInfo,
    SerDesLink,
    SerDesRole,
)


class TestContract:
    def test_serdeslink_is_abstract(self):
        with pytest.raises(TypeError):
            SerDesLink()                                  # type: ignore[abstract]

    def test_mode_enum_values(self):
        assert GmslMode.PAM4_12G.value == "GMSL3-PAM4-12G"
        assert GmslMode.NRZ_6G.value == "GMSL3-NRZ-6G"

    def test_direction_enum_values(self):
        assert LinkDirection.FORWARD.value == "forward"
        assert LinkDirection.REVERSE.value == "reverse"

    def test_prbs_pattern_includes_prbs24(self):
        # PRBS24 is the GMSL on-die pattern; as of Sprint 4.1.10 it is also in
        # the external-BERT set so a bench BERT can correlate against it.
        assert GmslPrbsPattern.PRBS24.value == "PRBS24"
        assert GmslPrbsPattern.PRBS31.value == "PRBS31"


class TestIdentity:
    def test_default_info_is_dual_link_deserializer(self):
        info = MockSerDes().info()
        assert isinstance(info, SerDesInfo)
        assert info.role == SerDesRole.DESERIALIZER
        assert info.links == 2                            # MAX96792A dual-link
        assert info.max_mode == GmslMode.PAM4_12G

    def test_info_to_dict_round_trip(self):
        s = MockSerDes(vendor="V", part_number="P", role=SerDesRole.SERIALIZER,
                       serial="SN", links=1)
        d = s.info().to_dict()
        assert d["vendor"] == "V" and d["role"] == "serializer" and d["links"] == 1


class TestLockAndMode:
    def test_all_links_locked_in_top_mode(self):
        s = MockSerDes(links=4)
        locks = s.lock_status()
        assert len(locks) == 4
        assert all(isinstance(li, LinkLock) for li in locks)
        assert s.all_locked()
        assert s.all_in_mode(GmslMode.PAM4_12G)
        assert locks[0].lock_time_ms is not None

    def test_degraded_mode_locks_but_fails_mode_check(self):
        # The GMSL3 defect: locked, but negotiated DOWN to NRZ-6G.
        s = MockSerDes(injected_mode=GmslMode.NRZ_6G)
        assert s.all_locked()                             # link is "up"...
        assert not s.all_in_mode(GmslMode.PAM4_12G)       # ...but not at design rate
        assert s.negotiated_mode(0) == GmslMode.NRZ_6G

    def test_unlocked_reports_unknown_mode(self):
        s = MockSerDes(injected_locked=False)
        assert not s.all_locked()
        assert not s.all_in_mode(GmslMode.PAM4_12G)
        assert s.negotiated_mode(0) == GmslMode.UNKNOWN
        assert s.lock_status()[0].lock_time_ms is None

    def test_negotiated_mode_missing_link_raises(self):
        with pytest.raises(SerDesError, match="not present"):
            MockSerDes(links=2).negotiated_mode(5)

    def test_mode_ok_helper(self):
        good = LinkLock(link=0, locked=True, mode=GmslMode.PAM4_12G)
        assert good.mode_ok(GmslMode.PAM4_12G)
        assert not good.mode_ok(GmslMode.NRZ_6G)
        down = LinkLock(link=0, locked=False, mode=GmslMode.UNKNOWN)
        assert not down.mode_ok(GmslMode.PAM4_12G)

    def test_lock_to_dict(self):
        d = LinkLock(link=1, locked=True, mode=GmslMode.PAM4_12G).to_dict()
        assert d["link"] == 1 and d["mode"] == "GMSL3-PAM4-12G"


class TestEom:
    def test_pam4_forward_has_three_subeyes(self):
        eom = MockSerDes(injected_mode=GmslMode.PAM4_12G).read_eom(0, LinkDirection.FORWARD)
        assert isinstance(eom, EomReading)
        assert len(eom.vertical_mv) == 3
        assert eom.worst_vertical_mv == min(eom.vertical_mv)

    def test_nrz_reverse_has_single_eye(self):
        eom = MockSerDes().read_eom(0, LinkDirection.REVERSE)
        assert len(eom.vertical_mv) == 1

    def test_non_pam4_forward_single_eye(self):
        eom = MockSerDes(injected_mode=GmslMode.GMSL2_6G).read_eom(0, LinkDirection.FORWARD)
        assert len(eom.vertical_mv) == 1

    def test_eom_out_of_range_link_raises(self):
        s = MockSerDes(links=2)
        with pytest.raises(SerDesError, match="out of range"):
            s.read_eom(9)
        with pytest.raises(SerDesError, match="out of range"):
            s.read_eom(-1)

    def test_eom_on_unlocked_link_raises(self):
        with pytest.raises(SerDesError, match="not locked"):
            MockSerDes(injected_locked=False).read_eom(0)

    def test_eom_deterministic_per_serial(self):
        a = MockSerDes(serial="SN-A")
        b = MockSerDes(serial="SN-A")
        assert a.read_eom(0).vertical_mv == b.read_eom(0).vertical_mv

    def test_eom_verdict_thresholds(self):
        s = MockSerDes(injected_eye_mv=70.0, injected_eye_ui=0.30)
        assert s.eom_verdict(s.read_eom(0))
        weak = MockSerDes(injected_eye_mv=10.0, injected_eye_ui=0.30)
        assert not weak.eom_verdict(weak.read_eom(0))

    def test_set_eom_mv_threshold_override(self):
        s = MockSerDes(injected_eye_mv=30.0, injected_eye_ui=0.30)
        assert not s.eom_verdict(s.read_eom(0))           # below default 40 mV
        s.set_eom_thresholds(mv_min=10.0)
        assert s.eom_verdict(s.read_eom(0))

    def test_set_eom_ui_threshold_override(self):
        # Healthy eye height but a narrow horizontal opening: fails on UI until
        # the UI floor is relaxed (exercises the ui_min-only override path).
        s = MockSerDes(injected_eye_mv=70.0, injected_eye_ui=0.10)
        assert not s.eom_verdict(s.read_eom(0))           # 0.10 UI below default 0.20
        s.set_eom_thresholds(ui_min=0.05)
        assert s.eom_verdict(s.read_eom(0))

    def test_eom_to_dict(self):
        d = MockSerDes().read_eom(0).to_dict()
        assert "worst_vertical_mv" in d and d["mode"] == "GMSL3-PAM4-12G"


class TestPrbs:
    def test_forward_pam4_bits_exceed_reverse(self):
        s = MockSerDes()
        fwd = s.run_prbs_bist(LinkDirection.FORWARD, duration_s=1.0)
        rev = s.run_prbs_bist(LinkDirection.REVERSE, duration_s=1.0)
        assert isinstance(fwd, PrbsResult)
        assert fwd.bits > rev.bits                        # 12 Gbps vs 187.5 Mbps

    def test_nrz_fallback_forward_bits_below_pam4(self):
        pam4 = MockSerDes().run_prbs_bist(LinkDirection.FORWARD, duration_s=1.0)
        nrz = MockSerDes(injected_mode=GmslMode.NRZ_6G).run_prbs_bist(
            LinkDirection.FORWARD, duration_s=1.0)
        assert nrz.bits < pam4.bits                       # 6 Gbps NRZ fallback

    def test_clean_link_zero_errors_locked(self):
        r = MockSerDes(injected_prbs_errors=0).run_prbs_bist()
        assert r.error_count == 0 and r.locked

    def test_high_error_count_unlocks(self):
        assert not MockSerDes(injected_prbs_errors=2_000_000).run_prbs_bist().locked

    def test_zero_duration_rejected(self):
        with pytest.raises(SerDesError, match="duration must be > 0"):
            MockSerDes().run_prbs_bist(duration_s=0.0)

    def test_prbs_out_of_range_link_raises(self):
        with pytest.raises(SerDesError, match="out of range"):
            MockSerDes(links=1).run_prbs_bist(link=3)

    def test_prbs_to_dict(self):
        d = MockSerDes().run_prbs_bist(pattern=GmslPrbsPattern.PRBS24).to_dict()
        assert d["pattern"] == "PRBS24" and d["bits"] > 0


class TestFec:
    def test_clean_fec(self):
        f = MockSerDes(injected_fec_corrected=10).read_fec_stats(0)
        assert isinstance(f, FecStats)
        assert f.corrected_symbols == 10 and f.clean

    def test_uncorrectable_block_not_clean(self):
        assert not MockSerDes(injected_fec_uncorrectable=1).read_fec_stats(0).clean

    def test_fec_out_of_range_raises(self):
        with pytest.raises(SerDesError, match="out of range"):
            MockSerDes(links=2).read_fec_stats(7)

    def test_fec_to_dict(self):
        d = MockSerDes(injected_fec_corrected=3).read_fec_stats(0).to_dict()
        assert d["corrected_symbols"] == 3 and d["clean"] is True


class TestErrorCountersAndErrb:
    def test_default_counters_clean(self):
        s = MockSerDes()
        c = s.read_error_counters(0)
        assert isinstance(c, ErrorCounters)
        assert not c.any_error
        assert not s.errb_asserted()

    def test_injected_counters_assert_errb(self):
        s = MockSerDes(injected_error_counters=ErrorCounters(link=0, line_fault=True))
        c = s.read_error_counters(1)
        assert c.link == 1 and c.line_fault              # re-stamped to queried link
        assert c.any_error
        assert s.errb_asserted()

    def test_video_crc_counts_as_error(self):
        s = MockSerDes(injected_error_counters=ErrorCounters(link=0, video_crc_errors=4))
        assert s.read_error_counters(0).any_error

    def test_counters_to_dict(self):
        d = ErrorCounters(link=0, decoding_errors=2).to_dict()
        assert d["decoding_errors"] == 2 and d["any_error"] is True


class TestLoopbackAndRelock:
    def test_loopback_per_direction(self):
        s = MockSerDes()
        assert not s.loopback_state(LinkDirection.FORWARD)
        s.set_loopback(True, direction=LinkDirection.FORWARD)
        assert s.loopback_state(LinkDirection.FORWARD)
        assert not s.loopback_state(LinkDirection.REVERSE)

    def test_force_relock_increments(self):
        s = MockSerDes()
        assert s.relock_count == 0
        s.force_relock()
        s.force_relock(0)
        assert s.relock_count == 2

    def test_force_relock_bad_link_raises(self):
        with pytest.raises(SerDesError, match="out of range"):
            MockSerDes(links=2).force_relock(9)


class TestPublicSurface:
    """Lock the package __all__ against the public namespace it re-exports.

    The gmsl/cxl/nvme.mi __init__ files re-export their subpackage symbols; the
    DEFAULT_* tuning constants are part of that contract. Because the gmsl
    imports use ``# noqa: F401``, ruff's re-export rule does NOT enforce __all__
    completeness for them, so this test is the guard.
    """

    def test_gmsl_exports_default_tuning_constants(self):
        import computetest.gmsl as gmsl
        for name in ("DEFAULT_BIST_DURATION_S", "DEFAULT_EOM_MV_MIN",
                     "DEFAULT_EOM_UI_MIN", "DEFAULT_MAX_LOCK_MS",
                     "DEFAULT_PRE_FEC_TARGET_BER"):
            assert name in gmsl.__all__, f"{name} missing from gmsl.__all__"
            assert hasattr(gmsl, name), f"{name} not importable from gmsl"

    @pytest.mark.parametrize("modname",
                             ["computetest.gmsl", "computetest.cxl",
                              "computetest.nvme.mi"])
    def test_all_matches_public_namespace(self, modname):
        import importlib
        import types
        mod = importlib.import_module(modname)
        exported = set(mod.__all__)
        # public, non-dunder names that are not submodules
        public = {k for k, v in vars(mod).items()
                  if not k.startswith("_") and not isinstance(v, types.ModuleType)}
        missing = public - exported
        dangling = exported - set(vars(mod))
        assert not missing, f"{modname}: public names absent from __all__: {sorted(missing)}"
        assert not dangling, f"{modname}: __all__ names not importable: {sorted(dangling)}"
        assert len(mod.__all__) == len(exported), f"{modname}: __all__ has duplicates"
