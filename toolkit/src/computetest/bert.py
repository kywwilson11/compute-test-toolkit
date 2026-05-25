"""
BERT orchestrator: arm -> stress -> read -> decide, to a confidence target.

Ties the pieces together:
  * arm/clear via aer.clear (write-1-to-clear the set bits)
  * count correctable events and watch for uncorrectable errors
  * account bits transferred from link_speed x link_width x elapsed
  * feed (errors, bits) to ber.assess until it says pass/fail, or time runs out

Two engines:
  * "python" (default) drives the loop through the Backend, so it runs identically
    on the mock (laptop) and on real Linux. Polling in Python is fine for the mock
    and for moderate error rates on hardware.
  * "c" shells out to the compiled c/pcie_bert for tight, high-rate polling on real
    hardware (errors faster than a Python loop can clear-and-recount).
"""
from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field

from . import aer, ber
from .backend import Backend, link_bits_per_second


@dataclass
class BertResult:
    bdf: str
    seconds: float
    bits: float
    correctable: int
    uncorrectable: int
    per_correctable: dict[str, int]
    link_speed_code: int
    link_width: int
    verdict: ber.BertVerdict
    stuck: bool = False
    uncorrectable_decode: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return self.verdict.status

    def to_dict(self) -> dict:
        return {
            "bdf": self.bdf, "seconds": round(self.seconds, 3), "bits": self.bits,
            "correctable": self.correctable, "uncorrectable": self.uncorrectable,
            "per_correctable": self.per_correctable,
            "link": f"Gen{self.link_speed_code} x{self.link_width}",
            "confidence": round(self.verdict.confidence_reached, 4),
            "ber_upper_bound": self.verdict.ber_upper,
            "uncorrectable_decode": self.uncorrectable_decode,
            "stuck_bits": self.stuck, "status": self.status,
        }

    def summary(self) -> str:
        extra = ""
        if self.uncorrectable:
            extra = f" UNCORR:{','.join(self.uncorrectable_decode)}"
        elif self.correctable:
            extra = " cor:" + ",".join(f"{k}={v}" for k, v in self.per_correctable.items())
        return (f"{self.bdf} Gen{self.link_speed_code}x{self.link_width} "
                f"{self.seconds:.1f}s n={self.bits:.2e} {self.verdict.summary()}{extra}")


def run_bert(backend: Backend, bdf: str, *, target_ber: float = 1e-12,
             confidence: float = 0.95, max_seconds: float = 30.0,
             poll_s: float = 0.002, engine: str = "python") -> BertResult:
    """Run the BERT on one BDF and return a verdict.

    PASS  = reached the confidence target with no uncorrectable errors.
    FAIL  = any uncorrectable error, OR ran out of time without reaching confidence.
    """
    if engine == "c":
        return _run_c_engine(backend, bdf, target_ber=target_ber,
                             confidence=confidence, seconds=max_seconds)

    dev = backend.get_device(bdf)
    bps = link_bits_per_second(dev.current_link_speed, dev.current_link_width)

    aer.clear(backend, bdf)  # arm both status latches
    base = aer.aer_base(backend, bdf)

    per: dict[str, int] = {}
    cor_total = unc_total = 0
    unc_bits_seen = 0
    stuck = False

    t0 = time.monotonic()
    while True:
        elapsed = time.monotonic() - t0
        snap = aer.snapshot(backend, bdf)
        if snap.correctable_raw:
            for _, name, _ in snap.correctable:
                per[name] = per.get(name, 0) + 1
                cor_total += 1
            res = aer.clear(backend, bdf, uncorrectable=False)  # re-arm fast
            if res.get("correctable") and not res["correctable"].ok:
                stuck = True
        if snap.uncorrectable_raw:
            unc_total += 1
            unc_bits_seen |= snap.uncorrectable_raw
            aer.clear(backend, bdf, correctable=False)

        bits = bps * elapsed
        verdict = ber.assess(cor_total, bits, target_ber, confidence, unc_total)

        if verdict.status == "fail":
            break
        if verdict.status == "pass":
            break
        if elapsed >= max_seconds:
            # Ran out of time without proving the target -> fail (inconclusive).
            verdict.status = "fail"
            break
        if poll_s:
            time.sleep(poll_s)

    unc_decode = [n for _, n, _ in aer.decode_uncorrectable(unc_bits_seen)]
    return BertResult(bdf, time.monotonic() - t0, bps * (time.monotonic() - t0),
                      cor_total, unc_total, per, dev.current_link_speed,
                      dev.current_link_width, verdict, stuck, unc_decode)


def _run_c_engine(backend: Backend, bdf: str, *, target_ber: float,
                  confidence: float, seconds: float,
                  binary: str = "c/pcie_bert") -> BertResult:
    """Run the compiled C engine for one fixed window, then assess in Python."""
    proc = subprocess.run([binary, "-d", bdf, "-t", str(seconds), "--json"],
                          capture_output=True, text=True, timeout=seconds + 30)
    data = json.loads(proc.stdout)
    verdict = ber.assess(data["correctable"], data["bits"], target_ber,
                         confidence, data["uncorrectable"])
    unc_decode = [n for _, n, _ in aer.decode_uncorrectable(data.get("uncorrectable_bits", 0))]
    return BertResult(
        data["bdf"], data["seconds"], data["bits"], data["correctable"],
        data["uncorrectable"], data.get("per_correctable", {}),
        data["link_speed_code"], data["link_width"], verdict,
        bool(data.get("stuck_correctable") or data.get("stuck_uncorrectable")),
        unc_decode)


def run_many(backend: Backend, bdfs: list[str], **kw) -> list[BertResult]:
    """Run the BERT across several BDFs (sequentially; the harness parallelizes)."""
    return [run_bert(backend, bdf, **kw) for bdf in bdfs]
