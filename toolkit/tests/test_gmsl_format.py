"""GmslHealth / GmslDeserHealth summary() + to_dict() formatting (mock-driven)."""
from computetest import gmsl


def test_single_link_summary_and_to_dict_ok():
    h = gmsl.check_gmsl("1-0029")
    s = h.summary()
    assert "OK" in s and "lock=True" in s and "1920x1080" in s and "frames=5" in s
    d = h.to_dict()
    assert d["ok"] is True and d["resolution"] == "1920x1080" and d["error_count"] == 0


def test_single_link_summary_lists_failed_checks():
    h = gmsl.check_gmsl("1-BAD")
    s = h.summary()
    assert "FAIL(" in s and "link_locked" in s            # names the failed check(s)
    assert h.to_dict()["ok"] is False


def test_deserializer_summary_counts_locked_links():
    d = gmsl.check_deserializer("1-0029", n_links=4)
    s = d.summary()
    assert "4/4 links locked" in s and "sync" in s and "-> OK" in s
    # The per-link summaries are appended under the header.
    assert s.count("\n    ") == 4


def test_deserializer_summary_shows_desync_and_fail():
    d = gmsl.check_deserializer("1-0029-DESYNC", n_links=4)
    s = d.summary()
    assert "DESYNC" in s and "-> FAIL" in s
    dd = d.to_dict()
    assert dd["frame_sync_ok"] is False and dd["ok"] is False and len(dd["links"]) == 4


def test_deserializer_one_link_down_summary():
    d = gmsl.check_deserializer("1-0029-BAD", n_links=4)
    s = d.summary()
    assert "3/4 links locked" in s and "-> FAIL" in s     # link 0 down


def test_deserializer_empty_is_not_ok():
    d = gmsl.check_deserializer("1-0029", n_links=0)
    assert d.links == [] and d.ok is False                # no links -> not ok
