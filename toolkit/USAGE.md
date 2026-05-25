# computetest — Usage Guide (operators & engineers)

A task-oriented how-to for the `computetest` manufacturing-test toolkit. For *what the
tool is and why each check exists*, see `README.md` and `../guides/`. This document is
about *running it*.

Everything below is reproducible on a laptop with no hardware: the toolkit ships a
**mock backend** that simulates a representative board (2 GPUs, 2 NVMe, a custom card).
Every example output in this guide was captured from the mock backend.

> Convention used throughout: commands are shown as `computetest <cmd> ...`. If you have
> not run `pip install -e .` (Section 1), substitute `PYTHONPATH=src python3 -m
> computetest.cli <cmd> ...`. On a laptop, prefix with `COMPUTETEST_BACKEND=mock` to be
> explicit (it is auto-selected there anyway — see Section 2).

---

## 1. Install & verify in 5 minutes (laptop / mock)

You need Python 3.10+. Nothing else is required to run against the mock backend.

```bash
cd toolkit
make test     # run the unit suite on the mock backend  -> expect "48 passed"
make demo     # run the PCIe BERT across the simulated board (python demo_bert.py)
```

`make test` runs `pytest` with `COMPUTETEST_BACKEND=mock`; a healthy checkout prints:

```
................................................                         [100%]
48 passed
```

`make plan` runs the full example test plan end to end (PCIe + NVMe + GPU + GMSL +
Ethernet + CAN) against the mock board — see Section 3 (`plan`) for its output.

### Install the `computetest` console script (optional but convenient)

```bash
cd toolkit
pip install -e .          # exposes the `computetest` entry point (pyproject [project.scripts])
computetest list          # now works from anywhere, no PYTHONPATH needed
```

### Optional extras — what each one unlocks

The toolkit works with a bare Python install; these only make specific paths faster or
enable optional features. Install per feature, or all at once:

| Extra | Install | What it unlocks | Without it |
|-------|---------|-----------------|------------|
| `scipy` | `pip install scipy` (or `pip install -e .[sci]`) | Faster, exact incomplete-gamma BER math | A pure-stdlib gamma implementation ships built in and is used automatically |
| `pyyaml` | `pip install pyyaml` (or `.[yaml]`) | `*.yaml`/`*.yml` test plans (e.g. `configs/example_topology.yaml`) | Use the equivalent `.json` plan; YAML loading raises a clear "PyYAML not installed" error |
| `fastapi` + `uvicorn` | `pip install fastapi uvicorn` (or `.[dashboard]`) | The results/heartbeat dashboard (`make dashboard`) | `dashboard/app.py` raises "dashboard needs FastAPI" on import; the rest of the toolkit is unaffected |
| `pytest` | `pip install pytest` (or `.[dev]`) | `make test` / running the suite | — |

The BER math automatically uses scipy if present and the stdlib fallback otherwise; you
never choose. The fallback raises (rather than returning a wrong answer) only in the
millions-of-errors regime, where the unit fails anyway — install scipy if you live there.

---

## 2. Mock vs real — how the backend is chosen

The exact same code runs against simulated or real hardware; only the backend differs.

**Selection order** (`computetest.backend.select_backend`):

1. `--backend mock|real` flag, if given (it sets `COMPUTETEST_BACKEND` for the process).
2. `COMPUTETEST_BACKEND=mock|real` environment variable.
3. **Auto** (the default, `--backend auto`): use the **real** Linux backend **iff
   `/sys/bus/pci` exists**, otherwise the **mock** backend.

So on a macOS laptop you automatically get mock; on a Linux test station you
automatically get real. Force either way when you need to:

```bash
computetest --backend mock list                 # force mock (flag form)
COMPUTETEST_BACKEND=real computetest list        # force real (env form)
```

The `--backend` flag and `--json` are **common options**: argparse accepts them either
**before or after** the subcommand. All of these are equivalent:

```bash
computetest --json list
computetest list --json
computetest --backend mock --json diagnose
```

**Confirm which backend you got.** Every hardware-touching command prints a one-line
banner to **stderr** (never stdout, so it never pollutes `--json` output):

```
# backend: MOCK (no hardware)
```
or
```
# backend: REAL hardware
```

Because the banner is on stderr, `computetest nvme /dev/nvme0 --json | jq .` pipes clean
JSON while you still see the banner in your terminal. (The pure-math `ber` command needs
no backend and prints no banner.)

