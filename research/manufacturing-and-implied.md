---
title: "Manufacturing-Test Engineering for Complex Electronic Assemblies"
subtitle: "Research + Implied-Requirements Analysis + DV/MT Toolkit Recommendations — Zoox Compute Test Engineer"
date: "May 2026"
author: "Prepared for the Zoox Compute Test Engineer role"
---

# Purpose and scope

This document is the research backbone behind the two job-prep guides
(`guides/01-job-prep-deep-dive.md`, `guides/02-success-guide.md`) and the companion
`toolkit/`. It covers manufacturing-test engineering for complex electronic assemblies
with a deliberate emphasis on two things the Zoox JD rewards:

1. **Mass production** — the realities that only show up at volume (takt time, parallel
   test, traceability, MES, OEE, false-fail vs escape economics, CM workflows).
2. **The Design-Verification (DV) vs Manufacturing-Test (MT) distinction** — how the
   *same* engineering and even the *same toolkit* serves characterization in DV and fast
   go/no-go on the line, and why conflating them is a classic mistake.

It closes with two role-specific deliverables: **(A)** the skills/knowledge/behaviors the
JD *implies but does not state*, and **(B)** concrete recommendations to make the toolkit
serve both DV and MT.

> **One-line framing.** *DV asks "how good is this design, and where are its edges?"
> (measure everything, small N, characterize). MT asks "is this specific unit good
> enough, fast?" (go/no-go against limits, huge N, capture the few parameters that let
> you tune those limits later).* A senior compute test engineer fluently moves a test
> between these two modes.

\newpage

# 1. Test phases & methods

## 1.1 The flow, end to end

A server-grade compute assembly is tested in a sequence of phases, each operating on a
progressively more-integrated unit. The governing principle is **shift-left**: catch each
defect at the earliest (cheapest) phase that is *physically capable* of detecting it.

| Phase | Unit under test | Methods | Primary defect classes caught |
|---|---|---|---|
| **Bare board** | PCB before assembly | Flying-probe / fixture electrical test per **IPC-9252**; netlist continuity/isolation | Opens/shorts in the fabricated board, impedance/continuity faults |
| **PCBA** | Populated board off SMT | **SPI**, **AOI**, **AXI/X-ray**, **ICT**, **flying probe**, **boundary scan (JTAG)** | Solder/placement defects, wrong/missing/mis-oriented parts, hidden BGA joints, shorts/opens, component-value errors |
| **Functional / module test** | Sealed functional module (PCBA + heatsink + enclosure) | Power-on, enumeration, at-speed link test, stress + error counting, thermal soak, FW flash, calibration | Marginal links, thermal/power defects, firmware/config errors, parametric out-of-spec under load |
| **System test** | Multiple modules integrated | Inter-module link + protocol test, full-load power/thermal, system boot + stack run | Integration faults, system power-budget/thermal, inter-module link marginality |
| **EOL / vehicle test** | Compute installed in vehicle with real sensors | End-to-end sensor streaming (real GMSL harness), CAN to vehicle, vehicle-level functional checks | Real-harness/EMI faults, end-to-end functional gaps, assembly-in-vehicle errors |

## 1.2 PCBA inspection & test methods, and what each catches

These are mostly run by the contract manufacturer, but a senior test engineer must
understand their *coverage envelope* to allocate test correctly (see §1.4).

| Method | What it does | Catches | Misses |
|---|---|---|---|
| **SPI** (Solder Paste Inspection) | 3-D measure of paste deposits pre-placement | Insufficient/excess/misregistered paste (root cause of many later defects) | Anything post-reflow |
| **AOI** (Automated Optical Inspection) | Camera inspection in visible light | Missing/wrong/misplaced/tombstoned parts, polarity, bridges, gross solder defects on *visible* joints | Hidden joints (BGA/QFN), electrical function, parametrics |
| **AXI / AXI** (Automated X-ray Inspection) | X-ray of internal/hidden features | **BGA balls**, QFN thermal pads, **voids**, insufficient solder *under* packages | Electrical function; fine parametrics |
| **ICT** (In-Circuit Test) | Bed-of-nails probes nets | Shorts/opens, component values (R/L/C), orientation, some powered/digital checks; programs flash/CPLD | Defects needing at-speed operation; anything not on a test pad |
| **Flying probe** | Movable probes, no custom fixture | Same electrical class as ICT, fixture-free → ideal for **low volume / prototypes** | Throughput (slow); high-volume economics |
| **Boundary scan (JTAG, IEEE 1149.1)** | Shifts patterns through IC scan chains | **Interconnect of BGA balls you can't probe**, opens/shorts between scan-capable devices; programs flash | Non-JTAG nets; analog; function |
| **Functional / FCT** | Powers the board and exercises it | Does the assembly actually *work* at its intended function | Pinpointing *which* component (functional is "black box"); is slower/board-specific |

**Standards anchors.** Acceptability of the *assembly* (solder-joint, placement, cleanliness
criteria) is governed by **IPC-A-610** (Class 1/2/3; automotive compute is typically
Class 3 / 3A). Bare-board electrical test requirements are **IPC-9252**. Workmanship and
the visual rulebook your AOI/X-ray dispositions trace back to are IPC-A-610.
([IPC-9252 guide][ipc9252], [Jarnistech SPI/AOI/AXI/ICT][jarnis], [PCBA test handbook][elepcb])

> AOI and AXI are complementary, not redundant: **AOI catches surface-visible defects
> fast; AXI verifies hidden connections (BGA/QFN) on critical assemblies.** A GPU's BGA
> solder void is invisible to AOI and is exactly what AXI exists for. ([Jarnistech][jarnis])

## 1.3 Functional / module / system / EOL test

Above PCBA, tests become *functional* and increasingly *integrated*:

- **Functional / module test** is the heart of the compute test engineer's job: enumerate
  every device (`lspci`, `nvme list`, `nvidia-smi`), prove each link trains at the expected
  speed/width, run traffic and watch error counters (PCIe AER, GPU ECC, DRAM EDAC, NVMe
  SMART), soak at temperature, flash and verify firmware, characterize rails. A typical
  functional test runs **5–30 minutes per board** and costs on the order of **$0.10–$1 per
  unit** in test time. ([RayPCB FCT][raypcb])
- **System test** validates the *interactions* — inter-module links, the system power
  budget, system thermals — once each module has been proven in isolation.
- **EOL / vehicle test** is the final functional gate with real sensors and harnesses,
  where things that only fail with a 15 m coax run or real EMI finally surface.

## 1.4 The ~10× cost-of-escape rule (and the placement skill)

