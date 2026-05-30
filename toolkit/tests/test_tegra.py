"""Tegra/Jetson SoC-telemetry check (tegrastats parser + thermal/throttle/XID verdict)."""
from __future__ import annotations

from computetest import tegra
from computetest.tegra import (
    TegraHealth,
    check_tegra,
    is_tegra,
    parse_tegrastats,
)

# Real Jetson Nano (L4T) tegrastats line.
NANO = (
    "RAM 2594/3956MB (lfb 12x4MB) SWAP 0/1978MB (cached 0MB) "
    "CPU [12%@1479,4%@1479,5%@1479,3%@1479] EMC_FREQ 8% GR3D_FREQ 37% "
    "PLL@39C CPU@41C PMIC@100C GPU@38C AO@45.5C thermal@40.25C "
    "POM_5V_IN 2532/2698 POM_5V_GPU 0/123 POM_5V_CPU 401/452"
)
# Orin-style line: tj/cpu zones, an offline (-256C) sensor, VDD_*/VIN_* rails.
ORIN = (
    "RAM 1500/7850MB EMC_FREQ 12% GR3D_FREQ 0% "
    "cpu@-256C tj@47.343C GPU@52C CPU@50C "
    "VDD_GPU_SOC 1191/1191 VDD_CPU_CV 400/450 VIN_SYS_5V0 2000/2100"
)
HOT = "GR3D_FREQ 50% GPU@99C thermal@98C CPU@90C POM_5V_IN 5000/5200"

XID_CRIT = "[10.0] NVRM: Xid (PCI:0000:65:00): 79, GPU has fallen off the bus\n"
XID_BENIGN = "[10.0] NVRM: Xid (PCI:0000:65:00): 13, graphics exception\n"


class TestParse:
    def test_nano_line(self):
        p = parse_tegrastats(NANO)
        assert p["thermal_c"]["GPU"] == 38.0 and p["thermal_c"]["AO"] == 45.5
        assert p["gr3d_pct"] == 37 and p["emc_pct"] == 8
        assert p["ram_used_mb"] == 2594 and p["ram_total_mb"] == 3956
        assert p["rails_mw"]["POM_5V_IN"] == (2532, 2698)
        # RAM/SWAP carry an `MB` suffix and must NOT be parsed as power rails.
        assert "RAM" not in p["rails_mw"] and "SWAP" not in p["rails_mw"]

    def test_orin_line(self):
        p = parse_tegrastats(ORIN)
        assert p["thermal_c"]["tj"] == 47.343 and p["thermal_c"]["cpu"] == -256.0
        assert p["rails_mw"]["VDD_GPU_SOC"] == (1191, 1191)
        assert p["rails_mw"]["VIN_SYS_5V0"] == (2000, 2100)

    def test_missing_fields_are_none(self):
        p = parse_tegrastats("GPU@40C")
        assert p["gr3d_pct"] is None and p["emc_pct"] is None
        assert p["ram_used_mb"] is None and p["rails_mw"] == {}


