import json

from computetest import cli


def test_json_is_pure_stdout(capsys):
    # --json (after the subcommand) must emit ONLY JSON on stdout, parseable by jq.
    rc = cli.main(["--backend", "mock", "nvme", "/dev/nvme0", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)            # raises if human text leaked onto stdout
    assert data["ok"] is True and rc == cli.EXIT_PASS


def test_ber_json_emitted(capsys):
    rc = cli.main(["ber", "--json", "--target-ber", "1e-12"])
    data = json.loads(capsys.readouterr().out)
    assert "bits_needed" in data and rc == cli.EXIT_PASS


def test_bad_device_clean_exit():
    rc = cli.main(["--backend", "mock", "bert", "-d", "9999:99:99.9"])
    assert rc == cli.EXIT_NOTFOUND   # exit 3, not a traceback


def test_missing_plan_clean_exit():
    rc = cli.main(["--backend", "mock", "plan", "/nonexistent-plan.json"])
    assert rc == cli.EXIT_IO         # exit 4


def test_bad_value_is_usage_error(capsys):
    rc = cli.main(["ber", "--target-ber", "0", "--confidence", "0.95"])
    assert rc == cli.EXIT_USAGE      # exit 2; ValueError mapped to usage


def test_list_human_output(capsys):
    rc = cli.main(["--backend", "mock", "list"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_PASS and "0000:03:00.0" in out


def test_list_json_includes_derived_properties(capsys):
    # JSON must align with the human listing: vendor_name/speed_str (derived properties)
    # are present, not dropped by a raw __dict__ dump that only had the raw fields.
    rc = cli.main(["--backend", "mock", "list", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_PASS and isinstance(data, list) and data
    first = data[0]
    assert "vendor_name" in first and "speed_str" in first   # the alignment fix
    assert first["bdf"] == "0000:03:00.0" and first["vendor_name"] == "NVIDIA"
    # sample_board's first GPU is Gen5 (the Zoox compute target).
    assert first["current_link_speed"] == 5 and "GT/s" in first["speed_str"]


def test_chain_command_runs(capsys):
    # Flat sample board: chain degenerates to the endpoint, but the command must run cleanly.
    rc = cli.main(["--backend", "mock", "chain", "0000:03:00.0",
                   "--target-ber", "1e-9", "--max-seconds", "2"])
    out = capsys.readouterr().out
    assert rc == cli.EXIT_PASS and "chain to 0000:03:00.0" in out