The cost to find-and-fix a defect rises by roughly an order of magnitude at each phase it
escapes to. This is the **1-10-100 rule** (Philip Crosby; later refined by ASQ): prevent
for ~$1, catch internally for ~$10, let it reach the customer for ~$100+ — and in
safety-/regulated industries the customer-stage multiplier **exceeds 1,000×** because of
recall, warranty, and regulatory exposure. ([Quality-escape / 1-10-100 calculator][copq],
[DevelopSense escape rate][developsense])

| Caught at | Rough relative cost | Why |
|---|---|---|
| Design / prevention | 1× | Fix on paper |
| PCBA | ~10× | Rework one board automatically in the line |
| Module | ~100× | Disassemble enclosure/heatsink, rework, re-test |
| System | ~1,000× | Tear down integrated system, isolate the module |
| Vehicle / field | ~10,000×+ | Pull compute from a car; truck-roll, downtime, brand/safety risk |

> **The placement skill (the senior move).** For each failure mode, find the *earliest
> phase physically capable* of detecting it, and put a test there — but **no earlier than
> the phase that can actually catch it.** A solder short dies at PCBA (ICT/AXI); a PCIe lane
> that's only marginal at 85 °C *cannot* be caught at room-temperature PCBA — it needs a
> thermal stress at module test. Knowing the coverage envelope of each method (§1.2) is
> what lets you allocate correctly. A 1% first-pass-yield improvement on a high-volume line
> is "tens of thousands of dollars per quarter." ([bestpcbs test points][bestpcbs])

\newpage

# 2. Design Verification (DV) vs Manufacturing Test (MT)

This is the distinction the JD's "support the test and validation of prototype designs"
(DV) plus "build and release test solutions for the manufacturing lines" (MT) is really
asking you to span. They share instruments, code, and physics, but their *goals,
statistics, and outputs* differ.

## 2.1 Goals and statistics side-by-side

| Dimension | **Design Verification (DV)** | **Manufacturing Test (MT)** |
|---|---|---|
| Question | "How good is this *design*? Where are its margins/edges?" | "Is *this unit* good enough — and fast?" |
| Output | Characterization data, margin maps, the *limits themselves* | A go/no-go verdict (+ a few captured parameters) |
| Sample size | Small N (EVT ~20–50; DVT ~50–500 units) | Huge N (every unit; PVT ~300–2,000 then full volume) |
| Method | **Characterization, shmoo, margining, corner/stress sweeps** | **Go/no-go against fixed limits**, fast |
| Conditions | Voltage/temp/frequency corners, worst-case combos | Nominal (+ targeted stress where a defect demands it) |
| Time budget | Hours–days per unit acceptable | Seconds–minutes per unit (takt-bound) |
| Run by | Test/EE engineers in the lab | Operators on the line / at the CM |
| Statistic | Distribution shape, design margin, $C_{pk}$ of the *design* | FPY, escape/false-fail rates, $C_{pk}$/$P_{pk}$ against limits |

The industry build-phase vocabulary that maps onto this: **EVT** (Engineering Validation,
~20–50 units, does it meet functional requirements), **DVT** (Design Verification,
~50–500 units, can it be *manufactured* to spec — heavy characterization/margining), and
**PVT** (Production Validation, ~300–2,000 units, can the *line* hit its metrics). DV work
lives in EVT/DVT; MT is what PVT proves out and mass production runs.
([EVT/DVT/PVT explained][evtdvt], [Instrumental stage gates][instrumental])

## 2.2 Characterization / shmoo / margining vs go/no-go

- **Shmoo plot.** A 2-D pass/fail map across two operating parameters (classically supply
  voltage × clock frequency), shading the region where the part works. It's a *design
  characterization* tool: it establishes that the design is stable across the process and
  "can be manufactured with virtually zero yield loss," i.e., verifies six-sigma-class
  design margin. You produce shmoos in DV; you do **not** shmoo every unit on the line.
  ([Wikipedia shmoo][shmoo_wiki], [Grokipedia shmoo][shmoo_grok],
  [OKI shmoo margin analysis][oki_shmoo])
- **Margining** is the continuous-parameter cousin: step an operating point (sampling time,
  voltage, a TX preset) until errors appear, and record *how much margin* there was. In DV
  you margin across corners to characterize the design; in MT you margin once at nominal and
  compare the margin number to a limit.
- **Go/no-go** is the MT default: run the test, compare each measured value to its
  pass/fail limit, emit PASS/FAIL. Fast, repeatable, operator-runnable.

## 2.3 The unifying idea: one toolkit, two modes (parametric capture)

The bridge — and the single most important design principle for the toolkit — is:

> **Capture the parameter, not just the verdict.** In DV you sweep and *plot* the captured
> parameter (the shmoo, the margin-vs-temperature curve). In MT you compare that same
> captured parameter to a limit for a fast pass/fail. **Same measurement code, same
> captured field; the only difference is whether you sweep-and-plot or compare-to-limit.**

Concretely, with this toolkit:

- The **BERT** measures `(errors, bits)` and a **BER upper bound**. In DV you run it across
  voltage/temperature corners and TX presets and *plot the surface*
  (`margining.characterize_equalization` does the preset sweep). In MT you run it once to a
  **confidence target** (prove BER < 1e-12 at 95% and stop) and emit pass/fail. Same engine.
- **Lane margining** yields a per-lane **timing margin in UI**. In DV you sweep it across
  temperature to characterize the eye and *set* the limit. In MT you compare the one nominal
  number to that limit. (PCIe Gen4 spec recommends ≥38% UI eye width / minimum 30% UI;
  the toolkit's default MT limit is 0.25 UI.) ([Synopsys PCIe 4.0 margining][synopsys_lmt],
  [Cadence margining Gen4→Gen6][cadence_lmt])

This is exactly why the JD pairs "capture test parameters" with "analyze results for
continuous improvement": the captured DV/MT parameter stream is what lets you set and
re-tune limits with data (§3).

\newpage

# 3. Statistics: SPC, capability, MSA, yield, limits

This is the quantitative core of "analyze results for continuous improvement" and
"improving... yield." It is also where DV-captured distributions become MT limits.

## 3.1 SPC and the Western Electric rules

**Statistical Process Control (SPC)** plots a measured parameter over time on a **control
chart** with a centerline and ±1/2/3-sigma zones, so the *process* signals drift or
instability *before* it makes scrap. A process can be **in control** (stable) yet **not
capable** (not fitting the spec) — which is why control charts are paired with capability
indices (§3.2). ([SixSigma.us SPC charts][6sigma_spc], [Lab Wizard Cp/Cpk/Pp/Ppk][labwiz_cpk])

The **Western Electric rules** are the canonical out-of-control detectors:

