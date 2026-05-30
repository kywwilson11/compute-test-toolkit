# computetest — Compute-Platform Test Toolkit

A manufacturing-test & diagnostics toolkit for the **server-grade compute** in an
autonomous vehicle. The centerpiece is a **PCIe bit-error-rate tester +
diagnostic suite** (a modern rebuild of the X-ES BERT flow); around it sit
register-level conformance + RAS checks for **NVMe, GPUs, GMSL3 camera SerDes,
automotive-Ethernet TSN, DDR5, CXL, Ethernet/CAN**, a config-driven **test-plan
harness**, **SCPI/VISA bench-instrument control**, an **OCP ocp-diag-core**
emitter, and a SQLite results store + dashboard.

**One codebase, two backends** — force with `--backend mock|real` or
`COMPUTETEST_BACKEND`, otherwise auto-selected:

- **Mock** (any laptop, no hardware) — a simulated board with injectable faults, so
  you can develop, demo, and run the **entire** test suite on macOS.
- **Real** (Linux station) — reads PCIe sysfs/config space and wraps `nvme-cli`,
  `nvidia-smi`/DCGM, `ethtool`, EDAC, `i2c-tools`, `can-utils`, pyvisa, …

> Python 3.10+ · ~1,000 mock-backed tests · ~96% line+branch coverage (CI-gated at 95%)

---

## Install

Requires **Python 3.10+**. Clone, make a virtualenv, install editable:

```bash
git clone https://github.com/kywwilson11/compute-test-toolkit.git
cd compute-test-toolkit/toolkit
python3 -m venv .venv && source .venv/bin/activate
pip install -e .              # installs the `computetest` command + the library
```

That's everything you need to run the whole toolkit on the mock backend — **no
hardware and no extra dependencies**. Install extras only as needed:

| Extra | Command | Adds |
|---|---|---|
| **dev** | `pip install -e '.[dev]'` | pytest, hypothesis, coverage, ruff, mypy, mutmut, pyyaml — everything to run the tests + CI gates *(recommended for development)* |
| **sci** | `pip install -e '.[sci]'` | scipy (faster/exacter BER math; a pure-Python fallback ships built in) |
| **yaml** | `pip install -e '.[yaml]'` | PyYAML (YAML test plans; JSON plans work without it) |
| **dashboard** | `pip install -e '.[dashboard]'` | FastAPI + uvicorn (the results dashboard) |

*(zsh needs the quotes around `.[dev]`.)* Optional **C BERT engine** (Linux runtime,
also compiles on macOS): `make c`. The Python BERT path works without it.

**Run from source without installing:** every command also works as
`PYTHONPATH=src python3 -m computetest.cli <command>`, and the `make` shortcuts
need no install.

---

## Run it — the `computetest` CLI

After `pip install -e .`, the program is the **`computetest`** command. It
**auto-selects the mock backend on a laptop** and real hardware on a Linux station
with PCIe sysfs (override with `--backend mock|real`).

```bash
computetest list                                       # enumerate PCIe devices
computetest diagnose                                   # full PCIe diagnostic, every device
computetest bert -d 0000:03:00.0 --target-ber 1e-12    # BERT one link to a confidence target
computetest nvme /dev/nvme0                             # NVMe SMART health
computetest plan configs/example_plan.json             # run a full config-driven test plan
computetest ber --target-ber 1e-12 --confidence 0.95   # just the math, no hardware
```

Full command list (`computetest <cmd> --help` prints every flag):

| Command | What it does | Key flags |
|---|---|---|
| `computetest list` | enumerate PCIe devices | |
| `computetest diagnose [-d BDF]` | full PCIe diagnostic: link + retrains + AER + BERT + margining | `--no-bert` `--no-margin` `--target-ber` `--max-seconds` |
| `computetest bert -d BDF` | BERT one link to a BER confidence target | `--target-ber 1e-12` `--confidence 0.95` `--engine python\|c` `--explain` |
| `computetest chain ENDPOINT_BDF` | diagnose every link on the root→endpoint path | `--expected-speed` `--expected-width` |
| `computetest nvme TARGET` | NVMe SMART / self-test / error log | |
| `computetest gpu INDEX` | GPU ECC / XID / throttle / clocks | |
| `computetest gmsl TARGET` | GMSL camera link lock + frame-sync | |
| `computetest eth IFACE` | Ethernet link / speed / error counters | |
| `computetest can IFACE` | CAN state / bus-off / error counters | |
| `computetest lmt -d BDF` | PCIe lane margining (OCP `pci_lmt`-compatible output) | `--format json\|csv` `--via-pci-lmt CONFIG` |
| `computetest plan CONFIG` | run a config-driven test plan (every layer) | `--db` `--serial` `--station` |
| `computetest ber` | BER confidence math only (no hardware) | `--target-ber` `--confidence` `--errors` `--bits` |

