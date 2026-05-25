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
    stuck: bool = False          # an error was present at IDLE (constant fault / severe SI)
    uncorrectable_decode: list[str] = field(default_factory=list)
    aer_available: bool = True   # False only if neither AER nor Device Status is present
    aer_source: str = "aer"      # "aer" (rich) | "devstatus" (coarse fallback) | "none"
    note: str = ""

    @property
    def status(self) -> str:
        # No error source at all => we couldn't measure; "skip" (never a false pass).
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
            "idle_errors": self.stuck, "error_source": self.aer_source,
            "aer_available": self.aer_available, "note": self.note,
            "status": self.status,
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
             margin: float = 1.2, extend_budget: float = 3.0, c_runner=None,
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
        return run_conductor(backend, bdf, target_ber=target_ber, confidence=confidence,
                             max_seconds=max_seconds, margin=margin,
                             extend_budget=extend_budget, c_runner=c_runner)

    dev = backend.get_device(bdf)
    bps = link_bits_per_second(dev.current_link_speed, dev.current_link_width)
    # Gen6+ uses PAM4 + FLIT + forward error correction: many symbol errors are
    # FEC-corrected and never become Bad-TLP/replay events, so AER-based BER counting
    # under-measures. Flag it (Gen1-5 NRZ links are measured faithfully).
    gen_note = ("Gen6+ FLIT/FEC: AER LCRC-retry counting under-measures BER; "
                "use FEC/symbol statistics") if dev.current_link_speed >= 6 else ""

    source = aer.error_source(backend, bdf)   # "aer" (rich) | "devstatus" (coarse) | "none"
    if source == "none":
        v = ber.BertVerdict(0, 0.0, target_ber, confidence, 0.0,
                            float("inf"), float("inf"), "skip")
        return BertResult(bdf, 0.0, 0.0, 0, 0, {}, dev.current_link_speed,
                          dev.current_link_width, v, aer_available=False,
                          aer_source="none",
                          note="no PCIe error source (neither AER nor Device Status)")

    # --- Idle baseline (begin): quiesce the link; bits set with no exercise are a
    #     constant fault (or severe marginality), NOT a per-error rate. We record and
    #     exclude them from the rate count so a real fault can't run the count away —
    #     and a high error rate under load is still counted, never masked.
    backend.set_exercising(bdf, False)
    aer.clear_errors(backend, bdf, source)
    idle0 = aer.read_errors(backend, bdf, source)
    idle_cor, idle_unc = idle0.correctable_raw, idle0.uncorrectable_raw

    # --- Exercise window.
    backend.set_exercising(bdf, True)
    aer.clear_errors(backend, bdf, source)   # start the measurement window clean

    per: dict[str, int] = {}
    cor_total = unc_total = 0
    unc_bits_seen = 0

    t0 = clock()
    elapsed = 0.0
    while True:
        elapsed = clock() - t0
        r = aer.read_errors(backend, bdf, source)
        rate_cor = r.correctable_raw & ~idle_cor        # exclude constant/idle bits
        if rate_cor:
            for _, name, _ in aer.ErrorReading(rate_cor, 0, source).correctable:
                per[name] = per.get(name, 0) + 1         # each distinct bit = >=1 error
                cor_total += 1
        if r.has_uncorrectable:
            new_unc = r.uncorrectable_raw & ~unc_bits_seen
            unc_total += bin(new_unc).count("1")         # count each type once
            unc_bits_seen |= r.uncorrectable_raw
        if r.has_correctable or r.has_uncorrectable:
            aer.clear_errors(backend, bdf, source)       # re-arm fast

        bits = bps * elapsed
        verdict = ber.assess(cor_total, bits, target_ber, confidence, unc_total)
        if verdict.status in ("pass", "fail"):
            break
        if elapsed >= max_seconds:
            verdict.status = "fail"                       # out of time, target not proven
            break
        if poll_s > 0:
            sleep(poll_s)

    # --- Idle baseline (end): errors still present with no traffic = a real fault.
    backend.set_exercising(bdf, False)
    aer.clear_errors(backend, bdf, source)
    idle1 = aer.read_errors(backend, bdf, source)
    idle_cor |= idle1.correctable_raw
    idle_unc |= idle1.uncorrectable_raw
    idle_fault = bool(idle_cor or idle_unc)
    if idle_fault and verdict.status != "fail":
        verdict.status = "fail"   # errors at idle => constant fault / severe SI

    note = gen_note
    if idle_fault:
        idle_names = ([n for _, n, _ in aer.ErrorReading(idle_cor, 0, source).correctable]
                      + [n for _, n, _ in aer.ErrorReading(0, idle_unc, source).uncorrectable])
        note = ((note + "; ") if note else "") + \
               "errors present at idle (constant fault): " + ",".join(idle_names)

    unc_decode = [n for _, n, _ in aer.ErrorReading(0, unc_bits_seen | idle_unc, source).uncorrectable]
    # Report the SAME bits the verdict was decided on (one elapsed value, no re-reads).
    return BertResult(bdf, elapsed, verdict.bits, cor_total, unc_total, per,
                      dev.current_link_speed, dev.current_link_width, verdict,
                      stuck=idle_fault, uncorrectable_decode=unc_decode,
                      aer_source=source, note=note)


