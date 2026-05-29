"""PCIe-domain subpackage.

Houses subsystems that compose the PCIe story but are bulky enough to warrant
their own namespace:

* ``retimer`` — vendor-agnostic retimer telemetry (eye, EQ, Tj, loopback,
  PRBS BIST) with a Mock implementation for tests and an Astera Aries
  stub for the SDK-gated real path.

The flat top-level modules (``aer``, ``bert``, ``diagnostics``, ``linkstate``,
``margining``, ``topology``) remain at ``computetest/`` for compatibility;
new PCIe-domain work lands here.
"""
