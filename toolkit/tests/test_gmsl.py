from computetest import gmsl


def test_deserializer_all_links_locked_passes():
    d = gmsl.check_deserializer("1-0029", n_links=4)
    assert len(d.links) == 4
    assert d.frame_sync_ok and d.ok


def test_deserializer_one_link_down_fails():
    d = gmsl.check_deserializer("1-0029-BAD", n_links=4)   # 'BAD' -> camera link 0 down
    assert not d.links[0].locked and not d.links[0].ok
    assert d.frame_sync_ok is False and not d.ok


def test_deserializer_desync_fails_even_if_all_locked():
    d = gmsl.check_deserializer("1-0029-DESYNC", n_links=4)
    assert all(li.locked for li in d.links)         # every camera locked...
    assert d.frame_sync_ok is False and not d.ok    # ...but not frame-synchronized


def test_single_link_check_still_works():
    h = gmsl.check_gmsl("1-0029")
    assert h.ok and h.locked


def test_real_path_unverified_frame_sync_does_not_synthesize_pass_or_fail():
    # On real hardware there is no inter-link FSYNC signal, so check_deserializer
    # reports frame_sync_ok=None ("not verified"). That must not fake a sync PASS, but
    # must not hard-FAIL an otherwise-good deser either; summary stays honest ("sync?").
    good = gmsl.check_gmsl("1-0029")
    d = gmsl.GmslDeserHealth("1-0029", [good], frame_sync_ok=None)
    assert d.frame_sync_ok is None
    assert d.ok is True                          # links ok + sync unverified -> not blocked
    assert "sync?" in d.summary()                # not advertised as confirmed sync
    assert d.to_dict()["frame_sync_ok"] is None


def test_unverified_frame_sync_with_a_down_link_still_fails():
    # An unverified sync (None) must not rescue a genuinely failed link.
    bad = gmsl.check_gmsl("1-BAD")
    d = gmsl.GmslDeserHealth("1-0029", [bad], frame_sync_ok=None)
    assert d.ok is False
