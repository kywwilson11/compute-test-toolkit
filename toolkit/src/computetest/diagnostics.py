"""
Full PCIe diagnostic: combine link health, AER decode, the BERT, lane margining,
and retrain monitoring into one report per device, with the failure evidence
attached. This is the "beyond BERT" layer — a single call that tells you not just
*that* a link is bad but *how* (which layer, which lane, which error type).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import aer, bert, linkstate, margining
from .backend import Backend
from .topology import DeviceExpectation


@dataclass
class PcieDiagnostic:
    bdf: str
    link: linkstate.LinkHealth
    aer_snapshot: aer.AerSnapshot
    bert: bert.BertResult | None = None
    margin: margining.MarginResult | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.aer_snapshot.has_uncorrectable:
            return "fail"
        if not self.link.ok:
            return "fail"
        if self.bert and self.bert.status == "fail":
            return "fail"
        if self.margin and self.margin.lanes and not self.margin.ok:
            return "fail"
        return "pass"

    def reasons(self) -> list[str]:
        r = []
        if self.aer_snapshot.has_uncorrectable:
            r.append("uncorrectable: " + ",".join(n for _, n, _ in self.aer_snapshot.uncorrectable))
        if self.link.speed_degraded:
            r.append(f"speed degraded (Gen{self.link.speed})")
        if self.link.width_degraded:
            r.append(f"width degraded (x{self.link.width})")
        if self.link.retrains:
            r.append(f"{self.link.retrains} retrains during soak")
        if self.bert and self.bert.status == "fail" and not self.aer_snapshot.has_uncorrectable:
            r.append(f"BERT fail (BER<= {self.bert.verdict.ber_upper:.2e})")
        if self.margin and self.margin.lanes and not self.margin.ok:
            w = self.margin.worst_lane
            r.append(f"lane {w.lane} margin {w.timing_ui:.3f}UI < {self.margin.limit_ui}UI")
        return r

    def summary(self) -> str:
        head = f"[{self.status.upper()}] {self.link.summary()}"
        reasons = self.reasons()
        if reasons:
            head += "\n      reasons: " + "; ".join(reasons)
        if self.bert:
            head += f"\n      bert: {self.bert.verdict.summary()}"
        if self.margin and self.margin.lanes:
            head += f"\n      margin: {self.margin.summary()}"
        return head

    def to_dict(self) -> dict:
        return {
            "bdf": self.bdf, "status": self.status, "reasons": self.reasons(),
            "link": self.link.to_dict(),
            "aer": {"correctable": [n for _, n, _ in self.aer_snapshot.correctable],
                    "uncorrectable": [n for _, n, _ in self.aer_snapshot.uncorrectable]},
            "bert": self.bert.to_dict() if self.bert else None,
            "margin": self.margin.to_dict() if self.margin else None,
        }


def diagnose(backend: Backend, bdf: str, *, expected: DeviceExpectation | None = None,
             do_bert: bool = True, do_margin: bool = True, watch_retrains_s: float = 0.2,
             target_ber: float = 1e-12, confidence: float = 0.95,
             bert_max_s: float = 30.0) -> PcieDiagnostic:
    """Run a complete PCIe diagnostic on one device."""
    exp_speed = expected.expected_speed if expected else None
    exp_width = expected.expected_width if expected else None
    limit_ui = expected.min_margin_ui if expected else margining.DEFAULT_MIN_TIMING_UI

    link = linkstate.check_link(backend, bdf, expected_speed=exp_speed,
                                expected_width=exp_width, watch_s=watch_retrains_s)
    snap = aer.snapshot(backend, bdf)
    bert_res = (bert.run_bert(backend, bdf, target_ber=target_ber, confidence=confidence,
                              max_seconds=bert_max_s) if do_bert else None)
    margin_res = (margining.margin_link(backend, bdf, limit_ui=limit_ui)
                  if do_margin else None)
    return PcieDiagnostic(bdf, link, snap, bert_res, margin_res)


def diagnose_all(backend: Backend, bdfs: list[str] | None = None, **kw) -> list[PcieDiagnostic]:
    """Diagnose every device (or a given list)."""
    targets = bdfs if bdfs is not None else backend.list_devices()
    return [diagnose(backend, bdf, **kw) for bdf in targets]
