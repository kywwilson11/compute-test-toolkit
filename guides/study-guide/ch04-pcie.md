## Why this chapter is the long one

The Zoox JD's bonus qualification reads *"strong expertise in PCIe troubleshooting —
especially with GPUs and similar electronic assemblies."* On the compute platform PCIe is
the spine: it carries every GPU, every Non-Volatile Memory Express (NVMe) drive, every custom sensor-interface card and
switch/fan-out board. Most of what you debug on the line is a SerDes link that trained
wrong, drifted with temperature, or quietly dropped a lane — and almost all of those wear a
PCIe costume. So this chapter goes deep and stays practical: the **register offsets and
bit-fields you actually read and write**, real `lspci`/`setpci` output, the **failure
signature → root-cause-layer** mapping, and how each finding lands in **manufacturing test
(MT)**.

The organizing idea, repeated until it is reflex:

> **A PCIe failure is never `errors=5`. It is a *tuple*: which Advanced Error Reporting (AER) bits, on which lane, in
> which direction, with which header log, at which retrain count, with how much eye margin,
> at what temperature and load. That tuple *is* the root cause.** Everything here exists to
> collect that tuple efficiently and read it.

Two cross-references so this chapter doesn't re-derive what lives elsewhere:

- The **Bit Error Rate (BER) confidence math** (Poisson model, the "3/BER" rule, the chi-squared upper bound)
  is derived in the **Math & Statistics chapter, §10**. Here we *use* it — when you see a
  confidence number, that's where the formula came from.
- The companion **`toolkit/`** implements this chapter: `aer.py` (decode + Write-1-to-Clear (W1C) clear),
  `linkstate.py` (speed/width + retrain/latch monitor), `ber.py` (the confidence math),
  `bert.py` (the arm→stress→read→decide loop), `margining.py` (lane margining + the Transmit (TX)-preset
  sweep), `topology.py`/`diagnostics.py` (the whole-chain walk). Read a section, then read the
  code that does it.

\newpage

## 1. Architecture: the chain you actually debug

PCIe is a **point-to-point, serial, packet-based** interconnect. Each *link* connects
exactly two ports — there is no shared bus. Multiple **lanes** (x1, x2, x4, x8, x16) are
bonded for bandwidth; each lane is a differential pair in each direction (TX+/TX−, Receive (RX)+/RX−),
so a link is **full-duplex** — it sends and receives simultaneously, and the two directions
are independent. *That independence matters for test:* a bit-error problem can exist on one
direction of one link and not the other, which is why errors are evaluated **per receiver**,
not per link (§13).

### 1.1 Topology and the four port types

The hierarchy fans out from the CPU:

```text
        CPU / Root Complex
              |
        [Root Port]              <- a Downstream Port (DSP); owns the link below it
              |  link (its own LTSSM, AER, equalization)
      [Switch Upstream Port]     <- USP: faces the root
       /        |        \
  [DSP]       [DSP]      [DSP]    <- each switch Downstream Port: its own link/LTSSM/AER
    |           |          |
 Endpoint   Endpoint    Endpoint  (GPU, NVMe, NIC, custom card)
```

| Port type | Code (DevType[3:0]) | Role |
|---|---|---|
| Endpoint | 0x0 | A leaf: GPU, NVMe controller, Network Interface Card (NIC), custom Field-Programmable Gate Array (FPGA) card |
| Root Port | 0x4 | A Root Complex egress port; a **downstream** port |
| Switch Upstream Port | 0x5 | The single port of a switch that faces the root |
| Switch Downstream Port | 0x6 | One of N switch ports that face leaves; **downstream** |

