# PCIe Deep Reference — Diagnosis & Root-Cause on Linux

> Engineer-grade reference for a Zoox Compute Test Engineer (manufacturing test &
> diagnostics: server boards, custom PCIe cards, GPUs, NVMe). Focus is on **(a)
> diagnosing PCIe errors/failures and (b) finding root cause on Linux**. Register
> values are cited against the Linux kernel `pci_regs.h`, the kernel PCI/AER docs,
> `pciutils` (`lspci`/`setpci`/`pcilmr`), and vendor/PCI-SIG material. Where a value
> is spec-version-dependent or I could not pin it to a primary source, it is flagged
> **[VERIFY]**.

**Conventions:** "BDF" = `domain:bus:device.function`, e.g. `0000:03:00.0`. "DSP" =
Downstream Port (root port or switch downstream). "USP" = Upstream Port (endpoint or
switch upstream). "EP" = endpoint. All config offsets are bytes; bit numbering is
LSB=0. "GT/s" is the raw symbol rate per lane; "Gen" is the speed code.

---

## 1. Architecture & layers

PCIe is a **layered, packet-switched, point-to-point** interconnect (not a bus — each
link is exactly two devices). Three layers, top to bottom:

| Layer | Unit | Job | What it adds to a packet |
|---|---|---|---|
| **Transaction (TL)** | **TLP** (Transaction Layer Packet) | Carries reads/writes/completions/messages between requester and completer | Header (fmt/type, requester ID, tag, address), optional data payload, optional **ECRC** (32-bit end-to-end CRC) |
| **Data Link (DL)** | **TLP + DLLP** | Reliable delivery across the one link: sequence numbers, **LCRC**, ACK/NAK replay, **credit-based flow control** | Prepends 12-bit **sequence number**, appends 32-bit **LCRC** to each TLP; emits **DLLPs** (ACK/NAK, FC, PM) |
| **Physical (PHY)** | Symbols / Ordered Sets | Serialize, encode, equalize, train the link (LTSSM lives here) | Framing, scrambling, line code (8b/10b or 128b/130b or PAM4+FEC), lane striping |

**TLP vs DLLP — the key distinction for debug:**

- A **TLP** is end-to-end (routed across switches by address/ID). It is protected by
  **LCRC** (link-local, checked every hop) and optionally **ECRC** (end-to-end, only
  checked at the ultimate completer). A TLP that fails LCRC is NAK'd and **replayed**
  by the DL layer — this is recoverable and shows up as the *correctable* **Bad TLP**.
- A **DLLP** is link-local only (ACK/NAK, flow-control updates, power-management). It
  is *not* retried — a corrupt DLLP just gets dropped, and the sender's replay timer
  eventually fires. A corrupt DLLP shows up as the *correctable* **Bad DLLP**.

**The replay (ACK/NAK) protocol** is the heart of DL-layer reliability and the source
of the most diagnostic correctable bits:
- Every transmitted TLP is buffered in the **replay buffer** and tagged with a
  sequence number.
- The receiver checks LCRC + sequence; on success it eventually returns an **ACK**
  DLLP (cumulative), on failure a **NAK**.
- On NAK — or when the **REPLAY_TIMER** expires with no ACK — the transmitter replays
  the buffered TLPs. Each full traversal of the replay-count increments
  **REPLAY_NUM**; wrapping it 4× is the **REPLAY_NUM Rollover** correctable error.
- **Therefore:** Replay Timer Timeout + Bad TLP + REPLAY_NUM Rollover clustering =
  the link is repeatedly corrupting TLPs and retrying = **physical-layer / signal
  integrity**. This cluster is the single most important "it's the PHY" signature.

### Credit-based flow control (why a receiver never overflows)

PCIe never drops a TLP for lack of buffer space — the transmitter is **forbidden from
sending** unless it holds enough credits. Mechanics (Shane Colton Pt.5; Xillybus):

- Credits are tracked **separately for three TLP classes** × **header (H) and data
  (D)**: **Posted (P)** (memory writes, messages), **Non-Posted (NP)** (reads, I/O,
  config, atomics), **Completion (Cpl)**. That's 6 counters per Virtual Channel.
- Header credit = 1 per TLP header; **Data credit = 1 per 4 DWORD (16-byte) unit** of
  payload. Field widths: typically **8-bit** header-credit counters, **12-bit**
  data-credit counters (scaled-credit extensions exist for high-bandwidth links).
- **Initialization (in Configuration/L0 bring-up):** transmitter floods **InitFC1**
  DLLPs (P, then NP, then Cpl) advertising its receive-buffer space; switches to
  **InitFC2** once it has seen the partner's InitFC1s. Re-sent every ~34 µs until
  done. **[VERIFY exact period per gen].**
- **Steady state:** receiver emits **UpdateFC** DLLPs as it drains its buffers,
  returning credits. Max period ~30–45 µs, or immediately if the partner is
  credit-starved.
- **Failure modes that surface in AER:**
  - **Flow Control Protocol Error** (uncorrectable, bit 13): a credit advertisement
    violated the rules (e.g. went backwards, exceeded the max) — almost always a
    **firmware/IP bug** in one endpoint, not a cable.
  - **Receiver Overflow** (uncorrectable, bit 17): a transmitter sent more than the
    advertised credits → the receiver buffer overran. Also a **protocol/IP bug** (or
    corrupted FC DLLPs causing credit miscount), *not* signal integrity per se.

