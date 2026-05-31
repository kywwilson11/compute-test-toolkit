"""topology config loading (dict/JSON-string/JSON-file/passthrough), DeviceExpectation
matching branches, and EnumerationReport.summary(). Complements test_topology_harness."""
import json

from computetest import topology
from computetest.backend import MockBackend, PciDevice


def _dev(bdf="0000:03:00.0", vid=0x10DE, did=0x2204, cls=0x030000):
    return PciDevice(bdf, vid, did, cls, 4, 16, 4, 16)


# --- DeviceExpectation.matches: each criterion + each mismatch -------------- #
def test_matches_all_criteria():
    exp = topology.DeviceExpectation("GPU", {"bdf": "0000:03:00.0", "vendor_id": 0x10DE,
                                             "device_id": 0x2204, "class_code": 0x030000})
    assert exp.matches(_dev()) is True


def test_matches_rejects_on_each_field():
    base = {"bdf": "0000:03:00.0", "vendor_id": 0x10DE, "device_id": 0x2204,
            "class_code": 0x030000}
    assert not topology.DeviceExpectation("g", base).matches(_dev(bdf="0000:09:00.0"))
    assert not topology.DeviceExpectation("g", base).matches(_dev(vid=0x8086))
    assert not topology.DeviceExpectation("g", base).matches(_dev(did=0x9999))
    assert not topology.DeviceExpectation("g", base).matches(_dev(cls=0x010802))


# --- load_config: every source form ---------------------------------------- #
def test_load_config_passthrough_returns_same_object():
    cfg = topology.TopologyConfig([], target_ber=1e-10, confidence=0.9)
    assert topology.load_config(cfg) is cfg               # already a config -> identity


def test_load_config_from_json_string():
    src = json.dumps({"target_ber": 1e-11, "confidence": 0.9,
                      "pcie_devices": [{"name": "GPU", "match": {"vendor_id": "0x10DE"}}]})
    cfg = topology.load_config(src)                        # leading '{' -> JSON string
    assert cfg.target_ber == 1e-11 and cfg.devices[0].match["vendor_id"] == 0x10DE


def test_load_config_from_json_file(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps({"pcie_devices": [{"name": "NVMe", "match": {"class_code": "0x010802"},
                                               "count": 2, "expected_speed": 4}]}))
    cfg = topology.load_config(str(p))                     # .json path branch
    assert cfg.devices[0].count == 2 and cfg.devices[0].expected_speed == 4
    assert cfg.devices[0].match["class_code"] == 0x010802  # 0x-string coerced to int


def test_load_config_bdf_match_round_trips_as_string():
    # Regression: load_config must NOT base-0 coerce the documented `bdf` match key
    # (int('0000:03:00.0', 0) raises ValueError). bdf is an address string that
    # matches() compares verbatim; numeric keys alongside it are still coerced.
    cfg = topology.load_config(
        {"pcie_devices": [{"name": "GPU", "match": {"bdf": "0000:03:00.0",
                                                    "vendor_id": "0x10DE"}}]})
    exp = cfg.devices[0]
    assert exp.match["bdf"] == "0000:03:00.0"   # left a string, not coerced/crashed
    assert exp.match["vendor_id"] == 0x10DE      # numeric key still coerced
    assert exp.matches(_dev(bdf="0000:03:00.0")) is True
    assert exp.matches(_dev(bdf="0000:09:00.0")) is False


# --- EnumerationReport.summary --------------------------------------------- #
def test_enumeration_summary_lists_matched_missing_unexpected():
    be = MockBackend()
    plan = {"pcie_devices": [
        {"name": "GPU", "match": {"vendor_id": 0x10DE, "class_code": 0x030000}, "count": 2},
        {"name": "Absent", "match": {"vendor_id": 0xDEAD}, "count": 1}]}
    report = topology.enumerate_against(be, topology.load_config(plan))
    s = report.summary()
    assert "Enumeration:" in s
    assert "GPU: 0000:03:00.0, 0000:04:00.0" in s         # the two matched GPUs
    assert "MISSING:" in s and "Absent" in s              # the unmet expectation
    assert "unexpected:" in s                             # NVMe/custom card unmatched here
    assert report.ok is False                             # a missing device fails


def test_enumeration_summary_none_when_no_match():
    be = MockBackend()
    plan = {"pcie_devices": [{"name": "GPU", "match": {"vendor_id": 0xDEAD}, "count": 0}]}
    report = topology.enumerate_against(be, topology.load_config(plan))
    assert "GPU: (none)" in report.summary()              # matched-but-empty shows (none)


def test_enumeration_missing_without_unexpected():
    # Every present device is claimed by some expectation (no unexpected), but one
    # expectation demands more than exist -> MISSING shown, 'unexpected:' omitted.
    be = MockBackend()
    plan = {"pcie_devices": [
        {"name": "GPU", "match": {"vendor_id": 0x10DE, "class_code": 0x030000}, "count": 9},
        {"name": "NVMe", "match": {"class_code": 0x010802}, "count": 2},
        {"name": "Custom", "match": {"vendor_id": 0x1B36}, "count": 1}]}
    report = topology.enumerate_against(be, topology.load_config(plan))
    s = report.summary()
    assert "MISSING:" in s and "GPU" in s                 # demanded 9 GPUs, board has 2
    assert "unexpected:" not in s                         # all present devices claimed
    assert report.ok is False
