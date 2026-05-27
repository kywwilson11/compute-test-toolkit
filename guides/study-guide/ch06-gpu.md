This is the GPU chapter. A GPU on a Zoox compute board is two things at once: the most
expensive, hottest, highest-power part on the assembly, and **a PCIe endpoint first.**
Hold both ideas. Most of what you will chase on a GPU — a link sitting at Gen3 when it
should be Gen4, a climbing replay count, a unit that "fell off the bus" under thermal
load — is a PCIe / power / thermal problem that the GPU happens to report through its own
rich telemetry. The rest is genuinely GPU-specific: Error-Correcting Code (ECC) on a huge memory array, row
remapping, XID error codes, and a thermal-under-load behavior that idle tells you nothing
about.

This chapter assumes the **PCIe chapter** for the link layer (Link Training and Status State Machine (LTSSM), Advanced Error Reporting (AER), lane margining,
the arm/stress/read discipline) and the **Memory chapter** for the host-Dynamic Random-Access Memory (DRAM) Error Detection and Correction (EDAC) story —
GPU ECC is the on-package analog of both. What you get here: the architecture you actually
need (not a graphics-programming tour), ECC in real depth, the XID table with an action
per code, the throttle-reasons bitmask decoded, NVLink, the health-check command set
(`nvidia-smi -q`, DCGM), failure signatures mapped to root cause, and the manufacturing
flows — what is a screen versus what is an Return Merchandise Authorization (RMA). The companion code is
`toolkit/src/computetest/gpu.py`; read a section here, then read the function that does it.

---

## 1. GPU Architecture, the Parts That Matter for Test

You are not writing CUDA kernels. But you cannot test a part you cannot reason about, and
the failure signatures map directly onto the architecture — a double-bit ECC error lives in
a specific memory array, a thermal slowdown throttles specific clock domains, an NVLink
error is a specific SerDes. So: the architecture, filtered to what changes how you test.

### 1.1 The compute hierarchy

NVIDIA's datacenter and automotive parts (A100, H100, L40/L40S, the Orin System-on-Chip (SoC)'s integrated
GPU, plus whatever Zoox's roadmap lands on) all share the same hierarchy:

```text
GPU die
  +-- GPC  (Graphics Processing Cluster)        a few per die
        +-- TPC  (Texture Processing Cluster)
              +-- SM  (Streaming Multiprocessor)  the core compute unit
                    +-- CUDA cores (FP32/INT)     -- ALUs
                    +-- Tensor cores              -- matrix-multiply units (the AI workhorse)
                    +-- register file, L1 cache / shared memory  (per-SM)
  +-- L2 cache  (shared across all SMs)
  +-- Memory controllers  --> HBM or GDDR stacks
  +-- Copy/DMA engines, NVENC/NVDEC (video), NVLink, PCIe interface
```

What matters for test:

- **SMs are the redundancy and binning unit.** A die has dozens of SMs; vendors fuse off
  defective ones and sell the result as a lower SKU. You will not usually test at SM
  granularity, but understand that "the same chip" can ship with different SM counts, and
  a compute stress test exercises *all* enabled SMs — which is how you surface a marginal
  one that a light load misses.
- **Tensor cores dominate the perception workload.** Zoox's stack is inference-heavy, so
  the parts that get hot and draw power under real load are the tensor cores. A thermal
  soak that only loads FP32 CUDA cores under-stresses the part; `gpu-burn --tensor` and
  DCGM's targeted-stress plugins exist precisely to drive the tensor path.
- **L2 and on-chip RAMs have ECC too**, not just the external memory. When you read ECC
  counters you are reading errors aggregated across HBM/GDDR *and* the internal SRAMs.

### 1.2 Memory: HBM vs GDDR, and why you care

The GPU's memory is the part most likely to throw a hard error in test, so know which kind
you are looking at:

| | **HBM2e / HBM3** | **GDDR6 / GDDR6X** |
|---|---|---|
| Where | A100, H100, high-end datacenter | L40, consumer, many automotive parts |
| Construction | Stacked DRAM dies on a silicon interposer, in-package | Discrete chips around the GPU on the PCB |
| Bandwidth | Very high (TB/s), wide bus (1024-bit+ per stack) | High (hundreds of GB/s), narrower bus |
| ECC | Native, side-band ECC on extra dies | Often **inline/soft ECC** — capacity carved from the array |
| Test implication | A bad stack is unrepairable in-package -> RMA | Can sometimes be a single discrete chip |

The ECC distinction bites you. On older GDDR parts, enabling ECC **reduces usable memory and
bandwidth** because the ECC bits are carved out of the same array (you will see total memory
drop when ECC is on). HBM parts carry ECC on dedicated dies, so enabling it is "free." For a
datacenter/AV part you run **with ECC enabled, always** — turning it off to win a benchmark
is exactly the kind of thing that must never happen on a safety-relevant box. Verify it is
on (§3.4).

### 1.3 How the GPU attaches: PCIe vs NVLink, and the driver stack

A GPU connects to the host over **PCIe Gen4/Gen5 x16** — that is the link your PCIe chapter
tools margin and Bit Error Rate Test (BERT). Multi-GPU boards may *additionally* wire GPUs to each other (or to an
NVSwitch) over **NVLink**, a separate, faster, NVIDIA-proprietary GPU-to-GPU SerDes. Keep
them straight:

- **PCIe** is host↔GPU: how the CPU feeds data in and reads results out. Every GPU has it.
  This is the link that matters for *your* per-GPU qualification.
- **NVLink** is GPU↔GPU: peer bandwidth for multi-GPU workloads (model/tensor parallelism).
  NVLink 3 (A100) ≈ 600 GB/s, NVLink 4 (H100) ≈ 900 GB/s bidirectional — far above PCIe. It
  has its **own** link-up, CRC, replay, and recovery state to verify, summarized by XID 74
  (§5) and read with `nvidia-smi nvlink` / DCGM (§6.3).

The software stack you are testing through, bottom to top:

```text
hardware
  NVIDIA kernel driver (nvidia.ko, nvidia-uvm.ko, nvidia-drm.ko)
     +-- emits "NVRM: Xid (...)" lines to dmesg          <- your XID source (Section 5)
  GSP firmware (GPU System Processor) -- on-GPU microcontroller running much of the
                                          driver logic; XID 119/120 are GSP faults
  NVML (libnvidia-ml) -- the C API that backs both tools below
     +-- nvidia-smi   -- human/CLI front end
     +-- DCGM         -- the daemon + dcgmi for fleet/automated health (Section 6)
  CUDA runtime/toolkit -- what gpu-burn, cuda-memtest, dcgmi diag stress with
```

