This is the context opener for the rest of the guide. It answers three questions
before any register appears: *what* you are testing (the compute platform and the
autonomy data path it serves), *where* you test it (the four manufacturing phases),
and *why the discipline is shaped the way it is* (Design Verification vs Manufacturing
Test — different goals, statistics, and consumers). Everything here is deliberately
shallow on detail; each interface and technique gets its own deep chapter, and this
opener tells you which one to reach for at which phase.

The one-sentence version of the job: **prove every interface on a compute board works
— at speed, under load, at temperature — and catch the bad units before they cost 10×
more to find downstream.** Hold that sentence; the whole guide serves it.

---

## 1. What the Zoox Compute Platform Is

Zoox builds a purpose-built robotaxi — a symmetric, bidirectional vehicle with no
steering wheel and no human driver to fall back on. The **compute platform** is the
brain of that vehicle: a set of **server-grade compute assemblies** that ingest sensor
data, run the perception → prediction → planning → control stack, and command the
vehicle's motion. You are testing data-center-class hardware that has been put inside a
car.

That phrase — *server-grade compute in a vehicle* — is the whole tension of the job.
Data-center parts assume a clean rack: stable power, filtered air, a bench tech a few
feet away. A robotaxi gives them none of that. The same silicon now lives in a sealed,
vibrating, temperature-cycled enclosure, on a vehicle power bus, expected to run a
safety-critical workload for years with no one watching. Manufacturing test is what
bridges that gap: it proves the data-center part survives the automotive environment
*before* it carries a passenger.

The shape of the platform:

- **Server-class SoCs/CPUs + GPUs** for the AI/perception workload — data-center-grade
  compute (lots of PCIe lanes, Error-Correcting Code (ECC) memory, high power and heat) in an automotive
  enclosure. (Deep dive: the **GPU chapter**.)
- **Custom PCIe devices.** Zoox designs its own PCIe cards — sensor-interface cards,
  Gigabit Multimedia Serial Link (GMSL) camera aggregators, Field-Programmable Gate Array (FPGA)/accelerator cards, switch/fan-out boards. *Custom* is
  the load-bearing word: there is no vendor datasheet test plan and no supplier-provided
  diagnostic. *You* write the test that proves the board works. This is the
  highest-leverage part of the job — there is no fallback, so the quality of the test
  is entirely on you. (Deep dive: the **PCIe chapter**.)
- **Storage and memory.** Non-Volatile Memory Express (NVMe) SSDs (OS, logging, maps and models — sensor logging
  alone is enormous) and Error-Correcting Code Dynamic Random-Access Memory (DRAM) (DDR4/DDR5). (Deep dives: the **NVMe** and **Memory**
  chapters.)
- **High-speed links — every one a SerDes that can train wrong, drift with temperature,
  or fail a marginal lane.** PCIe (Gen3/4/5) between everything; **Gigabit Multimedia Serial Link** from cameras;
  **automotive Ethernet** for radar/lidar/inter-module; **Controller Area Network (CAN)/Controller Area Network Flexible Data-Rate (CAN-FD)** on the vehicle
  bus. Most of what you debug lives here. (Deep dives: the **PCIe** and **automotive/
  serial-bus** chapters.)
- **Redundancy and safety.** Because a single compute fault could mean loss of vehicle
  control, the platform is built with redundancy — redundant compute paths, ECC on
  memory, dual-path links where it matters. Your tests don't just find defects; they
  **verify that the safety and detection mechanisms actually work** (ECC really corrects,
  the redundant link really carries traffic, bus-off recovery really recovers). More on
  this framing in the **functional-safety** chapter (ISO 26262 / Automotive Safety Integrity Level (ASIL)).
- **Linux, everywhere.** The platform runs Linux and your test stations run Linux. Your
  command-line debugging fluency is load-bearing, not incidental.

### 1.1 The autonomy data path (what you are protecting)

```text
 Cameras (e.g. AR0820) --MIPI CSI-2--> GMSL serializer (MAX96717)
        --coax up to 15 m through the harness--> GMSL deserializer (MAX96712)
        --CSI-2 / PCIe--> Compute SoC --PCIe--> GPU(s) + NVMe + NIC
 Lidar / Radar --Automotive Ethernet (100/1000BASE-T1)--> Compute
 Vehicle bus  --CAN / CAN-FD--> Compute
```