| Rule | Out-of-control signal |
|---|---|
| 1 | 1 point beyond the 3σ control limit (either side) |
| 2 | 2 of 3 consecutive points beyond 2σ on the same side |
| 3 | 4 of 5 consecutive points beyond 1σ on the same side |
| 4 | 8 consecutive points on the same side of the centerline |

([Western Electric rules guide][labwiz_we], [WECO rules PDF][weco_pdf])

Chart-type note: **I-MR** (individuals/moving-range) suits one-record-per-unit test data;
**X̄-R** suits subgroups; **p/np/c** suit attribute (pass/fail) data. For a per-unit test
station, I-MR on each captured parameter is the natural fit.

## 3.2 Capability: Cp/Cpk and Pp/Ppk

- **Cp** = spec width / process spread (potential capability, ignores centering).
- **Cpk** = capability accounting for *centering* relative to the nearest spec limit.
- **Pp/Ppk** are the **short-term/preliminary** analogs used *before* the process is proven
  stable (e.g., in DVT/PVT); **Cp/Cpk** are used once control charts show statistical
  stability. ([Lab Wizard][labwiz_cpk])

Acceptance bars: **Cpk ≥ 1.33** is the common "capable" bar; **Cpk ≥ 2.0** is world-class.
([Lab Wizard][labwiz_cpk]) A low Cpk means the process and the limits are too close — you
will bleed yield no matter how good the *test* is, which is itself a finding to hand back to
EE/process.

## 3.3 Measurement Systems Analysis (MSA / Gauge R&R)

Before trusting a limit, prove the **measurement system** is trustworthy. **Gauge R&R**
(Repeatability — same setup repeated; Reproducibility — across operators/stations)
quantifies how much of the observed variation is *the gauge* vs *the part*. AIAG-standard
study: **10 parts × 3 operators × 3 repeats.** Acceptance:

| %GRR (of tolerance/total variation) | Verdict |
|---|---|
| < 10% | Acceptable |
| 10–30% | Conditionally acceptable (depends on cost/criticality; customer approval often required) |
| > 30% | Unacceptable — improve the measurement system |

Also report **ndc (number of distinct categories) > 5** for an adequate system. ([SPC for
Excel MSA criteria][spcexcel_msa], [QI Macros Gage R&R][qimacros_grr]) If gauge variation is
large relative to the tolerance, your pass/fail is **noise**, and cross-CM correlation
(§5.5) is exactly a *reproducibility* study across sites.

## 3.4 Yield metrics: FPY and RTY

- **FPY (First Pass Yield)** — fraction passing the first time, with **no retest/rework**.
- **RTY (Rolled Throughput Yield)** — the product of FPY across all steps. Five steps each
  at 98% → 0.98⁵ ≈ **90%**. This is why every added test step costs yield and you don't add
  coverage casually (and why RTY, not a single-step number, is the honest line metric).

The two ways a *test program* hurts yield wrongly: **false fails** (good units failing — too-tight
limits, gauge noise, flaky tests) and **escapes** (bad units passing — too-loose limits,
missing coverage). The economics (§5.7) drive how you trade them.

## 3.5 Guard-banding and data-driven limit setting

