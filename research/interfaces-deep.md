# Non-PCIe Interfaces & Devices — Deep Reference for a Zoox Compute Test Engineer

Manufacturing test, diagnostics, and root-cause for an autonomous-vehicle (AV) compute
platform. Each section covers: **protocol essentials → real automotive part numbers → Linux
tools → key failure modes → how to test in manufacturing (and the diagnostic angle).**

> Scope note: PCIe link/AER/BERT/margining is covered by the existing toolkit
> (`backend.py`, `aer.py`, `bert.py`, `margining.py`, `linkstate.py`). This document covers the
> *endpoints and other buses* a Compute Test Engineer touches: **NVMe, NVIDIA GPUs, GMSL
> cameras, automotive Ethernet, CAN/CAN-FD, and DDR/EDAC memory.** An NVMe drive and a GPU are
> *also* PCIe endpoints, so the PCIe diagnostic always runs on their links too.

Citations are inline as footnote-style links and consolidated under **Sources** at the end.

---

## Table of Contents
1. [NVMe](#1-nvme)
2. [NVIDIA GPUs](#2-nvidia-gpus)
3. [GMSL Cameras](#3-gmsl-cameras)
4. [Automotive Ethernet](#4-automotive-ethernet)
5. [CAN / CAN-FD](#5-can--can-fd)
6. [DDR / Memory & EDAC](#6-ddr--memory--edac)
7. [Recommendations: concrete toolkit improvements](#7-recommendations--concrete-toolkit-improvements)

---

## 1. NVMe

### 1.1 Protocol essentials

**Queue model.** NVMe is built on **paired Submission Queues (SQ) and Completion Queues (CQ)**.
The host rings a doorbell to submit; the controller posts completions. At reset only the
**Admin queue pair (QID 0)** exists — used for management (create/delete I/O queues, Identify,
Get Log Page, format, firmware, etc.). I/O queues (QID ≥ 1) are then created for data. NVMe
supports up to 64K queues × 64K entries, and *multiple* SQs may map to one CQ. [[NVMe arch]](https://blogs.oracle.com/linux/overview-of-nvme-architecture) [[OSDev]](https://wiki.osdev.org/NVMe)

**Namespaces.** A **namespace** is a logically isolated range of LBAs, addressed by a unique
**Namespace ID (NSID)**. `/dev/nvme0` is the *controller*; `/dev/nvme0n1` is *namespace 1*.
This distinction matters for test commands: **Sanitize targets the controller**, **Format
targets a namespace**. [[NVMe arch]](https://blogs.oracle.com/linux/overview-of-nvme-architecture)

**Identify (CNS field, CDW10 low byte):** `CNS 00h` = Identify Namespace, `CNS 01h` = Identify
Controller, `CNS 02h` = active Namespace list. id-ctrl returns Model (`mn`), Serial (`sn`),
Firmware (`fr`), and capability bits (OACS/ONCS/SANICAP/FNA). [[NVMe 1.2a]](https://www.nvmexpress.org/wp-content/uploads/NVM-Express-1_2a.pdf)

### 1.2 Get Log Page — the diagnostic surface

| LID | Log | What it gives you |
|-----|-----|-------------------|
| `0x01` | **Error Information** | Ring of the most recent error entries (status code, command ID, LBA, NSID). Counts beyond what SMART's `num_err_log_entries` summarizes. |
| `0x02` | **SMART / Health Information** | The core health page (see fields below). Retained across power cycles. [[Microsoft NVME_HEALTH_INFO_LOG]](https://learn.microsoft.com/en-us/windows/win32/api/nvme/ns-nvme-nvme_health_info_log) |
| `0x03` | **Firmware Slot Information** | Active/next firmware slot, per-slot revision strings. |
| `0x06` | **Device Self-Test** | Result of the most recent self-tests (up to 20 entries), plus current-operation %-complete. [[mankier self-test-log]](https://www.mankier.com/2/nvme_self_test_log) |
| `0x07`/`0x08` | **Telemetry (Host / Controller-Initiated)** | Vendor binary blobs for failure-analysis (RMA). Captured with `nvme telemetry-log`. |
| `0x0D` | **Persistent Event Log** | Non-volatile, cross-power-cycle event history (power cycles, thermal excursions, firmware changes, errors). The richest field-return artifact. |

### 1.3 SMART / Health (0x02) — all critical fields + **new-drive** limits

| Field (nvme-cli key) | Meaning | New-drive (manufacturing) limit |
|---|---|---|
| `critical_warning` | Bitmask: bit0 spare<thresh, bit1 temp, bit2 reliability degraded (NVM unreliable), bit3 read-only/media, bit4 volatile-memory-backup failed, bit5 persistent-memory RO. [[Microsoft]](https://learn.microsoft.com/en-us/windows/win32/api/nvme/ns-nvme-nvme_health_info_log) | **== 0** |
| `temperature` | Composite temperature (reported in **Kelvin** by nvme-cli → subtract 273). | `0 < T ≤ ~70 °C` at idle bring-up |
| `available_spare` | Normalized **% (0–100)** of spare blocks remaining. | **== 100** (new drive) |
| `available_spare_threshold` | % at which the spare critical warning fires. | informational (e.g. 10%) |
| `percentage_used` | Vendor estimate of NVM life consumed; 100 = rated endurance reached (may exceed 100). | **< 2** for a new drive |
| `media_errors` (a.k.a. Media and Data Integrity Errors) | Count of **unrecovered** data-integrity errors (uncorrectable ECC, CRC fail, LBA tag mismatch). [[mankier smart-log]](https://www.mankier.com/2/nvme_smart_log) | **== 0** |
| `num_err_log_entries` | Lifetime count of Error Information Log entries. | **== 0** for new |
| `data_units_read` / `data_units_written` | Host R/W in 512KB units ×1000 (endurance / pre-burn check). | low (factory test only) |
| `host_read_commands` / `host_write_commands` | Lifetime command counts. | low |
| `power_cycles`, `power_on_hours` | Lifetime; a **new** drive should show single-digit hours. | low (catches re-used/RMA stock) |
| `unsafe_shutdowns` | Power loss without proper shutdown notification. | typically `0` |
| `warning_temp_time` / `critical_comp_time` | Minutes spent over WCTEMP / CCTEMP. | **== 0** |
| `thm_temp1_trans_count`/`thm_temp2_trans_count` | # transitions into HCTM throttle states 1/2. | **== 0** (no throttling at bring-up) |
| `thm_temp1_total_time`/`thm_temp2_total_time` | seconds in throttle states 1/2. | **== 0** |

### 1.4 Device Self-Test (DST)

- `nvme device-self-test /dev/nvmeX -s 1` → **short** (a few minutes); `-s 2` → **extended**
  (tens of minutes). `-s 0xf` aborts. Action codes: `0h`=show, `1h`=short, `2h`=extended,
  `eh`=vendor, `fh`=abort. [[Debian device-self-test]](https://manpages.debian.org/testing/nvme-cli/nvme-device-self-test.1.en.html)
- It is **non-blocking**: you start it, then **poll log page 0x06** (`nvme self-test-log`) for
  completion % and the **pass/fail result code** of the latest of up to 20 entries.
  [[mankier self-test-log]](https://www.mankier.com/2/nvme_self_test_log)
- DST exercises the drive's *internal* media/controller paths the host can't reach directly —
  high value at manufacturing (catches early NAND/controller defects before fio even runs).

### 1.5 fio recipes (including data-integrity verify)

```bash
# 1) Sequential write throughput (NEVER run R/W tests on a drive with live data)
fio --name=seqwrite --filename=/dev/nvme0n1 --direct=1 --ioengine=libaio \
    --rw=write --bs=128k --iodepth=32 --numjobs=1 --runtime=60 --time_based

# 2) Random read IOPS / latency
fio --name=randread --filename=/dev/nvme0n1 --direct=1 --ioengine=libaio \
    --rw=randread --bs=4k --iodepth=64 --numjobs=4 --runtime=60 --time_based

# 3) DATA-INTEGRITY VERIFY (write a known pattern, read back, check CRC)
fio --name=verify --filename=/dev/nvme0n1 --direct=1 --ioengine=libaio \
    --rw=write --bs=64k --iodepth=16 --size=4G \
    --verify=crc32c --verify_pattern=0xa5 --do_verify=1 --verify_backlog=1m
```
`verify=crc32c` + `do_verify=1` makes fio store a CRC per block and re-read/re-check it; a
mismatch dumps expected vs received and is the canonical *silent-data-corruption* catch.
[[fio basic-verify]](https://github.com/axboe/fio/blob/master/examples/basic-verify.fio)
[[NVMe verify blog]](https://medium.com/@krisiasty/nvme-storage-verification-and-benchmarking-49b026786297)
(Watch out for MDTS limits — overly large blocks can trip false CRC issues on some controllers. [[fio #411]](https://github.com/axboe/fio/issues/411))

### 1.6 Sanitize / Format / Secure-Erase (destructive — gated, never on data drives)

- **Sanitize** (`nvme sanitize /dev/nvme0`): targets the *controller*, also wipes
  over-provisioned/cache, and **resumes after power loss**. Modes: **Block Erase** (`--sanact=2`,
  resets NAND cells, ~1–5 min), **Crypto Erase** (`--sanact=4`, drops the media-encryption key,
  <1 s, requires SED), **Overwrite** (`--sanact=3`). [[tinyapps sanitize]](https://tinyapps.org/docs/nvme-sanitize.html) [[L1T]](https://forum.level1techs.com/t/nvme-crypto-erase-and-sanitize/161587)
- **Format NVM** (`nvme format /dev/nvme0n1`): targets a *namespace*; `--ses=1` user-data-erase,
  `--ses=2` cryptographic-erase; also where you set LBA format / metadata / PI.
- Check support first: `nvme id-ctrl /dev/nvme0 -H | grep -i sanitize` (SANICAP), and `fna` /
  `oncs` for format & crypto-erase support. [[ArchWiki]](https://wiki.archlinux.org/title/Solid_state_drive/Memory_cell_clearing)
- Manufacturing note: a sanitize is a clean way to bring a drive to a known state, **and some
  drives clear the error/SMART logs on sanitize** — capture logs *before* erasing.

### 1.7 Firmware download / commit / activate semantics

1. `nvme fw-download /dev/nvme0 --fw=image.bin` — stages the image into the controller; the
   image is **not applied** by download. [[Debian fw-download]](https://manpages.debian.org/testing/nvme-cli/nvme-fw-download.1.en.html)
2. `nvme fw-commit /dev/nvme0 --slot=N --action=A` — verifies and commits the staged image to a
   slot. **Commit Action (`--action`/`ca`):**
   - `0` = download to slot, **don't** activate.
   - `1` = download to slot, **activate on next reset**.
   - `2` = activate **existing** slot image on next reset.
   - `3` = **activate immediately, no reset**.
   [[mankier fw-commit]](https://www.mankier.com/1/nvme-fw-commit) [[Microsoft activate actions]](https://learn.microsoft.com/en-us/windows/win32/api/nvme/ne-nvme-nvme_firmware_activate_actions)
3. For actions `1`/`2`, complete with a controller reset (`nvme reset /dev/nvme0`, or CC.EN
   1→0 / FLR). Verify post-update with `nvme fw-log` (slot info, LID 0x03) and `id-ctrl` `fr`.

### 1.8 Form factors

| Form factor | Typical use | Notes |
|---|---|---|
| **M.2** (2280/22110) | small/edge compute, boot | edge connector; thermal-limited, often needs a heatsink |
| **U.2** (SFF-8639) | enterprise 2.5", hot-swap | 4 PCIe lanes over a cabled backplane |
| **U.3** (SFF-TA-1001) | tri-mode bays | SAS/SATA/NVMe on one connector |
| **EDSFF** (E1.S, E1.L, E3.S) | dense servers / AV compute | better thermals & hot-swap; E1.S replacing M.2 in racks |

### 1.9 Thermal throttling — HCTM (TMT1/TMT2) & temperature thresholds

**Host Controlled Thermal Management (HCTM)** lets the host program two thresholds on
*Composite Temperature*: **TMT1** (light throttle: drop to lower active power states with minimal
perf impact) and **TMT2** (heavy throttle: drop power states regardless of perf). The drive
self-throttles between them: `TMT1 ≤ T < TMT2` → light, `T ≥ TMT2` → heavy. [[Semiconductor Nerds]](https://semiconductor-nerds.com/can-your-host-damage-if-storage-device-gets-too-heated-hctm-is-there-to-rescue/) [[Hagiwara]](https://www.hagisol.com/techblog/?p=635)
Typical defaults ~80 °C (TMT1) / ~85 °C (TMT2), vendor-specific. **HCTMA** = the
HCTM *attributes* capability advertised in Identify Controller. Set via
`nvme set-feature /dev/nvme0 -f 0x10 -v <TMT2_K<<16 | TMT1_K>>`. Separately, **WCTEMP** (warning)
and **CCTEMP** (critical) composite-temperature thresholds drive the SMART warning/critical timers.
*Manufacturing relevance:* any nonzero `thm_temp1/2_trans_count` or `warning_temp_time` at bring-up
means the drive is **already throttling** → thermal solution / airflow / mounting problem.

### 1.10 NVMe-over-PCIe link health

Since an NVMe SSD is a PCIe endpoint, run the PCIe diagnostic on its BDF: confirm trained
**Gen/width** (e.g. Gen4 x4), zero **AER** correctable/uncorrectable, no Recovery retrains under
soak, and (Gen4+) lane margining. A drive that *enumerates* but reports media errors under fio
may actually be a **marginal PCIe link** — separate the two by checking AER first.

---

## 2. NVIDIA GPUs

### 2.1 SM architecture basics

A GPU is an array of **Streaming Multiprocessors (SMs)**; each SM has CUDA cores (FP32/INT),
Tensor Cores (matrix math), warp schedulers, register file, shared memory/L1, and (per GPU) an
L2 cache feeding the **GDDR/HBM** device memory over the memory controllers. AV inference GPUs
(e.g. Orin's integrated Ampere GPU, or discrete data-center parts) rely on **ECC-protected DRAM
and SRAM**. The Test Engineer cares less about microarchitecture than about **ECC, thermals,
power, link, and XID fault telemetry**.

### 2.2 ECC: SBE/DBE, volatile vs aggregate, remapping

- **SBE (single-bit error)** — corrected by ECC. A *high SBE rate* is a warning sign (XID 92).
- **DBE (double-bit error)** — **uncorrectable**; data is corrupt. Triggers **XID 48** and
  requires GPU reset/reboot; repeated DBE in device DRAM → RMA. [[XID catalog]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)
- **Volatile vs aggregate** counts: *volatile* resets on driver reload/reboot; *aggregate*
  persists for the life of the GPU (stored in InfoROM). Query both with nvidia-smi.
- **Row remapping (Ampere+) / dynamic page retirement (pre-Ampere):** on an uncorrectable (or
  threshold of correctable) error, the GPU retires/remaps the affected memory row so it's not
  reused. **XID 63** = remap **succeeded** (pending reset to take effect); **XID 64** = remap
  **failed**. Check remapped-row counts and the "remapping pending/failure" flags. [[XID catalog]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html)

### 2.3 XID error codes — the GPU's fault language

| XID | Name | Class / meaning |
|---|---|---|
| **13** | Graphics Engine Exception | Usually **app** (OOB / illegal instr). Rarely HW/driver. Run under `compute-sanitizer`. [[XID catalog]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html) |
| **31** | GPU memory page fault (MMU) | Usually **app** illegal address; can be driver/HW. |
| **43** | GPU stopped processing | SW-induced; app must terminate, GPU stays healthy. |
| **45** | Preemptive cleanup (prior error) | Driver tore down the app after abort/reset/SIGKILL. |
| **48** | **Double-Bit ECC (DBE)** | **Uncorrectable HW** memory error → reset/reboot; check SRAM DBE threshold for RMA. |
| **62** | Internal micro-controller halt | Firmware error → GPU reset. |
| **63** | Memory remapping **event** | Row remap (Ampere+) / page retirement succeeded; reset pending. |
| **64** | Memory remapping **failure** | Remap/retirement **failed** → reset; possible RMA. |
| **74** | **NVLink error** | Link problem between GPUs / NVSwitch; can be HW. |
| **79** | **GPU has fallen off the bus** | GPU inaccessible over PCIe — **often a PCIe link / power / thermal HW failure.** [[XID catalog]](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html) |
| **92** | High single-bit ECC rate | Excessive SBE interrupts (degrading memory). |
| **94** | **Contained** memory error | Isolated to one app; restart just that app. |
| **95** | **Uncontained** memory error | Affects multiple apps → GPU reset. |
| **119 / 120** | GSP RPC timeout / GSP error | GPU System Processor fault → reset. |

XIDs surface in **`dmesg`** / `/var/log/syslog` (`NVRM: Xid (PCI:...): NN, ...`) and via
**DCGM**. In manufacturing, *any* XID 48/63/64/79/92/94/95 during burn-in is a hard fail with a
clear root-cause bucket (memory vs bus vs NVLink). [[NVIDIA debug guidelines]](https://docs.nvidia.com/deploy/gpu-debug-guidelines/index.html)

### 2.4 nvidia-smi query fields (machine-readable)

```bash
nvidia-smi --query-gpu=index,name,pci.bus_id,pcie.link.gen.current,pcie.link.width.current,\
temperature.gpu,temperature.memory,power.draw,enforced.power.limit,\
ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total,\
ecc.errors.corrected.aggregate.total,ecc.errors.uncorrected.aggregate.total,\
retired_pages.single_bit_ecc.count,retired_pages.double_bit.count,retired_pages.pending,\
clocks_throttle_reasons.active,clocks_throttle_reasons.hw_thermal_slowdown,\
clocks_throttle_reasons.sw_thermal_slowdown,clocks_throttle_reasons.hw_power_brake_slowdown,\
pstate,utilization.gpu --format=csv,noheader,nounits -i 0
```
Notes: `pcie.replay.counter` reports PCIe replays (a marginal-link signal). On Ampere+, use
**`nvidia-smi -q -d ROW_REMAPPER`** for remapped rows / pending / failure flags (replaces the old
retired-pages on those parts). `clocks_throttle_reasons.active` is a **bitmask** — decode the
individual reasons (thermal vs power vs sync-boost vs idle) rather than treating any nonzero as
thermal.

### 2.5 DCGM / `dcgmi diag` — the qualification engine

| Level | Flag | What it covers (approx. duration) |
|---|---|---|
| 1 | `dcgmi diag -r 1` | **Quick** deployment/readiness: SW/driver, NVML, basic sanity (~seconds). |
| 2 | `dcgmi diag -r 2` | **Medium**: adds PCIe/NVLink checks, memory bandwidth, integration (~2 min). |
| 3 | `dcgmi diag -r 3` | **Long**: full HW diagnostics + stress — **Memory (Targeted), SM/Targeted Power & Stress, Diagnostics, PCIe** (~several min). The standard qualification run. |
| 4 | `dcgmi diag -r 4` | **Extra-long** (DCGM ≥ 2.4): adds **memtest** (Test0 walking-1s addressing test … pattern tests) and the **Pulse Test** (power-spike PSU stress). |

[[DCGM diag]](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html)
DCGM also exposes health watches (`dcgmi health`) and the field-ID telemetry stream used by
`dcgm-exporter` (Prometheus). For AV burn-in, `-r 3` (or `-r 4` for memtest+pulse) is the
go-to, paired with XID monitoring.

### 2.6 gpu-burn

`gpu-burn` runs sustained large GEMMs (optionally Tensor-Core / FP64) to drive the GPU to TDP
and thermal steady-state, with built-in result checking. Use it for a **thermal/power soak**
(e.g. 10–30 min) while watching temperature, throttle reasons, power, and ECC. Complementary to
DCGM: gpu-burn = max thermal load; DCGM `-r 3/4` = structured per-subsystem pass/fail.

### 2.7 NVLink status / errors (multi-GPU AV compute)

```bash
nvidia-smi nvlink --status          # per-link state (active/inactive) + speed
nvidia-smi nvlink -e                 # error counters per link
nvidia-smi nvlink -ec                # per-lane CRC error counters
dcgmi nvlink --link-status           # DCGM topology view
```
DCGM tracks **CRC FLIT**, **CRC Data**, **Replay**, and **Recovery** errors per link
(`DCGM_FI_DEV_NVLINK_*_ERROR_COUNT_TOTAL`). Rising CRC/replay/recovery = marginal NVLink SI;
**XID 74** = NVLink error. [[DCGM features]](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/feature-overview.html) [[Exxact NVLink]](https://www.exxactcorp.com/blog/HPC/exploring-nvidia-nvlink-nvidia-smi-commands)

### 2.8 Thermal / power / throttle / persistence

- **Throttle reasons** (decode the bitmask): HW thermal slowdown (TLIMIT hit), SW thermal,
  HW power brake (external), sync boost. At manufacturing, a thermal slowdown under gpu-burn
  means the cooling solution is inadequate — *not* a GPU defect per se.
- **Power**: compare `power.draw` to `enforced.power.limit`; a GPU pinned at the cap under load
  is power-limited (PSU / power-budget), distinct from thermal.
- **Persistence mode** (`nvidia-smi -pm 1`, or the `nvidia-persistenced` daemon): keeps the
  driver/GPU initialized when no client is attached, avoiding slow re-init and ensuring ECC/XID
  state is continuously tracked during a test sequence. Turn it **on** for stable bench testing.

---

## 3. GMSL Cameras

### 3.1 GMSL1 / 2 / 3 — rates & differences

| Gen | Forward rate | Reverse rate | Modulation | Notes |
|---|---|---|---|---|
| **GMSL1** | ~3.125 Gbps | (low-rate control) | NRZ | uncompressed 1080p60, <1 µs latency; legacy. |
| **GMSL2** | **3 or 6 Gbps** | **187.5 Mbps** (some 1.5 Gbps) | NRZ | 4K, added integrity checks, ≤15 m coax. |
| **GMSL3** | **3 / 6 / 12 Gbps** | 187.5 Mbps | NRZ (≤6G) / **PAM4 (12G)** | uncompressed 4K90; backward-compatible to GMSL2. |

GMSL3 12 Gbps uses **PAM4**; 6 Gbps and below are NRZ. **Backward compatibility chain:**
GMSL3↔GMSL2↔GMSL1. Forward rates are *fixed/selectable* (CFG pin resistors or register writes),
not auto-negotiated like Ethernet. [[ADI MAX96793 DS]](https://www.analog.com/media/en/technical-documentation/data-sheets/max96793.pdf) [[ADI AN-2615]](https://www.analog.com/en/resources/app-notes/an-2615.html) [[e-con GMSL]](https://www.e-consystems.com/blog/camera/technology/what-is-gmsl-technology-and-how-does-it-work/) [[Wikipedia GMSL]](https://en.wikipedia.org/wiki/Gigabit_Multimedia_Serial_Link)

### 3.2 SerDes part numbers (Analog Devices / Maxim) — real AV parts

| Part | Role | Capability |
|---|---|---|
| **MAX9295A/D** | Serializer (on camera) | GMSL2/GMSL1, single/dual MIPI CSI-2 in; 3/6 Gbps fwd, 187.5 Mbps rev. [[ADI MAX9295D]](https://www.analog.com/en/products/max9295d.html) |
| **MAX96717 / MAX96717F** | Serializer | GMSL2, CSI-2 in; F = functional-safety variant. [[ADI MAX96717]](https://www.analog.com/en/products/max96717.html) |
| **MAX96793** | Serializer | **GMSL3/2**, CSI-2 in; 3/6/12 Gbps fwd (CFG-pin selectable). [[ADI MAX96793 DS]](https://www.analog.com/media/en/technical-documentation/data-sheets/max96793.pdf) |
| **MAX9296A** | Deserializer (on carrier) | GMSL2/1 → CSI-2 D-PHY/C-PHY out; pairs with MAX9295/96717. [[Proventus]](https://proventusnova.com/blog/gmsl2-serdes-max9295-max9296-jetson/) |
| **MAX96712** | **Quad** deserializer | 4× GMSL2/1 in (4 cameras) → CSI-2 out; coax or STP. [[Mouser MAX96712]](https://www.mouser.com/ProductDetail/Analog-Devices-Maxim-Integrated/MAX96712GTB-V+) [[kernel max96712]](https://www.kernel.org/doc/Documentation/devicetree/bindings/media/i2c/maxim,max96712.yaml) |
| **MAX96792A** | Dual deserializer | GMSL3/2 → CSI-2. [[ADI MAX96792A]](https://www.analog.com/en/products/max96792a.html) |

Typical AV topology: **sensor → MAX9295/96717 (ser) → coax → MAX96712 (quad deser) → CSI-2 →
SoC**. On NVIDIA Jetson/Orin, the deserializer feeds the SoC's CSI/VI block; in mainline Linux
the **maxim GMSL2/3 i2c drivers** bind ser/deser and expose V4L2 subdevices. [[LWN GMSL drivers]](https://lwn.net/Articles/1030688/)

### 3.3 MIPI CSI-2, Power-over-Coax, the I2C reverse/control channel

- **MIPI CSI-2** is the SoC-side parallel-ish high-speed camera bus (D-PHY or C-PHY lanes) the
  deserializer outputs into. The GMSL link *tunnels* CSI-2 frames over a single coax.
- **Power-over-Coax (PoC):** power and high-speed video share **one coax**; a **PoC filter**
  (ferrite/inductor + cap network) separates the DC power band from the GHz signal band so they
  don't interfere. GMSL parts support **line-fault detection** to find PoC opens/shorts.
  [[ADI PoC line-fault]](https://www.analog.com/en/resources/design-notes/how-to-use-gmsl-linefault-detection-for-power-over-coax.html) [[Syslogic]](https://www.syslogic.com/blog/what-you-need-to-know-about-gmsl-technology)
- **Control (reverse) channel:** the **bidirectional I2C/UART tunnel** rides the link so the SoC
  can configure the *remote* image sensor and serializer through the deserializer. Each GMSL
  device also tunnels **GPIO** (used for FrameSync, see below). I2C **address translation** in
  the deserializer lets several identical sensors share one bus. [[EngineerZone I2C xlate]](https://ez.analog.com/video/f/q-a/570342/max96712-gmsl-deserializer-i2c-address-translation)

### 3.4 Link lock & error registers; FrameSync

- **Link lock** is the GMSL equivalent of PCIe L0: the deserializer's lock bit asserts when it's
  decoding a valid serial stream. *No lock* = no video, full stop. Read it over I2C (register
  varies by part; drivers also expose it). Error/decode counters (line-CRC, decode errors) live
  in per-device registers and accumulate on a marginal coax.
- **FrameSync** is GMSL's multi-camera **shutter synchronization**: a master timer in the
  deserializer (or an external pulse) is tunneled over the reverse channel as a periodic GPIO to
  trigger all sensors together — essential for AV sensor fusion. Verify all cameras lock to the
  same FrameSync and that frame timestamps are coherent.

### 3.5 V4L2 testing (`v4l2-ctl`)

```bash
v4l2-ctl --list-devices                       # enumerate /dev/videoN
v4l2-ctl -d /dev/video0 --get-fmt-video        # negotiated WxH + pixelformat
v4l2-ctl -d /dev/video0 --all                  # full subdev/control dump
v4l2-ctl -d /dev/video0 --stream-mmap \
         --stream-count=120 --stream-to=/dev/null   # capture frames, prove the path
```
A successful multi-frame capture proves **sensor → ser → coax → deser → CSI-2 → SoC** end to
end. Check the `fps`/dropped-frame line v4l2-ctl prints during streaming.

### 3.6 How marginal links show up at the vehicle stage

A GMSL camera can **lock and pass a short bench capture** yet still be marginal on the vehicle
(longer/older coax, connector corrosion, EMI, temperature, vibration). Symptoms: intermittent
loss of lock, climbing decode/line-CRC counters, dropped/torn frames, PoC line-fault flips, or
FrameSync drift. *Diagnostic angle:* trend the **error counters over a soak with the production
cable**, not just lock state at t=0; correlate frame drops with lock-loss events and with
temperature. This mirrors the PCIe "passes link-up but fails BERT" pattern.

---

## 4. Automotive Ethernet

### 4.1 Physical layers (single twisted pair / coax)

| Standard | Rate | Medium / line code | IEEE |
|---|---|---|---|
| **100BASE-T1** | 100 Mb/s | single pair, PAM3 | 802.3bw |
| **1000BASE-T1** | 1 Gb/s | single pair, **PAM3** | 802.3bp |
| **2.5/5/10GBASE-T1** | 2.5–10 Gb/s | single pair, **PAM4** | **802.3ch** |
| **10BASE-T1S** | 10 Mb/s | short multidrop, half-duplex | 802.3cg |

Automotive Ethernet runs **full-duplex over a single (unshielded) twisted pair (or coax for
multi-Gig)** with echo cancellation — there's no separate TX/RX pair. 1000BASE-T1 uses **PAM3**;
802.3ch multi-Gig uses PAM4. [[Marvell 88Q2112 PR]](https://www.marvell.com/company/newsroom/marvell-unveils-industrys-first-1000base-t1-automotive-ethernet-phy-transceiver.html)

### 4.2 Master/slave clock roles

Unlike consumer Ethernet, each automotive PHY link is configured **master or slave** (PHY-level
clock master/follower) — they must be opposite ends. A "no link" between two correctly cabled
PHYs is frequently a **master/master or slave/slave misconfiguration**, not a cable fault.

### 4.3 PHYs (real parts)

| Vendor | Part | Capability |
|---|---|---|
| Marvell | **88Q2112** | 100/1000BASE-T1 (industry's first 1000BASE-T1); integrates MDI termination. [[Marvell 88Q2112]](https://www.marvell.com/products/automotive/88q2112.html) |
| Marvell | 88Q222x / 88Q42xx | newer multi-Gig automotive PHY/switch family |
| TI | **DP83TG721-Q1** | 1000BASE-T1 with TSN/AVB + **TC10 sleep/wake** support. [[TI DP83TG721]](https://www.ti.com/lit/ds/symlink/dp83tg721r-q1.pdf) |
| Broadcom | BCM8989x / BCM8957x | automotive multi-Gig PHY/switch |

### 4.4 MDIO, ethtool, and cable-test TDR

- **MDIO** (Management Data I/O, clause-22/45) is the side-band bus to read/write PHY registers
  (link status, master/slave, error counters). Linux exposes PHYs via the netdev + phylib.
- **ethtool basics:** `ethtool eth0` (link/speed/duplex/master-slave), `ethtool -S eth0`
  (per-driver stats incl. CRC/align/PHY errors), `ethtool --show-phy` / register access.
- **Cable test (TDR)** — the killer manufacturing tool:
  ```bash
  ethtool --cable-test eth0          # pass/fail per pair
  ethtool --cable-test-tdr eth0      # TDR: fault type + distance to fault
  ```
  Result codes: **OK**, **Open Circuit** (with distance), **Short** (to another pair),
  **Impedance Mismatch** (reflection from a discontinuity), **Noise** (test couldn't complete).
  TDR reports **distance to the fault**, which on a harness pinpoints the bad connector/segment.
  [[ethtool(8)]](https://man7.org/linux/man-pages/man8/ethtool.8.html) [[kernel L1 diag]](https://docs.kernel.org/networking/diagnostic/twisted_pair_layer1_diagnostics.html)

### 4.5 TC10 sleep/wake

**OPEN Alliance TC10** standardizes coordinated **sleep and wake-up** over automotive Ethernet:
local wake, remote wake, **wake-forwarding**, and **sleep negotiation** so a whole network can
power down and a single event can wake it. Test that a PHY/switch enters sleep, wakes on the
defined event, and forwards wake correctly. [[OPEN Alliance TC10]](https://opensig.org/tech-committee/tc10-automotive-ethernet-sleep-wake-up/) [[TC10 spec]](https://opensig.org/wp-content/uploads/2024/01/TC10-Wake-up-and-Sleep-Specification-for-Automotive-Ethernet_11-2017.pdf)

### 4.6 TSN / gPTP time sync

AV networks need a shared time base for sensor fusion. **IEEE 802.1AS (gPTP)** — a profile of
IEEE 1588 PTP — distributes a grandmaster clock with low overhead; the broader **TSN** suite adds
time-aware shaping (802.1Qbv), preemption (802.1Qbu/802.3br), and reservation. Validate with
`ptp4l`/`phc2sys` (linuxptp) and check **offset-from-master** convergence and PHC↔system clock
sync. [[Intel gPTP]](https://eci.intel.com/docs/3.0/development/tsnrefsw/tsn-overview.html) [[AUTOSAR TSN]](https://www.autosar.org/fileadmin/standards/R23-11/FO/AUTOSAR_FO_EXP_TimeSensitiveNetworkFeatures.pdf)

### 4.7 iperf3 throughput

```bash
# partner (link-partner box / golden unit):
iperf3 -s
# DUT:
iperf3 -c <partner-ip> -t 30 -P 4         # TCP, 4 streams
iperf3 -c <partner-ip> -u -b 950M -t 30   # UDP near line rate, watch loss/jitter
```
For 1000BASE-T1, expect ~940 Mb/s TCP. Pair throughput with `ethtool -S` before/after to catch
PHY-layer errors that don't yet drop the link. *Diagnostic angle:* good link + low iperf + rising
CRC errors → marginal SI (cable/connector/PHY), confirm with `--cable-test-tdr`.

---

## 5. CAN / CAN-FD

### 5.1 Frames, arbitration, bit-timing

- **Frames:** Classic CAN data frames carry 11-bit (standard) or 29-bit (extended) identifiers
  and up to 8 data bytes; **CAN-FD** extends payload to **64 bytes** and allows a faster **data
  phase** bit-rate than the arbitration phase.
- **Arbitration:** CSMA/CR with **non-destructive bit-wise arbitration** — dominant (0) beats
  recessive (1); the **lowest ID wins** and keeps transmitting without re-sending. This is why
  IDs encode priority.
- **Bit timing:** each bit is divided into time quanta (sync, prop, phase1, phase2) with a
  **sample point**; CAN-FD has *separate* nominal (arbitration) and data bit-timing. On Linux you
  set `bitrate` (and `dbitrate` for FD); the kernel computes the quanta if
  `CONFIG_CAN_CALC_BITTIMING=y`. [[kernel SocketCAN]](https://docs.kernel.org/networking/can.html)

### 5.2 Termination (120 Ω × 2 → 60 Ω)

A CAN bus is terminated with **120 Ω at *each physical end*** of the trunk; the two in parallel
present **~60 Ω** to a transceiver. Missing/incorrect termination → reflections, bit errors,
rising error counters. Quick field check: power-off, measure CANH–CANL ≈ **60 Ω**. [[CSS CAN errors]](https://www.csselectronics.com/pages/can-bus-errors-intro-tutorial) [[kernel SocketCAN]](https://docs.kernel.org/networking/can.html)

### 5.3 Error states, TEC/REC, bus-off

Each node keeps a **Transmit Error Counter (TEC)** and **Receive Error Counter (REC)**:

| State | Condition | Behavior |
|---|---|---|
| **Error-Active** | TEC and REC ≤ 127 | normal; transmits **active** error flags |
| **Error-Passive** | TEC or REC > 127 | transmits **passive** error flags, backs off |
| **Bus-Off** | **TEC > 255** | stops transmitting entirely; needs recovery |

Bus-off recovery: the controller re-initializes and waits **128 × 11 recessive bits** before
rejoining. [[can-wiki error states]](http://www.can-wiki.info/doku.php?id=can_faq%3Acan_faq_erors) [[Vector e-learning]](https://elearning.vector.com/mod/page/view.php?id=359)
*Manufacturing thresholds:* a healthy new node should be **Error-Active with TEC=REC=0**; any
drift toward error-passive on the bench indicates wiring/termination/transceiver trouble.

### 5.4 Transceiver test

The CAN **transceiver** (e.g. TI TCAN/SN65HVD, NXP TJA1042/TJA1462 for CAN-FD) converts
logic-level TX/RX to the differential bus. Bench checks: confirm dominant/recessive levels
(CANH ~3.5 V / CANL ~1.5 V dominant; both ~2.5 V recessive), and that the device's standby/wake
pins behave. A transceiver stuck dominant jams the whole bus.

### 5.5 SocketCAN

```bash
# bring up classic CAN at 500 kbit/s
sudo ip link set can0 type can bitrate 500000
sudo ip link set up can0

# CAN-FD with data bitrate 2 Mbit/s
sudo ip link set can0 type can bitrate 500000 dbitrate 2000000 fd on
sudo ip link set up can0

candump -e any                 # dump all frames incl. error frames
cansend can0 123#DEADBEEF      # send a frame
cangen can0 -g 5 -I i          # generate traffic (5 ms gap, incrementing IDs)
ip -details -statistics link show can0   # state + TEC/REC + bus-off counters
```
[[kernel SocketCAN]](https://docs.kernel.org/networking/can.html) `ip -details link show` prints
the **state** (`ERROR-ACTIVE`/`ERROR-PASSIVE`/`BUS-OFF`), bit-timing, and the netlink error
counters — the authoritative source for TEC/REC (don't string-match the human text only).

### 5.6 Loopback

```bash
sudo ip link set can0 type can bitrate 500000 loopback on
sudo ip link set up can0
# cansend on can0 should be received back by candump can0
```
Internal/external loopback validates the controller+transceiver path **without** a partner node —
ideal for a single-board manufacturing fixture.

---

## 6. DDR / Memory & EDAC

### 6.1 DDR4 vs DDR5

- **DDR5** splits a DIMM into **two independent 32-bit (40-bit with ECC) sub-channels**, doubles
  bank groups, moves the power-management (PMIC) onto the module, and runs higher data rates with
  **decision-feedback equalization** on the PHY — vs DDR4's single 64-bit (72-bit ECC) channel.
- **Crucially, every DDR5 device has on-die ECC** (see below), which changes how errors present
  to the host.

### 6.2 ECC types

| Scheme | Corrects / detects | Where |
|---|---|---|
| **SECDED** | corrects 1-bit, detects 2-bit (per 64-bit word, +8 ECC bits = 72-bit DIMM) | classic DDR4 system ECC |
| **Chipkill / SDDC** | corrects an **entire failed x4 (or x8) DRAM** (Single Device Data Correction), via Reed-Solomon symbol codes spread across chips (e.g. S4ECD4ED, S8EC) | server memory controllers |
| **DDR5 on-die ECC (ODECC)** | corrects single-bit errors **inside the DRAM die** before data leaves the chip | every DDR5 device — but **not end-to-end**: it masks weak cells and does **not** replace system ECC |

[[Synopsys ECC]](https://www.synopsys.com/articles/ecc-memory-error-correction.html) [[SemiEng ECC]](https://semiengineering.com/what-designers-need-to-know-about-error-correction-code-ecc-in-ddr-memories/) [[memtest86 ECC]](https://www.memtest86.com/ecc.htm)
> Gotcha: DDR5 on-die ECC can **hide** a marginal cell from system-level counters, so a DDR5
> system can look clean while the die is silently correcting. System ECC + EDAC counters are
> still required to catch errors that escape on-die correction.

### 6.3 EDAC sysfs counters

Linux **EDAC** reports memory-controller ECC events:

```bash
# totals per memory controller
cat /sys/devices/system/edac/mc/mc0/ce_count    # corrected errors
cat /sys/devices/system/edac/mc/mc0/ue_count    # uncorrectable errors
# per-DIMM / per-rank (csrowX / dimmX) breakdown + labels
cat /sys/devices/system/edac/mc/mc0/dimm0/dimm_ce_count
cat /sys/devices/system/edac/mc/mc0/dimm0/dimm_label
```
`ce_count`/`ue_count` are the controller totals; per-`dimm*`/`csrow*` nodes localize the error,
and `dimm_label` maps it to a **physical DIMM silkscreen** for the technician. [[kernel sysfs-edac]](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-devices-edac) [[kernel EDAC]](https://docs.kernel.org/driver-api/edac.html)

### 6.4 rasdaemon / ras-mc-ctl (DIMM-label mapping)

`rasdaemon` is the modern replacement for `edac-utils`: it consumes the kernel **tracepoints**
and logs ECC (and PCIe AER, MCE, etc.) events to a SQLite DB.

```bash
ras-mc-ctl --error-count        # CE/UE per DIMM (from sysfs)
ras-mc-ctl --summary            # summary of logged errors
ras-mc-ctl --errors             # detailed error records (with DIMM labels)
ras-mc-ctl --layout             # memory layout / topology
```
Populate **DIMM labels** (board-specific `labels.db` keyed by DMI) so a CE/UE points at "DIMM_A1"
instead of an abstract csrow — this is what makes RAS output actionable on the line. [[setphasers rasdaemon]](https://www.setphaserstostun.org/posts/monitoring-ecc-memory-on-linux-with-rasdaemon/) [[ras-mc-ctl src]](https://github.com/mchehab/rasdaemon/blob/master/util/ras-mc-ctl.in)

### 6.5 Stress tools: memtest86+ vs stressapptest

| Tool | Runs | Strength | Limit |
|---|---|---|---|
| **memtest86+ / MemTest86** | bare-metal (boots its own kernel) | classic march/pattern tests; ECC-aware editions inject/report ECC | offline (no OS), slow full passes |
| **stressapptest** (SAT) | under Linux | high memory **bandwidth** stress + DMA, designed to find marginal hardware fast; pairs with EDAC/rasdaemon counters | runs in-OS (won't test the exact ranges the OS occupies) |

For manufacturing burn-in, **stressapptest under Linux while watching `ras-mc-ctl --error-count`**
is the practical combo (catch CEs/UEs under load); a bare-metal MemTest86 pass is the deeper
offline screen.

### 6.6 Thermal effects

DRAM error rates rise with temperature; DDR5 modules auto-adjust **refresh rate** with on-die
temp sensors (and the PMIC reports thermals). A DIMM that only throws CEs **under thermal +
bandwidth load** is the classic marginal-part signature — always stress **hot**, and log CE/UE
deltas vs temperature, not just a cold pass.

---

## 7. Recommendations — concrete toolkit improvements

Audit of `toolkit/src/computetest/{nvme,gpu,gmsl,ethernet}.py` and `backend.py`. The toolkit is
well-structured (clean Backend abstraction, faithful AER latch model, mock-everywhere
testability). Findings below are **inaccuracies**, **missing checks**, and **high-value
additions**, with file/line context. *(This section is advisory; per instructions no code files
were modified.)*

### 7.1 Inaccuracies / bugs to fix

1. **GPU ECC field is aggregate-only, mislabeled, and the limit is wrong** — `gpu.py:78` queries
   `ecc.errors.{corrected,uncorrected}.aggregate.total`, but the check `ecc_uncorrected==0`
   (`gpu.py:44`) then treats *lifetime aggregate* as a per-test gate. A used/qualified GPU with a
   single historical, already-remapped DBE would fail forever. Query **both volatile and
   aggregate**; gate manufacturing pass on **volatile uncorrected == 0** and treat nonzero
   aggregate as an *investigate/RMA-history* flag, not an automatic fail.

2. **GPU throttle decode is too coarse and drops real reasons** — `gpu.py:84,87`:
   `"active" if throttle_bits & ~0x1 else ""` lumps every non-idle reason into a single
   `throttle` string and mislabels it. The `clocks_throttle_reasons.active` value is a **bitmask**;
   decode HW-thermal / SW-thermal / HW-power-brake / sync-boost separately so a thermal fail is
   distinguishable from a power-cap event. Also query `clocks_throttle_reasons.hw_thermal_slowdown`
   etc. directly.

3. **GPU `replay_count` is hard-coded to 0 on the real path** — `gpu.py:87` sets
   `"replay_count": 0` always, yet `_apply_limits` gates on `replay_count < 100` (`gpu.py:47`).
   The PCIe-replay check is therefore dead on real hardware. Query `pcie.replay.counter` (and
   ideally `pcie.replay.rollover.counter`).

4. **NVMe DST result is never read** — `nvme.py:96` `start_self_test()` only *starts* the test and
   returns `True` immediately (even in mock). Nothing ever reads **log page 0x06**. As written it
   provides false assurance. Add a `self_test_log()` poller that parses the latest result code and
   completion %.

5. **NVMe new-drive limits are slightly loose / missing fields** — `nvme.py:45`: `percentage_used<2`
   and `available_spare>=100` are good, but there's no check on **`power_on_hours`/`power_cycles`**
   (catches re-used RMA stock sold as new) nor on **thermal-throttle transition counters**
   (`thm_temp1/2_trans_count`), nor `unsafe_shutdowns`. `media_errors==0` is correct and important.

6. **Ethernet speed parse is fragile** — `ethernet.py:62` strips *all* digits from the `Speed:`
   line, so `Speed: 1000Mb/s` works but any stray digit (or `2.5GbE`) breaks it. Parse with a
   regex anchored on the unit, and recognize multi-gig (`2500`, `5000`, `10000`).

7. **Ethernet error gate uses only `rx_errors`/`tx_errors`** — `ethernet.py:66`. On automotive
   PHYs the meaningful counters are driver-specific (CRC, alignment, PHY/symbol errors); summing
   just `rx_errors+tx_errors<10` can miss link-layer SI problems. Sum the relevant `*_err*`
   counters and also report CRC/PHY counters explicitly.

8. **GMSL real-path lock/error sysfs paths are placeholders** — `gmsl.py:65,70` read
   `/sys/bus/i2c/devices/{link}/link_status` and `/error_count`, which are *not* standard maxim
   driver attributes. On a real platform these likely don't exist (silently → `locked=False` or
   `errors=0`). Read the actual ser/deser registers over I2C (or the V4L2 subdev controls the
   mainline GMSL drivers expose) for the specific MAX9296/MAX96712 lock & decode-error registers.

### 7.2 Missing checks (high value)

9. **NVMe persistent event log (0x0D) + error log (0x01) capture** — pull `error-log` and
   `persistent-event-log` (and `telemetry-log` on failure) into the report so RMA/FA has the
   non-volatile history, and so the test can flag thermal excursions / prior errors a fresh SMART
   page hides.

10. **GPU XID monitoring** — none today. Scrape `dmesg`/`/var/log/syslog` (or the kernel ring
    buffer) for `NVRM: Xid` lines during a soak and **bucket by code** (48/63/64/79/74/92/94/95).
    This is the single highest-signal GPU manufacturing check and maps each fault to a root-cause
    class (memory vs bus vs NVLink vs GSP).

11. **GPU row-remap / retired-page check** — query `nvidia-smi -q -d ROW_REMAPPER` (Ampere+) or
    `retired_pages.*` (older) for remapped-row count, **pending**, and **failure** flags; nonzero
    pending/failure is an RMA signal even with zero live ECC.

12. **DCGM integration** — wrap `dcgmi diag -r {1,2,3}` (and `-r 4` for memtest+pulse) with
    structured pass/fail parsing (`-j` JSON), plus `dcgmi nvlink --link-status` and the NVLink
    CRC/replay/recovery counters. This is the standard GPU qualification path and complements
    gpu-burn (thermal soak).

13. **GMSL per-link iteration** — `check_gmsl` tests one `link`/`/dev/videoN` at a time. A quad
    MAX96712 carries **4 cameras**; add a `check_all_gmsl()` that enumerates ser/deser links and
    every `/dev/videoN`, captures frames per camera, and **trends decode/line-CRC counters over a
    soak with the production cable** (catches the "locks at t=0, marginal on-vehicle" failure).
    Also verify **FrameSync** coherence across cameras.

14. **Ethernet cable-test (TDR)** — add `ethtool --cable-test` / `--cable-test-tdr`, parse
    fault-type + distance, and surface master/slave role. This is the highest-value automotive
    Ethernet manufacturing test (pinpoints the bad harness segment) and is entirely absent.

15. **CAN: read real TEC/REC from netlink, not text** — `ethernet.py:114` sets `tec=rec=0` on the
    real path and only string-matches the state. Parse the actual counters from
    `ip -details -statistics link show` (or the CAN netlink attrs) so the `low_errors` gate has
    real data; add a **loopback** self-test mode for single-board fixtures and detect **CAN-FD**.

16. **New `memory.py` module (EDAC/RAS)** — there is no DDR/memory interface check at all. Add a
    module that reads EDAC `ce_count`/`ue_count` (+ per-DIMM and `dimm_label`), shells
    `ras-mc-ctl --error-count`/`--summary`, optionally drives a `stressapptest` soak, and gates on
    **zero UE and bounded CE under load** — mirroring the existing per-interface pattern so it
    plugs into the harness/dashboard. (Note DDR5 on-die ECC can mask cell weakness, so system-ECC
    counters remain necessary.)

### 7.3 Backend / architecture suggestions

17. **Generalize the Backend beyond PCIe** — `backend.py`'s `Backend` ABC is PCIe-config-space
    specific, while `nvme/gpu/gmsl/ethernet.py` each independently call `mock_mode()` and shell
    out. Consider a thin **command-runner / sysfs-reader seam** (an injectable `run(cmd)` +
    `read_sysfs(path)`) so the CLI-wrapping checks are unit-testable against canned tool output
    the same way `MockBackend` makes PCIe testable — today their real paths are
    `# pragma: no cover` and untested.

18. **NVMe temperature normalization is heuristic** — `nvme.py:88` converts to Celsius only if
    `temperature > 200`. Robust, but prefer keying off the known nvme-cli Kelvin convention and
    also surface the `warning_temp_time`/`critical_comp_time` fields rather than a single temp.

---

## Sources

**NVMe**
- NVMe SMART/Health log 0x02 fields — [Microsoft NVME_HEALTH_INFO_LOG](https://learn.microsoft.com/en-us/windows/win32/api/nvme/ns-nvme-nvme_health_info_log), [mankier nvme_smart_log](https://www.mankier.com/2/nvme_smart_log)
- Self-Test log 0x06 — [mankier nvme_self_test_log](https://www.mankier.com/2/nvme_self_test_log), [nvme device-self-test (Debian)](https://manpages.debian.org/testing/nvme-cli/nvme-device-self-test.1.en.html)
- Queues / namespaces / Identify CNS — [Oracle NVMe architecture](https://blogs.oracle.com/linux/overview-of-nvme-architecture), [OSDev NVMe](https://wiki.osdev.org/NVMe), [NVM Express 1.2a spec](https://www.nvmexpress.org/wp-content/uploads/NVM-Express-1_2a.pdf)
- fio verify — [fio basic-verify example](https://github.com/axboe/fio/blob/master/examples/basic-verify.fio), [NVMe verification & benchmarking](https://medium.com/@krisiasty/nvme-storage-verification-and-benchmarking-49b026786297), [fio MDTS issue #411](https://github.com/axboe/fio/issues/411)
- Sanitize/Format/Secure-erase — [tinyapps NVMe Sanitize](https://tinyapps.org/docs/nvme-sanitize.html), [L1T crypto-erase](https://forum.level1techs.com/t/nvme-crypto-erase-and-sanitize/161587), [ArchWiki memory-cell clearing](https://wiki.archlinux.org/title/Solid_state_drive/Memory_cell_clearing)
- Firmware download/commit — [mankier nvme-fw-commit](https://www.mankier.com/1/nvme-fw-commit), [Debian nvme-fw-download](https://manpages.debian.org/testing/nvme-cli/nvme-fw-download.1.en.html), [Microsoft firmware activate actions](https://learn.microsoft.com/en-us/windows/win32/api/nvme/ne-nvme-nvme_firmware_activate_actions)
- HCTM / TMT1 / TMT2 — [Semiconductor Nerds HCTM](https://semiconductor-nerds.com/can-your-host-damage-if-storage-device-gets-too-heated-hctm-is-there-to-rescue/), [Hagiwara NVMe thermal throttling](https://www.hagisol.com/techblog/?p=635)

**NVIDIA GPUs**
- XID error codes — [NVIDIA XID Errors](https://docs.nvidia.com/deploy/xid-errors/), [XID catalog](https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html), [NVIDIA GPU debug guidelines](https://docs.nvidia.com/deploy/gpu-debug-guidelines/index.html)
- DCGM diag levels — [DCGM Diagnostics](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/dcgm-diagnostics.html), [DCGM feature overview](https://docs.nvidia.com/datacenter/dcgm/latest/user-guide/feature-overview.html)
- NVLink — [Exxact NVLink nvidia-smi commands](https://www.exxactcorp.com/blog/HPC/exploring-nvidia-nvlink-nvidia-smi-commands), [DCGM field IDs](https://docs.nvidia.com/datacenter/dcgm/latest/dcgm-api/dcgm-api-field-ids.html)

**GMSL**
- Parts/rates — [ADI MAX9295D](https://www.analog.com/en/products/max9295d.html), [ADI MAX9295D datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/max9295d.pdf), [ADI MAX96717](https://www.analog.com/en/products/max96717.html), [ADI MAX96793 datasheet](https://www.analog.com/media/en/technical-documentation/data-sheets/max96793.pdf), [ADI MAX96792A](https://www.analog.com/en/products/max96792a.html), [Mouser MAX96712](https://www.mouser.com/ProductDetail/Analog-Devices-Maxim-Integrated/MAX96712GTB-V+), [ADI AN-2615 GMSL2→3](https://www.analog.com/en/resources/app-notes/an-2615.html), [Wikipedia GMSL](https://en.wikipedia.org/wiki/Gigabit_Multimedia_Serial_Link), [e-con GMSL](https://www.e-consystems.com/blog/camera/technology/what-is-gmsl-technology-and-how-does-it-work/)
- PoC / control channel / drivers — [ADI PoC line-fault detection](https://www.analog.com/en/resources/design-notes/how-to-use-gmsl-linefault-detection-for-power-over-coax.html), [Syslogic GMSL](https://www.syslogic.com/blog/what-you-need-to-know-about-gmsl-technology), [EngineerZone I2C address translation](https://ez.analog.com/video/f/q-a/570342/max96712-gmsl-deserializer-i2c-address-translation), [LWN maxim GMSL2/3 drivers](https://lwn.net/Articles/1030688/), [kernel max96712 binding](https://www.kernel.org/doc/Documentation/devicetree/bindings/media/i2c/maxim,max96712.yaml), [Proventus Jetson GMSL2](https://proventusnova.com/blog/gmsl2-serdes-max9295-max9296-jetson/)

**Automotive Ethernet**
- PHYs/standards — [Marvell 88Q2112 PR](https://www.marvell.com/company/newsroom/marvell-unveils-industrys-first-1000base-t1-automotive-ethernet-phy-transceiver.html), [Marvell 88Q2112 product](https://www.marvell.com/products/automotive/88q2112.html), [TI DP83TG721-Q1 datasheet](https://www.ti.com/lit/ds/symlink/dp83tg721r-q1.pdf)
- ethtool / cable-test TDR — [ethtool(8) man page](https://man7.org/linux/man-pages/man8/ethtool.8.html), [kernel twisted-pair L1 diagnostics](https://docs.kernel.org/networking/diagnostic/twisted_pair_layer1_diagnostics.html)
- TC10 / gPTP / TSN — [OPEN Alliance TC10](https://opensig.org/tech-committee/tc10-automotive-ethernet-sleep-wake-up/), [TC10 spec PDF](https://opensig.org/wp-content/uploads/2024/01/TC10-Wake-up-and-Sleep-Specification-for-Automotive-Ethernet_11-2017.pdf), [Intel gPTP overview](https://eci.intel.com/docs/3.0/development/tsnrefsw/tsn-overview.html), [AUTOSAR TSN features](https://www.autosar.org/fileadmin/standards/R23-11/FO/AUTOSAR_FO_EXP_TimeSensitiveNetworkFeatures.pdf)

**CAN / CAN-FD**
- SocketCAN — [kernel SocketCAN docs](https://docs.kernel.org/networking/can.html)
- Error states / termination — [can-wiki error states](http://www.can-wiki.info/doku.php?id=can_faq%3Acan_faq_erors), [CSS Electronics CAN errors](https://www.csselectronics.com/pages/can-bus-errors-intro-tutorial), [Vector e-learning error tracking](https://elearning.vector.com/mod/page/view.php?id=359)

**DDR / Memory**
- EDAC sysfs / RAS — [kernel sysfs-devices-edac](https://www.kernel.org/doc/Documentation/ABI/testing/sysfs-devices-edac), [kernel EDAC driver-api](https://docs.kernel.org/driver-api/edac.html), [setphasers rasdaemon](https://www.setphaserstostun.org/posts/monitoring-ecc-memory-on-linux-with-rasdaemon/), [ras-mc-ctl source](https://github.com/mchehab/rasdaemon/blob/master/util/ras-mc-ctl.in)
- ECC types / DDR5 — [Synopsys ECC in DDR](https://www.synopsys.com/articles/ecc-memory-error-correction.html), [SemiEngineering ECC](https://semiengineering.com/what-designers-need-to-know-about-error-correction-code-ecc-in-ddr-memories/), [MemTest86 ECC technical details](https://www.memtest86.com/ecc.htm)
