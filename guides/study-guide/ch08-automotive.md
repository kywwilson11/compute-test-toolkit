## Automotive and Serial Buses: GMSL, CAN, Ethernet, I2C/SPI/UART

Four families of bus carry everything on a Zoox compute board that *isn't* PCIe. **Gigabit Multimedia Serial Link (GMSL)**
brings camera pixels in from the harness. **Automotive Ethernet** brings radar, lidar, and
inter-module traffic. **Controller Area Network (CAN)/Controller Area Network Flexible Data-Rate (CAN-FD)** is the vehicle's control nervous system — brakes,
steering, power distribution. And **I2C/SPI/UART** are the housekeeping buses that
configure, identify, and monitor every die on the board. The first three are high-speed
serial links and share a single mental model with PCIe (the Networking and PCIe chapters);
the last three are low-speed but are where the *reason* for a high-speed failure usually
turns up.

This chapter goes deepest on **GMSL**, because on a robotaxi the camera path is both the
highest-channel-count interface you own and the one whose failures are most likely to
surface late — on the vehicle, over 15 m of coax, at temperature — where they cost the most
to catch. Everything else here is real and you'll use it daily, but GMSL is where a Compute
Test Engineer earns the title.

> **The one mental model.** PCIe, GMSL, automotive Ethernet, NVLink, USB, SATA — all the
> same kind of thing: a serial differential SerDes link that recovers a clock from the data
> (CDR), encodes/scrambles for DC balance, equalizes to fight channel loss (TX emphasis + RX
> Continuous-Time Linear Equalizer (CTLE)/Decision Feedback Equalizer (DFE)), *trains* both ends up together, and *counts errors*. The failure physics are
> identical — channel loss, jitter, Inter-Symbol Interference (ISI), reflections, temperature, connector/cable quality.
> Learn the model once; what changes per link is the **vocabulary and the tooling**. So your
> debugging instinct is always the same: *is it trained at the right rate, stress it, watch
> the error counters, suspect the channel and temperature first.*

---

## GMSL — Cameras Over Coax

### What GMSL is and why a robotaxi lives on it

GMSL is a SerDes designed to move
**uncompressed** sensor video from a remote camera to a host System-on-Chip (SoC) over a single inexpensive
cable, while *simultaneously* carrying bidirectional control, power, and synchronization on
that same cable. That "everything on one coax" property is the whole point: a robotaxi has
dozens of cameras scattered around the body, each up to ~15 m of harness away from the
compute box, and you cannot afford a fat multi-conductor cable, a separate power run, a
separate I2C run, and a separate sync wire to every one of them.

The end-to-end path you are protecting:

```text
  Image sensor (e.g. ON Semi AR0820)
     | MIPI CSI-2 (short board trace, camera module internal)
     v
  GMSL SERIALIZER  (MAX9295A / MAX96717 / MAX96717F)
     |  ===========  single 50 Ohm coax (or 100 Ohm STP)  ===========
     |  forward:  3 or 6 Gbps  uncompressed video + embedded data  ----->
     |  reverse:  187.5 Mbps   control (I2C/UART), GPIO, frame-sync   <----
     |  PoC:      DC power rides the same conductor  <-----
     v
  GMSL DESERIALIZER  (MAX9296A dual / MAX96712 quad / MAX96724 quad)
     | MIPI CSI-2 D-PHY or C-PHY (multiple virtual channels)
     v
  Compute SoC capture / VI / ISP  ----PCIe----> GPU(s), NVMe, NIC
```

Read it as: **sensor → serializer → coax → deserializer → CSI-2 → SoC.** That chain is the
unit under test. A frame that lands in `/dev/video0` proves *all* of it works — sensor power
and config, the forward link, the reverse control channel that programmed the sensor, the
coax, the deserializer, the CSI-2 output, and the SoC's capture block. That is why a real
frame capture is the gold end-to-end check and not just a "lock" bit.

### GMSL1 vs GMSL2 (vs GMSL3) — the generations you'll meet

| | GMSL1 | **GMSL2** | GMSL3 |
|---|---|---|---|
| Forward rate | up to ~3.125 Gbps | **3 or 6 Gbps** | 3 / 6 / **12** Gbps |
| Reverse rate | ~1 Mbps (slower control) | **187.5 Mbps** (1.5 Gbps option) | 187.5 Mbps |
| Signaling | NRZ | NRZ | NRZ (<=6G), **PAM4** at 12G |
| Forward EQ | fixed/limited | **continuous adaptive** | continuous adaptive |
| Control tunnel | basic I2C | **I2C + UART, GPIO tunneling** | same + more |
| Typical AV part | MAX9271/MAX9286 | **MAX9295/MAX96717 + MAX9296/MAX96712** | MAX96793 + MAX96792A |

(Rates: ADI GMSL1/GMSL2 channel-spec user guides and the GMSL Wikipedia summary — GMSL1
downlink up to 3.125 Gbps; GMSL2 forward 3 or 6 Gbps, reverse 187.5 Mbps with a 1.5 Gbps
reverse option on some parts; GMSL3 forward 12 Gbps using Pulse Amplitude Modulation 4-level (PAM4) above 6 Gbps.)

The forward rate is **fixed/selectable**, not auto-negotiated — set by CFG-strap resistors at
power-on or by register writes. This is a key difference from Ethernet: there is no
rate-fallback negotiation, so a GMSL2 link either locks at the configured rate or it doesn't
lock at all. The generations are backward compatible (a GMSL2 deserializer can run a GMSL1
serializer in GMSL1 mode), and a deserializer like the MAX9296A can even run **mixed** GMSL1
and GMSL2 links on its two inputs — which matters because the lock register and error
counters live at *different addresses* depending on which mode a given link came up in (more
below).

Zoox-relevant silicon, the parts you will actually probe over I2C:

| Part | Role | Capability |
|---|---|---|
| MAX9295A / MAX9295D | Serializer (on camera) | GMSL2/1, single/dual CSI-2 in; 3/6 Gbps fwd, 187.5 Mbps rev |
| MAX96717 / 96717F | Serializer | GMSL2, CSI-2 in; **F = ISO 26262 functional-safety** variant |
| MAX96793 | Serializer | GMSL3/2, CSI-2 in; 3/6/12 Gbps fwd |
| **MAX9296A** | **Dual** deserializer | 2x GMSL2/1 -> CSI-2; pairs with MAX9295/96717 |
| **MAX96712** | **Quad** deserializer | 4x GMSL2/1 -> CSI-2; coax or STP; the AV workhorse |
| MAX96724 | Quad deserializer | 4x GMSL2/1 -> CSI-2, **tunneling** focus |
| MAX96792A | Dual deserializer | GMSL3/2 -> CSI-2 |

The "F"/"R" suffixes matter on a safety vehicle: the **F** variants add functional-safety
features (CRC/Error-Correcting Code (ECC) on internal memories, register-readback verification, lock-step
diagnostics, a dedicated error pin) so the SerDes can participate in the Automotive Safety Integrity Level (ASIL) chain. If Electrical Engineering (EE)
specs a `96717F`/`96724F`, your test must read the safety status registers too, not just
lock.

### The SerDes model and link establishment

The serializer takes a parallel CSI-2 stream and **serializes** it onto the differential
forward channel; the deserializer recovers the clock from the data stream (no separate clock
wire), **deserializes** it back to CSI-2, and drives the SoC. Two facts drive everything you
do with GMSL2:

1. **The deserializer initiates and owns link training.** At power-on the deserializer drives
   the link and the serializer responds; the handshake is automatic and needs *no software*.
   This is why a GMSL link can lock before your test program has even run — and why "lock"
   alone is necessary but not sufficient (you still have to configure the sensor over the now-
   locked reverse channel before pixels flow). Lock typically establishes in **5–50 ms**
   depending on cable length and electrical conditions.

2. **GMSL2 runs *continuous adaptive equalization*.** At 3/6 Gbps over many meters of coax the
   channel badly attenuates the high-frequency content — the raw eye is closed. The
   deserializer's receiver continuously adapts its equalizer (CTLE + decision-feedback
   equalization, DFE — the receiver cancels inter-symbol interference using its own recent bit
   decisions) and **re-optimizes roughly once per second** to track temperature drift, cable
   aging, and connector wear. It also runs an **eye-opening monitor**: a built-in margin
   measurement with programmable alarm thresholds that fires a run-time alert when the link
   eye degrades *before* it actually loses lock.

> **Why the eye monitor is a high-leverage test lever.** It is the GMSL analog of PCIe lane
> margining — an on-die eye measurement with no scope. A link that *locks* but whose eye
> monitor sits near its alarm threshold is a marginal unit that will drop at temperature in
> the vehicle. Reading the eye-monitor margin (not just the lock bit) turns "it locked" into
> "it locked with X margin" — a captured *parameter* you can set a data-driven limit on,
> exactly the Design Verification (DV)-sets-the-limit / Manufacturing Test (MT)-checks-it pattern from the test-strategy chapter.

