## DRAM and Memory (EDAC/RAS)

Memory test has the same shape as every other interface in this guide: **clear the
counters → stress it (hot) → read the counters → decode an error to a physical part you
can Return Merchandise Authorization (RMA).** The counter system for Dynamic Random-Access Memory (DRAM) is **Error Detection and Correction (EDAC)**, and it
is the DRAM analog of PCIe Advanced Error Reporting (AER) and GPU Error-Correcting Code (ECC) — same mental model, different sysfs. On Zoox's
server-grade compute the DRAM is almost certainly **ECC** (RDIMM/LRDIMM), and verifying
that ECC *actually detects and corrects* — not just that the box boots — is a functional-
safety requirement, not a nicety: ECC is a fault-tolerance mechanism the manufacturing test
must prove engages.

> **The one-sentence version.** A single-bit corrected error (CE) is the DRAM analog of a
> PCIe correctable — a few over a long soak can be benign, a high or concentrated rate is a
> finding. An uncorrectable error (UE) is the DRAM analog of a PCIe uncorrectable — **any UE
> on a new unit is a fail**, and it can crash the box. Everything below is how you count
> them, run the system hot enough to provoke them, and turn one into a Dual Inline Memory Module (DIMM) silkscreen label.

### DDR4/DDR5 fundamentals relevant to test

You do not need to design a memory controller, but a handful of facts change what you test
and how you read a failure:

- **The bus is 64 data bits; ECC adds 8** for a **72-bit** channel. That extra DRAM device
  per rank is what carries the ECC syndrome — enough for **SECDED** (Single-Error-Correct,
  Double-Error-Detect) per 64-bit word.
- **Ranks and channels.** A DIMM has one or more **ranks** (a set of DRAM chips the
  controller activates together to make a full data word). The controller has multiple
  **channels**, each driving one or more DIMMs. EDAC reports per-`csrow`/per-`channel` (DDR4
  style) or per-`dimm`/per-`rank` (DDR5 style), which is the granularity at which you localize
  a fault.
- **DDR5 splits each DIMM into two independent 32-bit sub-channels** (so a DDR5 DIMM presents
  as *two* narrower channels), runs at higher data rates, moves voltage regulation **onto the
  DIMM** (the PMIC), and adds **on-die ECC** (below). The sub-channel split matters because a
  fault localizes to a sub-channel, and the higher rates make signal-integrity and thermal
  margin tighter — DDR5 is *less* forgiving, which is exactly why the hot soak matters more.
- **Refresh and timing.** DRAM is leaky; cells are refreshed on an interval (tREFI). Marginal
  cells fail when refresh can't keep them charged — which is strongly **temperature dependent**
  (hotter = leakier = more refresh stress). This is the physical reason "run it hot."

### ECC: SECDED, on-die ECC, and scrubbing

- **SECDED (system ECC):** the controller computes an 8-bit Hamming-style code over each
  64-bit word. It can **correct any single-bit** error and **detect (not correct) any
  double-bit** error. A corrected single-bit event is a **CE**; a detected-but-uncorrectable
  double-bit (or worse) event is a **UE**.
- **Chipkill / SDDC (Single Device Data Correction):** high-end controllers go beyond SECDED
  and can **correct an entire failed x4 (or x8) DRAM device** by spreading Reed-Solomon symbols
  across devices. This is a stronger scheme than basic 72-bit SECDED — a whole chip can die and
  the system keeps running and correcting. Know whether the platform has it, because it changes
  what "a CE" means (the controller may be masking a dead device).
