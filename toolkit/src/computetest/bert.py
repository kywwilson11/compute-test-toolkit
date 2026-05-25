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
from collections.abc import Callable
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
    aer_available: bool = True
    note: str = ""

    @property
    def status(self) -> str:
        # No AER capability => we couldn't measure errors; "skip" (never a false pass).
        return self.verdict.status if self.aer_available else "skip"

    @property
    def ok(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict:
        return {
            "bdf": self.bdf, "seconds": round(self.seconds, 3), "bits": self.bits,
            "correctable": self.correctable, "uncorrectable": self.uncorrectable,
            "per_correctable": self.per_correctable,
            "link": f"Gen{self.link_speed_code} x{self.link_width}",
            "confidence": round(self.verdict.confidence_reached, 4),
            "ber_upper_bound": self.verdict.ber_upper,
            "uncorrectable_decode": self.uncorrectable_decode,
            "stuck_bits": self.stuck, "aer_available": self.aer_available,
            "note": self.note, "status": self.status,
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
             poll_s: float = 0.002, engine: str = "python",
             clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep) -> BertResult:
    """Run the BERT on one BDF and return a verdict.

    PASS  = reached the confidence target with no uncorrectable errors.
    FAIL  = any uncorrectable error, OR ran out of time without reaching confidence.

    Counting model: an AER status bit is a latch (">=1 error of THIS type since the
    last clear"), not a counter. Each distinct correctable bit set in a poll counts as
    one error of that type — counting set bits is a tighter lower bound than 1-per-poll
    and is exact at the low rates where a unit passes. Same-type repeats within a clear
    window are not separable from AER status alone; for exact high-rate counts use
    device-native counters (switch per-lane, GPU replay). Uncorrectable errors are
    counted once per distinct type (any uncorrectable => FAIL regardless of count).

    Devices without an AER capability return status "skip" (we never claim PASS from
    zero errors we couldn't actually measure).

    ``clock``/``sleep`` are injectable so tests can drive the loop deterministically.
    """
    if engine == "c":
        return _run_c_engine(backend, bdf, target_ber=target_ber,
                             confidence=confidence, seconds=max_seconds)

    dev = backend.get_device(bdf)
    bps = link_bits_per_second(dev.current_link_speed, dev.current_link_width)
    # Gen6+ uses PAM4 + FLIT + forward error correction: many symbol errors are
    # FEC-corrected and never become Bad-TLP/replay events, so AER-based BER counting
    # under-measures. Flag it (Gen1-5 NRZ links are measured faithfully).
    gen_note = ("Gen6+ FLIT/FEC: AER LCRC-retry counting under-measures BER; "
                "use FEC/symbol statistics") if dev.current_link_speed >= 6 else ""

    if aer.aer_base(backend, bdf) is None:
        v = ber.BertVerdict(0, 0.0, target_ber, confidence, 0.0,
                            float("inf"), float("inf"), "skip")
        return BertResult(bdf, 0.0, 0.0, 0, 0, {}, dev.current_link_speed,
                          dev.current_link_width, v, aer_available=False,
                          note="no AER capability; use device-native error counters")

    aer.clear(backend, bdf)  # arm both status latches before measuring

    per: dict[str, int] = {}
    cor_total = unc_total = 0
    unc_bits_seen = 0
    stuck = False

    t0 = clock()
    elapsed = 0.0
    while True:
        elapsed = clock() - t0
        snap = aer.snapshot(backend, bdf)
        if snap.correctable_raw:
            for _, name, _ in snap.correctable:    # each distinct bit = >=1 error of that type
                per[name] = per.get(name, 0) + 1
                cor_total += 1
            res = aer.clear(backend, bdf, uncorrectable=False)  # re-arm fast
            if res.get("correctable") and not res["correctable"].ok:
                stuck = True
        if snap.uncorrectable_raw:
            new_unc = snap.uncorrectable_raw & ~unc_bits_seen   # count each type once
            unc_total += bin(new_unc).count("1")
            unc_bits_seen |= snap.uncorrectable_raw
            aer.clear(backend, bdf, correctable=False)

        bits = bps * elapsed
        verdict = ber.assess(cor_total, bits, target_ber, confidence, unc_total)

        if verdict.status in ("pass", "fail"):
            break
        if elapsed >= max_seconds:
            verdict.status = "fail"                # out of time without proving target
            break
        if poll_s > 0:
            sleep(poll_s)

    unc_decode = [n for _, n, _ in aer.decode_uncorrectable(unc_bits_seen)]
    # Report the SAME bits the verdict was decided on (one elapsed value, no re-reads).
    return BertResult(bdf, elapsed, verdict.bits, cor_total, unc_total, per,
                      dev.current_link_speed, dev.current_link_width, verdict,
                      stuck, unc_decode, note=gen_note)


def _run_c_engine(backend: Backend, bdf: str, *, target_ber: float,
                  confidence: float, seconds: float,
                  binary: str = "c/pcie_bert") -> BertResult:
    """Run the compiled C engine for one fixed window, then assess in Python."""
    try:
        proc = subprocess.run([binary, "-d", bdf, "-t", str(seconds), "--json"],
                              capture_output=True, text=True, timeout=seconds + 30)
    except FileNotFoundError as e:
        raise RuntimeError(f"C engine not found: {binary} (build it with: make -C c)") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"C engine timed out after {seconds + 30}s") from e
    if proc.returncode not in (0, 3):   # 0 = clean, 3 = uncorrectable seen (valid data)
        raise RuntimeError(f"C engine failed (rc={proc.returncode}): {proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"C engine produced invalid JSON: {proc.stdout!r}") from e
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