---

## 3. Command reference

Global help: `computetest -h`; per-command help: `computetest <cmd> -h`.

Subcommands: `list` · `bert` · `diagnose` · `nvme` · `gpu` · `gmsl` · `eth` · `can` ·
`plan` · `ber`.

### Exit-code contract (all commands)

| Code | Meaning |
|------|---------|
| `0` | **pass** — the DUT/target passed (or, for `list`/`ber`, the command simply succeeded) |
| `1` | **fail** — the DUT failed (a check did not meet limits) |
| `2` | **usage error** — bad/missing arguments |
| `3` | **device/target not found** — the BDF / index / interface does not exist |
| `4` | **config / IO error** — config file missing or unparseable, or an IO/runtime error |
| `5` | **capability unavailable** — feature not supported on this hardware/backend (e.g. real lane margining) |

This contract is stable and meant for scripting (e.g. a station runner branching on the
code). Exit codes are identical for human and `--json` modes.

### Common options

| Option | Default | Notes |
|--------|---------|-------|
| `--json` | off | Emit a JSON object/array to **stdout** only; human text is suppressed. Pipeable. |
| `--backend {auto,mock,real}` | `auto` | Force the hardware backend (Section 2). |

---

### `list` — enumerate PCIe devices

**Purpose:** show every PCIe function the backend sees (BDF, vendor, current link
speed×width, class code, bound driver). Your first sanity check that the board enumerated.

**Syntax:** `computetest list [--json] [--backend ...]`

**Example & output:**

```bash
$ COMPUTETEST_BACKEND=mock computetest list
# backend: MOCK (no hardware)        # (stderr)
0000:03:00.0  NVIDIA             Gen4x16  class=0x030000 drv=nvidia
0000:04:00.0  NVIDIA             Gen4x16  class=0x030000 drv=nvidia
0000:05:00.0  Samsung            Gen4x4  class=0x010802 drv=nvme
0000:06:00.0  Samsung            Gen4x4  class=0x010802 drv=nvme
0000:07:00.0  Zoox-custom(example) Gen3x8  class=0x088000 drv=zoox_custom
```

**Exit codes:** `0` always (enumeration itself does not pass/fail).

**`--json` shape:** a JSON **array** of device objects:

```json
[
  {
    "bdf": "0000:03:00.0", "vendor_id": 4318, "device_id": 8708,
    "class_code": 196608, "current_link_speed": 4, "current_link_width": 16,
    "max_link_speed": 4, "max_link_width": 16, "driver": "nvidia"
  }
]
```

---

### `bert` — run the BERT on one device

**Purpose:** run the bit-error-rate test on a single link — arm the AER status latches,
fast-poll/re-clear to count error events, accrue bits from `speed × width × time`, and
decide pass/fail against a **confidence-that-true-BER-≤-target** criterion.

**Syntax:**

```
computetest bert -d/--bdf BDF
                 [--target-ber FLOAT]   (default 1e-12)
                 [--confidence FLOAT]   (default 0.95)
                 [--max-seconds FLOAT]  (default 30.0)
                 [--engine {python,c}]  (default python)
                 [--json] [--backend ...]
```

> **`bert` defaults to a ~12 s, 1e-12 / 95% run.** At Gen4 x16 a clean link needs
> ≈ 3.0×10¹² bits to *prove* BER ≤ 1e-12 at 95% (the "3/BER" rule), which is ~12 s of
> transfer. With the default `--max-seconds 30` a healthy link finishes in ~12 s; if you
> see it "running" for ten-plus seconds, that's expected, not a hang (see Troubleshooting).
> Lower the target (`--target-ber 1e-9`) or `--max-seconds` for a quick smoke test.

**Example & output** (fast variant for the doc — `1e-9`, capped at 2 s):

```bash
$ COMPUTETEST_BACKEND=mock computetest bert -d 0000:03:00.0 --target-ber 1e-9 --max-seconds 2
# backend: MOCK (no hardware)        # (stderr)
0000:03:00.0 Gen4x16 0.0s n=3.18e+09 E=0 n=3.185e+09 | CL=0.9586 (target 0.95) | BER<=9.41e-10 | PASS
```

Read the verdict as: `E=` error events, `n=` bits transferred, `CL=` confidence reached
vs target, `BER<=` the upper bound you can now claim, then `PASS`/`FAIL`.