Why this picture matters to a test engineer: a single weak PCIe lane, a Gigabit Multimedia Serial Link (GMSL) link that
won't lock at 85 °C, or a Non-Volatile Memory Express (NVMe) drive that throttles under sustained logging can
*silently* degrade perception. In a robotaxi that is a safety issue, not a
customer-annoyance issue. Manufacturing test is the gate that keeps a marginal board
from ever reaching a vehicle. That is the weight behind "ship only good units" — and
the reason the false-fail-vs-escape trade is asymmetric here (see the Manufacturing Test
chapter on yield and test economics).

### 1.2 Where you sit

You are in the **Compute program**, on **manufacturing test & diagnostics**. Your daily
orbit:

- **Electrical Engineering (EE) / hardware design** — they design the boards; you test
  them. You receive schematics, board files, and bring-up reports; when your test finds
  a failure, Electrical Engineering is who you hand the evidence to. The sharper your evidence (decoded Advanced Error Reporting (AER),
  per-lane margin, eye/thermal correlation), the faster the fix.
- **Software / firmware** — drivers, Board Support Package (BSP), firmware images. Your tests run on their Linux
  image and flash their firmware; a new firmware drop means you re-qualify the test.
- **Manufacturing / New Product Introduction (NPI) / operations** — the people who run your tests on the line. Your
  test must be fast, robust, and operator-proof.
- **Contract Manufacturers (CMs)** — external partners who build at volume. You release
  test programs *to them* and support them remotely (see the Manufacturing Test chapter
  on the Contract Manufacturer relationship).
- **Quality / reliability** — yield data, Return Merchandise Authorization (RMA)/field returns, corrective action.

You are the person who turns "the hardware team thinks it works" into "we have data
proving 1,000 units work, and a gate that stops the ones that don't."

---

## 2. The Four Manufacturing Test Phases

This is the backbone of the whole job: **Printed Circuit Board Assembly (PCBA) → module → system → vehicle.** Every test
you write lives at one of these phases, and the central skill is **placing each test at
the earliest phase that can catch its defect.** This section is the orientation; the
phases get worked in depth in the Manufacturing Test chapter (The Four Phases, In Depth).

| Phase | What the unit is | Who runs it | What you're proving |
|---|---|---|---|
| **Printed Circuit Board Assembly** | Bare PCB + components, off the Surface-Mount Technology (SMT) line | Contract Manufacturer (CM), usually | Built correctly: right parts, good solder, no shorts/opens, powers on, basic boot |
| **Module** | PCBA + heatsink + enclosure + connectors = a functional unit | Contract Manufacturer or Zoox | Every interface works at speed, under load, at temperature; firmware loaded; calibrated |
| **System** | Multiple modules integrated into the compute box/rack | Zoox | Modules talk to each other; full-load power/thermal; system boots and runs the stack |
| **Vehicle** | Compute installed in the car with real sensors | Zoox | End-to-end: real cameras lock over Gigabit Multimedia Serial Link (GMSL), sensors stream, vehicle-level End-of-Line (EOL) checks |

The reason the phase matters is economic and it is steep: **the cost to find and fix a
defect rises roughly 10× at each phase you let it escape to** — rework a board in the
line (1×), tear open a module (10×), isolate which module in a system (100×), pull
compute from a vehicle (1000×), or roll a truck for a field return (10,000×). That
single curve is *why* you "shift left": push coverage to the earliest phase that can
detect a given defect class. The catch is that "earliest phase that can detect it" is
not always "earliest phase you can introduce it" — a marginal Gen4 lane that only fails
at 85 °C *cannot* be caught at room-temperature PCBA test; it needs a thermal stress at
module test. Knowing which defects are catchable where is the core of "correct test
coverage." (Worked allocation matrix and the full 10× table: Manufacturing Test chapter,
the phases and the 10× rule.)

---

## 3. Design Verification vs Manufacturing Test

These two modes share instruments, code, and physics but differ in **goal, statistics,
and who consumes the result.** Conflating them is a classic mistake; fluently moving a
test between them is the high-leverage skill. Full treatment in the Manufacturing Test
chapter, on Design Verification vs Manufacturing Test; here is the framing you carry into
every later chapter.

