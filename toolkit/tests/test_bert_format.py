"""BertResult.summary() branches, run_many, the python-engine reject->fail path, the
conductor's idle-name skip, and default_c_runner's process error-handling (via a fake
'C binary'). Complements test_bert.py (python engine) and test_conductor.py (engine='c')."""
import os
import stat

import pytest

from computetest import bert
from computetest.backend import MockBackend, MockDevice, link_bits_per_second

BPS = link_bits_per_second(4, 16)


def _be(**kw):
    return MockBackend([MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000,
                                   link_speed=4, link_width=16, **kw)])


# --- BertResult.summary() formatting ---------------------------------------- #
def test_summary_clean_link_no_extra():
    r = bert.run_bert(_be(injected_ber=0.0), "0000:03:00.0", target_ber=1e-9,
                      max_seconds=1)
    s = r.summary()
    assert s.startswith("0000:03:00.0 Gen4x16") and "PASS" in s
    assert "UNCORR:" not in s and "cor:" not in s        # no errors -> no error tail


def test_summary_shows_uncorrectable_decode():
    be = _be()
    be.inject_uncorrectable("0000:03:00.0", 14)          # Completion Timeout
    r = bert.run_bert(be, "0000:03:00.0", target_ber=1e-9, max_seconds=1)
    s = r.summary()
    assert "UNCORR:CmplTO" in s and "FAIL" in s


def test_summary_shows_correctable_breakdown():
    # A marginal link accrues correctable errors; the summary lists per-type counts.
    r = bert.run_bert(_be(injected_ber=5e-9), "0000:03:00.0", target_ber=1e-9,
                      max_seconds=1)
    assert r.correctable > 0
    assert " cor:" in r.summary() and "BadTLP=" in r.summary()


# --- python engine: high error rate -> reject -> fail ----------------------- #
def test_python_engine_high_rate_rejects_to_fail():
    # A high injected BER makes the optimistic lower BER bound exceed target -> reject.
    r = bert.run_bert(_be(injected_ber=1e-6), "0000:03:00.0", target_ber=1e-12,
                      confidence=0.95, max_seconds=5)
    assert r.status == "fail" and r.correctable > 0


# --- python engine: no-sleep poll loop (poll_s=0) iterates without sleeping --- #
def test_python_engine_zero_poll_iterates_to_pass():
    # poll_s=0 means the loop never sleeps; with a slow injected clock it still must
    # iterate several times accumulating bits before it reaches the confidence target.
    be = _be(injected_ber=0.0)
    t = [0.0]

    def clock():
        # Tiny steps: at Gen4 x16 (~2.5e11 b/s) the ~3e9 bits needed for 1e-9 @ 95%
        # accrue only after several 0.002s windows, forcing repeated loop passes.
        t[0] += 0.002
        return t[0]

    r = bert.run_bert(be, "0000:03:00.0", target_ber=1e-9, confidence=0.95,
                      max_seconds=30, poll_s=0.0, clock=clock, sleep=lambda _s: None)
    assert r.ok and t[0] > 0.004                          # took multiple loop passes


# --- run_many --------------------------------------------------------------- #
def test_run_many_over_several_bdfs():
    be = MockBackend([
        MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16),
        MockDevice("0000:05:00.0", 0x144D, 0xA80A, 0x010802, 4, 4, 4, 4)])
    results = bert.run_many(be, ["0000:03:00.0", "0000:05:00.0"], target_ber=1e-9,
                            max_seconds=1)
    assert len(results) == 2 and all(r.ok for r in results)


# --- conductor: an idle (constant-fault) correctable name is not rate-counted -- #
def test_conductor_skips_idle_correctable_name():
    be = _be()
    be.inject_stuck_correctable("0000:03:00.0")          # BadTLP set even at idle

    def runner(bdf, secs):
        bits = BPS * secs
        return {"source": "aer", "link_speed_code": 4, "link_width": 16,
                "bits": bits, "correctable": 5, "uncorrectable": 0,
                "uncorrectable_bits": 0, "per_correctable": {"BadTLP": 5}}

    r = bert.run_bert(be, "0000:03:00.0", engine="c", c_runner=runner,
                      target_ber=1e-12, max_seconds=1)
    # BadTLP is the idle/constant fault -> excluded from the rate count, fails on idle.
    assert r.correctable == 0 and "BadTLP" not in r.per_correctable
    assert r.stuck is True and r.status == "fail"


# --- default_c_runner: process error-handling (fake binary, no real hardware) -- #
def test_default_c_runner_missing_binary_raises_runtimeerror():
    with pytest.raises(RuntimeError, match="C engine not found"):
        bert.default_c_runner("0000:03:00.0", 0.1, binary="/nonexistent/pcie_bert")


def _fake_binary(tmp_path, body: str):
    p = tmp_path / "fake_bert"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(p)


def test_default_c_runner_parses_valid_json(tmp_path):
    bin_ = _fake_binary(tmp_path, 'echo \'{"correctable": 0, "bits": 1000}\'')
    out = bert.default_c_runner("0000:03:00.0", 0.1, binary=bin_)
    assert out == {"correctable": 0, "bits": 1000}


def test_default_c_runner_bad_returncode_raises(tmp_path):
    bin_ = _fake_binary(tmp_path, 'echo bad >&2; exit 2')   # rc 2 = error (not 0/3)
    with pytest.raises(RuntimeError, match="C engine failed"):
        bert.default_c_runner("0000:03:00.0", 0.1, binary=bin_)


def test_default_c_runner_uncorrectable_rc3_is_valid(tmp_path):
    # rc 3 means "uncorrectable seen" — still valid data, must parse not raise.
    bin_ = _fake_binary(tmp_path, 'echo \'{"uncorrectable": 1}\'; exit 3')
    assert bert.default_c_runner("0000:03:00.0", 0.1, binary=bin_)["uncorrectable"] == 1


def test_default_c_runner_invalid_json_raises(tmp_path):
    bin_ = _fake_binary(tmp_path, 'echo not-json')
    with pytest.raises(RuntimeError, match="invalid JSON"):
        bert.default_c_runner("0000:03:00.0", 0.1, binary=bin_)