class TestMockAndVerdict:
    def test_mock_good_passes(self):
        h = check_tegra(mock=True)
        assert isinstance(h, TegraHealth) and h.ok
        assert h.model == "Tegra (mock Jetson)"
        assert h.metrics["gpu_temp_c"] == 38.0
        # PMIC is a fixed dummy on Nano — reported as a note, never a failure.
        assert any("PMIC" in n for n in h.notes)

    def test_mock_mode_default(self, monkeypatch):
        monkeypatch.setattr(tegra, "mock_mode", lambda: True)
        assert check_tegra().ok                       # mock=None -> mock_mode()

    def test_hot_fails_overtemp_and_throttle(self):
        h = check_tegra(_lines=[HOT])
        assert not h.ok
        assert h.checks["max_zone<=85C"] is False
        assert h.checks["no_thermal_throttle"] is False
        assert h.metrics["throttle_inferred"] is True
        assert h.metrics["hot_zone"] == "GPU"

    def test_critical_xid_fails(self):
        h = check_tegra(_lines=[NANO], dmesg_reader=lambda: XID_CRIT)
        assert not h.ok and h.checks["no_critical_xid"] is False
        assert h.xid_errors == {79: 1}

    def test_benign_xid_passes(self):
        h = check_tegra(_lines=[NANO], dmesg_reader=lambda: XID_BENIGN)
        assert h.ok and h.xid_errors == {13: 1}       # reported but not critical

    def test_offline_sensor_ignored(self):
        h = check_tegra(_lines=[ORIN])
        assert h.ok                                   # cpu@-256C must not gate
        assert any("offline" in n for n in h.notes)
        assert h.metrics["hot_zone"] == "GPU" and h.metrics["gpu_temp_c"] == 52.0

    def test_gpu_temp_falls_back_to_tj(self):
        h = check_tegra(_lines=["tj@60C CPU@55C"])
        assert h.metrics["gpu_temp_c"] == 60.0        # no GPU zone -> tj

    def test_gpu_temp_falls_back_to_max_zone(self):
        h = check_tegra(_lines=["CPU@55C AO@50C"])
        assert h.metrics["gpu_temp_c"] == 55.0        # no GPU and no tj -> max zone

    def test_no_samples_fails(self):
        h = check_tegra(_lines=["", "   "])
        assert not h.ok and h.metrics["max_zone_c"] == 0.0
        assert h.metrics["hot_zone"] == "?"


class TestMerge:
    def test_worst_case_across_samples(self):
        # Two samples: peak temp, peak rail, peak load should win.
        a = "GPU@60C GR3D_FREQ 10% EMC_FREQ 5% RAM 100/8000MB POM_5V_IN 1000/1100"
        b = "GPU@70C GR3D_FREQ 90% EMC_FREQ 50% RAM 200/8000MB POM_5V_IN 1200/1300"
        h = check_tegra(_lines=[a, b])
        assert h.metrics["max_zone_c"] == 70.0        # peak temp
        assert h.metrics["gr3d_pct"] == 90 and h.metrics["emc_pct"] == 50
        assert h.metrics["ram_used_mb"] == 200
        assert h.rails_mw["POM_5V_IN"] == (1200, 1300)


class TestSummaryAndDict:
    def test_summary(self):
        assert "-> OK" in check_tegra(mock=True).summary()
        assert "FAIL(" in check_tegra(_lines=[HOT]).summary()

    def test_to_dict_surfaces_scalars(self):
        d = check_tegra(_lines=[NANO]).to_dict()
        assert d["ok"] is True
        assert d["rails_mw"]["POM_5V_IN"] == 2698      # average mW, not the (i,a) tuple
        assert set(d) >= {"model", "thermal_c", "rails_mw", "metrics", "checks",
                          "xid_errors", "notes", "ok"}


class TestIsTegra:
    def test_release_file_present(self, monkeypatch):
        monkeypatch.setattr(tegra.os.path, "exists", lambda p: True)
        assert is_tegra()

    def test_tegrastats_without_nvidia_smi(self, monkeypatch):
        monkeypatch.setattr(tegra.os.path, "exists", lambda p: False)
        monkeypatch.setattr(tegra.shutil, "which",
                            lambda n: "/usr/bin/tegrastats" if n == "tegrastats" else None)
        assert is_tegra()

    def test_discrete_gpu_box_is_not_tegra(self, monkeypatch):
        monkeypatch.setattr(tegra.os.path, "exists", lambda p: False)
        monkeypatch.setattr(tegra.shutil, "which", lambda n: "/usr/bin/" + n)
        assert not is_tegra()                          # nvidia-smi present -> discrete

    def test_plain_host_is_not_tegra(self, monkeypatch):
        monkeypatch.setattr(tegra.os.path, "exists", lambda p: False)
        monkeypatch.setattr(tegra.shutil, "which", lambda n: None)
        assert not is_tegra()
