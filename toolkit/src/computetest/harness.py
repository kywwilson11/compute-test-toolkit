"""
Test-plan harness: run coverage across every subsystem from one config, record the
results (with measured values), and return a pass/fail report. This is the data-
driven runner described in Guide A — a new board revision is a new config, not new
code. The pytest suite (tests/) drives this same machinery, parametrized per device.

A plan has TWO test LAYERS (this is not duplication — see USAGE.md "## Config
reference"). Layer 1 (`pcie_devices`, `chains`) tests the LINK to each device;
Layer 2 (`functional_checks`) tests the DEVICE itself by OS handle. A GPU
legitimately appears in both: its link in Layer 1, its ECC/thermal in Layer 2.

Plan format (dict, or JSON/YAML file):

    target_ber: 1.0e-12
    confidence: 0.95
    bert_max_s: 30
    pcie_devices:            # Layer 1: PCIe LINK expectations (see topology.py)
      - {name: GPU0, match: {class_code: 0x030000}, expected_speed: 4, expected_width: 16}
    chains:                  # Layer 1: whole-path (root..endpoint) link diagnostics
      - {endpoint: "0000:04:00.0", expected_speed: 4, expected_width: 16}
    functional_checks:       # Layer 2: DEVICE health, keyed by OS handle
      nvme:     ["/dev/nvme0", "/dev/nvme1"]
      gpus:     [0, 1]
      gmsl:     ["1-0029"]
      ethernet: ["eth0"]
      can:      ["can0"]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import diagnostics, ethernet, gmsl, gpu, nvme, topology
from .backend import Backend
from .results import ResultStore, TestRecord


@dataclass
class TestReport:
    records: list[TestRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.status != "fail" for r in self.records)

    @property
    def counts(self) -> dict[str, int]:
        c = {"pass": 0, "fail": 0, "skip": 0}
        for r in self.records:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    def summary(self) -> str:
        lines = [f"  [{r.status.upper():4}] {r.subsystem}/{r.target}: {r.test_name}"
                 + (f"  {r.message}" if r.message else "") for r in self.records]
        c = self.counts
        verdict = "PASS" if self.ok else "FAIL"
        return ("Test plan report:\n" + "\n".join(lines)
                + f"\n  => {c['pass']} pass, {c['fail']} fail, {c['skip']} skip  [{verdict}]")


def _heartbeat_detail(report: TestReport) -> str:
    """Terminal heartbeat verdict, using the SAME three-way logic as
    cli._verdict_exit_any: any genuine fail -> FAIL; else any skip -> INCOMPLETE
    (uncovered tests are not a pass); else PASS. report.ok alone would mislabel an
    all-skip run 'PASS' while the CLI exit code is EXIT_UNAVAIL."""
    statuses = [r.status for r in report.records]
    if any(s == "fail" for s in statuses):
        return "FAIL"
    if any(s in ("skip", "unavailable", "incomplete") for s in statuses):
        return "INCOMPLETE"
    return "PASS"


def run_test_plan(backend: Backend, plan: dict, *, store: ResultStore | None = None,
                  do_bert: bool = True, do_margin: bool = True) -> TestReport:
    """Run the full plan, recording each result. Returns a TestReport."""
    report = TestReport()
    cfg = topology.load_config(plan)
    if store:
        store.heartbeat("running", "executing test plan")

    def add(rec: TestRecord):
        report.records.append(rec)
        if store:
            store.record(rec)

    def safe_add(subsystem: str, target: str, test_name: str, make: Any) -> None:
        """Run ``make()`` (which returns a TestRecord) but never let one raising check
        abort the plan. Mirrors diagnostics._try_margin: on any fault, record a SKIP so
        the rest of the report -- and every remaining device/handle -- still runs."""
        try:
            add(make())
        except Exception as e:  # pragma: no cover - real-hw path (mock checks never raise)
            add(TestRecord(subsystem, target, test_name, "skip", {},
                           f"{type(e).__name__}: {e}"))

    def add_health(subsystem: str, target: str, test_name: str, make: Any) -> None:
        """Record a Layer-2 functional-check result. ``make`` is a zero-arg callable
        returning a duck-typed health object (.ok / .summary() / .to_dict()); it is
        invoked inside safe_add so a raising real-hw check degrades to SKIP."""
        def _rec() -> TestRecord:
            h = make()
            return TestRecord(subsystem, target, test_name,
                              "pass" if h.ok else "fail", h.to_dict(), h.summary())
        safe_add(subsystem, target, test_name, _rec)

    try:
        # 1. Enumeration: are the expected PCIe devices present?
        enum = topology.enumerate_against(backend, cfg)
        add(TestRecord("enum", "topology", "enumeration",
                       "pass" if enum.ok else "fail",
                       {"matched": enum.matched, "missing": enum.missing,
                        "unexpected": enum.unexpected},
                       "" if enum.ok else "missing: " + ", ".join(enum.missing)))

        # 2. PCIe diagnostics per matched device.
        for exp in cfg.devices:
            for bdf in enum.matched.get(exp.name, []):
                def _diag(bdf: str = bdf, exp: topology.DeviceExpectation = exp) -> TestRecord:
                    d = diagnostics.diagnose(
                        backend, bdf, expected=exp, do_bert=do_bert, do_margin=do_margin,
                        target_ber=cfg.target_ber, confidence=cfg.confidence,
                        bert_max_s=plan.get("bert_max_s", 30.0),
                        watch_retrains_s=plan.get("watch_retrains_s", 0.2))
                    return TestRecord("pcie", bdf, f"{exp.name}:diagnose", d.status,
                                      d.to_dict(), "; ".join(d.reasons()))
                safe_add("pcie", bdf, f"{exp.name}:diagnose", _diag)

        # 3. Layer 2: functional / DEVICE health checks (keyed by OS handle). These test
        #    each device itself (a GPU's link is Layer 1 above; its ECC/thermal is here).
        fc = plan.get("functional_checks", {})
        for dev in fc.get("nvme", []):
            add_health("nvme", dev, "smart", lambda dev=dev: nvme.check_nvme(dev))
        for idx in fc.get("gpus", []):
            add_health("gpu", str(idx), "health", lambda idx=idx: gpu.check_gpu(idx))
        for link in fc.get("gmsl", []):
            add_health("gmsl", link, "link+video", lambda link=link: gmsl.check_gmsl(link))
        for iface in fc.get("ethernet", []):
            add_health("ethernet", iface, "link",
                       lambda iface=iface: ethernet.check_ethernet(iface))
        for iface in fc.get("can", []):
            add_health("can", iface, "state", lambda iface=iface: ethernet.check_can(iface))

        # 4. Whole-chain diagnostics (every link in an endpoint's path). Each entry is an
        #    endpoint BDF, or {endpoint, expected_speed, expected_width}.
        for spec in plan.get("chains", []):
            ep = spec if isinstance(spec, str) else spec["endpoint"]
            kw = {} if isinstance(spec, str) else {
                "expected_speed": spec.get("expected_speed"),
                "expected_width": spec.get("expected_width")}
            def _chain(ep: str = ep, kw: dict = kw) -> TestRecord:
                # Distinct name (vs per-device `d` above) keeps the types crisp.
                chain_d = diagnostics.diagnose_chain(
                    backend, ep, target_ber=cfg.target_ber, confidence=cfg.confidence,
                    max_seconds=plan.get("bert_max_s", 30.0), **kw)
                return TestRecord("chain", ep, "chain", chain_d.status,
                                  chain_d.to_dict(), "; ".join(chain_d.reasons()))
            safe_add("chain", ep, "chain", _chain)
    finally:
        if store:
            # Terminal heartbeat ALWAYS supersedes the early "running" beat (even if a
            # bug below the safe_add net escaped) so no station is left stuck "running".
            store.heartbeat("idle", _heartbeat_detail(report))
    return report