The two facts to carry: **XID errors come from the kernel driver into `dmesg`** (so your
health check scrapes the kernel log), and **`nvidia-smi`/DCGM both sit on NVML** (so the
fields you query are NVML fields — which is why scripting against `--query-gpu` is stable
and parsing free-text `-q` output is fragile; §6.1).

---

## 2. The ECC Model in Depth

Datacenter GPUs run **ECC** over their external memory and internal RAMs. This is the GPU's
error-detection conscience, and getting the gating right is the single most common place a
GPU test is written wrong. Treat this section as the GPU analog of the AER chapter: same
philosophy (arm → stress → read; corrected vs uncorrected; volatile vs lifetime), different
registers.

### 2.1 Single-bit (SBE) vs double-bit (DBE)

ECC on these parts is SECDED-class (Single-Error-Correct, Double-Error-Detect), with the
high-end memory controllers adding stronger codes:

- **Corrected error — single-bit (SBE).** A single flipped bit the ECC fixed. Data is fine.
  The exact analog of a PCIe *correctable* error. A handful over a long soak can be normal
  (cosmic rays, a marginal cell); a **high or climbing** SBE rate is a degrading-memory
  finding, and NVIDIA raises **XID 92** when the rate crosses a threshold.
- **Uncorrectable error — double-bit (DBE).** Two (or more) bits flipped — detected but
  *not* correctable. Data is corrupt. The analog of a PCIe *uncorrectable* error, and it
  raises **XID 48** (plus the contained/uncontained XIDs 94/95 on newer parts). **Any DBE
  on a unit under test is a fail.**

### 2.2 Volatile vs aggregate — the gating gotcha

This is the distinction that breaks naive tools. NVIDIA keeps **two** sets of ECC counters:

| Counter set | Resets on | Lives in | What it tells you |
|---|---|---|---|
| **Volatile** | reboot / driver reload / `nvidia-smi -r` | RAM | Errors since the last reset -- i.e. during *this* test |
| **Aggregate** | never (lifetime) | InfoROM on the GPU | The part's whole-life error history |

The rule:

> **Gate the manufacturing pass on VOLATILE uncorrected == 0. Treat a nonzero AGGREGATE as
> investigate / RMA-history, NOT an automatic fail.**

Why this matters: a perfectly good GPU that took one DBE years ago, remapped the row, and has
run clean ever since carries that DBE in its **aggregate** count *forever*. If your test
queries `ecc.errors.uncorrected.aggregate.total` and fails on nonzero — a real and common bug
— you scrap good, already-healed parts on their lifetime history. Conversely, gating only on
aggregate can *miss* a fresh error if the part was reset between insertion and test.

The correct flow mirrors AER's arm/stress/read:

1. **Baseline / arm** — read (or reset) the volatile counters so you are counting *your*
   stress window, not boot + enumeration + the previous unit.
2. **Stress** — `gpu-burn` / `dcgmi diag -r 3` for the soak interval.
3. **Read volatile** — `ecc.errors.uncorrected.volatile.total` must be 0; log volatile SBE
   as a trend metric.
4. **Separately log aggregate** — for history / re-stock detection, never as the pass gate.

The toolkit encodes exactly this split — `_apply_limits()` gates on
`ecc_uncorrected_volatile == 0`, while `_history()` records `ecc_uncorrected_aggregate` and
remapped-row count as **history flags, not failures** (see the module docstring in
`gpu.py`).

A real `nvidia-smi -q -d ECC` block makes the two-counter structure obvious — note the
parallel **Volatile** and **Aggregate** subtrees, each with its own SBE/DBE split:

```text
    Ecc Mode
        Current                           : Enabled
        Pending                           : Enabled
    ECC Errors
        Volatile
            SRAM Correctable              : 0
            SRAM Uncorrectable            : 0
            DRAM Correctable              : 0
            DRAM Uncorrectable            : 0
        Aggregate
            SRAM Correctable              : 0
            SRAM Uncorrectable            : 0
            DRAM Correctable              : 14
            DRAM Uncorrectable            : 1
```

This exact part **passes**: volatile uncorrectable is 0 (nothing happened during your
window), even though aggregate DRAM uncorrectable is 1 — one lifetime DBE it took, remapped,
and has run clean through since. A tool that gates on `ecc.errors.uncorrected.aggregate.total`
scraps this good part; the correct tool gates on the **Volatile** subtree and logs the
**Aggregate** subtree as genealogy. Two field-name details that trip up parsers: the
counters are split **SRAM vs DRAM** (internal RAMs vs framebuffer) — the CSV
`--query-gpu` `.total` fields sum them, so use those when you just want a gate; and the
exact label is "Uncorrectable" in the `-q` tree but "uncorrected" in the
`--query-gpu` field name (`ecc.errors.uncorrected.volatile.total`). Match the right one.

### 2.3 Row remapping, retired pages, and remap-pending

When the GPU takes an uncorrectable error (or enough correctables on one row), it does not
just log it — it **retires the bad memory** so it is never used again. Two mechanisms by era:

- **Page retirement (Volta and older):** the driver retires the offending 64 KiB page,
  permanently blacklisting it. Read with `nvidia-smi -q -d PAGE_RETIREMENT` /
  `retired_pages.*`. Retirements due to DBE are the serious kind; due to SBE-threshold are
  the softer kind.
- **Row remapping (Ampere and newer — A100/H100):** finer-grained. The GPU has **spare
  rows** per memory bank and remaps a failing row to a spare, in hardware, so capacity is
  preserved. Read with `nvidia-smi -q -d ROW_REMAPPER`. The states you must parse:

| Field | Meaning | Test action |
|---|---|---|
| Correctable / Uncorrectable **remap count** | Rows already remapped (lifetime) | History flag, not a fail by itself |
| **Remap Pending : Yes** | A remap is queued but needs a **GPU reset** to take effect | The part is in a degraded state -> reset and re-verify; on a new unit, a finding |
| **Remap Failure Occurred : Yes** | A remap was attempted and **failed** -> no spare row, or the remap mechanism itself failed | **Hard fail / RMA.** The part can no longer protect itself |

