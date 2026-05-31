"""Lane-margining result formatting + EqSweep summary, and the no-capability path,
driven through the mock backend. Complements test_diagnostics (the weak-lane case)."""
from computetest import margining
from computetest.backend import ECAP_LANE_MARGINING, MockBackend, MockDevice


def _be(**kw):
    return MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000,
                                   link_speed=4, link_width=16, **kw)])


# --- MarginResult properties / summary -------------------------------------- #
def test_margin_result_empty_lanes_is_unavailable():
    r = margining.MarginResult("0000:03:00.0", [])
    assert r.min_timing_ui == 0.0 and r.worst_lane is None and r.ok is False
    assert r.summary() == "0000:03:00.0: margining unavailable"


def test_clean_link_margin_ok_summary():
    m = margining.margin_link(_be(injected_ber=0.0), "0000:03:00.0")
    assert len(m.lanes) == 16 and m.ok
    s = m.summary()
    assert "min margin" in s and "16 lanes" in s and "-> OK" in s


def test_marginal_link_summary_names_worst_lane():
    # 0000:04:00.0's seeded per-lane variation puts one lane below the 0.25 UI limit.
    be = MockBackend([MockDevice("0000:04:00.0", 0x10DE, 0x2204, 0x030000,
                                 link_speed=4, link_width=16, injected_ber=1e-9)])
    m = margining.margin_link(be, "0000:04:00.0")
    assert not m.ok
    s = m.summary()
    assert "FAIL(lane" in s and "UI<" in s                # the failing lane + its margin
    w = m.worst_lane
    assert w is not None and w.timing_ui == m.min_timing_ui


def test_to_dict_rounds_and_keys_by_lane():
    m = margining.margin_link(_be(injected_ber=0.0), "0000:03:00.0")
    d = m.to_dict()
    assert d["bdf"] == "0000:03:00.0" and d["ok"] is True
    assert set(d["lanes"].keys()) == set(range(16))       # per-lane margins


def test_margin_link_without_capability_returns_empty():
    dev = MockDevice("0000:03:00.0", link_speed=4, link_width=16)
    dev._ext_caps.pop(ECAP_LANE_MARGINING)                # not Gen4+ / unsupported
    m = margining.margin_link(MockBackend([dev]), "0000:03:00.0")
    assert m.lanes == [] and not m.ok                     # honestly reported as unavailable


def test_margin_link_explicit_lane_count():
    m = margining.margin_link(_be(injected_ber=0.0), "0000:03:00.0", lanes=4)
    assert len(m.lanes) == 4                              # override the device width


def test_marginal_narrow_link_can_fail_within_width():
    # Regression: the seeded worst lane must land WITHIN the configured width, so a
    # marginal x4/x8 link can drop a lane below the limit (it used to mod-16 and so a
    # narrow link never saw the penalised lane -> false PASS). Scan widths; at least
    # one narrow width must produce a sub-limit lane on a marginal link, and every
    # reported lane index must be < the width.
    found_fail = False
    for w in (1, 2, 4, 8):
        be = MockBackend([MockDevice("0000:05:00.0", 0x10DE, 0x2204, 0x030000,
                                     link_speed=4, link_width=w, injected_ber=1e-9)])
        m = margining.margin_link(be, "0000:05:00.0", lanes=w)
        assert all(lm.lane < w for lm in m.lanes)         # never index past the width
        found_fail = found_fail or not m.ok
    assert found_fail, "no narrow width surfaced a sub-limit lane (worst-lane mask bug)"


# --- EqSweep summary -------------------------------------------------------- #
def test_eq_sweep_summary_marks_best_preset():
    be = MockBackend([MockDevice("0000:09:00.0", 0x1B36, 0x10, 0x088000, 4, 8, 4, 8,
                                 optimal_preset=7)])
    sweep = margining.characterize_equalization(be, "0000:09:00.0")
    s = sweep.summary()
    assert "eq sweep:" in s and "best preset = P7" in s
    assert "DOWN" in s                                     # far-off presets fail to train


def test_eq_sweep_no_usable_link_summary():
    sweep = margining.EqSweep("0000:09:00.0", [
        margining.EqPoint(0, 0, 0.0, link_up=False)])
    assert sweep.best is None
    assert "no preset produced a usable link" in sweep.summary()