- **On-die ECC (ODECC, DDR5):** *internal* single-bit correction **inside each DRAM die**,
  before data leaves the chip. DDR5 mandates it (it is a *yield* feature — it lets the fab
  ship die with isolated weak cells): each die computes a SECDED-class code over an internal
  ~128-bit word with 8 extra check bits and corrects single-bit errors on read. The check
  bits are **not** transmitted on the bus, so the correction is **invisible to the host and
  does not replace system ECC** — the host's EDAC counters never see an error ODECC silently
  fixed. DDR5 also adds **on-die Error Check and Scrub (ECS)**, an internal scrub the die runs
  on its own array, again invisibly. The danger is mistaking "DDR5 has ECC" (on-die, internal,
  invisible) for "system ECC is on and clean" (controller-level, the 72-bit channel ECC, which
  is what your EDAC counters actually monitor). They are different layers, and only the second
  one is observable to your test.
- **Scrubbing** keeps single-bit errors from *accumulating* into uncorrectable double-bit
  errors over time:
  - **Patrol scrub:** the controller proactively walks all of memory in the background, reads
    each location, and **rewrites the corrected data** so a latent single-bit flip is fixed
    before a second bit in the same word flips and makes it uncorrectable.
  - **Demand scrub:** when a read hits a correctable error, the controller writes back the
    corrected value immediately (scrub-on-demand).
  - Why you care: patrol scrub is *why* a system can run for months without a CE turning into
    a UE, and a **disabled** scrubber is a latent reliability bug your test should verify is
    on. It also means CE counts you read are "errors found and fixed," not "errors waiting."

> **The DDR5 masking gotcha (state it back the way it bites).** DDR5 on-die ECC can **hide a
> marginal cell from system-level EDAC counters** — the die corrects it internally and the
> host sees nothing. So a DDR5 system can look *perfectly clean* in EDAC while a die is
> silently working overtime to correct a degrading cell. The takeaway: **system ECC + EDAC
> remain necessary** to catch what escapes on-die correction, and a clean EDAC count on DDR5
> is weaker evidence than on DDR4. Do not read "DDR5 has ECC" as "I can trust a clean EDAC
> count" — lean harder on the *stress* (miscompare detection in `stressapptest`) to provoke
> the cell past what ODECC can hide.

### The memory controller (where the counters come from)

On modern server silicon the **integrated memory controller (iMC)** lives on the CPU die,
one or more per socket, each owning several channels. When ECC corrects or detects an error,
the iMC logs it in **machine-check (MCA) registers**, and the platform reports it either via
a **CMCI** (Corrected Machine Check Interrupt) for CEs or an **MCE** for UEs. The Linux EDAC
subsystem (and `rasdaemon`) consumes those events and surfaces them as the counters you read.
This is why the model is identical to AER: a hardware block latches errors into registers, an
OS layer drains them, and your test arms/stresses/reads. The chipset-specific EDAC driver
(`skx_edac`, `i10nm_edac`, `amd64_edac`, etc.) is what knows your controller's topology and
must be **loaded** for `/sys/devices/system/edac/mc/` to populate — a missing driver looks
like "no memory errors ever," which is a silent test escape (verify the mc* nodes exist).

### EDAC sysfs — reading memory errors in Linux

The kernel surfaces the controller's error counters under `/sys/devices/system/edac/mc/`:

```bash
# Per-controller totals (mc0, mc1, ... one per integrated memory controller)
cat /sys/devices/system/edac/mc/mc0/ce_count        # corrected (single-bit) errors
cat /sys/devices/system/edac/mc/mc0/ue_count        # uncorrectable (double-bit+) errors

# Per-DIMM / per-rank breakdown (the localization granularity)
cat /sys/devices/system/edac/mc/mc0/dimm0/dimm_ce_count   # CE on this specific DIMM
cat /sys/devices/system/edac/mc/mc0/dimm0/dimm_label       # the silkscreen label (if populated)
cat /sys/devices/system/edac/mc/mc0/dimm0/dimm_location     # channel/slot location
cat /sys/devices/system/edac/mc/mc0/dimm0/size              # MB

# Older / csrow-style controllers expose csrowN/chX_ce_count instead of dimmN
ls /sys/devices/system/edac/mc/mc0/
```

The structure under each `mcN` (these attribute names are the kernel EDAC sysfs ABI):