Sources: [Shane Colton — PCIe Deep Dive Pt.5: Flow Control](https://scolton.blogspot.com/2024/11/pcie-deep-dive-part-5-flow-control.html),
[Xillybus TLP primer](https://xillybus.com/tutorials/pci-express-tlp-pcie-primer-tutorial-guide-2).

---

## 2. Generations Gen1–Gen6: rates, coding, and what changes for test

| Gen | Speed code | Rate/lane | Line code | x16 raw → ~payload | Test implications |
|---|---|---|---|---|---|
| **1** | 1 | 2.5 GT/s | 8b/10b (20% OH) | ~32 → 25.6 Gb/s | NRZ, generous margins; no link EQ; eye usually open. |
| **2** | 2 | 5.0 GT/s | 8b/10b | ~64 → 51.2 Gb/s | NRZ; optional de-emphasis (-3.5/-6 dB). Still scope-friendly. |
| **3** | 3 | 8.0 GT/s | **128b/130b** (~1.5% OH) | ~126 → 124 Gb/s | **Equalization becomes mandatory** (4-phase, 11 TX presets). Channel loss closes the raw eye; EQ reopens it. First gen where SI dominates. |
| **4** | 4 | 16.0 GT/s | 128b/130b | ~252 → 248 Gb/s | Tighter EQ; **Lane Margining at Receiver becomes mandatory** (timing required, voltage optional). Retimers common. |
| **5** | 5 | 32.0 GT/s | 128b/130b | ~504 → 496 Gb/s | Very lossy channels; retimers almost always; **voltage margining now mandatory**. Tx/Rx EQ presets critical. |
| **6** | 6 | 64.0 GT/s | **PAM4 + FLIT + FEC** | ~1024 → ~970 Gb/s | New error model (see below). BER target changes meaning. |

**8b/10b vs 128b/130b vs PAM4/FLIT/FEC — what actually changes:**

- **8b/10b (Gen1/2):** every 8 data bits → 10 line bits (DC balance + embedded clock).
  20% overhead. "Receiver Error" = an 8b/10b decode/disparity violation.
- **128b/130b (Gen3–5):** 128-bit blocks + a 2-bit **sync header** (01b=data,
  10b=ordered set). ~1.54% overhead. Scrambling provides transitions. "Receiver
  Error" now = sync-header or block-level error.
- **PAM4 + FLIT + FEC (Gen6):** signaling moves to **4 voltage levels (PAM4)** → 2
  bits/symbol at the same 16 GHz Nyquist as Gen5 (that's how 64 GT/s is reached
  without doubling the channel bandwidth). PAM4's 3 stacked eyes → ~3× worse SNR, so
  the raw **first-burst BER (FBER) is ~1e-6**, far too high to be useful raw.
  - The fix: **FLIT mode** (Flow Control Unit). Every transfer is a fixed **256-byte
    FLIT = 242 B payload + 8 B CRC + 6 B FEC** (the 250 B of payload+CRC is FEC-
    protected). **[VERIFY exact split per PCIe 6.0 final].**
  - **Lightweight FEC** corrects small burst errors inline (added latency budgeted
    <2 ns); a strong 64-bit CRC + replay catches the rest, giving an effective post-
    FEC BER many orders better than 1e-6, with retry probability/FLIT ~5e-6.
  - **DLLPs are eliminated** in FLIT mode — ACK/NAK and flow-control are carried in
    dedicated fields *inside* the FLIT, not as separate DLLPs.
  - **Testing changes:** "BER" splits into **correctable (FEC-corrected) symbols** vs
    **uncorrectable FLITs** + **CRC-triggered replays**. A Gen6 BERT must read FEC
    correctable/uncorrectable counters, not just AER LCRC retries. AER even gains a
    "TLP logged in FLIT mode" bit (`PCI_ERR_CAP_TLP_LOG_FLIT`, see §6).

Sources: [VIAVI PCIe 6.0 guide](https://www.viavisolutions.com/en-us/resources/learning-center/what-pcie-60),
[Synopsys FEC/CRC verification](https://www.synopsys.com/blogs/chip-design/pcie-6-verifaction-fec-crc.html),
kernel `pci_regs.h`.

---

## 3. LTSSM — Link Training and Status State Machine

The LTSSM is the PHY-layer state machine that brings a link from cold to L0 and
handles speed changes, power states, and recovery. Engineer-level breakdown with
**what "stuck" means** (after [Shane Colton Pt.4: LTSSM](https://scolton.blogspot.com/2024/01/pcie-deep-dive-part-4-ltssm.html)):

| State (sub-states) | What happens | Stuck here ⇒ |
|---|---|---|
| **Detect** (Quiet→Active) | TX in electrical idle; sense the partner's RX **termination** (an RC measurement on each lane). Loops ~12 ms if nothing found. | **No link partner / dead device / no power on far end / AC-coupling cap or termination missing / PERST# not released.** The classic "device not detected." |
| **Polling** (Active→Config→…) | Exchange **TS1** ordered sets to get **bit & symbol lock**; then TS2. (1024+ TS1 sent, 8 consecutive TS1/TS2 with PAD to advance.) Always at **2.5 GT/s** here. | **Bit-lock failure: REFCLK absent/wrong (SSC mismatch), receiver DC offset, gross SI (impedance/crosstalk), polarity.** |
| **Configuration** (Linkwidth.Start/Accept, Lanenum.Wait/Accept, Complete, Idle) | DSP leads: assign **Link Number** + **Lane Numbers**, negotiate **width**, resolve **lane reversal/polarity inversion**. Ends with IDL symbols at Gen1. | **Width can't be agreed (dead/noisy lanes), lane-reversal unsupported, bifurcation mismatch, scrambler problem (stuck in Config.Idle).** A link that *configures x8 instead of x16* fell out here on the high lanes. |
| **L0** | **Normal operation.** TLPs/DLLPs flow. If both ends support higher speed, enter Recovery to change speed. | (Healthy.) Frequent involuntary exits to Recovery = marginal link. |
| **Recovery** (RcvrLock, RcvrCfg, Speed, **Equalization** [Gen3+], Idle) | Re-acquire lock; **change speed**; run **equalization** (4-phase, Gen3+). Entered for speed change, after errors, or to retrain. | **Repeated Recovery (link "bouncing") = marginal SI / EQ preset mismatch / RX won't lock at the new speed / thermal drift.** This is the #1 dynamic failure signature. |
| **L0s** | Fast, low-latency power save on an idle direction (one direction at a time). ASPM. | Stuck/looping L0s entry+exit can hurt latency; rarely a hard fail. |
| **L1** | Deeper power save (whole link quiesced; REFCLK may stop with L1.2 substates). | L1 exit-latency or L1SS misconfig can cause timeouts; ASPM bugs cause Completion Timeouts. |
| **L2** | Deep sleep, main power may be off; only Vaux. Wake via beacon/WAKE#. | Wake failures = "device disappears after sleep." |
| **Disabled** | Link intentionally disabled (`LnkCtl.LD`=1). | Software/firmware left it disabled, or partner forced it. |
| **Hot Reset** | In-band reset propagated via TS1 with the Hot Reset bit. Forces partner back to Detect. | Used by `pci` reset paths; a link that keeps hot-resetting = upstream forcing resets (often DPC, see §9). |
| **Loopback** | Test/debug: RX echoes the bit stream back to the far TX. **The basis of compliance/BERT loopback and `setpci`-driven test modes.** | Stuck in Loopback = a compliance/test mode wasn't exited; not seen in production. |

**Equalization sub-phases (Recovery.Equalization, Gen3+):** advertised in TS1 **EC**
bits (00=none,01=Ph1,10=Ph2,11=Ph3). Phase 1: both ends advertise EQ capabilities.
Phase 2: **DSP adjusts USP's TX EQ while USP tunes its own RX**. Phase 3: reverse —
**USP adjusts DSP's TX EQ while DSP tunes its RX**. Every link must traverse all four
phases (0–3) even if no coefficients change; Ph2/Ph3 are where coefficients are
optimized and where a too-lossy channel **fails back to Recovery.Speed → renegotiates
a lower gen**.

**Observing LTSSM state on Linux:** the *full* LTSSM state is **not** in architected
config space — it lives in **vendor-specific** registers (Broadcom/PLX, Intel, NVIDIA
PHY/debug) or is inferred. Portably you can observe:
- **`LnkSta.LT` (Link Training, bit 11)** flicking to 1 = the link entered Recovery
  and is retraining. Poll it during stress; frequent toggles = marginal SI.
- **`LnkSta.DLLLA` (Data Link Layer Link Active, bit 13)** dropping = link went down.
- **`dmesg`** link up/down, speed-change, and "Link is degraded" messages.
- **`LnkSta.LBMS` (Link Bandwidth Management Status, bit 14)** sets when the link
  changed speed/width autonomously — a latched "the link renegotiated" flag.
- Vendor counters (some Broadcom switches, NVIDIA via `nvidia-smi -q | grep -i
  "replay\|recovery"`) expose Recovery-entry counts directly.

---

## 4. Link training & equalization (Gen3+)

At 8 GT/s and above the channel (traces, vias, connectors, cables) attenuates the
high-frequency content so badly the **raw eye is closed**. Equalization reopens it.

**The 4-phase EQ handshake (in Recovery.Equalization):**
1. **Phase 0:** preset values exchanged in the *initial* TS1/TS2; link comes up at the
   target speed with the negotiated/default presets.
2. **Phase 1:** both ports confirm they're ready and advertise EQ capability
   (Full-Scale, Low-Frequency, Post-cursor bounds).
3. **Phase 2:** **downstream port adjusts the upstream port's transmitter** (requests
   coefficient/preset changes) while the **downstream tunes its own receiver**.
4. **Phase 3:** roles reverse — **upstream adjusts the downstream's transmitter**,
   upstream tunes its own RX. ([Intel Gen3 EQ](https://www.intel.com/content/www/us/en/docs/programmable/683621/current/link-equalization-for-gen3.html),
   [Teledyne LeCroy link-training](https://blog.teledynelecroy.com/2014/11/an-under-hood-view-of-pcie-30-link.html))

**TX equalization** shapes each bit with three FIR taps under a fixed-swing constraint:
- **Pre-cursor C₋₁** (a.k.a. preshoot), **Cursor C₀** (main), **Post-cursor C₊₁**
  (de-emphasis). The boost on the cursors strengthens transitions (fights ISI) at the
  cost of the steady-state level. Too much → overshoot/ringing; too little → closed
  eye.

**The 11 TX presets (P0–P10)** bundle coefficient ratios for different channel losses.
Per the PCIe 3.0 spec preset table ([bitsilica](https://bitsilica.com/demystifying-pcie-equalization-from-gen-3-to-gen-6/),
[studylib reproduction of the spec table](https://studylib.net/doc/18589313/pci-express-3.0-equalization--the-mystery-unsolved)):

| Preset | De-emphasis (dB) | Preshoot (dB) | C₋₁ | C₀ | C₊₁ |
|---|---|---|---|---|---|
| P0 | -6.0 | 0 | 0.000 | 0.750 | -0.250 |
| P1 | -3.5 | 0 | 0.000 | 0.833 | -0.167 |
| P2 | -4.4 | 0 | 0.000 | 0.800 | -0.200 |
| P3 | -2.5 | 0 | 0.000 | 0.875 | -0.125 |
| P4 (no EQ) | 0 | 0 | 0.000 | 1.000 | 0.000 |
| P5 | 0 | 1.9 | -0.100 | 0.900 | 0.000 |
| P6 | 0 | 2.5 | -0.125 | 0.875 | 0.000 |
| P7 | -6.0 | 3.5 | -0.100 | 0.700 | -0.200 |
| P8 | -3.5 | 3.5 | -0.125 | 0.750 | -0.125 |
| P9 | 0 | 3.5 | -0.167 | 0.833 | 0.000 |
| P10 | (flat level = min diff. voltage) | — | — | — | — |

> **[VERIFY]** Some references list slightly different rounding for the dB columns and
> P10's exact definition; the **coefficient ratios** are the authoritative form and
> are what you'd program. P11–P15 are reserved.

**RX equalization:** **CTLE** (Continuous-Time Linear Equalizer — frequency-dependent
gain that boosts the attenuated high band) + **DFE** (Decision-Feedback Equalizer —
cancels post-cursor ISI using *past bit decisions*, so it adds no noise). The receiver
**adapts** these during Ph2/Ph3; you don't set them directly. They're why a marginal
TX preset can *still* train — the RX compensates until it can't, which is exactly why
**a link that "trains fine" can still be on the edge** (and why lane margining, §7,
matters more than pass/fail link-up).

**Why links fall back in speed or width — the canonical causes:**
- **Speed fallback (e.g. Gen4→Gen3):** EQ failed to reach a usable eye at the higher
  rate → Recovery.Equalization fails → renegotiate down. Causes: trace length/loss,
  via stubs, impedance discontinuities, connector seating, **temperature** (equalizes
  at 25 °C, fails at 85 °C), power-supply noise, a too-aggressive/too-weak preset,
  or a BIOS `Target Link Speed` cap (`LnkCtl2.TLS`).
- **Width fallback (e.g. x16→x8):** one or more lanes failed receiver detection
  (Detect) or couldn't lock (Polling/Config) → the link configures the largest
  contiguous working subset. Causes: bent/dirty connector pin on the high lanes, a
  dead SerDes, lane-reversal/polarity issue, **bifurcation mismatch** (BIOS split
  doesn't match the board), or an open AC-coupling cap on specific lanes.

---

## 5. Config space & capabilities

**Layout** (every function; 256 B legacy, **4096 B** with extended space):
```
0x000  Type 0/1 header (Vendor/Device ID, Command, Status, Class, BARs, …)
0x034  Capabilities Pointer (1 byte → first legacy cap)
0x040  Legacy Capability list  : each = [cap_id:8][next_ptr:8][cap body]
0x100  Extended Capability list: each = 32-bit header
        [cap_id:16][cap_version:4][next_offset:12], then body
0xFFF  end of extended config space
```

**Walking legacy caps** (find the PCIe cap, ID `0x10`):
```c
ptr = read8(0x34) & 0xFC;
while (ptr) { id = read8(ptr); if (id==target) return ptr; ptr = read8(ptr+1) & 0xFC; }
```
**Walking extended caps** (find AER, ID `0x0001`):
```c
off = 0x100;
while (off) { h=read32(off); if (h==0||h==~0u) break;
              if ((h & 0xFFFF)==target) return off; off=(h>>20)&0xFFF; }
```
(Both are implemented in `backend.py` `find_cap`/`find_ext_cap` and in
`pcie_bert.c`.)

### The PCIe Capability (legacy cap ID 0x10) — link registers

Offsets are **relative to the PCIe-cap base** (the byte where ID `0x10` lives). Values
from kernel `pci_regs.h`:

| Reg | Off | Key fields (mask) |
|---|---|---|
| **Link Capabilities** (`LNKCAP`) | `+0x0C` | Max Link Speed `[3:0]` (`SLS`: 1=2.5…6=64 GT/s); Max Link Width `[9:4]` (`MLW`); ASPM support `[11:10]`; **Surprise Down Err Reporting Capable** `[19]` (`SDERC`); **DLL Link Active Reporting Capable** `[20]` (`DLLLARC`); Port Number `[31:24]` |
| **Link Control** (`LNKCTL`) | `+0x10` | ASPM Ctrl `[1:0]`; **Link Disable** `[4]` (`LD`); **Retrain Link** `[5]` (`RL`, write-1 to force retrain); Common-Clock `[6]`; **HW Autonomous Width Disable** `[9]` (`HAWD`) |
| **Link Status** (`LNKSTA`) | `+0x12` | **Current Speed `[3:0]`** (`CLS`); **Current Width `[9:4]`** (`NLW`, shift 4); **Link Training `[11]`** (`LT`); **DLL Link Active `[13]`** (`DLLLA`); **Link BW Mgmt Status `[14]`** (`LBMS`); **Link Autonomous BW Status `[15]`** (`LABS`) |
| **Link Control 2** (`LNKCTL2`) | `+0x30` | **Target Link Speed `[3:0]`** (`TLS` — write to cap/force a gen, then Retrain); Enter Compliance `[4]`; **Transmit Margin `[9:7]`** (`TX_MARGIN`) |
| **Link Status 2** (`LNKSTA2`) | `+0x32` | **Flit Mode Status `[10]`** (`FLIT` — set when the link is in Gen6 FLIT mode); EQ-phase-complete / equalization status bits |

> Note: `LNKSTA` is 16-bit at `+0x12`; `LNKSTA2` is the 16-bit reg at `+0x32`. The
> `LNKCAP2` register sits at `+0x2C` (Supported Link Speeds Vector). Speed codes 1–6
> map to 2.5/5/8/16/32/64 GT/s consistently across `SLS`/`CLS`/`TLS`.

### Extended capabilities you care about

| Cap | Ext ID | What it holds |
|---|---|---|
| **AER** | `0x0001` | Correctable/uncorrectable error status, mask, severity, header log (§6) |
| **Secondary PCIe** | `0x0019` | Gen3+ **Lane Equalization Control** (per-lane: DSP TX preset, DSP RX preset hint, USP TX preset, USP RX preset hint) — used to read/override negotiated presets |
| **DPC** | `0x001D` | Downstream Port Containment (§9) |
| **L1 PM Substates** | `0x001E` | L1.1/L1.2 control (ASPM-related Completion-Timeout debugging) |
| **Lane Margining at Receiver** | `0x0027` | Per-lane RX eye margining (§7) |

> **[VERIFY]** The kernel `pci_regs.h` snapshot I pulled did **not** contain a
> `PCI_EXT_CAP_ID_LMR` symbol; the Lane Margining capability ID is **`0x0027`** per
> PCI-SIG / pciutils `pcilmr`. `pciutils` decodes it as "Physical Layer 16.0 GT/s …
> Margining". Treat `0x0027` as authoritative for the cap; the per-register layout is
> in §7.

---

## 6. AER in depth

AER (Advanced Error Reporting) is the extended cap (ID `0x0001`) where the hardware
**latches** correctable and uncorrectable errors. Offsets relative to the AER base
(kernel `pci_regs.h`):

| Off | Register | Notes |
|---|---|---|
| `+0x00` | Capability Header | ID `0x0001` |
| `+0x04` | **Uncorrectable Error Status** (`UNCOR_STATUS`) | **W1C**. Non-zero after a clean run = FAIL |
| `+0x08` | Uncorrectable Error **Mask** | 1 = not reported (still latched in status on some impls) |
| `+0x0C` | Uncorrectable Error **Severity** | 1 = Fatal (→ ERR_FATAL, link reset), 0 = Non-Fatal (→ ERR_NONFATAL) |
| `+0x10` | **Correctable Error Status** (`COR_STATUS`) | **W1C**. Recovered errors; rising count = SI concern |
| `+0x14` | Correctable Error **Mask** | 1 = masked |
| `+0x18` | **Advanced Error Cap & Control** (`ERR_CAP`) | **First Error Pointer `[4:0]`** (`FEP`); ECRC gen/check capable+enable; FLIT-mode-log bits |
| `+0x1C` | **Header Log** (16 B) | First 4 DWORDs of the TLP that caused the **first** uncorrectable error |
| `+0x2C` | **Root Error Command** | Root ports/RCEC only: enable COR/NONFATAL/FATAL reporting interrupts |
| `+0x30` | **Root Error Status** | Root ports only: which class(es) received, multi-error, first-fatal, AER IRQ msg # |
| `+0x34` | **Error Source ID** | Root ports: requester ID of the COR and the UNCOR source |
| `+0x38` | **TLP Prefix Log** | up to 16 B (when prefix logging present) |

### Every correctable bit (`COR_STATUS`, +0x10)

| Bit | Mask | Name | Meaning / typical root cause |
|---|---|---|---|
| 0 | `0x0001` | **Receiver Error** (`RCVR`) | Raw PHY symbol/8b10b/128b130b/sync-header error. **Pure SI** (loss, jitter, crosstalk, marginal EQ). |
| 6 | `0x0040` | **Bad TLP** (`BAD_TLP`) | A TLP arrived with bad **LCRC** or bad sequence number → NAK'd & replayed. **SI** (corruption on the wire). |
| 7 | `0x0080` | **Bad DLLP** (`BAD_DLLP`) | An ACK/NAK/FC/PM DLLP failed its CRC. **SI** (DLLPs aren't retried, just dropped). |
| 8 | `0x0100` | **REPLAY_NUM Rollover** (`REP_ROLL`) | Replay counter wrapped (4 consecutive replays of the same TLP) → many retries. **SI / marginal link.** |
| 12 | `0x1000` | **Replay Timer Timeout** (`REP_TIMER`) | No ACK before REPLAY_TIMER expired → replay. **Classic marginal-link symptom** (lost ACKs / lost TLPs). |
| 13 | `0x2000` | **Advisory Non-Fatal** (`ADV_NFAT`) | An uncorrectable error was *demoted* to advisory (e.g. an UnsupReq/CmplTO that spec says report as correctable). Look at the *uncorrectable* status to see what was demoted. |
| 14 | `0x4000` | **Corrected Internal Error** (`INTERNAL`) | Device-internal error the device corrected (e.g. ECC in its own RAM). Device-side, not link. |
| 15 | `0x8000` | **Header Log Overflow** (`LOG_OVER`) | More uncorrectable errors arrived than the 1-deep header log could hold. Means *many* uncorrectables — go fix those. |

### Every uncorrectable bit (`UNCOR_STATUS`, +0x04)

| Bit | Mask | Name | Meaning / typical root cause |
|---|---|---|---|
| 0 | `0x0000_0001` | **Undefined** (`UND`) | Reserved/undefined; should be 0. |
| 4 | `0x0000_0010` | **Data Link Protocol Error** (`DLP`) | ACK/sequence-number protocol violation (e.g. ACK for an unsent seq#). **Protocol/IP bug**, sometimes severe SI corrupting seq#s. |
| 5 | `0x0000_0020` | **Surprise Down** (`SURPDN`) | Link dropped to DL_Down unexpectedly. **Device/power lost, cable yanked, far-end PHY died, hot-unplug.** |
| 12 | `0x0000_1000` | **Poisoned TLP Received** (`POISON_TLP`) | A TLP arrived with the **EP (poison) bit** set (sender marked its own data bad, e.g. uncorrectable ECC in DRAM behind the requester). **Upstream data corruption**, not this link's SI. |
| 13 | `0x0000_2000` | **Flow Control Protocol Error** (`FCP`) | Credit accounting violated the rules. **Firmware/IP bug** (or corrupted FC DLLPs). |
| 14 | `0x0000_4000` | **Completion Timeout** (`COMP_TIME`) | A non-posted request (usually a read) never got its completion before the CTO timer fired. **Upstream device hung / wrong address / ASPM-L1 exit too slow / switch dropped it / firmware.** |
| 15 | `0x0000_8000` | **Completer Abort** (`COMP_ABORT`) | The completer refused the request (returned CA). **Target-side**: bad address, permission, or completer-internal error. |
| 16 | `0x0001_0000` | **Unexpected Completion** (`UNX_COMP`) | A completion arrived with no matching outstanding request (wrong tag/requester ID). **Switch mis-routing / duplicate tags / firmware.** |
| 17 | `0x0002_0000` | **Receiver Overflow** (`RX_OVER`) | Got more TLP data than the advertised credits. **Flow-control/IP bug** (transmitter overran). |
| 18 | `0x0004_0000` | **Malformed TLP** (`MALF_TLP`) | Structurally invalid TLP (bad length, byte-enables, addr/type). **Firmware/IP bug** or severe corruption. Usually **Fatal** by default severity. |
| 19 | `0x0008_0000` | **ECRC Error** (`ECRC`) | End-to-end CRC mismatch (only if ECRC gen+check enabled). **End-to-end data corruption** through a switch/retimer that LCRC didn't catch. |
| 20 | `0x0010_0000` | **Unsupported Request** (`UNSUP`) | Target doesn't support the request (e.g. access to an unimplemented BAR/region). **Address-map / enumeration / driver bug.** |
| 21 | `0x0020_0000` | **ACS Violation** (`ACSV`) | Access Control Services blocked a peer-to-peer TLP. **IOMMU/ACS policy** (often expected in virtualization). |
| 22 | `0x0040_0000` | **Uncorrectable Internal Error** (`INTN`) | Device-internal uncorrectable (its own logic/RAM). Device-side. |
| 23 | `0x0080_0000` | **MC Blocked TLP** (`MCBTLP`) | Multicast-blocked TLP. |
| 24 | `0x0100_0000` | **AtomicOp Egress Blocked** (`ATOMEG`) | Atomic op blocked at egress. |
| 25 | `0x0200_0000` | **TLP Prefix Blocked** (`TLPPRE`) | TLP prefix egress-blocked. |
| 26 | `0x0400_0000` | **Poisoned TLP Egress Blocked** (`POISON_BLK`) | Poisoned TLP blocked at egress (newer spec). |
| 27 | `0x0800_0000` | **DMWr Egress Blocked** (`DMWR_BLK`) | Deferred Memory Write request blocked. |
| 28–31 | — | IDE/PCRC checks (`IDE_CHECK`,`MISR_IDE`,`PCRC_CHECK`,`XLAT_BLK`) | Integrity & Data Encryption (IDE) and TLP-translation errors (newest spec). |

> The existing tool's table stops at bit 22, which covers everything you'll see on a
> normal compute board. Bits 23–31 are mostly multicast/atomics/IDE — worth adding to
> the decode table for completeness so an unexpected high bit isn't shown as "unknown".

**Severity & mask:** `UNCOR_SEVERITY` (+0x0C) selects Fatal(1)/Non-Fatal(0) **per
bit**; Fatal errors trigger ERR_FATAL → link reset / DPC. `*_MASK` selects whether the
error is *reported* (an interrupt/message is generated) — masked errors may still
latch in status. **For deterministic manufacturing measurement, read severity+mask
first so you know which bits will trigger DPC / link reset under you.**

**Header Log decode:** the 16 B at +0x1C are the **first 4 DWORDs of the offending
TLP** (DW0 = Fmt/Type/Length, DW1 = Requester ID/Tag/byte-enables, DW2/DW3 =
address or completion fields). Decoding it tells you *who* sent the bad TLP, *what*
type, and *what address* — turning "Completion Timeout" into "read of BAR2 offset
0x1040 by requester 03:00.0 timed out." `lspci -vvv` prints it raw; `tlp-tool`
(or your own decode) turns it into fields. **The First Error Pointer (`ERR_CAP[4:0]`)
tells you which uncorrectable bit the header log belongs to** — essential when
multiple bits are set.

**Root Error Status (+0x30, root ports only):** aggregates what the root port
received — `COR_RCV`, `UNCOR_RCV`, `MULTI_*`, `FIRST_FATAL`, plus the **AER interrupt
message number**. **Error Source ID (+0x34)** gives the requester IDs. On a system
with switches, the **root port** is where the kernel AER driver attaches, so this is
where the OS-level error story is assembled.

Sources: kernel [`pci_regs.h`](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/pci_regs.h),
[kernel AER HOWTO](https://docs.kernel.org/PCI/pcieaer-howto.html).

---

## 7. Lane Margining at the Receiver (Gen4+) — and how to drive it on Linux *today*

**What it is:** a **mandatory** feature for every port at **≥16 GT/s** (Gen4+). It
lets software command a receiver, **while the link stays in L0**, to **shift its
sampling point** in **time** (left/right) and (if supported) **voltage** (up/down),
**per lane**, and report when errors appear. Stepping the offset outward until errors
exceed a limit measures the **eye margin on-die, with no oscilloscope**. *Timing*
margining is required; *voltage* is optional at 16 GT/s and **mandatory at 32 GT/s+**.

### The extended-capability registers (ID 0x0027)

Per-port there is a **Port Capabilities/Status**, then **per-lane** a pair:
- **Margining Lane Control Register** (write commands here)
- **Margining Lane Status Register** (read responses here)

The control register fields (PCIe Base Spec §"Lane Margining at the Receiver"; field
names confirmed by Cadence/Synopsys/Intel docs):

| Field | Role |
|---|---|
| **Receiver Number** `[2:0]` | Which receiver in the path: **0 = reserved**, **1 = the port's own RX (Rx of the device closest to the upstream)**, **2–5 = retimer RXs**, **6 = the far port's RX**. (Up to 6 receivers along a link with 2 retimers.) |
| **Margin Type** `[5:3]` | Selects the command class (report capabilities, set error-count limit, step timing, step voltage, go-to-normal, clear-error-log, no-command). |
| **Usage Model** `[6]` | 0 = normal lane margining. |
| **Margin Payload** `[13:8]` | The command argument (e.g. number of steps; the error-count limit; which capability to report). |

> **[VERIFY]** The exact bit positions of Receiver Number / Margin Type / Usage Model
> / Margin Payload above are the conventional layout; confirm against the PCIe Base
> Spec revision for your silicon (the *fields* and *command model* are well-confirmed
> by Cadence and the OCP/pcilmr implementations; the *bit offsets* I'm citing from
> the conventional layout, not a primary doc).

### The margining command protocol (what `pcilmr`/`lmar`/LMT actually do)

1. **Query capabilities** (Margin Type = report): read **MaxTimingOffset**,
   **MaxVoltageOffset**, **NumTimingSteps**, **NumVoltageSteps**, **timing/voltage
   sampling rate**, **MaxLanes** (how many lanes can margin at once),
   **IndependentErrorSampler** (does it have a separate sampler so margining doesn't
   corrupt live data?), **SampleReportingMethod** (count vs rate).
2. **Set the error-count limit** (Margin Type = set error count limit; payload = limit,
   default 4 in `pcilmr`).
3. **Step margin to a timing/voltage offset** (Margin Type = step timing/voltage;
   payload = N steps left/right or up/down). The receiver moves its sampler.
4. **Dwell** for the dwell time (default 1 s in `pcilmr`) so enough bits are sampled,
   then **read the Margining Lane Status** for the error count / "too many errors".
5. **Step outward** until errors exceed the limit; the **largest passing step** is the
   margin at that point. Repeat for left/right (timing) and up/down (voltage).
6. **Go to normal settings** + **clear error log** to restore the link.

### Converting steps → UI and mV

The hardware reports the *full-scale* offset and the *number of steps*:
```
timing_margin_UI  =  (passing_timing_steps  / NumTimingSteps)  * (MaxTimingOffset  / 100)
voltage_margin_mV =  (passing_voltage_steps / NumVoltageSteps) * MaxVoltageOffset
```
- **MaxTimingOffset** is expressed as a **percent of a UI** (e.g. 50 ⇒ ±0.5 UI full
  scale). So `(MaxTimingOffset/100)` converts to UI, and the step fraction scales it.
- **MaxVoltageOffset** is in **mV** (or in units of 10 mV on some parts — **[VERIFY]**
  per silicon).
- The **sample count** for a pass/fail point follows `10^(samples/10)` bits in the
  OCP LMT convention (i.e. the dwell+rate define how many bits are checked).

Spec reference eye targets `pcilmr` uses for grading: at 16 GT/s, min **timing 30% UI
(18.75 ps)** / rec. 38% UI; min **voltage 15 mV** / rec. 21 mV. At 32 GT/s, min
timing 30% UI (9.375 ps), min voltage 15 mV.

### How to actually drive it on Linux today

**Kernel status:** there is **no generic kernel sysfs "margin this lane" interface**
as of the 6.x series. The kernel exposes config space; **margining is driven from
user space** by sequencing the cap registers. Two practical paths:

1. **`pcilmr` (part of `pciutils` ≥ 3.13, May 2024; improved in 3.14, Jun 2025)** —
   **this is the answer for "drive it today."** It's a standard tool, no vendor SDK.
   ```bash
   # Find links that can be margined (negotiated ≥16 GT/s):
   sudo pcilmr --scan

   # Margin one downstream port, all lanes, both timing+voltage:
   sudo pcilmr --margin -TV 0000:03:00.0

   # Margin specific lanes/receivers, save CSV, custom grade:
   sudo pcilmr -o ./csv 0000:ab:00.0 -r 1,6 -g 1t=20% -g 1v=f,30 \
               0000:52:00.0 -l 0,1,2 -TV

   # Margin every ready link in the system, one by one:
   sudo pcilmr --full -o ./csv
   ```
   Key flags: `-e <errlimit>` (default 4), `-d <dwell-sec>` (default 1),
   `-l <lanes>`, `-r <recv#>` (1=local RX … 6=far RX, includes retimers),
   `-t/-T` (timing), `-v/-V` (voltage), `-p` (parallel lanes, experimental),
   `-c` (capabilities only), `-g` (grading thresholds in % or ps). **Requires root**
   (extended config), the link must be **D0**, and **ASPM + HW-autonomous features
   must be disabled** during the test (pcilmr does the latter and warns).
   ([pcilmr man page](https://man7.org/linux/man-pages/man8/pcilmr.8.en.html),
   [pciutils ChangeLog](https://github.com/pciutils/pciutils/blob/master/ChangeLog))

2. **OCP `pci_lmt` / Google `pcie_lmt` / Oxide `lmar`** — purpose-built margining
   tools with HTML/CSV reports, lane-parallel margining, and per-receiver grading.
   `pcie_lmt` targets Gen4/5/6; `lmar` is "Gen4 or later." Use these when you want
   richer reporting or programmatic pass/fail than `pcilmr`. ([OCP pci_lmt](https://github.com/opencomputeproject/ocp-diag-pci_lmt),
   [google/pcie_lmt](https://github.com/google/pcie_lmt/blob/master/README.md),
   [oxidecomputer/lmar](https://github.com/oxidecomputer/lmar))

3. **Raw config sequence (DIY):** find cap `0x0027`, for each lane write the
   Margining Lane Control register (Margin Type + payload), poll the Status register,
   step outward. This is what the tools wrap. Only do this if you need to embed
   margining in your own station code and can't shell out — and validate against
   `pcilmr`'s output on the same part first, because vendor quirks (e.g. Ice Lake
   CPUs) need workarounds that `pcilmr` already hardcodes.

> **Bottom line for the toolkit:** the real lane-margining backend should **shell out
> to `pcilmr` and parse its CSV** (robust, handles quirks) rather than hand-rolling
> the register sequence. Keep the DIY sequence as a documented fallback.

---

## 8. Retimers vs redrivers, bifurcation, switches — manufacturing-test implications

**Redriver (analog repeater):** boosts/equalizes the analog signal. **Invisible to
software** (no PCIe link state of its own). Cheap, low-latency, but doesn't reset the
jitter budget — it's a band-aid for moderate loss. You cannot margin or query it.

**Retimer (protocol-aware repeater):** a **fully clocked** repeater that recovers the
data, re-equalizes, and **re-transmits a clean eye** — it resets the jitter/loss
budget and **creates an independent link segment on each side**. PCIe defines **up to
2 retimers per link**. Each retimer:
- Participates in LTSSM/EQ on both of its segments (each segment trains
  independently — a fallback can be on *either* segment).
- **Appears in lane margining as additional Receiver Numbers** (recv# 2–5), so
  `pcilmr -r` can margin the retimer's RX, not just the endpoints. This is huge for
  cabled/board-to-board links on Zoox's custom boards: you can localize a marginal
  segment to "before vs after the retimer."
- May or may not show as its own PCIe device (depends on whether it implements a
  config-space interface; many are transparent except via the margining cap).

**Bifurcation:** a root-port x16 controller can be split into e.g. **2×x8** or
**4×x4** independent links. The split is configured in **BIOS/strap** and **must match
the board layout**. Mismatch → "device not detected" or trains at the wrong width.
**Top cause of a custom card not enumerating.** Verify the BIOS bifurcation setting
against the schematic's lane assignment.

**PCIe switches (Broadcom/PLX, Microchip/Microsemi):** a switch is one **Upstream
Port** + N **Downstream Ports** internally bridged. Manufacturing-test implications:
- Each port has its **own LTSSM, AER, and link registers** — you must walk the whole
  tree (`lspci -t`) and check **every** link, not just the endpoints.
- The **secondary/subordinate bus** numbering matters: an endpoint's errors may be
  reported by the **switch downstream port** above it, or aggregated at the **root
  port**. AER's Error Source ID tells you the true requester.
- Switches often expose **vendor registers** for per-port LTSSM state, recovery
  counters, and SerDes eye — Broadcom's and Microchip's SDKs/CLI expose these; worth
  integrating for deep debug.
- A switch is itself a unit-under-test: bad switch SerDes, bad switch firmware
  (flow-control bugs → FCP/RX_OVER), or a switch that drops completions (→ Completion
  Timeout at the endpoint above it).

---

## 9. DIAGNOSIS & ROOT CAUSE ON LINUX *(the core section)*

### 9.1 The command toolkit

**Enumerate & topology:**
```bash
lspci -nn                      # vendor:device IDs at each BDF
lspci -tv                      # tree view: see switches, retimers, what's behind what
lspci -nnk -s 03:00.0          # driver bound (-k), IDs (-nn) for one device
lspci -vvv -s 03:00.0          # FULL: caps, LnkCap/LnkSta, AER status, header log
```
`lspci -vvv` is the workhorse — it decodes **LnkCap/LnkSta** (look for `(downgraded)`
on speed/width!), **AER** correctable/uncorrectable status with named bits, the
**header log**, **DPC**, and **Lane Margining** presence.

**Read link speed/width fast (sysfs, no root):**
```bash
cat /sys/bus/pci/devices/0000:03:00.0/current_link_speed   # "16.0 GT/s"
cat /sys/bus/pci/devices/0000:03:00.0/current_link_width    # "16"
cat /sys/bus/pci/devices/0000:03:00.0/max_link_speed        # capability ceiling
cat /sys/bus/pci/devices/0000:03:00.0/max_link_width
# Quick degraded-link scan across the whole machine:
for d in /sys/bus/pci/devices/*; do
  cur=$(cat $d/current_link_speed 2>/dev/null); max=$(cat $d/max_link_speed 2>/dev/null)
  [ -n "$cur" ] && [ "$cur" != "$max" ] && echo "$(basename $d): $cur (max $max)"
done
```

**Raw register reads/writes:**
```bash
# Read LnkSta via setpci (CAP_EXP is the PCIe cap; +0x12 = LnkSta):
setpci -s 03:00.0 CAP_EXP+0x12.W
# Read AER uncorrectable status via the AER ECAP alias:
setpci -s 03:00.0 ECAP_AER+0x04.L
# W1C-clear AER correctable status (write back the set bits is the precise form):
setpci -s 03:00.0 ECAP_AER+0x10.L=0xffffffff     # write-all-ones clears everything
# Force a retrain (set Retrain Link bit 5 in LnkCtl):
setpci -s 03:00.0 CAP_EXP+0x10.W=0x0020
```
(Reading `config` directly via `pread` — what `pcie_bert.c` does — avoids parsing
`lspci` text and is the robust path for a station tool.)

**Decode AER from dmesg.** The kernel `pcieport`/AER driver attaches to **root ports
and RCECs** and prints (exact format from the kernel HOWTO):
```
pcieport 0000:00:1c.0: PCIe Bus Error: severity=Corrected, type=Data Link Layer, (Receiver ID)
pcieport 0000:00:1c.0:   device [8086:9d14] error status/mask=00000040/00002000
pcieport 0000:00:1c.0:    [ 6] BadTLP
...
0000:50:00.0: PCIe Bus Error: severity=Uncorrectable (Fatal), type=Transaction Layer, (Requester ID)
0000:50:00.0:   device [8086:0329] error status/mask=00100000/00000000
0000:50:00.0:    [20] UnsupReq               (First)
0000:50:00.0:   TLP Header: 0x04000001 0x00200a03 0x05010000 0x00050100
```
Read it as: **severity** (Corrected / Uncorrectable-NonFatal / Uncorrectable-Fatal),
**type** (Physical / Data Link / Transaction layer), the **device** that logged it,
**status/mask** (the raw register + which bits were masked), the **named bit(s)** with
`(First)` marking the First-Error-Pointer bit, and the **TLP Header** (decode per §6).
Messages are **rate-limited** to 10 events / 5 s per device+type (fatal not limited).

**Enable/confirm AER is active:** AER requires firmware to hand control to the OS via
ACPI `_OSC`. Force it on if firmware is stubborn:
```
# kernel cmdline:
pcie_ports=native        # OS owns AER/DPC/hotplug even if _OSC didn't grant it
pci=noaer  (to DISABLE)  # for A/B testing whether AER itself is the noise source
```
Check: `dmesg | grep -i aer`, and `lspci -vvv` should show `AER` with `UESta/CESta`.

**Error-injection self-test (`aer-inject`).** Validate your *whole* AER pipeline (the
tool's clear/read/decode + the kernel path) without bad hardware:
```bash
# Kernel must have CONFIG_PCIEAER_INJECT (creates /dev/aer_inject).
# aer-inject config file grammar (jderrick/intel aer-inject):
#   AER  PCI_ID 0000:04:00.1
#   COR_STATUS BAD_TLP
#   HEADER_LOG 0 1 2 3
sudo aer-inject myerr.aer
dmesg | tail        # confirm the injected error appears & decodes correctly
```
Accepted COR symbols: `RCVR, BAD_TLP, BAD_DLLP, REP_ROLL, REP_TIMER`. Accepted UNCOR
symbols: `TRAIN, DLP, POISON_TLP, FCP, COMP_TIME, COMP_ABORT, UNX_COMP, RX_OVER,
MALF_TLP, ECRC, UNSUP`. ([aer-inject SPEC](https://github.com/jderrick/aer-inject/blob/master/SPEC),
[kernel HOWTO](https://docs.kernel.org/PCI/pcieaer-howto.html))

**debugfs:**
```bash
ls /sys/kernel/debug/pci*          # platform-dependent; controller PMU/aspm knobs
# AER statistics are exposed as sysfs attributes on the device (aer_dev_correctable,
# aer_dev_fatal, aer_dev_nonfatal) when CONFIG_PCIEAER_STATS is set:
cat /sys/bus/pci/devices/0000:00:1c.0/aer_dev_correctable
```

**PCIe PMU (perf) — bandwidth/traffic counters.** Useful to confirm the link is
actually carrying traffic during a BERT (so your bit-count isn't fiction) and to spot
abnormal retried traffic. Availability is **platform-specific**:
- **Synopsys DWC PCIe PMU** (`drivers/perf/dwc_pcie_pmu`): per-root-port events under
  `/sys/bus/event_source/devices/dwc_rootport_<sbdf>`. E.g.
  `perf stat -e dwc_rootport_<sbdf>/rx_pcie_tlp_data_payload/ -a sleep 5`.
- **HiSilicon PCIe PMU**, **Intel uncore** (PCIe events in the IIO/uncore PMUs),
  **NVIDIA Grace** uncore PMU. ([DWC PCIe PMU](https://docs.kernel.org/admin-guide/perf/dwc_pcie_pmu.html),
  [HiSilicon PCIe PMU](https://docs.kernel.org/admin-guide/perf/hisi-pcie-pmu.html))

**DPC (Downstream Port Containment).** A root/switch downstream-port mechanism that,
on a Fatal/Non-Fatal error, **automatically disables the link** to contain the error
(stops bad TLPs from propagating / a hung read from hanging the CPU). Cap ID `0x001D`.
When DPC fires, the device **disappears** and `dmesg` shows DPC containment, then the
kernel attempts recovery (DPC + ERR recovery → re-enumerate). **For manufacturing
test this is a double-edged sword:** it cleanly captures a fatal event (great
evidence) but also yanks the device out from under your test. Know whether DPC is
enabled on your root ports (`lspci -vvv` → `DPC:` … `Enabled`) and decide per-station
whether to leave it on (captures + isolates) or off (lets you keep stressing).
Registers: `DPC_STATUS` trigger reason tells you **why** it fired (uncorrectable /
ERR_NONFATAL / ERR_FATAL / RP-PIO / SW-trigger); `RP_PIO_*` logs the offending
root-port read. (kernel `pci_regs.h` `PCI_EXP_DPC_*`)

**Completion-Timeout handling.** The CTO timer range is programmable in **Device
Control 2** (`DEVCTL2[3:0]`, the "Completion Timeout Value" field); `DEVCAP2` says
which ranges are supported and whether CTO can be disabled. A `COMP_TIME` uncorrectable
that only appears under ASPM is the **L1-exit-latency** classic — try
`pcie_aspm=off` or disable L1SS and re-test to confirm.

### 9.2 Systematic root-cause methodology

Run this decision tree. Each branch lists the **evidence** that confirms it.

```
0. ARM: clear AER (W1C), record LnkCap (both ends), DPC/ASPM state, temperature.
1. Does it enumerate? (lspci -nn)
   NO → §LTSSM-stuck: power rails, REFCLK, PERST#, bifurcation, dead PHY.
        Evidence: dmesg "link training" / stuck; rails on scope; BIOS bifurcation.
2. Right link speed & width? (current vs max; lspci shows "downgraded")
   NO (speed) → EQUALIZATION / SI margin / BIOS gen-cap / THERMAL.
        Evidence: dmesg gen-change; retest hot & cold; pcilmr margin; compare LnkCap.
   NO (width) → connector/lane/bifurcation/lane-reversal.
        Evidence: per-lane pcilmr finds the dead lane; reseat; BIOS bifurcation.
3. Errors under stress? Decode WHICH AER bits:
   Correctable cluster {RxErr, BadTLP, BadDLLP, ReplayTO, RollOver}
        → PHYSICAL / SIGNAL INTEGRITY.
        Evidence: count rises with temperature; pcilmr margin shrinks; reseat/cable
        swap changes it; one weak lane in margining; improves with a better preset.
   Uncorrectable {CmplTO, UnexpCmpl, MalformedTLP, FCP, RxOverflow}
        → PROTOCOL / FIRMWARE / UPSTREAM.
        Evidence: header-log decode names the requester/type/address; reproducible
        regardless of temperature/reseat; tied to a specific traffic pattern or a
        firmware/driver version; switch involved.
   {Poisoned TLP, ECRC}
        → DATA CORRUPTION upstream / through a switch/retimer.
        Evidence: header log points upstream; ECRC only if enabled end-to-end.
   {SurpriseDown} → POWER / mechanical.
        Evidence: correlates with load steps / rail droop on scope; reseat; PSU.
4. Errors ONLY hot or ONLY under load → THERMAL SI or POWER droop.
        Evidence: thermal chamber sweep; rail on scope under load; margin vs temp.
5. Nothing reproduces but field-flaky → margin is the discriminator: pcilmr margin
        below limit on one lane = a latent SI fail a pass/fail link-up test misses.
```

**The discriminator table — same symptom, different root cause:**

| Root cause | Speed/width | Correctable bits | Uncorrectable | Margining | Temp/load dependence | Header log |
|---|---|---|---|---|---|---|
| **Signal integrity** | may fall back | RxErr/BadTLP/ReplayTO rising | rare (only if severe) | **low / shrinks; often one lane** | **worsens hot** | n/a |
| **Protocol/firmware** | nominal | none/few | CmplTO/Malformed/UnexpCmpl/FCP | **normal** | **independent** | **names the bad TLP** |
| **Power integrity** | falls back under load | bursty under load | SurpriseDown possible | margin drops under load | **load-dependent** | n/a |
| **Thermal** | falls back hot | rise hot | maybe at temp extreme | **margin vs temp curve** | **temp-dependent** | n/a |
| **Firmware/SW config** | wrong cap/speed | none | UnsupReq/ACSV | normal | independent | names address/region |

This is exactly why the tool should **never report just `errors=5`** — it must report
*which bits*, *which lane*, *the header log*, *the retrain count*, and *the margin*,
because that tuple **is** the root-cause discriminator.

---

## 10. Design Verification vs Manufacturing Test

| | **Design Verification (DV)** | **Manufacturing Test (MT)** |
|---|---|---|
| **Goal** | Prove the *design* is correct & robust across all corners | Prove *this unit* matches a known-good design, fast |
| **Coverage** | Exhaustive: all speeds, all presets, eye sweeps, compliance patterns, corner temps/voltages, protocol corner cases | Targeted: the configs the product ships in; pass/fail to a limit |
| **Tools** | Protocol analyzer (Teledyne LeCroy, Keysight), BERT instruments, scopes, compliance suites, exhaustive lane-margining sweeps | In-band Linux (`lspci`, AER, `pcilmr`), automated BERT to a confidence target, go/no-go limits |
| **Time budget** | Hours–days per design | **Seconds–minutes per unit** |
| **Output** | "The design meets spec at all corners" | "Unit SN1234 PASS: Gen4 x16, 0 uncorr, BER<1e-12 @95%, min margin 0.31 UI" |
| **Errors** | Characterize the *distribution*, find the cliff | Compare to a *limit* set from DV data |

**How one tool serves both:** a well-built in-band tool (like this toolkit) is a **DV
characterization engine** when you run it as a **sweep** (all presets, margin vs
temperature, long soaks → produce the *distribution* and find where the eye closes)
and a **MT go/no-go** when you run it with **fixed limits** derived from that DV data
(the same BERT + margining, but now "min margin ≥ 0.25 UI, 0 uncorrectable" → PASS).
The **lane-margining number is the bridge**: DV uses it to *set* the limit; MT uses it
to *check* the limit. That's the senior-level pitch: "I'd use receiver lane margining
to set a data-driven per-lane eye limit in DV, then enforce that same limit per unit in
MT — replacing pass/fail-on-link-up with a margin number."

---

# Recommendations for the tool and guide

Concrete improvements, grounded in the verification above. The toolkit's core
architecture (Backend abstraction, W1C clear-and-recount discipline, Poisson BER math,
mock/real split) is **sound and the AER bit map is correct** (matches kernel
`pci_regs.h` exactly: DLP=4, SurpriseDown=5, PoisonedTLP=12, FCP=13, CmplTO=14, …).
The gaps are in **breadth of evidence** and **the real-hardware paths**.

### Code (`toolkit/src/computetest/*` and `pcie_bert.c`)

1. **Wire real lane margining to `pcilmr`, not a raw register sequence.**
   `margining._real_margin_lane` is `NotImplementedError`. The robust real path is to
   shell out to `pcilmr` (pciutils ≥3.13), parse its CSV (`-o`), and map per-lane
   timing(%UI)/voltage(mV) to `LaneMargin`. This handles vendor quirks (Ice Lake etc.)
   that a hand-rolled sequence won't. Keep the DIY register sequence as a documented
   fallback and add the **step→UI/mV conversion** (`steps/NumTimingSteps *
   MaxTimingOffset/100`).

2. **Add AER Header-Log capture + decode.** The tool reads `COR_STATUS`/`UNCOR_STATUS`
   but never reads the **Header Log** (+0x1C, 16 B) or the **First Error Pointer**
   (`ERR_CAP[4:0]`, +0x18). For any uncorrectable, capture the 4 header DWORDs and
   decode Fmt/Type, Requester ID, Tag, and Address — turning "Completion Timeout" into
   "read of <addr> by <BDF> timed out." This is the single highest-value diagnostic
   addition.

3. **Correlate with `dmesg` AER and the kernel pcieport path.** Add a reader that
   tails `dmesg`/journal for `PCIe Bus Error` lines, parses severity/type/named-bit/
   TLP-header, and cross-checks against the tool's own register reads. Today the tool
   and the kernel can be **clearing each other's latches**; the guide notes this but
   the tool doesn't reconcile it. Document/disable kernel AER (`pci=noaer`) or read
   the kernel's `aer_dev_correctable`/`aer_dev_nonfatal`/`aer_dev_fatal` sysfs counters
   instead of racing it.

4. **Add an `aer-inject` self-test mode.** A `selftest` subcommand that (on a kernel
   with `CONFIG_PCIEAER_INJECT`) injects a known correctable (`BAD_TLP`) and a known
   uncorrectable with a header log, then asserts the tool's decode/clear/count path
   reports exactly that. This validates the whole pipeline with no bad hardware and is
   a strong interview artifact.

5. **Handle switches and the secondary-bus tree.** `diagnose_all` iterates a flat
   device list. Add `lspci -t`-style topology walking so it checks **every** link
   (root port → switch USP/DSP → endpoint), attributes errors via AER **Error Source
   ID**, and reports *which segment* degraded. Retimers should be surfaced as extra
   margining **Receiver Numbers** (`pcilmr -r 1..6`).

6. **Read severity & mask, and handle DPC.** Before a stress run, read
   `UNCOR_SEVERITY`/`UNCOR_MASK` so you know which bits are Fatal (will trigger
   DPC/link reset under you). Detect the **DPC** cap (`0x001D`), report its
   enabled/trigger state, and after a link-down read `DPC_STATUS` trigger-reason +
   `RP_PIO_*` log so a contained fatal error is captured as evidence rather than just
   "device vanished."

7. **Add Completion-Timeout context (ASPM/L1SS).** When `CmplTO` is seen, record
   `DEVCTL2` CTO value and ASPM/L1SS state, and suggest the `pcie_aspm=off` A/B test.
   Many field "random hang" CTOs are L1-exit-latency, not link SI.

8. **Replace the theoretical bit-count with measured throughput when possible.** The
   BERT accrues bits as `speed × width × time`, which assumes 100% utilization. For a
   credible BER it should *measure* bytes moved (DMA loopback, NVMe I/O, or a **PCIe
   PMU** counter like DWC `rx_pcie_tlp_data_payload`) and use that as `n`. Otherwise
   the BER bound is optimistic. At minimum, document that the bound assumes saturation.

9. **Make the C engine's link math FLIT-aware and fix the Gen6 efficiency comment.**
   `link_bits_per_sec` uses `242/256` for Gen6 — reasonable, but the comment should
   note Gen6 FLIT = 242 B payload / 256 B FLIT and that **Gen6 error accounting is
   FEC-based**, not LCRC-retry-based, so the AER-correctable BERT model under-measures
   Gen6. Add a note that a true Gen6 BERT needs FEC correctable/uncorrectable counters.

10. **Watch `LnkSta.LBMS`/`LABS` and `DLLLA`, not just bit 11.** `linkstate.py` polls
    only Link Training (bit 11). Also latch **Link BW Mgmt Status (14)** and **Link
    Autonomous BW Status (15)** (set when the link changed speed/width) and **DLL Link
    Active (13)** (link-down detection) — these are W1C latches that survive between
    polls, so they catch retrains your 5 ms poll loop misses.

### Guide (`guides/01-job-prep-deep-dive.md`, PCIe chapter)

11. **Fix two register details and one tooling claim.** (a) The guide labels LnkCtl2
    `+0x30` "Target link speed" — correct, but add that the *current* speed reads from
    **LnkSta `+0x12` [3:0]** and add **LnkSta2 `+0x32`** with the **Flit Mode Status**
    bit for Gen6. (b) The AER table shows Root Error Command/Status at `+0x2C/+0x30`,
    which is right for **root ports only** — state that explicitly. (c) The chapter
    says the toolkit "implements … the register-sequencing path for real Gen4+
    hardware," but `margining.py` raises `NotImplementedError` — either implement the
    `pcilmr` path (rec. #1) or soften the claim to "drives `pcilmr`." Accuracy matters
    in an interview artifact.

12. **Add the three things the chapter is missing: `pcilmr`, DPC, and header-log
    decode.** The chapter explains margining conceptually but never names **`pcilmr`**
    (the actual Linux tool that does it today, in pciutils) — add the `--scan` /
    `--margin -TV` / `-r` examples and the **step→UI/mV** conversion. Add a short
    **DPC** subsection (what it is, why a device "vanishes," reading the trigger
    reason). Add a worked **header-log decode** example (raw DWORDs → Fmt/Type/
    Requester/Address) since the chapter mentions the header log but never decodes one.
    Also add **`aer-inject`** as the way to self-test the AER pipeline.

> **Flagged uncertainties** (also marked **[VERIFY]** inline): the exact *bit
> positions* of the Margining Lane Control register fields (the command model is
> confirmed; the offsets I cite are conventional, not from a primary PCI-SIG doc);
> the precise Gen6 FLIT byte split and per-gen InitFC1 resend period; whether a given
> part reports MaxVoltageOffset in mV vs 10 mV units; and the exact dB rounding /
> P10 definition in the TX-preset table (the coefficient ratios are authoritative).
> Validate all register *offsets you write* against `lspci -vvv` / the PCIe Base Spec
> revision for the specific silicon before driving hardware.

## Sources

- Linux kernel — [`include/uapi/linux/pci_regs.h`](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/pci_regs.h) (AER, LnkCap/Ctl/Sta, DPC defines)
- Linux kernel docs — [PCIe AER HOWTO](https://docs.kernel.org/PCI/pcieaer-howto.html), [sysfs-pci](https://docs.kernel.org/PCI/sysfs-pci.html), [DWC PCIe PMU](https://docs.kernel.org/admin-guide/perf/dwc_pcie_pmu.html), [HiSilicon PCIe PMU](https://docs.kernel.org/admin-guide/perf/hisi-pcie-pmu.html)
- pciutils — [`pcilmr(8)` man page](https://man7.org/linux/man-pages/man8/pcilmr.8.en.html), [ChangeLog](https://github.com/pciutils/pciutils/blob/master/ChangeLog)
- Lane margining tools — [OCP `pci_lmt`](https://github.com/opencomputeproject/ocp-diag-pci_lmt), [google/`pcie_lmt`](https://github.com/google/pcie_lmt/blob/master/README.md), [oxidecomputer/`lmar`](https://github.com/oxidecomputer/lmar)
- AER injection — [`aer-inject` SPEC](https://github.com/jderrick/aer-inject/blob/master/SPEC), [kernel `aer_inject.c`](https://github.com/torvalds/linux/blob/master/drivers/pci/pcie/aer_inject.c)
- LTSSM & flow control — [Shane Colton: LTSSM (Pt.4)](https://scolton.blogspot.com/2024/01/pcie-deep-dive-part-4-ltssm.html), [Flow Control (Pt.5)](https://scolton.blogspot.com/2024/11/pcie-deep-dive-part-5-flow-control.html)
- Equalization — [Teledyne LeCroy: PCIe 3.0 link training](https://blog.teledynelecroy.com/2014/11/an-under-hood-view-of-pcie-30-link.html), [Intel Gen3 EQ](https://www.intel.com/content/www/us/en/docs/programmable/683621/current/link-equalization-for-gen3.html), [bitsilica: PCIe EQ Gen3–6](https://bitsilica.com/demystifying-pcie-equalization-from-gen-3-to-gen-6/), [PCIe 3.0 preset table](https://studylib.net/doc/18589313/pci-express-3.0-equalization--the-mystery-unsolved)
- Lane margining concepts — [Cadence: Demystifying PCIe Lane Margining](https://www.chipestimate.com/Demystifying-PCIe-Lane-Margining-Technology/Cadence/blogs/3694), [Synopsys PCIe 4 Lane Margining](https://www.synopsys.com/designware-ip/technical-bulletin/pci-express-4-lane-margining.html)
- Gen6 / PAM4 / FLIT / FEC — [VIAVI PCIe 6.0 guide](https://www.viavisolutions.com/en-us/resources/learning-center/what-pcie-60), [Synopsys PCIe 6.0 FEC/CRC verification](https://www.synopsys.com/blogs/chip-design/pcie-6-verifaction-fec-crc.html)
- TLP/DLLP — [Xillybus PCIe TLP primer](https://xillybus.com/tutorials/pci-express-tlp-pcie-primer-tutorial-guide-2)
