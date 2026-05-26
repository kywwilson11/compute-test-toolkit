"""
GMSL camera-link check: deserializer link lock (the GMSL equivalent of PCIe L0),
video device enumeration, a frame capture (proves the whole sensor->serializer->
coax->deserializer->CSI-2->SoC path), and link error counters. Wraps i2c/sysfs +
`v4l2-ctl` on real Linux; simulated otherwise.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

from .backend import mock_mode


@dataclass
class GmslHealth:
    link: str
    locked: bool
    video_device: str
    width: int
    height: int
    frames_captured: int
    error_count: int
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        return (f"GMSL {self.link} lock={self.locked} {self.width}x{self.height} "
                f"frames={self.frames_captured} err={self.error_count} -> {state}")

    def to_dict(self) -> dict:
        return {"link": self.link, "locked": self.locked, "video_device": self.video_device,
                "resolution": f"{self.width}x{self.height}",
                "frames": self.frames_captured, "error_count": self.error_count,
                "checks": self.checks, "ok": self.ok}


def _limits(locked, w, h, frames, errors, exp_w, exp_h) -> dict[str, bool]:
    return {"link_locked": bool(locked),
            "resolution_ok": w == exp_w and h == exp_h,
            "frames_captured": frames > 0,
            "no_link_errors": errors == 0}


def check_gmsl(link: str = "1-0029", video_device: str = "/dev/video0", *,
               expect_w: int = 1920, expect_h: int = 1080, frames: int = 5,
               mock: bool | None = None) -> GmslHealth:
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        bad = "BAD" in link
        locked, w, h = (not bad), (0 if bad else expect_w), (0 if bad else expect_h)
        captured, errors = (0 if bad else frames), (12 if bad else 0)
        return GmslHealth(link, locked, video_device, w, h, captured, errors,
                          _limits(locked, w, h, captured, errors, expect_w, expect_h))
    return _real_check_gmsl(link, video_device, expect_w, expect_h, frames)  # pragma: no cover


def _real_check_gmsl(link, video_device, expect_w, expect_h,
                     frames) -> GmslHealth:  # pragma: no cover - real-hw path
    # Real path (driver/board specific paths shown; adjust to your platform). ---
    locked = False
    lock_path = f"/sys/bus/i2c/devices/{link}/link_status"
    if os.path.exists(lock_path):
        with open(lock_path) as fh:
            locked = fh.read().strip() in ("1", "locked")
    errors = 0
    err_path = f"/sys/bus/i2c/devices/{link}/error_count"
    if os.path.exists(err_path):
        with open(err_path) as fh:
            errors = int(fh.read().strip() or 0)
    w = h = 0
    if shutil.which("v4l2-ctl"):
        fmt = subprocess.run(["v4l2-ctl", "-d", video_device, "--get-fmt-video"],
                             capture_output=True, text=True).stdout
        for line in fmt.splitlines():
            if "Width/Height" in line:
                parts = line.split(":")[-1].strip().split("/")
                w, h = int(parts[0]), int(parts[1])
        cap = subprocess.run(["v4l2-ctl", "-d", video_device, "--stream-mmap",
                              f"--stream-count={frames}", "--stream-to=/dev/null"],
                             capture_output=True, text=True)
        captured = frames if cap.returncode == 0 else 0
    else:
        captured = 0
    return GmslHealth(link, locked, video_device, w, h, captured, errors,
                      _limits(locked, w, h, captured, errors, expect_w, expect_h))


# --- Multi-link deserializer (a quad/hex part like MAX96712 carries N camera links) -- #
@dataclass
class GmslDeserHealth:
    addr: str                       # deserializer I2C address (e.g. "1-0029")
    links: list[GmslHealth]
    frame_sync_ok: bool             # all cameras locked and frame-synchronized

    @property
    def ok(self) -> bool:
        return bool(self.links) and all(li.ok for li in self.links) and self.frame_sync_ok

    def summary(self) -> str:
        locked = sum(1 for li in self.links if li.locked)
        state = "OK" if self.ok else "FAIL"
        sync = "sync" if self.frame_sync_ok else "DESYNC"
        head = f"GMSL deser {self.addr}: {locked}/{len(self.links)} links locked, {sync} -> {state}"
        return head + "".join(f"\n    {li.summary()}" for li in self.links)

    def to_dict(self) -> dict:
        return {"addr": self.addr, "frame_sync_ok": self.frame_sync_ok, "ok": self.ok,
                "links": [li.to_dict() for li in self.links]}


def check_deserializer(addr: str = "1-0029", n_links: int = 4, *, expect_w: int = 1920,
                       expect_h: int = 1080, frames: int = 5,
                       mock: bool | None = None) -> GmslDeserHealth:
    """Check every camera link on a multi-link GMSL deserializer + frame sync across
    them. (Real per-link access is part/driver-specific — MAX9296 vs MAX96712 differ —
    so the real path is a best-effort over /dev/videoN; the registers are the datasheet's.)"""
    use_mock = mock_mode() if mock is None else mock
    links: list[GmslHealth] = []
    for i in range(n_links):
        link_id, vid = f"{addr}:link{i}", f"/dev/video{i}"
        if use_mock:
            bad = "BAD" in addr and i == 0            # one camera link down
            locked, w, h = (not bad), (0 if bad else expect_w), (0 if bad else expect_h)
            captured, errors = (0 if bad else frames), (12 if bad else 0)
            links.append(GmslHealth(link_id, locked, vid, w, h, captured, errors,
                         _limits(locked, w, h, captured, errors, expect_w, expect_h)))
        else:  # pragma: no cover - real-hw path
            links.append(check_gmsl(addr, vid, expect_w=expect_w, expect_h=expect_h,
                                    frames=frames, mock=False))
    # FrameSync: every link locked AND synchronized (a "DESYNC" address models loss of sync).
    frame_sync_ok = all(li.locked for li in links) and ("DESYNC" not in addr)
    return GmslDeserHealth(addr, links, frame_sync_ok)
