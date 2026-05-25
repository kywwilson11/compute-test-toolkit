#!/usr/bin/env python3
"""Demo the PCIe CHAIN monitor on a simulated switch topology (no hardware).

    python3 demo_chain.py

A GPU sits behind a switch:  Root Port -> Switch Up -> Switch Down -> GPU.
The Root->SwUp direction of the first link is marginal; the GPU's own link is clean.
An endpoint-only BERT would PASS this board — the chain monitor catches the upstream
fault and pins it to the exact link + direction.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from computetest import diagnostics                                       # noqa: E402
from computetest.backend import (MockBackend, MockDevice, PORT_ENDPOINT,  # noqa: E402
                                  PORT_ROOT, PORT_SWITCH_DOWNSTREAM, PORT_SWITCH_UPSTREAM)


def main() -> None:
    be = MockBackend([
        MockDevice("0000:00:1c.0", 0x8086, 0x0000, 0x060400, 4, 16, 4, 16, "pcieport",
                   parent=None, port_type=PORT_ROOT),
        MockDevice("0000:02:00.0", 0x1000, 0x0000, 0x060400, 4, 16, 4, 16, "pcieport",
                   parent="0000:00:1c.0", port_type=PORT_SWITCH_UPSTREAM, injected_ber=1e-8),
        MockDevice("0000:03:00.0", 0x1000, 0x0000, 0x060400, 4, 16, 4, 16, "pcieport",
                   parent="0000:02:00.0", port_type=PORT_SWITCH_DOWNSTREAM),
        MockDevice("0000:04:00.0", 0x10DE, 0x2204, 0x030000, 4, 16, 4, 16, "nvidia",
                   parent="0000:03:00.0", port_type=PORT_ENDPOINT),
    ])
    print("Topology:  Root Port -> Switch Up -> Switch Down -> GPU (endpoint)")
    print("Fault:     Link A is marginal in the Root->SwUp direction; the GPU link is clean.\n")
    d = diagnostics.diagnose_chain(be, "0000:04:00.0", target_ber=1e-9, max_seconds=3,
                                   expected_speed=4, expected_width=16)
    print(d.summary())
    print("\nAn endpoint-only BERT PASSES this board. The chain monitor fails it and pins")
    print("the fault to the exact link + direction — the upstream segment a single-BDF test misses.")


if __name__ == "__main__":
    main()
