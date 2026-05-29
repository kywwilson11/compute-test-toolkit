"""
computetest — command-line front end.

On a laptop everything runs against the mock backend; on a Linux test station it uses
real hardware. Read-only by default; the BERT's AER arm/clear (write-1-to-clear of
status bits) is the only write path.

Backend selection: auto (real iff /sys/bus/pci exists, else mock), or force with
--backend mock|real or COMPUTETEST_BACKEND=mock|real.

Exit codes:  0 pass  ·  1 fail (DUT failed)  ·  2 usage error  ·
             3 device/target not found  ·  4 config/IO error  ·
             5 capability unavailable on this hardware
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from typing import Any

from . import ber, diagnostics, ethernet, gmsl, gpu, nvme
from .backend import select_backend
from .bert import run_bert
from .harness import run_test_plan
from .io import ocpdiag
from .results import ResultStore

EXIT_PASS, EXIT_FAIL, EXIT_USAGE, EXIT_NOTFOUND, EXIT_IO, EXIT_UNAVAIL = 0, 1, 2, 3, 4, 5

_EXAMPLES = """\
examples:
  computetest list
  computetest diagnose                       # full PCIe diagnostic, every device
  computetest chain 0000:04:00.0             # every link in the endpoint's path
  computetest bert -d 0000:03:00.0 --target-ber 1e-12
  computetest nvme /dev/nvme0 --json | jq .  # --json is stdout-pure, pipeable
  computetest plan configs/example_plan.json --db results.db --serial SN123
  computetest ber --target-ber 1e-12 --confidence 0.95   # just the math, no hardware