A *compliant GMSL2 channel* is specified to deliver a **Bit Error Rate (BER) of 1e-15 or better** under
worst-case conditions (longest cable, aged cable, temperature extremes, PCB impedance
variation, min/max PoC load). That number is your acceptance bar: GMSL is essentially an
error-free pipe when healthy, so any nonzero decode/CRC error count on a soak is a finding,
not noise.

> **Two ways an eye/channel check fakes a pass — both real audit finds.** (1) If the
> eye-opening-monitor verdict compares against a *fixed default* threshold instead of the
> **threshold you configured**, a tightened limit doesn't actually gate — the check reports
> PASS at a margin you meant to fail. Make the verdict read the per-link threshold you set.
> (2) Channel-compliance against the S-parameter masks (insertion/return loss, e.g. ADI's
> AN-2585) is meaningless without the *real* mask numbers; if you don't have them, the check
> must **raise / refuse to pass**, never quietly pass against a placeholder mask. A green
> "channel compliant" with no mask behind it is the worst kind of result — confidently wrong,
> and it ships a marginal camera link. "I don't have the limit yet" is an honest skip; a
> faked pass is a latent field failure.

### Forward and reverse control channels

GMSL is **full-duplex on one conductor**. The two directions are:

- **Forward channel** (3/6 Gbps): serializer → deserializer. Carries the video plus embedded
  data (statistics lines, the sensor's embedded metadata rows).
- **Reverse channel** (187.5 Mbps): deserializer → serializer. Carries *control*: the I2C/UART
  tunnel, GPIO state, frame-sync triggers, and link-management traffic.

The reverse channel is what makes GMSL more than a video pipe. The image sensor and the
serializer sit at the *far* end of 15 m of coax, but the SoC must configure them — set
exposure, gain, the output resolution/format, enable the sensor's streaming, arm frame-sync.
GMSL solves this by **tunneling I2C** (and optionally UART) over the reverse channel so the
SoC's *local* I2C controller can read and write registers in the remote serializer and sensor
**as if they were on the local bus.** The deserializer is the local I2C device; it forwards
each transaction up the reverse channel to the serializer, which replays it on the camera-
module-local I2C bus.

```text
   SoC I2C controller
     | local I2C (e.g. /dev/i2c-1)
     v
   DESERIALIZER (e.g. 0x48 or 0x29)  <-- you i2cdetect this locally
     |  reverse channel over coax  (I2C tunnel)
     v
   SERIALIZER (e.g. 0x40)            <-- appears as a *translated* local address
     |  camera-module-local I2C
     v
   IMAGE SENSOR (e.g. 0x10)          <-- also appears at a translated local address
```

Two consequences own a big slice of your camera debugging:

- **A camera that "won't configure" is often a reverse-channel problem, not a sensor problem.**
  If the forward link is locked but I2C writes to the sensor fail (NACK / `-ENXIO` in
  `dmesg`), suspect the reverse channel: serializer not locked in the reverse direction, I2C
  tunnel not enabled, or an address-translation mistake — before you ever suspect the sensor
  itself.
- **I2C address translation lets identical cameras share one bus.** Four identical sensors all
  ship with the *same* hardwired I2C address (say `0x10`). A quad deserializer performs
  **address translation** so each remote sensor and serializer appears at a *distinct* address
  on the local bus. When you `i2cdetect` a quad-camera carrier you see the deserializer plus
  four translated serializer addresses and four translated sensor addresses — not four devices
  colliding at `0x10`.

### GPIO tunneling and the I2C/UART control tunnel

Beyond register access, the reverse (and forward) channel can **tunnel GPIO**: a logic level
on a pin at one end is reproduced on a mapped pin at the other end, with no separate wire.
On the Maxim parts these are the **MFP (multi-function pin) / GPIO** lines. You map, e.g.,
deserializer GPIO_x → serializer GPIO_y, and a level or pulse driven into the deserializer
pin appears at the serializer pin across the coax. This is how:

- **Frame-sync** triggers reach the sensor (the host's FSYNC pulse is tunneled to each
  camera's trigger input — see below).
- A camera's **error/interrupt** line is brought back to the SoC (sensor fault → serializer
  GPIO → tunneled → deserializer GPIO → SoC interrupt).
- **Reset / power-enable** of the remote module can be driven from the host side.

The **UART tunnel** is the same idea for a byte stream — useful when a camera module has its
own MCU that speaks UART rather than (or in addition to) I2C. For test you mostly care that
the **I2C tunnel** is alive (you can read a known sensor ID register through it) and that the
**GPIO/frame-sync tunnel** is alive (the cameras actually fire together).

### Video transport: tunnel mode vs pixel mode, data types, virtual channels

The deserializer reconstructs a MIPI Alliance (MIPI) **CSI-2** stream for the SoC, and how the video crosses
the GMSL link is configured in one of two modes — a real EE/firmware decision you must
understand to debug a "frames are corrupt / wrong format" failure:

- **Pixel mode** (the original GMSL2 mode). The serializer *strips* the CSI-2 packet header
  and footer, converts the payload to GMSL's internal **pixel** representation, sends that,
  and the deserializer rebuilds a fresh CSI-2 packet (new header/footer) on the far side. It
  understands the data type — so it can do bits-per-pixel packing, **watermarking**, and per-
  stream manipulation. Supported types: RAW8/10/12/14/16/20, RGB565/666/888, YUV422 8/10-bit,
  embedded (EMB8), user-defined, generic long-packet.
- **Tunnel mode** (a.k.a. CSI-2 forwarding). The serializer re-packetizes the *entire* CSI-2
  structure and forwards it verbatim; the deserializer emits it unchanged. It is data-type
  agnostic (*any* CSI-2 type, including ones the pixel-mode logic doesn't model) and lower
  latency, but does no per-pixel processing. The MAX96724 is explicitly the "tunneling"
  variant.

> **Why this matters in a corrupt-frame debug.** A format mismatch — sensor outputs RAW12 but
> the pipe is configured for RAW10, or pixel-mode bits-per-pixel set wrong — produces a frame
> that *captures* (lock is fine, frames > 0) but looks sheared, miscolored, or wrong size.
> That is a **configuration** bug in the video-pipe/data-type setup, not a link or cable
> fault. Lock + frames-captured + *correct resolution and format* must all be checked
> together; the toolkit's `resolution_ok` check exists precisely so a wrong-format capture
> doesn't pass as good.

**Video pipes, streams, and virtual channels.** Inside the link, video moves in **pipes**;
each pipe carries one or more **streams**, and each stream is tagged with a CSI-2 **virtual
channel (VC)** and data type. A quad deserializer aggregates up to four cameras' streams and
multiplexes them onto its CSI-2 output, assigning each a distinct VC (the MAX96714 family
supports up to **16 virtual channels**; a dual-4-lane CSI-2 deserializer like the MAX9296 can
decode up to 16 VC IDs). On the SoC side, each VC typically lands as its own `/dev/videoN`.
This is the mechanism behind "one deserializer, four cameras, four video nodes":

```text
  cam0 --GMSL link0--> pipe Z --VC0--+
  cam1 --GMSL link1--> pipe Y --VC1--+--> CSI-2 (D-PHY/C-PHY) --> SoC --> /dev/video0..3
  cam2 --GMSL link2--> pipe X --VC2--+
  cam3 --GMSL link3--> pipe W --VC3--+
```

When you debug "camera 3 is missing," the question is whether **link3** failed to lock,
whether its **stream/VC mapping** is wrong (locked but routed to the wrong VC or dropped at
the pipe), or whether the SoC's CSI-2 receiver isn't configured for that VC. Three different
root causes, three different fixes.

### FrameSync — multi-camera shutter alignment

For sensor fusion, all the cameras in a cluster must expose **at the same instant** —
otherwise a moving object lands at inconsistent positions across cameras and perception
mis-fuses it. Software timestamp matching is far too jittery; this has to be **hardware**
frame sync, and GMSL provides it through GPIO tunneling:

```text
  Host FSYNC source (SoC GPIO timer, or deserializer's internal generator)
     |  drives the deserializer FSYNC / GPIO pin at the exact frame rate (e.g. 30 Hz)
     v
  DESERIALIZER  -- broadcasts the FSYNC pulse over each link's reverse/control channel -->
     v
  each SERIALIZER  -- drives its tunneled GPIO to the sensor's trigger/FSYNC input -->
     v
  every sensor exposes on the same edge  ==> frames are shutter-aligned
```

The FSYNC master can be the **deserializer's own internal frame-sync generator** (free-
running at a programmed rate) or an **external** source (a SoC GPIO timer, or a vehicle-wide
clock for cross-cluster alignment). The key point for test: a single pulse is fanned out over
the *control channel* to every camera — there is no separate sync wire per camera. So
frame-sync depends on (a) every link being locked and (b) the GPIO/frame-sync tunnel being
configured on every link.

> **The failure mode that a single-camera test misses entirely.** Every camera can lock,
> enumerate, and stream perfect individual frames — and still be **out of frame-sync**, if the
> FSYNC pulse isn't reaching one camera (its GPIO-tunnel mapping is wrong, or that link's
> reverse channel is marginal). Each camera looks healthy in isolation; only when you check
> *cross-camera alignment* do you catch it. This is exactly the
> **all-links-locked-but-not-synchronized** case, and it is why the toolkit models frame-sync
> as a property *separate from* per-link lock — see `check_deserializer` below.

