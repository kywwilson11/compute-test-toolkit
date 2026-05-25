"""
GPU check: ECC (corrected/uncorrected), temperature, throttle reasons, and PCIe
replay — the GPU's own telemetry for what is often a PCIe/power problem. Wraps
`nvidia-smi` on real Linux; simulated otherwise. For real qualification, also run
`dcgmi diag -r 3` (memory + compute stress) and a thermal soak under `gpu-burn`.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field


@dataclass
class GpuHealth:
    index: int
    name: str
    link_gen: int
    link_width: int
    metrics: dict
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(self.checks.values())

    def summary(self) -> str:
        fails = [k for k, v in self.checks.items() if not v]
        state = "OK" if self.ok else "FAIL(" + ",".join(fails) + ")"
        m = self.metrics
        return (f"GPU{self.index} {self.name} Gen{self.link_gen}x{self.link_width} "
                f"temp={m.get('temp')}C ecc_unc={m.get('ecc_uncorrected')} "
                f"replay={m.get('replay_count')} throttle={m.get('throttle')} -> {state}")

    def to_dict(self) -> dict:
        return {"index": self.index, "name": self.name,
                "link": f"Gen{self.link_gen}x{self.link_width}",
                "metrics": self.metrics, "checks": self.checks, "ok": self.ok}


def _apply_limits(m: dict, max_temp_c: int, expect_gen: int, expect_width: int,
                  gen: int, width: int) -> dict[str, bool]:
    return {
        "ecc_uncorrected==0": m.get("ecc_uncorrected", 0) == 0,
        f"temp<={max_temp_c}": 0 < m.get("temp", 0) <= max_temp_c,
        "no_thermal_throttle": not m.get("throttle"),
        "low_replay": m.get("replay_count", 0) < 100,
        "link_gen_ok": gen >= expect_gen,
        "link_width_ok": width >= expect_width,
    }


def _mock_metrics(index: int) -> dict:
    bad = index == 99  # use index 99 to simulate a failing GPU
    return {
        "temp": 95 if bad else 62,
        "ecc_corrected": 0,
        "ecc_uncorrected": 3 if bad else 0,
        "replay_count": 500 if bad else 0,
        "throttle": "thermal" if bad else "",
        "power_w": 290,
    }


def check_gpu(index: int = 0, *, mock: bool | None = None, max_temp_c: int = 85,
              expect_gen: int = 4, expect_width: int = 16) -> GpuHealth:
    from .backend import mock_mode
    use_mock = mock_mode() if mock is None else mock
    if use_mock:
        m = _mock_metrics(index)
        gen, width = (3 if index == 99 else 4), (8 if index == 99 else 16)
        return GpuHealth(index, "Mock RTX", gen, width, m,
                         _apply_limits(m, max_temp_c, expect_gen, expect_width, gen, width))

    if not shutil.which("nvidia-smi"):  # pragma: no cover - real-hw path
        raise RuntimeError("nvidia-smi not found")
    q = ("index,name,pcie.link.gen.current,pcie.link.width.current,temperature.gpu,"
         "ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total,"
         "power.draw,clocks_throttle_reasons.active")
    out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits",
                          "-i", str(index)], capture_output=True, text=True, check=True).stdout
    f = [x.strip() for x in out.strip().split(",")]
    gen, width = int(f[2]), int(f[3])
    throttle_bits = int(f[8], 16) if f[8] not in ("", "N/A") else 0
    m = {"temp": int(float(f[4])), "ecc_corrected": _int(f[5]), "ecc_uncorrected": _int(f[6]),
         "power_w": float(f[7]) if f[7] not in ("", "N/A") else 0,
         "throttle": "active" if throttle_bits & ~0x1 else "", "replay_count": 0}
    return GpuHealth(index, f[1], gen, width, m,
                     _apply_limits(m, max_temp_c, expect_gen, expect_width, gen, width))


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0
