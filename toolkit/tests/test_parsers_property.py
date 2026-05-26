"""
Property-based tests (Hypothesis) for the pure parsers — Phase 0 of the real-path
testing plan (docs/realpath-simulation.md). These harden the parsers against the
*version-drift / malformed-input* bug class: each is fuzzed for "never raises" and
for the exact behavior real hardware depends on, and each is seeded with @example
pinning a bug we have actually hit, as a permanent regression.

Bugs pinned here (and fixed in the parsers as a result):
  * _stat: a superstring counter ('rx_errors_phy') must not shadow 'rx_errors'; and a
    Unicode digit in a value (str.isdigit() accepts e.g. superscripts) must not crash int().
  * _parse_speed: a malformed Speed number (e.g. '.') must not crash float().
  * _normalize_smart_keys: nvme-cli's abbreviated SMART keys must decode identically to
    the canonical keys (the avail_spare>=100 false-FAIL bug).
  * RealBackend.find_ext_cap: an arbitrary/looping capability chain must terminate, never raise.
"""
from __future__ import annotations

from hypothesis import example, given, settings
from hypothesis import strategies as st

from computetest.backend import ECAP_AER, RealBackend
from computetest.ethernet import _parse_speed, _stat
from computetest.nvme import _apply_limits, _normalize_smart_keys

# Identifier-shaped counter/key names (no ':' or whitespace, so they can't confuse parsing).
NAMES = st.from_regex(r"[a-z][a-z_]{0,19}", fullmatch=True)
COUNTS = st.integers(min_value=0, max_value=10**12)


# --- _parse_speed -------------------------------------------------------------- #
@given(st.text())
def test_parse_speed_never_raises(text):
    assert isinstance(_parse_speed(text), int)


@given(mbps=COUNTS)
@example(mbps=1000)
@example(mbps=10000)
def test_parse_speed_megabit(mbps):
    assert _parse_speed(f"Speed: {mbps}Mb/s") == mbps


@given(whole=st.integers(min_value=1, max_value=400), frac=st.integers(min_value=0, max_value=9))
@example(whole=2, frac=5)   # the '2.5G' -> 2500 case from the docstring
def test_parse_speed_gigabit(whole, frac):
    s = f"{whole}.{frac}"
    # mirror the function's exact arithmetic (incl. float rounding) so this can't be flaky
    assert _parse_speed(f"Speed: {s}Gb/s") == int(float(s) * 1000)


def test_parse_speed_malformed_number_returns_zero():
    # regression: '[\\d.]+' used to match '.' and feed float('.') -> ValueError
    for junk in ("Speed: . G", "Speed: .. M", "Speed: 1.2.3 G", "Link detected: yes"):
        assert _parse_speed(junk) == 0


# --- _stat --------------------------------------------------------------------- #
@given(st.text(), st.text())
def test_stat_never_raises(stats, key):
    assert isinstance(_stat(stats, key), int)


@given(key=NAMES, n=COUNTS)
@example(key="rx_errors", n=5)
def test_stat_reads_exact_counter(key, n):
    assert _stat(f"NIC stats:\n     {key}: {n}\n", key) == n


@given(key=NAMES, n=COUNTS, m=COUNTS, j=COUNTS)
@example(key="rx_errors", n=3, m=999, j=7)
def test_stat_not_shadowed_by_superstring(key, n, m, j):
    # decoys (superstrings + a prefixed name) precede the real line; the exact match wins
    stats = (f"     {key}_phy: {m}\n"
             f"     {key}_extra: {j}\n"
             f"     prefixed_{key}: {j}\n"
             f"     {key}: {n}\n")
    assert _stat(stats, key) == n


def test_stat_rx_errors_not_shadowed_by_phy():
    blob = "     rx_errors_phy: 999\n     rx_errors: 3\n     tx_errors: 0\n"
    assert _stat(blob, "rx_errors") == 3
    assert _stat(blob, "tx_errors") == 0


def test_stat_unicode_digit_does_not_crash():
    # regression: '²'.isdigit() is True but int('5²') raises; keep ASCII digits only
    assert _stat("weird: 5²\n", "weird") == 5
    assert _stat("commas: 1,234\n", "commas") == 1234   # existing behavior preserved


# --- _normalize_smart_keys ----------------------------------------------------- #
_SMART_BASE = st.fixed_dictionaries({
    "critical_warning": st.integers(0, 1),
    "media_errors": st.integers(0, 100),
    "num_err_log_entries": st.integers(0, 100),
    "temperature": st.integers(0, 120),
    "power_on_hours": st.integers(0, 100000),
})


@given(spare=st.integers(0, 100), thresh=st.integers(0, 50), used=st.integers(0, 100),
       base=_SMART_BASE)
@example(spare=100, thresh=10, used=0,
         base={"critical_warning": 0, "media_errors": 0, "num_err_log_entries": 0,
               "temperature": 41, "power_on_hours": 1})   # a healthy nvme-cli 2.x drive
def test_normalize_abbreviated_matches_canonical(spare, thresh, used, base):
    """The abbreviated nvme-cli keys must decode to the same verdicts as the canonical keys."""
    canonical = {**base, "available_spare": spare,
                 "available_spare_threshold": thresh, "percentage_used": used}
    abbreviated = {**base, "avail_spare": spare, "spare_thresh": thresh, "percent_used": used}
    normalized = _normalize_smart_keys(dict(abbreviated))
    assert _apply_limits(normalized, 70, 50) == _apply_limits(canonical, 70, 50)


def test_normalize_does_not_overwrite_existing_canonical():
    out = _normalize_smart_keys({"avail_spare": 50, "available_spare": 100})
    assert out["available_spare"] == 100   # canonical value already present wins


def test_normalize_is_idempotent():
    once = _normalize_smart_keys({"avail_spare": 50})
    twice = _normalize_smart_keys(dict(once))
    assert once == twice


# --- RealBackend.find_ext_cap (capability-list walk) --------------------------- #
class _BlobBackend(RealBackend):
    """A RealBackend whose config space is an in-memory 4 KB blob, so the real
    find_ext_cap walk runs off-hardware (on macOS too)."""

    def __init__(self, blob: bytes):
        self._blob = blob

    def read_config(self, bdf: str, offset: int, size: int = 4) -> int:
        return int.from_bytes(self._blob[offset:offset + size], "little")


@settings(max_examples=300)
@given(blob=st.binary(max_size=4096), cap=st.integers(0, 0xFFFF))
def test_find_ext_cap_terminates_and_never_raises(blob, cap):
    res = _BlobBackend(blob).find_ext_cap("0000:00:00.0", cap)
    assert res is None or (isinstance(res, int) and 0 <= res <= 0xFFF)


def test_find_ext_cap_finds_chained_cap():
    blob = bytearray(4096)
    blob[0x100:0x104] = (((0x140 << 20) | 0x000B)).to_bytes(4, "little")  # other cap -> 0x140
    blob[0x140:0x144] = (0x0001).to_bytes(4, "little")                    # AER, next = 0
    assert _BlobBackend(bytes(blob)).find_ext_cap("x", ECAP_AER) == 0x140


def test_find_ext_cap_self_loop_terminates():
    blob = bytearray(4096)
    blob[0x100:0x104] = (((0x100 << 20) | 0x0002)).to_bytes(4, "little")  # points at itself
    assert _BlobBackend(bytes(blob)).find_ext_cap("x", ECAP_AER) is None