**When a remap fails** is the case that must always fail a unit: it means either the spare
rows for that bank are exhausted (the memory is badly degraded) or the remapping hardware is
broken. Either way the GPU has lost its ability to heal, and a future DBE will be
unrecoverable. **XID 64** is the kernel signature of this; **XID 63** is the benign sibling
(remap *succeeded*, reset pending). The toolkit's `_apply_limits()` fails on
`row_remap_failure` *and* `row_remap_pending`, and `_query_row_remap()` parses the three
fields above out of `nvidia-smi -q -d ROW_REMAPPER`.

What actually trips the remap-failure flag is worth knowing, because it tells you *how
degraded* a part is and it is also NVIDIA's stated RMA criterion. Per NVIDIA's GPU memory
error management docs, every DRAM bank ships with a fixed pool of spare rows, and a
remapping **failure** is raised when any of these happens:

- a remap is attempted for an uncorrectable error on a bank that **already has 8 rows
  remapped for uncorrectable errors** (the per-bank spare pool for the UCE class is used up);
- a remap is attempted on a **row that was already remapped** (the remap did not stick);
- **512 total uncorrectable-error remappings** have accumulated across the GPU.

Any one of those sets `Remap Failure Occurred : Yes`, and NVIDIA's policy is that a part
with the row-remap-failure flag set — once **confirmed by the NVIDIA Field Diagnostic** —
qualifies for RMA. That is the clean dividing line for the floor: a remap that *succeeded*
(pending reset) is a heal; a remap that *failed* is an RMA. Note the contrast with the
lifetime *count*: a handful of successfully remapped rows is just history, but the failure
flag means the heal machinery itself is out of headroom or broken.

A real `nvidia-smi -q -d ROW_REMAPPER` block looks like this (the fields the parser keys on):

```text
    Remapped Rows
        Correctable Error                 : 0
        Uncorrectable Error               : 2
        Pending                           : No
        Remapping Failure Occurred        : No
        Bank Remap Availability Histogram
            Max                           : 95 bank(s)
            High                          : 1 bank(s)
            Partial                       : 0 bank(s)
            Low                           : 0 bank(s)
            None                          : 0 bank(s)
```

Two remapped uncorrectable rows with `Pending : No` and `Failure Occurred : No` is a part
that took two UCEs, healed both, and reset clean — a **history flag, not a fail**. The
**Bank Remap Availability Histogram** is the underused field: it buckets banks by how many
spare rows remain (Max = full spare pool, down to None = exhausted). A bank in **Low** or
**None** is a degrading-part early warning even when the failure flag is still `No` — log it
as a trend metric, the same way you trend SBE rate.

> **The one-line ECC gate.** *Fail on: volatile DBE > 0, remap-failure, remap-pending, or a
> critical XID. Log (do not fail) on: aggregate DBE, lifetime remap count.* That single rule
> is what separates a correct GPU test from one that either passes corrupt parts or scraps
> good ones.

---

## 3. Clocks, Power, Thermal, and the Throttle Bitmask

A GPU at idle tells you almost nothing. The defects you are catching at module test — bad
heatsink mount, wrong or insufficient thermal interface material, a dead fan, paste
pump-out, a marginal power stage — only appear **under sustained compute load**. So the test
is the load, and the instrument is the set of `nvidia-smi` telemetry fields plus the throttle
bitmask.

### 3.1 The fields you read (and their limits)

```text
nvidia-smi --query-gpu=index,name,temperature.gpu,temperature.memory,\
power.draw,power.limit,enforced.power.limit,\
clocks.current.sm,clocks.max.sm,clocks.current.memory,\
utilization.gpu,pstate,clocks_event_reasons.active --format=csv
```

| Field | What it is | Manufacturing expectation |
|---|---|---|
| `temperature.gpu` | GPU core temp (degC) | Below the slowdown threshold under load (commonly < 83-87 degC; part-specific) |
| `temperature.memory` | HBM/GDDR temp | Below its own limit (HBM throttles ~95 degC) |
| `power.draw` vs `power.limit` | Live draw vs cap | Should reach near TDP under burn, not exceed `enforced.power.limit` |
| `clocks.current.sm` vs `clocks.max.sm` | SM clock vs P0 boost | Stays near P0 under load -- a sustained drop means throttling |
| `pstate` | Performance state (P0 = max, P8 = idle) | P0 under full load; if it sags to P2/P3 while hot, investigate why |
| `clocks_event_reasons.active` | **Throttle bitmask** (hex) | Decode it -- this is the *why* behind any clock drop (§3.2) |

Note the field rename: older drivers call it `clocks_throttle_reasons.active`; newer ones
`clocks_event_reasons.active`. Query the one your driver exposes (the toolkit queries
`clocks_throttle_reasons.active`). Either way the bits are identical.

### 3.2 Decoding the throttle bitmask

`clocks_event_reasons.active` is a **bitmask** — multiple reasons can be set at once. This is
the highest-signal field for any "why are clocks low / why is it slow" question, and reading
the raw hex correctly is the skill. The NVML bit definitions:

| Bit (hex) | Reason | Benign or a finding? |
|---|---|---|
| `0x0001` | GPU Idle | Benign -- nothing running |
| `0x0002` | Applications Clocks Setting | Benign -- clocks were set by the operator/app |
| `0x0004` | **SW Power Cap** | Driver capping to stay under the **power limit**. Often normal at full load; suspicious if the limit is set too low |
| `0x0008` | **HW Slowdown** | **Finding.** Hardware forced a slowdown -- thermal *or* power-brake *or* a failing voltage regulator. Look at the more specific bits below |
| `0x0010` | Sync Boost | Benign -- clocks synced across a group of GPUs |
| `0x0020` | **SW Thermal Slowdown** | **Finding.** Driver throttled because temp hit the soft limit -- a cooling problem |
| `0x0040` | **HW Thermal Slowdown** | **Serious finding.** Hardware throttled at the *hard* thermal limit (TLIMIT). Cooling is badly inadequate or a sensor/mount problem |
| `0x0080` | **HW Power Brake Slowdown** | **Finding.** An external power-brake signal (EDPp / `PWR_BRAKE#`) fired -- the PSU/VRM asserted it. Power-delivery problem |
| `0x0100` | Display Clock Setting | Benign (not relevant on headless compute) |

