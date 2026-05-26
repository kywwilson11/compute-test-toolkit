# computetest — Compute-Platform Manufacturing-Test Toolkit

A manufacturing-test & diagnostics toolkit for the **server-grade compute** in an autonomous
vehicle. The centerpiece is a **PCIe bit-error-rate tester + diagnostic suite** (a modern
rebuild of the X-ES BERT flow); around it sit register-level health checks for **NVMe, GPUs,
GMSL cameras, automotive/standard Ethernet, CAN, and DRAM**, a **config-driven test-plan
harness**, a **SQLite results store + FastAPI dashboard**, and **SCPI/VISA bench-instrument
control**.

**One codebase, two backends:**

- **Real (Linux station)** — reads PCIe sysfs/config space directly and wraps `nvme-cli`,
  `nvidia-smi`/DCGM, `ethtool`, `i2c-tools`, `can-utils`, etc.
- **Mock (any laptop, no hardware)** — simulates a board with injectable errors, so you can
  develop, demo, and run the entire test suite on macOS. Force it with
  `COMPUTETEST_BACKEND=mock|real`; otherwise it auto-selects (real if Linux sysfs is present).

> **First time? Read [`USAGE.md`](USAGE.md)** — install/verify in 5 minutes, the per-command
> reference (flags, real output, exit codes, `--json` shapes), the **config reference**,
> running on a real station, the safety model, and troubleshooting.

## What it checks

| Subsystem | What it verifies | Real-HW source |
|---|---|---|
| **PCIe link** | enumeration vs expected topology; speed/width vs max & expected; LTSSM retrains during soak; AER correctable/uncorrectable decode; **BER to a confidence target**; lane-margining eye | sysfs/config space + C engine |
| **PCIe chain** | every BDF and link on a root -> switch -> endpoint path, per direction | sysfs |
| **NVMe** | SMART health (spare, wear, media errors, temp), device self-test, error log | `nvme-cli` |
| **GPU** | ECC (volatile/aggregate, row-remap), critical XIDs, throttle reasons, clocks | `nvidia-smi` / DCGM |
| **GMSL** | per-link lock + frame-sync across a quad deserializer | i2c / driver sysfs |
| **Ethernet / CAN** | link/speed/error counters (throughput opt-in via iperf3); CAN state, bus-off, error counts | `ethtool` / SocketCAN |
| **DRAM** | EDAC corrected/uncorrectable per DIMM, mapped to the silkscreen label | EDAC sysfs / rasdaemon |
| **Instruments** | SCPI/VISA control of PSU / DMM / scope / e-load for the bench | pyvisa |

## Quick start (no hardware needed)

```bash
cd toolkit
make demo     # run the PCIe BERT across a simulated board
make plan     # run the example test plan (PCIe + every interface check)
make test     # the full mock-backed unit suite
make c        # build the C BERT engine (Linux runtime; compiles on macOS too)
```

CLI (auto-selects mock on a laptop, real hardware on a Linux station):

```bash
PYTHONPATH=src python3 -m computetest.cli list                 # enumerate PCIe devices
PYTHONPATH=src python3 -m computetest.cli diagnose             # full PCIe diagnostic, all devices
PYTHONPATH=src python3 -m computetest.cli bert -d 0000:03:00.0 --target-ber 1e-12
PYTHONPATH=src python3 -m computetest.cli plan configs/example_plan.json
```

Every command takes `--json` (machine-readable, stdout-clean) and returns a meaningful
**exit code**: `0` pass, `1` fail, `5` couldn't-measure / unavailable (e.g. a device with no
AER capability). That lets a line or CI distinguish *"the DUT is bad"* from *"we couldn't test
it"* — the two must never be conflated.

## A board revision is a config, not new code

The harness is driven by a plan file (`configs/example_topology.yaml`, with a stdlib JSON
mirror) that declares the board in **two layers** — this is the source of the old "why is there
a device list *and* interface lists?" confusion, now made explicit:

- **`pcie_devices`** — the **PCIe-link layer**: which endpoints must enumerate, at what
  Gen/width/margin. Tests the *link to* each device (enumeration, link health, AER, BERT,
  margining), matched by vendor/class/BDF.
- **`functional_checks`** — the **device layer**: each endpoint's own health, by OS handle
  (NVMe SMART, GPU ECC, GMSL lock, …). Tests the *chip itself*.

A GPU legitimately appears in both — once to test its PCIe link, once to test the silicon. A
new board revision is a new YAML file, **not new code**. Full field-by-field docs are in the
**Config reference** in [`USAGE.md`](USAGE.md). (YAML is primary because a board config wants
inline comments and hex IDs like `0x10DE`; the JSON file is a no-dependency stdlib mirror.)

## The BERT, in one paragraph

The C engine (`c/pcie_bert.c`) exercises a link, **arms the AER status latches with a
write-1-to-clear of only the set bits**, then fast-polls and re-clears so it can *count* error
events (a latch means "an error happened", not a count — clearing fast is how you count, and
why slow clearing undercounts). It accounts bits from `link_speed × width × time` and emits
JSON. The Python layer (`src/computetest/ber.py`) turns `(errors, bits)` into a **confidence
that the true BER is below target**:

```
CL = 1 − PoissonCDF(E; n·p)         # n = bits, p = target BER, E = errors
zero-error bits needed: n = −ln(1−CL)/p      # 95% @ 1e-12  =>  2.996×10^12 bits ("3/BER")
BER upper bound:        chi2^-1(CL, 2E+2) / (2n)
```