A **guard band** tightens the *test* (acceptance) limit inside the *spec* limit by an
amount related to measurement uncertainty, so that gauge error doesn't pass a truly-bad
unit (or, with a reverse guard band, doesn't fail a truly-good one). The size of the guard
band comes from the GR&R/uncertainty (§3.3). This is the formal link between MSA and limits.

**Data-driven limit setting** is the senior workflow that ties §2–§3 together:

1. In **DV/early production**, *capture the parameter* on many units across corners.
2. Build the **distribution** (the histogram, the mean ± σ, the tails).
3. Set the MT limit from the distribution + a guard band — *not* from a guess. E.g.,
   "fleet timing margin is 0.42 ± 0.04 UI; a 0.25 UI limit gives Cpk ≈ 1.4 with margin for
   gauge error."
4. Monitor the captured parameter with **SPC** in production; re-tune when the process
   moves.

> You cannot set a good limit on data you didn't keep. This is the entire reason MT must
> **capture parameters, not just verdicts** (§2.3) — the captured stream feeds SPC, Cpk,
> guard-banding, and limit re-tuning.

\newpage

# 4. Reliability & stress: burn-in, ESS, HALT/HASS, thermal cycling

These answer "why does a unit that passes at 25 °C fail at 85 °C," and *where in the flow*
each belongs. The defining split: **HALT is a design tool (find the design's limits);
HASS/ESS/burn-in are production screens (catch weak built units).**
([NI HALT/HASS summit][ni_halthass], [Quest stress screening][qes_ess],
[Quanterion ESS profile][quanterion_ess])

| Technique | Stress | Applied to | Goal | Where in flow |
|---|---|---|---|---|
| **HALT** (Highly Accelerated Life Test) | Extreme temp + multi-axis vibration, *beyond* spec, to destruction | **Prototypes (DV)** | Find design weaknesses / operating + destruct limits | **DV / NPI**, not production |
| **HASS** (Highly Accelerated Stress Screen) | HALT-derived profile, near/just beyond operating limits | **Production units** | Precipitate latent process/assembly defects fast | Production screen (post-assembly) |
| **ESS** (Environmental Stress Screening) | Thermal cycling + vibration *within* spec limits | **Production units** | Screen out infant-mortality / workmanship escapes | Production screen |
| **Burn-in** | Steady elevated temp (often powered/under load), hours | **Production units** | Screen **infant-mortality** by accelerating early-life failures | Production (module/system) |
| **Thermal cycling** | Repeated hot↔cold ramps | Both | Surface solder-fatigue, CTE-mismatch, intermittent joints | DV (reliability) + production ESS |

## 4.1 Why "passes at 25 °C, fails at 85 °C"

Two physics reasons a senior engineer should be able to state:

1. **High-speed links lose margin with temperature.** Conductor loss and jitter rise with
   temperature, so a SerDes link (PCIe, GMSL, automotive Ethernet) that equalizes to an open
   eye at 25 °C can have a closed eye — link errors, retrains, or a speed/width fallback — at
   55–85 °C. This is why **lane margining and AER monitoring must be done hot**, not just at
   ambient. (DRAM marginality is likewise strongly temperature- and voltage-dependent.)
2. **Failure rate is Arrhenius in temperature.** Temperature-activated failure mechanisms
   follow an exponential (Arrhenius) law; the rule-of-thumb **"failure rate roughly doubles
   per ~10 °C"** is the practical consequence. Elevated-temperature life tests are processed
   *through* the Arrhenius equation to predict behavior at normal temperatures — which is the
   theory under burn-in/HASS. ([EDN temp & failure rate][edn_arrhenius],
   [JetCool temp & MTTF][jetcool_mttf])

## 4.2 Where this lands in the compute flow

- **HALT** belongs to NPI/reliability on prototypes — it tells you the design's edges and,
  crucially, *derives the HASS profile*.
- **Burn-in / ESS / HASS** belong at **module or system** test as a powered thermal (±
  vibration) soak, during which you keep monitoring the *same* counters (AER, ECC, EDAC,
  SMART) and link/throttle state. The screen *precipitates* the latent defect; your
  functional monitor *detects* it.
- The compute test engineer's contribution is the **in-soak functional monitor**: the soak
  provides the stress; your test provides the at-temperature link/error/throttle checks that
  turn "we baked it" into "we baked it and proved every interface still trains clean hot."

\newpage

# 5. Mass-production realities

This is where the JD's "manufacturing lines at Zoox and at contract manufacturing partners"
and "test deployment, test runtime, and yield" live. These are the things that don't appear
on a single bench but dominate at volume.

## 5.1 Cycle time / takt, and parallel / multi-up test

- **Takt time** is the available production time ÷ required output — the drumbeat the line
  must hit. **Test cycle time** must fit inside takt or the test station becomes the
  bottleneck (and you need more stations, i.e., more capital).
- **Parallel / multi-up / multisite test** is the primary runtime lever: test **N DUTs at
  once** on one station (multisite), and run **independent tests concurrently** on one DUT
  (e.g., NVMe `fio` soak *while* GPU stress *while* the PCIe AER monitor runs). This is how
  you amortize a fixed soak across throughput. Right-sizing soaks (the BERT **confidence
  target** instead of a padded fixed time) is the other big lever — run exactly long enough
  to prove the target and stop, with a statistical guarantee.
- Order-of-magnitude data point: a station producing one record per cycle at a **30-second
  takt** generates **~100,000 records/month** — well within ordinary database capacity, but
  it tells you the data-volume scale you're designing the results store for.
  ([Tulip MES features][tulip_mes], [AMD test data management][amd_tdm])

## 5.2 Test-station architecture

A station is a **Linux test PC** running a *versioned, released* test program (pytest +
toolkit), instrument drivers (VISA/SCPI over LAN/USB/GPIB), fixture I/O (DUT power, resets,
signal routing), and a results writer to a local store that syncs to a central DB +
dashboard. **Many identical stations run the same program version and report to the same
dashboard.** Off-the-shelf **test executives/sequencers** (NI **TestStand**, Keysight
**OpenTAP/PathWave**) provide sequencing, per-step limits, result logging, and operator UI;
the build-vs-buy decision is real, and a pytest-based harness is a valid "build" answer for a
Linux/Python shop. ([NI TestStand][ni_teststand], [NI test executive features][ni_testexec],
[Keysight PathWave][keysight_pathwave])

## 5.3 Traceability & genealogy (serial / barcode)

Every unit carries a **serial number** (1-D/2-D barcode, **Direct Part Marking**, or RFID),
scanned to start test, so each result row records *which unit, which station, which program
version, when*. **Genealogy** records which component lots/sub-assembly serials went into
each finished unit — top-down and bottom-up — so that when a bad lot or a failing test mode
is found, you can **trace every affected unit** quickly. Barcode/scan entry also removes
keystroke errors. ([Tulip MES][tulip_mes], [yieldWerx lot genealogy][yieldwerx_geneal],
[EZ-MES genealogy][ezmes_geneal]) For Zoox compute this is also a *quality* lever: e.g.,
catching re-labeled/returned NVMe stock by logging `percentage_used`/`data_units_written`
even when the unit passes.

## 5.4 Test-program release & versioning

A test program is a **released artifact**, versioned and tagged like firmware:

- Each unit's record states **which program version** tested it.
- **Config is separate from code** (limits, topology, station/CM-specific settings) so the
  same code runs at Zoox and at a CM with different fixtures — a retarget is a config change,
  not a code release + re-qualification.
- Releases have notes, are **gated by golden-unit correlation** (§5.5), and are
  **revertible** (rollback). "Deployment" is a first-class JD metric precisely because a test
  that takes a week to push to a CM is worse than a slightly-less-thorough one that deploys
  in an hour.

## 5.5 Golden / known-good units & cross-station correlation

- Keep **golden units**: characterized **known-good** *and* **known-bad** references.
- Before/after every release, prove the test still **passes the known-good and fails the
  known-bad** — this catches a too-strict (false-fail) or too-loose (escape) change *before*
  the line, not on it.
- **Cross-station / cross-site correlation**: run the *same* golden unit at Zoox and at the
  CM; the stations must agree. A correlation gap is a fixture/calibration/environment
  difference you must resolve before trusting their yield. This is operationally a **Gauge
  R&R reproducibility** study across sites (§3.3).

## 5.6 FAI / first-article, CM workflows, remote support, MES/data

- **FAI (First Article Inspection)** — the first unit(s) off a new line/process/revision get
  a thorough, documented verification before volume is released. It's the production-side gate
  that the line is set up correctly.
- **CM (contract-manufacturer) workflow** — Zoox releases test programs *to* EMS partners
  (Flex/Jabil-type) who build at volume on units you may never touch. What changes when the
  line is 2,000 miles away: **robustness is everything** (timeouts on every instrument/DUT
  call, clear failure states, graceful recovery), **logs are your eyes** (every test logs
  measured values + decoded errors + dmesg + versions, so you diagnose from the record),
  **documentation/training** (runbook, fixture spec, triage guide), and **correlation +
  golden units** as above. The **release package** is the artifact: versioned code + config,
  setup/runbook, fixture spec, acceptance criteria, golden-unit correlation data, triage
  guide.
- **MES integration** — the **Manufacturing Execution System** owns work-order release,
  electronic work instructions, serialization, **genealogy/traceability**, quality/NCR
  handling, and performance tracking (OEE). Your station typically *checks in/out* with MES
  (is this serial allowed to test here? record the result/verdict back) and streams test data
  to a **test-data-management/analytics** layer. ([Cleverence MES][cleverence_mes],
  [Tulip MES][tulip_mes])

## 5.7 OEE / throughput, and false-fail vs escape economics

- **OEE (Overall Equipment Effectiveness)** = Availability × Performance × Quality — the
  standard line/station health metric MES computes. A test station drags OEE down through
  downtime (Availability), slow cycles (Performance), and false-fails/retests (Quality).
- **The false-fail vs escape trade is an economic decision, not a moral one — except in a
  robotaxi.** A **false fail** costs throughput, retest labor, and credibility (and at a CM,
  remote firefighting). An **escape** costs the 10×-per-phase curve (§1.4) and, for
  safety-critical compute, a field/safety event whose cost is effectively unbounded.
  Therefore the standing rule is **"ship only good units"**: you do not buy yield by loosening
  a limit that lets a real defect through. You buy yield by *reducing false fails* (better
  limits via data + guard-banding, less gauge noise, less test flakiness) — never by raising
  the escape rate.

\newpage

# 6. Automotive / safety context (ISO 26262 awareness)

Zoox compute is safety-critical, so a baseline **ISO 26262** awareness is expected even
though the test engineer is not the functional-safety owner.

- **ISO 26262** is the international standard for **functional safety of automotive E/E
  systems** (2011, revised 2018). Its central concept is **ASIL** (Automotive Safety
  Integrity Level), **A (lowest) → D (highest)**. ([AUTOSAR.io ISO 26262][autosar_iso],
  [Wikipedia ISO 26262][wiki_iso])
- **Higher ASIL ⇒ higher required test coverage, mandatory traceability, and comprehensive
  documentation**, with evidence at each development layer **traceable to requirements** and
  captured as a **safety case**. Functional safety spans the whole lifecycle *including
  production release*. ([Keysight ISO 26262][keysight_iso], [LDRA ISO 26262][ldra_iso])

What this means concretely for compute manufacturing test:

- **Traceability is not optional**: unit serial → component genealogy → test program version
  → measured results → disposition must form an auditable chain (this is the §5.3/§5.4
  machinery, now with a safety rationale).
- **Coverage and limits are evidence**: which defect each test catches, and why a limit is
  set where it is, are part of the argument that the compute is safe to ship — i.e., the
  data-driven-limit discipline (§3.5) is also a safety-case input.
- **Change control**: a test-program or limit change touches the safety argument, which is
  another reason for versioned, gated, documented releases (§5.4).
- Higher-layer automotive diagnostics you'll meet at the edges: **UDS** (ISO 14229),
  **DoIP**, **SOME/IP** — awareness-level for a compute test engineer day one.

\newpage

# A. Implied (not explicitly stated) requirements of the JD

The JD is a paragraph. The *job* is much larger. Below is what a senior compute test
engineer is expected to bring that the JD only gestures at — derived from the JD text, the
two guides, and the research above. Organized by theme; each item notes the JD phrase it
hides behind.

## A.1 Lab instrumentation & physical-layer measurement

- **Bench-instrument fluency**: DMM, oscilloscope, programmable PSU, electronic load,
  DAQ/switch matrix, thermal forcer/chamber — and *what each proves on a compute board*
  (rail voltages under load, ripple/noise, power-up sequencing, inrush, PERST#/reset timing).
  *(hidden in "control test instruments")*
- **Instrument automation stack**: **VISA/SCPI** over LAN/LXI/USB/GPIB, programmatic control,
  and a shared-instrument server pattern for multi-station benches.
  *("control test instruments")*
- **Measurement discipline**: 4-wire/Kelvin for low-R and shunts, settling/averaging, fixed
  vs autorange, and **calibration/NIST traceability** (log instrument IDN + cal status with
  every measurement). *(implied by credible "capture test parameters")*
- **"Digital failures are often power/SI failures"** instinct — reaching for the **scope +
  AER decode together** to root-cause intermittent link errors. *(implied by the whole role)*

## A.2 Scripting & software engineering at scale

- Not "can write Python," but **test-framework engineering**: a config-driven harness,
  pytest fixtures/parametrize, structured result schemas, defensive coding with **timeouts on
  every instrument/DUT call**, graceful failure/recovery. *("develop testing scripts and
  code... analyze results")*
- **C/C++ for tight loops** where Python can't keep up (e.g., fast AER clear-and-count, the
  BERT inner loop). *("C++ or C# beneficial")*
- **Config-over-code** so a new board revision or a new CM is a config change, not a code
  release + re-qualification. *("release updated test programs for new generations")*
- **Version control, release engineering, rollback** for *test programs as released
  artifacts*. *("build and release test solutions")*

## A.3 Data systems, statistics & continuous improvement

- **SPC, Cpk/Ppk, GR&R/MSA, FPY/RTY, guard-banding, data-driven limit setting** —
  the statistical backbone of "improve yield." *(hidden in "analyze results for continuous
  improvement," "improving... yield")*
- **A results database + dashboards** at fleet scale; **Pareto + RCA (5-whys/fishbone)** to
  pick and close the top failure modes; closed-loop CI (baseline → change → correlate →
  deploy → measure delta). *("continuous improvement")*
- **MES/test-data-management integration** awareness (check-in/out, push verdicts/parameters).
  *(implied by "manufacturing lines")*

## A.4 Working across all four phases (and DFT influence on design)

- Understanding the **coverage envelope of PCBA methods** (AOI/AXI/ICT/JTAG/flying probe) even
  though the CM runs them, in order to **allocate coverage** across phases (the 10× placement
  skill). *("correct test coverage across all phases")*
- **DFT (Design-for-Test) influence on the design**: pushing for test pads/ICT access,
  **boundary-scan/JTAG** coverage, a scratch/ID register on custom FPGA cards, accessible
  rails, telemetry hooks — *upstream*, while EE is still laying out the board.
  *(implied by "collaborate... to develop and validate new products," "support prototype
  designs")*
- **Bring-up of custom hardware with no vendor test plan** — working from the schematic
  (root-port mapping, bifurcation, retimers, rails) to stand up coverage from scratch.
  *("custom PCIe devices," "support test and validation of prototype designs")*

## A.5 CM management & remote line support

- **Releasing test programs to contract manufacturers** and supporting them **remotely**:
  release packages, runbooks/fixture specs/triage guides, remote training, **golden-unit
  cross-site correlation**, and owning the test (and partly the yield) where you don't own the
  line. *("at contract manufacturing partners")*
- **On-call / line-down support reflex**: a CM line stop is a cross-time-zone incident;
  robustness, clear logs, and fast triage are the difference between a 10-minute issue and an
  overnight outage. *(implied by "manufacturing lines... at contract manufacturing partners")*

## A.6 Reliability/stress integration

- Knowing **where HALT/HASS/ESS/burn-in/thermal-cycling belong** and that the test
  engineer's job is the **in-soak functional monitor** (the at-temperature link/error/throttle
  checks during the screen). *(implied by "all phases," and by testing safety-critical compute)*
- The **"passes at 25 °C, fails at 85 °C"** intuition (SerDes margin loss + Arrhenius) and
  therefore **testing hot**. *(implied by credible link/thermal coverage)*

## A.7 Safety-criticality mindset & traceability

- **"Ship only good units"** as a non-negotiable: never recover yield by raising the escape
  rate on safety-critical compute. *(implied by autonomous-vehicle context)*
- **ISO 26262 awareness**: ASIL, mandatory traceability, coverage/limits as safety-case
  evidence, change control on test programs/limits. *(not in the JD at all, but inherent to a
  robotaxi compute role)*
- **Full traceability/genealogy** expectation: serial → genealogy → program version →
  parameters → disposition as an auditable chain. *(implied by "manufacturing lines" +
  safety)*

## A.8 Behavioral / seniority expectations

- **Judgment and ownership over an ambiguous mandate** ("this board needs coverage") rather
  than ticket-taking; the **coverage/runtime/yield triad** traded *consciously, with data*.
  *("work independently, manage priorities, meet critical deadlines")*
- **Evidence-based communication** (decoded, layer-identified failures EE believes; altitude-
  matched updates), **influence without authority**, **disagree-and-commit**, and **writing it
  down** so knowledge scales across sites. *(implied by "collaborate closely with EE and SW")*
- **Operator/CM-centric design**: operators and CM engineers are your *users*; an
  operator-hostile test fails in practice no matter how technically correct.
  *(implied by "manufacturing lines")*
- **Not breaking the line**: read-only-first, sandbox-before-production, golden-unit gate,
  single-variable debugging, rollback. *(implied by releasing to live production)*

\newpage

# B. Making the toolkit serve BOTH design verification AND manufacturing test

The toolkit (`toolkit/`) is already well-architected for this dual purpose: a clean
`Backend` abstraction (real Linux vs injectable mock), a confidence-target BERT, AER decode,
lane margining, an eq-preset sweep, a config-driven harness, a SQLite results store with a
**free-form `measured` dict** (parametric-capture-ready), and a FastAPI yield/heartbeat
dashboard. The recommendations below close the specific gaps that would make it credible on
*both* a DV bench and a high-volume / CM line. Each notes the file(s) to touch and the
DV and MT payoff.

## B.1 Parametric logging with limits + units (the DV/MT bridge)

**Gap.** `results.TestRecord` captures `measured` (good) but no **per-parameter limits,
units, or numeric value** alongside the verdict; pass/fail limits are hard-coded inside each
check's `_apply_limits`. Without limits *in the record*, you can't compute Cpk, guard-band,
or shmoo from the stored data.

**Recommendation.** Add a small structured **parameter** concept: each measurement logs
`{name, value, units, low_limit, high_limit, pass}`. Store these (a `parameters` table or a
JSON column) so every row carries *value + limits + verdict*.

- **DV payoff**: sweep a parameter across corners and *plot* it (shmoo / margin-vs-temp)
  straight from the store.
- **MT payoff**: the same field gives fast go/no-go *and* feeds Cpk/SPC/limit-tuning.
- **Files**: `results.py` (schema + `TestRecord`), `nvme.py`/`gpu.py`/`linkstate.py`/
  `margining.py` (emit numeric value+limit, not just a bool).

## B.2 Centralized, externalized limits / config management

**Gap.** Limits live in code (`nvme._apply_limits` `max_temp_c`, `gpu` replay `<100`,
`margining.DEFAULT_MIN_TIMING_UI`, BERT target/confidence). The JD's "config over code" and
the CM-retarget story need limits **in config**.

**Recommendation.** Extend the existing topology/plan YAML/JSON into a single **limits +
topology + station profile** config: per-device expected speed/width, per-parameter
low/high limits and units, BERT target/confidence/`max_seconds`, soak temperature, per-CM/
per-station overrides. Load once; pass limits down to the checks. (`topology.DeviceExpectation`
already does this for speed/width/`min_margin_ui` — generalize it to all parameters.)

- **DV payoff**: a "characterization" profile with wide/disabled limits to capture raw data;
  flip to a tight profile for MT — same code.
- **MT payoff**: new board rev or new CM = new config, not a code release + re-qualification.
- **Files**: `topology.py`, `harness.py`, `configs/`.

## B.3 A first-class "characterization / sweep" mode (DV)

**Gap.** `margining.characterize_equalization` does a preset sweep, but there's no general
**sweep driver** that varies a condition (voltage / temperature / preset / margining offset),
runs the existing checks at each point, and stores the parametric surface. DV *is* sweeping.

**Recommendation.** Add a `characterize()` / sweep harness: given an axis (e.g., temperature
via a thermal-forcer instrument, or TX preset, or supply voltage via PSU) and the checks to
run, iterate, capture parameters at each point, and emit a **shmoo/margin table**. Reuse the
BERT and margining as the per-point measurement.

- **DV payoff**: turnkey shmoo and margin-vs-temperature curves; *sets* the limits B.2/B.1
  then enforce.
- **MT payoff**: none directly — but it's how the *limits MT uses* get produced, closing the
  DV→MT loop.
- **Files**: new `characterize.py`; reuse `bert.py`, `margining.py`, instrument drivers (B.7).

## B.4 Parallel / multi-DUT execution (MT runtime)

**Gap.** `harness.run_test_plan` runs strictly **serially** over one DUT's devices; the
README and `bert.run_many` note "the harness parallelizes" but it does not yet. Volume test
needs concurrency.

**Recommendation.** (a) **Intra-DUT concurrency**: run independent long checks concurrently
(NVMe `fio` soak ∥ GPU stress ∥ PCIe AER monitor) via threads/async, since they're
I/O-bound subprocess/sysfs waits. (b) **Multi-DUT / multisite**: parametrize the harness over
a list of DUTs (each its own serial + slot) so one station drives N units — the natural
**multisite** model. pytest-xdist is the test-side analog.

- **DV payoff**: faster corner sweeps.
- **MT payoff**: directly attacks takt/cycle-time and station count — the JD's "test runtime."
- **Files**: `harness.py` (concurrency + per-DUT loop), `results.py` (already keys on
  `dut_serial` — make it per-record, see B.5).

## B.5 Traceability fields per record (serial / station / version / lot / operator / temp)

**Gap.** `ResultStore` fixes `station`/`dut_serial`/`program_version` **at construction**, so
all rows in a session share one serial — wrong for a multi-DUT station, and missing
genealogy/operator/conditions entirely.

**Recommendation.** Move `dut_serial` (and `slot`, `operator`, `program_version`,
`fixture_id`, `temperature_c`, `site`/`cm`, `seq`/retest-count, per-test `duration_s`) onto
the **per-record** path, and add a **component-genealogy** field (component lots/sub-serials).
Populate `dut_serial`/`operator` from a **barcode scan** at test start.

- **DV payoff**: tag each datapoint with its exact conditions (temp, unit) for analysis.
- **MT payoff**: the §5.3/§6 traceability/genealogy chain and per-station/CM correlation;
  also required for the safety case.
- **Files**: `results.py` (schema + `record()` signature), `harness.py` (thread serial/
  conditions through), `cli.py` (scan-to-start).

## B.6 SPC / Cpk / yield export (CI + limit-tuning)

**Gap.** The store has `summary()`, `yield_by_test()` (great start) but **no parametric
export** (CSV/Parquet), no Cpk/Ppk, no control-chart/SPC view, no per-station/CM/time
filtering, no Pareto of failure reasons.

**Recommendation.** Add (a) an **export** (`to_csv`/Parquet of parameters+limits) for
offline analysis (JMP/pandas); (b) a **capability** helper computing Cpk/Ppk per parameter
from stored values+limits; (c) a **Pareto** query over failure `message`/`reasons`; (d)
dashboard panels for **parameter trend + control limits (I-MR)**, **Cpk**, **Pareto**, and a
**per-station/CM filter** (the cross-site correlation view).

- **DV payoff**: capability of the *design*; export feeds limit-setting.
- **MT payoff**: the literal "analyze results for continuous improvement" + yield engine.
- **Files**: `results.py` (export + capability + pareto queries), `dashboard/app.py`
  (panels + filters).

## B.7 Instrument-control layer (VISA/SCPI) for rails, power, temperature

**Gap.** The toolkit reads *on-DUT* telemetry well (PCIe/NVMe/GPU/EDAC) but has **no
external-instrument layer** — yet the JD names "control test instruments," and rail
voltage/ripple/sequencing + thermal forcing are core compute-board measurements (and the
axis B.3's sweep needs).

**Recommendation.** Add a thin **instrument abstraction** mirroring `Backend`: a real
`pyvisa`/SCPI backend (DMM/PSU/eload/thermal-forcer) and a **mock instrument** for laptop
demos/tests. Log instrument **IDN + cal status** with every measurement (B.1 record).

- **DV payoff**: rail/ripple/sequencing characterization; drives the temperature/voltage
  sweep axis.
- **MT payoff**: rail-under-load and power-sequencing pass/fail at module test; cal-traceable
  records.
- **Files**: new `instruments.py` (+ `MockInstrument`), wired into `harness.py`/`characterize.py`.

## B.8 Station / operator UX & robustness (CM-ready MT)

**Gap.** The CLI is engineer-facing; there's no operator flow (big PASS/FAIL, scan-to-start,
unambiguous states, recovery), and not every DUT/subprocess call is wrapped in a **timeout**
(critical for unattended CM lines).

**Recommendation.** (a) An **operator mode** (or a minimal station UI): barcode scan →
auto-select config → big PASS/FAIL → on fail, show the decoded top reason + log location →
recover cleanly. (b) **Timeouts + clear failure states** on every instrument/DUT/subprocess
call (the BERT loop and interface checks). (c) Bundle a **CM release package** generator
(versioned code+config, runbook, golden-unit correlation report, triage guide).

- **DV payoff**: minimal (DV is engineer-run) — but the robustness helps unattended sweeps.
- **MT payoff**: this is what makes it deployable on a Zoox line and at a CM you can't walk to.
- **Files**: `cli.py` (operator subcommand), `bert.py`/`nvme.py`/`gpu.py`/`ethernet.py`
  (timeouts), new `release.py` (package generator).

## B.9 Real lane-margining + hot-margining path (coverage upgrade)

**Gap.** `margining._real_margin_lane` and the real eq sweep are `NotImplementedError`
(rightly guarded), so the strongest DV/MT differentiator (per-lane eye margin) is mock-only.
There are now reference implementations to wire to (Linux `pcilmr`, Google `pcie_lmt`,
Oxide `lmar`). ([pcilmr man page][pcilmr_man], [google/pcie_lmt][google_lmt],
[oxide/lmar][oxide_lmar])

**Recommendation.** Wire the real margining path to one of those references behind the
existing guard, and add **margining-during-thermal-soak** (margin *hot*, §4.1).

- **DV payoff**: real eye-margin-vs-temperature characterization → the data-driven UI limit.
- **MT payoff**: per-lane margin go/no-go at temperature catches marginal-but-passing links
  that "trains at Gen4 x16" would miss — a coverage upgrade over pass-on-link-up.
- **Files**: `margining.py`, `characterize.py` (B.3), thermal instrument (B.7).

## B.10 Summary: the dual-purpose principle in code

| Toolkit element | DV use (characterize) | MT use (go/no-go) |
|---|---|---|
| BERT (`bert.py`) | Run across corners/presets, plot BER surface | Confidence-target, pass/fail, stop early |
| Margining (`margining.py`) | Margin vs temperature → set the UI limit | Compare nominal margin to limit |
| Parameters + limits (B.1/B.2) | Capture wide; plot distributions | Compare to tight limits; feed Cpk/SPC |
| Sweep harness (B.3) | The DV engine (shmoo/margin curves) | Produces the limits MT enforces |
| Results store + export (B.6) | Distributions, capability of the design | FPY/RTY, SPC, Pareto, limit-tuning |
| Parallel harness (B.4) | Faster sweeps | Takt/cycle-time, multisite throughput |
| Traceability (B.5) | Tag datapoints with conditions | Genealogy chain, correlation, safety case |
| Instruments (B.7) | Rail/temp/voltage sweep axes | Rail-under-load pass/fail, cal-traceable |

> The single principle behind all ten: **measure once, capture the parameter + its limits,
> then either sweep-and-plot it (DV) or compare-it-to-limit (MT).** Everything else is
> plumbing to make that work at volume, across sites, traceably, and fast.

\newpage

# Sources

**PCBA test methods & standards**

- IPC-9252 bare-board electrical test — PCBSync: [ipc9252][ipc9252]
- SPI/AOI/AXI/ICT inspection & test — Jarnistech: [jarnis][jarnis]
- PCBA testing handbook (types, defects) — Elepcb: [elepcb][elepcb]
- Functional test (FCT) cost/time — RayPCB: [raypcb][raypcb]
- Test-point ROI / FPY economics — bestpcbs: [bestpcbs][bestpcbs]

**Cost of escape / 1-10-100**

- Quality-escape / 1-10-100 (COPQ) calculator — simulations4all: [copq][copq]
- Defect escape rate — DevelopSense: [developsense][developsense]

**DV vs MT / characterization / shmoo / build phases**

- EVT/DVT/PVT explained — EW Mfg: [evtdvt][evtdvt]
- EVT/DVT/PVT stage gates — Instrumental: [instrumental][instrumental]
- Shmoo plot — Wikipedia: [shmoo_wiki][shmoo_wiki]; Grokipedia: [shmoo_grok][shmoo_grok]
- Shmoo margin analysis — OKI Engineering: [oki_shmoo][oki_shmoo]
- PCIe 4.0 lane margining benefits — Synopsys: [synopsys_lmt][synopsys_lmt]
- PCIe lane margining Gen4→Gen6 — Cadence: [cadence_lmt][cadence_lmt]

**Statistics / SPC / capability / MSA**

- SPC charts (types/rules) — SixSigma.us: [6sigma_spc][6sigma_spc]
- Cp/Cpk/Pp/Ppk — Lab Wizard: [labwiz_cpk][labwiz_cpk]
- Western Electric rules — Lab Wizard: [labwiz_we][labwiz_we]; WECO PDF: [weco_pdf][weco_pdf]
- MSA / Gauge R&R acceptance criteria — SPC for Excel: [spcexcel_msa][spcexcel_msa]
- AIAG Gage R&R study design — QI Macros: [qimacros_grr][qimacros_grr]

**Reliability / stress**

- HALT/HASS implementation — NI: [ni_halthass][ni_halthass]
- Stress screening (HALT/HASS/ESS) — Quest Engineering: [qes_ess][qes_ess]
- ESS profile selection — Quanterion: [quanterion_ess][quanterion_ess]
- Temperature & failure rate (Arrhenius) — EDN: [edn_arrhenius][edn_arrhenius]
- Temperature & MTTF — JetCool: [jetcool_mttf][jetcool_mttf]

**Mass-production / MES / test executive**

- MES core features (genealogy/OEE/serialization) — Tulip: [tulip_mes][tulip_mes]
- MES definition/architecture — Cleverence: [cleverence_mes][cleverence_mes]
- Lot genealogy / traceability — yieldWerx: [yieldwerx_geneal][yieldwerx_geneal]; EZ-MES: [ezmes_geneal][ezmes_geneal]
- Test data management & analytics — AMD Machines: [amd_tdm][amd_tdm]
- NI TestStand — NI: [ni_teststand][ni_teststand]; test-executive features — NI: [ni_testexec][ni_testexec]
- Keysight PathWave Test Automation: [keysight_pathwave][keysight_pathwave]

**Lane-margining reference implementations**

- pcilmr (pciutils) man page: [pcilmr_man][pcilmr_man]
- google/pcie_lmt: [google_lmt][google_lmt]
- oxide/lmar: [oxide_lmar][oxide_lmar]

**ISO 26262 / functional safety**

- ISO 26262 guide — AUTOSAR.io: [autosar_iso][autosar_iso]
- ISO 26262 — Wikipedia: [wiki_iso][wiki_iso]
- ISO 26262 compliance — Keysight: [keysight_iso][keysight_iso]
- ISO 26262 & ASILs — LDRA: [ldra_iso][ldra_iso]

[ipc9252]: https://pcbsync.com/ipc-9252/
[jarnis]: https://www.jarnistech.com/quality-assurance/pcba-inspection-and-testing-spi-aoi-axi-ict
[elepcb]: https://www.elepcb.com/blog/pcba-test/
[raypcb]: https://www.raypcb.com/functional-testing-of-pcb-assembly-and-pcba-fct-costs
[bestpcbs]: https://www.bestpcbs.com/blog/2026/05/circuit-board-test-points/
[copq]: https://simulations4all.com/simulations/quality-escape-cost-estimator
[developsense]: https://developsense.com/defect-escape-rate
[evtdvt]: https://news.ewmfg.com/blog/evt-dvt-pvt-explained
[instrumental]: https://instrumental.com/build-better-handbook/evt-dvt-pvt
[shmoo_wiki]: https://en.wikipedia.org/wiki/Shmoo_plot
[shmoo_grok]: https://grokipedia.com/page/Shmoo_plot
[oki_shmoo]: https://www.oeg.co.jp/en/semicon/shmoo.html
[synopsys_lmt]: https://www.synopsys.com/designware-ip/technical-bulletin/pci-express-4-lane-margining.html
[cadence_lmt]: https://www.chipestimate.com/PCIe-Lane-Margining-What-changed-from-Gen4-to-Gen6/Cadence/blogs/3698
[6sigma_spc]: https://www.6sigma.us/six-sigma-in-focus/spc-charts/
[labwiz_cpk]: https://lab-wizard.com/en/resources/knowledge/understanding-spc-parameters/
[labwiz_we]: https://lab-wizard.com/en/resources/knowledge/spc-western-electric-rules/
[weco_pdf]: https://www.scribd.com/document/485351193/Western-Electric-Company-SPC-OOC-Rules
[spcexcel_msa]: https://www.spcforexcel.com/knowledge/measurement-systems-analysis-gage-rr/acceptance-criteria-for-msa/
[qimacros_grr]: https://www.qimacros.com/gage-r-and-r-study/aiag-msa-gage-r-and-r/
[ni_halthass]: https://www.ni.com/pdf/testsummit/us/Stress
[qes_ess]: https://qes.com/stress-screening/
[quanterion_ess]: https://www.quanterion.com/environmental-stress-screening-basic-steps-in-choosing-an-ess-profile/
[edn_arrhenius]: https://www.edn.com/the-effect-of-temperature-on-failure-rate/
[jetcool_mttf]: https://jetcool.com/post/semiconductor-lifetime-how-temperature-affects-mean-time-to-failure-device-reliability/
[tulip_mes]: https://tulip.co/blog/core-features-of-mes-manufacturing-execution-systems/
[cleverence_mes]: https://www.cleverence.com/articles/for-business/what-is-a-manufacturing-execution-system-4829/
[yieldwerx_geneal]: https://yieldwerx.com/blog/semiconductor-traceability-using-lot-genealogy-for-multi-chip-modules/
[ezmes_geneal]: https://eazyworks.com/features-product-tracking-and-genealogy
[amd_tdm]: https://amdmachines.com/blog/test-data-management-and-analytics/
[ni_teststand]: https://www.ni.com/en/shop/electronic-test-instrumentation/application-software-for-electronic-test-and-instrumentation-category/what-is-teststand.html
[ni_testexec]: https://www.ni.com/content/dam/web/scene7/white-papers/22/06_Test%20Executive%20Software_LTR_EN_WR.pdf
[keysight_pathwave]: https://www.keysight.com/us/en/products/software/pathwave-test-software/pathwave-test-automation-software.html
[pcilmr_man]: https://man7.org/linux/man-pages/man8/pcilmr.8.html
[google_lmt]: https://github.com/google/pcie_lmt/blob/master/README.md
[oxide_lmar]: https://github.com/oxidecomputer/lmar
[autosar_iso]: https://autosar.io/en/insights/iso26262-guide
[wiki_iso]: https://en.wikipedia.org/wiki/ISO_26262
[keysight_iso]: https://www.keysight.com/blogs/en/tech/sim-des/achieve-compliance-with-iso-26262-functional-safety-standards
[ldra_iso]: https://ldra.com/iso-26262/