```text
/sys/devices/system/edac/mc/mc0/
  mc_name             <- the EDAC driver / controller name (e.g. "skx_edac")
  size_mb             <- total memory this controller manages, in MB
  ce_count            <- controller-total corrected errors
  ue_count            <- controller-total uncorrectable errors
  ce_noinfo_count     <- CEs the controller could not attribute to a DIMM
  ue_noinfo_count     <- UEs with no location info
  reset_counters      <- write-only: writing here zeroes this mc's counters
  seconds_since_reset <- seconds since the last counter reset (your baseline clock)
  sdram_scrub_rate    <- scrub bandwidth in bytes/sec (0 or absent = scrub off/unsupported)
  max_location        <- the deepest topology string this mc can report
  dimm0/  dimm1/  ...  (or  rank0/ ...  or  csrow0/ ... on older drivers)
     dimm_ce_count    <- per-DIMM corrected count
     dimm_ue_count    <- per-DIMM uncorrectable count
     dimm_label       <- e.g. "DIMM_A1"  (only if you registered the label DB)
     dimm_location    <- e.g. "memory controller 0 channel 1 slot 0"
     dimm_mem_type    <- e.g. "Registered-DDR5"
     dimm_edac_mode   <- the ECC scheme in force, e.g. "S4ECD4ED" or "SECDED"
     size             <- DIMM size in MB
```

Two of these directly serve the arm/stress/read discipline below: `reset_counters`
(write-to-zero, when the driver supports it) and `seconds_since_reset` give you a clean
baseline and a rate denominator, and `sdram_scrub_rate` lets the test **prove the patrol
scrubber is on** (a zero here on a platform that should scrub is the "disabled scrubber"
latent bug called out earlier). `dimm_edac_mode` is worth logging too — it tells you whether
the controller is running plain SECDED or a Chipkill/SDDC-class code (`S4ECD4ED` =
single-x4-device correct, double-x4-device detect), which changes what a clean CE count means.

A real listing on a two-controller DDR5 box looks like:

```text
$ ls /sys/devices/system/edac/mc/
mc0  mc1
$ ls /sys/devices/system/edac/mc/mc0/
ce_count  ce_noinfo_count  dimm0  dimm1  dimm2  dimm3  max_location
mc_name  reset_counters  sdram_scrub_rate  seconds_since_reset  size_mb
ue_count  ue_noinfo_count
$ cat /sys/devices/system/edac/mc/mc0/dimm0/dimm_location
memory controller 0 channel 0 slot 0
$ cat /sys/devices/system/edac/mc/mc0/dimm0/dimm_edac_mode
S4ECD4ED
```

**To read it in a test:** sum `ce_count`/`ue_count` across every `mcN` for the totals, and
walk each `dimmN`/`rankN` for the per-DIMM breakdown, falling back to the directory name
(`dimm0`) when `dimm_label` is empty. That is exactly what the toolkit's `_read_edac()` does:

```python
# computetest.memory._read_edac()  (real-hardware path)
def _read_edac():
    mcs = sorted(glob.glob("/sys/devices/system/edac/mc/mc[0-9]*"))
    total_ce = total_ue = 0
    per_dimm = {}
    for mc in mcs:
        total_ce += _read_int(f"{mc}/ce_count")
        total_ue += _read_int(f"{mc}/ue_count")
        for dimm in sorted(glob.glob(f"{mc}/dimm[0-9]*") + glob.glob(f"{mc}/rank[0-9]*")):
            label = _read_str(f"{dimm}/dimm_label") or os.path.basename(dimm)
            per_dimm[label] = per_dimm.get(label, 0) + _read_int(f"{dimm}/dimm_ce_count")
    return len(mcs), total_ce, total_ue, per_dimm
```

Note the two robustness moves you should copy: glob **both** `dimm*` and `rank*` (different
EDAC drivers name the leaf nodes differently), and fall back to the **node name** when the
silkscreen label has not been populated — so the test still localizes to *something* even on
a board whose label DB you have not built yet.

