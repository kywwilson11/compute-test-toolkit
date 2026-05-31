"""
Full PCIe diagnostic: combine link health, AER decode, the BERT, lane margining,
and retrain monitoring into one report per device, with the failure evidence
attached. This is the "beyond BERT" layer — a single call that tells you not just
*that* a link is bad but *how* (which layer, which lane, which error type).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import aer, bert, dmesg, linkstate, margining, topology
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
        # A real fault always wins over an incomplete measurement.
        if self.aer_snapshot.has_uncorrectable:
            return "fail"
        if not self.link.ok:
            return "fail"
        if self.bert and self.bert.status == "fail":
            return "fail"
        if self.margin and self.margin.lanes and not self.margin.ok:
            return "fail"
        # No fail signal — but a device never error-tested has NOT passed. We never
        # claim PASS from a measurement we couldn't (or didn't) make:
        #   * BERT ran but skipped (no AER / Device Status source) -> skip
        #   * BERT was explicitly opted out (`--no-bert`, bert is None) -> skip
        if self.bert is None or self.bert.status == "skip":
            return "skip"
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
        if self.bert and self.bert.status == "skip":
            r.append(f"BERT skipped: {self.bert.note or 'no PCIe error source'}")
        elif self.bert is None:
            r.append("BERT skipped: --no-bert (no error-rate measurement performed)")
        if self.margin and self.margin.lanes and not self.margin.ok:
            w = self.margin.worst_lane
            assert w is not None    # `self.margin.lanes` is truthy -> worst_lane returns one
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
        elif self.margin and self.margin.note:
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
    margin_res = _try_margin(backend, bdf, limit_ui) if do_margin else None
    return PcieDiagnostic(bdf, link, snap, bert_res, margin_res)


def _try_margin(backend: Backend, bdf: str, limit_ui: float) -> margining.MarginResult:
    """Margin the link, but never let a margining failure abort the diagnostic.

    The real Gen4+ margining path is guarded (raises NotImplementedError until
    validated per-hardware) and a live margining sequence can fault. Either way the
    link/AER/BERT evidence we already gathered must survive: record margining as
    unavailable (no lanes, with the reason) and let the rest of the report stand.
    """
    try:
        return margining.margin_link(backend, bdf, limit_ui=limit_ui)
    except NotImplementedError as e:
        return margining.MarginResult(bdf, [], limit_ui=limit_ui,
                                      note=f"unavailable: {e}")
    except Exception as e:  # pragma: no cover - defensive: any live-margining fault
        return margining.MarginResult(bdf, [], limit_ui=limit_ui,
                                      note=f"margining error: {type(e).__name__}: {e}")


def diagnose_all(backend: Backend, bdfs: list[str] | None = None, **kw) -> list[PcieDiagnostic]:
    """Diagnose every device (or a given list)."""
    targets = bdfs if bdfs is not None else backend.list_devices()
    return [diagnose(backend, bdf, **kw) for bdf in targets]


# --- Whole-chain diagnostic: every link in an endpoint's path ------------------ #
@dataclass
class ChainSegmentResult:
    """A monitored BDF: its AER reports ONE direction of ONE link (per the receiver)."""
    bdf: str
    direction: str                      # e.g. "0000:00:1c.0->0000:02:00.0"
    correctable_types: list[str]        # correctable bit names seen this direction
    uncorrectable_types: list[str]      # uncorrectable bit names seen this direction
    status: str                         # "pass" | "fail" | "skip" (no PCIe error source)


@dataclass
class ChainLinkResult:
    """A per-link downgrade result (speed/width is a link property, read once)."""
    name: str
    downstream_bdf: str
    speed: int
    width: int
    expected_speed: int | None
    expected_width: int | None
    bw_changed: bool
    status: str                         # "pass" | "fail"


@dataclass
class ChainDiagnostic:
    endpoint: str
    bert: bert.BertResult               # confidence BERT on the endpoint link
    segments: list[ChainSegmentResult]  # upstream BDFs, monitored by direction
    links: list[ChainLinkResult]        # per-link downgrade results
    kernel_events: list = field(default_factory=list)  # dmesg-decoded PCIe/AER events

    @property
    def bad_kernel_events(self) -> list:
        # Kernel-logged uncorrectables / link-downs are fail signals; corrected are info.
        return [e for e in self.kernel_events if e.uncorrectable or e.severity == "link"]

    @property
    def status(self) -> str:
        if (self.bert.status == "fail" or any(s.status == "fail" for s in self.segments)
                or any(li.status == "fail" for li in self.links) or self.bad_kernel_events):
            return "fail"
        # An unmeasurable segment (no PCIe error source) is never a silent pass: fold it
        # through to skip, mirroring the single-device honesty rule (fail > skip > pass).
        if any(s.status == "skip" for s in self.segments):
            return "skip"
        return self.bert.status         # "pass" (or "skip" if the endpoint had no AER)

    def reasons(self) -> list[str]:
        r = []
        if self.bert.status == "fail":
            r.append(f"endpoint {self.endpoint}: {self.bert.verdict.summary()}")
        for s in self.segments:
            if s.status == "fail":
                r.append(f"errors {s.direction}: "
                         + ",".join(s.uncorrectable_types + s.correctable_types))
        for li in self.links:
            if li.status == "fail":
                r.append(f"downgrade {li.name}: Gen{li.speed} x{li.width}"
                         + (" (LBMS/LABS latched)" if li.bw_changed else ""))
        for e in self.bad_kernel_events:
            r.append(f"kernel: {e.text}")
        return r

    def summary(self) -> str:
        lines = [f"[{self.status.upper()}] chain to {self.endpoint} "
                 f"({len(self.links)} link(s), {len(self.segments) + 1} BDF(s))",
                 f"      endpoint  {self.bert.summary()}"]
        for s in self.segments:
            detail = "OK" if s.status == "pass" else \
                "FAIL " + ",".join(s.uncorrectable_types + s.correctable_types)
            lines.append(f"      seg  {s.direction}: {detail}")
        for li in self.links:
            state = "OK" if li.status == "pass" else "DOWNGRADE"
            lines.append(f"      link {li.name}: Gen{li.speed} x{li.width} -> {state}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"endpoint": self.endpoint, "status": self.status,
                "reasons": self.reasons(), "bert": self.bert.to_dict(),
                "segments": [vars(s) for s in self.segments],
                "links": [vars(li) for li in self.links],
                "kernel_events": [vars(e) for e in self.kernel_events]}


def diagnose_chain(backend: Backend, endpoint_bdf: str, *, expected_speed: int | None = None,
                   expected_width: int | None = None, target_ber: float = 1e-12,
                   confidence: float = 0.95, max_seconds: float = 30.0,
                   upstream_correctable_ok: int = 0, dmesg_reader=None,
                   **bert_kw) -> ChainDiagnostic:
    """Test EVERY link in an endpoint's path. One stress window (the endpoint BERT;
    its traffic traverses every link): a confidence BERT on the endpoint link, AER
    monitored per-direction on every other BDF, a per-link downgrade check read at each
    link's downstream port, and the kernel log (dmesg) watched for PCIe/AER events on
    any chain BDF. Any error/downgrade/uncorrectable on any segment fails the chain,
    pinned to the exact BDF+direction or link."""
    members, links = topology.analyze_chain(backend, endpoint_bdf)
    upstream = [m for m in members if m.bdf != endpoint_bdf]

    # Watch the kernel log across the whole window (no-op off Linux without a reader).
    monitor = dmesg.DmesgMonitor(reader=dmesg_reader)
    monitor.start()

    # Arm the monitored upstream BDFs and the links' bandwidth-change latches.
    for m in upstream:
        backend.set_exercising(m.bdf, True)
        aer.clear_errors(backend, m.bdf, aer.error_source(backend, m.bdf))
    for li in links:
        backend.clear_link_bw_status(li.downstream_bdf)

    # The one stress window: traffic to the endpoint traverses the whole chain.
    bert_res = bert.run_bert(backend, endpoint_bdf, target_ber=target_ber,
                             confidence=confidence, max_seconds=max_seconds, **bert_kw)
    kernel_events = monitor.collect([m.bdf for m in members])

    # Read each upstream BDF's accrued errors (one direction of one link).
    segments = []
    for m in upstream:
        src = aer.error_source(backend, m.bdf)
        r = aer.read_errors(backend, m.bdf, src)
        backend.set_exercising(m.bdf, False)
        cor = [n for _, n, _ in r.correctable]
        unc = [n for _, n, _ in r.uncorrectable]
        if src == "none":           # no AER and no Device Status: BER was never measurable
            status = "skip"
        elif not unc and len(cor) <= upstream_correctable_ok:
            status = "pass"
        else:
            status = "fail"
        segments.append(ChainSegmentResult(m.bdf, m.direction, cor, unc, status))

    # Per-link downgrade, read once at the downstream port (both ends agree on speed/width).
    link_results = []
    for li in links:
        ls = backend.read_link_status(li.downstream_bdf)
        # No explicit expectation -> hold the link to the downstream port's OWN max, so a
        # link that came up degraded BEFORE the window (LBMS/LABS never latched) is caught.
        port = backend.get_device(li.downstream_bdf)
        tgt_speed = expected_speed or port.max_link_speed
        tgt_width = expected_width or port.max_link_width
        bw = ls.bw_changed or ls.autonomous_bw
        downgraded = bool((tgt_speed and ls.speed < tgt_speed)
                          or (tgt_width and ls.width < tgt_width) or bw)
        link_results.append(ChainLinkResult(li.name, li.downstream_bdf, ls.speed, ls.width,
                                             expected_speed, expected_width, bw,
                                             "fail" if downgraded else "pass"))
    return ChainDiagnostic(endpoint_bdf, bert_res, segments, link_results, kernel_events)
