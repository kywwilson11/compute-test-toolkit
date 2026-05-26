"""
Backend contract suite — the linchpin of Phase 1 (docs/realpath-simulation.md).

The SAME behavioral assertions run against BOTH MockBackend and a RealBackend pointed at a
fixture sysfs tree describing the SAME logical device. If RealBackend's sysfs/config parsing
ever drifts from what the mock promises, these go red — on the laptop, no hardware. This is
the discipline whose absence let the avail_spare / rx_errors_phy class of bug live unnoticed
in `# pragma: no cover` code.
"""
from __future__ import annotations

import pytest

from computetest.backend import (
    AER_CORR_STATUS, CAP_PCIE, ECAP_AER, LinkStatus, MockBackend, MockDevice, PORT_ENDPOINT,
    PORT_ROOT, PciDevice, RealBackend,
)
from sysfs_fixture import build_pci_tree

BDF = "0000:03:00.0"
_REAL_SPEC = dict(bdf=BDF, vendor=0x10DE, device=0x2204, class_code=0x030000,
                  speed_code=4, width=16, driver="nvidia", port_type=PORT_ENDPOINT)
_EXPECTED_DEVICE = PciDevice(BDF, 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, "nvidia")
_EXPECTED_LINK = LinkStatus(4, 16, False, False, False, True)


@pytest.fixture(params=["mock", "real"])
def backend(request, tmp_path):
    """The same logical device, as a MockBackend and as a RealBackend over a fixture tree."""
    if request.param == "mock":
        return MockBackend([MockDevice(BDF, 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, "nvidia")])
    return RealBackend(sys_root=build_pci_tree(tmp_path, [_REAL_SPEC]))


# --- the shared contract: both backends must agree --------------------------------- #
def test_list_devices(backend):
    assert backend.list_devices() == [BDF]


def test_get_device(backend):
    assert backend.get_device(BDF) == _EXPECTED_DEVICE


def test_vendor_name_and_speed_str(backend):
    d = backend.get_device(BDF)
    assert d.vendor_name == "NVIDIA"
    assert d.speed_str == "16 GT/s"


def test_find_aer_cap(backend):
    assert backend.find_ext_cap(BDF, ECAP_AER) == 0x100


def test_read_link_status(backend):
    assert backend.read_link_status(BDF) == _EXPECTED_LINK


def test_read_port_type(backend):
    assert backend.read_port_type(BDF) == PORT_ENDPOINT


def test_device_status_clean(backend):
    assert backend.read_device_status(BDF) == 0


def test_link_chain_single_device(backend):
    assert backend.link_chain(BDF) == [BDF]


def test_link_chain_root_to_endpoint(tmp_path):
    """A 2-level topology: both backends must report root -> endpoint in order."""
    root, ep = "0000:00:1c.0", "0000:03:00.0"
    mock = MockBackend([
        MockDevice(root, 0x8086, 0x0001, 0x060400, 4, 16, 4, 16, "pcieport", port_type=PORT_ROOT),
        MockDevice(ep, 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, "nvidia", parent=root),
    ])
    real = RealBackend(sys_root=build_pci_tree(tmp_path, [
        dict(bdf=root, vendor=0x8086, device=0x0001, class_code=0x060400, speed_code=4,
             width=16, driver="pcieport", port_type=PORT_ROOT),
        dict(bdf=ep, vendor=0x10DE, device=0x2204, class_code=0x030000, speed_code=4,
             width=16, driver="nvidia", port_type=PORT_ENDPOINT, parent=root),
    ]))
    assert mock.link_chain(ep) == [root, ep]
    assert real.link_chain(ep) == [root, ep]


# --- RealBackend-only paths the mock doesn't model (lifts more `# pragma: no cover`) -- #
def test_real_config_read_write_roundtrip(tmp_path):
    be = RealBackend(sys_root=build_pci_tree(tmp_path, [_REAL_SPEC]))
    be.write_config(BDF, 0x10, 0xABCD, 2)               # os.open/lseek/os.write
    assert be.read_config(BDF, 0x10, 2) == 0xABCD       # open/seek/read


def test_real_find_legacy_pcie_cap(tmp_path):
    be = RealBackend(sys_root=build_pci_tree(tmp_path, [_REAL_SPEC]))
    assert be.find_cap(BDF, CAP_PCIE) == 0x40


def test_real_reads_injected_aer_status(tmp_path):
    spec = {**_REAL_SPEC, "cor_status": (1 << 6) | (1 << 7)}   # BadTLP + BadDLLP latched
    be = RealBackend(sys_root=build_pci_tree(tmp_path, [spec]))
    aer = be.find_ext_cap(BDF, ECAP_AER)
    assert be.read_config(BDF, aer + AER_CORR_STATUS, 4) == (1 << 6) | (1 << 7)


def test_real_no_aer_cap_returns_none(tmp_path):
    be = RealBackend(sys_root=build_pci_tree(tmp_path, [{**_REAL_SPEC, "aer_cap": False}]))
    assert be.find_ext_cap(BDF, ECAP_AER) is None


def test_real_no_pcie_cap_fallbacks(tmp_path):
    be = RealBackend(sys_root=build_pci_tree(
        tmp_path, [{**_REAL_SPEC, "pcie_cap": False, "aer_cap": False}]))
    assert be.find_cap(BDF, CAP_PCIE) is None
    assert be.read_device_status(BDF) is None
    assert be.read_port_type(BDF) == PORT_ENDPOINT
    ls = be.read_link_status(BDF)                       # falls back to sysfs speed/width
    assert ls.speed == 4 and ls.width == 16
