"""
Command-line front end. On a laptop everything runs against the mock backend; on a
Linux test station it uses real hardware (force either way with
COMPUTETEST_BACKEND=mock|real). Read-only by default; the BERT's arm/clear writes to
AER status only and is the only write path.

    computetest list
    computetest bert -d 0000:03:00.0 --target-ber 1e-12
    computetest diagnose            # full PCIe diagnostic on every device
    computetest nvme /dev/nvme0
    computetest gpu 0
    computetest plan configs/example_plan.json
    computetest ber --confidence 0.95 --target-ber 1e-12   # just the math
"""
from __future__ import annotations

import argparse
import json
import sys

from . import ber, diagnostics, ethernet, gmsl, gpu, nvme
from .backend import select_backend
from .bert import run_bert
from .harness import run_test_plan
from .results import ResultStore


def _print(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="computetest", description=__doc__)
    p.add_argument("--json", action="store_true", help="emit JSON")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="enumerate PCIe devices")

    sp = sub.add_parser("bert", help="run the BERT on one device")
    sp.add_argument("-d", "--bdf", required=True)
    sp.add_argument("--target-ber", type=float, default=1e-12)
    sp.add_argument("--confidence", type=float, default=0.95)
    sp.add_argument("--max-seconds", type=float, default=30.0)
    sp.add_argument("--engine", choices=["python", "c"], default="python")

    sp = sub.add_parser("diagnose", help="full PCIe diagnostic")
    sp.add_argument("-d", "--bdf", default=None, help="one BDF (default: all)")
    sp.add_argument("--target-ber", type=float, default=1e-12)
    sp.add_argument("--no-bert", action="store_true")
    sp.add_argument("--no-margin", action="store_true")
    sp.add_argument("--max-seconds", type=float, default=30.0)

    sp = sub.add_parser("nvme", help="NVMe SMART health"); sp.add_argument("device")
    sp = sub.add_parser("gpu", help="GPU health"); sp.add_argument("index", type=int)
    sp = sub.add_parser("gmsl", help="GMSL link+video"); sp.add_argument("link")
    sp = sub.add_parser("eth", help="Ethernet link"); sp.add_argument("iface")
    sp = sub.add_parser("can", help="CAN state"); sp.add_argument("iface")

    sp = sub.add_parser("plan", help="run a full test plan from a config")
    sp.add_argument("config")
    sp.add_argument("--db", default=":memory:", help="SQLite results path")
    sp.add_argument("--serial", default="DUT-DEMO")

    sp = sub.add_parser("ber", help="BER confidence math only (no hardware)")
    sp.add_argument("--target-ber", type=float, default=1e-12)
    sp.add_argument("--confidence", type=float, default=0.95)
    sp.add_argument("--errors", type=int, default=0)
    sp.add_argument("--bits", type=float, default=None)

    args = p.parse_args(argv)

    if args.cmd == "ber":  # pure math, needs no backend
        if args.bits is None:
            n = ber.bits_for_confidence(args.target_ber, args.confidence, args.errors)
            print(f"To prove BER <= {args.target_ber:.1e} at {args.confidence:.0%} "
                  f"with {args.errors} errors: {n:.4e} bits "
                  f"(~{n / (31.5e9 * 8):.1f}s at Gen4 x16)")
        else:
            v = ber.assess(args.errors, args.bits, args.target_ber, args.confidence)
            print(v.summary())
        return 0

    backend = select_backend()
    where = "MOCK (no hardware)" if backend.is_mock() else "REAL hardware"
    print(f"# backend: {where}\n", file=sys.stderr)

    if args.cmd == "list":
        for bdf in backend.list_devices():
            d = backend.get_device(bdf)
            print(f"{bdf}  {d.vendor_name:18} Gen{d.current_link_speed}x{d.current_link_width}"
                  f"  class={d.class_code:#08x} drv={d.driver}")
        return 0

    if args.cmd == "bert":
        r = run_bert(backend, args.bdf, target_ber=args.target_ber,
                     confidence=args.confidence, max_seconds=args.max_seconds,
                     engine=args.engine)
        print(r.summary()); _print(r.to_dict(), args.json)
        return 0 if r.status == "pass" else 1

    if args.cmd == "diagnose":
        bdfs = [args.bdf] if args.bdf else None
        results = diagnostics.diagnose_all(
            backend, bdfs, do_bert=not args.no_bert, do_margin=not args.no_margin,
            target_ber=args.target_ber, bert_max_s=args.max_seconds)
        worst = 0
        for d in results:
            print(d.summary())
            worst = max(worst, 0 if d.status == "pass" else 1)
        _print([d.to_dict() for d in results], args.json)
        return worst

    if args.cmd in ("nvme", "gpu", "gmsl", "eth", "can"):
        h = {"nvme": lambda: nvme.check_nvme(args.device),
             "gpu": lambda: gpu.check_gpu(args.index),
             "gmsl": lambda: gmsl.check_gmsl(args.link),
             "eth": lambda: ethernet.check_ethernet(args.iface),
             "can": lambda: ethernet.check_can(args.iface)}[args.cmd]()
        print(h.summary()); _print(h.to_dict(), args.json)
        return 0 if h.ok else 1

    if args.cmd == "plan":
        with open(args.config) as fh:
            plan = json.load(fh) if args.config.endswith(".json") else None
        if plan is None:
            from .topology import load_config  # YAML path
            cfg = load_config(args.config)
            plan = {"target_ber": cfg.target_ber, "confidence": cfg.confidence,
                    "devices": [vars(d) for d in cfg.devices]}
        store = ResultStore(args.db, dut_serial=args.serial)
        report = run_test_plan(backend, plan, store=store)
        print(report.summary())
        _print({"report": [vars(r) for r in report.records],
                "summary": store.summary()}, args.json)
        return 0 if report.ok else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