**Engines:** `--engine python` (default) drives the poll loop through the backend and
runs identically on mock and real. `--engine c` shells out to the compiled `c/pcie_bert`
for tight, high-rate polling on real hardware (build it first — Section 5). If the C
binary is missing, you get a clear `error:` and exit `4`.

**Exit codes:** `0` pass · `1` fail (any uncorrectable error, or ran out of time without
reaching the confidence target) · `3` BDF not found · `4` C engine missing/failed.

**`--json` shape:**

```json
{
  "bdf": "0000:03:00.0", "seconds": 0.013, "bits": 3177918276.42,
  "correctable": 0, "uncorrectable": 0, "per_correctable": {},
  "link": "Gen4 x16", "confidence": 0.9583,
  "ber_upper_bound": 9.43e-10, "uncorrectable_decode": [],
  "stuck_bits": false, "status": "pass"
}
```

---

### `diagnose` — full PCIe diagnostic

**Purpose:** the "beyond BERT" combined per-device report: link health (speed/width vs
expected/max), retrain monitoring, AER decode (which *layer* failed), the BERT, and
lane margining — with the failure evidence attached. Tells you not just *that* a link is
bad but *how*.

**Syntax:**

```
computetest diagnose [-d/--bdf BDF]      (default: all devices)
                     [--target-ber FLOAT] (default 1e-12)
                     [--no-bert]          (skip the BERT)
                     [--no-margin]        (skip lane margining)
                     [--max-seconds FLOAT] (default 30.0; BERT cap)
                     [--json] [--backend ...]
```

**Example & output** (one device, BERT capped at 2 s so it returns quickly; the failing
GPU in the mock board):

```bash
$ COMPUTETEST_BACKEND=mock computetest diagnose -d 0000:04:00.0 --max-seconds 2
# backend: MOCK (no hardware)        # (stderr)
[FAIL] 0000:04:00.0 Gen4(16GT/s) x16 -> OK
      reasons: BERT fail (BER<= 1.60e-09); lane 2 margin 0.020UI < 0.25UI
      bert: E=762 n=5.043e+11 | CL=0.0000 (target 0.95) | BER<=1.60e-09 | FAIL
      margin: 0000:04:00.0: min margin 0.020 UI across 16 lanes -> FAIL(lane 2=0.020UI<0.25UI)
```

Running `diagnose` with **no `-d`** diagnoses every enumerated device (default
`--max-seconds 30`, so a full healthy run is several minutes — the BERT dominates).
A clean device looks like:

```
[PASS] 0000:03:00.0 Gen4(16GT/s) x16 -> OK
      bert: E=0 n=2.996e+12 | CL=0.9500 (target 0.95) | BER<=1.00e-12 | PASS
      margin: 0000:03:00.0: min margin 0.393 UI across 16 lanes -> OK
```

