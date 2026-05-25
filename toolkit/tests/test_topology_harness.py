import json
import os

from computetest import topology
from computetest.backend import MockBackend
from computetest.harness import run_test_plan
from computetest.results import ResultStore

PLAN = {
    "target_ber": 1e-9, "confidence": 0.95, "bert_max_s": 1.5, "watch_retrains_s": 0.05,
    "devices": [
        {"name": "GPU", "match": {"vendor_id": 0x10DE, "class_code": 0x030000},
         "count": 2, "expected_speed": 4, "expected_width": 16},
        {"name": "NVMe", "match": {"class_code": 0x010802},
         "count": 2, "expected_speed": 4, "expected_width": 4},
        {"name": "CustomCard", "match": {"vendor_id": 0x1B36},
         "count": 1, "expected_speed": 4, "expected_width": 8},
    ],
    "nvme": ["/dev/nvme0"], "gpus": [0], "gmsl": ["1-0029"],
    "ethernet": ["eth0"], "can": ["can0"],
}


def test_load_config_from_dict():
    cfg = topology.load_config(PLAN)
    assert len(cfg.devices) == 3
    assert cfg.devices[0].expected_width == 16
    assert cfg.target_ber == 1e-9


def test_enumeration_matches_sample_board():
    be = MockBackend()
    report = topology.enumerate_against(be, topology.load_config(PLAN))
    assert report.ok                       # all expected devices present
    assert len(report.matched["GPU"]) == 2
    assert len(report.matched["NVMe"]) == 2


def test_enumeration_reports_missing():
    be = MockBackend()
    plan = dict(PLAN)
    plan["devices"] = [{"name": "GPU", "match": {"vendor_id": 0x10DE, "class_code": 0x030000},
                        "count": 9}]   # demand 9 GPUs; board has 2
    report = topology.enumerate_against(be, topology.load_config(plan))
    assert not report.ok and report.missing


def test_full_plan_runs_and_records():
    be = MockBackend()
    store = ResultStore(":memory:", dut_serial="DUT-TEST")
    report = run_test_plan(be, PLAN, store=store)
    # The sample board has a marginal GPU link and a degraded custom card -> FAIL.
    assert report.ok is False
    assert report.counts["fail"] >= 2
    # Results were persisted with measured values.
    recent = store.recent(100)
    assert len(recent) == len(report.records)
    summ = store.summary()
    assert summ["total"] == len(report.records) and 0.0 <= summ["yield"] <= 1.0


def test_example_plan_json_is_valid():
    path = os.path.join(os.path.dirname(__file__), "..", "configs", "example_plan.json")
    with open(path) as fh:
        data = json.load(fh)
    cfg = topology.load_config(data)
    assert cfg.devices and cfg.target_ber > 0


def test_example_yaml_loads_if_pyyaml_present():
    import pytest
    pytest.importorskip("yaml")
    path = os.path.join(os.path.dirname(__file__), "..", "configs", "example_topology.yaml")
    cfg = topology.load_config(path)
    assert len(cfg.devices) == 3
    assert cfg.devices[0].match["vendor_id"] == 0x10DE   # 0x-string parsed to int
