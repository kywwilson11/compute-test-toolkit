"""LinkHealth properties + summary formatting, and the watch loop (retrains, min
speed/width, LBMS/LABS bandwidth-change latch) driven through the mock backend."""
from computetest import linkstate
from computetest.backend import MockBackend, MockDevice


def _be(**kw):
    return MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, **kw)])


# --- LinkHealth properties (constructed directly) --------------------------- #
def test_post_init_seeds_min_from_current():
    h = linkstate.LinkHealth("x", speed=4, width=16, max_speed=4, max_width=16)
    assert h.min_speed == 4 and h.min_width == 16        # seeded from current
    # Explicit mins are preserved (not overwritten by __post_init__).
    h2 = linkstate.LinkHealth("x", 4, 16, 4, 16, min_speed=2, min_width=8)
    assert h2.min_speed == 2 and h2.min_width == 8


def test_clean_link_ok_and_summary():
    h = linkstate.LinkHealth("0000:03:00.0", 4, 16, 4, 16)
    assert h.ok and not h.speed_degraded and not h.width_degraded
    s = h.summary()
    assert "OK" in s and "Gen4" in s and "x16" in s and "16GT/s" in s


def test_speed_degraded_summary_shows_min_and_target():
    # Ended Gen4 but dipped to Gen2 mid-soak, against an expected Gen4.
    h = linkstate.LinkHealth("0000:03:00.0", 4, 16, 4, 16, expected_speed=4, min_speed=2)
    assert h.speed_degraded and not h.ok
    s = h.summary()
    assert "DEGRADED" in s and "SPEED" in s and "min Gen2" in s and "<Gen4" in s


def test_width_degraded_summary_against_expectation():
    h = linkstate.LinkHealth("0000:03:00.0", 4, 8, 4, 16, expected_width=16, min_width=4)
    assert h.width_degraded
    s = h.summary()
    assert "WIDTH" in s and "x8" in s and "min x4" in s and "<x16" in s


def test_retrains_and_bw_change_flags_in_summary():
    h = linkstate.LinkHealth("0000:03:00.0", 4, 16, 4, 16, retrains=3, bw_changed=True)
    assert not h.ok
    s = h.summary()
    assert "RETRAINS=3" in s and "BW-CHANGE(LBMS/LABS)" in s


def test_to_dict_carries_degraded_and_ok():
    h = linkstate.LinkHealth("0000:03:00.0", 3, 16, 4, 16)   # Gen3 < max Gen4
    d = h.to_dict()
    assert d["speed_degraded"] is True and d["ok"] is False
    assert d["bdf"] == "0000:03:00.0" and d["min_speed"] == 3


# --- check_link: snapshot (no watch) ---------------------------------------- #
def test_check_link_no_watch_reports_current():
    h = linkstate.check_link(_be(link_speed=4, link_width=16, max_link_speed=4,
                                  max_link_width=16), "0000:03:00.0")
    assert h.poll_count == 0 and h.ok                    # watch_s=0 -> no polling


# --- check_link: watch loop -------------------------------------------------- #
def test_check_link_watch_catches_downtrain_and_bw_latch():
    be = _be(link_speed=4, link_width=16, max_link_speed=4, max_link_width=16)
    be.inject_downtrain("0000:03:00.0", per_sec=1e6)     # transient speed dip under load
    h = linkstate.check_link(be, "0000:03:00.0", expected_speed=4,
                             watch_s=0.05, poll_s=0.0)
    assert h.poll_count > 0
    assert h.bw_changed                                  # LBMS/LABS latched during watch
    assert h.min_speed < 4 and h.speed_degraded          # the dip was caught
    assert not h.ok


def test_check_link_watch_counts_retrains_on_rising_edge():
    be = _be(link_speed=4, link_width=16, max_link_speed=4, max_link_width=16,
             inject_retrains_per_sec=200)
    h = linkstate.check_link(be, "0000:03:00.0", watch_s=0.1, poll_s=0.001)
    assert h.retrains > 0 and not h.ok                   # entered Recovery during the soak


# --- FIX 4: unknown (0) speed/width = enumeration/parse failure, NOT a pass --- #
def test_zero_speed_is_unknown_and_flagged_not_silent_pass():
    # sysfs gave nothing: current=0, max=0, no expectation. The old `target = expected
    # or max = 0` made `speed < 0` False -> a silent pass. It must read as degraded.
    h = linkstate.LinkHealth("0000:03:00.0", speed=0, width=16, max_speed=0, max_width=16)
    assert h.speed_unknown and h.speed_degraded
    assert not h.ok                                      # an enumeration failure isn't healthy
    s = h.summary()
    assert "SPEED unknown" in s and "DEGRADED" in s
    assert h.to_dict()["speed_unknown"] is True and h.to_dict()["ok"] is False


def test_zero_width_is_unknown_and_flagged():
    h = linkstate.LinkHealth("0000:03:00.0", speed=4, width=0, max_speed=4, max_width=0)
    assert h.width_unknown and h.width_degraded and not h.ok
    assert "WIDTH unknown" in h.summary()


def test_fully_unknown_link_is_not_ok():
    # The exact regression: a totally failed read (all zeros) used to look healthy.
    h = linkstate.LinkHealth("0000:03:00.0", speed=0, width=0, max_speed=0, max_width=0)
    assert not h.ok and h.speed_unknown and h.width_unknown


def test_known_good_link_is_not_flagged_unknown():
    # A real Gen4 x16 link must NOT be mistaken for unknown.
    h = linkstate.LinkHealth("0000:03:00.0", 4, 16, 4, 16)
    assert not h.speed_unknown and not h.width_unknown and h.ok