It uses `scipy` if installed and a pure-stdlib incomplete-gamma implementation otherwise, so it
runs on a bare test station. The Python conductor decides how long the C engine must run to
reach the confidence target (with margin), extends the run when an observed error rate can
still meet the target, and fails fast (reject) when it provably cannot — bounded by a takt
budget.

## Beyond the BERT (the diagnostics)

`computetest.diagnostics.diagnose()` combines, per device:

- **Link health** — current vs max/expected speed & width (catches silent Gen/width drops; an
  *unknown* speed is flagged, not passed).
- **Retrain monitoring** — counts LTSSM Recovery entries during a soak (a link that ends fine
  but retrained 40× is a failing unit).
- **AER decode** — names the correctable/uncorrectable bits (which *layer* failed).
- **BERT** — the confidence-target measurement above.
- **Lane margining** — per-lane receiver eye margin (the scope-free, spec-standard successor to
  a pre-emphasis sweep). If a device doesn't support margining, it's recorded as *skipped* and
  the rest of the diagnostic still runs.

## Where it fits in a production line

This toolkit is a **test executive** — it sequences tests on one unit, applies limits, and
emits a verdict + parametric data. In a full mass-production stack it sits *below* the data and
observation layers, and it's built to **feed** them, not replace them:

```
[ this toolkit: sequence + measure + verdict ]      <- per unit, on the station
        |  results (JSON / SQLite / REST)
        v
[ MES: routing + genealogy / traceability ]   +   [ warehouse: parametric history ]
        v
[ Grafana / yield analytics: live yield, station heartbeats, SPC, drift ]
```

On a real line you'd point its results at the team's MES + data warehouse and dashboard rather
than rely on the bundled SQLite store + FastAPI dashboard, which exist for the bench and for
demos. (Notably, Zoox's own test-infrastructure job posts describe exposing manufacturing data
via **REST APIs** — the same shape this toolkit's results layer uses.) See the study guide's
Manufacturing-Test chapter for the full layered model.

## Tests, coverage & mutation

The whole toolkit runs on the mock backend at **99% line+branch coverage** — run `make test`,
or for the numbers:

```bash
python3 -m coverage run -m pytest && python3 -m coverage report -m   # coverage
python3 -m mutmut run                                                # mutation testing
```

Real-hardware I/O paths are marked `# pragma: no cover - real-hw path` (validated on the bench,
not the mock). Mutation testing is configured in `setup.cfg` (scoped to the decision/math
modules); the BER math (`ber.py`) was mutation-checked directly — off-by-ones in the
Poisson/chi-squared formulas and the fail-fast reject bound are all caught. (Note: mutmut 2.5's
pony-ORM cache is incompatible with **Python 3.13** — run it under a 3.11/3.12 venv or
`pip install 'mutmut>=3'`.)

## Project layout

```
toolkit/
├── c/pcie_bert.c              # C BERT engine (arm/clear, count, bits, JSON)
├── src/computetest/
│   ├── backend.py             # Real (Linux sysfs) / Mock (injectable) hardware access
│   ├── ber.py                 # BER confidence math (Poisson/chi^2), scipy-optional
│   ├── aer.py                 # AER decode + write-1-to-clear primitive
│   ├── bert.py                # arm->stress->read->decide orchestrator (python or C engine)
│   ├── linkstate.py           # link speed/width + retrain monitoring
│   ├── margining.py           # lane margining + eq-preset sweep
│   ├── topology.py            # expected-topology config + enumeration check
│   ├── diagnostics.py         # the combined per-device PCIe diagnostic
│   ├── nvme.py gpu.py gmsl.py ethernet.py memory.py   # functional interface checks
│   ├── instruments.py         # SCPI/VISA bench-instrument control (PSU/DMM/scope/load)
│   ├── results.py             # SQLite results + heartbeats
│   ├── harness.py             # config-driven full test-plan runner
│   └── cli.py                 # command-line front end
├── dashboard/app.py           # FastAPI results + heartbeat dashboard (bench/demo)
├── configs/                   # example_topology.yaml (primary) + example_plan.json (mirror)
├── tests/                     # mock-backed pytest suite
└── demo_bert.py               # standalone BERT demo
```

## Safety

- **Read-only by default.** The only write path is the BERT's AER arm/clear (write-1-to-clear
  of status bits). Link retrain and any traffic generation are opt-in.
- **Real config-space writes need root.** The C engine and `RealBackend.write_config` open
  `/sys/.../config` `O_RDWR`.
- **Destructive tests are gated.** NVMe `format`/secure-erase and `fio` writes destroy data;
  those paths require explicit confirmation and must never run against a mounted/boot drive.
- **Lane margining perturbs a live link** and depends on uneven kernel/vendor support, so the
  real path is intentionally guarded — validate on your hardware before enabling. Mock
  margining works everywhere for development and demos.

## Real-hardware notes

On a Linux station the backend auto-selects real hardware. The C engine builds with
`make -C c` and runs as `sudo ./c/pcie_bert -d <bdf> -t 12 --json`. The interface checks need
the usual tools (`nvme-cli`, NVIDIA driver + `nvidia-smi`/DCGM, `ethtool`, `i2c-tools`,
`can-utils`). YAML test plans need `pip install pyyaml`; the dashboard needs
`pip install fastapi uvicorn`; SCPI instrument control needs `pip install pyvisa`.

See `../guides/` for the deep-dive on the *why* behind every check.
