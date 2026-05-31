"""dmesg severity classification edges (link-down, fatal vs non-fatal) and the
DmesgEvent.involves() helper. Complements test_dmesg.py (parse/monitor/chain)."""
from computetest import dmesg


def test_severity_link_down_variants():
    assert dmesg._severity("pcieport 0000:00:1c.0: Link Down") == "link"
    assert dmesg._severity("nvme: link is down") == "link"


def test_severity_fatal_vs_non_fatal_disambiguation():
    assert dmesg._severity("AER: Uncorrected (Fatal) error received") == "fatal"
    # 'non-fatal' must NOT be classified as fatal even though it contains 'fatal'.
    assert dmesg._severity("AER: Uncorrected (Non-Fatal) error received") == "non_fatal"
    assert dmesg._severity("AER: Corrected error received") == "corrected"
    assert dmesg._severity("some other line") == "other"


def test_parse_classifies_link_down_event():
    evs = dmesg.parse_events("[1.0] pcieport 0000:00:1c.0: Link Down on 0000:04:00.0\n")
    assert len(evs) == 1 and evs[0].severity == "link"
    assert not evs[0].uncorrectable                       # link-down isn't 'uncorrectable'


def test_parse_filter_is_case_insensitive_for_link_down_variants():
    # The filter and the classifier share one lowercased source-of-truth, so a
    # non-canonical casing must NOT be dropped before classification (the chain
    # diagnostic fails on severity=='link', so a dropped link-down = a missed fail).
    for variant in ("pcie link down", "LINK DOWN", "something: Link is Down"):
        evs = dmesg.parse_events(f"[1.0] pcieport 0000:00:1c.0: {variant}\n")
        assert len(evs) == 1 and evs[0].severity == "link", variant


def test_event_involves_bdf():
    ev = dmesg.DmesgEvent("non_fatal", ["0000:04:00.0", "0000:00:1c.0"], "text")
    assert ev.involves("0000:04:00.0") is True
    assert ev.involves("0000:99:99.9") is False
    assert ev.uncorrectable is True                       # non_fatal counts as uncorrectable


def test_read_kernel_log_with_injected_reader():
    assert dmesg.read_kernel_log(lambda: "injected log\n") == "injected log\n"
