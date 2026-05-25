#!/usr/bin/env python3
"""Demo the PCIe BERT across a simulated compute board — no hardware needed.

    python3 demo_bert.py

Uses the mock backend (a board with 2 GPUs, 2 NVMe, and a custom card; one GPU
link is intentionally marginal). Runs the arm -> stress -> read -> decide loop to
a confidence target. A fast target (1e-9) is used so the demo finishes in seconds;
production uses 1e-12 (~12 s of clean traffic at Gen4 x16).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from computetest.backend import MockBackend          # noqa: E402
from computetest import bert                          # noqa: E402


def main() -> None:
    be = MockBackend()
    print("Simulated compute board:")
    for bdf in be.list_devices():
        d = be.get_device(bdf)
        print(f"  {bdf}  {d.vendor_name:18} Gen{d.current_link_speed} x{d.current_link_width}"
              f"  (max Gen{d.max_link_speed} x{d.max_link_width})")

    print("\nPCIe BERT  (target 1e-9 @ 95% confidence for a fast demo)\n" + "-" * 78)
    for bdf in be.list_devices():
        r = bert.run_bert(be, bdf, target_ber=1e-9, confidence=0.95, max_seconds=2.0)
        print("  " + r.summary())
    print("-" * 78)
    print("PASS = proved BER <= target at the confidence level, no uncorrectable errors.")
    print("FAIL = an uncorrectable error, or couldn't prove the target in the time budget.")


if __name__ == "__main__":
    main()