Every command also takes `--json` (machine-readable, **stdout-pure**, pipe into
`jq`) and `--ocpdiag PATH` (emit OCP ocp-diag-core JSONL alongside).

**Exit codes** — so a line/CI can tell *"the DUT is bad"* from *"we couldn't test
it"* (the two must never be conflated):

`0` pass · `1` fail · `2` usage error · `3` device/target not found · `4` config/IO error · `5` capability unavailable on this hardware

No-hardware shortcuts: `make demo` (BERT a simulated board), `make plan` (the
example plan across every interface).

---

## Run the tests

The whole toolkit runs on the **mock backend**, so the full suite passes on a
laptop with nothing attached. From `toolkit/` after `pip install -e '.[dev]'`:

```bash
make test          # the full pytest suite (mock backend)
pytest             # identical — config lives in pyproject.toml
```

**Directed runs — one interface at a time:**

| Area | Command |
|---|---|
| **Everything** | `pytest` |
| PCIe link / BERT / AER / margining | `pytest -k "bert or ber or aer or diagnostics or linkstate or margining or retimer"` |
| NVMe (+ NVMe-MI v2.1) | `pytest tests/test_nvme*` |
| GPU | `pytest tests/test_gpu*` |
| GMSL3 camera SerDes | `pytest tests/test_gmsl*` |
| Ethernet + CAN | `pytest tests/test_ethernet*` |
| DRAM / DDR5 RAS | `pytest tests/test_memory* tests/test_ddr5_*` |
| Automotive-Ethernet TSN | `pytest tests/test_tsn_*` |
| CXL | `pytest tests/test_cxl_*` |
| Bench instruments + MSA | `pytest tests/test_instruments* tests/test_msa.py` |
| Conformance coverage-matrix gates | `pytest tests/test_*coverage*` |

Ad-hoc: `pytest -k <keyword>` (substring match), or a single test with
`pytest tests/test_bert.py::test_clean_link_passes`.

**The gates CI enforces** (lint + types + coverage, in one):

```bash
make check         # = ruff + mypy + coverage (fail_under from pyproject)
# or individually:
ruff check src tests                                # style / imports / bugbear / pyupgrade
mypy                                                # type-check src/
coverage run -m pytest && coverage report -m        # ~96% line+branch (CI gate: 95%)
```

The **7 conformance coverage matrices** (UNH-IOL NVMe-MI v25, GMSL3, three TSN,
DDR5, CXL CTS) are themselves enforced by gate tests — every *Mandatory* row must
map to a real test or CI fails (`pytest tests/test_*coverage*`).

> **Python 3.13 note:** mutmut 2.x's cache is incompatible with 3.13 — run mutation
> testing (`make mutmut`) under a 3.11/3.12 venv or `pip install 'mutmut>=3'`.
> Everything else runs fine on 3.13.

---

## What it checks

| Subsystem | What it verifies | Real-HW source |
|---|---|---|
| **PCIe link / chain** | enumeration vs topology; speed/width; LTSSM retrains during soak; AER decode; **BER to a confidence target**; lane-margining eye — across every link on a root→endpoint path | sysfs/config space + C engine |
| **NVMe** | SMART (spare/wear/media/temp), self-test, error log — plus **NVMe-MI v2.1** out-of-band over MCTP/CCI | `nvme-cli` / MCTP |
| **GPU** | ECC (volatile/aggregate, row-remap), XIDs, throttle reasons, clocks | `nvidia-smi` / DCGM |
| **GMSL3** | per-link lock + **negotiated mode** (catches silent PAM4→NRZ degrade), PRBS pre/post-FEC BER, EOM eye, channel S-parameters, CSI-2 + control-channel integrity, V/T shmoo, ERRB safety | i2c / ADI registers + VNA |
| **Automotive Ethernet (TSN)** | PHY SQI/TDR, 802.1AS gPTP time-error + protocol conformance, Qbv gate timing, Qbu/Clause-99 preemption, 802.1CB FRER, hot-standby failover, OPEN-Alliance TC8 PMA | ethtool / linuxptp / TSN tester |
| **DDR5 RAS** | on-die ECC + EINJ reporting, ECS scrub, PPR self-heal, CRC/parity, RFM/PRAC enablement, training margin, SPD | EDAC sysfs / ACPI-EINJ |
| **CXL** | CCI mailbox, RAS UE/CE (write-1-to-clear), link/flit negotiation, event records, poison/viral containment, HDM interleave, Perform-Maintenance, coherency, link-extension retimer | sysfs / mailbox / QEMU |
| **Ethernet / CAN (base)** | link/speed/error counters (iperf3 opt-in); CAN state, bus-off, error counts | ethtool / SocketCAN |
| **Bench instruments** | SCPI/VISA control of PSU / DMM / scope / e-load / BERT / VNA / thermal-chamber / TSN gear | pyvisa |