The toolkit's `_THROTTLE_BAD` map is exactly the four that matter for a fail:
`{0x8: hw_slowdown, 0x20: sw_thermal, 0x40: hw_thermal, 0x80: hw_power_brake}` — and
`_apply_limits()` fails the unit if **any** of them is set (`no_bad_throttle`). The benign
bits (idle, app-clocks, sync-boost) are deliberately *not* in that set, so a GPU that is
merely idle or operator-clocked does not false-fail.

How to read it by hand: take the hex value, e.g. `0x0000000000000060`. Mask off the bits:
`0x60 = 0x40 | 0x20` → **HW Thermal Slowdown + SW Thermal Slowdown** are both set. That is a
unit cooking under load — a heatsink/TIM/fan problem, not a silicon defect. Contrast
`0x0004` alone (SW Power Cap) under a `gpu-burn` at a deliberately low power limit, which is
expected and benign.

### 3.3 Power and clock limits

```bash
nvidia-smi -q -d POWER                 # min/max/default/enforced power limits, current draw
nvidia-smi -pl 300 -i 0                # set GPU 0 power limit to 300 W (within min/max)
nvidia-smi -q -d CLOCK                 # current/max/default clocks per domain
nvidia-smi -lgc 1400,1400 -i 0         # lock SM clock to 1400 MHz (repeatable test point)
nvidia-smi -rgc -i 0                   # reset locked clocks back to default
nvidia-smi -pm 1                       # PERSISTENCE MODE on -- keeps the driver resident
```

Two operational notes that save you time on the floor:

- **Persistence mode (`-pm 1`) belongs in every test station's setup.** Without it, the
  driver unloads when no client is using the GPU, so the *first* query after idle pays a
  multi-second init penalty and your enumeration/timing looks flaky. Turn it on once at
  station start. (On the newest drivers this is the `nvidia-persistenced` daemon rather
  than the deprecated flag — same effect.)
- **Locking clocks (`-lgc`) makes a power/thermal measurement repeatable.** For a
  characterization or a station-to-station correlation, pinning the SM clock removes
  boost-algorithm variance so two runs are comparable. For a normal go/no-go soak you leave
  boost free and watch the throttle bits instead.

### 3.4 Confirm ECC is enabled (it is part of the power/clock setup)

```bash
nvidia-smi -q -d ECC | grep -A2 "Ecc Mode"     # Current: Enabled / Pending: Enabled
nvidia-smi -e 1 -i 0                            # ENABLE ECC on GPU 0 -- needs a GPU reset to apply
```

ECC mode is per-GPU persistent state in the InfoROM, and toggling it requires a reset (the
`Pending` line shows the value that takes effect after reset). A datacenter/AV part **must**
run with ECC enabled; a station check that confirms `Ecc Mode: Current: Enabled` belongs in
your baseline, because a part that shipped (or got flashed) with ECC off has no memory
protection at all.

---

## 4. The Manufacturing Test Sequence

Now assemble the pieces into the flow you actually run at module test. The shape is the same
as every other interface in this guide: **enumerate → verify link → baseline counters →
stress hot → re-read counters → decode → verdict.**

```bash
# 1. ENUMERATE -- correct GPU count present?
gpu_count=$(nvidia-smi -L | wc -l)
[[ "$gpu_count" -eq 4 ]] || fail "Expected 4 GPUs, found $gpu_count"

# 2. PCIe LINK per GPU -- right gen and width (this is a PCIe test surfaced via nvidia-smi)
nvidia-smi -q -d PCIE | grep -E "Link (Gen|Width)|Replay"
#   Link Gen   Max: 4  Current: 4      (Gen4)  -- a new board at Gen3 is your first SI finding
#   Link Width Max: 16x Current: 16x            -- x8 = a dead lane / bifurcation problem
#   Replay Count: 0                             -- nonzero/climbing = SI problem (see PCIe chapter)

# 3. BASELINE -- ECC enabled, volatile counters armed, no pre-existing critical state
nvidia-smi -q -d ECC | grep -E "Ecc Mode|Volatile" 
nvidia-smi -q -d ROW_REMAPPER | grep -E "Pending|Failure"   # must be No / No
dmesg --since "$(uptime -s)" | grep "NVRM: Xid" || true      # no pre-existing XIDs

# 4. STRESS -- real compute + memory load, hot (the actual test)
dcgmi diag -r 3            # structured per-subsystem pass/fail (memory, SM stress, PCIe, power)
#   and/or:  gpu-burn -tc 300     # 5 min tensor-core max-thermal soak

# 5. MONITOR during stress -- watch temp, power, and the throttle bitmask
nvidia-smi dmon -s pucvm -d 1     # power+temp / util / clock / violations / mem, 1 s interval
#   (add 'e' for ecc+pcie-replay; 't' is PCIe throughput, NOT temperature -- temp rides on 'p')
#   temp must stay under the slowdown threshold; throttle reasons must decode to benign-only

# 6. POST-STRESS verdict
#   - volatile uncorrected ECC (DBE) == 0          (Section 2)
#   - row-remap pending == No, failure == No        (Section 2.3)
#   - PCIe replay count not climbing                 (PCIe chapter)
#   - no critical XID in dmesg during the window     (Section 5)
#   - throttle bitmask had no HW/SW-thermal/power-brake bit set (Section 3.2)
#   - GPU still enumerable (did NOT fall off the bus -> XID 79)
```

The `nvidia-smi dmon` field codes are worth memorizing because it is your live floor view,
and one of them is a classic trap. The official `-s` metric groups are:

| `-s` flag | What it actually selects (per the nvidia-smi docs) |
|---|---|
| `p` | **Power (W) AND GPU/memory temperature (degC)** -- temp lives here, not under `t` |
| `u` | Utilization (SM, memory, encoder, decoder, JPEG, OFA) in % |
| `c` | Proc (SM) and memory clocks (MHz) |
| `v` | Power violations (%) and thermal violations (boolean) |
| `m` | Frame-buffer + BAR1 (+ confidential-compute) memory used (MB) |
| `e` | **ECC errors (aggregate SBE/DBE counts) AND PCIe replay errors** -- not ECC alone |
| `t` | **PCIe Rx/Tx throughput (MB/s)** -- this is the trap: `t` is *throughput*, NOT temperature |