> **Arm/stress/read for memory.** EDAC counters are **monotonic** — they count from boot and
> do not auto-clear, so a raw read includes errors from POST, boot, and prior tests. For a
> clean per-unit measurement you must **baseline before the stress window** (read the counts,
> or reset them where the driver allows) and compare the *delta* after the soak. Reading the
> absolute count and gating on it is the EDAC version of forgetting to Write-1-to-Clear (W1C)-clear AER before a
> Bit Error Rate Test (BERT) — you end up failing units on errors that predate your test.

### rasdaemon and decoding an error to a physical DIMM silkscreen label

This is the highest-value capability in the whole chapter, because it turns a vague "the
board has memory errors" into an **actionable RMA**: *"DIMM_A1 is throwing 40 CE/hour, swap
that stick."* `rasdaemon` is the userspace daemon that consumes the kernel's Reliability, Availability, Serviceability (RAS) tracepoints,
decodes each CE/UE, and logs it — with the DIMM label and a timestamp — to a SQLite DB.

```bash
ras-mc-ctl --summary        # totals of CE/UE seen since rasdaemon started
ras-mc-ctl --error-count    # CE/UE per DIMM (from sysfs)
ras-mc-ctl --errors         # per-error detail: which DIMM, rank, syndrome, WHEN
ras-mc-ctl --layout         # the memory topology (controllers, channels, slots, sizes)
```

`ras-mc-ctl --errors` is the one you screenshot into a failure report:

```text
  1 2026-05-24 11:42:07 -0700  1 Corrected error(s)  memory read error at CPU_SrcID#0_MC#1_Chan#0_DIMM#0  ... label="DIMM_A1"
  2 2026-05-24 11:42:09 -0700  1 Corrected error(s)  memory read error at CPU_SrcID#0_MC#1_Chan#0_DIMM#0  ... label="DIMM_A1"
```

`--summary` rolls the same data into per-DIMM totals (the `location:` tuple is
`mc:top:mid:low`, i.e. controller : channel : slot, with `-1` meaning "not applicable at
this level"):

```text
Memory controller events summary:
        Corrected on DIMM Label(s): 'DIMM_A1' location: 1:0:0:0 errors: 42
        Corrected on DIMM Label(s): 'DIMM_B1' location: 1:1:0:0 errors: 1
No uncorrectable errors.
No PCIe AER errors.
No MCE errors.
```

and `--error-count` is the columnar CE/UE per DIMM you parse for the gate (labeled once the
label DB is registered; bare `mc#0csrow#2channel#0`-style rows before that):

```text
Label                 CE      UE
DIMM_A1               42       0
DIMM_B1                1       0
```

**The path from a kernel CE to the silkscreen label `DIMM_A1`** (so the operator knows which
physical slot to pull):

1. The iMC logs the error; the EDAC driver attributes it to a `(controller, channel, slot)`
   tuple — by default an abstract location like `CPU_SrcID#0_MC#1_Chan#0_DIMM#0`.
2. To turn that into the **board's silkscreen label** (`DIMM_A1`, the text printed next to
   the socket), you write a board-specific label map keyed by **DMI/SMBIOS identity**.
   `ras-mc-ctl` reads `/sys/class/dmi/id/board_vendor` and `/sys/class/dmi/id/board_name`
   (falling back to `dmidecode`), matches them against a config file under
   `/etc/ras/dimm_labels.d/` (or `/etc/ras/dimm_labels.db`), and `ras-mc-ctl
   --register-labels` writes the names into each `dimm*/dimm_label` sysfs node. The config
   format is a `Vendor:` / `Model:` header followed by `label: mc.top.mid.low` lines. You
   build this mapping **once per board revision** — read the schematic/mechanical to learn
   which controller/channel/slot maps to which silkscreen, write the file, and **every
   station inherits it** (register at boot via the `ras-mc-ctl` systemd unit).