### Coax vs STP cabling and Power-over-Coax (PoC)

GMSL runs over either **50 Ω coax** or **100 Ω shielded twisted pair (STP)**. Coax has lower
insertion loss per meter and can support runs up to ~50% longer for the same link margin, so
robotaxi camera harnesses are typically coax. STP shows up where routing or weight favors it.
The cable choice changes the channel-loss budget and the termination, both of which your
hardware team designs — your job is to know which one a given camera uses so you can read
its cable diagnostics correctly.

**Power-over-Coax (PoC)** runs the camera's DC power up the *same* conductor that carries
the GHz video. A **PoC filter network** (series ferrite/inductor + shunt caps, a bias-tee)
on each end separates the DC power band from the high-frequency signal band so they coexist
without the inductor loading the signal or the signal coupling into the power rail. This is
elegant — one cable does power, video, control, and sync — but it concentrates failure
modes onto one connector:

- **A bad coax connector (cold solder joint, not fully clicked in, corrosion) kills power
  *and* signal** at once: the camera looks completely dead — no PoC, no lock, no I2C. Don't
  chase the sensor; check the connector and the PoC rail first.
- **A PoC filter problem** (open inductor, shorted cap, wrong-value part) can drop the remote
  power, or — more insidiously — pass enough DC to power the camera but couple noise into the
  signal band and *degrade the eye* (rising decode errors, intermittent lock at temperature).
- **PoC line-fault detection.** GMSL parts include **line-fault detection** — an on-chip
  multilevel comparator that classifies the cable condition as **normal, open (disconnected),
  short-to-ground, or short-to-battery** (ADI design note *How to Use GMSL Line-Fault
  Detection for Power Over Coax*). Reading that status localizes a cabling fault without a
  scope. Two caveats worth knowing: it needs a **dedicated line-fault pin/divider circuit**,
  and on many parts it **cannot run simultaneously with PoC on the simple bias-tee** — the
  datasheet specifies an *alternate* PoC+line-fault filter topology if you want both. So
  whether your board exposes line-fault at all is an EE schematic question; confirm it before
  a test step depends on it.

> **The PoC mental checklist.** Camera totally dead → PoC/connector (power gone). Camera
> powers but never locks → PoC noise into signal band, or link-rate/mode mismatch, or
> reverse-channel dead. Camera locks but decode errors climb → marginal signal eye (cable
> loss, PoC noise, temperature). The *symptom* of "dead vs no-lock vs errors" already points
> at the layer.

### Link-lock and frame-sync status registers — the bits that matter

These are the registers your driver/sysfs/I2C reads resolve to, and knowing them lets you
diagnose by hand when sysfs is missing or stale. (Exact addresses are part-specific; these
are the canonical Maxim/ADI ones.)

| What | Where (MAX9296/96712 family) | Meaning |
|---|---|---|
| **GMSL2 link lock** | reg `0x0013`, **bit 3** (`LOCKED`) | 1 = PLLs locked and the forward receive datapath is operational |
| **GMSL1 link lock** | a *separate*, mode-specific lock reg (see below) | 1 = locked when the link came up in **GMSL1** mode (different reg from the GMSL2 bit) |
| **LOCK pin** | open-drain output pin | hardware mirror of lock; high = locked. A board-level "is it up" you can scope |
| **Decode / line-CRC errors** | per-link error-count registers | accumulate on a marginal channel; the GMSL analog of PCIe AER correctable |
| **Video-pipe / packet status** | pipe status regs | per-pipe "video detected", overflow, line-length errors |
| **PoC / line-fault status** | line-fault detect regs | open/short/short-to-battery on the cable (alternate circuit; see PoC section) |
| **(F-parts) safety status** | dedicated error/CRC regs + ERRB pin | memory CRC/ECC, register-readback mismatch, lock-step fault |

The GMSL2 lock bit `0x0013[3]` (`LOCKED`) is well-documented and is what the Jetson/ADI
flows read (e.g. `i2cget -y <bus> 0x48 0x0013`, mask `0x08`). The single most important
*subtlety* is that **lock lives at a different register depending on the mode the link
negotiated.** A link that came up in GMSL2 reports at `0x0013[3]`; a link that came up in
GMSL1 (because the serializer is a GMSL1 part, or a mode mismatch forced it) reports through
a *different, GMSL1-mode* lock register — not the GMSL2 bit. If your check reads only the
GMSL2 lock bit and the link is actually in GMSL1, you wrongly report "no lock." This is a
classic mixed-fleet bug on a deserializer that supports both.

> **Confirm the GMSL1 lock address against your exact part's datasheet — do not hard-code it
> from memory.** The GMSL1-mode lock register is *not* publicly documented as a single
> portable address across the GMSL2/1 deserializer family the way `0x0013[3]` is, and it
> differs by part. Concrete anchor: a pure GMSL1 deserializer like the **MAX9286** reports
> "all enabled links locked" at reg **`0x27` bit 7** (`MAX9286_LOCKED` in the mainline Linux
> `max9286.c` driver); a GMSL2/1 part such as the MAX9296/MAX96712 exposes its
> own GMSL1-mode lock status that you must read out of *that* device's register map (or, more
> safely, via the vendor driver's sysfs `link_status`, which abstracts the mode). The robust
> rule for test code: prefer the driver's mode-agnostic lock attribute; only fall to raw I2C
> when you've pulled the exact address+bit for the exact silicon from its datasheet.

(Lock is fundamentally the detection of valid **sync words** on the serial stream; the
Phase-Locked Loop (PLL)-lock + sync-word condition is what sets the bit and drives the LOCK pin.)

The **LOCK pin gotcha** worth knowing from the field: if the LOCK output reads asserted while
the deserializer is *not even connected* to a serializer, the device is mispowered or damaged
(or stuck in a board-specific reset/programming mode) — a high LOCK with no link partner is
not "good," it's "broken."

