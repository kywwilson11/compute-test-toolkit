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