> Note: with the strict default `--target-ber 1e-12`, narrow links (e.g. Gen4 **x4**
> NVMe) accrue bits slowly and may not *prove* 1e-12 within the time cap (you'll see
> `CL≈0.85 … FAIL` even with zero errors — it's "not proven yet", not "errored"). Give
> them more time, use `--engine c`, or relax the target for a smoke test.

**Exit codes:** `0` if **all** diagnosed devices pass, else `1`. (`3` if a `-d` BDF
doesn't exist.)

**`--json` shape:** a JSON **array** of per-device diagnostics:

```json
[
  {
    "bdf": "0000:04:00.0", "status": "fail",
    "reasons": ["BERT fail (BER<= 1.60e-09)", "lane 2 margin 0.020UI < 0.25UI"],
    "link": {"bdf": "0000:04:00.0", "speed": 4, "width": 16, "max_speed": 4,
             "max_width": 16, "speed_degraded": false, "width_degraded": false,
             "retrains": 0, "ok": true},
    "aer": {"correctable": ["BadTLP"], "uncorrectable": []},
    "bert": { "...": "same shape as `bert --json` above" },
    "margin": {"bdf": "0000:04:00.0", "min_timing_ui": 0.02, "limit_ui": 0.25,
               "ok": false, "lanes": {"0": 0.39, "2": 0.02, "...": 0.0}}
  }
]
```

---

### Interface checks: `nvme` / `gpu` / `gmsl` / `eth` / `can`

These five share a shape: one positional `target`, optional `--json`/`--backend`,
exit `0` if the target passes its limits and `1` if not (`3` if the target can't be
parsed/found — e.g. a non-integer GPU index). On the mock backend, put the string `BAD`
in the target (or use GPU index `99`) to simulate a failing unit.

| Command | Syntax | `target` is | Checks (mock pass values) |
|---------|--------|-------------|----------------------------|
| `nvme` | `computetest nvme TARGET` | device path, e.g. `/dev/nvme0` | SMART vs new-drive limits: `critical_warning==0`, `media_errors==0`, no error-log entries, `percentage_used<2`, `available_spare>=100`, `temp<=70` |
| `gpu` | `computetest gpu TARGET` | integer index, e.g. `0` | `ecc_uncorrected==0`, `temp<=85`, no thermal throttle, `replay<100`, link gen/width ≥ expected |
| `gmsl` | `computetest gmsl TARGET` | i2c link id, e.g. `1-0029` | link locked, resolution 1920×1080, frames captured > 0, no link errors |
| `eth` | `computetest eth TARGET` | interface, e.g. `eth0` | link up, speed ≥ 1000 Mb, low errors, throughput ≥ 900 Mb |
| `can` | `computetest can TARGET` | interface, e.g. `can0` | not BUS-OFF, ERROR-ACTIVE, TEC/REC < 96 |

**Examples & output:**

```bash
$ COMPUTETEST_BACKEND=mock computetest gpu 0
# backend: MOCK (no hardware)
GPU0 Mock RTX Gen4x16 temp=62C ecc_unc=0 replay=0 throttle= -> OK

$ COMPUTETEST_BACKEND=mock computetest gmsl 1-0029
GMSL 1-0029 lock=True 1920x1080 frames=5 err=0 -> OK

$ COMPUTETEST_BACKEND=mock computetest eth eth0
eth0 up=True 1000Mb rx_err=0 tx_err=0 iperf=950Mb -> OK

$ COMPUTETEST_BACKEND=mock computetest can can0
can0 state=ERROR-ACTIVE TEC=0 REC=0 -> OK
```

**`nvme --json` shape** (the others follow the same `{... per-field metrics ...,
"checks": {name: bool}, "ok": bool}` pattern):

```json
{
  "device": "/dev/nvme0", "model": "MockSSD-1TB", "serial": "MOCK0001",
  "firmware": "MK1.0",
  "smart": {"critical_warning": 0, "temperature": 41, "available_spare": 100,
            "percentage_used": 0, "media_errors": 0, "num_err_log_entries": 0,
            "data_units_written": 1234, "data_units_read": 5678, "unsafe_shutdowns": 0},
  "checks": {"critical_warning==0": true, "media_errors==0": true,
             "no_error_log_entries": true, "percentage_used<2": true,
             "available_spare>=100": true, "temp<=70": true},
  "ok": true
}
```

Per-command JSON keys: `gpu` → `index,name,link,metrics,checks,ok`; `gmsl` →
`link,locked,video_device,resolution,frames,error_count,checks,ok`; `eth` →
`iface,link_up,speed_mbps,rx_errors,tx_errors,throughput_mbps,checks,ok`; `can` →
`iface,state,tec,rec,checks,ok`.

---

### `plan` — run a full test plan from a config

**Purpose:** run coverage across **every** subsystem from one config file (PCIe
enumeration + per-device diagnostics + the five interface checks), record each result
(with measured values) into a SQLite store, and return a pass/fail report. The data-
driven station runner: *a new board revision is a new config file, not new code.*

**Syntax:**

```
computetest plan CONFIG
                 [--db PATH]      (default ":memory:" — in-memory, nothing persisted)
                 [--serial STR]   (default "DUT-DEMO")  DUT serial recorded with results
                 [--station STR]  (default "station-1") station id recorded with results
                 [--json] [--backend ...]
```

`CONFIG` is a `.json` plan or (with PyYAML installed) a `.yaml`/`.yml` plan — see
Section 4. Pass `--db results.db` to persist results for the dashboard (Section 5 of the
README / `make dashboard`).

**Example & output** (the shipped JSON plan — a fast `1e-9`, 2 s-BERT demo):

```bash
$ COMPUTETEST_BACKEND=mock computetest plan configs/example_plan.json
# backend: MOCK (no hardware)
Test plan report:
  [PASS] enum/topology: enumeration
  [PASS] pcie/0000:03:00.0: GPU:diagnose
  [FAIL] pcie/0000:04:00.0: GPU:diagnose  BERT fail (BER<= 1.60e-09); lane 2 margin 0.020UI < 0.25UI
  [PASS] pcie/0000:05:00.0: NVMe:diagnose
  [PASS] pcie/0000:06:00.0: NVMe:diagnose
  [FAIL] pcie/0000:07:00.0: CustomCard:diagnose  speed degraded (Gen3)
  [PASS] nvme//dev/nvme0: smart  /dev/nvme0 MockSSD-1TB fw=MK1.0 temp=41C used=0% media_err=0 -> OK
  [PASS] nvme//dev/nvme1: smart  /dev/nvme1 MockSSD-1TB fw=MK1.0 temp=41C used=0% media_err=0 -> OK
  [PASS] gpu/0: health  GPU0 Mock RTX Gen4x16 temp=62C ecc_unc=0 replay=0 throttle= -> OK
  [PASS] gpu/1: health  GPU1 Mock RTX Gen4x16 temp=62C ecc_unc=0 replay=0 throttle= -> OK
  [PASS] gmsl/1-0029: link+video  GMSL 1-0029 lock=True 1920x1080 frames=5 err=0 -> OK
  [PASS] ethernet/eth0: link  eth0 up=True 1000Mb rx_err=0 tx_err=0 iperf=950Mb -> OK
  [PASS] can/can0: state  can0 state=ERROR-ACTIVE TEC=0 REC=0 -> OK
  => 11 pass, 2 fail, 0 skip  [FAIL]
```

The two intentional failures demonstrate detection: one GPU link has an injected BER
(fails the BERT + margining), and the custom card trains at Gen3 instead of the expected
Gen4 (degraded). The overall exit is `1`.

> The shipped **YAML** plan (`configs/example_topology.yaml`) uses the production
> `target_ber: 1e-12` with `bert_max_s: 30`, so on the mock board the narrow x4 NVMe
> links don't *prove* 1e-12 within the cap and report `FAIL` (CL≈0.85) — same "not
> proven yet" effect described under `diagnose`. The `.json` plan is tuned for a fast,
> mostly-passing demo (`1e-9`, `bert_max_s: 2`).

**Exit codes:** `0` if every record passed, else `1`. `4` if the config file is missing
or unparseable.

**`--json` shape:** `{"report": [<record>, ...], "summary": {...}}` where each record is
`{subsystem, target, test_name, status, measured, message}` and `summary` is
`{total, passed, failed, skipped, yield}`.

---

### `ber` — BER confidence math only (no hardware)

**Purpose:** the statistics half of the BERT, standalone — answer "how many bits to prove
a target?" or "given this many errors in this many bits, what's my confidence / BER upper
bound?" No backend, no banner.

**Syntax:**

```
computetest ber [--target-ber FLOAT]  (default 1e-12)
                [--confidence FLOAT]  (default 0.95)
                [--errors INT]        (default 0)
                [--bits FLOAT]        (default: none)
                [--json]
```

Two modes:
- **`--bits` omitted** → "bits needed" planning: how many bits to reach the confidence.
- **`--bits` given** → "assess" a real measurement of `(errors, bits)`: confidence
  reached, BER upper bound, pass/continue/fail.

**Examples & output:**

```bash
$ computetest ber --target-ber 1e-12
To prove BER <= 1.0e-12 at 95% with 0 errors: 2.9957e+12 bits (~11.9s at Gen4 x16)

$ computetest ber --target-ber 1e-12 --bits 3e12 --errors 0
E=0 n=3.000e+12 | CL=0.9502 (target 0.95) | BER<=9.99e-13 | PASS
```

**Exit codes:** `0` (this is a calculator; it does not pass/fail a DUT).

**`--json` shape:**

- planning mode (`--bits` omitted):
  ```json
  {"target_ber": 1e-12, "confidence": 0.95, "errors": 0, "bits_needed": 2995732273553.99}
  ```
- assess mode (`--bits` given): the full verdict
  `{errors, bits, target_ber, confidence_target, confidence_reached, ber_upper,
  bits_needed, status}`.

---

## 4. Writing a test plan / topology config

A plan declares **what a good board should look like** (which devices, at which
speed/width) plus the list of interfaces to check. The toolkit compares reality to it.
**A new board revision is a new file like this — not new code.** JSON and YAML are
equivalent (YAML needs PyYAML; numbers like `0x10DE` may be written in hex in YAML, or as
decimals in JSON).

### `configs/example_plan.json` — annotated

```jsonc
{
  "target_ber": 1e-9,          // BER target for every per-device BERT (demo uses 1e-9 for speed; prod 1e-12)
  "confidence": 0.95,          // confidence level the BERT must reach to PASS
  "bert_max_s": 2.0,           // per-device BERT time cap, seconds (demo: short; prod: ~30)
  "watch_retrains_s": 0.3,     // how long to watch the link-training bit for retrain events
  "devices": [                 // PCIe expectations — each entry is matched against enumerated devices
    {"name": "GPU",            //   label used in the report
     "match": {"vendor_id": 4318, "class_code": 196608}, // match criteria: any of vendor_id/device_id/class_code/bdf
     "count": 2,               //   how many devices must match (enumeration fails if fewer found)
     "expected_speed": 4,      //   expected link gen (4 = Gen4); a slower trained link => "degraded"
     "expected_width": 16,     //   expected lane width
     "min_margin_ui": 0.25},   //   per-lane margining pass limit, in UI
    {"name": "NVMe", "match": {"class_code": 67586},
     "count": 2, "expected_speed": 4, "expected_width": 4, "min_margin_ui": 0.25},
    {"name": "CustomCard", "match": {"vendor_id": 6966}, // 6966 = 0x1B36 (the example vendor)
     "count": 1, "expected_speed": 4, "expected_width": 8} // expects Gen4 but mock trains Gen3 => flagged
  ],
  "nvme":     ["/dev/nvme0", "/dev/nvme1"], // NVMe SMART checks (device paths)
  "gpus":     [0, 1],                       // GPU health checks (indices)
  "gmsl":     ["1-0029"],                   // GMSL link+video checks (i2c link ids)
  "ethernet": ["eth0"],                     // Ethernet link checks (interfaces)
  "can":      ["can0"]                      // CAN state checks (interfaces)
}
```

(JSON has no comments; the `//` above are explanatory only — keep the real file plain.)

### `configs/example_topology.yaml` — annotated (same schema, YAML form)

```yaml
target_ber: 1.0e-12      # production target; the demo .json overrides to 1e-9 for speed
confidence: 0.95
bert_max_s: 30           # per-device BERT cap (prod value; longer than the demo)
watch_retrains_s: 0.3

# PCIe devices the board must present, matched by vendor/class (or an exact bdf).
devices:
  - name: GPU
    match: {vendor_id: 0x10DE, class_code: 0x030000}   # hex literals are fine in YAML
    count: 2
    expected_speed: 4        # Gen4
    expected_width: 16
    min_margin_ui: 0.25
  - name: NVMe
    match: {class_code: 0x010802}
    count: 2
    expected_speed: 4
    expected_width: 4
    min_margin_ui: 0.25
  - name: CustomCard
    match: {vendor_id: 0x1B36}
    count: 1
    expected_speed: 4        # this card trains at Gen3 in the mock -> flagged degraded
    expected_width: 8

# Interface checks (keyed by device path / index / interface name).
nvme:     ["/dev/nvme0", "/dev/nvme1"]
gpus:     [0, 1]
gmsl:     ["1-0029"]
ethernet: ["eth0"]
can:      ["can0"]
```

**Field reference (per `devices[]` entry):**

| Field | Meaning |
|-------|---------|
| `name` | Label shown in the report; groups matched BDFs. |
| `match` | Any of `bdf`, `vendor_id`, `device_id`, `class_code`. A device matches if **all** given keys match. Hex in YAML (`0x10DE`) or decimal in JSON (`4318`). |
| `count` | Required number of matching devices; enumeration **fails** (reports MISSING) if fewer are present. Default 1. |
| `expected_speed` | Expected link gen code (1=Gen1 … 6=Gen6). A device trained below this is flagged **speed degraded**. Omit to compare against the device's own max. |
| `expected_width` | Expected lane width (e.g. 16). Below this is **width degraded**. |
| `min_margin_ui` | Per-lane lane-margining pass limit in UI (default 0.25). |

Top-level `target_ber` / `confidence` / `bert_max_s` / `watch_retrains_s` set the BERT and
retrain-soak parameters used for every device. The interface lists (`nvme`, `gpus`,
`gmsl`, `ethernet`, `can`) drive the corresponding checks; omit a list to skip that
subsystem.

---

## 5. Running on a real Linux test station

On a Linux box with `/sys/bus/pci`, the backend auto-selects **real** hardware. The CLI
then reads real PCIe sysfs/config space and wraps the standard vendor/distro tools.

### Prerequisite checklist

Install the tool behind each interface check; the check raises a clear `RuntimeError`
(exit `4`) if its tool is missing.

| Capability | Tool(s) needed | Debian/Ubuntu package | Quick check |
|------------|----------------|-----------------------|-------------|
| NVMe SMART/identify | `nvme` | `nvme-cli` | `which nvme` |
| GPU health | `nvidia-smi` (+ `dcgmi` for `dcgmi diag`) | NVIDIA driver; `datacenter-gpu-manager` for DCGM | `which nvidia-smi` |
| Ethernet link/stats | `ethtool`, `ip` | `ethtool`, `iproute2` | `which ethtool ip` |
| GMSL video capture | `v4l2-ctl` | `v4l-utils` | `which v4l2-ctl` |
| I²C (GMSL link/error regs) | `i2cdetect`/sysfs | `i2c-tools` | `which i2cdetect` |
| CAN state | `ip` (+ `candump` etc.) | `can-utils`, `iproute2` | `which ip candump` |
| C BERT engine build | `cc`/`gcc`, `make` | `build-essential` | `which cc make` |

One-liner to check them all at once:

```bash
for t in nvme nvidia-smi ethtool ip v4l2-ctl i2cdetect candump cc make; do \
  printf '%-12s ' "$t"; command -v "$t" || echo "MISSING"; done
```

### What needs root, and why

- **PCIe config-space writes** (the BERT's AER arm/clear) require root: both the C engine
  and `RealBackend.write_config` open `/sys/bus/pci/devices/<bdf>/config` with `O_RDWR`,
  which needs `CAP_SYS_ADMIN`. So `bert`/`diagnose`/`plan` against real hardware run under
  `sudo`. *Reads* (enumeration, link status, SMART, etc.) generally do not need root.
- **Some interface tools** want privilege of their own (e.g. `nvme`, configuring a CAN
  interface). Run the station runner as root in practice.

### Build & run the C BERT engine

The C engine gives tight, high-rate polling for real hardware (errors faster than a
Python loop can clear-and-recount). It is **Linux-only at runtime** (reads `/sys/bus/pci`)
but compiles anywhere (handy for development on macOS).

```bash
make -C c                                  # builds c/pcie_bert (cc -O2 -Wall -Wextra -std=c11)
sudo ./c/pcie_bert -d 0000:03:00.0 -t 12 --json
```

`-t` is the run duration in seconds (default 12 — the ~1e-12/95% window at Gen4 x16);
`--json` prints a record the Python layer (`bert --engine c`) consumes. The engine's own
exit codes: `0` clean, `3` an uncorrectable error was seen (still valid data), `1`/`2`
for open/usage errors. To use it through the CLI: `sudo computetest bert -d <bdf>
--engine c` (it shells out to `c/pcie_bert`).

### The dashboard (fleet view)

```bash
pip install fastapi uvicorn
cd toolkit && make dashboard          # COMPUTETEST_DB=results.db uvicorn dashboard.app:app --reload
# open http://127.0.0.1:8000
```

Point stations at a shared DB (`computetest plan ... --db results.db --station st-3
--serial SNxxxx`); the dashboard shows per-station heartbeats, fleet yield, and the
lowest-yielding tests.

---

## 6. Safety

The toolkit is conservative by design. The defaults touch no data and barely touch the
link.

- **Read-only by default.** Enumeration, link status, AER *reads*, SMART, GPU/Eth/CAN/GMSL
  telemetry — all read-only.
- **The one write path is the BERT's AER arm/clear.** To *count* PCIe errors you must
  clear the AER status latches and re-read so each new error can re-latch. The clear is a
  **write-1-to-clear of only the bits that are already set** (read status, write that same
  value back): it clears exactly those event bits and touches nothing else. This is the
  only thing the tool writes to a device. A bit that refuses to clear is reported as
  *stuck* rather than retried, so a sticky fault can't inflate the count.
- **Destructive NVMe operations are gated.** NVMe `format` / secure-erase and `fio`
  writes destroy data; those paths require explicit confirmation and must never run
  against a mounted/boot drive. The shipped `nvme` check is SMART/identify only
  (read-only) plus an optional non-destructive device self-test.
- **Lane margining and the eq-preset sweep perturb a *live* link.** Receiver lane
  margining steps the sampling point on a running link, and the equalization sweep changes
  TX presets and retrains — both depend on uneven kernel/vendor support. They are
  therefore **mock-only until validated on your hardware**: on the real backend
  `margining._real_margin_lane` and `characterize_equalization` raise
  `NotImplementedError`, which the CLI maps to **exit `5` (capability unavailable)**. The
  mock paths work everywhere for development and demos. Wire the real path to your
  validated kernel/vendor margining flow before enabling it on a station.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `dashboard needs FastAPI: pip install fastapi uvicorn` | FastAPI/uvicorn not installed | `pip install fastapi uvicorn` (or `pip install -e .[dashboard]`), then `make dashboard` |
| `PyYAML not installed; use a .json config or 'pip install pyyaml'` | Loading a `.yaml`/`.yml` plan without PyYAML | `pip install pyyaml` (or `.[yaml]`), or use the equivalent `.json` plan (e.g. `configs/example_plan.json`) |
| `bert`/`diagnose` "seems hung" for ~12 s | Expected: the default 1e-12 / 95% run needs ≈ 3×10¹² bits ≈ 12 s at Gen4 x16 to prove the target | Wait; or smoke-test with `--target-ber 1e-9` and/or `--max-seconds`. Narrow (x4) links take longer to accrue bits. |
| `diagnose`/`plan` reports `FAIL` with **zero errors** and `CL≈0.85` | Not "errored" — the BERT ran out of bits/time before *proving* the strict target (1e-12) on a narrow link | Increase `--max-seconds`/`bert_max_s`, use `--engine c`, or lower `target_ber` for a quick check |
| `error: not available on this backend/hardware: Real lane margining needs per-hardware validation…` (exit `5`) | Real lane margining / eq sweep is intentionally guarded (mock-only) | Run with `--backend mock` (or on a laptop), or wire `margining.py` to your validated hardware path. Use `--no-margin` on `diagnose` to skip. |
| `error: device/target not found: …` (exit `3`) | BDF/index/interface doesn't exist (typo, or wrong backend) | Run `computetest list` to see real BDFs; check you're on the intended backend (banner on stderr) |
| `error: config/IO: [Errno 2] No such file or directory: …` (exit `4`) | Plan/config path is wrong or unreadable | Check the path; run from `toolkit/` so `configs/…` resolves |
| `error: C engine not found: c/pcie_bert (build it with: make -C c)` (exit `4`) | Used `--engine c` without building it | `make -C c`, then re-run (with `sudo` on real hardware) |
| `error: no AER capability on <bdf>` (from the C engine) | That function has no AER extended capability | Target a function that has AER (most endpoints/switches do); not all functions expose it |
| `open … config: Permission denied (need root for config writes)` | Real BERT without root | Run under `sudo` (config-space writes need `CAP_SYS_ADMIN`) |
| `nvme-cli not found` / `ethtool not found` / `nvidia-smi not found` (exit `4`) | The interface tool isn't installed on the station | Install it (Section 5 table), or force `--backend mock` to develop without hardware |
| Banner says `REAL hardware` on a laptop, or you want to force mock/real | Auto-selection saw (or didn't see) `/sys/bus/pci` | Force it: `--backend mock` / `--backend real`, or `COMPUTETEST_BACKEND=mock|real` |
| `incomplete-gamma … did not converge … install scipy for this regime` | Pure-stdlib BER math hit the millions-of-errors regime (unit is failing anyway) | `pip install scipy` for that regime; normally the fallback is exact |

---

## Roadmap (not yet in the CLI)

Some capabilities discussed in planning are **not present in the current CLI** and are
intentionally **not documented above** as if they existed. If you're looking for them:

- An operator-friendly `station` mode (guided, repeated DUT runs against a fixed plan).
- CSV export of results (today: query the SQLite DB / use `plan --json`, or the dashboard).
- Config-driven pass/fail *limits* for the interface checks (today the SMART/GPU/etc.
  limits are constants in code; PCIe expectations *are* already config-driven via the plan).

Until those land, use `plan` with a per-board config plus `--db`/`--json` and the
dashboard. This guide documents the CLI exactly as it ships today.