The gotcha that bites people: `t` is **PCIe throughput**, and temperature comes from `p`.
A monitor string written as "`pucvmet` for temp+power+..." still happens to show temperature
(because `p` carries it), but if you reach for a bare `-s t` expecting degrees you get PCIe
MB/s instead. For a soak the high-signal set is `p` (power+temp), `c` (clocks), `v`
(violations) and `m` (memory); add `e` when you want live ECC/replay counts in the same view.
`nvidia-smi dmon -s pucvm -d 1` is the one-line "show me everything once a second" you leave
running on a second terminal during a burn.

---

## 5. XID Errors — the Codes That Matter and the Action for Each

XID errors are NVIDIA's catch-all hardware/driver error codes, emitted by the kernel driver
into `dmesg` as:

```text
NVRM: Xid (PCI:0000:65:00): 48, pid=12345, name=python, ...
```

Learning to read them is the GPU analog of decoding AER bits: **each code maps to a
root-cause bucket** (app vs memory vs bus vs NVLink vs firmware), which is what makes XID
monitoring the single highest-signal GPU manufacturing check. You scrape `dmesg` (or
journald) for `NVRM: Xid` across the stress window and **bucket by code.** The toolkit's
`_scan_xids()` does exactly that with the regex
`NVRM:\s*Xid\s*\([^)]*\):\s*(\d+)`, counting occurrences per code.

The codes that actually matter, with the action:

| XID | Name | Bucket | Action on the floor |
|---|---|---|---|
| **13** | Graphics Engine Exception (GR: SW Notify Error) | App (usually) | Out-of-bounds / illegal instruction in the workload. Re-run under `compute-sanitizer`; rarely HW. Not a unit fail by itself |
| **31** | GPU memory page fault (FIFO: MMU Error) | App (usually) | Illegal address access. Usually the test app's bug; can be driver/HW if it repeats across apps |
| **43** | GPU stopped processing (Channel Reset Verification Error) | SW teardown | The driver stopped a misbehaving context (app abort/SIGKILL). GPU stays healthy. Not a fail |
| **45** | Preemptive cleanup / channel removal (due to previous errors) | SW teardown | Robust-channel recovery after an app was killed. Benign for the GPU |
| **48** | **Double-Bit ECC (DBE)** | **Memory HW** | **Uncorrectable memory error.** Reset/reboot to clear; **fail the unit.** Repeated across resets -> RMA |
| **62** | Internal micro-controller halt | Firmware HW | Firmware fault -> GPU reset. Repeated -> RMA |
| **63** | Row-remap / page-retirement **event (succeeded)** | Memory HW | A row was successfully remapped; **reset pending.** On a *new* unit this is a finding (why did a fresh part remap?) -> reset, re-verify, log |
| **64** | Row-remap / page-retirement **FAILURE** | **Memory HW** | Remap **failed** -- no spare row or broken remap HW. **Hard fail / RMA** (Section 2.3) |
| **74** | **NVLink error** | **NVLink HW** | CRC/replay/recovery problem on a GPU-to-GPU or NVSwitch link. Read `nvidia-smi nvlink -e`; can be HW -> fail/RMA |
| **79** | **GPU has fallen off the bus** | **PCIe / power / thermal HW** | GPU is no longer accessible over PCIe. **Almost always a hardware failure** -- link, power droop, or thermal. **Hard fail**; correlate with rail/temp logs |
| **92** | High single-bit ECC rate (Excessive SBE Interrupts) | Memory (degrading) | SBE rate crossed threshold. Degrading memory -> investigate; trend it, fail if it climbs |
| **94** | **Contained** ECC error | Memory HW | Uncorrectable error isolated to one app (the rest of the GPU keeps running). Restart the app; **fail the unit** on a new part |
| **95** | **Uncontained** ECC error | Memory HW | Uncorrectable error that escaped containment -> affects multiple apps -> GPU reset required. **Hard fail** |
| **119 / 120** | GSP RPC Timeout / GSP Error | Firmware HW | GPU System Processor (on-die microcontroller) fault -> reset. Repeated -> RMA |

> **The XID gate.** In burn-in, **any of XID 48, 63, 64, 74, 79, 92, 94, 95** is a hard fail
> with a clear root-cause bucket already attached. XID 13/31/43/45 are app/SW teardown noise
> *unless* they repeat across different workloads. This is precisely the toolkit's
> `_XID_CRITICAL` set: `{48: DBE, 63: remap-pending, 64: remap-failure, 74: NVLink, 79: fell
> off bus, 92: high-SBE-rate, 94: contained, 95: uncontained}` — and `no_critical_xid` fails
> the unit if any of those appear in the dmesg scan. (XID 92 is the high single-bit-rate
> code, *not* a containment code — 94/95 are the contained/uncontained pair.)

A subtlety worth internalizing: **XID 79 ("fell off the bus") is a PCIe/power/thermal story,
not a GPU-internal one.** When you see it, you do not start by suspecting the silicon — you
correlate with the PCIe AER counters on that GPU's root port (PCIe chapter), the rail
voltages (a droop under load can drop the GPU off the link), and the thermal log (an
over-temp can do the same). The XID tells you *what* happened; the surrounding telemetry
tells you *which layer*.

Why 94/95 exist at all — the containment story. On Volta and older, a single uncorrectable
ECC error took down **every** running context on the GPU; there was no isolation. Starting
with the A100, NVIDIA added **error containment**: the driver tries to confine an
uncorrectable error to just the offending application (XID 94, *contained* — the rest of the
GPU keeps running) and only falls back to a full GPU reset when it cannot (XID 95,
*uncontained*). For *your* purposes both are a fail on a new part — a DBE is a DBE — but the
94-vs-95 split tells you whether the GPU's containment machinery did its job, which matters
when you are deciding screen-vs-RMA on a part that throws these repeatedly.

What the scrape actually looks like, and how to bucket it. The driver writes one line per
event; a real soak log with a memory fault in it reads:

```text
NVRM: Xid (PCI:0000:65:00): 48, pid=12345, name=python3, Row Remapper: ...
NVRM: Xid (PCI:0000:65:00): 63, pid=0, Row remap: pending, reset required
NVRM: Xid (PCI:0000:b3:00): 31, pid=22871, name=cuda-memtest, Ch 00000008, ...
```