3. With the labels registered, `dimm_label` reads `DIMM_A1` and `ras-mc-ctl --errors`,
   `--summary`, and `--error-count` all print it.

Without the label DB you still get the abstract `Chan#0_DIMM#0` location — usable, but an
operator can't act on it without a translation sheet. Building the label map per revision is
the difference between a test that says "memory error somewhere" and one that says "pull the
stick in slot A1." Wire `ras-mc-ctl` into the test and **log the label on every error**, pass
or fail.

### RAS concepts: CE vs UE, predictive failure, PPR, row-hammer

The error classes and what each means for a verdict:

- **CE (Corrected Error):** a single-bit flip the ECC fixed. The data was *correct* — nothing
  crashed. A handful over a long soak can be cosmic-ray noise; the signal is **rate** and
  **concentration** (many on one DIMM), not the existence of one CE.
- **UE (Uncorrectable Error):** ECC detected an error it could not fix (double-bit, or a
  failure beyond the code's strength). The data is *wrong*; the consequence is a machine-check
  — typically a kernel panic or an application crash. **Any UE on a new unit fails it.**

The Reliability, Availability, Serviceability (RAS) features built to manage these:

- **Predictive Failure Analysis (PFA):** rather than wait for a UE, the platform watches the
  **CE rate per DIMM/row** and flags a DIMM as *predicted-to-fail* when its CE rate crosses a
  threshold — the assumption being that a cell throwing rising single-bit errors will
  eventually throw an uncorrectable one. In the field this triggers a proactive RMA before a
  crash; in manufacturing it is *why* per-DIMM CE concentration is a gate, not just total CE.
- **Post-Package Repair (PPR):** DDR4/DDR5 DRAM ships with **spare rows** inside each device.
  When a row goes bad, the controller/BIOS can **remap a failing row to a spare** — **soft PPR**
  (volatile, until next boot) or **hard PPR** (a permanent, one-time fuse blow). This is the
  DRAM analog of Non-Volatile Memory Express (NVMe) spare blocks or GPU row-remapping. Test relevance: a board that has
  **already consumed PPR resources** on a "new" DIMM, or that *needs* PPR to pass, is a marginal
  part — log the PPR/repair state, don't just let BIOS quietly repair around a defect and ship it.
- **Row-hammer awareness:** repeatedly activating one DRAM row at high rate can disturb charge
  in **adjacent** rows and flip their bits — a reliability and security concern. Mitigations
  (TRR / Target Row Refresh, RFM / Refresh Management in DDR5, increased refresh) live in the
  DRAM and controller. You usually do not row-hammer test on the line, but know that a cluster
  of CEs in physically adjacent rows can be a row-hammer signature rather than a single weak
  cell, and that disabling refresh-management mitigations to "speed up" a test is a mistake.

### Stress and soak: stressapptest and memtester

The stress *provokes* errors; EDAC *counts* them. The two tools, and when to use each:

- **`stressapptest`** (Google's "stressful application test") — the manufacturing favorite,
  because it runs **under Linux**, inside your normal pytest/test environment. It hammers
  memory **bandwidth and patterns** using many threads, and it detects errors **two ways**:
  by **miscompare** (it writes known data, reads it back, and compares — catching errors ECC
  might mask *and* errors on non-ECC paths) and via the system's ECC/EDAC counters. It also
  exercises some I/O and cache coherency. Typical soak invocation:

  ```bash
  stressapptest -s 120 -M 28000 -W          # 120 s, use ~28 GB, with memory-copy (-W) threads
  stressapptest -s 600 -W                    # 10 min soak, auto-size memory, copy threads
  ```

  `-M` caps the memory footprint (MB) so you don't OOM the test station; omit it to let it
  auto-size to most of free RAM. `-W` adds memory-copy worker threads (more bus stress). `-s`
  is the soak duration. The toolkit's `stress_memory(seconds, mb)` wraps exactly this:
  `["stressapptest", "-s", str(seconds), "-W"]` plus `-M` if a footprint is given.

- **`memtester`** — a lighter userspace tool that mmaps a buffer and walks it through many
  pattern tests (walking ones/zeros, checkerboard, address-in-address, etc.). Good for a quick
  targeted pattern sweep of a *region*; weaker than `stressapptest` for whole-system bandwidth
  stress and multi-threaded coherency. `memtester 4G 3` tests 4 GB for 3 passes.

- **`memtest86+`** (awareness) — boots **instead of** the OS and walks the *full* address
  space with the most thorough patterns. Highest coverage of pattern-sensitive defects, but
  it is **offline** (cannot run inside your pytest harness) and slow — reserve it for a debug
  bring-up step or a fielded-failure deep dive, not volume line test.

**Interpreting the results.** A clean run is `stressapptest` exiting 0 **and** zero new UE
**and** total CE under your limit **and** no single-DIMM CE concentration. The tail of a
clean run ends with the status line you gate the exit on:

```text
Log: Seconds remaining: 0
Stats: Found 0 hardware incidents
Stats: Completed: 1835008.00M in 600.02s 3058.25MB/s, with 0 hardware incidents, 0 errors
Status: PASS - please verify no corrected errors
```

A `stressapptest` **miscompare** is a hard fail — it means data read back wrong, which on an
ECC system implies the error exceeded ECC's correction strength (effectively a UE) or hit a
path ECC doesn't cover. The failure line names the address, the expected vs actual bits, and
the worker thread:

```text
Hardware Error: miscompare on CPU 5(0x2) at 0x7f3a1c004000(0x...): read:0x0000000000000000, reread:0xffffffffffffffff expected:0x0000000000000000
Hardware Error: miscompare on CPU 5 ... ECC? (re-read matched expected -> transient/marginal)
Status: FAIL - 1 hardware incidents
```

The re-read tells you something: if the **first** read was wrong and the **re-read** matched
expected, the bit flipped and self-corrected — a transient/marginal cell or a soft error,
still a fail on a new unit but more "marginal DIMM" than "stuck cell." A miscompare whose
re-read *also* reads wrong is a hard/stuck fault. Always pair the stress run with a
**before/after EDAC read**: the tool provokes, EDAC attributes the error to a DIMM. The tool
says "something is wrong"; EDAC + the label DB say "DIMM_A1 is wrong." Note that on an ECC
system, a single-bit flip stressapptest provokes is usually *corrected before stressapptest
ever sees it* — it shows up as a **CE in EDAC**, not a stressapptest miscompare. So the two
detectors are complementary: EDAC catches the corrected single-bit events, the miscompare
catches what got past ECC. That is exactly why the gate reads **both**.

### Temperature and voltage dependence — run it hot

DRAM marginality is **strongly temperature- and voltage-dependent**, and this is the single
most important operational fact in this chapter. The physics: hotter cells leak charge faster,
so a marginal cell that holds its value at 25 C loses it before the next refresh at 70 C;
voltage droop (a sagging VDD/VDDQ rail under load) shrinks the noise margin the same way. The
consequence for test:

- **A room-temperature memory test is a weak test.** The classic escape is **room-temp pass /
  hot fail** — a DIMM that is clean on the bench and throws CEs (or UEs) at operating
  temperature in the vehicle. So you soak **at temperature** (thermal chamber, or under a load
  that self-heats the platform) and/or **at worst-case voltage** if the platform lets you margin
  the rail.
- **Correlate the rail.** If CEs appear under load, measure VDDQ with a DMM/scope at the
  same moment — a CE burst coincident with a rail droop is a **power-delivery** finding (a weak
  VRM/PMIC), not a bad DIMM. This is the same "is it the part or the support circuitry?"
  discipline as the NVMe thermal-throttle-vs-bad-drive split.
- **DDR5 ties this together:** higher data rates (tighter margins) plus on-die ECC (which hides
  early degradation) means the hot soak is doing *more* of the catching on DDR5 than it did on
  DDR4. Lean on it.

### Failure signatures and RMA decisions

| Symptom | Most likely cause | Decision / first moves |
|---|---|---|
| **Any UE** (`ue_count > 0`) | A real uncorrectable memory fault | **Hard fail.** Find the DIMM via `ras-mc-ctl --errors`; RMA that stick |
| CEs **concentrated on one DIMM** | That DIMM/rank is marginal (PFA signal) | Fail / RMA the named DIMM even if the *total* is under budget |
| CEs **spread evenly, low rate**, no concentration | Possibly cosmic-ray noise / benign | Pass if under the total CE limit; log for fleet trend |
| CEs **only under load / heat** | Marginal cell at temperature, or **rail droop** | Run hot; measure VDDQ during the burst — droop = power, not DIMM |
| `stressapptest` **miscompare** | Data read back wrong (beyond ECC, or non-ECC path) | Hard fail; treat as effectively a UE; capture which address |
| **UE escalating to MCE / kernel panic** mid-soak | Severe uncorrectable fault | Hard fail; the panic log + `ras-mc-ctl` identify the DIMM |
| **No `mc*` nodes** in EDAC sysfs at all | EDAC driver not loaded (silent escape) | Fix the test environment — load the chipset EDAC driver; a "clean" no-data result is invalid |
| Board needed/consumed **PPR** to pass on a new DIMM | Marginal DIMM that BIOS repaired around | Log PPR state; flag as marginal — don't ship a part that needed repair to pass |
| Cluster of CEs in **adjacent rows** | Possible row-hammer signature | Note it; verify refresh-management mitigations are enabled |

**The RMA discipline:** memory failures are *componentized* — you do not RMA "the board," you
RMA the **DIMM** (or, soldered-down, you flag the specific device location for the failure-
analysis lab). The whole reason for the label DB and `rasdaemon` is to make that decision
crisp: a failure report that says *"DIMM_B1, 3 UE during 10-min soak at 70 C, syndrome
attached"* is a closed-loop RMA; *"board has memory errors"* is a week of back-and-forth.

### Manufacturing-test limits and the gate

The memory gate at module test, stated as limits — and exactly what the toolkit's `_limits()`
enforces:

| Check | Limit | Why |
|---|---|---|
| `no_uncorrectable` | `total_ue == 0` | **Any UE = fail.** Non-negotiable on a new unit |
| `ce_total <= max_ce_total` | bounded total CE (default 100) | A small CE count over a soak can be benign; a flood is a finding |
| `no_ce_concentration` | worst DIMM `<= max_ce_per_dimm` (default 20) | **A hot DIMM even within the total budget is suspect** (PFA) — concentration localizes a marginal stick |

The third check is the subtle, high-leverage one: **total CE under budget is not enough.** A
board with 100 CE spread across 8 DIMMs is plausibly benign noise; a board with 100 CE *all on
DIMM_A1* is a marginal DIMM that happens to fit under the total — and PFA says it will fail in
the field. So you gate on **both** total CE **and** per-DIMM concentration. The toolkit:

```python
# computetest.memory._limits()
def _limits(total_ce, total_ue, per_dimm, max_ce_total, max_ce_per_dimm):
    worst = max(per_dimm.values()) if per_dimm else 0
    return {
        "no_uncorrectable":                     total_ue == 0,
        f"ce_total<={max_ce_total}":            total_ce <= max_ce_total,
        f"no_ce_concentration(<={max_ce_per_dimm}/dimm)": worst <= max_ce_per_dimm,
    }
```

> **Set the limit from fleet data, not a guess.** The 100-total / 20-per-DIMM defaults are
> starting points. The right values come from **Design Verification (DV) characterization** (soak good units across
> temperature, see what the healthy CE distribution actually is) and then from **production
> fleet data** (the CE distribution of known-good shipped units). Capture the per-DIMM CE
> counts on **every** unit — pass or fail — so the limit can be tightened to the real
> population, and so a *lot-wide* shift in CE rate (a bad DRAM batch) shows up as a trend
> before it shows up as field returns. This is the same "capture the parameter, not just the
> verdict" principle the rest of the toolkit runs on.

The full memory flow at module test, in order:

1. **Verify EDAC is alive.** `ls /sys/devices/system/edac/mc/` shows `mc0` (etc.) — the
   chipset EDAC driver is loaded. No nodes = invalid test, fix the environment first.
2. **Confirm topology.** `ras-mc-ctl --layout` / `dmidecode --type memory` — the right number,
   size, and speed of DIMMs are present (a missing or down-clocked DIMM is its own defect).
3. **Baseline the counters.** Read CE/UE per controller and per DIMM (or reset where allowed)
   so you measure the *delta* across the soak, not boot-time noise.
4. **Soak hot.** `stressapptest -s <soak> -W` (most of RAM, copy threads) at temperature —
   long enough and hot enough to provoke marginal cells. This is the catching step.
5. **Read the counters.** Delta CE/UE total and per-DIMM; `ras-mc-ctl --errors` for any error
   detail and the DIMM label.
6. **Apply the gate.** `total_ue == 0`, `total_ce <= limit`, no single-DIMM concentration; a
   `stressapptest` miscompare is a hard fail regardless.
7. **Log & decide.** Capture per-DIMM CE counts and any error detail (with label) into the
   test record. On fail, the named DIMM is the RMA; on pass, the numbers feed the fleet trend.

For step 2, `dmidecode --type 17` (DMI type 17 = Memory Device) is the topology gate — it
tells you populated slots, size, configured speed, and part number per slot, which is how you
catch a DIMM that is missing, undersized, or running below its rated speed (a down-clock is
its own defect: a single slow stick can drag the whole channel to the lowest common speed):

```text
Memory Device
        Locator: DIMM_A1
        Size: 32 GB
        Type: DDR5
        Speed: 4800 MT/s                  <- rated speed of the part
        Configured Memory Speed: 4800 MT/s  <- what it is actually clocked at (gate on this)
        Manufacturer: <vendor>
        Part Number: <pn>
        Rank: 2
```

Gate on **Configured Memory Speed** matching the expected rate (not just the rated `Speed`),
on the populated-slot count and size matching the BOM, and on the part number matching the
qualified part — a substituted or down-binned DIMM is a supply-chain finding the same way a
re-stock NVMe drive is. The `Locator` (`DIMM_A1`) is the same silkscreen string you want the
EDAC label DB to reproduce, so cross-checking the two also validates your label map.

### Memory health check in Python (toolkit cross-reference)

```python
from computetest.memory import check_memory, stress_memory

# Hot soak first (provokes), then read EDAC (counts):
stress_memory(seconds=600, mb=28000)            # stressapptest -s 600 -M 28000 -W
h = check_memory(max_ce_total=100, max_ce_per_dimm=20)

print(h.summary())
# memory: 2 mc, CE=4 UE=0 worst=DIMM_A1(2) -> OK

if not h.ok:
    failed = [k for k, ok in h.checks.items() if not ok]   # e.g. ['no_uncorrectable']
    # worst_dimm names the stick to pull:
    print("RMA target:", h.worst_dimm)                     # e.g. 'DIMM_B1'

for note in h.history:        # actionable per-DIMM notes, logged even on pass
    log.info(note)            # e.g. "DIMM_A1 has 2 CE (swap that stick)"
```

The `MemoryHealth` dataclass mirrors the NVMe one: `checks` (the three limits) drive
pass/fail via `.ok`, `per_dimm` carries the localization, `worst_dimm` names the RMA target,
and `history` records the actionable "swap that stick" note **even when the unit passes** — so
the per-DIMM signal feeds fleet trending and a marginal-but-passing DIMM is still visible to
Quality. Same pattern as everywhere in this toolkit: arm/stress/read, capture the number,
compare to a limit, and hand a failure to whoever fixes it with the part already named.
