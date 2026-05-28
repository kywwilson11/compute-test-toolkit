# computetest — User Manual

A focused guide to using `computetest` well. **`README.md`** is the elevator pitch and
**`USAGE.md`** is the per-command reference; **this** is the document to read first if
you want to *think correctly* about what the toolkit is doing and what its verdicts mean.

Time to read: ~20 minutes. Keep it next to your workstation; come back to the
troubleshooting cookbook (§7) when something is unclear.

---

## Contents

1. [What it does, in one screen](#1-what-it-does-in-one-screen)
2. [The mental model: two test layers](#2-the-mental-model-two-test-layers)
3. [Verdicts: pass, fail, skip — and what they mean](#3-verdicts-pass-fail-skip--and-what-they-mean)
4. [The BERT, explained](#4-the-bert-explained)
5. [Running on real hardware](#5-running-on-real-hardware)
6. [Writing a test plan](#6-writing-a-test-plan)
7. [Troubleshooting cookbook](#7-troubleshooting-cookbook)
8. [Workflows: manufacturing line, R&D debug, CI](#8-workflows)
9. [Reference: exit codes, env vars, file layout](#9-reference)

---

## 1. What it does, in one screen

`computetest` is a manufacturing-test and diagnostics toolkit for the **server-grade
compute platform in a Zoox AV**: GPUs, NVMe SSDs, GMSL cameras, automotive Ethernet,
CAN, DRAM, and the PCIe interconnect that ties them together. Currently a **PCIe Gen5**
target (32 GT/s NRZ), with full support across **Gen1-Gen6**.

The centerpiece is a **PCIe Bit-Error-Rate Tester (BERT)** that decides per-link, with a
defensible confidence number, whether the link meets a BER target (production:
`1e-12 @ 95 %`). Around the BERT sit register-level health checks for each subsystem,
a **config-driven test-plan harness**, an **SQLite results store** and **FastAPI
dashboard**, and **SCPI/VISA bench-instrument control** for stress runs.

Three things to know up front:

- **Two backends.** `MockBackend` simulates a board on your laptop (everything in this
  manual runs locally without hardware). `RealBackend` reads `/sys/bus/pci` and config
  space on a Linux station. The toolkit auto-picks, or force with
  `COMPUTETEST_BACKEND=mock|real`.
- **Read-only by default.** The only writes are AER status-bit clears (write-1-to-clear,
  during the BERT). No reformatting, no retraining, no stress runs unless you opt in.
- **The verdict is what matters.** Every command produces a `pass | fail | skip` verdict
  AND the data behind it. Read both — see §3.

Quick start (no hardware, ~30 seconds):

```bash
cd toolkit
make demo            # runs the BERT on a simulated board, shows JSON verdicts
make plan            # runs the full example test plan against the mock board
make test            # 434 pytest tests, ~13s; the regression net
```

---

## 2. The mental model: two test layers

A board passes manufacturing test iff **every link is healthy AND every device behind
those links is healthy**. The toolkit makes this explicit by splitting tests into two
layers; a test plan tests each device on **both** layers:

| Layer | Tests | Example checks | Commands |
|-------|-------|----------------|----------|
| **Layer 1 — LINK** | The wire to each device | speed/width vs expected, AER decode, BERT, lane margining, retrains during soak | `bert`, `diagnose`, `chain` |
| **Layer 2 — DEVICE** | The device itself | NVMe SMART, GPU ECC/thermal, GMSL link+video, Ethernet link, CAN state | `nvme`, `gpu`, `gmsl`, `eth`, `can` |

A GPU appears in **both** layers on purpose: Layer 1 verifies its PCIe link trains
Gen5 x16 with no errors; Layer 2 verifies its ECC counter is clean and it isn't
thermally throttled. A perfect link to a GPU with failing ECC must still fail, and a
healthy GPU on a Gen3-degraded link must still fail — so the same device is
intentionally referenced in both layers of a plan. Listing it in `pcie_devices` does
NOT health-check it, and listing it in `functional_checks.gpus` does NOT test its link.

**Why this split is useful.** When a board fails, the layer tells you what kind of
fault: Layer 1 fail = wire/interconnect/marginal-link; Layer 2 fail = device-internal
fault. They're triaged differently — see §7.

---

## 3. Verdicts: pass, fail, skip — and what they mean

Every check produces one of three verdicts. The distinction between **fail** and
**skip** is the single most important semantic in the tool, so read this section once.

| Verdict | Meaning | Exit code |
|---------|---------|-----------|
| **`pass`** | The measurement was made AND the DUT met the target | 0 |
| **`fail`** | The measurement was made AND the DUT did NOT meet the target | 1 |
| **`skip`** | The measurement could NOT, or was NOT, made | 5 (EXIT_UNAVAIL) |

**`skip` is never a silent pass.** The toolkit is built so that *not measuring* a thing
is reported as honestly as a failure. Concretely, you get a `skip` verdict when:

- The device has neither an AER capability nor a PCIe Device Status register → no error
  source to measure → BERT skips (rather than reporting `pass` from zero errors it
  couldn't see).
- The device's link speed/width came back as `0` from sysfs (enumeration failure) →
  `bps == 0` → BERT can't account for bits → skip (rather than busy-loop for `max_seconds`
  and report `fail`).
- You ran `diagnose --no-bert` → the device wasn't error-rate-measured at all → skip
  (rather than report a `pass` from a measurement we deliberately did NOT make).
- A real-hardware-only path (`v4l2-ctl` for GMSL, `nvme-cli`, `ethtool`) isn't
  installed → raises, mapped to `EXIT_UNAVAIL`.

**Read `skip` as "couldn't measure," not "looks fine."** A board with 8 GPUs where 1
returns `skip` is not a passing board — it's an incompletely-tested board. The harness's
aggregate verdict is: `any fail → fail, else any skip → fail (incomplete), else pass`.

A real fault always **wins over** skip. If the link is degraded (Layer 1) or any
uncorrectable AER bit is set, the diagnostic returns `fail` even if the BERT was a
skip — see `PcieDiagnostic.status` for the full ordering.

---

## 4. The BERT, explained

The PCIe BERT is the centerpiece. This section is the minimum you need to interpret a
verdict and recognize when one is borderline. Math intuition only; the full derivation
is in `src/computetest/ber.py`.

### What BER is, and why confidence matters

Bit Error Rate (BER) is the probability that a transmitted bit comes out wrong on the
receive side. The PCIe 5.0 spec target (pre-FEC) is **`BER ≤ 1e-12`** — at most one
error in a trillion bits. At Gen5 x16 that's `~504 Gb/s` payload, so a 1e-12 link
produces about `0.5 errors/sec` on average.

You **can't observe 1e-12 directly** — to be 95 % confident the true BER is ≤ 1e-12
with **zero observed errors** you need to drive `~3 × 10¹²` bits with no errors. At
Gen5 x16 that's ~6 seconds. At Gen4 x16 ~12 seconds. At Gen3 ~24 seconds.

The toolkit's `computetest ber --target-ber 1e-12 --confidence 0.95 --errors 0` runs
this math and prints the time estimate at all four reference rates:

```
To prove BER <= 1.0e-12 at 95% with 0 errors: 2.9957e+12 bits
(~5.9s @ Gen5 x16; 11.9s @ Gen4 x16; 23.8s @ Gen3 x16; 3.1s @ Gen6 x16)
```

### How the toolkit counts errors

The AER (Advanced Error Reporting) **status registers are latches** — one bit per
error *type*, set to 1 when at least one such error has occurred since the last clear.
A latch does NOT count: 1000 Bad TLPs in one poll window show up as a single bit set,
just like 1 Bad TLP.

To turn latches into a count, the engine:

1. **Arms** the registers (write-1-to-clear every bit we handle).
2. **Polls** in a tight loop: read the correctable-status register, count each set
   bit's type, and W1C-clear immediately so the next error can latch.
3. **Accounts** bits transferred from `link_speed × link_width × elapsed`.
4. **Decides**: a sequential confidence test (Poisson / chi-squared bound) — every
   poll, do we have enough bits-and-few-enough-errors to claim `BER ≤ target` at the
   confidence target, OR is the upper bound now so high that we'd reject? Either way
   we stop; otherwise we extend up to a takt budget (default `3 × n₀`) and a wall-clock
   cap (default `max_seconds`).

### When to trust the verdict (the calibration corner)

The bit-counting model is **exact only while error_rate << poll_rate**. At higher rates,
the count saturates at the poll rate (a catastrophically failing link still reports
`fail` decisively — the verdict is still right — but the *count* isn't a calibrated
rate). The C engine reports its achieved `poll_rate_hz` in the JSON; the Python
conductor surfaces a calibration note if it falls below `10 × target_ber × bps`. If you
see that note in the result, treat the count as a lower bound, not a measurement.

A healthy station typically achieves 100k+ polls/sec on the C engine; a 1 kHz poll rate
is a sign of a contended host (CPU saturation, virtualized PCIe config, etc.) — fix
that before trusting borderline counts.

### Generation caveat: Gen6+ FLIT/FEC

Gen1–Gen5 use NRZ + 128b/130b (or 8b/10b on Gen1/2) and report bit errors as AER Bad
TLP / Replay events the bit-counting model catches faithfully. **Gen6** introduces
PAM4 + 256B FLIT framing + forward error correction: many physical-layer symbol errors
are FEC-corrected and never become AER events. On a Gen6 link the toolkit emits the
note: *"Gen6+ FLIT/FEC: AER LCRC-retry counting under-measures BER; use FEC/symbol
statistics."* For Gen5 (today's Zoox target) this caveat does not apply.

---

## 5. Running on real hardware

Everything above also runs on a real Linux test station. Differences:

- **Run as root.** Reading the AER extended-capability config space, and the W1C
  clears, require root.
- **Linux only.** `RealBackend` reads `/sys/bus/pci/devices`. macOS/Windows fall back
  to the mock.
- **Sysfs path is overridable.** Pass `-r /some/sysfs` to the C engine, or set
  `COMPUTETEST_SYSROOT=/some/sysfs` for both the C engine and the Python `RealBackend`.
  This is how the QEMU+QMP CI lane works against a guest, and how the test corpus
  replays real outputs.
- **The backend auto-picks.** `select_backend()` checks `/sys/bus/pci`: if it exists
  and is a real Linux PCI host, `RealBackend`; else `MockBackend`. Force the choice
  with `--backend mock|real` or `COMPUTETEST_BACKEND=mock|real`.

A standard prep on a fresh station:

```bash
# Required tools
sudo apt-get install -y nvme-cli ethtool pciutils iproute2 v4l-utils

# Build the C BERT engine (no install needed)
make -C c

# Sanity-check: enumerate, then a 2-second BERT on the first PCIe device
sudo COMPUTETEST_BACKEND=real python -m computetest.cli list
sudo COMPUTETEST_BACKEND=real python -m computetest.cli bert <BDF> --max-seconds 2
```

### Performance characteristics on real hardware

| Operation | Typical wall-clock on a healthy station |
|-----------|-----------------------------------------|
| `list` enumeration | < 100 ms |
| Single-device link health (`diagnose --no-bert --no-margin`) | < 1 s |
| BERT to prove `1e-12 @ 95 %`, zero errors, Gen5 x16 | ~6 s |
| BERT to prove `1e-12 @ 95 %`, zero errors, Gen4 x16 | ~12 s |
| Full sample-plan run (the mock board: 5 PCIe devices + Layer 2 checks) | < 20 s (mock) |

If your numbers are much higher than these, watch the BERT result for the
`poll rate ... saturates` note (§4) and the link-degrade reasons in the diagnose
output — both will point you at the bottleneck.

---

## 6. Writing a test plan

A test plan is YAML or JSON (the same schema; YAML's just nicer to comment). One file
per board revision; CI tests the file's loadability.

A minimal annotated plan (`configs/example_topology.yaml` is the full version):

```yaml
target_ber:       1.0e-12      # BERT target — production: 1e-12
confidence:       0.95         # BERT confidence — production: 0.95
bert_max_s:       30           # per-device BERT time cap (seconds)
watch_retrains_s: 0.3          # how long to watch for retrain events during BERT

# Layer 1 — PCIe LINK expectations. Each entry matches one or more devices and tests
# the LINK to each match (enumeration, link speed/width, AER, BERT, margining).
pcie_devices:
  - name: GPU
    match: {vendor_id: 0x10DE, class_code: 0x030000}
    count: 2                   # the board must present exactly 2 GPUs
    expected_speed: 5          # Gen5 (32 GT/s) — the Zoox compute target
    expected_width: 16
    min_margin_ui: 0.25        # lane-margining threshold

  - name: NVMe
    match: {class_code: 0x010802}
    count: 2
    expected_speed: 4          # NVMe SSDs commonly stay at Gen4
    expected_width: 4

# Layer 2 — DEVICE health, keyed by OS handle. Tests each device's own state.
functional_checks:
  nvme:     ["/dev/nvme0", "/dev/nvme1"]   # NVMe SMART (device paths)
  gpus:     [0, 1]                         # GPU ECC/thermal (NVML indices)
  gmsl:     ["1-0029"]                     # GMSL link+video (i2c-bus + addr)
  ethernet: ["eth0"]                       # iface names
  can:      ["can0"]                       # iface names
```

Run it:

```bash
computetest plan configs/your-plan.yaml --db results.db --serial DUT-001
```

The plan writes every record to SQLite (the dashboard reads from there) and prints the
human report. Exit code maps the aggregate: any `fail → 1`, else any `skip → 5`, else `0`.

**Gotchas writing plans.**

- `expected_speed` is a **PCIe generation code** (1..6), not GT/s.
- `match` uses the same field names that sysfs uses: `vendor_id`, `device_id`,
  `class_code`, or an exact `bdf`. Hex literals (`0x10DE`) work in YAML.
- Unknown keys are silently ignored — useful for comments like `_speed_note:` but
  also why typos in real keys go unnoticed. Diff against the example to be safe.
- For a clean run on a fresh station, write to a file DB: `--db results.db`. The
  default `:memory:` is per-process and the dashboard can't read it.

---

## 7. Troubleshooting cookbook

A symptom → cause → action table for the most common failure modes. Each row is
written to be read top-to-bottom in a debug session.

### BERT reports `fail` but the link looks fine

| Symptom | Likely cause | Action |
|---|---|---|
| `fail`, `correctable > 0`, `uncorrectable == 0`, `stuck == false` | Real rate errors above target — marginal link or marginal endpoint | Re-run with `--target-ber 1e-9` to confirm; check link with `diagnose`; physical: cable, slot reseat, retorque heatsink |
| `fail`, `stuck == true`, idle errors in the note | **Constant fault** — a bit set even at idle = SI/marginality so severe the link reports errors with no traffic | Likely physical (cable/board); not a BER rate question. Replace, re-seat |
| `fail`, `note` contains `errors present at idle (constant fault)` | Same as above. Reported separately so you don't confuse it with a high-rate run | Replace the device or cable |
| `fail`, `note` contains `poll rate ... saturates above this rate` | Host loaded — the count is a lower bound, not a calibrated rate | The verdict is still correct (saturated count rejects); but for diagnostic precision, free up the host and re-run |
| `fail`, `uncorrectable > 0` | An uncorrectable AER bit was latched. Decode in `uncorrectable_decode` | Any uncorrectable is an automatic fail. Treat as a real fault; do NOT continue without a fix |

### BERT reports `skip` — what was it?

| Symptom | Cause | Action |
|---|---|---|
| `skip`, `aer_available == false`, `note` "no PCIe error source" | Device has neither AER nor a PCIe capability (legacy / virtualized endpoint) | Add a Layer 2 check; we cannot make a Layer 1 BER assertion |
| `skip`, `note` "link rate unknown" | sysfs `current_link_speed`/`current_link_width` came back 0 — enumeration failure or a non-trained link | Re-enumerate (`echo 1 > /sys/bus/pci/rescan`); check kernel log for training errors |
| `skip` from `diagnose --no-bert` | You opted out of the BER measurement. Verdict is honestly "couldn't claim PASS from a measurement we didn't make" | Either drop `--no-bert` or accept the exit 5 (incomplete coverage) |

### `diagnose --no-bert` returns exit 5 (EXIT_UNAVAIL)

Working as designed. We never report `pass` from a measurement we didn't make. If you
want a quick "link looks alive without spending BERT time," `--no-bert --no-margin` is
fine for triage; just don't gate your manufacturing-line green light on its exit code.

### `nvme` / `eth` / `gmsl` returns exit 5

The CLI tool for that subsystem isn't installed. Install it (`apt-get install nvme-cli
ethtool v4l-utils`) and re-run. The toolkit deliberately distinguishes "tool missing"
(EXIT_UNAVAIL) from "device failed" (EXIT_FAIL) so a station's tooling gap doesn't
look like a hardware fault.

### Speed/width reads as "Gen3" on a Gen5-capable link

| Cause | Confirm | Action |
|---|---|---|
| Link trained low at boot (signal-integrity, equalization) | `current_link_speed` ≠ `max_link_speed` in `list` output | Reset the link: `echo 1 > /sys/bus/pci/devices/$BDF/reset` (root); if it still trains low, physical layer |
| The endpoint can't do Gen5 | `max_link_speed` reads Gen3 | Check the part number — common for some NVMe / capture cards |
| Cable is too long / wrong | Physical inspection | Replace; spec the cable for Gen5 reach |

### "I see a fail in the dashboard but the device passes when re-tested"

Either a transient fault, a glitchy host, or a stale record. Plan retries belong at
the harness layer (re-run the test) not inside the BERT — a real BERT pass is the
*answer* to retries. Check the timestamps and tags on the record vs the re-test.

---

## 8. Workflows

### A. Manufacturing line (production)

The end-to-end flow each board runs through:

```bash
# 1. One plan file per board revision, version-controlled.
# 2. Each station has a unique --station name and a shared --db path.
sudo COMPUTETEST_BACKEND=real \
  computetest plan configs/rev-c.yaml \
    --db /var/lib/computetest/results.db \
    --serial $DUT_SERIAL \
    --station ${HOSTNAME}

# Exit code:
#   0 -> board passes (green light, move to next station)
#   1 -> board fails  (failure bin, with the plan report in the operator console)
#   5 -> incomplete coverage (tool missing / device couldn't be measured — treat as fail)
```

**Wire-up notes.** The CLI writes to SQLite atomically; the dashboard reads from the
same file. Set `COMPUTETEST_DB=...` for the dashboard host (it doesn't need root —
it's a reader). The plan + the schema are the only artifacts a station needs to be
revision-portable.

### B. R&D debug (one board on the bench)

A typical "this board feels marginal" session:

```bash
# 1. What does this board look like at the link layer?
sudo computetest list
sudo computetest diagnose --json | jq .

# 2. Run a quick BERT (loose target) on the suspect device.
sudo computetest bert 0000:03:00.0 --target-ber 1e-9 --confidence 0.95 --max-seconds 5

# 3. If that passes but the prod target fails: a marginal link.
sudo computetest bert 0000:03:00.0 --target-ber 1e-12 --confidence 0.95 --max-seconds 30

# 4. Get a per-lane margining number for a Gen4+ link.
#    (Real-hw guarded; on the mock you get a believable simulated number.)
sudo computetest diagnose -d 0000:03:00.0 --json | jq '.margin'

# 5. Watch for retrains during a short soak.
sudo computetest diagnose -d 0000:03:00.0 --json | jq '.link.retrains'
```

When triaging, use `--json | jq` for everything. The JSON shape is the source of truth;
the human summary is a convenience.

### C. CI (the non-hardware lane in this repo)

`make test` runs the no-hardware pytest suite (~434 tests, ~13s) plus `ruff`, `mypy`,
`coverage`. Add `make ctest` for the C engine's Unity tests. CI runs both on every
push (`.github/workflows/test.yml`) on Ubuntu+macOS × py3.10-3.13.

For the privileged real-hardware path verification (AER injection + nvme-loop + vcan),
CI boots a QEMU guest (`.github/workflows/qemu.yml`), drives QMP from the host, and
asserts on what the toolkit's real backend reports inside the guest. See
`sim/qemu/README.md` for the lane.

Nightly mutation testing (mutmut) runs at 07:00 UTC on the six logic modules:
`.github/workflows/mutmut.yml`. Survivor count is the signal for "where to add tests."

---

## 9. Reference

### Exit codes

| Code | Constant | When |
|---|---|---|
| 0 | `EXIT_PASS` | The DUT passed |
| 1 | `EXIT_FAIL` | The DUT failed (a real fault) |
| 2 | `EXIT_USAGE` | Bad CLI args / invalid input value |
| 3 | `EXIT_NOTFOUND` | The target device/handle wasn't found |
| 4 | `EXIT_IO` | Config/IO/SQLite error (incl. JSON decode, bad `--db` path) |
| 5 | `EXIT_UNAVAIL` | Tool missing, capability unavailable, OR the measurement was skipped/incomplete |
| 130 | (convention) | Ctrl-C — clean interrupt, no traceback |

The harness aggregate (plan, diagnose-all): any `fail → 1`, else any `skip → 5`, else `0`.

### Environment variables

| Var | Meaning |
|---|---|
| `COMPUTETEST_BACKEND` | `mock` or `real` — overrides the auto-pick |
| `COMPUTETEST_SYSROOT` | Sysfs root for the Real backend + C engine (defaults to `/sys/bus/pci/devices`) |
| `COMPUTETEST_DB` | SQLite path for the dashboard (`:memory:` is per-request and empty) |

### File layout

```
toolkit/
├── README.md          # elevator pitch
├── MANUAL.md          # this document
├── USAGE.md           # per-command reference
├── Makefile           # entry points: demo, plan, test, ctest, lint, typecheck, ...
├── pyproject.toml     # [tool.ruff], [tool.mypy], [tool.coverage]
├── src/computetest/   # the package (Python)
│   ├── backend.py     # Mock + Real backends, sample board
│   ├── ber.py         # BER confidence math (Poisson / chi-squared)
│   ├── bert.py        # the BERT loop (python + c conductors)
│   ├── aer.py         # AER decode + W1C
│   ├── diagnostics.py # the PcieDiagnostic orchestrator
│   ├── topology.py    # plan file loader + matching
│   ├── linkstate.py   # link health (speed/width vs expected, retrains, LBMS/LABS)
│   ├── margining.py   # Lane Margining (Gen4+ mandatory on Gen5 downstream ports)
│   ├── nvme.py, gpu.py, gmsl.py, ethernet.py, memory.py   # Layer 2 checks
│   ├── results.py     # SQLite store + dashboard backing
│   ├── harness.py     # plan runner
│   ├── cli.py         # the CLI dispatcher
│   └── dmesg.py, instruments.py
├── c/
│   ├── pcie_bert.c          # the C BERT engine (the "dumb fast counter")
│   ├── pcie_bert_core.{c,h} # testable core behind a cfg_io seam
│   └── test/                # Unity unit tests
├── dashboard/app.py   # FastAPI dashboard
├── tests/             # pytest suite (~434 tests)
├── configs/           # example plan (YAML + JSON mirror)
├── corpus/            # real captures of nvme-cli/ethtool, replayed in tests
└── sim/qemu/          # QEMU+QMP AER-injection CI lane (Phase 2/3)
```

### Commands at a glance

```
computetest list                    # enumerate PCIe devices
computetest bert <BDF>              # run the BERT on one device
computetest diagnose [-d <BDF>]     # full PCIe diagnostic per device
computetest chain <ENDPOINT>        # diagnose every link in an endpoint's path
computetest plan <CONFIG>           # run a full board test plan
computetest nvme   <DEVICE>         # NVMe SMART health
computetest gpu    <INDEX>          # GPU ECC/thermal health
computetest gmsl   <I2C-ID>         # GMSL link + video
computetest eth    <IFACE>          # Ethernet link
computetest can    <IFACE>          # CAN state
computetest ber    --target-ber ... # pure BER math, no hardware (planning tool)
```

All commands accept `--json` for machine output and `--backend mock|real` to force the
backend. See `USAGE.md` for every flag.

---

*Last revised: this manual tracks the audit-2/audit-3 round (Gen5 first-class, the
`--no-bert` skip semantics, the dashboard XSS fix, MockBackend thread-safety, the C
engine `poll_rate_hz` calibration signal). When the toolkit grows new commands or
verdict semantics, update §3, §7, and §9 here.*
