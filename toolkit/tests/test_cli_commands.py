"""CLI command dispatch + exit codes for every subcommand and error path, on the mock
backend. Complements test_cli.py (json purity, bad device/plan/value)."""
import json
import os

import pytest

from computetest import cli

CONFIGS = os.path.join(os.path.dirname(__file__), "..", "configs")


# --- ber: the assess (--bits given) branch ---------------------------------- #
def test_ber_assess_with_bits(capsys):
    rc = cli.main(["ber", "--bits", "5e12", "--target-ber", "1e-12", "--confidence", "0.95"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_PASS and "PASS" in out          # 5e12 bits proves 1e-12 @ 95%


def test_ber_assess_json_has_verdict_fields(capsys):
    cli.main(["ber", "--json", "--bits", "1e12", "--errors", "0"])
    data = json.loads(capsys.readouterr().out)
    assert "status" in data and "confidence_reached" in data and "ber_upper" in data


# --- bert command ----------------------------------------------------------- #
def test_bert_command_passes_clean_device(capsys):
    rc = cli.main(["--backend", "mock", "bert", "-d", "0000:03:00.0",
                   "--target-ber", "1e-9", "--max-seconds", "1"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_PASS and "0000:03:00.0" in out and "PASS" in out


def test_bert_command_fails_marginal_device(capsys):
    rc = cli.main(["--backend", "mock", "bert", "-d", "0000:04:00.0",
                   "--target-ber", "1e-12", "--max-seconds", "1"])
    assert rc == cli.EXIT_FAIL                             # the marginal sample GPU link


# --- diagnose command ------------------------------------------------------- #
def test_diagnose_single_device_json(capsys):
    # --no-bert opts out of the BER measurement entirely. Per the audit fix, that's
    # "couldn't/didn't measure" -> EXIT_UNAVAIL (5), NOT EXIT_PASS -- we never claim
    # PASS from a measurement we didn't make.
    rc = cli.main(["--backend", "mock", "diagnose", "-d", "0000:03:00.0", "--json",
                   "--no-bert", "--no-margin"])
    data = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_UNAVAIL and isinstance(data, list)
    assert data[0]["bdf"] == "0000:03:00.0" and data[0]["status"] == "skip"


def test_diagnose_all_fails_on_sample_board(capsys):
    # The sample board has a marginal GPU + a degraded custom card -> overall FAIL.
    rc = cli.main(["--backend", "mock", "diagnose", "--no-bert", "--max-seconds", "1"])
    assert rc == cli.EXIT_FAIL


# --- FIX 2: "couldn't measure" (skip) -> EXIT_UNAVAIL(5), distinct from FAIL(1) #
def _no_error_source_backend(bdf="0000:0b:00.0"):
    from computetest.backend import ECAP_AER, MockBackend, MockDevice
    dev = MockDevice(bdf, 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, has_pcie_cap=False)
    dev._ext_caps.pop(ECAP_AER)        # no AER AND no Device Status -> verdict "skip"
    return MockBackend([dev]), bdf


def test_bert_skip_exits_unavail_not_fail(monkeypatch, capsys):
    be, bdf = _no_error_source_backend()
    monkeypatch.setattr(cli, "select_backend", lambda: be)
    rc = cli.main(["--backend", "mock", "bert", "-d", bdf, "--max-seconds", "1"])
    assert rc == cli.EXIT_UNAVAIL      # 5, not 1 — we couldn't measure, DUT didn't fail


def test_bert_real_fail_still_exits_fail(capsys):
    # A genuine DUT failure (the marginal sample GPU link) stays EXIT_FAIL(1).
    rc = cli.main(["--backend", "mock", "bert", "-d", "0000:04:00.0",
                   "--target-ber", "1e-12", "--max-seconds", "1"])
    assert rc == cli.EXIT_FAIL


def test_diagnose_skip_exits_unavail(monkeypatch, capsys):
    be, bdf = _no_error_source_backend()
    monkeypatch.setattr(cli, "select_backend", lambda: be)
    rc = cli.main(["--backend", "mock", "diagnose", "-d", bdf, "--no-margin",
                   "--max-seconds", "1"])
    assert rc == cli.EXIT_UNAVAIL      # link trained but never error-tested -> 5


def test_diagnose_fail_wins_over_skip(monkeypatch, capsys):
    # A board with a degraded device AND a skip-only device must exit FAIL (real fault wins).
    from computetest.backend import ECAP_AER, MockBackend, MockDevice
    skip_dev = MockDevice("0000:0b:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16,
                          has_pcie_cap=False)
    skip_dev._ext_caps.pop(ECAP_AER)
    bad_dev = MockDevice("0000:07:00.0", 0x1B36, 0x10, 0x088000, 3, 8, 4, 8)  # Gen3<Gen4
    monkeypatch.setattr(cli, "select_backend", lambda: MockBackend([skip_dev, bad_dev]))
    rc = cli.main(["--backend", "mock", "diagnose", "--no-margin", "--max-seconds", "1"])
    assert rc == cli.EXIT_FAIL


def test_chain_skip_exits_unavail(monkeypatch, capsys):
    # A flat endpoint with no error source: the chain BERT is "skip" -> EXIT_UNAVAIL.
    be, bdf = _no_error_source_backend("0000:04:00.0")
    monkeypatch.setattr(cli, "select_backend", lambda: be)
    rc = cli.main(["--backend", "mock", "chain", bdf, "--max-seconds", "1"])
    assert rc == cli.EXIT_UNAVAIL


# --- interface commands ----------------------------------------------------- #
@pytest.mark.parametrize("cmd,target,expect_rc", [
    ("gpu", "0", cli.EXIT_PASS),
    ("gpu", "99", cli.EXIT_FAIL),
    ("gmsl", "1-0029", cli.EXIT_PASS),
    ("eth", "eth0", cli.EXIT_PASS),
    ("can", "canBAD", cli.EXIT_FAIL),
])
def test_interface_commands(capsys, cmd, target, expect_rc):
    rc = cli.main(["--backend", "mock", cmd, target])
    assert rc == expect_rc


# --- plan command ----------------------------------------------------------- #
def test_plan_json_runs_and_reports(capsys):
    rc = cli.main(["--backend", "mock", "plan", os.path.join(CONFIGS, "example_plan.json"),
                   "--serial", "SN999"])
    out = capsys.readouterr().out
    assert "Test plan report:" in out
    assert rc in (cli.EXIT_PASS, cli.EXIT_FAIL)           # depends on the sample board


def test_plan_json_emits_summary_object(capsys):
    cli.main(["--backend", "mock", "plan", os.path.join(CONFIGS, "example_plan.json"),
              "--json"])
    data = json.loads(capsys.readouterr().out)
    assert "report" in data and "summary" in data and data["summary"]["total"] > 0


def test_plan_yaml_loads_via_load_config(tmp_path, capsys):
    pytest.importorskip("yaml")
    # Copy the example with bert_max_s minimized — the test proves the YAML loader path
    # works, not that production BERT timing runs (BERT timing is covered elsewhere).
    # Without this override the test spends ~100 s running prod-length BERTs.
    src = open(os.path.join(CONFIGS, "example_topology.yaml")).read()
    dst = tmp_path / "plan.yaml"
    dst.write_text(src.replace("bert_max_s: 30", "bert_max_s: 0.5"))
    rc = cli.main(["--backend", "mock", "plan", str(dst)])
    assert rc in (cli.EXIT_PASS, cli.EXIT_FAIL)
    assert "Test plan report:" in capsys.readouterr().out


# --- error-to-exit-code mapping --------------------------------------------- #
def test_not_implemented_maps_to_unavail(monkeypatch, capsys):
    # The CLI maps NotImplementedError (a capability missing on this hardware) -> EXIT_UNAVAIL.
    def boom(*a, **k):
        raise NotImplementedError("real margining needs validation")
    monkeypatch.setattr(cli.gpu, "check_gpu", boom)
    rc = cli.main(["--backend", "mock", "gpu", "0"])
    assert rc == cli.EXIT_UNAVAIL
    assert "not available on this backend" in capsys.readouterr().err


def test_runtime_error_maps_to_io(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("nvme-cli not found")
    monkeypatch.setattr(cli.nvme, "check_nvme", boom)
    rc = cli.main(["--backend", "mock", "nvme", "/dev/nvme0"])
    assert rc == cli.EXIT_IO
    assert "error:" in capsys.readouterr().err


def test_backend_banner_on_stderr(capsys):
    cli.main(["--backend", "mock", "list"])
    assert "backend: MOCK" in capsys.readouterr().err     # banner goes to stderr, not stdout


def test_backend_flag_after_subcommand_sets_env(capsys, monkeypatch):
    # --backend (when given after the subcommand) forces the backend via the env var.
    monkeypatch.delenv("COMPUTETEST_BACKEND", raising=False)
    rc = cli.main(["list", "--backend", "mock"])
    assert rc == cli.EXIT_PASS
    assert os.environ.get("COMPUTETEST_BACKEND") == "mock"   # the override was applied
    assert "backend: MOCK" in capsys.readouterr().err


def test_keyboard_interrupt_maps_to_exit_130(monkeypatch, capsys):
    """Ctrl-C during a long BERT must exit cleanly with 130 (the bash/autoconf
    convention 128 + SIGINT(2)), not dump a Python traceback into the station log."""
    def boom(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli.nvme, "check_nvme", boom)
    rc = cli.main(["--backend", "mock", "nvme", "/dev/nvme0"])
    assert rc == 130
    assert "interrupted" in capsys.readouterr().err


def test_sqlite_error_maps_to_exit_io(capsys):
    """Audit Major: a bad --db path (or a disk-full / permission failure) used to
    blow up the operator console with a raw sqlite3.OperationalError traceback. CLI
    now catches sqlite3.Error and maps it to the documented EXIT_IO (4)."""
    bad = os.path.join("/nonexistent", "dir", "results.db")
    rc = cli.main(["--backend", "mock", "plan", os.path.join(CONFIGS, "example_plan.json"),
                   "--db", bad])
    assert rc == cli.EXIT_IO
    err = capsys.readouterr().err
    assert "error: results DB" in err
    assert "Traceback" not in err                # the whole point: no Python TB