Each subsystem ships a **UNH-IOL-style coverage matrix** mapping every Mandatory
conformance item to a backing test, enforced in CI.

---

## How a board is described (two layers, not new code)

The harness is driven by a plan file (`configs/example_topology.yaml`, with a
stdlib JSON mirror) that declares the board in **two layers** — this is the source
of the old *"why is there a device list **and** interface lists?"* confusion, now
made explicit:

- **`pcie_devices`** — the **link layer**: which endpoints must enumerate, at what
  Gen/width/margin. Tests the *link to* each device.
- **`functional_checks`** — the **device layer**: each endpoint's own health by OS
  handle (NVMe SMART, GPU ECC, GMSL lock, …). Tests the *chip itself*.

A GPU legitimately appears in both — once for its PCIe link, once for its silicon.
**A new board revision is a new YAML file, not new code.**

## The BERT, in one paragraph

The C engine (`c/pcie_bert.c`) exercises a link, **arms the AER status latches with
a write-1-to-clear of only the set bits**, then fast-polls and re-clears so it can
*count* error events (a latch means "an error happened", not a count — clearing
fast is how you count). It accounts bits from `link_speed × width × time` and emits
JSON; `src/computetest/ber.py` turns `(errors, bits)` into a **confidence that the
true BER is below target**:

```
CL = 1 − PoissonCDF(E; n·p)          # n = bits, p = target BER, E = errors
zero-error bits needed: n = −ln(1−CL)/p       # 95% @ 1e-12  ⇒  ~3.0×10¹² bits ("3/BER")
BER upper bound:        chi2⁻¹(CL, 2E+2) / (2n)
```

It uses `scipy` if installed and a pure-stdlib incomplete-gamma implementation
otherwise, so it runs on a bare station.

---

## Project layout

```
toolkit/
├── c/pcie_bert.c              # C BERT engine (arm/clear, count, bits, JSON)
├── src/computetest/
│   ├── backend.py             # Real (Linux sysfs) / Mock (injectable) hardware access
│   ├── ber.py aer.py bert.py linkstate.py margining.py diagnostics.py topology.py
│   │                          #   PCIe BERT + diagnostics core
│   ├── nvme/                  # NVMe health + NVMe-MI v2.1 (MCTP/CCI)
│   ├── gmsl/                  # GMSL3 camera SerDes — video · serdes · channel · checks
│   ├── tsn/                   # automotive-Ethernet TSN — phy · gptp · qbv · preemption · frer · …
│   ├── ddr5/                  # DDR5 RAS — ecc · ecs · ppr · crc · rfm · spd · …
│   ├── cxl/                   # CXL conformance + RAS — mailbox · ras · events · poison · hdm · …
│   ├── gpu.py ethernet.py memory.py     # GPU / Ethernet+CAN / DRAM interface checks
│   ├── instruments.py fixtures.py       # SCPI/VISA bench HAL + fixture-role map
│   ├── lmt_adapter.py         # OCP pci_lmt schema compatibility
│   ├── msa/                   # measurement-system analysis (gage R&R, station correlation)
│   ├── io/ocpdiag.py          # OCP ocp-diag-core JSON emitter
│   └── results.py harness.py cli.py     # results store · plan runner · CLI
├── sim/qemu/                  # QMP AER + CXL injection clients (hardware-free RAS CI)
├── docs/                      # Diataxis docs + 7 conformance coverage matrices
├── configs/                   # example topology/plan + fixture/calibration maps
├── tests/                     # ~1,000 mock-backed pytest tests
└── Makefile                   # demo · plan · test · check · ...
```

## Safety

- **Read-only by default.** The only write path is the BERT's AER arm/clear
  (write-1-to-clear of status bits); link retrain and traffic generation are opt-in.
- **Real config-space writes need root** (the C engine / `RealBackend.write_config`
  open `/sys/.../config` `O_RDWR`).
- **Destructive tests are gated** — NVMe format/secure-erase and `fio` writes
  require explicit confirmation and must never run against a mounted/boot drive.
- **Lane margining perturbs a live link** and depends on uneven kernel/vendor
  support, so the real path is guarded — validate on your hardware before enabling.
  Mock margining works everywhere for development and demos.

## Documentation

Docs follow [Diátaxis](https://diataxis.fr/):

- **[`docs/tutorials/01-first-bert.md`](docs/tutorials/01-first-bert.md)** — a 10-minute walk-through to a green BERT verdict, no hardware.
- **[`docs/howto/`](docs/howto/)** — task recipes (run on real hardware, emit ocp-diag, run Gage R&R, …).
- **[`docs/reference/`](docs/reference/)** — authoritative CLI + result-shape reference.
- **[`docs/explanation/`](docs/explanation/)** — *why* the toolkit measures the way it does (BER confidence math, PCIe 6.0 FEC).
- **[`docs/research/`](docs/research/)** — the deep-research roadmaps that drove each sprint.
