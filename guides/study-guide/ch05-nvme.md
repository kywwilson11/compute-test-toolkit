## NVMe and Storage

An Non-Volatile Memory Express (NVMe) SSD is two things at once, and you test it as both. It is a **PCIe endpoint** —
so everything in the PCIe chapter applies *first*: a NVMe drive that throws Bad-Transaction Layer Packet (TLP)
correctable errors, trains x4→x2, or drops to Gen3 is a PCIe problem wearing a storage
costume, and you debug it with `lspci`, Advanced Error Reporting (AER), and lane margining, not `nvme-cli`. And it
is a **storage controller** with its own command set, health telemetry, self-test
engine, and failure modes — which is what this chapter covers. On Zoox's compute, NVMe
holds the OS, the maps and AI models, and the sensor-logging firehose (perception logging
alone is enormous and sustained), so "does it hit rated bandwidth and *hold* it under a
continuous write soak at temperature" is a real safety-relevant question, not a benchmark.

> **Rule of thumb for the floor.** When a drive misbehaves, ask "PCIe or storage?" before
> you touch `nvme-cli`. Check the link first: `nvme list`, then the drive's PCIe Bus/Device/Function (BDF) in
> sysfs (`current_link_speed`/`current_link_width`) and its AER counters. A throttling-or-
> errors story on the *link* is a PCIe-chapter problem; a SMART/media/self-test story is a
> storage problem. Half the "NVMe failures" you will chase are actually link failures.

### Why NVMe (and why the queue model matters to test)

NVMe replaced AHCI as the host-controller interface for flash. AHCI was designed for
spinning disks: one command queue, 32 entries. NVMe allows up to **65,535 I/O queues of
65,536 commands each**, which is what lets it exploit flash's internal parallel channels.
That parallelism is also *why* the manufacturing performance test looks the way it does:
sequential bandwidth is a single-queue, large-block test, but **IOPS is a queue-depth and
parallelism test** — you only see a drive's true random-read IOPS at high `iodepth` across
multiple jobs, because that is what fills all those queues. A drive that hits sequential BW
but misses IOPS often has a controller or parallelism problem, not a media problem.

### The architecture, in one paragraph you can act on

The host and controller communicate through **queues that live in host memory**:

