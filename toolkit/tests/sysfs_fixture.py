"""
Build a fake /sys/bus/pci/devices tree in a tmp dir so RealBackend's real path — sysfs
attribute reads, config-space parsing via seek/read, the driver symlink, and the realpath
link-chain — runs off-hardware, on macOS too. Real files and real symlinks; config space
is a byte-accurate blob with a PCIe capability (at 0x40) and an AER extended capability
(at 0x100), placed at exactly the offsets backend.py reads.

This is the substrate for the Mock<->Real contract suite (test_backend_contract.py): one
logical device is described once and built for whichever backend the test parametrizes.
"""
from __future__ import annotations

import os
import struct

from computetest.backend import (
    AER_CORR_STATUS, AER_UNCORR_STATUS, CAP_PCIE, LINK_SPEED_GTPS, LNKSTA_DLLLA, LNKSTA_LABS,
    LNKSTA_LBMS, LNKSTA_TRAINING, PCIE_CAP_FLAGS, PCIE_DEV_STATUS, PCIE_LINK_STATUS,
    PORT_ENDPOINT,
)

PCIE_CAP_OFF = 0x40    # legacy PCIe capability base
AER_CAP_OFF = 0x100    # AER extended capability base (start of extended config space)
_ECAP_AER_ID = 0x0001

_CFG_FLAGS = ("port_type", "pcie_cap", "aer_cap", "dev_status", "cor_status", "unc_status",
              "link_train", "bw_changed", "dl_active")


def build_config(*, speed_code: int = 4, width: int = 16, port_type: int = PORT_ENDPOINT,
                 pcie_cap: bool = True, aer_cap: bool = True, dev_status: int = 0,
                 cor_status: int = 0, unc_status: int = 0, link_train: bool = False,
                 bw_changed: bool = False, dl_active: bool = True) -> bytes:
    """A 4 KB config-space image laid out exactly where backend.py looks."""
    cfg = bytearray(4096)
    if pcie_cap:
        cfg[0x34] = PCIE_CAP_OFF                          # capabilities pointer
        cfg[PCIE_CAP_OFF] = CAP_PCIE                      # cap id 0x10
        cfg[PCIE_CAP_OFF + 1] = 0x00                      # next = end of list
        struct.pack_into("<H", cfg, PCIE_CAP_OFF + PCIE_CAP_FLAGS, (port_type & 0xF) << 4)
        struct.pack_into("<H", cfg, PCIE_CAP_OFF + PCIE_DEV_STATUS, dev_status & 0xFFFF)
        ls = (speed_code & 0xF) | ((width & 0x3F) << 4)
        ls |= LNKSTA_DLLLA if dl_active else 0
        ls |= LNKSTA_TRAINING if link_train else 0
        ls |= (LNKSTA_LBMS | LNKSTA_LABS) if bw_changed else 0
        struct.pack_into("<H", cfg, PCIE_CAP_OFF + PCIE_LINK_STATUS, ls)
    if aer_cap:
        struct.pack_into("<I", cfg, AER_CAP_OFF, _ECAP_AER_ID | (0x1 << 16))  # id, ver 1, next 0
        struct.pack_into("<I", cfg, AER_CAP_OFF + AER_UNCORR_STATUS, unc_status & 0xFFFFFFFF)
        struct.pack_into("<I", cfg, AER_CAP_OFF + AER_CORR_STATUS, cor_status & 0xFFFFFFFF)
    return bytes(cfg)


def _write(path: str, text: str) -> None:
    with open(path, "w") as fh:
        fh.write(text)


def _write_device_files(d: str, spec: dict) -> None:
    sc, width = spec.get("speed_code", 4), spec.get("width", 16)
    _write(os.path.join(d, "vendor"), f"0x{spec['vendor']:04x}\n")
    _write(os.path.join(d, "device"), f"0x{spec['device']:04x}\n")
    _write(os.path.join(d, "class"), f"0x{spec['class_code']:06x}\n")
    _write(os.path.join(d, "current_link_speed"), f"{LINK_SPEED_GTPS[sc]:g} GT/s\n")
    _write(os.path.join(d, "current_link_width"), f"{width}\n")
    msc = spec.get("max_speed_code", sc)
    _write(os.path.join(d, "max_link_speed"), f"{LINK_SPEED_GTPS[msc]:g} GT/s\n")
    _write(os.path.join(d, "max_link_width"), f"{spec.get('max_width', width)}\n")
    if spec.get("driver"):
        # target need not exist; RealBackend only reads the link's basename
        os.symlink(f"../../../bus/pci/drivers/{spec['driver']}", os.path.join(d, "driver"))
    cfg_kwargs = {k: spec[k] for k in _CFG_FLAGS if k in spec}
    with open(os.path.join(d, "config"), "wb") as fh:
        fh.write(build_config(speed_code=sc, width=width, **cfg_kwargs))


def build_pci_tree(base, specs: list[dict]) -> str:
    """Create the tree under ``base`` and return the sys_root (``.../bus/pci/devices``).

    Each spec: required ``bdf, vendor, device, class_code``; optional ``speed_code, width,
    driver, parent`` and any build_config flag. Order specs root-first so a child nests
    inside its parent's real directory — which is what makes RealBackend.link_chain's
    realpath walk return the full root->endpoint path."""
    base = str(base)
    sys_root = os.path.join(base, "bus", "pci", "devices")
    os.makedirs(sys_root, exist_ok=True)
    real_dir: dict[str, str] = {}
    for spec in specs:
        bdf = spec["bdf"]
        parent = spec.get("parent")
        d = (os.path.join(real_dir[parent], bdf) if parent
             else os.path.join(base, "devices", "pci0000:00", bdf))
        os.makedirs(d, exist_ok=True)
        real_dir[bdf] = d
        _write_device_files(d, spec)
        link = os.path.join(sys_root, bdf)
        if not os.path.lexists(link):
            os.symlink(d, link)
    return sys_root
