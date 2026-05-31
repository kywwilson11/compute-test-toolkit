"""
Corpus replay — Phase 1 drift defense (docs/realpath-simulation.md).

Each capture under corpus/ is real-format vendor-tool output. We replay it through the
ACTUAL real-hardware code path — `check_nvme`/`check_ethernet` with `mock=False`, the tool
subprocess stubbed to return the capture — and assert the decode. This exercises the
previously `# pragma: no cover` real branches off-hardware AND is the tripwire for the next
schema drift: add a new version's capture and a breaking key change turns this red. Seeds
are documented-format; replace/augment with real captures (see corpus/README.md).
"""
import os
from types import SimpleNamespace

import pytest

from computetest import ethernet as eth_mod
from computetest import nvme as nvme_mod
from computetest.ethernet import check_ethernet
from computetest.nvme import check_nvme

CORPUS = os.path.join(os.path.dirname(__file__), "..", "corpus")


def _read(*parts: str) -> str:
    with open(os.path.join(CORPUS, *parts)) as fh:
        return fh.read()


# --- NVMe: replay real-format smart-log/id-ctrl through check_nvme(mock=False) ------ #
_PM9A3 = "SAMSUNG MZQL2960HCJR-00A07"
NVME_CASES = {
    "nvme/2.10/samsung-pm9a3":
        dict(model=_PM9A3, temp=41, avail=100, used=0, ok=True,  history=0),
    "nvme/2.11/samsung-pm9a3":
        dict(model=_PM9A3, temp=41, avail=100, used=0, ok=True,  history=0),
    "nvme/2.11/used-stock-drive":
        # 102 GB written (data_units_written=200000 * 512 kB) is trivial burn-in on a
        # 960 GB drive, so the corrected ~1 TB threshold no longer flags "significant
        # lifetime writes" -> 2 history flags, not 3 (step 25 data_units_written fix).
        dict(model=_PM9A3, temp=47, avail=100, used=1, ok=False, history=2),
}


@pytest.fixture
def stub_nvme_tool(monkeypatch):
    def _install(case_dir: str):
        smart, idctrl = _read(case_dir, "smart-log.json"), _read(case_dir, "id-ctrl.json")

        def fake_run(cmd, *a, **k):
            out = smart if cmd[1] == "smart-log" else idctrl
            return SimpleNamespace(stdout=out, stderr="", returncode=0)

        monkeypatch.setattr(nvme_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(nvme_mod.shutil, "which", lambda name: f"/usr/sbin/{name}")
    return _install


@pytest.mark.parametrize("case_dir, exp", list(NVME_CASES.items()))
def test_nvme_corpus_decodes(stub_nvme_tool, case_dir, exp):
    stub_nvme_tool(case_dir)
    h = check_nvme("/dev/nvme0", mock=False)
    assert h.model == exp["model"]
    assert h.smart["temperature"] == exp["temp"]        # Kelvin -> Celsius fixup
    assert h.smart["available_spare"] == exp["avail"]   # avail_spare alias decoded
    assert h.smart["percentage_used"] == exp["used"]    # percent_used alias decoded
    assert h.ok is exp["ok"]
    assert len(h.history) == exp["history"]


# --- Ethernet: replay real ethtool output through check_ethernet(mock=False) -------- #
ETH_CASES = {
    "ethtool/6.7/bcm89xx-good":     dict(speed=1000, rx=0,  tx=0, role="master", ok=True),
    "ethtool/6.7/bcm89xx-degraded": dict(speed=1000, rx=40, tx=5, role="master", ok=False),
}


@pytest.fixture
def stub_ethtool(monkeypatch):
    def _install(case_dir: str):
        link, stats = _read(case_dir, "link.txt"), _read(case_dir, "stats.txt")

        def fake_run(cmd, *a, **k):
            out = stats if "-S" in cmd else link
            return SimpleNamespace(stdout=out, stderr="", returncode=0)

        monkeypatch.setattr(eth_mod.subprocess, "run", fake_run)
        monkeypatch.setattr(eth_mod.shutil, "which", lambda name: f"/usr/sbin/{name}")
    return _install


@pytest.mark.parametrize("case_dir, exp", list(ETH_CASES.items()))
def test_ethtool_corpus_decodes(stub_ethtool, case_dir, exp):
    stub_ethtool(case_dir)
    h = check_ethernet("eth0", expect_mbps=1000, mock=False)
    assert h.speed_mbps == exp["speed"]
    assert h.rx_errors == exp["rx"]    # the exact-match counter, NOT rx_errors_phy
    assert h.tx_errors == exp["tx"]
    assert h.role == exp["role"]
    assert h.ok is exp["ok"]


def test_ethtool_degraded_ignores_phy_superstring(stub_ethtool):
    """End-to-end: the degraded capture has rx_errors_phy: 999; the real path reads 40."""
    stub_ethtool("ethtool/6.7/bcm89xx-degraded")
    h = check_ethernet("eth0", mock=False)
    assert h.rx_errors == 40