What the error counters are actually counting is worth knowing so you read them right. GMSL2
protects its traffic with multiple CRCs: each **control packet** carries a 4-bit sequence
number and a 16-bit CRC, and the link checks (and strips) the **CSI-2 packet ECC** on the
header and the **CSI-2 CRC** on the footer as it converts to/from pixel form. So the
per-link "decode error" / line-CRC counters increment on *physical-layer* decode failures and
CRC mismatches — the marginal-channel symptom. (End-to-end **Video Line CRC**, VID_PXL_CRC, is
a *GMSL3* addition; on GMSL2 your end-to-end pixel-integrity proof is the captured frame plus
the sensor's own embedded-stats CRC, not a single link counter.) Net for test: a nonzero
decode/CRC counter is a channel finding; for true pixel-payload integrity you still rely on
the frame capture and any sensor-side CRC, especially on GMSL2.

### Multi-camera deserializer topologies

A quad deserializer (MAX96712) is the AV building block: four coax inputs, four cameras, one
CSI-2 output (often split across two D-PHY/C-PHY ports) to the SoC. Common topologies you'll
test:

```text
  (A) Quad deser, 4 independent cameras   (the common AV cluster)
      cam0..3 --coax--> MAX96712 --CSI-2(2x4-lane)--> SoC   (4 video nodes, frame-synced)

  (B) Aggregation / GMSL switch board (a "custom PCIe device" at Zoox)
      many cameras --coax--> [multiple quad desers] --PCIe--> SoC
      (a sensor-interface card; each deser is its own I2C device on the carrier)

  (C) Daisy / line-fanout variants
      sensor clusters share trunk runs; address translation keeps identical sensors distinct
```

The test implication is constant: **you enumerate and verify each link, not "the
deserializer."** A quad part with three good links and one dead camera is a *failing* unit,
and a per-deserializer "is it there" check would pass it. Your check must be per-link
(lock + video node + frames + errors for *each* of the four) *and* cross-link (frame-sync
across all four). That two-level structure is exactly how the toolkit models it.

### Toolkit cross-reference: `gmsl.py`

The toolkit's `gmsl.py` (`toolkit/src/computetest/gmsl.py`) implements this model in two
layers, with a real-hardware path (sysfs lock/error + `v4l2-ctl`) and a mock path for
laptop/CI demos.

- **`check_gmsl(link, video_device, ...)` → `GmslHealth`** checks *one* camera link. It reads
  link lock from `/sys/bus/i2c/devices/<link>/link_status`, the error count from
  `.../error_count`, then uses `v4l2-ctl --get-fmt-video` to confirm resolution and
  `v4l2-ctl --stream-mmap --stream-count=N` to actually **capture frames** to `/dev/null`. The
  four pass/fail limits encode exactly the right gate:

  ```text
  link_locked      : the lock bit is set            (PCIe-L0 analog; necessary, not sufficient)
  resolution_ok    : width/height == expected        (catches wrong-format/data-type config)
  frames_captured  : frames > 0                       (the end-to-end sensor->...->SoC proof)
  no_link_errors   : decode/CRC error_count == 0      (marginal-channel catch)
  ```

  This is the GMSL equivalent of the PCIe rule "don't just report `errors=5` — report *which*
  layer." Lock without frames = reverse-channel/sensor-config problem; frames with wrong
  resolution = pipe/data-type config; lock + frames + rising errors = marginal coax.

- **`check_deserializer(addr, n_links=4, ...)` → `GmslDeserHealth`** is the **multi-camera**
  layer. It runs `check_gmsl` per link and then computes a *separate*
  `frame_sync_ok = all(link locked) and (not DESYNC)`. The crucial design choice:
  **`ok` requires every link healthy *and* `frame_sync_ok`** — so the dataclass deliberately
  models the **all-links-locked-but-not-frame-synchronized** case. In the mock, a `"DESYNC"`
  address yields four locked, streaming, error-free links that *still* fail overall, because
  frame-sync is false. That is the fusion-breaking failure a naive per-camera test cannot see,
  encoded as a first-class state. (A `"BAD"` address instead drops `link0` to model a single
  dead camera; the deserializer is still partly up but `ok` is false.)

The `summary()` line a station logs reads like the bench truth you want:

```text
GMSL deser 1-0029: 4/4 links locked, DESYNC -> FAIL
    GMSL 1-0029:link0 lock=True 1920x1080 frames=5 err=0 -> OK
    GMSL 1-0029:link1 lock=True 1920x1080 frames=5 err=0 -> OK
    ...
```

Four OK links, one FAIL deserializer — because they aren't shutter-aligned. That one line is
the whole argument for testing frame-sync as its own thing.

A few **production-hardening decisions** in the toolkit's `_real_check_gmsl` worth
calling out — each was a real audit finding, not a hypothetical:

- **Every `v4l2-ctl` call has an explicit timeout.** `--get-fmt-video` uses
  `timeout=10`; the hot path, `--stream-mmap --stream-count=N`, uses `timeout=max(30,
  frames * 2)` (≥2 s per frame). The streaming case is the most likely hang in the
  whole toolkit — a locked link with no frames flowing (the classic intermittent
  FrameSync scenario this test exists to catch) wedges `v4l2-ctl` indefinitely. On
  timeout the toolkit treats it as `captured = 0` so the existing
  `frames_captured > 0` gate fails honestly instead of the test station wedging.
- **`link` and `video_device` are regex-validated before they reach sysfs/argv.**
  `_LINK_RE = r"^\d{1,4}-[0-9a-fA-F]{4}$"` matches a Linux I²C device id;
  `_VIDEO_RE = r"^/dev/video\d+$"`. Without these, a hostile `link` from a plan file
  (`link: "../../../proc/self/environ"`) would be interpolated into
  `/sys/bus/i2c/devices/{link}/link_status` and read whatever the path resolved to —
  a sysfs information-disclosure primitive.
- **Missing `v4l2-ctl` raises, not silent-fails.** Earlier behavior was to fall
  through to `captured = 0`, which then read as a hardware fault on the station log
  ("0 frames captured = camera dead"); the actual cause was just an unininstalled
  CLI. Now the toolkit raises `RuntimeError("v4l2-ctl not found ...")` which the CLI
  maps to `EXIT_UNAVAIL` (5) — matching nvme-cli / ethtool / nvidia-smi's
  "tool-missing is not a hardware fail" contract.

### Diagnostic flow: common GMSL failure signatures → root cause

This is the table you keep open on the bench. Read the *signature* (what lock/frames/errors/
sync are doing) and it points at the layer.

| Signature | Most likely cause | First moves |
|---|---|---|
| **No lock, camera totally dead** (no I2C either) | PoC/power gone: connector not clicked, cold solder, open PoC inductor | Check coax seating; measure PoC rail at the camera; PoC line-fault status; swap a known-good cable |
| **No lock, camera *is* powered** | Rate/mode mismatch (GMSL1 vs 2, 3 vs 6 Gbps), reverse channel dead, wrong term | Confirm ser/deser generation + rate match; read lock via sysfs `link_status` (mode-agnostic) or `0x0013[3]` for GMSL2 (GMSL1 lock is a separate part-specific reg); check 50/100 ohm termination; verify CFG straps |
| **Intermittent lock** (drops and re-locks, worse hot) | Marginal eye: cable loss/length, connector, PoC noise, temperature | Read **eye-opening monitor** margin + alarm; soak hot/cold and watch lock + error count; reseat/replace coax; check PoC filter |
| **All links locked, but DESYNC** (cameras stream individually, fusion off) | Frame-sync not reaching one camera: GPIO/FSYNC tunnel mapping, marginal reverse channel on one link | Verify FSYNC source + per-link GPIO-tunnel config; confirm every camera fires on the pulse; check the one link's reverse channel |
| **Locked, frames captured, but corrupt/wrong size/color** | Video-pipe config: wrong data type (RAW10 vs RAW12), pixel-mode bpp, VC/stream mapping | Compare sensor output format vs pipe config; check tunnel vs pixel mode; verify VC -> `/dev/videoN` mapping |
| **Locked, but decode/CRC error count climbing** | Marginal channel — the GMSL "links up but fails BERT" pattern | Trend `error_count` over a soak; eye-monitor margin; suspect cable/connector/temperature; this is a *fail* even though it locked |
| **Camera won't configure** (lock OK, I2C NACKs) | Reverse-channel / I2C-tunnel / address-translation problem | `i2cdetect` the deserializer locally; check tunnel enabled; verify translated sensor/ser addresses; `dmesg` for `-ENXIO` |
| **One sensor of an identical set unreachable** | Address-translation collision/misconfig | Check each remote's *translated* address is distinct; two cameras translated to the same address collide |

> **The Printed Circuit Board Assembly (PCBA)→vehicle escape this is all built around.** On the bench you test GMSL through a
> *short* cable; a marginal eye passes. In the vehicle the same link runs **15 m of coax
> through a real harness with real connectors and Electromagnetic Interference (EMI), at temperature** — and that's where a
> marginal link drops or its error count climbs. This is the GMSL twin of the PCIe "passes at
> 25 °C, fails at 85 °C" escape, and it's exactly why GMSL earns *real* coverage at the
> **vehicle/EOL** phase, not just a lock-bit check at module test. Reading the eye-monitor
> margin at module test is the lever that pulls some of that catch *earlier* (capture the
> margin parameter, set a limit), instead of waiting for the vehicle to find it.

### Mapping GMSL to manufacturing-test phases

How the GMSL checks above spread across the line — and which numbers you trend for yield:

| Phase | What runs | The yield/quality signal you watch |
|---|---|---|
| **Bare-board / ICT** | continuity + impedance on the coax/STP traces, PoC-rail presence | shorts/opens before any silicon is stressed; bad-trace boards never reach lock test |
| **Module / PCBA functional** | per-link lock, `v4l2` resolution + frame capture, error_count==0, **eye-monitor margin** captured, frame-sync across the cluster | **camera-lock first-pass yield** (locked-and-streaming / attempted); **eye-margin distribution** (a left-shifting histogram = a marginal lot or a connector/PCB change); DESYNC rate |
| **Cabling / harness screen** | PoC line-fault status (open/short/short-to-batt) on every conductor; lock + error_count through the *real* harness cable, not a bench pigtail | **PoC fault rate by conductor/connector**; lock-fail localized to "cable" vs "module" by swapping a golden cable |
| **Soak / thermal** | lock held + `error_count` and eye-margin **trended over hours, hot and cold** | **drop events per camera-hour** and **error-count slope**; a unit that locks at 25 degC but drops or climbs errors at 85 degC is the classic late escape |
| **Vehicle / EOL** | full-harness lock, frame-sync across clusters, end-to-end capture into the perception stack | final **escape rate** — what the earlier phases with margin limits are trying to drive to zero |

Three field-tested screens worth calling out:

- **Camera-lock yield is a per-link metric, never per-board.** A quad part with one dead link
  is one failed unit *and* one failed link — track both, because a single link that fails
  across many boards points at a specific channel/connector (a fixturing or layout problem),
  while scattered single-link fails point at modules or cables.
- **The eye-margin histogram is the early-warning instrument.** Lock is binary and hides the
  cliff; the eye-monitor margin captured at module test is a continuous parameter, so a lot
  whose margin distribution has shifted toward the alarm threshold flags a marginal build
  *before* any unit actually fails lock — the whole point of capturing the parameter.
- **A golden-cable / golden-module swap is the fastest "module vs cable" split.** When lock or
  error_count fails through the production harness, re-run with a known-good coax (or move the
  suspect module to a known-good slot): if it passes, the harness/connector is the fault; if it
  still fails, the module is. This two-swap routine resolves most "no-lock, powered" fails on
  the line without a scope.

### The hands-on sequence

```bash
# 1. Find the deserializer (and translated sensor/serializer addresses) on the I2C bus
i2cdetect -y -r 1                 # e.g. deser at 0x29 or 0x48; translated cams alongside

# 2. Read link lock.  Prefer the driver's sysfs (mode-agnostic); fall to raw I2C if needed.
cat /sys/bus/i2c/devices/1-0029/link_status      # "locked" / "1" == forward link up
#   raw GMSL2 lock bit by hand (bit 3 of reg 0x0013, the well-documented LOCKED bit):
i2cget -y 1 0x48 0x0013           # & 0x08 -> GMSL2 locked
#   if the link is in GMSL1 mode, lock lives in a DIFFERENT, part-specific register --
#   look up the exact address+bit for your silicon in its datasheet (MAX9286 GMSL1
#   deser, for reference, uses reg 0x27 bit 7), or just trust sysfs link_status above.

# 3. Enumerate the video nodes the SoC sees (one per camera/VC)
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --get-fmt-video          # resolution/format/framerate sane?

# 4. Capture real frames -- the end-to-end proof (sensor->ser->coax->deser->CSI-2->SoC)
v4l2-ctl -d /dev/video0 --stream-mmap --stream-count=10 --stream-to=/tmp/cam0.raw

# 5. Read the physical-layer error counter; trend it over a soak (should stay 0)
cat /sys/bus/i2c/devices/1-0029/error_count      # nonzero == decode/line-CRC errors

# 6. (multi-camera) repeat 2-5 for every link, THEN verify frame-sync across them
```

What the `i2cdetect` of a healthy quad-camera carrier actually looks like makes the
address-translation story concrete — one deserializer plus four *translated* serializer
addresses and four *translated* sensor addresses, none of them colliding at the sensors'
shared hardware address:

```text
$ i2cdetect -y -r 1
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:                         -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: -- -- -- -- -- -- -- -- -- 48 -- -- -- -- -- --   <- 0x29 deserializer (the local device)
30: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
40: 40 41 42 43 -- -- -- -- -- -- -- -- -- -- -- --   <- 4 serializers, translated 0x40..0x43
50: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
60: 60 61 62 63 -- -- -- -- -- -- -- -- -- -- -- --   <- 4 sensors, translated 0x60..0x63
70: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
```

(Real carriers vary; some show `UU` instead of the hex value where a kernel driver has
already claimed the device — `UU` still means "present and bound," not "missing.") The
diagnostic read of this grid: all four serializer *and* sensor addresses present = every
reverse channel is tunneling and address translation is configured. A **missing serializer
address** points at that link's reverse channel or lock; a serializer present but its
**sensor address missing** points at sensor power (PoC) or the camera-module-local I2C; **two
cameras at the same translated address** is an address-translation misconfig (the
"one sensor unreachable" row in the signature table).

The toolkit's `check_deserializer` wraps steps 2–6 into a single per-link-plus-frame-sync
verdict so the operator gets one PASS/FAIL with the per-camera detail attached.

---

## CAN and CAN-FD — The Vehicle Control Bus

CAN is the vehicle's low-bandwidth, ultra-reliable control bus:
brake controllers, steering modules, power distribution, body electronics. Your compute
board has **CAN transceivers** you must prove work — send actuator commands, read vehicle
state, run diagnostics over UDS/DoIP. The compute platform's *high-bandwidth* sensor data
rides Ethernet/PCIe/GMSL; CAN-FD is the control-plane link to the rest of the car.

### The bus, physically

Two wires, **CAN_H and CAN_L**, differential, multi-drop. Bits are **dominant (0)** or
**recessive (1)**:

```text
  Dominant (logical 0):  CAN_H ~3.5V, CAN_L ~1.5V  -> differential ~2V  (actively driven)
  Recessive (logical 1): CAN_H ~2.5V, CAN_L ~2.5V  -> differential ~0V  (idle/released)

  Dominant ALWAYS wins: if any node drives dominant, the bus is dominant.
  Only when ALL nodes release does the bus stay recessive. <- this is what makes
  bitwise arbitration work without collisions.
```

**Termination: 120 Ω at *each physical end* of the bus.** With both terminators present you
measure **~60 Ω** across CAN_H/CAN_L with the bus powered off (two 120 Ω in parallel). This
DMM check is the single fastest, highest-value CAN test on the line:

- **~60 Ω** → both terminators present, healthy.
- **~120 Ω** → one terminator missing (you're reading a single resistor).
- **open / very high** → wiring fault, both terminators missing, or bus not connected.
- **near 0 Ω** → CAN_H/CAN_L shorted.

### Arbitration — how the bus shares without collisions

Every node monitors the bus *while* transmitting. When a node sends recessive (1) but reads
back dominant (0), a higher-priority message is on the bus — that node **loses arbitration,
backs off, and retries later**, while the winner transmits *uninterrupted*. Lower ID = more
dominant bits earlier = higher priority. The winner never even knows there was contention;
no time is wasted (nondestructive arbitration).

```text
  Node A wants ID 0x100 = 001 0000 0000
  Node B wants ID 0x050 = 000 0101 0000
                            ^bit where they differ
  Both start together. At the bit where A sends recessive(1) and B sends dominant(0):
  A reads back dominant -> A LOST -> A stops and waits. B continues, frame intact.
  B (0x050, lower ID) wins because its dominant bit came first.
```

### Frame formats — Classic CAN and CAN-FD

| Field | Classic CAN | Notes |
|---|---|---|
| SOF | 1 bit | Start of frame (dominant) |
| ID | 11 bit (std) / 29 bit (ext) | lower = higher priority |
| RTR / control | a few bits | remote request, IDE, reserved |
| DLC | 4 bit | data length code (0–8 bytes) |
| Data | 0–8 bytes | payload |
| CRC | 15 bit | CRC-15 over frame content |
| ACK | 2 bit | receivers acknowledge |
| EOF | 7 bit | end of frame (recessive) |

**CAN-FD** extends Classic CAN where you need more data but Ethernet is
overkill (e.g., faster ECU firmware flashing):

| Feature | Classic CAN | CAN-FD |
|---|---|---|
| Max data rate | 1 Mbit/s | up to ~8 Mbit/s in the **data phase** |
| Max payload | 8 bytes | **64 bytes** |
| CRC | CRC-15 | CRC-17 (<=16 B) or CRC-21 (>16 B) |
| Bit-rate switching | No | **Yes** (arbitration at nominal rate, data phase faster) |

CAN-FD keeps arbitration at the nominal bit rate (so priority/arbitration still works across
the whole bus) and switches to the faster rate only for the data + CRC phase, flagged by the
**BRS** (bit-rate switch) bit. CAN-FD frames carry the **FDF** bit; a Classic-CAN-only node
sees an FD frame as an error, so mixed FD + classic on one bus is delicate (you generally
don't send FD frames while a classic-only node is present).

**CAN-FD DLC mapping** (non-linear above 8): DLC 0–8 → 0–8 bytes, then 9→12, 10→16, 11→20,
12→24, 13→32, 14→48, 15→64 bytes.

### Bit timing and the sample point

A CAN bit is divided into time quanta with a programmable **sample point** — the fraction of
the bit time at which the receiver samples the level (commonly **75–87.5%** of the bit).
Both ends must agree on bit rate *and* roughly on sample-point placement, or you get
intermittent errors that look like noise. A **wrong bit-rate/timing config** is a top cause
of a node that goes bus-off the moment it tries to transmit — the symptoms look like a
hardware fault but the fix is in the timing registers (`ip link set ... type can` parameters,
or the controller's `tq`/`prop_seg`/`phase_seg` settings).

### Error frames and the three error states

Every node keeps a **Transmit Error Counter (TEC)** and **Receive Error Counter (REC)**.
CAN's brilliance is *self-healing fault confinement* — a node that's causing errors
progressively removes itself:

```text
  ERROR-ACTIVE   (TEC and REC < 128)    normal; sends ACTIVE error flags (dominant)
        |  errors accumulate                 ^  returns here when TEC and REC are
        v                                     |  both back <= 127
  ERROR-PASSIVE  (TEC or REC >= 128) -------+  sends PASSIVE error flags; backs off more
        |  TEC keeps climbing
        v
  BUS-OFF        (TEC > 255, i.e. >= 256)   node removes itself from the bus entirely
                                            (recover: 128 occurrences of 11 recessive bits,
                                             then both counters reset to 0)
```

The boundaries are exact and worth memorizing: error-passive at **TEC or REC >= 128**,
bus-off at **TEC > 255** (the 8-bit counter's top). A node drops back from error-passive to
error-active only once *both* counters fall to **<= 127** again. (Sources: the CAN error-
confinement rules as documented by can-wiki.info and the CSS Electronics CAN-errors intro.)

The five error-detection mechanisms that drive those counters: **bit monitoring** (sent !=
read back), **bit stuffing** (a complementary bit after 5 identical — a violation is an
error), **CRC check**, **frame-format check** (fixed-form fields), and **ACK check**
(at least one receiver must acknowledge). Detecting an error makes the node transmit an
**error frame** (6 dominant bits for an active flag), which destroys the current frame on the
bus and forces a retransmit — and bumps the counters.

> **The single-node test trap.** A node alone on a bus **cannot** transmit successfully:
> after each frame it checks the ACK slot, finds no other node drove it dominant, flags an ACK
> error, retries, and marches TEC straight to BUS-OFF. So testing a compute board's CAN port
> with *nothing else on the bus* always "fails" — you need at least one other node (a CAN
> tool, another board, or a loopback). This catches people constantly; it's not a defect,
> it's physics.

### Linux: SocketCAN and can-utils

Linux treats CAN as a network interface (SocketCAN), so the tooling is `ip` + `can-utils`:

```bash
# Bring up the interface (classic, 500 kbit/s -- the common vehicle rate)
ip link set can0 up type can bitrate 500000
# CAN-FD: nominal 500k arbitration + 2M data phase
ip link set can0 up type can bitrate 500000 dbitrate 2000000 fd on

# State + error counters -- the CAN analog of AER/EDAC; READ THIS FIRST when debugging
ip -details -statistics link show can0
#   look for: state ERROR-ACTIVE (good) / ERROR-PASSIVE / BUS-OFF (bad)
#             berr-counter tx <TEC> rx <REC>
#             bus error / restart counts

candump can0                      # watch all traffic (hex)
candump -t d can0                 # with delta timestamps (timing analysis)
candump -e can0                   # show error frames
candump -l can0                   # log to file (candump-<timestamp>.log)

cansend can0 123#DEADBEEF                       # standard ID 0x123, 4 data bytes
cansend can0 00000123#01.02.03.04.05.06.07.08   # extended ID, 8 bytes
cansend can0 123##0.01.02.03                    # CAN-FD frame (## then flags byte)

cangen can0 -g 10 -I 100 -L 8 -D random   # generate: every 10ms, ID 0x100, 8 random bytes
canbusload can0@500000                    # bus utilization at 500 kbit/s
```

What those tools actually print is worth recognizing on sight. A healthy `candump` is one
column of interface, ID, length-in-brackets, then hex bytes:

```text
$ candump can0
  can0  100   [8]  01 02 03 04 05 06 07 08
  can0  1A0   [3]  DE AD BE
  can0  123   [8]  00 00 00 00 00 00 00 2A
```

The single most important read is the interface-state line. On a *healthy* bus the counters
sit at zero and the state is ERROR-ACTIVE:

```text
$ ip -details -statistics link show can0
2: can0: <NOARP,UP,LOWER_UP,ECHO> mtu 16 ... state UP
    link/can  promiscuity 0
    can <FD> state ERROR-ACTIVE (berr-counter tx 0 rx 0) restart-ms 100
          bitrate 500000 sample-point 0.875
          ...
    RX:  bytes packets errors dropped ...
          ...        0      0       0
    TX:  bytes packets errors dropped ...
          ...        0      0       0
```

On a *sick* bus those same fields are the diagnosis — note `state BUS-OFF` and a saturated
TEC, and the bus-error/restart counts climbing:

```text
    can <FD> state BUS-OFF (berr-counter tx 255 rx 0) restart-ms 100
    ...
          re-started bus-errors arbit-lost error-warn error-pass bus-off
                   3        511          0          4          2       3
```

And with `-e`, an error frame decodes in human-readable form, error counters and all — this
is the exact moment the controller flagged a fault, with the cause spelled out:

```text
$ candump -e can0
  can0  20000088  [8]  00 00 80 19 00 00 00 00
        ERRORFRAME protocol-violation{{error-on-tx}{acknowledge-slot}}
        bus-error error-counter{tx{128}rx{97}}
```

That `acknowledge-slot` / `error-on-tx` with the TEC at 128 is the textbook **single-node /
no-ACK** signature from the trap above — the board transmitted, nobody ACKed, TEC jumped.

**The DBC database.** Raw CAN is just IDs and bytes; a **DBC** file (Vector's format) is the
schema that says "ID 0x100 byte 0 bits 0–7 is `WheelSpeed`, scale 0.1, offset 0, unit km/h."
Tools like `cantools` (Python) decode live traffic against a DBC so your test reads *signals*
("wheel speed = 42.3 km/h"), not bytes. Manufacturing tests use a DBC to assert that a board
emits the right signals at the right rates, and to craft stimulus frames by signal name
rather than hand-packing bytes.

### What a manufacturing CAN test actually does

1. **Termination check** — DMM across CAN_H/CAN_L, expect ~60 Ω (5-second, highest-yield
   check).
2. **Bring-up at the configured bitrate** with a known partner on the bus (CAN tool / another
   board / loopback) — never solo.
3. **Loopback / known-frame exchange** — send known frames, verify received intact in both
   directions; confirm the bitrate is right (wrong bitrate → immediate errors).
4. **Watch TEC/REC and state** — must stay `ERROR-ACTIVE`, TEC/REC at/near 0, never go
   `BUS-OFF` under traffic.
5. **Stress** — `cangen` near max bus load, confirm no error frames and counters stay clean.
6. **Bus-off recovery** — deliberately inject errors, confirm the controller recovers.
7. **(Deeper)** scope CAN_H/CAN_L for clean dominant/recessive levels (~2.5 V common mode,
   ~2 V differential dominant).

### Toolkit cross-reference: `check_can`

`ethernet.py`'s `check_can(iface)` → `CanHealth` reads exactly the right state from
`ip -details -statistics link show`: it classifies `state` (BUS-OFF / ERROR-PASSIVE /
ERROR-WARNING / ERROR-ACTIVE), parses `berr-counter tx/rx` into **TEC/REC**, and detects FD
mode. Its three checks encode the gate: `not_bus_off`, `error_active` (the *only* fully
healthy state), and `low_errors` (TEC and REC both < 96 — a margin below the 128
error-passive threshold, so a unit that's *trending* toward trouble fails before it actually
crosses into error-passive). That sub-threshold limit is the CAN version of "capture the
parameter, set the limit below the cliff."

> **Higher layers are awareness-level for you day one.** **UDS** (ISO 14229) diagnostics —
> sessions, security access, Read/Write Data By Identifier, Routine Control (self-tests),
> firmware download — ride on CAN (ISO 15765 / ISO-TP) or on Ethernet via **DoIP** (ISO
> 13400, TCP port 13400, for fast firmware flashing). **SOME/IP** and **DDS** (ROS 2's
> transport) are service-oriented middleware over Automotive Ethernet. You'll meet them, but
> the compute-board CAN test is about the transceiver, termination, and error counters.

---

## Automotive Ethernet — One Pair, Master/Slave

Radar, lidar, and inter-module traffic ride **automotive Ethernet**. It is standard Ethernet
at the MAC/IP layer (so everything in the **Networking chapter** about IP, sockets, `ip`,
`tcpdump`, iperf applies) but with a radically different **physical layer**, and a few
gotchas that bite specifically on the line.

### The physical layer: single twisted pair, PAM3

| Standard | Speed | PHY | Use at Zoox |
|---|---|---|---|
| 100BASE-T1 (BroadR-Reach) | 100 Mbit/s | single twisted pair, full-duplex, PAM3 | body/chassis, gateways, slower sensors |
| 1000BASE-T1 | 1 Gbit/s | single twisted pair, full-duplex, PAM3 | camera/radar/lidar streams, inter-module |
| 10BASE-T1S | 10 Mbit/s | single pair, multidrop | low-speed multidrop (CAN replacement) |
| 2.5/5/10GBASE-T1 | multi-Gbit | single pair | compute backbone, high-rate sensors |

**Why single pair?** Weight and connector size. A standard 4-pair RJ45 link is unthinkable
across a vehicle harness with hundreds of connections; a single unshielded/shielded twisted
pair, full-duplex (Transmit (TX) and Receive (RX) share the pair via echo cancellation), with **PAM3** line coding
(three voltage levels, more bits per symbol than Non-Return-to-Zero (NRZ)) gets 100 Mbit/s–multi-Gbit over one
lightweight pair. Contrast with consumer Ethernet (the Networking chapter): 4 pairs, RJ45,
auto-MDI-X, and clock auto-negotiation — none of which apply here.

### Master/slave — a config, not a cable

This is the automotive-Ethernet gotcha that wastes the most bench time. Unlike consumer
Ethernet, one PHY on each link is the **MASTER** (it provides the clock) and the other is the
**SLAVE** (it recovers the clock). The two ends **must be opposite**. A "no link" between two
correctly-cabled, healthy PHYs is very often a **master/master or slave/slave
misconfiguration**, not a wiring fault.

> **First move on a dead automotive-Ethernet link: check the *role*, not the cable.** Run
> `ethtool` and read master/slave on both ends *before* you suspect the harness. Two masters
> or two slaves never link, no matter how perfect the cable.

### The PHY and MDIO

The PHY (Marvell **88Q2112**, TI **DP83TG721-Q1**, Broadcom multi-Gig families) is configured
over **MDIO** (the management bus — clause-22 for legacy 100 Mbit, clause-45 for Gig+), and
Linux exposes it through the netdev + **phylib**. The master/slave role, link status, and the
PHY's error/diagnostic counters all live in PHY registers reachable over MDIO; `ethtool`
surfaces the common ones, and `mdio-tool`/`phytool` reach raw registers when you need them.
Many automotive PHYs (e.g., DP83TG721) also carry **TSN/AVB** support and **TC10**
(OPEN Alliance coordinated sleep/wake) — words you'll see in the datasheet; not your day-one
ownership.

### AVB/TSN basics

**TSN** (Time-Sensitive Networking, the IEEE 802.1 suite) makes Ethernet *deterministic* —
bounded latency and jitter for real-time sensor traffic alongside best-effort traffic, which
an AV stack needs so sensor frames arrive in their time window. The pieces you'll hear:
**gPTP** (802.1AS, a 1588 PTP profile) distributes a grandmaster clock for sensor fusion;
**802.1Qbv** time-aware shaping schedules traffic into time slots; **802.1Qbu/802.3br**
frame preemption lets urgent frames interrupt long ones. Validate gPTP with
`ptp4l`/`phc2sys` and check **offset-from-master** convergence and PHC↔system-clock sync. You
don't own the TSN config day one, but the test hooks (is the clock locked, what's the
offset) shouldn't surprise you.

### Diagnostics

```bash
ethtool eth1                 # link up? speed? duplex? -- and master/slave role
ethtool -i eth1              # driver + firmware version (re-qualify on FW change)
ethtool -S eth1             # stats: rx/tx errors, CRC/align errors, dropped -> error counters
ethtool -t eth1 online       # PHY/MAC self-test (offline is more thorough but drops link)
ethtool --cable-test eth1            # TDR cable diagnostics on supported PHYs
ethtool --cable-test-tdr eth1        # TDR with fault type + DISTANCE TO FAULT
iperf3 -c <partner> -t 30            # throughput: 1000BASE-T1 should sustain ~0.95 Gbps
```

The fields the toolkit (and you) actually parse out of `ethtool eth1` — note the
master/slave line that consumer Ethernet never has, and the `Speed` line `_parse_speed`
reads:

```text
$ ethtool eth1
Settings for eth1:
    Supported ports: [ TP ]
    Supported link modes:   1000baseT1/Full
    Speed: 1000Mb/s
    Duplex: Full
    Port: Twisted Pair
    PHYAD: 0
    master-slave cfg: forced master
    master-slave status: master
    Link detected: yes
```

`master-slave status: master` on *both* ends is the no-link bug from above; one must read
`slave`. The error counters live in `ethtool -S` and are the marginal-Signal Integrity (SI) tell when they
creep on an otherwise-up link:

```text
$ ethtool -S eth1 | grep -iE 'err|crc|symbol'
     rx_errors: 0
     tx_errors: 0
     rx_crc_errors: 0
     SymbolErrorDuringCarrier: 0
```

**`ethtool --cable-test-tdr` is the automotive-Ethernet killer app** — the single-pair cousin
of PCIe lane margining, scope-free. It pulses the pair and times the reflection to report
**fault type + distance to fault**: **OK**, **Open Circuit** (with distance), **Short** (to
another pair), **Impedance Mismatch** (a reflection from a discontinuity), or **Noise** (the
test couldn't complete). On a vehicle harness the *distance* pinpoints which connector or
segment is bad. The result reads back per-pair, with the distance that localizes the fault:

```text
$ ethtool --cable-test-tdr eth1
Cable test completed for device eth1.
Pair A code Open Circuit
Pair A, fault length: 4.50m
```

(A clean run reports `Pair A code OK`. On an unsupported PHY the command errors out instead —
which is exactly why `check_ethernet` treats "unsupported" as *skipped*, never a fail.) The
diagnostic triad: a link that's *up* with *low iperf throughput* and *rising CRC counts*
(`ethtool -S`) is marginal SI — confirm it with the Time-Domain Reflectometry (TDR), which localizes the fault on the
cable.

**Raw PHY registers when `ethtool` isn't enough.** The master/slave bit and link state live
in standard PHY registers you can read directly over MDIO with `phytool`/`mdio-tool` — useful
when a driver doesn't surface a field or you're bringing up a board pre-driver. The 1000BASE-T1
role lives in the PMA control register; the generic status register `0x01` (BMSR) bit 2 is
link-up:

```text
# phytool read <iface>/<phyaddr>/<reg>   (clause-22)  or  /<devad>/<reg> for clause-45
$ phytool read eth1/0/0x01          # BMSR; bit 2 (0x0004) = Link Status (1 = up)
0x796d
$ mdio eth1 phy 0x00                # raw register dump via the 'mdio' tool, if present
```

(Exact register/bit for the master/slave *role* is PHY-specific — Marvell 88Q2112 vs TI
DP83TG721 differ; pull it from that PHY's datasheet. `ethtool`'s `master-slave status` is the
portable read; raw MDIO is the fallback.)

### Toolkit cross-reference: `check_ethernet`

`ethernet.py`'s `check_ethernet(iface)` → `EthHealth` parses `ethtool` for **link, speed
(handling "2.5G" → 2500 Mbps), and master/slave role**, reads **Receive (RX)/Transmit (TX) errors** from
`ethtool -S`, optionally runs an `iperf3` throughput leg, and — on supported PHYs — runs a
**TDR cable test** (`--cable-test-tdr`), parsing fault `pair`/`code`/`distance_m`. The checks:
`link_up`, `speed_ok` (>= expected — catches a 1000BASE-T1 link that came up at 100), 
`low_errors` (rx+tx < 10), `throughput_ok`, and `cable_ok` (TDR not a fault). Note the
deliberate design that the real TDR path treats *unsupported* as **"skipped," never a fail**,
so a PHY that can't do TDR doesn't false-fail a good link — the same "don't punish a missing
capability" discipline you want everywhere in MT code.

Three **production-hardening** decisions in the toolkit's implementation worth knowing:

- **The interface name is regex-validated before it reaches `ethtool` / `ip`.**
  `_IFACE_RE = r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,14}$"` — Linux's `IFNAMSIZ - 1` of 15
  characters, with the **leading character constrained** to alnum/underscore so a
  value like `--help` can't slip through into argv as an option to `ethtool`/`ip`. A
  plan-file `ethernet: ["--help"]` would otherwise emit `ethtool --help` and parse the
  help text as if it were the device's link state. The leading-char constraint was
  the audit fix; the bare-char-class regex caught everything else but missed that.
- **Every `subprocess.run` has an explicit timeout.** `ethtool`/`ethtool -S` and
  `ip -details -statistics link show` use `timeout=10`; without it, a wedged PHY (a
  driver bug, a flaky MDIO controller) wedges the test station indefinitely. The
  sibling calls `_real_iperf` and `_real_cable_test` had timeouts since day one;
  this is the audit-1 finding that closed the inconsistency.
- **Parser hardening from Hypothesis.** `_parse_speed` was crashing on
  `"Speed: . G"` (regex `[\d.]+` matched the bare `.` and fed `float('.')` to the
  parser) and `_stat` was crashing on `"weird: 5²\n"` (Unicode digit, `str.isdigit()`
  returns True, `int()` raises). Both were found by property tests in
  `tests/test_parsers_property.py` and pinned with `@example(...)` decorators. The
  real-world cases came from `ethtool` output captured on a marginal NIC.

> **Contrast with the Networking chapter.** That chapter owns the IP/socket/`tcpdump`/iperf
> layer and standard Ethernet. Here the *additions* are: single-pair PAM3 PHYs, the
> **master/slave role** failure mode, **MDIO/phylib** access, **TDR cable test**, and
> **TSN/gPTP** determinism. Same MAC/IP debugging on top; different wire underneath.

---

## I2C, SPI, UART — The Housekeeping Buses

These three carry *configuration, identity, and health*, not sensor bandwidth: who are you
(EEPROM), what's your temperature (sensor), set your registers (sensor/SerDes config), are
your rails in spec (power monitor), what time is it (RTC). They're far simpler than PCIe — and
that's exactly why you must know them cold: **when a high-speed link won't come up, the reason
is frequently sitting in a register you read over I2C.** (Full depth lives in the Embedded
Buses chapter; this is the test-floor essentials and the GMSL tie-in.)

| Property | I2C | SPI | UART |
|---|---|---|---|
| Wires | 2: SDA, SCL | 4: MOSI, MISO, SCLK, CS | 2: TX, RX |
| Clock | shared, from controller | shared, from controller | **none** — async, agreed baud |
| Topology | multi-controller, multi-target (shared) | 1 controller, N targets (one CS each) | point-to-point |
| Addressing | 7-bit (or 10-bit) device address | none — CS selects target | none |
| Duplex | half | full | full |
| Drive | open-drain + pull-ups (wired-AND) | push-pull | push-pull |
| Typical speed | 100k / 400k / 1M / 3.4M Hz | 1–100 MHz | 9600–115200 (to a few Mbaud) |

Mental model: **I2C trades speed for wires** (two wires, address-multiplexed, slow). **SPI
trades wires for speed** (more pins, no addressing, fast). **UART trades a clock wire for a
timing agreement** (no clock, both ends must already know the baud).

### I2C — the test floor's most-used bus

Both lines are **open-drain**: a device can only pull low; external pull-ups pull high
(wired-AND — any device holding low wins). A transaction: **START** (SDA falls while SCL
high) → 7-bit address + R/W bit → target **ACK** (pulls SDA low) → data bytes each ACK'd →
**STOP** (SDA rises while SCL high). A register read uses a **repeated START**: write the
register pointer, repeated START, then read.

> **The #1 I2C gotcha: 7-bit vs 8-bit address.** Datasheets list a **7-bit** address (e.g.
> `0x48`); the byte on the wire is `addr << 1 | r/w` (so `0x90` write / `0x91` read). **Linux
> tools take the 7-bit address.** "The device is at 0x48 but my code talks to 0x90 and gets
> nothing" is the classic mistake.

```bash
i2cdetect -l                 # list all I2C buses the kernel knows about
i2cdetect -y 1               # scan bus 1: grid of addresses that respond
                             #   "UU" = claimed by a bound kernel driver (don't poke)
                             #   "48" = ACKs, no driver bound (free to probe)
                             #   "--" = no response
i2cget -y 1 0x48 0x00        # read register 0x00 from device 0x48
i2cget -y 1 0x48 0x00 w      # read a 16-bit word (watch byte order!)
i2cset -y 1 0x48 0x01 0xFF   # write 0xFF to register 0x01
i2cdump -y 1 0x48            # dump all registers (great for exploring an unknown part)
i2ctransfer -y 1 w2@0x48 0x01 0xFF r1@0x48   # explicit write-then-read (repeated START)
```

**Debug outside-in:** `i2cdetect` first (ACK at the right address? `--` everywhere = dead
bus/pull-ups; wrong address = check ADDR strap pins). Then scope SDA+SCL together: no edges =
not clocking; stuck low = a device holding the bus or a hung target stretching SCL forever
(fix: clock 9+ SCL pulses, a bus-recovery sequence); slow rounded rising edges = pull-ups too
weak / bus capacitance too high; missing ACK = wrong address / unpowered / in reset / the
7-vs-8-bit mistake. `dmesg` logs `-ETIMEDOUT`/`-ENXIO` (no ACK) with the bus and address.

**The GMSL tie-in (why I2C is *the* camera-bring-up bus).** As covered in the GMSL section,
the deserializer is a *local* I2C device that **tunnels** transactions over the reverse
channel to the remote serializer and sensor, with **address translation** so identical
cameras don't collide. The whole camera diagnostic flow is I2C: talk to the deserializer
(lock asserted at `0x0013[3]`?), then through the tunnel to the sensor (configured? powered
via PoC?). The single highest-value tunnel probe is reading a **known sensor ID register**
through the translated address — if it returns the datasheet's chip-ID, the entire reverse
path (deser tunnel → coax → serializer → sensor I2C) is proven in one read:

```text
# deserializer GMSL2 lock first (bit 3 of 0x0013):
$ i2cget -y 1 0x48 0x0013
0x08                              # bit 3 set -> GMSL2 locked

# then the sensor's chip-ID register THROUGH the tunnel, at its TRANSLATED address (0x60).
# (ON-Semi AR-series sensors conventionally expose chip-version at 0x3000 -- use YOUR
#  sensor's actual ID register + expected value from its datasheet; values below illustrate.)
$ i2cget -y 1 0x60 0x3000 w       # 16-bit chip-ID register read through the tunnel
0xAABB                            # == datasheet chip-ID -> reverse tunnel + sensor alive

# a sensor that won't answer through the tunnel NACKs (reverse channel / translation / PoC):
$ i2cget -y 1 0x60 0x3000 w
Error: Read failed                # -> dmesg shows i2c -ENXIO on bus 1, addr 0x60
```

**A dead I2C reverse channel = no sensor config = no video, even over a perfect coax** — and
lock can be asserted while decode-error counters climb on a marginal coax, the PCIe "links up
but fails Bit Error Rate Test (BERT)" pattern read over I2C instead of Advanced Error Reporting (AER). (The sensor's ID-register address and
expected value are part-specific; pull them from the image-sensor datasheet, not memory.)

### SPI — fast, point-to-point-ish

Four push-pull lines: **SCLK** (clock), **MOSI** (out), **MISO** (in), **CS** (chip select,
active-low, one per target). No addresses — asserting a target's CS selects it. Full-duplex:
a bit goes out on MOSI and a bit comes in on MISO on every clock.

**The SPI gotcha: CPOL/CPHA mode.** Both ends must agree on clock polarity (idle high/low)
and phase (sample on leading/trailing edge), or every byte is garbage:

| Mode | CPOL (idle) | CPHA | Sample on |
|---|---|---|---|
| 0 | 0 (low) | 0 | leading edge |
| 1 | 0 (low) | 1 | trailing edge |
| 2 | 1 (high) | 0 | leading edge |
| 3 | 1 (high) | 1 | trailing edge |

Modes 0 and 3 dominate. Wrong mode → data shifted a bit or scrambled while clock/CS look
perfect on the scope. Bit order (MSB/LSB-first) and word size must match too.

```bash
ls /dev/spidev*                                  # devices are /dev/spidevB.C = bus.chip-select
spidev_test -D /dev/spidev0.0 -s 1000000 -v -p "\x9F\x00\x00\x00"
#   0x9F = JEDEC READ-ID on most SPI NOR flash; reply = manufacturer/device ID -> link proven
flashrom -p linux_spi:dev=/dev/spidev0.0,spispeed=1000   # read/probe a SPI NOR flash
```

**Debug:** verify with a **known command** — a flash's READ-ID (`0x9F`) returns a fixed
manufacturer/device ID; the right ID is the fastest end-to-end proof. `0x00`/`0xFF`
everywhere = nothing driving MISO (dead/unselected target, wrong CS, wrong mode so the
command is never recognized).

### UART — the debug console

Two lines **Transmit (TX)** and **Receive (RX)** (cross-connected: each device's TX → the other's RX) + common
ground. **Asynchronous** — no clock, both ends preconfigured to the same **baud**. Frame:
idle-high, **start bit** (high→low), data bits (LSB-first), optional parity, 1–2 **stop
bits**. **"115200 8N1"** = 115200 baud, 8 data, No parity, 1 stop — the de-facto console
default.

> **Two UART hazards.** (1) **Voltage:** board debug UARTs are TTL/CMOS **3.3 V (or 1.8 V)**;
> true **RS-232** swings ±3–15 V with *inverted* logic. Connecting a ±12 V RS-232 line to a
> 3.3 V pin **destroys it** — use a level-shifter/USB-TTL adapter at the right voltage,
> confirmed against the schematic. (2) **Baud mismatch** is the #1 failure: garbage characters
> on a perfect cable. Try the ladder (9600/19200/38400/57600/115200/921600). And **TX/RX
> swap** is the eternal bug — both ends "transmit on TX," so you must cross them.

```bash
dmesg | grep tty                                 # which serial devices enumerated
stty -F /dev/ttyUSB0 115200 cs8 -cstopb -parenb  # configure 115200 8N1
picocom -b 115200 /dev/ttyUSB0                   # interactive console (exit: C-a C-x)
screen /dev/ttyUSB0 115200                       # alternative terminal
```

For programmatic/instrument use, `pyserial` — and **always set a timeout** so a UART that
never replies can't hang the test:

```python
import serial
ser = serial.Serial("/dev/ttyUSB0", 115200, timeout=2,
                    bytesize=8, parity="N", stopbits=1)
ser.write(b"*IDN?\n")
print(ser.readline().decode(errors="replace"))
ser.close()
```

**Common test uses:** the boot console (watch a board power up, catch a bootloader hang or a
kernel panic in real time) is the highest-value UART; also GPS modules, instrument links, and
a camera module's MCU when it speaks UART (possibly tunneled over GMSL).

---

## Putting It Together at Zoox

Every bus in this chapter maps to a manufacturing-test phase and a captured parameter, the
same way PCIe and Non-Volatile Memory Express (NVMe) do:

| Bus | What you prove | Earliest phase that catches it | Captured parameter |
|---|---|---|---|
| **GMSL** | each link locks, streams frames, error-free, frame-synced | lock/format at **module**; marginality at **vehicle** (real 15 m harness) | per-link lock, **eye-monitor margin**, decode-error count, frame-sync state |
| **CAN** | transceiver works, no bus-off under load | **module** (with a bus partner); vehicle EOL talks to real ECUs | state, **TEC/REC**, termination ohms |
| **Auto Ethernet** | link at rate, low errors, cable healthy | **module**; harness faults at **vehicle** | speed, master/slave role, rx/tx errors, **TDR distance-to-fault** |
| **I2C/SPI/UART** | every housekeeping device present and readable | **PCBA/module** (enumeration + known-ID reads) | `i2cdetect` map, known-ID readbacks |

The throughline is the same one the rest of this guide hammers: **capture the parameter, not
just the verdict.** "Camera locked" / "CAN is up" / "Ethernet linked" are binary and hide the
margin. The GMSL eye-monitor margin, the CAN TEC/REC trend, the Ethernet TDR distance, the
GMSL decode-error count over a soak — those are the *numbers* that let DV set a data-driven
limit and MT check it, and that turn a vehicle-level "passes at 25 °C, drops at 85 °C" escape
into a module-level catch with evidence already attached. Most of the expensive failures here
are *only detectable* late (over the full harness, at temperature) but were *introducible*
early — which is the whole reason GMSL gets the deepest coverage of the four, and why it gets
real teeth at the vehicle phase.
