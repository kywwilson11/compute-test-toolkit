"""Whole-chain diagnostic: a switch topology (Root -> SwUp -> SwDown -> Endpoint).
Bit errors are evaluated per-BDF (per direction); link downgrades per-link."""
from computetest import diagnostics
from computetest.backend import (
    ECAP_AER,
    PORT_ENDPOINT,
    PORT_ROOT,
    PORT_SWITCH_DOWNSTREAM,
    PORT_SWITCH_UPSTREAM,
    MockBackend,
    MockDevice,
)


def _switch_board(**overrides):
    spec = {
        "0000:00:1c.0": dict(parent=None, port_type=PORT_ROOT),
        "0000:02:00.0": dict(parent="0000:00:1c.0", port_type=PORT_SWITCH_UPSTREAM),
        "0000:03:00.0": dict(parent="0000:02:00.0", port_type=PORT_SWITCH_DOWNSTREAM),
        "0000:04:00.0": dict(parent="0000:03:00.0", port_type=PORT_ENDPOINT),
    }
    for bdf, kw in overrides.items():
        spec[bdf].update(kw)
    return MockBackend([MockDevice(bdf, **kw) for bdf, kw in spec.items()])


def _diag(be):
    return diagnostics.diagnose_chain(be, "0000:04:00.0", target_ber=1e-9, max_seconds=3,
                                      expected_speed=4, expected_width=16)


def test_chain_all_clean_passes():
    d = _diag(_switch_board())
    assert d.status == "pass"
    assert len(d.links) == 2 and len(d.segments) == 3   # 4 BDFs: endpoint + 3 monitored


def test_chain_flags_upstream_error_with_clean_endpoint():
    # The switch-upstream port's receiver (link A, Root->SwUp direction) errors;
    # the endpoint link itself is clean. The chain must still fail, pinned to that port.
    d = _diag(_switch_board(**{"0000:02:00.0": dict(injected_ber=1e-8)}))
    assert d.bert.status == "pass"      # endpoint link clean
    assert d.status == "fail"           # chain fails on the upstream segment
    failed = [s.bdf for s in d.segments if s.status == "fail"]
    assert "0000:02:00.0" in failed


def test_chain_flags_link_downgrade():
    # Link B (downstream port 03:00.0) trained at Gen3, Gen4 expected.
    d = _diag(_switch_board(**{"0000:03:00.0": dict(link_speed=3)}))
    assert d.status == "fail"
    bad = [li for li in d.links if li.status == "fail"]
    assert any(li.downstream_bdf == "0000:03:00.0" and li.speed == 3 for li in bad)


def test_chain_attributes_uncorrectable_on_a_segment():
    be = _switch_board()
    be.inject_uncorrectable("0000:03:00.0", 14)   # Completion Timeout on the switch-down port
    d = _diag(be)
    assert d.status == "fail"
    seg = next(s for s in d.segments if s.bdf == "0000:03:00.0")
    assert "CmplTO" in seg.uncorrectable_types


def test_chain_segment_with_no_error_source_is_skip_not_pass():
    # An upstream BDF with neither AER nor Device Status cannot have its BER measured.
    # It must be reported 'skip', never a silent 'pass' (mirrors the single-device rule).
    be = _switch_board(**{"0000:02:00.0": dict(has_pcie_cap=False)})
    be._devs["0000:02:00.0"]._ext_caps.pop(ECAP_AER)   # no AER AND no Device Status -> 'none'
    d = diagnostics.diagnose_chain(be, "0000:04:00.0", target_ber=1e-9, max_seconds=3,
                                   expected_speed=4, expected_width=16)
    seg = next(s for s in d.segments if s.bdf == "0000:02:00.0")
    assert seg.status == "skip"          # unmeasurable, not pass
    assert d.status == "skip"            # folds through (no fail anywhere else)


def test_chain_no_expectation_falls_back_to_downstream_port_max():
    # No expected_speed/width supplied: a link statically trained below its OWN max
    # (Gen3 vs max Gen4, no LBMS/LABS latched) must still FAIL via the max fallback.
    be = _switch_board(**{"0000:03:00.0": dict(link_speed=3)})   # max_link_speed stays 4
    d = diagnostics.diagnose_chain(be, "0000:04:00.0", target_ber=1e-9, max_seconds=3)
    assert d.status == "fail"
    bad = [li for li in d.links if li.status == "fail"]
    assert any(li.downstream_bdf == "0000:03:00.0" and li.speed == 3 for li in bad)


def test_chain_no_expectation_clean_link_at_max_passes():
    # Same path, link at its max (Gen4=Gen4) and no expectation: must NOT false-fail.
    d = diagnostics.diagnose_chain(_switch_board(), "0000:04:00.0", target_ber=1e-9,
                                   max_seconds=3)
    assert all(li.status == "pass" for li in d.links)