- **Submission Queue (SQ):** the host writes a command here, then writes the SQ's
  **doorbell register** (in the controller's Base Address Register (BAR)-mapped Memory-Mapped I/O (MMIO) space) to tell the controller
  "go look." The doorbell is the one piece of the model that is a real MMIO register on the
  device; the queues themselves are host RAM.
- **Completion Queue (CQ):** the controller writes a completion entry here and raises an
  **Message Signaled Interrupt Extended (MSI-X) interrupt**. The host processes completions and writes the CQ doorbell to free
  slots. A phase-bit in each entry tells the host which entries are new without re-reading
  the doorbell.
- **Admin queue:** exactly one pair (SQ0/CQ0), created at init. It carries *management*
  commands — Identify, Get Log Page, Get/Set Feature, Format, Firmware Download/Commit,
  Device Self-Test (DST), Create/Delete I/O Queue. This is the queue your test program lives on.
- **I/O queues:** created via admin commands, typically **one SQ/CQ pair per CPU core** for
  lock-free parallelism. They carry Read/Write/Flush/Compare. This is where `fio` traffic
  goes.

```text
   HOST MEMORY                         CONTROLLER (the SSD)
  +-----------+   doorbell write   +----------------------+
  | Admin SQ  | -----------------> |  fetches cmd via DMA |
  | I/O SQ x N|                    |  executes on NAND    |
  +-----------+                    |  DMAs data to/from   |
  | Admin CQ  | <-- MSI-X IRQ ---- |  writes completion   |
  | I/O CQ x N|                    +----------------------+
  +-----------+
        ^  PCIe (Gen3 x4 ~3.5 GB/s, Gen4 x4 ~7 GB/s, Gen5 x4 ~14 GB/s)
```

**Controller vs namespace** (the distinction that bites people in test):

- A **controller** (`/dev/nvme0`) is the NVMe hardware — one chip per SSD, usually. It
  handles command processing, wear leveling, Error-Correcting Code (ECC) on the NAND, and the PCIe interface.
- A **namespace** (`/dev/nvme0n1`) is a logical collection of LBA blocks under that
  controller — like a partition, but at the controller level, with its own block-address
  range and LBA format (block size + metadata). One controller can host **multiple**
  namespaces, each appearing as a separate block device (`/dev/nvme0n1`, `/dev/nvme0n2`).
- **The test trap:** `nvme list` enumerates **namespaces**, not controllers. A drive whose
  controller is perfectly alive but has **no namespace configured** shows up in `lspci` and
  in `/dev/nvme0` but **not** as `/dev/nvme0n1` and **not** in `nvme list`. Some drives ship
  un-provisioned exactly this way. Your provisioning step may have to *create* the namespace
  before any data test can run (see Manufacturing Flows below). "Drive is dead" is the wrong
  conclusion; "drive has no namespace yet" is often the right one.

M.2, U.2/U.3, and EDSFF (E1.S/E1.L/E3) are **physical form factors, not protocols** — they
all speak NVMe over PCIe. The form factor matters for *cooling* (and therefore throttling),
for hot-swap (U.2/U.3 and EDSFF are hot-swappable — your test may need to verify hot-insert),
and for the connector you are qualifying. M.2's poorer thermal path makes thermal throttling
a far more common finding there; Zoox's server-grade assemblies more likely use U.2/U.3 or
EDSFF with a real heatsink and airflow.

### Admin vs I/O command sets, and Identify

NVMe splits commands into two sets, matching the two queue types:

| Set | Where it runs | Representative commands |
|---|---|---|
| **Admin** | Admin queue (SQ0/CQ0) | Identify, Get Log Page, Get/Set Feature, Format NVM, Firmware Download/Commit, DST, Create/Delete I/O SQ/CQ, Sanitize |
| **NVM I/O** | I/O queues | Read, Write, Flush, Compare, Write Zeroes, Dataset Management (TRIM), Write Uncorrectable |

Two **Identify** commands anchor every qualification because they are how you confirm you
are testing the part you think you are, and what it is *capable* of:

```bash
nvme id-ctrl /dev/nvme0 -o json     # Identify Controller (CNS 0x01)
# Key fields:
#   mn   model number          sn  serial number       fr  firmware revision
#   vid  PCI vendor ID         oacs optional admin cmds supported (bit 4 = DST supported)
#   wctemp / cctemp            warning / critical composite temperature thresholds (Kelvin)
#   tnvmcap                    total NVM capacity (bytes)
#   sanicap                    sanitize capabilities (which erase types are supported)

nvme id-ns /dev/nvme0n1 -o json     # Identify Namespace (CNS 0x00)
# Key fields:
#   nsze / ncap / nuse         namespace size / capacity / utilization (in LBAs)
#   flbas                      formatted LBA size index -> which lbaf is active
#   lbaf[]                     the LBA-format table: ds = log2(block size), ms = metadata bytes
#   nsfeat, dpc, dps           thin-provisioning / protection-information capabilities
```

`id-ctrl` gives you model/serial/FW (traceability — every test record must capture these),
the **temperature thresholds** the drive throttles against (`wctemp`/`cctemp`, in Kelvin),
and the **OACS** bitmap that tells you whether DST is even supported (bit 4).
`id-ns` gives you the namespace size and the **active LBA format** — which is how you verify
a drive was provisioned to 512 B vs 4 KB blocks (a provisioning mistake that silently
changes performance and capacity).

### SMART / Health — your primary pass/fail gate (log page 0x02)

`nvme smart-log /dev/nvme0 -o json` is the single most important command in NVMe test. It
reads **Log Identifier 0x02**, the SMART / Health Information log. Unlike the rest of this
chapter, the byte offsets here are worth knowing cold, because in a pinch you can decode the
log from a raw `get-log` dump when the JSON parser hiccups on a vendor field. Offsets are
into the 512-byte log page:

| Offset | Bytes | Field | New-drive expectation | Why it matters |
|---|---|---|---|---|
| 0x00 | 1 | **Critical Warning** (bitmap) | **0** | Any bit set = drive is telling you it is failing |
| 0x01 | 2 | **Composite Temperature** (Kelvin) | within spec (often 0-70 C) | Out of range during test = thermal/airflow problem |
| 0x03 | 1 | **Available Spare** (%) | **100** | Spare blocks remaining; declines as flash wears |
| 0x04 | 1 | **Available Spare Threshold** (%) | below current spare | If spare drops below this, Critical-Warning bit 0 trips |
| 0x05 | 1 | **Percentage Used** (%, can exceed 100) | **0** | Wear indicator; non-zero on a "new" drive = used stock |
| 0x20 | 16 | **Data Units Read** (x1000 x 512 B) | small/reasonable | Lifetime reads; large on "new" = drive has history |
| 0x30 | 16 | **Data Units Written** | small/reasonable | Lifetime writes; the endurance-burn signal |
| 0x40 | 16 | **Host Read Commands** | small | Lifetime read-command count (the count behind DUR) |
| 0x50 | 16 | **Host Write Commands** | small | Lifetime write-command count (the count behind DUW) |
| 0x60 | 16 | **Controller Busy Time** (min) | small | Minutes the controller had I/O outstanding |
| 0x70 | 16 | **Power Cycles** | low (single digits) | Re-stock signal |
| 0x80 | 16 | **Power On Hours** | single-digit hours | **The re-stock detector** (see below) |
| 0x90 | 16 | **Unsafe Shutdowns** | typically 0 | Context for field returns / power-path issues |
| 0xA0 | 16 | **Media and Data Integrity Errors** | **0** | Uncorrectable media errors -> bad NAND. ANY = fail |
| 0xB0 | 16 | **Number of Error Information Log Entries** | **0** (or known-benign) | Total entries in the 0x01 error log |
| 0xC0 | 4 | **Warning Composite Temp Time** (min) | **0** | Minutes spent at/above WCTEMP (but below CCTEMP); nonzero = it throttled |
| 0xC4 | 4 | **Critical Composite Temp Time** (min) | **0** | Minutes above CCTEMP; a serious thermal finding |
| 0xC8 | 16 | **Temperature Sensor 1..8** (Kelvin, 2 B ea) | within spec | Per-sensor temps; a 0 entry = sensor not implemented |
| 0xD8 | 4 | **Thermal Mgmt Temp 1 Transition Count** | **0** | Nonzero = drive hit the light-throttle setpoint (TMT1) |
| 0xDC | 4 | **Thermal Mgmt Temp 2 Transition Count** | **0** | Nonzero = drive hit the heavy-throttle setpoint (TMT2) |
| 0xE0 | 4 | **Total Time For Thermal Mgmt Temp 1** (sec) | **0** | Seconds spent in TMT1 throttle |
| 0xE4 | 4 | **Total Time For Thermal Mgmt Temp 2** (sec) | **0** | Seconds spent in TMT2 throttle |

These offsets are from the SMART / Health Information Log layout in the NVMe Base
Specification (mirrored field-for-field by libnvme's `struct nvme_smart_log` and Microsoft's
`NVME_HEALTH_INFO_LOG`). The 16-byte lifetime counters (offsets 0x20 through 0xB7) are
little-endian 128-bit values; `nvme-cli` parses them for you. Note the field order on the
wire is **Power Cycles (0x70) then Power On Hours (0x80) then Unsafe Shutdowns (0x90) then
Media Errors (0xA0)** — a common mistake is to assume Media Errors sits low in the page; it
does not. When in doubt, key off the JSON field *name* (`media_errors`, `power_on_hours`,
`thm_temp1_trans_count`), not a hand-counted offset.

**Critical Warning** is a bitmap; **any** set bit fails a new drive. Decode it:

| Bit | Meaning |
|---|---|
| 0 | Available spare has fallen below the threshold |
| 1 | Composite temperature exceeded a threshold (WCTEMP/CCTEMP) |
| 2 | NVM subsystem reliability degraded (excessive media errors / internal) |
| 3 | Media placed in **read-only** mode (the drive gave up on writes) |
| 4 | Volatile-memory backup device failed (the capacitor that flushes the write cache) |
| 5 | Persistent-memory region became read-only / unreliable (if present) |

The new-drive manufacturing limits, stated as a gate, and exactly what the toolkit's
`nvme.py` enforces in `_apply_limits()`:

| Check | Limit | Rationale |
|---|---|---|
| `critical_warning == 0` | hard fail if any bit | the drive's own self-assessment |
| `media_errors == 0` | hard fail | a brand-new drive has touched no bad NAND |
| `num_err_log_entries == 0` | fail / investigate | clean error history expected |
| `percentage_used < 2` | fail above | wear; ~0 on new |
| `available_spare >= 100` | fail below | full spare pool on new |
| `0 < temperature <= 70` C | fail outside | in spec, and *nonzero* (a 0 reads as a sensor fault) |
| `power_on_hours <= 50` | fail above | **re-stock / used-stock detector** |

> **The used-vs-new signal — a quality finding, not a drive fault.** `percentage_used`,
> `data_units_written`, **`power_on_hours`**, and **`power_cycles`** that are non-zero on a
> drive that is supposed to be new is how you catch **re-labeled or returned ("re-stock")
> drives entering your line.** A "new" drive with 6,200 power-on-hours and 800 power cycles
> is used inventory — a supply-chain/quality problem, not a defective part. The toolkit
> treats these correctly: it **gates** on `power_on_hours` (the cleanest single signal) but
> reports `power_cycles`, `unsafe_shutdowns`, and large lifetime writes as **history flags**
> (`_history()`), so the fleet data can flag a bad lot without false-failing every drive
> with a benign power cycle. Log these *even when they pass* — genealogy is how Quality
> finds a contaminated lot before it spreads.

> **The already-throttling signal.** Any nonzero `thm_temp1/2_trans_count`,
> `warning_temp_time`, or `critical_comp_time` on a new drive means the drive **throttled
> during your own test** — that is a cooling/airflow/heatsink-mount problem on *your* fixture
> or the module, not (necessarily) a drive defect. These are the difference between "the
> drive is bad" and "your thermal solution is bad," and only the module-level soak surfaces
> them. They are the high-value adds beyond a bare `critical_warning==0` check.

The default (non-JSON) `nvme smart-log` print is what you eyeball at the bench; it names
every field, so it doubles as a decoder ring for the offsets above:

```text
Smart Log for NVME device:nvme0 namespace-id:ffffffff
critical_warning                        : 0
temperature                             : 41 C (314 Kelvin)
available_spare                         : 100%
available_spare_threshold               : 10%
percentage_used                         : 0%
data_units_read                         : 5,678 (2.90 GB)
data_units_written                      : 1,234 (631 MB)
host_read_commands                      : 88,142
host_write_commands                     : 41,003
controller_busy_time                    : 0
power_cycles                            : 3
power_on_hours                          : 1
unsafe_shutdowns                        : 0
media_errors                            : 0
num_err_log_entries                     : 0
Warning Temperature Time                : 0
Critical Composite Temperature Time     : 0
Temperature Sensor 1                    : 41 C (314 Kelvin)
Temperature Sensor 2                    : 44 C (317 Kelvin)
Thermal Management T1 Trans Count       : 0
Thermal Management T2 Trans Count       : 0
Thermal Management T1 Total Time        : 0
Thermal Management T2 Total Time        : 0
```

Newer `nvme-cli` prints the human-readable temperature for you (the `41 C (314 Kelvin)`
form); older versions print only the Kelvin integer, which is the version skew the toolkit's
"subtract 273 if it looks like Kelvin" guard exists to absorb. When you script, take the
JSON, not this text — the labels and number formatting shift across `nvme-cli` releases.

### The other logs that a credible qualification reads (Get Log Page)

`smart-log` is one log page. A real NVMe qualification reads several, via `nvme get-log`
or the dedicated subcommands. The Get-Log-Page surface:

| LID | Log | What it gives you | Subcommand |
|---|---|---|---|
| 0x01 | **Error Information** | Ring of recent command errors: status code, command ID, **LBA**, namespace ID, error count — far more detail than SMART's `num_err_log_entries` summary | `nvme error-log` |
| 0x02 | **SMART / Health** | The core health page above | `nvme smart-log` |
| 0x03 | **Firmware Slot Info** | Active slot + next slot + per-slot revision strings | `nvme fw-log` |
| 0x06 | **DST** | Result of the last self-tests (up to 20 entries) + current-operation % | `nvme self-test-log` |
| 0x07 | **Telemetry Host-Initiated** | Vendor binary blob for FA/RMA, triggered by the host | `nvme telemetry-log` |
| 0x08 | **Telemetry Controller-Initiated** | Vendor blob the controller captured on its own (e.g., at an internal fault) | `nvme telemetry-log` |
| 0x0D | **Persistent Event Log** | **Non-volatile, cross-power-cycle history**: power cycles, thermal excursions, firmware changes, error bursts — the richest field-return artifact | `nvme persistent-event-log` |

**The Error Information Log (0x01)** is the detail behind the SMART summary. Where SMART
says "7 error log entries," the error log says *what* those 7 errors were — status code, the
LBA involved, the command that triggered it. On a failing drive that is the difference
between "errors exist" and "writes to LBA 0x4A1B00 are returning a media error," which is an
actionable defect description. A real `nvme error-log` entry on a drive with a media fault:

```text
Error Log Entries for device:nvme0 entries:1
.................
 Entry[ 0]
.................
error_count     : 41
sqid            : 3
cmdid           : 0x1a
status_field    : 0x2002(INVALID_FIELD: A reserved coded value or an unsupported value in a defined field)
phase_tag       : 0
parm_err_loc    : 0xffff
lba             : 0x4a1b00
nsid            : 0x1
vs              : 0
trtype          : The transport type is not indicated or the error is not transport related.
cs              : 0
.................
```

The fields that matter for triage: `status_field` (the NVMe status code — bit 15.. is the
phase tag, the low bits are the SCT/SC status-code-type and status-code; a media error shows
up as SCT 0x2 with codes like 0x81 unrecovered-read-error / 0x80 write-fault), `lba` (the
block that faulted — feed it back to `fio`/`dd` to confirm it is reproducible), and
`error_count` (which is the *running* error counter, not the entry index). Decode the status
code against the NVMe spec status tables, or let `nvme-cli` print the parenthetical for you.

**Telemetry (0x07/0x08)** is a vendor-defined binary dump — you do not parse it on the
line; you **capture it on a failure** and hand it to the SSD vendor's FA team. Capturing it
costs you nothing and is the single thing the vendor will ask for on an Return Merchandise Authorization (RMA).

> **The non-volatile logs are the RMA story.** SMART resets some context across power
> cycles and a `format`/`sanitize` can clear logs entirely. The **Persistent Event Log
> (0x0D)** and **Error Information Log (0x01)** carry the history a fresh SMART page hides —
> prior thermal excursions, firmware changes, error bursts. **Capture both (plus
> `telemetry-log`) into the test record on any failure, and capture them *before* any erase**
> — a sanitize on some drives wipes the logs you would have wanted. The non-volatile history,
> not the moment-of-test snapshot, is what lets Quality and the vendor root-cause a return.

### Device Self-Test (DST) — free coverage, with one fatal gotcha

The controller can run its own internal diagnostic — a read/verify pass across the media
plus internal structural checks — and report pass/fail. It is **coverage you did not have to
write**, which makes it valuable. Two modes:

- **Short** (`-s 1`): a few minutes, a bounded sample of the media + internal checks. Run
  this on every unit.
- **Extended** (`-s 2`): tens of minutes to hours, a full-media pass. Reserve for burn-in /
  reliability sampling, not per-unit takt time.

```bash
nvme device-self-test /dev/nvme0 -s 1     # START a short self-test
nvme device-self-test /dev/nvme0 -s 2     # START an extended self-test
nvme self-test-log    /dev/nvme0 -o json  # POLL: percent complete + pass/fail result code
```

> **DST is non-blocking — this is the trap.** `nvme device-self-test ... -s 1` only
> *starts* the test and returns immediately. It does **not** wait, and it does **not** return
> the result. You **must then poll log page 0x06** (`nvme self-test-log`) for the
> completion percentage and the **result code of the latest entry** (0 = passed; nonzero =
> a failure code per the NVMe spec). A test that fires a DST and never reads 0x06 has
> proven *nothing* — it gives false assurance. The right pattern is: **start a short DST
> early, run your other tests (fio, link checks) in parallel, then read the 0x06 result at
> the end.** The toolkit does exactly this — `start_self_test()` kicks it off,
> `poll_self_test()` polls `self_test_log()` on an interval until `in_progress` clears or a
> timeout fires, and only then reads the result code; the gate is `result == 0`.

Real `nvme self-test-log` output mid-test and after a clean pass:

```text
Device Self Test Log for NVME device:nvme0
Current operation  : 0x1            <- 0x1 = short in progress (0x0 = none, 0x2 = extended)
Current Completion : 60%            <- poll this until Current operation returns to 0x0
Self Test Result[0]:
  Operation Result             : 0x0     <- 0x0 = completed without error; this is the gate
  Self Test Code               : 0x1     <- which test produced this entry (short)
  Power on hours (POH)         : 0x1
  Vendor Specific              : 0 0
```

The `Operation Result` value is a 4-bit code, and only the exact values matter — do not
treat "nonzero" uniformly, because some nonzero codes are *aborts* (inconclusive, re-run)
while others are genuine *failures* (hard fail + telemetry). The NVMe-spec codes:

| Code | Meaning | Verdict |
|---|---|---|
| 0x0 | Completed without error | **pass** (this is the gate) |
| 0x1 | Aborted by a Device Self-test command | inconclusive — re-run |
| 0x2 | Aborted by a Controller-Level Reset | inconclusive — re-run |
| 0x3 | Aborted, a namespace was removed | inconclusive — re-run |
| 0x4 | Aborted by a Format NVM command | inconclusive — re-run |
| 0x5 | A fatal or unknown error occurred during the test | **fail** + telemetry |
| 0x6 | Completed, failed, segment that failed not known | **fail** + telemetry |
| 0x7 | Completed, one or more **segments failed** | **fail** + telemetry (read `SegmentNumber` + `FailingLBA`) |
| 0x8 | Aborted for an unknown reason | inconclusive — re-run |
| 0x9 | Aborted due to a Sanitize operation | inconclusive — re-run |
| 0xF | Entry not used (no self-test result here yet) | not a result |

So the gate is exactly `result == 0x0`, and `0x5`/`0x6`/`0x7` are the real failures (capture
telemetry; for `0x7` the entry also carries the `SegmentNumber` and `FailingLBA`). The
`Current operation` field reads `0x1`/`0x2` while a short/extended test runs and returns to
`0x0` when done — poll that back to `0x0` before you trust `Self Test Result[0]`, or you will
read a stale prior result.

### nvme-cli usage with representative output

```bash
nvme list                                 # all namespaces: node, model, serial, FW, size
nvme list -o json                         # machine-readable (script against this, not text)
nvme id-ctrl /dev/nvme0 -o json           # controller identify
nvme id-ns   /dev/nvme0n1 -o json         # namespace identify
nvme smart-log /dev/nvme0 -o json         # the health gate
nvme error-log /dev/nvme0 -o json         # recent command errors
nvme fw-log   /dev/nvme0                   # firmware slots + active slot
nvme get-feature /dev/nvme0 -f 0x04 -H    # Temperature Threshold feature (over/under, per sensor), human-readable
nvme self-test-log /dev/nvme0 -o json     # DST result
nvme telemetry-log /dev/nvme0 -o telem.bin  # capture telemetry blob (on failure)
```

Representative `nvme list` output on a healthy module:

```text
Node          SN          Model                 Namespace Usage              Format   FW Rev
------------- ----------- --------------------- --------- ------------------ -------- --------
/dev/nvme0n1  S5GXNX0R    Zoox-Logging-3.84TB           1   0.00 B / 3.84 TB   512 B    ZX2.14
```

Representative trimmed `nvme smart-log -o json` (the fields the gate reads):

```json
{
  "critical_warning": 0,
  "temperature": 314,
  "avail_spare": 100,
  "spare_thresh": 10,
  "percent_used": 0,
  "data_units_read": 5678,
  "data_units_written": 1234,
  "media_errors": 0,
  "num_err_log_entries": 0,
  "warning_temp_time": 0,
  "critical_comp_time": 0,
  "thm_temp1_trans_count": 0,
  "thm_temp2_trans_count": 0,
  "power_on_hours": 1,
  "power_cycles": 3,
  "unsafe_shutdowns": 0
}
```

Note `temperature: 314` — that is **Kelvin** (314 - 273 = 41 C; the exact relation is
C = K - 273.15, but the NVMe field is an integer Kelvin count so a flat -273 is what every
tool uses). The composite temperature and all eight `temperature_sensor` fields are reported
in Kelvin per the spec; your code must convert. The toolkit handles this defensively: if the
parsed temperature is `> 200` it subtracts 273, which covers the Kelvin-vs-Celsius ambiguity
across `nvme-cli` versions (some print the converted Celsius, some the raw Kelvin) without
guessing wrong on a real 41 C reading.

### PCIe-attach implications (point to the PCIe chapter)

An NVMe drive is a PCIe endpoint, full stop. The whole PCIe chapter — Link Training and Status State Machine (LTSSM), link
train/width, AER correctable/uncorrectable decode, the write-1-to-clear arm/stress/read
discipline, lane margining, retrain counting — applies to its link **before** any storage
test is meaningful. Concretely, on the floor:

- **Verify the link first.** A logging drive should be at, say, Gen4 x4. Find its PCIe BDF
  from sysfs and read `current_link_speed` / `current_link_width`. A drive that trained
  Gen4→Gen3 or x4→x2 is a *PCIe* finding (SI, seating, bifurcation), and no amount of SMART
  reading will explain it.
- **Watch its AER counters across the soak.** Bad-TLP / Replay-Timer correctables climbing
  during a write soak point at physical-layer Signal Integrity (SI) on the M.2/U.2 connector or trace —
  identical to the PCIe-chapter triage, just on a drive.
- **"Drive disappeared mid-test"** can be **Downstream Port Containment (DPC)** (DPC) on the root
  port firing on a fatal error, a surprise-down, or a power glitch — read it the way the
  PCIe chapter says, not as "the SSD died."

The toolkit's NVMe check is explicitly documented to run **alongside** the PCIe diagnostic
on the drive's link — the storage health check and the link check are two halves of one
qualification.

### Stress and data integrity with `fio`

SMART tells you the drive's *opinion of itself*; `fio` makes you form your own. These are
the recipes you keep on the bench:

```bash
# Sequential WRITE throughput - does it hit rated BW, and does it throttle/heat under sustain?
fio --name=seqwrite --filename=/dev/nvme0n1 --rw=write --bs=128k \
    --iodepth=32 --numjobs=1 --direct=1 --runtime=120 --time_based --group_reporting

# Sequential READ bandwidth
fio --name=seqread --filename=/dev/nvme0n1 --rw=read --bs=128k \
    --iodepth=32 --numjobs=1 --direct=1 --runtime=30 --time_based --group_reporting

# Random 4K READ IOPS - the queue-depth / parallelism test (high iodepth, many jobs)
fio --name=randread --filename=/dev/nvme0n1 --rw=randread --bs=4k \
    --iodepth=256 --numjobs=4 --direct=1 --runtime=120 --time_based --group_reporting

# Mixed 70/30 read/write - a realistic logging-ish workload
fio --name=mixed --filename=/dev/nvme0n1 --rw=randrw --rwmixread=70 --bs=4k \
    --iodepth=64 --numjobs=4 --direct=1 --runtime=300 --time_based --group_reporting

# DATA INTEGRITY - write a known pattern, read it back, verify every byte
fio --name=verify --filename=/dev/nvme0n1 --rw=write --bs=64k --direct=1 \
    --verify=crc32c --verify_fatal=1 --do_verify=1 --size=4G

# Sustained ENDURANCE write (burn-in; large size, time-based)
fio --name=endurance --filename=/dev/nvme0n1 --rw=write --bs=128k \
    --iodepth=32 --numjobs=1 --direct=1 --size=100G --runtime=3600 --time_based
```

`--direct=1` bypasses the page cache so you measure the **drive**, not host RAM —
non-negotiable for a real measurement. `--verify=crc32c` is the integrity test that catches
silent data corruption: write a CRC-tagged pattern, read it back, compare; `--verify_fatal=1`
aborts on the first miscompare so a corruption can't hide in a sea of good blocks.

**What to watch *during* the soak**, not just after:

- **Throughput holding steady.** A drive that does rated BW for 10 s then *halves it* hit
  the thermal wall — that is throttling, and only a module-level soak (not a 10-second Printed Circuit Board Assembly (PCBA)
  check) catches it.
- **Composite temperature vs the thresholds.** Poll `nvme smart-log` temperature against
  `wctemp`/`cctemp`. TMT1 triggers light throttle; TMT2 heavier. A throttle event is a
  finding about your **airflow/heatsink**, recorded via the thermal transition counters above.
- **AER on its PCIe link** — because it is a PCIe device.

```bash
# Live temperature monitor during a soak
watch -n 1 'nvme smart-log /dev/nvme0 -o json | jq ".temperature - 273"'
```

> **Destructive-test discipline — treat this like a loaded tool.** Writing to
> `/dev/nvme0n1`, `nvme format`, and `nvme sanitize` **destroy data irrecoverably.** On the
> line that is fine — the drive is blank. On any shared or development machine it is a
> disaster, and "I ran the wrong device node" has wiped engineers' boot drives. Every
> destructive step in your toolkit must (a) require an explicit "yes, this exact device, I
> mean it" confirmation, and (b) refuse to run against a **mounted** filesystem or the
> **boot** drive. Build the guard once and never bypass it.

### Failure signatures → root cause

| Symptom | Most likely cause | First moves |
|---|---|---|
| In `lspci` but not in `nvme list` (no `/dev/nvme0n1`) | **No namespace provisioned**, or controller init failed | `ls /dev/nvme*` (is `/dev/nvme0` there?); `nvme id-ctrl /dev/nvme0`; `nvme list-ns /dev/nvme0`; create namespace if none |
| Trained below expected speed/width (Gen4->Gen3, x4->x2) | **PCIe SI / seating / bifurcation** — a link problem | PCIe chapter: link speed/width, lane margining, reseat; *not* a SMART issue |
| `critical_warning != 0` | Drive self-reports failing (spare/temp/read-only/backup) | Decode the bit; read SMART; read error-log 0x01; usually RMA |
| `media_errors > 0` or rising | Bad NAND / uncorrectable media | Read error-log 0x01 for the LBAs; fail; capture telemetry |
| Namespace went **read-only** (Critical-Warning bit 3) | Spare exhausted, or controller protective lockdown | Hard fail / RMA; the drive stopped accepting writes to protect data |
| Throughput cliff mid-soak; `thm_temp*_trans_count` rising | **Thermal throttling** — airflow/heatsink/mount | Fix cooling on the fixture/module; *not* inherently a bad drive |
| **Controller timeout / reset** in `dmesg` (`nvme nvme0: I/O timeout`, `resetting controller`) | Firmware hang, severe thermal, or a dying drive; sometimes a PCIe link event | `dmesg | grep nvme`; check link/AER; check temperature; reproduce; if persistent, RMA |
| `power_on_hours`/`power_cycles` high on a "new" drive | **Re-stock / returned inventory** | Quality/supply-chain finding; flag the lot; not a drive defect |
| DST `result != 0` | Controller's own diagnostic failed | Read the result code; fail; capture logs + telemetry for FA |

The `dmesg` lines worth recognizing on sight:

```text
nvme nvme0: I/O 384 QID 3 timeout, aborting
nvme nvme0: Abort status: 0x0
nvme nvme0: I/O 384 QID 3 timeout, reset controller
nvme nvme0: 16/0/0 default/read/poll queues
```

A single timeout under a brutal soak can be a fluke; **repeated** timeouts or a controller
reset is a failing unit (or a link that keeps dropping the drive — check AER/DPC first).

### Firmware update flow (you will own this)

"Release updated test programs for new generations of hardware" includes flashing drive
firmware on the line:

```bash
nvme fw-download /dev/nvme0 --fw=image.bin       # stage the image (chunked to the controller)
nvme fw-commit   /dev/nvme0 --slot=1 --action=1  # commit to slot 1, activate on next reset
# then a controller reset (or power cycle) to run it; re-read fw-log to CONFIRM the active slot
nvme fw-log /dev/nvme0
```

The gotchas: the **commit action code** matters (download-only vs activate-on-reset vs
activate-immediately), and some drives need a **full power cycle**, not just a controller
reset, to run the new image. Your test must confirm the new version is *running* —
`fw-log` shows the new revision in the **active slot** — not merely that it downloaded.
Record the firmware version in the test result for traceability (a firmware delta means you
re-qualify the test).

### Manufacturing-test flow (provision → test → soak → gate)

The end-to-end NVMe flow at module test, in order:

1. **Enumerate & identify.** `nvme list` — drive present, correct model/FW? If it's in
   `lspci` but not here, **provision a namespace** (`nvme create-ns ... --nsze ... --ncap ...
   --flbas 0`, then `nvme attach-ns`) — un-provisioned drives are common and this is a
   *step*, not a failure.
2. **Verify the PCIe link.** Expected speed/width (Gen4 x4 etc.); AER clean. (PCIe chapter.)
3. **Format / secure-erase to a known state.** `nvme format /dev/nvme0n1 --ses=1` (secure
   erase) or a **sanitize** for a stronger crypto/block erase — establishes a clean,
   known-LBA-format starting point and removes any factory test data. **Destructive — gated.**
4. **SMART gate (baseline).** `critical_warning==0`, `media_errors==0`, `percentage_used~0`,
   `available_spare==100`, temperature in range — and the **re-stock check**
   (`power_on_hours`/`power_cycles` low). Capture model/serial/FW for traceability.
5. **Start a short DST** in the background (it runs while step 6 runs).
6. **Performance + integrity.** `fio` sequential R/W (must meet the **datasheet BW limit**),
   random 4K IOPS (must meet the **IOPS limit**), and a CRC verify pass. These limits come
   from the datasheet for go/no-go and from *your fleet data* for tightening (capture the
   numbers, not just pass/fail).
7. **Soak (module/burn-in).** Sustained write at temperature; watch for the throughput
   cliff and the thermal transition counters — this is the phase that catches throttling and
   marginal drives that a room-temp PCBA check passes.
8. **Read the DST result** (poll log 0x06; `result==0`).
9. **Post-stress SMART.** Re-read: **no new** `media_errors`, no new error-log entries, no
   new thermal transitions, `available_spare` unchanged. A delta here is the real catch.
10. **Capture logs.** On any failure, dump error-log (0x01), persistent-event-log (0x0D),
    and telemetry (0x07) **before** any erase, into the test record. On pass, still log the
    lifetime counters for genealogy.

**The limits, summarized:** zero `critical_warning`, zero `media_errors`, zero new error-log
entries, `percentage_used` ~0, `available_spare` 100%, temperature in range with zero
thermal-throttle transitions, `power_on_hours`/`power_cycles` low (re-stock gate), DST
passes, and `fio` BW/IOPS at or above datasheet — every one captured as a number so Design Verification (DV) can
set and Quality can tighten the limit later.

### NVMe health check in Python (toolkit cross-reference)

The toolkit's `computetest.nvme` module implements this gate. The shape is the same
"capture the parameter, then compare to a limit" pattern used across the toolkit:

```python
from computetest.nvme import check_nvme

# Reads SMART + Identify, applies new-drive limits, optionally runs + polls a DST.
h = check_nvme("/dev/nvme0", max_temp_c=70, max_power_on_hours=50, run_self_test=True)

print(h.summary())
# /dev/nvme0 Zoox-Logging-3.84TB fw=ZX2.14 temp=41C used=0% media_err=0 poh=1 -> OK  dst=pass

if not h.ok:
    failed = [k for k, ok in h.checks.items() if not ok]   # e.g. ['media_errors==0']
    # capture logs + telemetry, then fail the unit

for flag in h.history:        # used-stock / RMA-history flags that did NOT fail the gate
    log.warning("NVMe history: %s", flag)   # e.g. "unsafe_shutdowns=40"
```

Two design points worth lifting from that module into any NVMe test you write:

- **Separate faults from history.** `checks` (faults) drive pass/fail; `history` (high
  lifetime counters) is logged for fleet/quality but does **not** auto-fail a drive that is
  functionally fine — except the one `power_on_hours` gate, which is the cleanest re-stock
  signal. Gating the pass on lifetime counters that a legitimately power-cycled good drive
  accrues would false-fail good parts.
- **DST is polled to completion, never fire-and-forget.** `run_self_test=True` calls
  `start_self_test()` then `poll_self_test()`, and only the polled `result == 0` sets the
  `self_test_passed` check. This is the antidote to the non-blocking gotcha above.