The term **Downstream Port (DSP)** = root port *or* switch downstream port; it **owns the
link below it** (and the link's bandwidth-change latches). **Upstream Port (USP)** = an
endpoint, or a switch's upstream port. This distinction is load-bearing for diagnosis: the
DSP is where speed/width and the Link Bandwidth Management Status (LBMS)/Link Autonomous Bandwidth Status (LABS) latches for that link live (§4), and the root port
is the only place the kernel assembles the OS-level error story (§5).

A **switch** is internally one USP bridged to N DSPs; **every one of those ports has its own
Link Training and Status State Machine (LTSSM), its own Advanced Error Reporting (AER) capability, and its own link registers.** A fallback or an error cluster
can be on *any* segment — so when you debug a device behind a switch you walk the whole tree
(`lspci -tv`) and check each link, not just the endpoint (§12).

### 1.2 BDF: how every function is addressed

Every PCIe function is named by **Bus/Device/Function (BDF) = `domain:bus:device.function`**, e.g.
`0000:03:00.0`. Domain (a.k.a. segment) is usually `0000`; bus is assigned during
enumeration; device is 0–31 on a bus; function is 0–7 (multi-function devices, and Single Root I/O Virtualization (SR-IOV)
VFs, use the function digit). Config reads/writes are addressed by Bus/Device/Function; Advanced Error Reporting's **Error Source
ID** reports the BDF of the requester that caused an error. Throughout this chapter
`BDF=0000:03:00.0` is the running example.

### 1.3 The layer model

Like networking, PCIe is layered — and as in networking, the fastest first question on any
failure is *"which layer?"*

| Layer | Packet | Job | What breaks here |
|---|---|---|---|
| **Transaction (TL)** | **Transaction Layer Packet (TLP)** | Carries reads/writes/completions/messages | Protocol: wrong address, timeout, malformed packet |
| **Data Link (Data Link Layer (DLL))** | **Data Link Layer Packet (DLLP)** | Reliability: sequence numbers, Link CRC (LCRC), ACK/NAK, replay, flow-control credits | Link-level: corrupted TLPs get NAK'd and replayed |
| **Physical (PL)** | **PLP / ordered sets** | SerDes, 8b/10b or 128b/130b coding, equalization, lane bonding, the LTSSM | Signal Integrity (SI): symbol errors, closed eye, retrains |

- **TLP — Transaction Layer Packet.** The payload-carrying unit. Types: **Memory Read/Write**
  (access a device's Base Address Register (BAR) space — a write is *posted*, no completion; a read is *non-posted*,
  needs a Completion), **Config Read/Write** (Bus/Device/Function-addressed access to config space),
  **Completion** (the response to a non-posted request — carries read data or a status code),
  **I/O** (legacy), and **Message** (Message Signaled Interrupt (MSI)/Message Signaled Interrupt Extended (MSI-X) interrupts, error signaling, power management).
- **DLLP — Data Link Layer Packet.** Small, link-local, never routed: ACK/NAK, flow-control
  credit updates, power-management. DLLPs are **not retried** — a corrupted one is just
  dropped (which is why a *Bad DLLP* correctable is a pure signal-integrity tell, §5).
- **PLP / ordered sets** — the physical-layer framing and training sequences (TS1/TS2, SKP,
  EIEOS) that the Link Training and Status State Machine uses to bring a link up and keep it locked.

**Reliability is at the Data Link Layer, by replay.** Every TLP gets a **sequence number** and an
**LCRC**. The receiver checks the Link CRC, and ACKs good TLPs / NAKs bad ones; an un-ACKed TLP
is **replayed** from a retry buffer. This is the mechanism behind the correctable-error
counters you live in (Bad TLP, Replay Timer Timeout, REPLAY_NUM Rollover) — they are the
DLL telling you the physical layer is corrupting packets and the link is retrying.

**Flow control is credit-based, at the TL.** The receiver advertises buffer space (credits)
per TLP class — **Posted, Non-Posted, Completion** — and the transmitter may not send without
credits. This prevents receiver overflow without per-packet ACKs at the transaction layer. A
credit-accounting bug surfaces as a **Flow Control Protocol** or **Receiver Overflow**
uncorrectable (§5).

\newpage

## 2. Generations, widths, encoding, and the GT/s → GB/s math

This is a place to be precise — getting the bandwidth math wrong in front of EE is an easy
own-goal, and the numbers drive your pass/fail expectations.

| Gen | Raw rate | Encoding | Coding overhead | Effective BW/lane | x16 bandwidth (one dir) |
|---|---|---|---|---|---|
| Gen1 | 2.5 GT/s | 8b/10b | 20% | 250 MB/s | 4.0 GB/s |
| Gen2 | 5.0 GT/s | 8b/10b | 20% | 500 MB/s | 8.0 GB/s |
| Gen3 | 8.0 GT/s | 128b/130b | ~1.5% | ~984.6 MB/s | ~15.75 GB/s |
| Gen4 | 16 GT/s | 128b/130b | ~1.5% | ~1.969 GB/s | ~31.5 GB/s |
| Gen5 | 32 GT/s | 128b/130b | ~1.5% | ~3.938 GB/s | ~63 GB/s |
| Gen6 | 64 GT/s | PAM4 + 256B FLIT + FEC/CRC | ~few % (FEC/CRC/FLIT framing) | ~7.56 GB/s | ~121 GB/s |

**The math, worked.** "GT/s" is **giga-transfers per second** — one bit per transfer for the
Non-Return-to-Zero (NRZ) gens (Gen1–5), so the raw bit rate per lane equals the GT/s number in Gbit/s. Apply the
coding efficiency, then divide by 8 for bytes:

```text
Gen3 x1:  8.0 GT/s x (128/130) = 7.877 Gbit/s -> /8 = 0.9846 GB/s per lane
Gen4 x16: 16 GT/s x (128/130) x 16 / 8 = 31.5 GB/s   (one direction)
Gen5 x16: 32 GT/s x (128/130) x 16 / 8 = 63 GB/s
```

Two facts to internalize:

- **8b/10b** (Gen1/2) maps 8 data bits to 10 line bits to guarantee DC balance and enough
  transitions for clock recovery → a flat **20% overhead**. **128b/130b** (Gen3–5) sends 128
  payload bits under a 2-bit sync header and uses *scrambling* (not block-coding) for DC
  balance → only **~1.5%** overhead.
- **The Gen2→Gen3 jump is bigger than 1.6×.** The raw rate went 5→8 GT/s (1.6×) *and* the
  overhead dropped 20%→1.5%, so per-lane bandwidth roughly **doubled** (500 → ~985 MB/s).
  That combined step is why "Gen3" is such a watershed and why everything above it needs
  equalization.

**Gen6 changes the signaling and the error model.** Gen6 uses **Pulse Amplitude Modulation 4-level (PAM4)** (4 voltage levels →
2 bits per symbol, so 64 GT/s at the same ~32 GBaud as Gen5), moves to a fixed **256-byte
Fixed-size Link Packet (FLIT)** with **Forward Error Correction (FEC) (lightweight LDPC FEC) + a strong CRC + replay** instead
of LCRC-retry, and **eliminates standalone DLLPs** (ACK/NAK and flow-control move *inside* the
FLIT). The 256-byte FLIT budgets roughly **236 B of TLP payload, ~6 B of DLP (the old DLLP
content), 8 B CRC, and ~6 B FEC** — so the DLLP information is still there, just carried in the
FLIT rather than as separate, droppable link packets. Because PAM4 has a *raw* symbol-error
rate orders of magnitude worse than NRZ (three eyes instead of one), the spec leans on Forward Error Correction to
pull the post-correction error rate back down — FEC keeps the link-retry probability under
~1e-5. The practical consequence for your tools: **an AER-correctable Bit Error Rate Test (BERT) under-measures a
Gen6 link**, because most symbol errors are FEC-corrected and never become Bad-TLP/replay
events. A true Gen6 error rate comes from **FEC corrected/uncorrected symbol counters** (and
the CRC/retry rate), not AER correctables. The toolkit flags this (`bert.py` emits a note when
`current_link_speed >= 6`); you check the **Flit Mode Status** bit before trusting LCRC-retry
accounting (the LTSSM section covers reading it).

**Bandwidth → time, for sizing a BERT.** Gen4 x16 ≈ 31.5 GB/s ≈ 2.5×10¹¹ bit/s; an NVMe Gen3
x4 link ≈ 3.5 GB/s. These set how long a confidence BERT must run (§11): 3×10¹² clean bits is
≈ **12 s** on Gen4 x16 and ≈ **110 s** on Gen3 x4.

\newpage

## 3. Config space is just a file you can read

Every PCIe function exposes a **256-byte** legacy config space, extended to **4096 bytes**
for PCIe (where Advanced Error Reporting and the other extended capabilities live). On Linux it is literally a
file:

```bash
/sys/bus/pci/devices/0000:03:00.0/config              # raw 4096-byte config space
/sys/bus/pci/devices/0000:03:00.0/current_link_speed  # "16.0 GT/s"
/sys/bus/pci/devices/0000:03:00.0/current_link_width  # "16"
/sys/bus/pci/devices/0000:03:00.0/max_link_speed      # capability ceiling
/sys/bus/pci/devices/0000:03:00.0/max_link_width
```

You can read any register by `pread`-ing `config` at the right offset — which is exactly what
the toolkit's backend does (`read_config(bdf, offset, size)`), so it never fragile-parses
`lspci` text. Reading the extended space (offset ≥ 0x100) needs root.

### 3.1 The header and the layout you navigate

```text
0x000  +-----------------------------+
       | Type 0 (EP) or Type 1 hdr   |  Vendor/Device ID, Command, Status, Class, BARs...
0x040  +-----------------------------+
       | Capability list             |  linked list via "next pointer" bytes:
       |  - PCI Express Cap (ID 0x10)|   -> Link Cap/Ctrl/Status, Dev Ctrl/Status 2
       |  - MSI / MSI-X              |
       |  - Power Management         |
0x100  +-----------------------------+  <- Extended config space (PCIe-only, root needed)
       | Extended Capability list    |  another linked list:
       |  - AER            (0x0001) |   -> the error registers
       |  - Secondary PCIe (0x0019) |   -> Gen3+ equalization control
       |  - Lane Margining (0x0027) |   -> receiver eye margin per lane (Gen4+)
       |  - DPC (0x001D), ACS, SR-IOV ...
0xFFF  +-----------------------------+
```

The header registers you check first on any device:

| Offset | Register | Size | What to verify |
|---|---|---|---|
| 0x00 | Vendor ID | 16-bit | Expected vendor (0x10DE NVIDIA, 0x144D Samsung NVMe, 0x8086 Intel) |
| 0x02 | Device ID | 16-bit | Expected GPU/NVMe/card model |
| 0x04 | Command | 16-bit | **Bit 2 = Bus Master Enable** (must be set for Direct Memory Access (DMA)) |
| 0x06 | Status | 16-bit | Capabilities-list present (bit 4); error summary bits |
| 0x08 | Revision ID | 8-bit | Silicon revision |
| 0x09 | Class Code | 24-bit | 0x030000 VGA, 0x030200 3D controller, 0x010802 NVMe |
| 0x0E | Header Type | 8-bit | 0x00 = Type 0 (endpoint), 0x01 = Type 1 (bridge/switch port) |
| 0x10–0x27 | BAR 0–5 | 32-bit ea | Base Address Registers — where the device's Memory-Mapped I/O (MMIO) is mapped |

> **MMIO vs the BARs, and why "unassigned" = dead.** A modern PCIe device's registers are
> mapped into the CPU's memory space (Memory-Mapped I/O); the BAR tells the OS where. If `lspci -vvv` shows
> `Region 0: Memory at <unassigned>` or `BAR ... can't assign`, the device is **visible but
> non-functional** — software cannot reach its registers. On a custom card this usually means
> the BIOS couldn't fit the requested window (often a 64-bit-prefetch/`above-4G` setting). It
> is a top "detected but does nothing" cause (§7).

### 3.2 Walking the capability lists by hand (once)

Capabilities are linked lists, and walking one once cements how `setpci`'s aliases and the
toolkit's `find_ext_cap()` work.

- **Legacy caps** start at the byte pointed to by config `0x34`. Each cap is
  `[cap_id (1B)][next_ptr (1B)] ...`; follow `next` until it's 0. The **PCI Express
  Capability** has ID `0x10` — Link/Device control and status hang off it.
- **Extended caps** start at `0x100`. Each has a 32-bit header:
  `[cap_id:16][cap_version:4][next_offset:12]`. Follow `next_offset` until 0 (or the header
  reads `0x00000000`/`0xFFFFFFFF`). **Advanced Error Reporting = ID `0x0001`**, **Secondary PCIe = `0x0019`**,
  **Lane Margining = `0x0027`**, **Downstream Port Containment (DPC) = `0x001D`**.

```python
# toolkit/backend.py - the extended-cap walk (paraphrased)
offset = 0x100
while offset:
    header = read_config(bdf, offset, 4)
    if header in (0, 0xFFFFFFFF):
        break
    if (header & 0xFFFF) == cap_id:          # low 16 bits = capability ID
        return offset                        # found it (e.g. AER at 0x100)
    offset = (header >> 20) & 0xFFF           # next-capability offset
```

`setpci` gives you symbolic aliases so you don't hard-code these: **`CAP_EXP`** = the PCIe
capability base, **`ECAP_AER`** = the Advanced Error Reporting extended-cap base, `ECAP_VSEC` = a vendor-specific
extended cap. So `setpci -s $BDF CAP_EXP+0x12.W` reads Link Status regardless of where the
PCIe cap physically sits.

\newpage

## 4. Link training, the LTSSM, and the latched status bits

### 4.1 The LTSSM

The **Link Training and Status State Machine (LTSSM)** brings a link up and maintains it.
Knowing the states maps a "stuck" symptom directly onto a physical cause:

| State | What happens | "Stuck here" means |
|---|---|---|
| **Detect** | Look for a partner (sense far-end Receive termination) | No device / no power / **PCIe Reset signal (PERST#)** not released / missing AC-cap or termination / dead PHY — the classic *"device not detected."* |
| **Polling** | Establish bit + symbol lock, exchange TS1/TS2, agree polarity (always at 2.5 GT/s) | **Reference Clock (REFCLK)** absent/wrong, **Spread Spectrum Clocking (SSC)** mismatch, RX DC offset, gross Signal Integrity, inverted polarity. |
| **Configuration** | Negotiate **link width** + lane numbers (x16→x8 falls out here) | Dead/noisy high lanes, **lane-reversal** unsupported, **bifurcation mismatch**, scrambler problem. |
| **L0** | **Normal operation** — data flows. This is where you want to live. | — |
| **Recovery** | Re-enter training to change speed, run equalization (Gen3+), or recover from errors | **Looping** Recovery = marginal SI / equalization (EQ) preset mismatch / RX won't lock at the new rate / thermal drift. The #1 *dynamic* failure. |
| **L0s / L1 / L2** | Low-power link states (see §6) | A too-slow exit can manufacture a Completion Timeout (CTO) (§9). |

**Speed changes happen in Recovery**, and so does **equalization** (§8). For Gen3+ the link
first trains to Gen1 in Polling, reaches L0, then renegotiates up through Recovery —
equalizing at each higher rate. **A link that *trains* to Gen4 but keeps re-entering Recovery
is marginal even though a snapshot shows Gen4.** This is why your monitor watches the training
bit over the soak, not just the final state.

### 4.2 Reading link speed, width, and the latches (PCIe Capability)

The negotiated link state lives in the **PCIe Capability** (not Advanced Error Reporting). Offsets are relative to
the PCIe-cap base (`CAP_EXP`):

| Register | Offset | Key fields |
|---|---|---|
| Link Capabilities (`LnkCap`) | `+0x0C` | Max speed [3:0], max width [9:4], **Data Link Layer (DLL) Link Active Reporting Capable (bit 20)** |
| Link Control (`LnkCtl`) | `+0x10` | Active State Power Management (ASPM) control [1:0], **Retrain Link (bit 5)**, Link Disable (bit 4), Common Clock (bit 6) |
| Link Status (`LnkSta`) | `+0x12` | **Current speed [3:0]**, **current width [9:4]**, **Link Training (bit 11)**, **DLLLA (bit 13)**, **LBMS (bit 14)**, **LABS (bit 15)** |
| Device Control 2 (`DevCtl2`) | `+0x28` | **Completion Timeout Value [3:0]**, CTO Disable (bit 4) |
| Device Capabilities 2 (`DevCap2`) | `+0x24` | Supported CTO ranges, CTO-disable-supported |
| Link Control 2 (`LnkCtl2`) | `+0x30` | **Target Link Speed [3:0]** (force a gen for retrain) |
| Link Status 2 (`LnkSta2`) | `+0x32` | **Flit Mode Status (bit 10)** — set in Gen6 FLIT mode |

**Speed code → GT/s:** `1`=2.5 (Gen1), `2`=5 (Gen2), `3`=8 (Gen3), `4`=16 (Gen4), `5`=32
(Gen5), `6`=64 (Gen6). Both `LnkCap[3:0]` (max) and `LnkSta[3:0]` (current) use this code.

The easiest read of the **current** speed/width is sysfs (`current_link_speed` /
`current_link_width`), which mirrors `LnkSta[3:0]`/`[9:4]`. But knowing the register lets you
read it via `setpci` when sysfs is stale, or on a port behind a switch:

```bash
setpci -s $BDF CAP_EXP+0x12.W        # raw Link Status word (CLS/NLW/LT/DLLLA/LBMS/LABS)
setpci -s $BDF CAP_EXP+0x0c.L        # Link Capabilities (max speed/width, DLLLARC bit 20)
```

### 4.3 The latched bits a snapshot misses

A 5 ms poll on "is it Gen4 x16 *right now*" misses a link that bounced to Recovery and back
between polls. `LnkSta` carries **latches that survive between reads** — these catch the
transients:

| `LnkSta` bit | Name | What it tells you |
|---|---|---|
| 11 | **Link Training (LT)** | Set *while* retraining (in Recovery). Flicking = the link is bouncing. |
| 13 | **DLLLA** (DL Link Active) | Drops to 0 on a link-down (DL_Down). A **latched link-down** detector — *but only meaningful if `LnkCap` bit 20 is set.* |
| 14 | **LBMS** (Link Bandwidth Management Status) | **Write-1-to-Clear latch**: set when speed/width changed via a *managed* retrain (software/hardware initiated). |
| 15 | **LABS** (Link Autonomous Bandwidth Status) | **W1C latch**: set when the hardware changed speed/width **autonomously** — i.e. it couldn't *hold* the higher rate. |

> **`LABS` is the gem.** `LABS` latching after a soak means **the hardware autonomously
> dropped speed/width during your test** — it trained to Gen4 x16 but couldn't hold it. A
> one-shot "current speed = Gen4 x16" check *passes* that unit; the `LABS` latch *fails* it
> correctly. This is the canonical **"trains fine, marginal under load/temperature"** catch,
> and it costs one extra register read. Arm it (W1C) before the soak:

```bash
setpci -s $BDF CAP_EXP+0x12.W=0xc000     # write 1 to bits 14,15 -> clear LBMS|LABS (arm)
#   ... run stress / thermal soak ...
setpci -s $BDF CAP_EXP+0x12.W            # re-read: bit15(LABS) or bit14(LBMS) set => it renegotiated
```

What the words actually look like, decoded by hand (Link Status is 16-bit; width field is
bits [9:4], so x16 = field value 0x10 sits at bit 8 = `0x0100`):

```text
# Healthy Gen4 x16, link up, freshly armed, held the rate through the soak:
$ setpci -s 0000:03:00.0 CAP_EXP+0x12.W
2104
  0x2104 = 0010 0001 0000 0100b
    [3:0]  = 0x4         -> Current Link Speed = 16 GT/s (Gen4)
    [9:4]  = 0x10        -> Current Link Width = x16   (the 0x0100 bit)
    bit11 (LT)    = 0    -> not training right now
    bit13 (DLLLA) = 1    -> data link active (the 0x2000 bit) -- link is up
    bit14 (LBMS)  = 0, bit15 (LABS) = 0  -> nothing renegotiated. PASS.

# Same link AFTER a hot soak -- it bounced down and recovered:
$ setpci -s 0000:03:00.0 CAP_EXP+0x12.W
e104
  0xE104 = 1110 0001 0000 0100b
    [3:0]=0x4 (16 GT/s), [9:4]=0x10 (x16)  -> ends Gen4 x16 again (a snapshot would PASS)
    bit13 (DLLLA) = 1    -> link is up
    bit14 (LBMS)  = 1    -> bandwidth changed via a managed retrain
    bit15 (LABS)  = 1    -> AUTONOMOUS bandwidth change -> it could not hold the rate -> FAIL
```

The point: both reads end at "Gen4 x16," so the speed/width fields alone pass the unit. Only
the **latched** LBMS/LABS bits (the high nibble: `0xE...` with bits 14-15 set vs `0x2...` with
them clear) separate the healthy link from the one that dropped out and clawed back during the
soak.

The toolkit's `linkstate.check_link(..., watch_s=...)` does exactly this: it arms the
latches, polls `LnkSta` over the window counting **retrains** (rising edges of LT), tracks the
**minimum** speed/width seen, and flags **bw_changed** if LBMS/LABS latched. A link that ends
at Gen4 x16 but retrained 40 times during the soak fails — a one-snapshot test would pass it.

**Gen6 / Flit mode.** When `LnkSta2+0x32` bit 10 (Flit Mode Status) is set, the error model is
FEC-based (§2), not LCRC-retry — switch your mental model before trusting AER correctable
counts on Gen6 silicon.

**The full LTSSM state is *not* in standard config space.** Detect/Polling/Config/Recovery
live in **vendor-specific** registers (Broadcom/PLX, Intel) or a PHY/debug interface — and on
NVIDIA GPUs, partially via `nvidia-smi`. What you *can* observe portably: the LT bit flicking,
`dmesg` link-down/up and speed-change lines, and recovery-entry counters where a device/switch
exposes them.

\newpage

## 5. AER in depth — the bits are the layer

**Advanced Error Reporting (AER)** is the extended capability (ID `0x0001`) where the hardware
**latches** correctable and uncorrectable errors. Its entire diagnostic value is that **the
bit names the layer**, which names the root-cause class. Your tool must never just say
`errors=5` — it must say *which bits*, because that's the first fork in the debug tree.

### 5.1 AER register map (offsets relative to the AER cap base)

| Offset | Register | Notes |
|---|---|---|
| `+0x00` | Capability Header | Cap ID `0x0001` |
| `+0x04` | **Uncorrectable Error Status** (`UNCOR_STATUS`) | **Write-1-to-Clear (W1C)**. Non-zero after a clean run = FAIL |
| `+0x08` | Uncorrectable Error **Mask** | 1 = not *reported* (may still latch in status) |
| `+0x0C` | Uncorrectable Error **Severity** | 1 = Fatal (→ ERR_FATAL, link reset / Downstream Port Containment), 0 = Non-Fatal |
| `+0x10` | **Correctable Error Status** (`COR_STATUS`) | **W1C**. Recovered errors; rising count = SI concern |
| `+0x14` | Correctable Error **Mask** | 1 = masked |
| `+0x18` | **Adv. Error Cap & Control** (`ERR_CAP`) | **First Error Pointer [4:0]** (which UNCOR bit the header log belongs to); End-to-End CRC (ECRC) gen/chk enables |
| `+0x1C` | **Header Log** (16 bytes) | First 4 DWORDs of the TLP that caused the **first** uncorrectable error |
| `+0x2C` | **Root Error Command** | **Root ports / Root Complex Event Collector (RCEC) only** |
| `+0x30` | **Root Error Status** | **Root ports only** |
| `+0x34` | **Error Source ID** | **Root ports only** — requester ID of the COR / UNCOR source |

> **Root-port-only caveat (often gotten wrong).** `Root Error Command` (+0x2C), `Root Error
> Status` (+0x30), and `Error Source ID` (+0x34) exist **only on Root Ports and Root Complex
> Event Collectors (RCECs)** — *not* on endpoints or switch downstream ports. To learn "which
> requester caused this," read **Error Source ID on the root port above the device**, not on
> the device. The root port is where the kernel Advanced Error Reporting driver assembles the OS-level story.

### 5.2 Correctable Error Status (`COR_STATUS`, `+0x10`) — the SI early-warning system

| Bit | Mask | Name | Meaning / typical root cause |
|---|---|---|---|
| 0 | 0x0001 | **Receiver Error** | Raw PHY symbol / 8b10b / 128b130b / sync-header error. **Pure Signal Integrity** (loss, jitter, crosstalk, marginal EQ). |
| 6 | 0x0040 | **Bad TLP** | TLP with bad **Link CRC (LCRC)**/sequence → NAK'd & replayed. **SI** (corruption on the wire). |
| 7 | 0x0080 | **Bad DLLP** | An ACK/NAK/FC/PM Data Link Layer Packet failed CRC. **SI** (DLLPs aren't retried, just dropped). |
| 8 | 0x0100 | **REPLAY_NUM Rollover** | Replay counter wrapped (4 consecutive replays of one TLP). **SI / marginal link.** |
| 12 | 0x1000 | **Replay Timer Timeout** | No ACK before REPLAY_TIMER expired → replay. **Classic marginal-link symptom.** |
| 13 | 0x2000 | **Advisory Non-Fatal** | An uncorrectable error was *demoted* to advisory. **Read `UNCOR_STATUS` to see what was demoted.** |
| 14 | 0x4000 | **Corrected Internal Error** | Device-internal corrected error (its own RAM Error-Correcting Code (ECC)). Device-side, not the link. |
| 15 | 0x8000 | **Header Log Overflow** | More uncorrectables than the 1-deep log could hold → *many* uncorrectables; go fix those. |

**The single most important correctable signature:** *Replay Timer Timeout + Bad TLP +
REPLAY_NUM Rollover clustering* = the link is repeatedly corrupting TLPs and retrying =
**physical-layer / signal integrity**. See that cluster and you are in §8 (SI), not §6
(protocol). The toolkit's `aer.CORRECTABLE_BITS` table carries exactly these names and
meanings so a decode reads like a sentence.

### 5.3 Uncorrectable Error Status (`UNCOR_STATUS`, `+0x04`) — any bit set after a clean run = FAIL

| Bit | Mask | Name | Meaning / typical root cause |
|---|---|---|---|
| 4 | 0x0000_0010 | **Data Link Protocol** | ACK/sequence-number protocol violation. **Protocol/IP bug**, sometimes severe SI corrupting seq#s. |
| 5 | 0x0000_0020 | **Surprise Down** | Link dropped to DL_Down unexpectedly. **Device/power lost, cable yanked, far-end PHY died, hot-unplug.** |
| 12 | 0x0000_1000 | **Poisoned TLP Received** | TLP arrived with the **EP (poison) bit** set (sender marked its own data bad). **Upstream data corruption**, not this link's Signal Integrity. |
| 13 | 0x0000_2000 | **Flow Control Protocol** | Credit accounting violated the rules. **Firmware/IP bug** (or corrupted FC DLLPs). |
| 14 | 0x0000_4000 | **Completion Timeout** | A non-posted request (usually a read) never got its completion before the CTO timer fired. **Upstream hung / wrong address / Active State Power Management-L1 exit too slow / switch dropped it / FW.** (§9) |
| 15 | 0x0000_8000 | **Completer Abort** | The completer refused the request (returned CA). **Target-side**: bad address, permission, completer-internal error. |
| 16 | 0x0001_0000 | **Unexpected Completion** | A completion with no matching outstanding request. **Switch mis-routing / duplicate tags / firmware.** |
| 17 | 0x0002_0000 | **Receiver Overflow** | More TLP data than advertised credits. **Flow-control/IP bug** (transmitter overran). |
| 18 | 0x0004_0000 | **Malformed TLP** | Structurally invalid TLP (bad length/byte-enables/addr/type). **FW/IP bug** or severe corruption. Usually **Fatal** by default. |
| 19 | 0x0008_0000 | **End-to-End CRC (ECRC) Error** | End-to-End CRC mismatch (only if ECRC gen+check enabled). **End-to-end corruption** an LCRC didn't catch through a switch/retimer. |
| 20 | 0x0010_0000 | **Unsupported Request** | Target doesn't support the request (access to an unimplemented Base Address Register/region). **Address-map / enumeration / driver bug.** |
| 21 | 0x0020_0000 | **Access Control Services (ACS) Violation** | Access Control Services blocked a peer-to-peer TLP. **Input-Output Memory Management Unit (IOMMU)/ACS policy** (often *expected* under virtualization). |
| 22 | 0x0040_0000 | **Uncorrectable Internal** | Device-internal uncorrectable (its own logic/RAM). Device-side. |
| 23–31 | — | MC-Blocked / AtomicOp-Egress / TLP-Prefix-Blocked / IDE / PCRC | Newer-spec integrity/encryption/translation errors. **Decode them anyway** so an unexpected high bit reads as a name, not "unknown bit 24." |

The fork this table encodes: **Receiver Error / Bad TLP / Replay Timer** cluster → physical
Signal Integrity (reseat, temperature, margining, equalization). **Completion Timeout / Malformed /
Unexpected Completion / FCP** → transaction/protocol (upstream device, switch, firmware).
*Where* the bit sits is the first half of triage.

> **Decode the full word.** A naive decoder that stops at bit 22 shows an IDE or AtomicOp
> error as "unknown bit 24" and sends a tech down the wrong path. Decode bits 23–31 even
> though they're rare on an ordinary compute board — being able to *name* an unexpected bit is
> half of triage.

### 5.4 Severity and mask — read them *before* you stress

`UNCOR_SEVERITY` (+0x0C) selects **Fatal(1)/Non-Fatal(0) per bit**; a Fatal error triggers
ERR_FATAL → link reset / DPC. `*_MASK` selects whether the error is *reported* (generates a
message/interrupt); masked errors may still latch in status. **Read severity + mask in the arm
step** so you know which bits will trigger Downstream Port Containment or a link reset *under you* mid-stress —
otherwise the device vanishes and you blame the wrong thing.

```bash
setpci -s $BDF ECAP_AER+0x0c.L     # UNCOR severity (1=Fatal -> ERR_FATAL/DPC)
setpci -s $BDF ECAP_AER+0x08.L     # UNCOR mask
setpci -s $BDF ECAP_AER+0x14.L     # COR mask
```

### 5.5 The write-1-to-clear discipline (your tool's core primitive)

Advanced Error Reporting status bits are **write-1-to-clear (W1C)**: writing a `1` clears a bit, writing `0` does
nothing. Two consequences drive the tool design:

1. **You must arm (clear) before a measurement.** The latches accumulate **from boot** —
   through power-on glitches, enumeration, link training, and every prior test. If you read
   without arming first you're reading history, not your stress window. **Arm → stress →
   read** is the only valid sequence.

2. **Clear only the bits that are set, and verify.** The elegant W1C trick: read the status
   register, then **write the value you just read back to it.** Because only the set bits are
   `1` in that value, the write clears exactly those and touches nothing else. Then re-read
   and confirm `0`.

```c
// toolkit aer._clear_one() (conceptual; real path is pread/pwrite on /sys/.../config)
uint32_t sts = read_config(fd, aer_base + AER_COR_STATUS, 4);
if (sts != 0) {                              // only act if something is set
    write_config(fd, aer_base + AER_COR_STATUS, sts, 4);   // W1C: write back the set bits
    uint32_t after = read_config(fd, aer_base + AER_COR_STATUS, 4);
    uint32_t stuck = after & sts;            // a bit that REFUSES to clear is a finding
}
```

> The blunt form `setpci -s $BDF ECAP_AER+0x04.L=0xffffffff` (write-all-ones) also clears
> everything — writing 1 to an already-clear or reserved bit is harmless. But
> **write-back-the-read-value** is the precise, auditable form: it only ever clears what was
> set, and **a bit that won't clear is itself a result** — a hardware fault re-latching it
> every cycle (a stuck PHY, a persistent internal error). Don't paper over it by re-clearing
> in a loop; record it. The toolkit returns `ClearResult(cleared, stuck)` for exactly this.

### 5.6 Who else is clearing your latches (the reconciliation trap)

The kernel `pcieport`/Advanced Error Reporting driver attaches to **root ports and RCECs** and will read-and-clear
AER status out from under you to print its `dmesg` lines. If both your tool and the kernel
clear the same W1C bits, each hides errors from the other and your counts are wrong and
irreproducible. Three options, in order of preference:

1. **Read the kernel's own counters instead of racing it.** With `CONFIG_PCIEAER_STATS` the
   kernel exposes monotonic, per-named-error counters it maintains:
   ```bash
   cat /sys/bus/pci/devices/$BDF/aer_dev_correctable   # RxErr/BadTLP/... tallies
   cat /sys/bus/pci/devices/$BDF/aer_dev_nonfatal
   cat /sys/bus/pci/devices/$BDF/aer_dev_fatal
   ```
   These survive between polls and aren't cleared by your `setpci` — **for a station tool this
   is often the robust source**: delta them across the stress window and never touch the raw
   W1C bits.
2. **Own the registers in a window the kernel won't report in** (mask the class you're
   counting, arm, stress, read raw status, restore).
3. **Disable kernel AER for an A/B test** (`pci=noaer` on the cmdline) to prove the kernel path
   isn't the noise source — diagnostic only, you never ship with AER off.

> Pick **one owner per run** and say which one in the record. The cleanest station design
> reads `aer_dev_*` (kernel-owned) and never clears the raw bits.

### 5.7 The Device Status fallback when AER is absent

Not every function has an Advanced Error Reporting capability (some endpoints, many simple devices). **Every PCIe
function has a Device Status register** in the PCIe Capability (`+0x0A`) with coarse
error-detected summary bits — your universal fallback:

| `DevSta` bit | Mask | Meaning |
|---|---|---|
| 0 | `DEVSTA_CORR` | Correctable Error Detected (no per-type breakdown) |
| 1 | `DEVSTA_NONFATAL` | Non-Fatal error detected |
| 2 | `DEVSTA_FATAL` | Fatal error detected |
| 3 | `DEVSTA_UR` | Unsupported Request detected |

The toolkit codifies the preference explicitly: `aer.error_source()` returns `"aer"` (rich
per-type), else `"devstatus"` (coarse summary), else `"none"`. The Bit Error Rate Test honors it — a device
with **no error source at all returns status `"skip"`**, never a false `pass` from zero errors
it couldn't actually measure. That "skip not pass" rule is a manufacturing-safety property: a
test must never claim a unit good on the strength of a measurement it didn't make.

\newpage

## 6. Power management: ASPM, L1 substates, and their failure modes

PCIe links save power by parking in low-power states. The state machine for *active* power
management (the link decides, in hardware) is **Active State Power Management (ASPM)**:

| State | Description | Exit latency | Power saved |
|---|---|---|---|
| **L0** | Active — normal operation | 0 | none |
| **L0s** | Standby — one direction idle | <1 µs | low |
| **L1** | Both directions idle, Phase-Locked Loop (PLL) may stay on | 2–4 µs (spec target; real parts advertise up to 32–64 µs) | medium |
| **L1.1** | L1 substate — common-mode kept, clock/PLL off | order ~10–20 µs | high |
| **L1.2** | L1 substate — common-mode *off* too (lowest power) | order ~100 µs (CLKREQ + common-mode re-establish) | highest |
| **L2** | Link off — full retrain on wake | >100 µs | maximum |

ASPM is controlled by `LnkCtl[1:0]`; the **L1 substates (L1SS)** have their own control
capability (`L1SubCtl1/2`). They are great for idle power and **a recurring source of two
failure modes in test:**

- **Latency that manufactures a Completion Timeout.** If the link is in L1/L1.1/L1.2 and the
  time to wake it and return to L0 exceeds the CTO timer, an in-flight read **times out** —
  surfacing as a `Completion Timeout` uncorrectable that *looks* like a link error but is
  really power-management latency (§9).
- **Transitions that look like errors / link unavailability.** Entering and exiting low-power
  states briefly makes the link unavailable; under a tight poll this can read as a transient
  or a retrain.

> **MT implication: usually disable ASPM during test.** ASPM transitions add noise that can
> masquerade as link errors, and lane margining (§10) *requires* Active State Power Management off. Disable it during
> the stress window and re-run to confirm observed errors aren't ASPM-related:
> ```bash
> # runtime, per-policy:
> echo performance | sudo tee /sys/module/pcie_aspm/parameters/policy
> # or globally at boot:   pcie_aspm=off   on the kernel cmdline
> ```

**Reset primitives you'll use alongside power management:** **Hot Reset** (via a downstream
port's Bridge Control register — resets everything below the port), and **Function Level Reset (FLR)** (resets a single function without disturbing the link — the clean way to re-init a
device between test phases).

\newpage

## 7. Enumeration and bring-up: when the link never reaches L0

If `lspci -nn` doesn't show the device (or shows it with no BARs), the link never reached
**L0** — the LTSSM is stuck early, and you can't read Advanced Error Reporting on a link that never came up. This
branch is about the *physical bring-up* facts.

| Stuck in (LTSSM) | First moves |
|---|---|
| **Detect** | Far-end **power** off? **PERST#** released at the right time after rails are up? **AC-coupling cap** / termination missing on a lane? Dead PHY? |
| **Polling** | **REFCLK** present (100 MHz, SSC if expected)? **SSC mismatch**? RX DC offset? Inverted polarity? Gross Signal Integrity (impedance/crosstalk)? |
| **Configuration** | Dead/noisy high lanes (configures smaller width), **lane-reversal** unsupported, **bifurcation mismatch**, scrambler problem. A link that comes up **x8 instead of x16** fell out here. |

```bash
# 1. Is it there at all? Right Vendor:Device at the expected BDF?
lspci -nn | grep -i <vendorID>
dmesg | grep -iE 'link (training|up|down)|not ready|timed out|Bifurcation'

# 2. Present but dead to software -- are BARs assigned?
lspci -vvv -s $BDF | grep -iE 'Region|BAR|ignoring'   # "can't assign"/"ignoring BAR" = dead

# 3. Rails + REFCLK + PERST#  (where the scope comes in, sec 14):
#    scope PHY supply rails at power-on (sequencing + level); confirm REFCLK at the slot;
#    confirm PERST# deasserts at the right time after rails are up.

# 4. Bifurcation vs the schematic:
#    does the BIOS/strap split (e.g. 2x8 or 4x4) MATCH the board's lane assignment?
```

> **Bifurcation is the #1 custom-card enumeration bug.** **Bifurcation** splits one wide root
> port into multiple narrower links — x16 → 2×x8, x16 → 4×x4, x8 → 2×x4 — configured in
> **BIOS/strap** and it *must match the board layout*. Zoox's compute platform almost
> certainly uses it to fan a single x16 root port out to multiple NVMe drives or custom
> devices. A mismatch shows up as "device not detected" or "trains at the wrong width." On a
> custom card, verify the BIOS bifurcation setting against the schematic's lane assignment
> **before** you suspect a dead PHY. (Ghost devices from a previous config sometimes need a
> cold boot to clear.)

**The custom-card bring-up order** (the JD's "custom PCIe devices" — for an off-the-shelf GPU
you have NVIDIA's tools; for Zoox's own card you have a schematic and a problem):

1. **Does it enumerate?** `lspci -nn` — right Vendor/Device ID at the expected Bus/Device/Function? If not:
   rails, REFCLK, PERST#, LTSSM stuck in Detect/Polling, or a layout error.
2. **Right class/BARs?** `lspci -vvv -s $BDF` — are BARs assigned? Unassigned = dead to
   software even though visible.
3. **Right link?** Current vs max speed/width. x16→x8 or Gen4→Gen3 on a new board is your
   first Signal Integrity finding.
4. **Does it respond?** Read a known register over MMIO; for a custom FPGA the design team
   gives you a scratch/ID register to prove the datapath.
5. **Driver / interrupts?** `lspci -k` — does the driver bind? Is `/proc/interrupts` counting
   during activity? (MSI/MSI-X: the device writes a special address to signal an interrupt;
   Message Signaled Interrupt Extended scales to 2048 vectors with per-vector masking. A device that looks fine in `lspci`
   but never interrupts is dead from the OS's view.)
6. **Errors under stress?** Now the BERT/AER monitor, hot and cold (§5, §11).
7. **Margin?** Lane margining for the per-lane eye (§10).

\newpage

## 8. Equalization, presets, and why links fall back

At Gen3 (8 GT/s) and above the channel — PCB traces, vias, connectors, cables — attenuates the
high-frequency content of the signal until the raw eye is **closed**. **Equalization** reopens
it.

- **TX equalization** shapes each bit with three finite impulse response (FIR) coefficients under a fixed-swing
  constraint:
  ```text
        pre-cursor (C-1):  boost the bit BEFORE a transition (compensates loss to come)
        cursor     (C0):   the main bit amplitude ("drive strength")
        post-cursor(C+1):  boost the bit AFTER a transition (cancels Inter-Symbol Interference (ISI) from the prev bit)
        constraint:        |C-1| + |C0| + |C+1| = const   (full-swing budget)
  ```
  More emphasis on the cursors = stronger transitions but a weaker main bit. **Too much →
  overshoot/ringing; too little → closed eye.**
- The spec defines **11 TX presets (P0–P10)** bundling coefficient ratios for different channel
  losses (P0 ≈ no emphasis for a short channel; higher presets add post/pre-emphasis for lossy
  channels). During training the hardware negotiates a preset *per lane*.
- **RX equalization**: **Continuous-Time Linear Equalizer (CTLE)** (continuous-time linear EQ — frequency-dependent gain that
  boosts high frequencies) + **Decision Feedback Equalizer (DFE)** (decision-feedback EQ — cancels ISI using past bit
  decisions). The receiver *adapts* these during training, which is why a marginal link can
  *still train* — the RX compensates until it can't. **That is exactly why lane margining
  (§10) matters more than pass/fail-on-link-up.**

**The 4-phase EQ handshake** runs in Recovery.Equalization at Gen3+:

| Phase | Who tunes what | What happens |
|---|---|---|
| 0 | Downstream Port TX -> Upstream Port RX | Link is now at the higher rate. The DSP transmits using the **preset values** it sent in its training sets; the USP applies those starting presets (and RxHint) to **its own** transmitter. Coarse setup, no coefficient requests yet. |
| 1 | both directions | Both partners exchange the **FS (Full Swing) / LF (Low Frequency)** bounds — the upper/lower limits on TX coefficients — and confirm they can hold the higher rate at a coarse Bit Error Rate (BER < 1e-4). |
| 2 | **USP tunes the DSP's TX** | The **upstream** port evaluates *its own* receiver and requests TX coefficient changes **from the downstream** port; the downstream adjusts its transmitter until the USP's Receive is optimized. |
| 3 | **DSP tunes the USP's TX** | The **downstream** port evaluates *its own* receiver and requests TX coefficient changes **from the upstream** port; fine-tuning until the DSP's RX hits its eye target. |

> **The phase ownership is a localization lever.** Phase 2 optimizes the **downstream-pointing**
> direction (Downstream Port transmitter → Upstream Port receiver); Phase 3 optimizes the **upstream-pointing**
> direction (USP transmitter → DSP receiver). A board that equalizes one direction fine but
> not the other (an asymmetric channel — a long TX trace on one side, a connector on one side
> only) shows up as an EQ failure or downgrade you can map back to a phase, which maps to a
> direction, which maps to a physical leg. This is the same per-direction thinking the receiver
> error model uses (per-receiver, never per-link) — the channel is two independent halves and
> EQ tunes each half separately.

**Why a link falls back to a lower speed/width — the single most common PCIe finding.** EQ
couldn't reach a usable eye at the higher gen, so the LTSSM negotiated down. Causes: trace
length/loss, **via stubs**, impedance discontinuities, connector seating, **temperature** (a
board that equalizes fine at 25 °C can fail at 55–85 °C), power-supply noise, a too-aggressive
or too-weak TX preset, or a BIOS **Target Link Speed** cap (`LnkCtl2[3:0]`).

### 8.1 Reading and overriding the negotiated presets

The **Secondary PCIe** extended capability (ID `0x0019`) holds the Gen3+ **Lane Equalization
Control** — per lane, the DSP TX preset, USP TX preset, and RX preset hints. Reading it tells
you which preset each lane negotiated; on a board where one lane is marginal, a different
preset there is a clue.

```bash
lspci -vvv -s $BDF | grep -A8 'Secondary PCI Express'   # per-lane EQ presets (if decoded)

# Force a gen to test EQ at a specific rate, then retrain:
setpci -s $BDF CAP_EXP+0x30.W                # LnkCtl2: [3:0] = Target Link Speed
setpci -s $BDF CAP_EXP+0x30.W=0x0003         # cap target at Gen3 (3)  -- example
setpci -s $BDF CAP_EXP+0x10.W=0x0020         # set Retrain Link (LnkCtl bit5) -> forces Recovery
```

> **Retrain warning.** Writing **Retrain Link** or **Target Link Speed** *perturbs a live
> link* — it will blip the device. Do it on a dev station or with the DUT quiesced, **never on
> a unit mid-soak on a production line.**

### 8.2 The TX-preset characterization sweep (the X-ES pattern, generalized)

Before lane margining was standard, the way to find the best equalization for a board was to
sweep TX presets and measure link quality at each — the grid-search a signal-integrity
engineer does manually on a bench, automated. At X-ES this was a script that iterated TX
pre-emphasis on a PLX/Broadcom switch, retrained, ran a quick functional check, then ran the
Bit Error Rate Test to measure quality — producing a **(preset, lane) → BER** matrix that told you which
preset gave the best eye for that specific board revision. Once characterized, the optimal
preset was locked into switch firmware for production.

The toolkit generalizes it as `margining.characterize_equalization()`: sweep `P0..P10`, set
the preset via the Secondary PCIe cap, toggle Retrain Link, run a short BERT + margining per
preset, and return an `EqSweep` whose `.best` is the preset with the **largest eye margin**
(tie-broken by fewest errors). On real hardware the per-step register write is the part you
validate per switch model; the *concept* is what transfers.

> **Why this still matters at Zoox.** Any time the PCB layout, connector, or cable changes —
> a new board revision, a different riser, a new cable assembly — you must **re-characterize
> the equalization**. EQ is sensitive to everything the channel does, and a board that passes
> at 25 °C can fail in burn-in. This is Design Verification (DV)/New Product Introduction (NPI) work that feeds the production setting.

\newpage

## 9. Completion Timeout: the ASPM / L1SS / CTO story

`Completion Timeout` (UNCOR bit 14) is the uncorrectable bit **most often misdiagnosed as Signal Integrity**
when it's actually **power-management latency or a hung completer**. A non-posted request
(usually a memory read) didn't get its completion before the Completion Timeout (CTO) timer fired. The header log
(§10.3) names *what* read and *who* issued it; this chapter finds *why it was slow.*

**The CTO timer lives in Device Control 2.** The timeout *range* is programmable in `DevCtl2`
(`+0x28`), **Completion Timeout Value field [3:0]**; `DevCap2` (`+0x24`) advertises supported
ranges and whether CTO can be disabled.

```bash
setpci -s $BDF CAP_EXP+0x28.W       # DEVCTL2: [3:0]=CTO value, [4]=CTO disable
setpci -s $BDF CAP_EXP+0x24.L       # DEVCAP2: supported CTO ranges, CTO-disable-supported
```

Default CTO ranges run ~50 µs up to ~64 s. A too-*short* CTO can manufacture timeouts on a
perfectly good but slow completer; a too-*long* one can hang the CPU on a dead one.

**The ASPM/L1 connection.** The classic "random hang / occasional CmplTO under no obvious
stress" is **L1-exit latency**: the link went into L1 (or deeper L1.1/L1.2), and the wake time
to L0 exceeded the CTO timer, so an in-flight read timed out (§6).

### 9.1 The decisive A/B test

```bash
# A: reproduce the CmplTO with ASPM as-shipped (record rate + header log).
# B: disable ASPM and re-run the EXACT same stress:
#    boot with  pcie_aspm=off   (or at runtime:)
echo performance | sudo tee /sys/module/pcie_aspm/parameters/policy
```

- **Timeouts vanish with `pcie_aspm=off`** → root cause is **L1-exit latency**. Fix is an
  ASPM/L1SS policy or exit-latency-advertisement change. **Do not** margin lanes or reseat
  connectors — it's not the wire.
- **Timeouts persist with ASPM off** → the completer is genuinely **hung or mis-addressed**.
  Back to the header log and the upstream device.

> This one A/B routinely saves a day of chasing the wrong layer. The high-leverage move is to
> rule out ASPM/L1SS **first** — it's a 10-minute reboot — before spending an hour on margining
> and reseating. CmplTO *looks* like a link error in AER and `dmesg`, and the instinct is to
> suspect the wire; resist it.

\newpage

## 10. Lane margining at the receiver — the scope-free eye

This is the modern technique that turns "sweep presets and run a Bit Error Rate Test" into a spec-standard,
push-button measurement, and it's a genuinely strong thing to bring to Zoox.

**Lane Margining at the Receiver** is a **mandatory PCIe Gen4+ feature** (extended capability
ID `0x0027`). It commands a receiver — *while the link stays in L0* — to **shift its sampling
point** in **time** (left/right of the data eye) and, on capable receivers, in **voltage**
(up/down), **per lane**, and report when errors start. Stepping the offset outward until
errors exceed a limit measures the **eye margin directly, on-die, with no oscilloscope.**

```text
        voltage
          ^         . . . . . . .          <- step up until errors   (voltage margin)
          |       .               .
   sample +     .      DATA EYE      .
   point  |       .               .
          |         . . . . . . .          <- step down until errors
          +---------------+-------+----> time (UI)
                          ^       ^
                    step left   step right   (timing margin, fractions of a UI)
```

Timing margining is required at Gen4 (16 GT/s); **voltage margining is mandatory at Gen5
(32 GT/s)** and up.

**The real Linux tool is `pcilmr`** — part of `pciutils` (≥ 3.13, May 2024; improved in 3.14),
no vendor SDK required. *This is the answer to "how do I margin a lane today."* There is no
generic kernel sysfs "margin this lane" interface; `pcilmr` drives the capability registers
from user space and hardcodes vendor quirks (e.g. Ice Lake) a hand-rolled sequence would miss.

```bash
sudo pcilmr --scan                              # links that can be margined (negotiated >=16 GT/s)
sudo pcilmr --margin -TV 0000:03:00.0           # all lanes, timing (T) + voltage (V)
sudo pcilmr --margin -TV -r 1,2,3,6 0000:03:00.0  # near RX(1), retimer RXs(2-5), far RX(6)
sudo pcilmr -o ./csv --full                     # margin every ready link, CSV out for limit-setting
```

Key flags: `-e <errlimit>` (default 4), `-d <dwell-sec>` (default 1), `-l <lanes>`,
`-r <recv#>` (1 = the port's own RX … 6 = far-end RX, **including retimers 2–5**), `-t/-T`
(timing), `-v/-V` (voltage), `-g` (grade in %UI or ps), `-c` (capabilities only). **Requires
root**, the link in **D0**, and **Active State Power Management + HW-autonomous features disabled** during the test
(pcilmr does the latter and warns).

### 10.1 Converting steps to UI and mV

What `pcilmr` does internally (useful when you parse its CSV or hand-roll a fallback):

$$ \text{timing\_margin\_UI} = \frac{\text{passing\_timing\_steps}}{\text{NumTimingSteps}} \times \frac{\text{MaxTimingOffset}}{100} $$

$$ \text{voltage\_margin\_mV} = \frac{\text{passing\_voltage\_steps}}{\text{NumVoltageSteps}} \times \text{MaxVoltageOffset} $$

`MaxTimingOffset` is a **percent of a Unit Interval** (so `/100` gives UI); `MaxVoltageOffset` is in mV
(or 10 mV units on some parts — verify per silicon). The conversion is anchored on the **UI**:
at 16 GT/s one UI ≈ 62.5 ps (1/16e9 s), at 32 GT/s ≈ 31.25 ps — so a "% of UI" margin halves in
picoseconds when you double the rate even though the percentage looks the same. Spec eye targets
`pcilmr` grades against: at **16 GT/s** min timing **30% UI (≈18.75 ps)** / recommended 38% UI
(≈23.75 ps), min voltage **15 mV** / recommended 21 mV; at **32 GT/s** min timing 30% UI
(≈9.375 ps), min voltage 15 mV. That shrinking-ps-at-fixed-%UI is *why* a board that margins
comfortably at Gen4 can fail at Gen5 with the identical channel — same percentage, half the
absolute eye.

The toolkit ships a **mock margining backend** (`margining.margin_link`) so you can demo the
sweep on a laptop — it derives a believable per-lane margin from an injected BER with seeded
per-lane variation, so a marginal link shows **one weak lane** (the realistic failure shape).
The default manufacturing limit is `DEFAULT_MIN_TIMING_UI = 0.25` UI. The robust real-hardware
path is to **shell out to `pcilmr` and parse its CSV** (richer alternatives: OCP `pci_lmt`,
Google `pcie_lmt` for Gen4/5/6, Oxide `lmar`). Either way the output is the per-lane
**timing/voltage margin** number.

> **Why this lands at Zoox.** Walking in able to say *"I'd add receiver lane margining to
> module test so we get a per-lane eye margin number and set a data-driven limit, instead of
> pass/fail on link-up"* is a high-leverage coverage improvement, not a script. It's the
> spec-blessed successor to the pre-emphasis sweep — and the **per-lane** number is what lets
> DV *set* an eye limit that MT then *checks* per unit.

### 10.2 Retimers and redrivers — and localizing the bad segment

Long channels (a board-to-board cable, a riser) insert repeaters:

- A **retimer** is a **protocol-aware** repeater: it recovers the data, re-equalizes, and
  re-transmits a clean eye, **resetting the jitter/loss budget** and **creating an independent
  link segment on each side** (PCIe allows **up to 2 retimers per link**). It shows up as a
  PCIe device with its own link/error state — *and crucially* it appears in lane margining as
  **additional Receiver Numbers** (`pcilmr -r 2..5`). That lets you margin the retimer's RX and
  **localize a marginal eye to "before vs after the retimer"** — i.e. board trace vs cable. On
  Zoox's cabled board-to-board links that's a huge lever.
- A **redriver** is an **analog** booster: **invisible to software**, no link state, you
  cannot margin or query it. If a link has a redriver and a marginal eye, you're back to the
  scope.

```bash
# Margin the near RX (recv#1), retimer RXs (2..5 if present), and the far RX (recv#6):
sudo pcilmr --margin -TV -r 1,2,3,6 0000:03:00.0
```

### 10.3 Decoding the AER Header Log — turning a bit into a sentence

When an uncorrectable error latches, Advanced Error Reporting captures the **first 4 DWORDs of the offending TLP**
in the **Header Log** (`+0x1C`), and the **First Error Pointer** (`ERR_CAP[4:0]`, `+0x18`)
says which uncorrectable bit that log belongs to. Decoding it turns "Completion Timeout" into
"a 4-byte memory read of address `0x05010000` by requester `00:04.0` timed out" — the
difference between a guess and a root cause.

**Where to get the raw DWORDs:**

```bash
lspci -vvv -s $BDF | grep -A1 'Header Log'        # HeaderLog: 04000001 00200a03 05010000 00050100
dmesg | grep -A4 'PCIe Bus Error' | grep 'TLP Header'   # the kernel prints the same 4 DWORDs
setpci -s $BDF ECAP_AER+0x1c.L ECAP_AER+0x20.L ECAP_AER+0x24.L ECAP_AER+0x28.L  # raw
```

**A worked decode** (`DW0 = 0x04000001`):

```text
DW0 = 0x04000001
  byte0 = 0x04 = 0b0000_0100
     [7:5] Fmt  = 0b000 -> 3-DWORD header, NO data  (a memory READ request)
     [4:0] Type = 0b00000 -> Memory class
     => Memory Read Request, 32-bit address (3DW header)
  Length [9:0] = 0x001 -> 1 DWORD requested (a 4-byte read)

DW1 = 0x00200a03
  [31:16] Requester ID = 0x0020 -> bus 0x00, dev 0x04, fn 0   (i.e. 00:04.0)
  [15:8]  Tag          = 0x0a
  [7:0]   Byte enables = 0x03   (first-DW BE / last-DW BE)

DW2 = 0x05010000  -> Address[31:2] (3DW header => 32-bit address)
  => target address ~ 0x05010000

DW3 = 0x00050100  -> next captured DWORD (for a 4DW/64-bit header, DW2/DW3 = Address[63:32]/[31:2])
```

**Read it as a sentence:** *"A 4-byte memory read of `0x05010000` by requester `00:04.0`
(tag 0x0a) triggered the first uncorrectable error."* Now go look: is `0x05010000` inside a
Base Address Register that exists? Is `00:04.0` the device you expect? If the bit set was **Completion Timeout**,
that read never completed — so the **completer of `0x05010000`** is the suspect (hung endpoint,
slow L1 exit, or a switch that dropped the completion), not the requester's link Signal Integrity.

> **Decode shortcuts.** Fmt `[7:5]`: `000`=3DW/no-data, `001`=4DW/no-data, `010`=3DW/with-data,
> `011`=4DW/with-data, `100`=TLP-prefix. Type `[4:0]`: `00000`=Memory, `00010`=I/O,
> `00100`/`00101`=Config Type0/1, `01010`=Completion. A **Completion** TLP's DW1/DW2 carry
> Completer ID, Completion Status, Byte Count, and Lower Address instead of an address — so an
> **Unexpected Completion** header log tells you *who completed* and *what status*, which is
> exactly the mis-routing / duplicate-tag clue.

**The toolkit captures the header log + First Error Pointer for every uncorrectable**, so a
failure arrives with its requester/type/address already decoded — the single highest-value
diagnostic addition over `errors=N`.

\newpage

## 11. The BERT: bit-error rate to a confidence level

This is your tool's core, and it's the X-ES pattern: a **C engine counts errors and bits
fast**; a **Python layer decides whether you've proven the link good**. The goal of a
manufacturing Bit Error Rate Test (BERT) is *not* to measure the exact Bit Error Rate (BER) — it's to **prove BER < target (e.g.
1e-12) at a stated confidence, in the shortest time**, and to fail fast when it can't.

### 11.1 The math, in one paragraph (full derivation: Math chapter §10)

Model errors as **Poisson** with mean `λ = n·p`, where `n` = bits transferred and `p` = the
target BER. The **confidence that the true BER is below `p`**, given `E` errors observed:

$$ \mathrm{CL} = 1 - \mathrm{PoissonCDF}(E;\, np) = 1 - \sum_{k=0}^{E} e^{-np}\frac{(np)^k}{k!} $$

Two results run the whole test:

- **Zero-error case** (the common one): set `E=0` and solve for the bits needed,
  $n = -\ln(1-\mathrm{CL})/p.$ For 95% confidence at `p = 1e-12`: `n ≈ 3.0×10¹²` bits — the
  **"3/BER" rule** (since `−ln(0.05) ≈ 2.996`). That's the target your tool transfers cleanly.
- **Reporting the bound** (any error count): the exact upper bound on BER you can claim at
  confidence CL after `n` bits with `E` errors is the chi-squared form
  $\mathrm{BER_{upper}} = \chi^{2}_{\mathrm{inv}}(\mathrm{CL},\,2E{+}2)/(2n),$ which reduces to
  the zero-error formula when `E=0`.

This is why the math is in **Python** (`ber.py` — regularized incomplete-gamma / chi-squared
inverse, with a pure-Python fallback so it runs on a bare laptop) while the **C engine just
counts** — large `np` products and factorials need care that belongs in the high-level layer,
not the tight counting loop.

### 11.2 Bits → time

The engine accounts `n` either by **measuring** a real workload's throughput or
**theoretically** from the link: `n ≈ link_speed × link_width × efficiency × time`
(`backend.link_bits_per_second`). Sanity points: Gen4 x16 ≈ 31.5 GB/s ≈ 2.5×10¹¹ bit/s, so
3×10¹² bits ≈ **~12 s** of clean traffic; NVMe Gen3 x4 ≈ 3.5 GB/s, so ≈ **~110 s**. Those are
realistic module-test durations — which is the point of choosing a confidence target rather
than running forever.

### 11.3 The loop the tool runs (arm → stress → read → decide)

`bert.run_bert()` implements exactly this:

1. **Idle baseline (begin).** Quiesce the link, clear Advanced Error Reporting, read what's set with *no* traffic.
   Bits set at idle are a **constant fault** (or severe marginality), not a per-error rate —
   recorded and excluded from the rate count so a real fault can't run the count away.
2. **Arm.** Clear AER status with the W1C read-back primitive (§5.5) and confirm zero; start
   the measurement window clean.
3. **Stress + read.** Drive traffic (or account theoretical bits) and poll AER. Each distinct
   correctable **bit** set per poll counts as one error of that type (a tighter lower bound
   than 1-per-poll, exact at the low rates where a unit passes), then clear-and-re-arm fast so
   the next error can latch. Any **uncorrectable** → immediate fail.
4. **Decide (sequential).** Feed `(errors, bits, p, CL)` to `ber.sequential_decision`:
   - **pass** — proved BER ≤ target at the confidence level (enough clean bits);
   - **reject/fail** — even the optimistic lower bound on BER exceeds the target → fail fast,
     no extra runtime will rescue it;
   - **continue** — undecided; extend the window if the takt budget allows
     (`extend_budget × the zero-error target`, hard-capped by `max_seconds`).
5. **Idle baseline (end).** Errors still present with no traffic = a real fault → fail
   regardless of the rate verdict.

The diagnostics ride along: which AER bits, the per-type correctable counts, the uncorrectable
decode, the Gen6/FLIT note, and the idle-fault flag — so a failure comes with its root-cause
evidence already attached, not just a number. (For high error rates a Python poll loop can't
clear-and-recount fast enough; that's the `engine="c"` path — the C binary counts in tight
increments while the same Python "conductor" makes the sequential decision and bounds the
runtime.)

> **The Design Verification (DV) ↔ Manufacturing Test (MT) bridge (why MT must capture the parameter, not just the verdict).** The *same*
> BERT engine serves both modes. **DV:** run it across voltage/temperature corners and TX
> presets and *plot the surface*. **MT:** run it once to a confidence target and emit
> pass/fail. The captured `(errors, bits, BER-bound)` is what later feeds Statistical Process Control (SPC) and
> guard-banding — *you cannot set a good limit on data you didn't keep.*

\newpage

## 12. Switches, retimers, and walking the whole chain

Zoox builds custom switch/fan-out boards and cabled board-to-board links, so you rarely debug
a single point-to-point link in isolation — you debug a **tree**. Walk all of it.

```bash
lspci -tv                                   # the tree: root ports -> switches -> endpoints
# For EVERY link in the path (root port, switch USP, each switch DSP, endpoint),
# check speed/width AND AER -- not just the endpoint:
for bdf in 0000:00:1c.0 0000:02:00.0 0000:03:00.0 ; do
  echo "== $bdf =="
  cat /sys/bus/pci/devices/$bdf/current_link_speed /sys/bus/pci/devices/$bdf/current_link_width
  setpci -s $bdf ECAP_AER+0x10.L ECAP_AER+0x04.L   # COR / UNCOR status on this link
done
```

A switch is itself a unit-under-test: bad switch SerDes (→ correctable cluster on a DSP), a
switch-firmware flow-control bug (→ FCP / RX_OVER), or a switch that drops completions (→
**Completion Timeout at the endpoint above it**, even though the endpoint's own link is clean).
On the **root port** (only), **Error Source ID** (+0x34) gives the requester ID of the COR/
UNCOR source — on a tree with switches that's how you attribute an error to the *true*
originating device rather than the port that happened to log it.

The toolkit makes this first-class. `topology.analyze_chain()` decomposes an endpoint's path
into **per-Bus/Device/Function error directions** (each BDF's AER reports *one direction of one link* — its
receiver side) and **per-link downgrade targets** (speed/width is a per-*link* property owned
by the downstream port, read *once*). `diagnostics.diagnose_chain()` then runs **one** stress
window (the endpoint BERT — its traffic traverses every link), monitors AER per-direction on
every BDF, checks each link's downgrade at its downstream port, and watches `dmesg` for PCIe/
AER events across the whole window. Any error/downgrade/uncorrectable on any segment fails the
chain, **pinned to the exact BDF+direction or link.** This is the data model behind §13.

\newpage

## 13. The diagnosis playbook

The bench artifact: given a misbehaving link, localize it to a layer and a physical cause, in
a defensible order, with the exact commands.

### 13.1 The 30-second decision tree

```text
0. ARM. Clear AER (W1C). Record: LnkCap both ends, current speed/width, DPC state,
   ASPM/L1SS state, severity+mask, temperature.

1. Does it ENUMERATE?  (lspci -nn)
   NO  -> LTSSM stuck (Detect/Polling/Config). Cause set: power/REFCLK/PERST#/
          bifurcation/dead PHY/AC-cap. Evidence: dmesg "training" stuck; rails on
          scope; BIOS bifurcation vs schematic; reseat changes it.                  (sec 7)

2. Right SPEED and WIDTH?  (current vs max; lspci prints "downgraded")
   SPEED low (Gen4->Gen3) -> EQUALIZATION / SI margin / BIOS gen-cap / THERMAL.
          Evidence: dmesg gen-change; retest hot AND cold; pcilmr margin shrinks. (sec 8,sec 10)
   WIDTH low (x16->x8)    -> connector / dead lane / bifurcation / lane-reversal.
          Evidence: per-lane pcilmr finds the dead lane; reseat; BIOS bifurcation.    (sec 7)

3. ERRORS under stress?  Decode WHICH AER bits.                                    (sec 5)
   Correctable {RxErr,BadTLP,BadDLLP,ReplayTO,RollOver} -> PHYSICAL / SI.    (sec 5,sec 8,sec 10)
          Evidence: count rises with temperature; pcilmr margin low on one lane;
          reseat/cable swap changes it; a better TX preset improves it.
   Uncorrectable {CmplTO,UnexpCmpl,MalformedTLP,FCP,RxOverflow} -> PROTOCOL/FW/UPSTREAM.
          Evidence: header-log decode names requester/type/address; reproducible
          regardless of temp/reseat; tied to a traffic pattern or FW/driver.      (sec 5,sec 9,sec 12)
   {PoisonedTLP,ECRC} -> DATA CORRUPTION upstream / through a switch or retimer.  (sec 12)
   {SurpriseDown}     -> POWER / mechanical.                                       (sec 14)

4. Errors ONLY hot or ONLY under load -> THERMAL SI or POWER droop.            (sec 8,sec 14)
5. Nothing reproduces but field-flaky -> MARGIN is the discriminator: a pcilmr
   margin below limit on one lane is a latent SI fail a pass/fail link-up test misses. (sec 10)
```

The reason it works: **different root causes leave different evidence in different
registers.** Signal Integrity (SI) = correctable clusters that move with temperature and shrink the eye margin;
protocol = uncorrectable bits whose header log names a transaction; power = Surprise Down /
load-correlated bursts; firmware = reproducible, temperature-independent, version-tied
uncorrectables.

**Failure-signature → root-cause quick lookup.** The same content as the tree, indexed the way
a symptom actually arrives at the bench — by what you *observe* first:

| Observed signature | Most likely root cause | The one test that confirms it |
|---|---|---|
| Device absent from `lspci -nn` | LTSSM stuck pre-L0 (power/REFCLK/PERST#/bifurcation/dead PHY) | dmesg "training"/"timed out"; scope rails+REFCLK+PERST#; BIOS bifurcation vs schematic |
| Present, `Region N: <unassigned>` / "can't assign BAR" | BIOS couldn't fit the MMIO window (often 64-bit/above-4G) | Toggle above-4G decoding / resize BAR; re-enumerate |
| `Speed ...(downgraded)`, width OK | EQ / SI margin / BIOS gen-cap / thermal | Retest hot AND cold; `pcilmr` margin; check LnkCtl2 Target Link Speed cap |
| `Width ...(downgraded)`, speed OK | Dead lane / connector / bifurcation / lane-reversal | Per-lane `pcilmr` finds the dead lane; reseat; BIOS bifurcation |
| Ends at full speed/width but `ABWMgmt+` (LABS) latched | Trained high, could NOT hold it (marginal under load/temp) | Arm LBMS/LABS, soak, re-read; count LT retrains over the window |
| `Train+` flickering across reads | Link bouncing through Recovery | Poll LnkSta over the soak; correlate with temperature/load |
| COR cluster RxErr+BadTLP+ReplayTO | Physical / SI on the wire | Count rises with temp; one-lane `pcilmr` low; reseat/cable swap moves it; better preset helps |
| `Bad DLLP` heavy, little else | SI on the DLLP path (same SI workup) | Same as above; DLLPs aren't retried so this is a pure-SI tell |
| UNCOR Completion Timeout, no SI cluster | ASPM/L1-exit latency OR hung/mis-addressed completer | `pcie_aspm=off` A/B: vanishes => L1 latency; persists => header-log the completer |
| UNCOR Malformed/UnexpCmpl/FCP/RxOverflow | Protocol / firmware / IP bug | Reproducible regardless of temp/reseat; tied to traffic pattern or FW/driver version |
| UNCOR Poisoned TLP / ECRC | Data corruption upstream / through a switch or retimer | Walk the chain; check Error Source ID at the root port; margin each segment |
| UNCOR Surprise Down | Power lost / browned out / connector opened / hot-unplug | Scope the device rail at the load step; reseat; swap PSU |
| Device vanishes then re-enumerates | DPC contained a fatal OR a power glitch reset it | dmesg DPC containment + trigger reason vs a clean re-enumerate; rail scope at the event |
| Bursts correlated with load steps | Rail droop/ripple at a current transient | Scope rail AC-coupled under the same load profile; back off one load |
| Nothing reproduces but field-flaky | Latent marginal eye that still trains | `pcilmr` per-lane margin vs a UI limit — link-up alone passes it |

### 13.2 Arm before you measure (the full checklist)

```bash
BDF=0000:03:00.0
# 1. Static facts BEFORE you touch anything (these don't change under test):
lspci -vvv -s $BDF | sed -n '/LnkCap:/p; /LnkSta:/p; /LnkCap2:/p; /LnkSta2:/p'
cat /sys/bus/pci/devices/$BDF/{max,current}_link_speed
cat /sys/bus/pci/devices/$BDF/{max,current}_link_width
# 2. Severity + mask FIRST (so you know which bits trip DPC/reset under you mid-test):
setpci -s $BDF ECAP_AER+0x0c.L      # uncorrectable severity (1=Fatal -> ERR_FATAL/DPC)
setpci -s $BDF ECAP_AER+0x08.L ECAP_AER+0x14.L   # UNCOR mask, COR mask
# 3. DPC + ASPM/L1SS state (these change what a fatal error DOES to your test):
lspci -vvv -s $BDF | grep -A2 -iE 'DPC:|ASPM|L1SubCtl'
# 4. ARM: clear correctable + uncorrectable status (W1C):
setpci -s $BDF ECAP_AER+0x10.L=0xffffffff   # clear COR_STATUS
setpci -s $BDF ECAP_AER+0x04.L=0xffffffff   # clear UNCOR_STATUS
# 5. VERIFY the arm took (both should now read 0):
setpci -s $BDF ECAP_AER+0x10.L ECAP_AER+0x04.L
```

### 13.3 Decoding `lspci -vvv` for a GPU

`lspci -vvv` already decodes the PCIe-cap and Advanced Error Reporting words for you — the skill is knowing which
lines carry a verdict. A realistic (lightly edited) capture of a degraded GPU link, with the
load-bearing lines called out:

```text
03:00.0 VGA compatible controller: NVIDIA Corporation ... (rev a1)
        ...
        Capabilities: [68] Express (v2) Endpoint, MSI 00
                DevCap: MaxPayload 256 bytes, PhantFunc 0
                DevCtl: ... MaxPayload 256 bytes, MaxReadReq 512 bytes
                DevSta: CorrErr+ NonFatalErr- FatalErr- UnsuppReq-  <- coarse summary (DevStatus fallback)
                LnkCap: Port #0, Speed 16GT/s, Width x16, ASPM L0s L1   <- what THIS end can do
                LnkCtl: ASPM L1 Enabled; ... Retrain- CommClk+         <- ASPM L1 on -> CmplTO suspect
                LnkSta: Speed 8GT/s (downgraded), Width x8 (downgraded) <- what it IS: TWO findings
                        TrErr- Train- SlotClk+ DLActive+ BWMgmt+ ABWMgmt+  <- LBMS+ AND LABS+ latched!
                LnkCap2: Supported Link Speeds: 2.5-16GT/s, ...
                LnkCtl2: Target Link Speed 16GT/s, ...
                LnkSta2: Current De-emphasis Level: -6dB, EqualizationComplete+ EqualizationPhase3+
        Capabilities: [100 v2] Advanced Error Reporting
                UESta:  DLP- SDES- TLP- FCP- CmpltTO- CmpltAbrt- UnxCmplt- RxOF- MalfTLP- ECRC- UnsupReq- ...
                UEMsk:  ... (which UNCOR bits are masked from reporting)
                UESvrt: DLP+ SDES+ ... FCP+ ... MalfTLP+ ...          <- which bits are FATAL (-> DPC/reset)
                CESta:  RxErr+ BadTLP+ BadDLLP- Rollover- Timeout+ AdvNonFatalErr- <- COR bits set: SI cluster
                CEMsk:  ...
                AERCap: First Error Pointer: 00, ECRCGenCap+ ECRCGenEn- ECRCChkCap+ ECRCChkEn-
        Capabilities: [bb0 v1] Lane Margining at the Receiver  <- present => pcilmr can margin this link
```

How to read it, top to bottom:

- **`LnkSta: Speed 8GT/s (downgraded), Width x8 (downgraded)`** — the headline. `lspci` prints
  `(downgraded)` whenever current < `LnkCap`. **Two** tags = a speed finding *and* a width
  finding; chase both (speed → equalization/SI/thermal; width → connector/dead-lane/
  bifurcation). Compare against the *other* end's `LnkCap` — the negotiated max is the **min**
  of the two ends, so a GPU advertising 16GT/s behind a root port capped at 8GT/s is a config
  ceiling, not a defect.
- **`DLActive+ BWMgmt+ ABWMgmt+`** on the `LnkSta` line — `lspci`'s names for **DLLLA**, **LBMS**,
  and **LABS**. `ABWMgmt+` (LABS) is the gem: this link **autonomously downgraded** — it could
  not hold the higher rate. A snapshot of "Speed 8GT/s" alone doesn't tell you *whether it ever
  tried 16*; `ABWMgmt+` does.
- **`Train-`** is the LT bit at the instant of capture; if it flickers `Train+` across repeated
  reads the link is bouncing through Recovery (the live-retrain tell a single read misses).
- **`CESta: RxErr+ BadTLP+ ... Timeout+`** — the correctable cluster. RxErr + BadTLP + Replay
  Timer Timeout together is the canonical **physical-layer / Signal Integrity** signature.
- **`UESvrt:`** — read this in the **arm** step: it tells you which uncorrectable bits are Fatal
  and will trip DPC / a link reset *under you* mid-stress.
- **`EqualizationComplete+ EqualizationPhase3+`** on `LnkSta2` — the link finished all four EQ
  phases. If you ever see it stuck below Phase 3 with a downgrade, EQ itself failed to converge
  (channel too lossy for any preset at that rate).
- **`Lane Margining at the Receiver`** capability present → this link is margin-able with
  `pcilmr` (it negotiated ≥ 16 GT/s).

The `+`/`-` suffix is set/clear throughout. `CESta`/`UESta` are the decoded AER status words —
read which bits, then go to the AER section.

### 13.4 dmesg signatures

```bash
dmesg | grep -iE 'pcie|aer|link|train|dpc|bifurcation'
```

| dmesg line (paraphrased) | Reading |
|---|---|
| `pcieport ...: AER: Corrected error received` + `BadTLP`/`Timeout` | Correctable SI cluster — §8/§10 |
| `... AER: Uncorrected (Non-Fatal) error` + `TLP Header: ...` | Uncorrectable; **decode the header log** (§10.3) |
| `pci ...: ... link is down` / `... Link Down` | Link-down event — Surprise Down or DPC; correlate timestamp |
| `... downgraded link to 8 GT/s` / `... not capable of link width x16` | Speed/width fallback at training — §8/§7 |
| `pcieport ...: DPC: containment event` + trigger reason | DPC fired on a fatal error — §13.6 |
| `... timed out` during enumeration | LTSSM never reached L0 — §7 |

### 13.5 Link-down vs link-degrade — the discriminator

- **Link-down** = the link dropped out of L0 entirely. Evidence: **DLLLA** drops (if capable),
  **Surprise Down** (UNCOR 5) latches, `dmesg` "link is down," the device may vanish. Causes:
  power/mechanical (§14), a far-end PHY dying, Downstream Port Containment (DPC) containment (§13.6).
- **Link-degrade** = the link stayed up but at a lower speed/width, or it's bouncing. Evidence:
  `LnkSta` current < max, **LABS** latched (autonomous downgrade — the "couldn't hold it" tell,
  §4.3), **LT** flicking (retrains), correctable clusters rising. Causes: equalization/SI
  margin, thermal (§8), connector/dead-lane (§7).

A snapshot conflates these; the **latches separate them**. `LABS` set + final state Gen4 x16 =
it degraded and recovered during the soak = a **fail** a one-shot test passes.

### 13.6 DPC — when the device vanishes on purpose

**Downstream Port Containment (DPC)** (DPC, ext-cap ID `0x001D`) is a root/switch downstream-port
mechanism that, on a Fatal/Non-Fatal error, **automatically disables the link** to contain the
error (stops bad TLPs propagating, stops a hung read from hanging the CPU). When DPC fires,
**the device disappears**, then the kernel attempts recovery and re-enumerates.

```bash
lspci -vvv -s <root-or-switch-DSP-bdf> | grep -A3 'DPC:'   # Enabled? Trigger reason?
dmesg | grep -iE 'DPC|containment'                          # WHY it fired = the evidence
#   trigger reason: uncorrectable / ERR_NONFATAL / ERR_FATAL / RP-PIO / SW-trigger
#   RP_PIO_* logs the offending root-port read that tripped RP-PIO
```

For MT it's a **double-edged sword**: it cleanly *captures and isolates* a fatal event (great
forensics — the trigger reason tells you *why*) **but it also yanks the device out from under
your test**. Decide per station: **DPC on** = a contained fatal is a clean, attributed event,
but your stress run ends when it fires; **DPC off** (`pcie_ports=native` to let the OS own it,
or firmware-disabled) = the link stays up so you keep stressing past the first fatal. Know
which one your root ports are set to *before* a run so "the device vanished" is read correctly
(DPC containing a real fatal vs a power glitch vs a hot-unplug).

### 13.7 Confirming signal integrity and localizing the lane

Once you've decoded a correctable cluster (§5.2) or a fallback (§13.1), confirm SI — the
discriminators are **temperature dependence**, **eye margin**, and **reseat/cable
sensitivity**:

1. **Decode which correctable bit(s)** — RxErr/BadTLP/ReplayTO cluster confirms PHY-layer.
2. **Margin the lanes** (`pcilmr -TV`) — a margin **below limit on one lane** localizes it to
   a connector pin, a via, a SerDes, or an AC-cap on that lane.
3. **Sweep temperature** — count correctables hot vs cold; **margin shrinking with
   temperature** is the Signal Integrity fingerprint (the reason to test hot).
4. **Reseat / swap the cable** — if the symptom moves with the connector/cable, it's
   mechanical/contact SI, not silicon.
5. **Try a better TX preset** — if a preset change improves margin/error rate, the channel was
   under-equalized.

### 13.8 The correctable-error storm

A flood of correctable errors during stress (the screen scrolling AER lines) is the common
"link works but is marginal" failure. Triage:

```bash
# 1. ARM both status registers (sec 13.2). 2. Run stress 10 min (gpu-burn / fio / iperf3).
# 3. Read the kernel-maintained counters (don't race the W1C bits, sec 5.6):
cat /sys/bus/pci/devices/$BDF/aer_dev_correctable     # which named errors, and how many
# 4. Classify the cluster:
#    BadTLP + ReplayTO + RollOver  -> SI (reseat, temperature, margining, preset)  sec 8/sec 10
#    Bad DLLP heavy                -> SI on the DLLP path (same SI workup)
#    AdvisoryNonFatal              -> an uncorrectable got demoted; read UNCOR_STATUS  sec 5.3
# 5. Correlate: temperature (thermal chamber?), load step (rail droop?), one lane (margining?).
```

For MT a correctable rate isn't automatically a fail — set a defensible limit (e.g. the BERT's
confidence target, or `< N` correctables/hour). But a *cluster that grows with temperature* or
*localizes to one lane* is a reject even if the count is "low," because it's a latent SI defect
that will worsen in the field.

### 13.9 Power, mechanical, and the load-vs-temperature split

When AER shows **Surprise Down**, or correctables come in **bursts that correlate with load
steps**, suspect **power integrity or a mechanical/contact problem**, not the SerDes channel.

| Symptom | Reading | Confirm with |
|---|---|---|
| **Surprise Down** under load | Far end lost power / browned out, or a connector momentarily opened | Scope the PHY/device rail at the load step; reseat; swap PSU |
| Correctable **bursts at load steps** | Rail droop/ripple at a current transient corrupts the eye transiently | Scope rail AC-coupled under the same load profile; correlate timestamps |
| Errors only under **combined load** (GPU+NVMe+link busy) | System power budget / shared-rail droop | Measure the rail with everything loaded; back off one load |
| Device **vanishes then re-enumerates** | DPC fired (§13.6) OR a power glitch reset it | `dmesg` for DPC containment vs a clean re-enumerate; rail scope at the event |

> **The discriminator (power droop vs thermal SI):** power droop is **load-correlated** (it
> tracks current transients and improves when you reduce load even at the same temperature);
> thermal Signal Integrity is **temperature-correlated** (it tracks junction temperature and improves when
> you cool the part even at the same load). **Vary load at fixed temperature, then temperature
> at fixed load, to separate them.** A lot of "digital" PCIe failures are power/SI failures —
> the engineer who reaches for the **scope and the AER decode together** closes the
> intermittent ones.

### 13.10 Self-testing the AER pipeline with `aer-inject`

Before you trust a station's Advanced Error Reporting decode/clear/count path, validate it with **no bad hardware**
by *injecting* a known error and asserting the pipeline reports exactly that (needs a kernel
with `CONFIG_PCIEAER_INJECT` → `/dev/aer_inject`).

```bash
cat > badtlp.aer <<'EOF'
AER
PCI_ID 0000:03:00.0
COR_STATUS BAD_TLP
HEADER_LOG 0x04000001 0x00200a03 0x05010000 0x00050100
EOF
sudo aer-inject badtlp.aer
dmesg | tail        # confirm the injected BadTLP appears AND decodes correctly
```

Inject one COR (`RCVR, BAD_TLP, BAD_DLLP, REP_ROLL, REP_TIMER`) and one UNCOR (`TRAIN, DLP,
POISON_TLP, FCP, COMP_TIME, COMP_ABORT, UNX_COMP, RX_OVER, MALF_TLP, ECRC, UNSUP`) and assert
the five-point test: (a) the correct bit is set in status, (b) the header log matches what you
injected, (c) the First Error Pointer points at the right uncorrectable bit, (d) your W1C clear
actually clears it, (e) your kernel-counter delta is exactly one. That **is** the AER
self-test — the analog of a golden-unit correlation. A station whose Advanced Error Reporting decode is subtly
wrong (off-by-one bit, doesn't read the header log, races the kernel) mis-triages every real
failure; `aer-inject` proves the pipeline correct on a *known* input so you trust it on
*unknown* hardware.

\newpage

## 14. How this maps to manufacturing test

The whole point: turn each PCIe finding into a **test placed at the earliest phase that can
catch it**, with a defensible pass/fail and a captured parameter. The two structural facts
that shape every PCIe test:

> **Bit errors are per-Bus/Device/Function and per-direction, evaluated at the receiver. Link downgrades are
> per-link and seen at both ends.**

That's not pedantry — it's the data model:

- A link's two directions are independent differential pairs. **Advanced Error Reporting on a given BDF reports the
  errors its receiver saw** — one direction of one link. Four BDFs in a chain = **four
  independent error counts**, each pinned to a direction. So a bit-error problem is attributed
  to a **BDF + direction** (the toolkit's `ChainSegmentResult`), never lumped per link.
- Speed/width (and the LBMS/LABS latches) are a **negotiated link property — both ends report
  the same value.** So a downgrade is read **once, at the downstream port that owns the link**
  (the toolkit's `ChainLink` / `ChainLinkResult`), never double-counted across its two BDFs.

Getting this right is what lets a chain diagnostic say *"errors on `02:00.0`'s receiver
(downstream direction), and the `00:1c.0↔02:00.0` link downgraded to Gen3"* — two distinct,
correctly-scoped findings — instead of a vague "the path has errors."

**Worked, on a 3-hop chain** (root port → switch → GPU). The endpoint BERT's traffic traverses
every link, so one stress window exercises all of them; you read each link's own AER and each
link's negotiated speed/width:

```text
  Root Port            Switch                       GPU (EP)
  00:1c.0  ===linkA===  02:00.0(USP)               .
                         02:01.0(DSP)  ===linkB===  03:00.0
  ---------------------------------------------------------------------------
  AER receivers (per BDF = one direction of one link):
    00:1c.0  AER -> errors the ROOT PORT's RX saw   (upstream-pointing traffic on linkA)
    02:00.0  AER -> errors the switch USP's RX saw  (downstream-pointing traffic on linkA)
    02:01.0  AER -> errors the switch DSP's RX saw  (upstream-pointing traffic on linkB)
    03:00.0  AER -> errors the GPU's RX saw         (downstream-pointing traffic on linkB)
  => 4 BDFs = 4 independent, direction-pinned error counts (never summed into "the chain").

  Link speed/width (per LINK, read ONCE at the downstream port that owns it):
    linkA: owned by 00:1c.0 (root port is a DSP)     -> read CLS/NLW + LBMS/LABS here
    linkB: owned by 02:01.0 (switch DSP)             -> read CLS/NLW + LBMS/LABS here
  => 2 links = 2 downgrade verdicts; 02:00.0 and 03:00.0 (the USPs) report the SAME value,
     so you do NOT double-count.
```

So "GPU has errors" actually resolves to, e.g., *"`03:00.0` RX correctables (the GPU receiving
on linkB, i.e. switch-DSP-TX → GPU-RX) plus linkB autonomously downgraded (LABS at `02:01.0`)"*
— which points the SI workup at **linkB's downstream-pointing leg** (switch DSP transmitter,
that cable/trace, GPU receiver), not at linkA or the GPU's transmitter. That is the difference
between a reseat-everything afternoon and a one-leg fix. This is the data model behind the
toolkit's `ChainSegmentResult` (per BDF + direction) and `ChainLinkResult` (per link).

### 14.1 Placing PCIe coverage across the four phases

| Failure mode | Catchable at | Test |
|---|---|---|
| Wrong/missing PCIe device (BOM/stuffing) | **Printed Circuit Board Assembly (PCBA)** (enumeration) + Module | `lspci` enumeration vs expected topology config |
| BGA solder void under a GPU | **PCBA** (X-ray) + **Module** (thermal cycling surfaces it) | X-ray; thermal soak + AER monitor |
| Lane marginal at temperature | **Module** (*not* PCBA — room temp passes it) | Stress + AER + **lane margining hot/cold** |
| Speed/width fallback (EQ/SI) | **Module** | Enumerate at expected Gen/width; `LABS` latch over soak; margining |
| Marginal eye that still trains | **Module** | **Lane margining** vs a Unit Interval (UI) limit (link-up alone passes it) |
| Inter-module / cabled-link marginal | **System** (real cable/connector) | Chain BERT + per-segment margining (retimer localizes board-vs-cable) |
| Custom card not detected (bifurcation) | **PCBA/Module** | Enumeration + bifurcation-vs-schematic check |

The shift-left rule: a marginal Gen4 lane that only fails at 85 °C **cannot** be caught at
room-temperature PCBA — it needs the **module-level thermal stress**, which is where your
BERT/AER/margining tools live. A solder short, by contrast, should die at PCBA (ICT), never
surface as a PCIe link failure at system test.

### 14.2 Takt-bounded BER confidence with margin

On the line every test is **takt-bound** — seconds to minutes per unit, not hours. The BERT's
confidence target is what makes that tractable: instead of "run forever and hope," you
**transfer exactly the bits needed to prove BER ≤ target at the confidence level, then stop**
(§11). Gen4 x16 to 95% at 1e-12 is ≈ 12 s of clean traffic; the engine **fails fast** (rejects
the moment the error rate disproves the target) so a bad unit doesn't burn the full budget, and
it **bounds the extend** by a takt budget so an undecided unit can't run away.

The expert addition is **margin**, two ways:

1. **Run with headroom on the confidence/bits** (`bert.run_bert(margin=1.2)` transfers the
   target-plus-margin so a borderline-but-good unit still clears, and a clearly-good one passes
   quickly).
2. **Gate on the *margin number*, not just pass/fail** — the lane-margining UI/mV value, and
   the BER *upper bound* (not just "no errors"). This is the DV↔MT bridge made concrete: **DV
   uses the per-lane margin to *set* a data-driven eye limit across temperature; MT *checks*
   that limit per unit.** Pass/fail-on-link-up is replaced by a margin with guard-band — which
   is exactly the coverage improvement to propose on day one.

### 14.3 The manufacturing PCIe checklist (shape of the module-test gate)

```bash
#!/bin/bash
set -euo pipefail     # -e: stop on any error; -u: unset var is an error; -o pipefail:
                      # a pipeline fails if ANY stage fails. The single most important
                      # line in a hardware test script.

# 1. Enumerate everything against the expected topology (count, BDF, vendor/class).
expected_gpus=4
actual_gpus=$(lspci -d 10de: | wc -l)
[[ "$actual_gpus" -eq "$expected_gpus" ]] || die "Expected $expected_gpus GPUs, found $actual_gpus"

# 2. Verify link speed AND width for each device (sysfs, no root needed).
for bdf in $(lspci -d 10de: -D | awk '{print $1}'); do
    speed=$(cat /sys/bus/pci/devices/$bdf/current_link_speed)
    width=$(cat /sys/bus/pci/devices/$bdf/current_link_width)
    [[ "$speed" == "16.0 GT/s" ]] || die "GPU $bdf: speed $speed (expected 16.0 GT/s)"
    [[ "$width" == "16" ]]        || die "GPU $bdf: width x$width (expected x16)"
done

# 3. Arm LBMS/LABS latches + AER, soak under load, re-read: any LABS latch or
#    uncorrectable bit = FAIL (catches "trains fine, marginal under load", sec 4.3/sec 13.5).

# 4. Confidence BERT per link to the target (prove BER <= 1e-12 @ 95%, fail fast), sec 11.

# 5. Lane margining per link; gate on >= the per-lane UI limit (sec 10), capture the number.

# 6. Repeat 3-5 hot (thermal chamber) -- the marginal-at-temperature defects PCBA can't see.
```

Every gate above **captures its parameter** (the speed/width, the AER bit-set, the BER bound,
the per-lane margin, the temperature) into the test record — because the captured stream is
what later sets limits, feeds Statistical Process Control (SPC)/Cpk, and flags a bad lot. *Capture the parameter, not just
the verdict* is the rule the whole toolkit is built on.

\newpage

## 15. Command quick-reference

`BDF=0000:03:00.0` throughout.

**Enumerate & topology**
```bash
lspci -nn                      # vendor:device IDs at each BDF
lspci -tv                      # tree: switches, retimers, what's behind what
lspci -nnk -s $BDF             # driver bound (-k) + IDs for one device
lspci -vvv -s $BDF             # FULL: LnkCap/LnkSta, AER status+header log, DPC, margining
```

**Speed/width (sysfs, no root)**
```bash
cat /sys/bus/pci/devices/$BDF/{current,max}_link_speed
cat /sys/bus/pci/devices/$BDF/{current,max}_link_width
# Whole-machine degraded-link scan:
for d in /sys/bus/pci/devices/*; do
  cur=$(cat $d/current_link_speed 2>/dev/null); max=$(cat $d/max_link_speed 2>/dev/null)
  [ -n "$cur" ] && [ "$cur" != "$max" ] && echo "$(basename $d): $cur (max $max)"
done
```

**AER via setpci (ECAP_AER alias)**
```bash
setpci -s $BDF ECAP_AER+0x04.L                 # UNCOR status (read)
setpci -s $BDF ECAP_AER+0x10.L                 # COR status (read)
setpci -s $BDF ECAP_AER+0x0c.L                 # UNCOR severity
setpci -s $BDF ECAP_AER+0x08.L ECAP_AER+0x14.L # UNCOR mask, COR mask
setpci -s $BDF ECAP_AER+0x18.L                 # ERR_CAP (First Error Pointer [4:0])
setpci -s $BDF ECAP_AER+0x1c.L ECAP_AER+0x20.L ECAP_AER+0x24.L ECAP_AER+0x28.L  # header log
setpci -s $BDF ECAP_AER+0x10.L=0xffffffff      # clear COR (W1C / arm)
setpci -s $BDF ECAP_AER+0x04.L=0xffffffff      # clear UNCOR (W1C / arm)
```

**Link registers via setpci (CAP_EXP alias = PCIe cap base)**
```bash
setpci -s $BDF CAP_EXP+0x0c.L                  # LnkCap (max speed/width, DLLLARC bit20)
setpci -s $BDF CAP_EXP+0x12.W                  # LnkSta (CLS/NLW/LT/DLLLA/LBMS/LABS)
setpci -s $BDF CAP_EXP+0x12.W=0xc000           # arm LBMS|LABS latches (W1C)
setpci -s $BDF CAP_EXP+0x32.W                  # LnkSta2 (Flit Mode Status [10])
setpci -s $BDF CAP_EXP+0x24.L CAP_EXP+0x28.W   # DEVCAP2, DEVCTL2 (CTO value [3:0])
setpci -s $BDF CAP_EXP+0x10.W=0x0020           # Retrain Link (LnkCtl bit5)  [perturbs link!]
```

**Kernel-side**
```bash
dmesg | grep -iE 'pcie|aer|link|train|dpc|bifurcation'
cat /sys/bus/pci/devices/$BDF/aer_dev_correctable   # kernel-maintained counters
cat /sys/bus/pci/devices/$BDF/aer_dev_nonfatal /sys/bus/pci/devices/$BDF/aer_dev_fatal
# cmdline knobs: pcie_ports=native (OS owns AER/DPC), pci=noaer (A/B), pcie_aspm=off
```

**Margining & self-test**
```bash
sudo pcilmr --scan                             # links that can be margined (>=16 GT/s)
sudo pcilmr --margin -TV $BDF                  # all lanes, timing+voltage
sudo pcilmr --margin -TV -r 1,2,3,6 $BDF       # near RX, retimer RXs, far RX
sudo pcilmr -o ./csv --full                    # every ready link, CSV out
sudo aer-inject badtlp.aer                     # inject a known error (needs CONFIG_PCIEAER_INJECT)
```

\newpage

## 16. Accuracy notes & caveats

A few things are version- or silicon-dependent — confirm against *your* hardware before
driving it (a wrong offset on a live link turns a diagnosis into an outage):

- **Lane-margining control-register bit offsets.** The *command model* (Receiver Number,
  Margin Type, Margin Payload; query → set-limit → step → dwell → read) is well-confirmed; the
  exact *bit positions* are the conventional layout — confirm against the PCIe Base Spec for
  your silicon. In practice **drive `pcilmr` and parse its CSV** rather than hand-coding the
  sequence; it hardcodes per-vendor quirks.
- **`MaxVoltageOffset` units** — mV vs 10 mV on some parts. Verify before converting steps to
  mV.
- **Gen6 Fixed-size Link Packet layout / Forward Error Correction error model** — the operational point (FEC-corrected symbols + CRC +
  replay replace AER-LCRC-retry accounting; DLLPs are gone) is what changes your measurement;
  the precise FLIT byte-split is spec-final.
- **TX preset dB rounding / P10 definition** — the coefficient *ratios* are authoritative; some
  references round the dB columns differently.
- **`setpci` offsets you write** — always validate against `lspci -vvv` for the specific device
  before a write.

**Primary sources** (consolidated): Linux kernel `include/uapi/linux/pci_regs.h` (AER,
LnkCap/Ctl/Sta, DPC, DEVCTL2 defines) and docs (PCIe AER HOWTO, sysfs-pci); `pciutils`
`pcilmr(8)` man page + ChangeLog; lane-margining tools (OCP `pci_lmt`, `google/pcie_lmt`,
`oxidecomputer/lmar`); `aer-inject` SPEC + kernel `aer_inject.c`; PCI-SIG/vendor material on
equalization, the preset table, and Gen6 PAM4/FLIT/FEC. The Bit Error Rate confidence math is derived in
the Math & Statistics chapter (§10).
