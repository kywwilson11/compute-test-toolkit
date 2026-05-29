"""
PCIe 6.0+ FEC-aware BERT fields: pre/post-FEC counters, FBER estimate, burst
histogram, --explain mode, and the Gen6 caveat. These tests pin the behaviour
that surfaces FEC reliability metrics on Gen6+ links without claiming them on
Gen<=5 links (where FEC isn't in the spec).
"""
from __future__ import annotations

from computetest import bert
from computetest.backend import FecStats, MockBackend, MockDevice


def _gen5_be():
    return MockBackend([
        MockDevice("0000:03:00.0", 0x10DE, 0x2204, 0x030000, 5, 16, 5, 16),
    ])


def _gen6_be(**kw):
    d = MockDevice("0000:08:00.0", 0x10DE, 0x2330, 0x030000, 6, 8, 6, 8,
                    injected_pre_fec_symbol_rate=kw.pop(
                        "injected_pre_fec_symbol_rate", 1e-6),
                    injected_post_fec_flit_rate=kw.pop(
                        "injected_post_fec_flit_rate", 1e-12),
                    **kw)
    return MockBackend([d])


class TestGen5NoFecFields:
    def test_gen5_link_fec_fields_are_none(self):
        r = bert.run_bert(_gen5_be(), "0000:03:00.0",
                          target_ber=1e-9, max_seconds=1.0)
        assert r.pre_fec_symbol_errors is None
        assert r.post_fec_flit_errors is None
        assert r.fber_estimate is None
        assert r.burst_length_histogram is None
        assert r.is_gen6_or_later is False

    def test_gen5_to_dict_omits_fec_keys(self):
        r = bert.run_bert(_gen5_be(), "0000:03:00.0",
                          target_ber=1e-9, max_seconds=1.0)
        d = r.to_dict()
        for key in ("pre_fec_symbol_errors", "post_fec_flit_errors",
                    "fber_estimate", "burst_length_histogram"):
            assert key not in d, key


class TestGen6FecFields:
    def test_gen6_link_populates_fec_fields(self):
        r = bert.run_bert(_gen6_be(), "0000:08:00.0",
                          target_ber=1e-6, max_seconds=1.0)
        assert r.is_gen6_or_later is True
        assert r.pre_fec_symbol_errors is not None
        assert r.post_fec_flit_errors is not None
        assert r.fber_estimate is not None
        assert r.burst_length_histogram is not None

    def test_gen6_pre_fec_is_nonzero_with_pre_fec_rate(self):
        # Pre-FEC rate 1e-5 × Gen6 throughput × 1s should easily produce >0 errors.
        be = _gen6_be(injected_pre_fec_symbol_rate=1e-5,
                      injected_post_fec_flit_rate=0.0)
        r = bert.run_bert(be, "0000:08:00.0", target_ber=1e-6, max_seconds=1.0)
        assert r.pre_fec_symbol_errors > 0
        assert r.post_fec_flit_errors == 0                  # FEC corrects everything
        assert r.fber_estimate == 0.0

    def test_gen6_post_fec_flits_drive_fber(self):
        # Push post-FEC rate high enough that we'll see at least one FLIT error.
        be = _gen6_be(injected_pre_fec_symbol_rate=0.0,
                      injected_post_fec_flit_rate=1e-3)
        r = bert.run_bert(be, "0000:08:00.0", target_ber=1e-6, max_seconds=1.0)
        assert r.post_fec_flit_errors > 0
        assert r.fber_estimate > 0.0

    def test_gen6_burst_histogram_is_length_keyed(self):
        be = _gen6_be(injected_pre_fec_symbol_rate=1e-5)
        r = bert.run_bert(be, "0000:08:00.0", target_ber=1e-6, max_seconds=1.0)
        # Histogram is {length_in_symbols: count}. Length 1 should dominate.
        hist = r.burst_length_histogram
        assert hist is not None
        assert all(isinstance(k, int) and k >= 1 for k in hist.keys())
        if any(v > 0 for v in hist.values()):
            assert hist[1] >= max(hist.get(k, 0) for k in hist if k != 1)

    def test_gen6_note_mentions_fec_undercount(self):
        r = bert.run_bert(_gen6_be(), "0000:08:00.0",
                          target_ber=1e-6, max_seconds=1.0)
        assert "FLIT/FEC" in r.note


class TestExplainMode:
    def test_explain_includes_link_and_ber_lines(self):
        r = bert.run_bert(_gen5_be(), "0000:03:00.0",
                          target_ber=1e-9, max_seconds=1.0)
        text = r.explain()
        assert "BERT verdict:" in text
        assert "Link:" in text and "Gen5" in text
        assert "BER bound:" in text

    def test_explain_omits_fec_block_on_gen5(self):
        r = bert.run_bert(_gen5_be(), "0000:03:00.0",
                          target_ber=1e-9, max_seconds=1.0)
        assert "PCIe 6.0+ FEC counters" not in r.explain()
        assert "PAM-4 at 64 GT/s" not in r.explain()

    def test_explain_includes_full_fec_block_on_gen6(self):
        r = bert.run_bert(_gen6_be(), "0000:08:00.0",
                          target_ber=1e-6, max_seconds=1.0)
        text = r.explain()
        assert "PCIe 6.0+ FEC counters" in text
        assert "PAM-4 at 64 GT/s" in text
        assert "Reed-Solomon over GF(2^8)" in text
        assert "FBER target ~1e-6" in text


class TestBackendInterface:
    def test_base_backend_returns_none(self):
        # The default read_fec_stats on Backend returns None — confirms the contract.
        be = _gen5_be()
        assert be.read_fec_stats("0000:03:00.0", 1.0) is None

    def test_mock_returns_fecstats_for_gen6(self):
        be = _gen6_be(injected_pre_fec_symbol_rate=1e-5)
        stats = be.read_fec_stats("0000:08:00.0", 1.0)
        assert isinstance(stats, FecStats)
        assert stats.pre_fec_symbol_errors >= 0
        assert stats.post_fec_flit_errors >= 0
        assert stats.fber_estimate >= 0.0
        assert isinstance(stats.burst_length_histogram, dict)

    def test_mock_returns_none_for_zero_elapsed(self):
        be = _gen6_be()
        assert be.read_fec_stats("0000:08:00.0", 0.0) is None


class TestCliExplainFlag:
    def test_explain_prints_fec_block_for_gen6(self, capsys):
        from computetest import cli
        rc = cli.main([
            "--backend", "mock", "bert",
            "-d", "0000:08:00.0", "--target-ber", "1e-6",
            "--max-seconds", "1.0", "--explain"])
        out = capsys.readouterr().out
        assert rc in (cli.EXIT_PASS, cli.EXIT_FAIL)
        assert "BERT verdict:" in out
        assert "PCIe 6.0+ FEC counters" in out
        assert "PAM-4 at 64 GT/s" in out

    def test_explain_omitted_with_json(self, capsys):
        # --json + --explain: JSON wins (explain is for humans).
        import json

        from computetest import cli
        rc = cli.main([
            "--backend", "mock", "bert",
            "-d", "0000:08:00.0", "--target-ber", "1e-6",
            "--max-seconds", "1.0", "--explain", "--json"])
        out = capsys.readouterr().out
        # stdout must be pure JSON (no human explain block leaks).
        data = json.loads(out)
        # FEC fields are present in the JSON for Gen6.
        assert "pre_fec_symbol_errors" in data
        assert "post_fec_flit_errors" in data
        assert "fber_estimate" in data
        assert rc in (cli.EXIT_PASS, cli.EXIT_FAIL)