The toolkit's regex `NVRM:\s*Xid\s*\([^)]*\):\s*(\d+)` pulls the bus-id-and-code shape and
keys on the integer, so the three lines above bucket to `{48: 1, 63: 1, 31: 1}`. The verdict
logic then says: 48 and 63 are in `_XID_CRITICAL` -> **fail**, with the bus IDs telling you
*which* GPU (`65:00` took the DBE-and-remap; `b3:00` only logged an app-level MMU fault that
is noise unless it repeats). One bench-level gotcha: scrape `dmesg` with a **timestamp filter
bounded to the stress window** (`dmesg --since` / `journalctl --since`), not the whole boot
log, or you will pick up enumeration-time and previous-unit XIDs and false-fail. The
companion `_scan_xids()` is run against the captured window for exactly this reason.

---

## 6. Health Checks: nvidia-smi -q and DCGM

### 6.1 Parsing nvidia-smi: CSV for machines, `-q` for humans

There are two query modes and you must use the right one. **`nvidia-smi -q`** dumps a deep,
human-readable tree (`-d ECC|POWER|CLOCK|TEMPERATURE|PCIE|ROW_REMAPPER|PERFORMANCE` to scope
it). It is what you read at the bench. But **do not parse it with regex in a test program if
you can avoid it** — it is free-form and changes between driver versions.

For automation, **`--query-gpu=<fields> --format=csv,noheader,nounits`** returns clean CSV
that maps straight into a parser, no regex:

```bash
nvidia-smi --query-gpu=index,name,pci.bus_id,\
pcie.link.gen.current,pcie.link.width.current,\
temperature.gpu,power.draw,clocks.current.sm,utilization.gpu,\
ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,\
ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total,\
pcie.replay.counter,clocks_throttle_reasons.active \
--format=csv,noheader,nounits
```

This is exactly the field list the toolkit's `_query_nvidia_smi()` requests. Note it asks
for **both** `.volatile.total` and `.aggregate.total` for corrected and uncorrected — so the
code can gate on volatile and log aggregate (§2.2). The few things CSV cannot give you (the
row-remapper detail, the ECC *mode*) you fetch with a scoped `-q -d ROW_REMAPPER` and parse
narrowly, which is why `_query_row_remap()` is a separate function.