def default_c_runner(bdf: str, seconds: float, binary: str = "c/pcie_bert") -> dict:
    """Run the compiled C counter for one window and return its parsed JSON.

    Contract (the C engine's only job — count fast, accurately, for ``seconds``):
      {source, link_speed_code, link_width, link_unknown, bits,
       correctable, uncorrectable, uncorrectable_bits, per_correctable:{name:count}}
    """
    try:
        proc = subprocess.run([binary, "-d", bdf, "-t", f"{seconds:.4f}", "--json"],
                              capture_output=True, text=True, timeout=seconds + 30)
    except FileNotFoundError as e:
        raise RuntimeError(f"C engine not found: {binary} (build it with: make -C c)") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"C engine timed out after {seconds + 30}s") from e
    if proc.returncode not in (0, 3):   # 0 = clean, 3 = uncorrectable seen (valid data)
        raise RuntimeError(f"C engine failed (rc={proc.returncode}): {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"C engine produced invalid JSON: {proc.stdout!r}") from e


def run_conductor(backend: Backend, bdf: str, *, target_ber: float = 1e-12,
                  confidence: float = 0.95, max_seconds: float = 30.0,
                  margin: float = 1.2, extend_budget: float = 3.0, c_runner=None) -> BertResult:
    """The Python conductor for the real (fast) path. C is a dumb counter; Python:

      1. reads the error registers at IDLE (begin) — no traffic,
      2. runs the C counter for an increment while the link is exercised,
      3. applies the sequential decision (pass / reject / continue),
      4. EXTENDS (re-runs C) while undecided, bounded by a takt budget
         (``extend_budget`` x the zero-error target, and a hard ``max_seconds``),
      5. reads the error registers at IDLE (end) — a bit set at idle = constant fault.

    Errors and bits accumulate across the C increments. A high error rate is COUNTED
    (and rejected fast); a stray early error can be out-run within budget; a fault
    present at idle fails regardless. The C engine never decides how long to run.
    """
    runner = c_runner or default_c_runner
    dev = backend.get_device(bdf)
    bps = link_bits_per_second(dev.current_link_speed, dev.current_link_width)
    gen_note = ("Gen6+ FLIT/FEC: AER LCRC-retry counting under-measures BER; "
                "use FEC/symbol statistics") if dev.current_link_speed >= 6 else ""

    source = aer.error_source(backend, bdf)
    if source == "none":
        v = ber.BertVerdict(0, 0.0, target_ber, confidence, 0.0,
                            float("inf"), float("inf"), "skip")
        return BertResult(bdf, 0.0, 0.0, 0, 0, {}, dev.current_link_speed,
                          dev.current_link_width, v, aer_available=False,
                          aer_source="none",
                          note="no PCIe error source (neither AER nor Device Status)")

    # 1. Idle baseline (begin): quiesce, read what's set with no traffic.
    backend.set_exercising(bdf, False)
    aer.clear_errors(backend, bdf, source)
    idle0 = aer.read_errors(backend, bdf, source)
    idle_cor, idle_unc = idle0.correctable_raw, idle0.uncorrectable_raw
    idle_cor_names = {n for _, n, _ in aer.ErrorReading(idle_cor, 0, source).correctable}

    n0 = ber.bits_for_confidence(target_ber, confidence, 0)   # zero-error target bits
    budget_bits = n0 * extend_budget
    max_bits_by_time = bps * max_seconds if bps > 0 else float("inf")

    # 2-4. Exercise + sequential decision + bounded extend.
    backend.set_exercising(bdf, True)
    per: dict[str, int] = {}
    cor_total = unc_total = unc_bits_seen = 0
    n = elapsed = 0.0
    next_bits = n0 * margin                  # first window: target + margin
    status, out = "continue", {}
    while True:
        secs = (next_bits / bps) if bps > 0 else max_seconds
        secs = max(0.05, min(secs, max(0.05, max_seconds - elapsed)))
        out = runner(bdf, secs)
        for name, cnt in out.get("per_correctable", {}).items():
            if name in idle_cor_names:        # constant/idle fault — not a rate, don't count
                continue
            per[name] = per.get(name, 0) + cnt
            cor_total += cnt
        unc_total += out.get("uncorrectable", 0)
        unc_bits_seen |= out.get("uncorrectable_bits", 0)
        n += out.get("bits", 0.0)
        elapsed += secs

        if unc_total > 0:
            status = "fail"; break            # any uncorrectable = immediate fail
        decision = ber.sequential_decision(cor_total, n, target_ber, confidence)
        if decision in ("pass", "reject"):
            status = "pass" if decision == "pass" else "fail"
            break
        if n >= budget_bits or n >= max_bits_by_time or elapsed >= max_seconds:
            status = "fail"; break            # budget/takt exhausted, target not proven
        need = ber.bits_for_confidence(target_ber, confidence, cor_total)
        next_bits = max(n0 * 0.25, need - n)  # extend toward the bits the current E needs

    # 5. Idle baseline (end): errors persisting with no traffic = a constant fault.
    backend.set_exercising(bdf, False)
    aer.clear_errors(backend, bdf, source)
    idle1 = aer.read_errors(backend, bdf, source)
    idle_cor |= idle1.correctable_raw
    idle_unc |= idle1.uncorrectable_raw
    idle_fault = bool(idle_cor or idle_unc)
    if idle_fault and status != "fail":
        status = "fail"

    note = gen_note
    if idle_fault:
        names = ([nm for _, nm, _ in aer.ErrorReading(idle_cor, 0, source).correctable]
                 + [nm for _, nm, _ in aer.ErrorReading(0, idle_unc, source).uncorrectable])
        note = ((note + "; ") if note else "") + \
               "errors present at idle (constant fault): " + ",".join(names)

    verdict = ber.assess(cor_total, max(n, 1.0), target_ber, confidence, unc_total)
    verdict.status = status
    unc_decode = [nm for _, nm, _ in
                  aer.ErrorReading(0, unc_bits_seen | idle_unc, source).uncorrectable]
    return BertResult(bdf, elapsed, n, cor_total, unc_total, per,
                      out.get("link_speed_code", dev.current_link_speed),
                      out.get("link_width", dev.current_link_width), verdict,
                      stuck=idle_fault, uncorrectable_decode=unc_decode,
                      aer_source=source, note=note)


def run_many(backend: Backend, bdfs: list[str], **kw) -> list[BertResult]:
    """Run the BERT across several BDFs (sequentially; the harness parallelizes)."""
    return [run_bert(backend, bdf, **kw) for bdf in bdfs]