"""


def _emit(human: str, obj, as_json: bool, *, sink=None) -> None:
    """Human text to ``sink`` (default stdout), OR (with --json) only the JSON object.

    ``sink`` is parameterized so ``--ocpdiag -`` can route the ocp-diag JSONL
    stream to stdout while the human output goes to stderr — keeping stdout pure
    for downstream OCP consumers.
    """
    print(json.dumps(obj, indent=2, default=str) if as_json else human,
          file=sink or sys.stdout)


def _emit_ocp(args, kind: str, result, **kw) -> None:
    """Dispatch a command's result(s) to the right ocp-diag-core adapter.

    No-op when ``--ocpdiag`` was not passed (``args._ocp`` is None). The status
    of the run is decided in ``main`` from the final exit code, not here.
    """
    em = getattr(args, "_ocp", None)
    if em is None:
        return
    if kind == "bert":
        ocpdiag.emit_bert(em, result, target_ber=kw.get("target_ber"))
    elif kind == "diagnose":
        for d in result:
            ocpdiag.emit_pcie_diagnostic(em, d)
    elif kind == "chain":
        ocpdiag.emit_chain(em, result)
    elif kind == "health":
        ocpdiag.emit_health(em, result, label=kw["label"])
    elif kind == "plan":
        for r in result:
            ocpdiag.emit_test_record(em, r)
    elif kind == "list":
        sid = em.step_start("pcie.list")
        for d in result:
            em.measurement(name=f"device.{d.bdf}.speed_gen",
                           value=d.current_link_speed, unit="gen")
            em.measurement(name=f"device.{d.bdf}.width",
                           value=d.current_link_width, unit="lane")
            em.measurement(name=f"device.{d.bdf}.vendor",
                           value=d.vendor_name)
        em.diagnosis(verdict="pcie.list.pass", type_="PASS")
        em.step_end("pass", step_id=sid)
    elif kind == "ber":
        sid = em.step_start("ber.math")
        for k, v in result.items():
            if isinstance(v, (bool, int, float, str)):
                em.measurement(name=k, value=v)
        em.diagnosis(verdict="ber.math.pass", type_="PASS")
        em.step_end("pass", step_id=sid)


def _verdict_exit(status: str) -> int:
    """Map a measurement verdict to an exit code, distinguishing "couldn't measure"
    from "DUT failed":  pass -> 0,  fail -> 1,  skip/unavailable -> 5."""
    if status == "pass":
        return EXIT_PASS
    if status in ("skip", "unavailable", "incomplete"):
        return EXIT_UNAVAIL
    return EXIT_FAIL


def _verdict_exit_any(statuses) -> int:
    """Aggregate exit for several verdicts: any genuine fail -> 1 (a real fault wins);
    else any skip -> 5 (incomplete coverage); else 0."""
    seen = list(statuses)
    if any(s == "fail" for s in seen):
        return EXIT_FAIL
    if any(s in ("skip", "unavailable", "incomplete") for s in seen):
        return EXIT_UNAVAIL
    return EXIT_PASS


def _build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="emit JSON to stdout (pipeable)")
    common.add_argument("--backend", choices=["auto", "mock", "real"], default="auto",
                        help="force the hardware backend (default: auto)")
    common.add_argument("--ocpdiag", metavar="PATH", default=None,
                        help="also emit OCP ocp-diag-core JSONL to PATH ('-' for stdout, "
                             "in which case human output goes to stderr)")
    common.add_argument("--ocpdiag-serial", metavar="DUT", default="UNKNOWN",
                        help="DUT serial for the ocp-diag testRunStart.dutInfo")
    common.add_argument("--ocpdiag-station", metavar="STATION", default="station-1",
                        help="test station name for the ocp-diag testRunStart.dutInfo")

    p = argparse.ArgumentParser(prog="computetest", parents=[common],
                                description=__doc__, epilog=_EXAMPLES,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", parents=[common], help="enumerate PCIe devices")

    sp = sub.add_parser("bert", parents=[common], help="run the BERT on one device")
    sp.add_argument("-d", "--bdf", required=True)
    sp.add_argument("--target-ber", type=float, default=1e-12)
    sp.add_argument("--confidence", type=float, default=0.95)
    sp.add_argument("--max-seconds", type=float, default=30.0)
    sp.add_argument("--engine", choices=["python", "c"], default="python")

    sp = sub.add_parser("diagnose", parents=[common], help="full PCIe diagnostic")
    sp.add_argument("-d", "--bdf", default=None, help="one BDF (default: all)")
    sp.add_argument("--target-ber", type=float, default=1e-12)
    sp.add_argument("--no-bert", action="store_true")
    sp.add_argument("--no-margin", action="store_true")
    sp.add_argument("--max-seconds", type=float, default=30.0)

    sp = sub.add_parser("chain", parents=[common],
                        help="diagnose every link in an endpoint's path (root..endpoint)")
    sp.add_argument("endpoint", help="endpoint BDF; expands to its full PCIe path")
    sp.add_argument("--target-ber", type=float, default=1e-12)
    sp.add_argument("--confidence", type=float, default=0.95)
    sp.add_argument("--max-seconds", type=float, default=30.0)
    sp.add_argument("--expected-speed", type=int, default=None, help="expected Gen (1-6)")
    sp.add_argument("--expected-width", type=int, default=None, help="expected lane width")

    for name, help_ in [("nvme", "NVMe SMART health"), ("gpu", "GPU health"),
                        ("gmsl", "GMSL link+video"), ("eth", "Ethernet link"),
                        ("can", "CAN state")]:
        sp = sub.add_parser(name, parents=[common], help=help_)
        sp.add_argument("target", help="device / index / interface")

    sp = sub.add_parser("plan", parents=[common], help="run a full test plan from a config")
    sp.add_argument("config")
    sp.add_argument("--db", default=":memory:", help="SQLite results path")
    sp.add_argument("--serial", default="DUT-DEMO")
    sp.add_argument("--station", default="station-1")

    sp = sub.add_parser("ber", parents=[common], help="BER confidence math only (no hardware)")
    sp.add_argument("--target-ber", type=float, default=1e-12)
    sp.add_argument("--confidence", type=float, default=0.95)
    sp.add_argument("--errors", type=int, default=0)
    sp.add_argument("--bits", type=float, default=None)
    return p


def _run(args) -> int:
    sink = getattr(args, "_human", sys.stdout)

    # 'ber' is pure math — no backend needed.
    if args.cmd == "ber":
        if args.bits is None:
            n = ber.bits_for_confidence(args.target_ber, args.confidence, args.errors)
            human = (f"To prove BER <= {args.target_ber:.1e} at {args.confidence:.0%} "
                     f"with {args.errors} errors: {n:.4e} bits "
                     f"{ber.time_estimate(n)}")
            obj = {"target_ber": args.target_ber, "confidence": args.confidence,
                   "errors": args.errors, "bits_needed": n}
        else:
            v = ber.assess(args.errors, args.bits, args.target_ber, args.confidence)
            human, obj = v.summary(), vars(v)
        _emit(human, obj, args.json, sink=sink)
        _emit_ocp(args, "ber", obj)
        return EXIT_PASS

    if args.backend != "auto":
        os.environ["COMPUTETEST_BACKEND"] = args.backend
    backend = select_backend()
    print(f"# backend: {'MOCK (no hardware)' if backend.is_mock() else 'REAL hardware'}",
          file=sys.stderr)

    if args.cmd == "list":
        devs = [backend.get_device(b) for b in backend.list_devices()]
        human = "\n".join(
            f"{d.bdf}  {d.vendor_name:18} Gen{d.current_link_speed}x{d.current_link_width}"
            f"  class={d.class_code:#08x} drv={d.driver}" for d in devs)
        _emit(human, [d.to_dict() for d in devs], args.json, sink=sink)
        _emit_ocp(args, "list", devs)
        return EXIT_PASS

    if args.cmd == "bert":
        r = run_bert(backend, args.bdf, target_ber=args.target_ber,
                     confidence=args.confidence, max_seconds=args.max_seconds,
                     engine=args.engine)
        _emit(r.summary(), r.to_dict(), args.json, sink=sink)
        _emit_ocp(args, "bert", r, target_ber=args.target_ber)
        return _verdict_exit(r.status)

    if args.cmd == "diagnose":
        bdfs = [args.bdf] if args.bdf else None
        results = diagnostics.diagnose_all(
            backend, bdfs, do_bert=not args.no_bert, do_margin=not args.no_margin,
            target_ber=args.target_ber, bert_max_s=args.max_seconds)
        human = "\n".join(d.summary() for d in results)
        _emit(human, [d.to_dict() for d in results], args.json, sink=sink)
        _emit_ocp(args, "diagnose", results)
        return _verdict_exit_any(d.status for d in results)

    if args.cmd == "chain":
        d = diagnostics.diagnose_chain(
            backend, args.endpoint, target_ber=args.target_ber, confidence=args.confidence,
            max_seconds=args.max_seconds, expected_speed=args.expected_speed,
            expected_width=args.expected_width)
        _emit(d.summary(), d.to_dict(), args.json, sink=sink)
        _emit_ocp(args, "chain", d)
        return _verdict_exit(d.status)

    if args.cmd in ("nvme", "gpu", "gmsl", "eth", "can"):
        # The dispatch returns one of five distinct Health dataclasses; they share the
        # .ok / .summary() / .to_dict() shape but not a common nominal type, so Any
        # is the honest annotation here (a Protocol would be more boilerplate than payoff).
        h: Any = {"nvme": lambda: nvme.check_nvme(args.target),
                  "gpu": lambda: gpu.check_gpu(int(args.target)),
                  "gmsl": lambda: gmsl.check_gmsl(args.target),
                  "eth": lambda: ethernet.check_ethernet(args.target),
                  "can": lambda: ethernet.check_can(args.target)}[args.cmd]()
        _emit(h.summary(), h.to_dict(), args.json, sink=sink)
        _emit_ocp(args, "health", h, label=args.cmd)
        return EXIT_PASS if h.ok else EXIT_FAIL

    if args.cmd == "plan":
        plan = _load_plan(args.config)
        with ResultStore(args.db, dut_serial=args.serial, station=args.station) as store:
            report = run_test_plan(backend, plan, store=store)
            obj = {"report": [vars(r) for r in report.records], "summary": store.summary()}
        _emit(report.summary(), obj, args.json, sink=sink)
        _emit_ocp(args, "plan", report.records)
        # Consistent with single-command verdicts: a real fail -> 1, an unmeasured/skip
        # record -> 5 (incomplete coverage), else 0. (plan previously returned 0 on skip.)
        return _verdict_exit_any(r.status for r in report.records)

    return EXIT_USAGE  # pragma: no cover - unreachable (argparse requires a subcommand)


def _load_plan(path: str) -> dict:
    """Load a plan as a raw dict, preserving every section (both test layers:
    pcie_devices + chains, and functional_checks) and the top-level knobs."""
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml  # optional dependency
        except ImportError as e:
            raise RuntimeError("PyYAML not installed; use a .json config or "
                               "`pip install pyyaml`") from e
        with open(path) as fh:
            return yaml.safe_load(fh)
    with open(path) as fh:
        return json.load(fh)


_EXIT_TO_OCP_STATUS = {EXIT_PASS: "pass", EXIT_FAIL: "fail", EXIT_UNAVAIL: "skip"}


def _open_ocpdiag(args, argv: list[str] | None):
    """If ``--ocpdiag`` is set, attach an Emitter and a closeable stream to ``args``.

    Sets:
      * ``args._human``      — sink for human/--json output (stderr when --ocpdiag -)
      * ``args._ocp``        — the ocp-diag Emitter (None if --ocpdiag was not passed)
      * ``args._ocp_stream`` — the underlying stream (sys.stdout or an open file)
    """
    args._human = sys.stdout
    args._ocp = None
    args._ocp_stream = None
    if not args.ocpdiag:
        return
    if args.ocpdiag == "-":
        args._human = sys.stderr
        args._ocp_stream = sys.stdout
    else:
        args._ocp_stream = open(args.ocpdiag, "w")
    # `plan` carries its own --serial/--station for the ResultStore; prefer those
    # over the ocp-diag-specific defaults so a single source-of-truth wins.
    dut = getattr(args, "serial", None) or args.ocpdiag_serial
    station = getattr(args, "station", None) or args.ocpdiag_station
    cmdline = " ".join(argv) if argv is not None else " ".join(sys.argv)
    params = {k: v for k, v in vars(args).items() if not k.startswith("_")}
    args._ocp = ocpdiag.open_run(
        args._ocp_stream, program_version="0.1.0", command_line=cmdline,
        parameters=params, dut_serial=dut, station=station)


def _close_ocpdiag(args, rc: int) -> None:
    em = getattr(args, "_ocp", None)
    if em is None:
        return
    status = _EXIT_TO_OCP_STATUS.get(rc, "fail")
    try:
        em.run_end(status)
    finally:
        stream = args._ocp_stream
        if stream is not None and stream is not sys.stdout:
            stream.close()


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _open_ocpdiag(args, argv)
    rc = EXIT_USAGE
    try:
        rc = _run(args)
        return rc
    except KeyboardInterrupt:
        # Convention from Bash / autoconf: 128 + SIGINT(2) = 130. Without this, Ctrl-C
        # during a long BERT prints a Python traceback into the station log.
        print("interrupted", file=sys.stderr)
        rc = 130
        return rc
    except KeyError as e:
        print(f"error: device/target not found: {e}", file=sys.stderr)
        if args._ocp:
            args._ocp.run_error("device-not-found", str(e))
        rc = EXIT_NOTFOUND
        return rc
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"error: config/IO: {e}", file=sys.stderr)
        if args._ocp:
            args._ocp.run_error("config-io-error", str(e))
        rc = EXIT_IO
        return rc
    except sqlite3.Error as e:
        # ResultStore failures (bad --db path, disk full, perms): map to EXIT_IO so
        # the operator console sees the documented exit code, not a Python traceback.
        print(f"error: results DB: {e}", file=sys.stderr)
        if args._ocp:
            args._ocp.run_error("results-db-error", str(e))
        rc = EXIT_IO
        return rc
    except NotImplementedError as e:
        print(f"error: not available on this backend/hardware: {e}", file=sys.stderr)
        if args._ocp:
            args._ocp.run_error("unavailable", str(e))
        rc = EXIT_UNAVAIL
        return rc
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        if args._ocp:
            args._ocp.run_error("usage-error", str(e))
        rc = EXIT_USAGE
        return rc
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        if args._ocp:
            args._ocp.run_error("runtime-error", str(e))
        rc = EXIT_IO
        return rc
    finally:
        _close_ocpdiag(args, rc)


if __name__ == "__main__":
    raise SystemExit(main())