The minimal Python pattern (the toolkit's `gpu.py` is the full version):

```python
import subprocess, csv, io

FIELDS = ["index", "name", "pcie.link.gen.current", "pcie.link.width.current",
          "temperature.gpu", "ecc.errors.uncorrected.volatile.total",
          "ecc.errors.uncorrected.aggregate.total", "pcie.replay.counter",
          "clocks_throttle_reasons.active"]

def query_gpus() -> list[dict]:
    out = subprocess.run(
        ["nvidia-smi", f"--query-gpu={','.join(FIELDS)}",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10, check=True).stdout
    # csv with manual fieldnames since we asked for noheader:
    reader = csv.DictReader(io.StringIO(out), fieldnames=FIELDS, skipinitialspace=True)
    return [dict(row) for row in reader]
```

### 6.2 DCGM: the manufacturing-grade tool

`nvidia-smi` is for humans; **DCGM (Data Center GPU Manager)** is for test programs. It is a
daemon (`nv-hostengine`) plus the `dcgmi` CLI, and it is what you wrap for an automated
station because it produces **structured, per-subsystem pass/fail** and machine-readable
output.

```bash
dcgmi discovery -l        # enumerate GPUs + topology (NVLink/PCIe)
dcgmi diag -r 1           # quick readiness (seconds): driver/NVML/sanity
dcgmi diag -r 2           # medium (~2 min): + PCIe/NVLink, memory bandwidth, integration
dcgmi diag -r 3           # long (several min): full HW diag + stress -- THE qualification run
dcgmi diag -r 4           # extra-long (DCGM >= 2.4): + memtest + Pulse (power-spike) test
dcgmi diag -r 3 -j        # JSON output -- parse this in your harness
```

| Level | Flag | Coverage (approx.) |
|---|---|---|
| 1 | `-r 1` | Quick readiness: SW/driver, NVML, basic sanity (seconds) |
| 2 | `-r 2` | Medium: + PCIe/NVLink checks, memory bandwidth, integration (~2 min) |
| 3 | `-r 3` | Long: full HW diag + stress -- Memory (Targeted), SM/Targeted Power & Stress, PCIe. **The standard qualification run** |
| 4 | `-r 4` | Extra-long: adds memtest (walking-1s + pattern) and the Pulse Test (PSU power-spike stress) |

The named plugins `-r 3` runs (these are the keys you will see in the output) are: **Memory**,
**Memory Bandwidth**, **PCIe** (+ NVLink), **SM Stress**, **Targeted Stress** (drives a target
gigaflops via large cuBLAS GEMMs), and **Targeted Power** (drives the part to its power
limit). `-r 4` adds **Memtest** and **Pulse Test**. A plaintext run prints a pass/fail grid:

```text
+---------------------------+------------------------------------------------+
| Diagnostic                | Result                                         |
+===========================+================================================+
|-----  Deployment  --------+------------------------------------------------|
| Denylist                  | Pass                                           |
| NVML Library              | Pass                                           |
| Persistence Mode          | Pass                                           |
+-----  Integration  -------+------------------------------------------------|
| PCIe                      | Pass - All                                     |
+-----  Hardware  ----------+------------------------------------------------|
| GPU Memory                | Pass - All                                     |
| Memtest                   | Pass - All                                     |
+-----  Stress  ------------+------------------------------------------------|
| Targeted Stress           | Fail - GPU 2                                   |
| Targeted Power            | Pass - All                                     |
| SM Stress                 | Pass - All                                     |
+---------------------------+------------------------------------------------+
```

In a harness you do not parse that grid — you run `dcgmi diag -r 3 -j` and read the JSON,
where each test carries a `status` plus, on failure, a `warnings` array with the specific
reason (a thermal-violation message, a NVVS error code, an ECC count). The `Fail - GPU 2` on
Targeted Stress above is the hook: the JSON for that entry will say *why* (most often a
thermal throttle the stress provoked, which points you straight back to the §3.2 throttle
bits for that GPU). Fold the per-test status into your pytest result and attach the warning
text to the failure so the bench sees the bucket, not just "DCGM failed."

`dcgmi diag -r 3` is close to a turnkey GPU module test: memory tests (catch ECC/memory
defects), compute stress (drives thermal + power), PCIe checks, all with structured pass/fail.
But it is not the *whole* test. Your job is to **wrap it**: set the right plugin thresholds,
parse `-j` JSON, fold it into the pytest harness, and **add what it does not cover** — your
PCIe lane margining (PCIe chapter), thermal-correlated AER, XID bucketing across the soak,
and rail-voltage measurements with a real DMM (power chapter). DCGM complements **gpu-burn**:
gpu-burn is a brute max-thermal soak that proves the *cooling solution*; `-r 3/4` gives you
structured per-subsystem coverage. Run both.

### 6.3 NVLink and multi-GPU checks

On a multi-GPU board, NVLink is a separate interface to verify (§1.3). The commands:

```bash
nvidia-smi topo -m              # topology matrix: which GPUs are NVLink (NV#) vs PCIe (PIX/PXB/SYS)
nvidia-smi nvlink -s            # per-link state: active/inactive + per-link speed (GB/s)
nvidia-smi nvlink -e            # per-link ERROR counters: CRC, replay, recovery
nvidia-smi nvlink -ec           # per-LANE CRC error counters (finer than -e; isolates one lane)
dcgmi nvlink --link-status      # DCGM view; DCGM_FI_DEV_NVLINK_* fields for fleet monitoring
nvbandwidth                     # measured GPU-to-GPU / host bandwidth (separate from PCIe BW)
```

What to check: the topology matches the board design (a full mesh or the specific tree Electrical Engineering (EE)
specified — a missing `NV#` entry means a link did not come up), every expected link is
**active**, the NVLink **CRC/replay/recovery** counters are zero or not climbing under load,
and peer bandwidth hits expected. **XID 74** is the kernel's summary of an NVLink fault;
`nvidia-smi nvlink -e` is where you read the detail that XID 74 points at. Multi-GPU boards
also have a **power-budget** test: 4 GPUs at ~300 W each is ~1.2 kW — start all GPUs on max
compute simultaneously, watch the PSU rails for droop (should not sag >5%), and confirm no
GPU drops off (XID 79) when the whole board is loaded at once.

`nvidia-smi nvlink -e` reports **four** counter types per link; know what each one implies:

| Counter | What it counts | Reading it |
|---|---|---|
| **CRC FLIT Error** | Receive flow-control-digit (header/control) CRC errors | A few isolated ones can be recovered; a climbing rate is a marginal lane/SerDes |
| **CRC Data Error** | Receive data-payload CRC errors | Same -- physical-layer integrity; climbing == SI/SerDes problem |
| **Replay Error** | Transmit-side replays (a flit had to be re-sent) | The NVLink analog of a PCIe replay -- climbing == marginal link |
| **Recovery Error** | Link had to run its recovery/retrain sequence | The serious one: the link went down far enough to need recovery; repeated == failing link |

These mirror the PCIe correctable story exactly: an occasional CRC the link corrected is one
thing; a **rate that climbs under thermal load** is the finding, and the fix path is the same
(check the lane with `-ec`, soak hot, suspect the SerDes / the connector / the peer device).
A non-zero **Recovery Error** is the most alarming of the four because it means the link
dropped, not merely flickered.

The link-count arithmetic is worth carrying for the topology check. NVLink aggregate
bandwidth is *per-link speed x number of links*, so a partly-trained part is easy to spot by
the number, not just the missing `NV#`:

- **A100 / NVLink 3:** 12 links x 50 GB/s = **600 GB/s** total bidirectional.
- **H100 / NVLink 4:** 18 links x 50 GB/s = **900 GB/s** total bidirectional.

So if `nvidia-smi nvlink -s` on an H100 shows only 16 active links instead of 18, you are
looking at ~800 GB/s and two links that never trained — a finding even though the GPU
enumerates and most links are up. Cross-check the active-link *count* against the part's spec,
not just "are there some links."

---

## 7. Failure Signatures → Root Cause

The payoff table. A GPU finding rarely arrives labeled; you read the telemetry and map it to
a layer. This is the GPU analog of the PCIe triage table, and it is what you keep open when a
unit fails on the fixture.

| Symptom (what you observe) | Most likely root cause | First moves |
|---|---|---|
| Link at Gen3 (expected Gen4) / x8 (expected x16) | PCIe SI margin, bent pin, bifurcation, thermal | **It's a PCIe problem** -- compare LnkCap both ends, retest hot/cold, lane-margin per lane (PCIe chapter) |
| Replay count climbing under load | PCIe physical-layer SI, marginal lane, temperature | Decode AER bits on the root port; thermal soak; margining (PCIe chapter) |
| Volatile **DBE > 0** / **XID 48** | Uncorrectable memory error (HBM/GDDR or SRAM) | **Fail the unit.** Check row-remapper state; repeated across resets -> RMA |
| **XID 64** / Remap Failure = Yes | Spare rows exhausted or broken remap HW | **Hard fail / RMA** -- the part can no longer self-heal (Section 2.3) |
| Remap Pending = Yes on a new unit | A remap is queued (recent uncorrectable) | Reset, re-verify; ask *why a fresh part remapped* -- a finding |
| High/climbing SBE rate / **XID 92** | Degrading memory cells | Trend it across the soak; fail if it climbs; correlate with temp |
| Clocks sag under load, throttle bits `0x40`/`0x20` | **Cooling problem** -- bad TIM/mount, dead fan, pump-out | Re-seat heatsink, re-paste, check fan RPM; *not* a silicon defect |
| Clocks sag, throttle bit `0x80` (power brake) | Power-delivery problem -- PSU/VRM asserted brake | Scope the rails under load; check PSU sizing and the power-brake net |
| Clocks sag, only `0x04` (SW power cap) at high load | Often **normal** -- hitting the configured power limit | Check the limit is set correctly (`-q -d POWER`); raise if too conservative |
| **XID 79** -- "fell off the bus" | **PCIe / power / thermal HW** (not GPU-internal) | Correlate AER (root port) + rail voltage + temp; reseat; **hard fail** |
| **XID 74** / NVLCRC/replay climbing | NVLink SerDes problem | `nvidia-smi nvlink -e`; check the peer GPU and NVSwitch; can be HW |
| `temperature.memory` high but core OK | HBM/GDDR cooling path (separate from die) | Check memory thermal pads/contact; HBM throttles ~95 degC |
| GPU not enumerated at all (`nvidia-smi -L` short) | Power rail, PCIe link dead, seating, BIOS | Rails first; `dmesg` for LTSSM-stuck/training-fail; reseat; bifurcation (PCIe chapter) |
| **XID 119/120** -- GSP fault | GPU firmware (GSP) | Reset; reflash/match driver+firmware; repeated -> RMA |

The throughline: **the throttle bitmask and the XID code each name a layer.** Thermal-bit
throttle → cooling. Power-brake bit → power delivery. XID 48/64/92/94/95 → memory. XID 79 →
link/power/thermal (go correlate). XID 74 → NVLink. Your test should never report only "GPU
failed" — it should report *which bit / which XID*, because that is the first fork in the
debug tree, and it is what lets EE fix the board instead of guessing.

---

## 8. Manufacturing Flows: Screen vs RMA

Tie it to the four phases (platform chapter). A GPU defect is catchable at different phases,
and the organizing principle is to put each test at the earliest phase that can catch its
defect -- the earlier a defect is caught, the cheaper the rework and the less value has been
added to a part that was going to fail anyway.

**At Printed Circuit Board Assembly (PCBA)** (bare board, before the GPU's thermal solution is even mounted, often at the Contract Manufacturer (CM)):
- Enumeration and basic link train — does `nvidia-smi -L` see it, does it reach the expected
  gen/width at room temperature. Catches gross assembly defects (dead lane, bad solder under
  the package — pair with X-ray).
- Cannot catch: thermal-mount defects (no heatsink yet), marginal-at-temperature behavior.

**At module** (the heart of GPU test — the unit is sealed with its heatsink/TIM/fan):
- **Burn-in / thermal soak.** `gpu-burn` or `dcgmi diag -r 3` for a sustained interval
  (typically 15+ minutes to reach thermal steady state). This is where a bad TIM application,
  a dead fan, or paste pump-out surfaces as a thermal throttle — defects that **cannot** be
  caught at room-temperature PCBA.
- **ECC stress.** `dcgmi diag -r 3/4` memory tests (and `cuda-memtest --stress` if available)
  drive the memory array to surface weak cells -> volatile DBE / row-remap events.
- **Thermal-correlated link + XID monitoring.** Run the burn while watching the throttle
  bitmask, AER on the PCIe link, and `dmesg` XIDs — the marginal-lane-at-85degC and
  fell-off-the-bus-under-load defects only appear here.
- **Power characterization.** Measure the rails with a DMM/scope under full load; confirm no
  droop, no power-brake assertion.

The module burn-in is the highest-yield step in the whole flow, so it is worth being precise
about *why each parameter is set where it is*:

- **Why 15+ minutes, not 2.** A heatsink/TIM defect does not show up cold — the die has to
  reach **thermal steady state** before a bad thermal path expresses itself as a throttle.
  Steady state on a big GPU under full tensor load is several minutes of rising temperature;
  the soak has to run *past* the knee so the temperature has plateaued, then hold so a slow
  failure (paste pump-out, a fan spinning down) has time to appear. A 2-minute run can pass a
  part that throttles at minute 8. Pick the dwell from the thermal curve, not a round number.
- **Why tensor load specifically.** `gpu-burn --tensor` / DCGM **Targeted Stress** drive the
  tensor path, which is the hottest, highest-power region (§1.1). An FP32-only load under-
  drives the part and under-tests the cooling. The point of burn-in is to provoke the worst-
  case thermal, so use the worst-case workload.
- **What "good" looks like during the soak.** Temperature rises and **plateaus** below the
  slowdown threshold; `clocks.current.sm` holds near P0 boost (a *sustained* sag is the
  throttle signature); `clocks_event_reasons.active` decodes to benign-only (idle/app-clocks/
  sync-boost, or `0x4` SW power cap if you deliberately capped power); volatile DBE stays 0;
  no new XID in the windowed `dmesg` scrape. A bad TIM mount shows the opposite: temperature
  keeps climbing to the hard limit and `0x40` (HW thermal) latches.
- **ECC-stress as a distinct objective.** Thermal soak proves the *cooling*; the memory test
  (DCGM **Memtest** / **Memory**, walking-1s and pattern writes) proves the *array*. They are
  different defects — a part can cool perfectly and still have weak cells — so a complete
  module test runs both, not one as a proxy for the other. The ECC-stress pass criterion is
  volatile uncorrected == 0 and no new remap-pending/failure across the test (§2).
- **Repeatability across stations.** When you are correlating station-to-station or chasing a
  marginal part, **lock the clock** (`-lgc`) and **pin the power limit** (`-pl`) so boost
  variance does not muddy the temperature/power numbers; for a normal go/no-go soak leave
  boost free and judge on the throttle bits instead (§3.3).

**At system** (multiple GPUs/modules integrated): the **simultaneous** full-load power and
thermal test — all GPUs at max at once, system airflow, PSU under aggregate load — plus
NVLink/peer bandwidth across the real topology. Single-module test should already have proven
each GPU in isolation, so a system-test failure points at integration (power budget, airflow,
inter-module links).

**At vehicle (EOL):** the compute runs in the car; you confirm the GPUs come up and run the
real perception workload in the real thermal/vibration environment.

### What is a screen vs an RMA

The distinction that decides what happens to a failing unit:

- **A SCREEN is a defect introduced in *your* build that rework can fix** — re-seat the
  heatsink, re-apply TIM, replace a fan, re-flash firmware, reseat the card. A thermal
  throttle from a bad mount, an XID 119 from mismatched firmware, a Gen3 link from a poorly
  seated card: you rework and re-test, and the unit passes. The defect never leaves your line.
- **An RMA is a defect in the GPU itself that you cannot fix** — a DBE that recurs across
  resets, a remap *failure* (no spare rows / broken remap HW, XID 64), a recurring NVLink HW
  fault (XID 74), a part that repeatedly falls off the bus after power/thermal/seating have
  been cleared (XID 79). The part goes back to the vendor.

The judgment call lives in the middle: **a part with nonzero *aggregate* ECC or a lifetime
remap count is not automatically either.** It is RMA *history* (§2.2) — most often it tells
you a **used or returned part entered your new-build line** (a supply-chain/genealogy
finding), which is why you *log* aggregate and remap counts on every unit even when they
pass. A fresh part should have a clean lifetime history; one that does not is a flag for
quality, not necessarily a scrap. That logging discipline — capture the parameter, not just
the verdict (Design Verification (DV)-vs-Manufacturing Test (MT) chapter) — is what lets the fleet data later flag a bad lot or a
re-stocked tray before it becomes a field problem.