> **One line.** *Design Verification (DV) asks "how good is this **design**, and where are its edges?" (measure
> everything, small N, characterize). Manufacturing Test (MT) asks "is **this unit** good enough — fast?"
> (go/no-go against limits, huge N, capture the few parameters that let you tune those
> limits later).*

| Dimension | **Design Verification** | **Manufacturing Test** |
|---|---|---|
| Question | How good is the *design*? Where are its margins? | Is *this unit* good enough — and fast? |
| Output | Characterization, margin maps, **the limits themselves** | A go/no-go verdict (+ a few captured parameters) |
| Sample size | Small N (tens to a few hundred) | Every unit, at volume |
| Method | Characterization, shmoo, margining, corner/stress sweeps | Go/no-go against fixed limits, fast |
| Time budget | Hours-days per unit acceptable | Seconds-minutes per unit (takt-bound) |
| Who consumes it | Test/Electrical Engineering (EE) engineers, the design itself | The line, quality, the safety case |
| Who runs it | Engineers in the lab | Operators on the line / at the Contract Manufacturer (CM) |

**Intent and consumers.** DV's customer is the *design* and the Electrical Engineering team: its job is to
find the design's edges and produce the data that *sets* the limits. MT's customer is
the *line, quality, and the safety case*: its job is a fast, repeatable verdict on each
unit, plus enough captured data to keep the limits honest over time. That is why limits
exist (so a unit can be judged good-enough without re-characterizing it) and why takt
time exists (so the line keeps moving — a test that overruns takt makes the station the
bottleneck and forces more capital).

**The bridge you will use constantly:** *capture the parameter, not just the verdict.*
The same measurement code that DV sweeps-and-plots to characterize a design is what MT
compares-to-a-limit for a fast pass/fail. DV uses the captured distribution to *set* a
data-driven limit; MT *checks* that limit per unit and keeps capturing, which is what
later feeds SPC, $C_{pk}$, and guard-banding. **You cannot set a good limit on data you
didn't keep** (Manufacturing Test chapter, on DV-vs-MT and on yield/economics).

---

## 4. Which Chapter Matters at Which Phase

A map so you know where to turn when you are standing at a station at a given phase. The
register-level detail is deferred to the named chapters — this table only points.

| Phase | What you're chasing | Primary chapters |
|---|---|---|
| **Printed Circuit Board Assembly (PCBA)** | Build correctness: shorts/opens, missing/wrong parts, basic boot | Manufacturing Test (In-Circuit Test (ICT)/Automated Optical Inspection (AOI)/boundary-scan, Design for Testability (DFT)); Power/Bring-Up |
| **Module** | Every interface at speed, under load, hot; firmware; calibration | PCIe (link/Advanced Error Reporting (AER)/margining); Non-Volatile Memory Express (NVMe); GPU; Memory; Automotive buses; Instruments (rails, scope, thermal forcer) |
| **System** | Inter-module links, system power/thermal budget, full-stack boot | PCIe (switch/retimer tree-walk); Networking; Power/Bring-Up |
| **Vehicle (End-of-Line (EOL))** | Real harnesses: Gigabit Multimedia Serial Link (GMSL) camera lock, sensor streaming, Controller Area Network (CAN), vehicle checks | Automotive buses (Gigabit Multimedia Serial Link/Controller Area Network/Auto-Ethernet); Networking |
| **All phases** | Limits, yield, SPC, traceability, Contract Manufacturer (CM) correlation, debug-to-root-cause | Manufacturing Test; Math & Statistics (Cpk/SPC/Gage R&R/Bit Error Rate (BER)); Bash/Linux (the debug toolbox) |

Two cross-cutting chapters underpin every phase. The **Math & Statistics** chapter holds
the derivations the discipline rests on — capability ($C_{pk}/P_{pk}$), SPC and the
Western Electric rules, Gage R&R, yield/Rolled Throughput Yield (RTY)/cost-of-test, and the Bit Error Rate/Bit Error Rate Test (BERT) confidence
math. The **Bash/Linux** chapter is the command-line debug toolbox you reach for on every
failure. The Manufacturing Test chapter (next) is the discipline itself: the phases in
depth, Design Verification (DV)-vs-Manufacturing Test (MT), limits and guard-banding, yield and economics, the tooling and
dashboards the line runs on, Contract Manufacturer correlation, traceability, bring-up vs production, the
debug-to-root-cause workflow, and the New Product Introduction (NPI)-to-mass-production ramp.
