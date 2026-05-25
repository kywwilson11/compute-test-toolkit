# computetest — Compute-Platform Manufacturing-Test Toolkit

A manufacturing test & diagnostics toolkit for server-grade compute boards. The
centerpiece is a **PCIe BERT + diagnostics** suite (a modern rebuild of the X-ES BERT
flow); around it are **NVMe / GPU / GMSL / Ethernet / CAN** checks, a **config-driven
pytest harness**, a **SQLite results store**, and a **FastAPI dashboard**.

It runs two ways from the same code:

- **On a Linux test station** — reads real PCIe sysfs/config space and wraps
  `nvme-cli`, `nvidia-smi`, `ethtool`, `v4l2-ctl`, etc.
- **On a laptop (macOS/anything) with no hardware** — a **mock backend** simulates a
  board with injectable errors, so you can develop, demo, and unit-test everything.
  Force the choice with `COMPUTETEST_BACKEND=mock|real`.

> **New here? Read [`USAGE.md`](USAGE.md)** — the task-oriented how-to: install/verify in
> 5 minutes, mock-vs-real, a full per-command reference (syntax, flags, real output, exit
> codes, `--json` shapes), writing a test plan, running on a real Linux station, safety,
> and troubleshooting.

## Quick start (no hardware needed)

```bash
cd toolkit
make demo        # run the PCIe BERT across a simulated board
make plan        # run the full example test plan (PCIe + NVMe + GPU + GMSL + Eth + CAN)
make test        # 48 unit tests, all on the mock backend
make c           # build the C BERT engine (Linux runtime; compiles on macOS too)
```

CLI (auto-selects mock on a laptop, real hardware on a Linux station):

```bash
PYTHONPATH=src python3 -m computetest.cli list
PYTHONPATH=src python3 -m computetest.cli diagnose            # full PCIe diagnostic, all devices
PYTHONPATH=src python3 -m computetest.cli bert -d 0000:03:00.0 --target-ber 1e-12
PYTHONPATH=src python3 -m computetest.cli ber --target-ber 1e-12 --confidence 0.95   # math only
PYTHONPATH=src python3 -m computetest.cli nvme /dev/nvme0
PYTHONPATH=src python3 -m computetest.cli plan configs/example_plan.json
```

## The BERT, in one paragraph

The C engine (`c/pcie_bert.c`) exercises a link, **arms the AER status latches with a
write-1-to-clear of only the set bits**, then fast-polls and re-clears so it can *count*
error events (a latch is "an error happened", not a count — clearing fast is how you
count, and why slow clearing undercounts). It accounts bits from `link_speed × width ×
time` and emits JSON. The Python layer (`src/computetest/ber.py`) turns `(errors, bits)`
into a **confidence that the true BER is below target**:

```
CL = 1 − PoissonCDF(E; n·p)         # n = bits, p = target BER, E = errors
zero-error bits needed: n = −ln(1−CL)/p      # 95% @ 1e-12 ⇒ 2.996×10¹² bits ("3/BER")
BER upper bound:        χ²⁻¹(CL, 2E+2) / (2n)
```

It uses `scipy` if installed and a pure-stdlib incomplete-gamma implementation otherwise.

## Beyond the BERT (the diagnostics)

`computetest.diagnostics.diagnose()` combines, per device:

- **Link health** — current vs max/expected speed & width (catches silent Gen/width drops).
- **Retrain monitoring** — counts LTSSM Recovery entries during a soak (a link that ends
  fine but retrained 40× is a failing unit).
- **AER decode** — names the correctable/uncorrectable bits (which *layer* failed).
- **BERT** — the confidence-target measurement above.
- **Lane margining** — per-lane receiver eye margin (the scope-free, spec-standard
  successor to a pre-emphasis sweep). `margining.characterize_equalization()` does the
  TX-preset sweep and finds the best preset.

## Project layout

```
toolkit/
├── c/pcie_bert.c              # C BERT engine (arm/clear, count, bits, JSON)
├── src/computetest/
│   ├── backend.py             # Real (Linux sysfs) / Mock (injectable) hardware access
│   ├── ber.py                 # BER confidence math (Poisson/χ²), scipy-optional
│   ├── aer.py                 # AER decode + write-1-to-clear primitive
│   ├── bert.py                # arm→stress→read→decide orchestrator (python or C engine)
│   ├── linkstate.py           # link speed/width + retrain monitoring
│   ├── margining.py           # lane margining + eq-preset sweep
│   ├── topology.py            # expected-topology config + enumeration check
│   ├── diagnostics.py         # the combined per-device PCIe diagnostic
│   ├── nvme.py gpu.py gmsl.py ethernet.py   # interface checks
│   ├── results.py             # SQLite results + heartbeats
│   ├── harness.py             # config-driven full test-plan runner
│   └── cli.py                 # command-line front end
├── dashboard/app.py           # FastAPI results + heartbeat dashboard
├── configs/                   # example_plan.json / example_topology.yaml
├── tests/                     # 48 pytest tests (all run on the mock backend)
└── demo_bert.py               # standalone BERT demo
```

## Safety

- **Read-only by default.** The only write path is the BERT's AER arm/clear (write-1-to-
  clear of status bits). Link retrain and any traffic generation are opt-in.
- **Real config-space writes need root.** The C engine and `RealBackend.write_config` open
  `/sys/.../config` `O_RDWR`.
- **Destructive tests are gated.** NVMe `format`/secure-erase and `fio` writes destroy data;
  those paths require explicit confirmation and must never run against a mounted/boot drive.
- **Lane margining perturbs a live link** and depends on uneven kernel/vendor support, so
  the real path is intentionally guarded — validate on your hardware before enabling. The
  mock margining works everywhere for development and demos.

## Real-hardware notes

On a Linux station the backend auto-selects real hardware. The C engine builds with
`make -C c` and runs as `sudo ./c/pcie_bert -d <bdf> -t 12 --json`. The interface checks
require the usual tools (`nvme-cli`, NVIDIA driver + `nvidia-smi`/DCGM, `ethtool`,
`v4l2-ctl`, `i2c-tools`, `can-utils`). YAML test plans need `pip install pyyaml`; the
dashboard needs `pip install fastapi uvicorn`.

See `../guides/` for the deep-dive on the *why* behind every check.
