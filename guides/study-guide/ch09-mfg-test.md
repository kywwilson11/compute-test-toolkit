This is the discipline that the rest of the guide serves. The interface chapters teach
you *how* to make a measurement (read Advanced Error Reporting (AER), margin a lane, count Error-Correcting Code (ECC) errors); this
chapter is *why* you make it, *where* you make it, *what limit* you judge it against,
and *how* you turn the captured numbers into yield, root cause, and a test program you
can release to a contract manufacturer and trust.

Manufacturing test exists to answer one question on every unit: **"Is this unit good
enough to ship?"** Every decision in the chapter — phase placement, limit setting, soak
duration, parallelization, correlation — is a balance of three forces: **coverage**
(catch the defect), **time** (hit takt / throughput), and **cost** (equipment, labor,
floor space, and the cost of being wrong). The expert skill is holding all three at once.

> The math behind everything quantitative here — capability ($C_{pk}/P_{pk}$), Statistical Process Control (SPC) and
> the Western Electric rules, Gage R&R, yield (First Pass Yield (FPY)/Rolled Throughput Yield (RTY)), cost-of-test, and the Bit Error Rate Test (BERT)
> confidence formulas — is *derived* in the **Math & Statistics chapter**. This chapter
> uses those results to make decisions; it points there for the derivations rather than
> repeating them.

---

## 1. The Four Phases, In Depth

The phases are **Printed Circuit Board Assembly (PCBA) → module → system → vehicle**, and the organizing rule is **place
each test at the earliest phase that can catch its defect.** Each phase tests a
different *thing*, owned by a different group, proving a different property.

### 1.1 PCBA — "was it built correctly?"

The unit is a bare board just off the Surface-Mount Technology (SMT) line: right components, good solder, no
shorts/opens, powers on, reaches a basic boot. Mostly run **at the Contract Manufacturer (CM)**, mostly not your
code — but you must understand it, because a defect that escapes here costs 10× more at
the next phase, and because the *coverage gaps* here are what your module test must
cover.

- **Automated Optical Inspection (AOI)** — cameras check for missing / misplaced / wrong
  / tombstoned parts and gross solder defects. Fast, before anything is powered.
- **In-Circuit Test (ICT)** — a bed-of-nails fixture probes nets to measure R/C, find
  shorts and opens, and verify component values. The workhorse PCBA test for
  high-volume, *fixtured* boards.
- **Boundary scan / JTAG** (IEEE 1149.1) — shifts test patterns through device scan
  chains to test interconnects you cannot physically probe (BGA balls under the package)
  and to program flash/CPLD. Often the *first* test on a new board, before you try to
  boot it. (See Design for Testability (DFT), §1.5.)
- **Flying probe** — ICT without a custom fixture, for low volume and prototypes (slower,
  no fixture cost).
- **X-ray (AXI)** — sees solder voids and bridges *under* BGAs that AOI cannot. The only
  way to catch a void under the GPU or a large connector at PCBA.
- **First power-on / boot** — does it come up, draw the right current, reach a prompt.
  Often programs the initial bootloader/firmware here.

### 1.2 Module — "does every interface work, at speed, under load, hot?"

The unit is now a sealed, functional module *with its thermal solution*. **This is the
heart of your job.** Almost every interface test in the guide runs here:

- **Enumerate everything** — `lspci`, `nvme list`, `nvidia-smi`, NIC/CAN/GMSL presence.
- **Prove each link trains at the *expected* speed and width** — PCIe Gen4 x16, Non-Volatile Memory Express (NVMe)
  Gen4 x4, etc. Trained-below-max is your first Signal Integrity (SI) finding (PCIe chapter).
- **Stress + error counting** — drive traffic and watch AER / SMART / ECC / Error Detection and Correction (EDAC) counters
  (the BERT and diagnostic tools live here; PCIe/NVMe/GPU/Memory chapters).
- **Thermal / burn-in** — soak at temperature and/or under load, re-check links and
  errors. **This is the phase that catches marginal-at-temperature defects PCBA
  structurally cannot** (§7).
- **Power characterization** — measure rail voltages/currents under load with a DMM/scope
  (Instruments chapter); out-of-window-under-load is a power-delivery defect and a common
  root cause of link errors.
- **Firmware / version verification** — flash and confirm firmware/Board Support Package (BSP) versions; record
  them for traceability (§6).
- **Calibration** where applicable.

### 1.3 System — "do the modules work *together*?"

Modules are integrated into the compute box/rack. You now test the *interactions*:
inter-module PCIe/Ethernet links, the system power budget under full load, system-level
thermals (fans, airflow, the whole box), and that the integrated system boots and runs
the stack. Failures here are expensive to localize — which is exactly why module test
should have already proven each module *in isolation*. If you are debugging "which of
six modules is marginal" at system test, the right fix is usually upstream: tighten the
module-phase coverage so it never gets here.

### 1.4 Vehicle (End-of-Line (EOL) — End Of Line) — "does it work in the car, end to end?"

Compute is installed in the vehicle with real sensors. Cameras must lock over *real* GMSL
harnesses (15 m of coax, real connectors, real Electromagnetic Interference (EMI)), all sensors stream, CAN talks to
the vehicle bus, and the vehicle-level functional checks pass before it ships. **This is
where channel-marginal SerDes defects surface** — a GMSL link that locks on a short bench
cable can drop over a full harness at temperature, the GMSL version of the PCIe "passes
at 25 °C" escape (Automotive buses chapter). You cannot move this coverage earlier
because the *real channel* only exists at the vehicle.

### 1.5 Design for Testability (DFT) — earning the coverage before the board exists

DFT means designing the hardware so it *can* be tested effectively in manufacturing. If
DFT is neglected, you get boards that work but can't be verified — and you discover the
defects in the field instead of the factory. As the test engineer you push for DFT *in
design reviews*, before there is a board to test. What you ask for:

- **Test points** on critical signals — power rails (voltage + ripple), clocks
  (frequency + amplitude), high-speed SerDes (scope access for debug). Physically
  reachable by bed-of-nails / flying probe, ≥25 mil pad, not buried under a heatsink.
- **JTAG / boundary scan** accessible on the production fixture — interconnect test +
  BIST + flash programming.
- **Loopback paths** — PCIe / Ethernet / UART loopback to test a full signal path
  (serializer → driver → receiver → deserializer) *without* a second device, saving
  fixture cost and test time.
- **BIST** (Built-In Self-Test) — memory BIST (walking-ones, checkerboard patterns) and
  logic BIST run *at full speed* and cover internal paths external tests cannot reach.
- **On-board identity (EEPROM/flash)** — serial number, MAC, board revision, manufacturing
  date, test results, calibration. Travels with the board through its lifecycle; the
  anchor for traceability (§6).
- **Debug interfaces** — a UART console that works during boot (the #1 bring-up tool),
  JTAG header, I2C/SPI taps — accessible on *production* boards for field-return failure
  analysis.

**DFT review checklist (carry this into the review):** Can every power rail be measured
without board mods? Is JTAG reachable on the production fixture? Can every high-speed
link run in loopback? Is there a boot-time UART console? Can board identity + test
history live on-board? Can firmware be updated without special equipment? Each "no" is a
coverage gap or a debug blind spot you will pay for at volume.

---

## 2. The 10× Rule and Test-Coverage Allocation

The cost to find and fix a defect rises ~10× at each phase it escapes to. This single
curve drives the entire test strategy.

| Caught at | Rough relative cost | Why |
|---|---|---|
| PCBA | 1x | Rework a single board in the line, often automatically |
| Module | 10x | Disassemble enclosure/heatsink, rework, re-test |
| System | 100x | Tear down an integrated system, isolate which module |
| Vehicle | 1000x | Pull compute from a vehicle, diagnose in situ |
| Field (RMA) | 10,000x+ | Truck roll, downtime, brand/safety risk |

The economic version of the same curve (the classic semiconductor framing): wafer test
~\$1, package test ~\$10, board test ~\$100, system test ~\$1,000, field ~\$10,000+. The
numbers are illustrative; the *order of magnitude per phase* is the durable point.

**The allocation skill.** When Electrical Engineering (EE) hands you a new board, the expert question for *each
way it can fail* is: what is the cheapest phase that can catch this, and what test
detects it there? You build a small matrix:

| Failure mode | Catchable at | Test |
|---|---|---|
| Missing/wrong component | PCBA | AOI / ICT |
| BGA solder void under the GPU | PCBA (X-ray) + Module (thermal cycling surfaces it) | AXI; thermal soak + link-error monitor |
| PCIe lane marginal at temperature | Module (not PCBA) | Stress + AER + lane margining, hot and cold |
| NVMe throttles under sustained write | Module | `fio` soak + SMART temperature/throttle log |
| GMSL won't lock over full harness | Vehicle (real harness) | Link-lock + frame capture at End-of-Line (EOL) |
| Inter-module link marginal | System | System link enumeration + stress |

The expensive mistakes are defects that are only *detectable* late but were
*introducible* early — a workmanship defect on a SerDes trace that doesn't show until a
hot system soak. You will not get this matrix perfect on day one. Thinking in it is the
point: **test coverage is a placement problem.**

---

## 3. Design Verification vs Manufacturing Test, In Depth

Design Verification (DV) and Manufacturing Test (MT) share instruments, code, and physics but differ in goal, statistics, and
consumer. (The framing is introduced in the Platform chapter, on DV vs MT; this is the
working depth.)

| Dimension | **DV** | **MT** |
|---|---|---|
| Question | How good is the *design*? Where are its margins/edges? | Is *this unit* good enough — and fast? |
| Output | Characterization data, margin maps, **the limits themselves** | A go/no-go verdict (+ a few captured parameters) |
| Sample size | Small N (EVT ~20-50; DVT ~50-500) | Every unit (PVT ~300-2,000, then full volume) |
| Method | Characterization, shmoo, margining, corner/stress sweeps | Go/no-go against fixed limits, fast |
| Conditions | Voltage/temp/frequency corners, worst-case combos | Nominal (+ targeted stress where a defect demands it) |
| Time budget | Hours-days per unit acceptable | Seconds-minutes per unit (takt-bound) |
| Run by | Test/EE engineers in the lab | Operators on the line / at the CM |
| Statistic | Distribution shape, design margin, $C_{pk}$ of the *design* | FPY, escape/false-fail rates, $C_{pk}/P_{pk}$ vs limits |

**The build-phase vocabulary maps onto this.** **EVT** (Engineering Validation, ~20-50
units, "does it meet functional requirements") and **DVT** (DV, ~50-500
units, "can it be *manufactured* to spec" — heavy characterization/margining) are DV
work. **PVT** (Production Validation, ~300-2,000 units, "can the *line* hit its metrics")
is where MT is proven out before mass production runs it.

### 3.1 Shmoo, margining, go/no-go

- A **shmoo plot** is a 2-D pass/fail map across two operating parameters (classically
  supply voltage × clock frequency), shading where the part works. It is a *design
  characterization* tool — it shows the design is stable across process so it "can be
  manufactured with virtually zero yield loss." You produce shmoos in **DV**; you do
  **not** shmoo every unit on the line.
- **Margining** is the continuous-parameter cousin: step an operating point (sampling
  time/voltage, a Transmit (TX) preset) until errors appear and record *how much margin* there was.
  In DV you margin across corners to characterize; in MT you margin once at nominal and
  compare to a limit. PCIe **lane margining** (PCIe chapter) is exactly this.
- **Go/no-go** is the MT default: run, compare each measured value to its limit, emit
  PASS/FAIL. Fast, repeatable, operator-runnable.

### 3.2 The unifying idea — capture the parameter, not just the verdict

This is the single most important design principle for your test code:

> **Capture the parameter, not just the verdict.** In DV you sweep and *plot* the captured
> parameter (the shmoo, the margin-vs-temperature curve). In MT you compare that *same*
> captured parameter to a limit for a fast pass/fail. **Same measurement code, same
> captured field; the only difference is whether you sweep-and-plot (DV) or compare-to-
> limit (MT).**

Concretely:

- A **BERT** measures `(errors, bits)` → a Bit Error Rate (BER) upper bound. **DV:** run it across
  voltage/temperature corners and Transmit (TX) presets and *plot the surface*. **MT:** run it once
  to a confidence target (prove BER < 1e-12 at 95% and stop) and emit pass/fail. Same
  engine. (Confidence math: Math chapter, the BER/BERT confidence section.)
- **Lane margining** yields a per-lane **timing margin in UI**. **DV:** sweep it across
  temperature to characterize the eye and *set* the limit. **MT:** compare the one nominal
  number to that limit.

The lane-margin number is the bridge: DV uses it to *set* a data-driven per-lane eye
limit; MT uses it to *check* that limit per unit — replacing "pass on link-up" with a
margin number. That is why MT must capture parameters: the captured stream is what later
feeds SPC, $C_{pk}$, and guard-banding (§4). **You cannot set a good limit on data you
didn't keep.**

---

## 4. Test Limits — Guard-Banding and Cpk-Driven Limits

A test limit is a number that turns a measurement into a verdict. Setting it well is
where false-fails and escapes are won or lost. The derivations (capability indices,
PPM-from-$C_{pk}$, the normal tables) are in the **Math chapter**, in the capability
section; here is the *engineering* of it.

### 4.1 Guard-banding — tighten the test limit inside the spec limit

The spec limit is the customer/design requirement (e.g., "rail must stay ≥ 11.4 V under
load"). Your **measurement is not perfect** — it has gauge uncertainty (§5.2; Gage R&R
derivation in the Math chapter). If you test directly at the spec limit, gauge error will
pass units that
are truly out of spec (an escape) and fail units that are truly in spec (a false fail).
A **guard band** moves the *test* limit *inside* the *spec* limit by an amount tied to
that uncertainty:

```text
test_limit = spec_limit - guard_band
guard_band ~= k * sigma_gauge     # k from the confidence you need (often ~2-3)
```

So a 11.4 V spec floor with a 50 mV gauge uncertainty might get a 11.55 V *test* floor.
The guard band trades a little yield (some good-but-marginal units fail) for protection
against the escape — and in a safety product that trade is correct (§8). The guard band
comes *out of the Gage R&R study* (§5.2): you cannot pick `k * sigma_gauge` until you
have measured `sigma_gauge`.

### 4.2 Cpk-driven, data-driven limits

Do not pull a limit from a guess or a datasheet round number. Set it from the *measured
fleet distribution*, then check that the limit gives an acceptable capability:

1. **Capture the parameter on many units across corners (DV).** This is the EVT/DVT
   characterization data.
2. **Build the distribution.** Mean, spread, shape, tails.
3. **Set the MT limit from the distribution + a guard band.** Place it so the process
   sits comfortably inside it — i.e. so $C_{pk}$ is healthy. *Example phrasing you will
   actually write in a limit-justification doc:* "fleet timing margin is 0.42 ± 0.04 UI;
   a 0.25 UI limit gives $C_{pk} \approx 1.4$ with room for gauge error."
4. **Monitor with SPC and re-tune when the process moves.**

The capability bars you target (Math chapter, capability section): **$C_{pk} \ge 1.33$** is the industry
"capable" floor (~32 PPM one-sided), **≥ 1.67** is strong, **≥ 2.0** is world-class. A
**low $C_{pk}$ is itself a finding**, not a limit problem: it means the process spread
and the spec are too close, and you will bleed yield *no matter how good the test is* —
hand that back to EE/process as "the design margin is too tight," not "loosen the limit."
Note also $P_p/P_{pk}$ vs $C_p/C_{pk}$: use the long-term $P_{pk}$ in DVT/PVT *before* the
process is proven stable, $C_{pk}$ once control charts show stability (Math chapter,
capability section).

---

## 5. Per-Unit Data, Traceability, and SPC

Every unit a station tests must leave a record richer than PASS/FAIL. That record is the
raw material for limit-setting, yield analysis, SPC, Return Merchandise Authorization (RMA) root-cause, and the safety case.

### 5.1 Traceability and genealogy

Every unit carries a **serial number** (1-D/2-D barcode, Direct Part Marking, or RFID),
scanned to *start* the test — which both removes keystroke error and stamps each result
row with **which unit, which station, which test-program version, when**. **Genealogy**
records which component lots and sub-assembly serials went into each finished unit, so
when a bad lot or a failing test mode appears you can trace *every affected unit* quickly
(top-down: "which units got lot X"; bottom-up: "what went into this failing unit"). For
Zoox compute this is also a *quality* lever — it is how logging a NVMe drive's
`power_on_hours` / `data_units_written` catches re-labeled or returned stock entering the
line even when the unit otherwise passes (NVMe chapter). And it is a **safety-case
requirement**: serial → genealogy → program version → measured results → disposition must
be an auditable chain (functional-safety chapter).

The structured result a station emits per unit:

```text
station_id, dut_serial, test_program_version, operator, timestamp,
  per_test: { name, measured_value, limit_low, limit_high, result },
  artifacts_on_failure: { decoded_AER, dmesg_snippet, margin_matrix, ... }
```

A test that records only PASS/FAIL throws away the data you need for SPC and
limit-setting — and forces the next engineer to *reproduce* a failure to diagnose it
instead of reading its attached evidence.

### 5.2 Measurement-system trust — Gage R&R (MSA)

Before you trust *any* limit, prove the *measurement system*. **Gage R&R** decomposes
observed variation into the gauge versus the part:

- **Repeatability** — same operator, same part, same equipment, repeated. Variation =
  equipment noise.
- **Reproducibility** — different operators/stations, same part. Variation =
  operator/station-to-station.

The AIAG study is **10 parts × 3 operators × 3 repeats** (90 measurements). Acceptance:
**%GRR < 10%** good, **10-30%** conditional, **> 30%** unacceptable (and `ndc > 5`). If
gauge variation is large relative to the tolerance, *your pass/fail is noise.* This is
where `sigma_gauge` for the guard band (§4.1) comes from, and — crucially — **cross-CM
correlation is a reproducibility study across sites** (§5.4). (Derivation and the
variance math: Math chapter, Gage R&R section.)

### 5.3 SPC — the process talks to you before it makes scrap

Capability ($C_{pk}$) is a snapshot; **SPC** is the movie. Plot each captured parameter
over time with center line and ±1/2/3σ zones. For per-unit test data the natural chart is
the **I-MR** (individuals / moving-range) pair. The control limits are the *process's
own* voice (mean ± 3σ), **not** the spec limits — a key distinction:

> A process can be **in control yet not capable** (stable but too wide for the spec), or
> **capable yet out of control** (fits the spec today but drifting). Control charts and
> capability are a pair; you need both.

The **Western Electric rules** flag special-cause variation *before* it becomes scrap
(Math chapter, Western Electric rules): 1 point beyond 3σ; 2 of 3 beyond 2σ (same side); 4 of 5 beyond 1σ
(same side); 8 in a row on one side; 6 in a row trending. **Reading the yield chart:** a
*sudden* drop says process change, equipment failure, or bad incoming material; a
*gradual* decline says drift — tool calibration, fixture wear. The chart points; Root Cause Analysis (RCA) (§9)
finds the cause.

### 5.4 Cross-site correlation with golden units

Keep **golden units**: characterized **known-good** *and* **known-bad** references. Two
uses:

- **Release gate.** Before and after every release, prove the test still **passes the
  known-good and fails the known-bad**. This catches a too-tight (false-fail) or too-loose
  (escape) change *before* the line, not on it.
- **Cross-station / cross-site correlation.** Run the *same* golden unit at Zoox and at
  the CM; the stations must agree. A correlation gap is a fixture / calibration /
  environment difference you must resolve before you trust their yield numbers.
  Operationally this *is* a Gage R&R reproducibility study across sites (§5.2).

---

## 6. Yield and Test Economics

These are the three numbers a test engineer is judged on, and the JD names them: **test
deployment, test runtime, and yield.** (Yield/RTY/throughput/cost-of-test derivations:
Math chapter, yield and cost-of-test.)

### 6.1 Yield

- **FPY** — fraction passing the *first* time, no retest/rework. The
  primary KPI.
- **RTY** — product of FPY across all steps. Five steps each at
  98% → `0.98^5 ~= 90%`. **This is why every added test step costs yield**, and why you
  do not add coverage casually.
- **Pareto analysis** — bar chart of failure modes by frequency with a cumulative line.
  80% of failures come from ~20% of causes; **always attack the tallest bar first** (§9).

A test program hurts yield in two opposite ways: **false fails** (good units failing —
gauge noise, too-tight limits, flaky tests) and **escapes** (bad units passing —
too-loose limits, missing coverage). The whole limit/guard-band discipline (§4) is about
minimizing both.

### 6.2 Runtime — test time is money

Test time sets line throughput and station count. **Takt time** = available production
time ÷ required output is the drumbeat the line must hit; **your test's cycle time must
fit inside takt** or the station becomes the bottleneck and you need more stations (more
capital). Scale intuition: a station producing one record per cycle at a 30 s takt
generates ~100,000 records/month — that sets the data-volume scale you design the results
store for. The levers:

- **Parallelize.** Run independent tests concurrently — NVMe `fio` soak ∥ GPU `gpu-burn`
  ∥ PCIe AER monitor — and test **N DUTs at once** on one station (multisite / multi-up).
  This is how you amortize a fixed soak across throughput.
- **Right-size soaks.** The **BERT confidence target** is an *economic* tool: run exactly
  long enough to prove 1e-12 at 95% confidence and **stop**, not a padded fixed duration
  (Math chapter, BER/BERT confidence). Choosing a confidence level instead of a wall-clock time is a
  runtime optimization *with a statistical guarantee*.
- **Adaptive testing.** If a unit passes a quick screen, skip the extended version.
- **Move tests left** — run a test at the cheapest phase that still catches its defect
  (balanced against the 10× escape cost; never move a test earlier than the phase that
  can *catch* the defect).
- **Cut tests that never catch anything.** If the fleet data shows a 30 s test has caught
  zero real defects across 10,000 units, it is a candidate to drop or sample. The data
  tells you which tests earn their runtime.

### 6.3 The false-fail vs escape trade — asymmetric here

Normally false-fail vs escape is a pure economic optimization. **In a robotaxi it is
asymmetric.** A **false fail** costs throughput, retest labor, and (at a CM) remote
firefighting. An **escape** costs the 10×-per-phase curve *and*, for safety-critical
compute, a field/safety event whose cost is effectively unbounded. So the standing rule
is **"ship only good units":** you do **not** buy yield by loosening a limit that lets a
real defect through. You buy yield by *reducing false fails* — better limits via data +
guard-banding, less gauge noise, less test flakiness — **never** by raising the escape
rate.

### 6.4 Deployment

How fast and how reliably a new/updated test reaches every station and CM. Levers: config
over code (no code change to retarget a board revision or a CM), versioned releases with
rollback, golden-unit correlation gating a rollout, remote station update, and clear
release notes. A test that takes a week to deploy to a CM is operationally *worse* than a
slightly-less-thorough one that deploys in an hour — which is why deployment is a
first-class metric, not an afterthought.

---

## 7. Bring-Up vs Production

The same hardware passes through two very different regimes, and the test engineer works
both.

### 7.1 Board bring-up (a DV-adjacent activity)

When a new board revision arrives, you do a structured bring-up *before* production test
development begins. The order is "fail fast, cheap first":

1. **Visual inspection** — obvious assembly defects (missing parts, solder bridges, wrong
   orientation); verify the BOM matches the design.
2. **Power-on (the scary part)** — apply power with a **current-limited supply** and watch
   the current draw: a short pulls max current instantly. Verify every rail comes up to
   spec; check for components getting hot (thermal camera / touch test).
3. **Boot to console** — UART debug console; watch boot messages (BIOS POST, kernel,
   login). Each line that scrolls is another subsystem that came up; if it stops, the
   *last* message tells you what failed.
4. **Device enumeration** — `lspci -vvv`, `lsusb`, `ip link show`, `dmesg | grep -i error`;
   compare against the expected device list from the schematic.
5. **Basic functional** — each subsystem individually: GPU `nvidia-smi`, NVMe `nvme list`,
   NIC `ethtool`, memory `free -h` / `dmidecode --type memory`, CAN a test frame.
6. **Characterization** — stress, thermals, PCIe equalization, power under load. **This
   data informs the production test limits** — bring-up is where the DV characterization
   that *sets* limits (§4) happens.

For *custom* PCIe cards (the JD's "custom PCIe devices") you work from the schematic:
which root port feeds the slot, is the slot **bifurcated** (a top cause of
"device-not-detected" when BIOS bifurcation doesn't match layout), where are the
retimers, which rails feed the PHY. Bring-up is where you and EE are closest; your job is
to produce evidence sharp enough that a layout or stuffing fix is *obvious* (PCIe chapter
has the full bring-up checklist).

### 7.2 Production

The unit is no longer a question, it is a verdict. The test is locked, versioned,
operator-run, takt-bound, and measured on FPY; the properties that matter flip from
"deep and exploratory" to "fast, robust, repeatable, operator-proof, correlated across
stations." The *measurements* are often the same code as bring-up — the difference is
sweep-and-characterize (bring-up) vs compare-to-limit-and-move-on (production): the DV→MT
transition (§3) made concrete in the life of one board.

---

## 8. The Debug-to-Root-Cause Workflow

When a unit fails — or worse, when *yield* drops — you need a method, not a hunch. The
goal is always to get from a **symptom** to a **physical root cause** you can hand to EE,
process, or the supply chain as an actionable correction.

### 8.1 The reflex (single-unit failure)

```text
failure
  -> enumerate        # is it even there?  lspci / nvme list / nvidia-smi / ip link
  -> dmesg            # what did the kernel see?  link-down, AER, XID, training
  -> counters         # arm -> stress -> read   (AER / SMART / ECC / EDAC / TEC-REC)
  -> isolate          # swap / reseat / known-good unit / known-good slot
  -> measure          # scope the rail, DMM the current, thermal-force it
  -> decode           # which AER bit / XID code / DIMM label -> the layer -> the part
```

The meta-skill is not memorizing flags; it is the *reflex* and the discipline of **arming
counters before you measure** (clear → stress → read) so you count errors from *your*
stress window, not from boot. The interface chapters supply the decode tables (which AER
bit means which layer; which XID code means which GPU subsystem; which Dual Inline Memory Module (DIMM) label a CE
maps to). Many "digital" failures are really **power / SI** failures wearing
a digital costume — the engineer who reaches for the scope *and* the AER decode together
is the one who closes the hard intermittent bugs (Instruments / Power chapters).
### 8.2 Structured RCA (yield drop / repeated failure)

When the problem is a *population*, not one unit, use a structured method so you find the
true cause, not the first plausible one:

**Fishbone (Ishikawa) — categorize candidate causes** (the 6 M's):

| Category | Examples |
|---|---|
| **Man** (operator) | Training gap, wrong procedure, skipped step |
| **Machine** (equipment) | Fixture failure, instrument drift, cable wear |
| **Material** | Bad component lot, wrong revision, incoming quality |
| **Method** (process) | Procedure error, wrong test limit, missing test |
| **Measurement** | Instrument accuracy, Gage R&R failure, wrong probe point |
| **Environment** | Temperature, humidity, vibration, ESD |

**5 Whys — drill from symptom to root cause.** A worked example:

1. Why did the GPU fail thermal test? → Temperature exceeded 90 °C.
2. Why did it exceed 90 °C? → Heatsink thermal resistance was too high.
3. Why was thermal resistance too high? → Insufficient thermal-paste coverage.
4. Why was coverage insufficient? → The stencil aperture was undersized.
5. Why was the aperture undersized? → The stencil spec wasn't updated for the new
   heatsink design.

*Root cause:* stencil specification not updated. *Fix:* update the stencil spec, add
incoming inspection for paste coverage. Notice the symptom ("GPU too hot") and the root
cause ("a document wasn't updated") are five steps apart — stopping at "bad heatsink"
would have treated a symptom.

**Pareto + 80/20 — attack the tallest bar first.** When you have a list of failure modes,
compute the cumulative percentage and fix the top contributors for the biggest yield gain
per engineering hour:

| Failure mode | Count | % | Cumulative % |
|---|---|---|---|
| PCIe link degraded | 45 | 36% | 36% |
| NVMe not detected | 25 | 20% | 56% |
| GPU ECC error | 15 | 12% | 68% |
| Thermal throttle | 12 | 10% | 78% |
| NIC link down | 8 | 6% | 84% |
| All others | 20 | 16% | 100% |

Fixing the top two here (PCIe + NVMe = 56% of all failures) is where the leverage is.
**Always verify the fix moved the bar** — re-Pareto after the corrective action; if the
top bar didn't shrink, you fixed a symptom, not the cause.

---

## 9. Reliability and Stress Screening

"Passes at 25 °C, fails at 85 °C" is the defining manufacturing-test reality, and it is
why a thermal forcer / chamber is on the bench. Two physics facts to keep in hand:

1. **High-speed links lose margin with temperature.** Conductor loss and jitter rise with
   temperature, so a SerDes link (PCIe, GMSL, automotive Ethernet) that equalizes to an
   open eye at 25 °C can have a *closed* eye — errors, retrains, or a speed/width fallback
   — at 55-85 °C. **This is why lane margining and AER/ECC monitoring must be done hot**,
   not just at ambient, and why module-phase burn-in exists.
2. **Failure rate is Arrhenius in temperature** — temperature-activated mechanisms follow
   an exponential law; rule of thumb, **failure rate roughly doubles per ~10 °C**.
   Elevated-temperature life tests are processed *through* the Arrhenius equation to
   predict normal-temperature behavior. This is the theory under burn-in and the
   infant-mortality (left) side of the **bathtub curve** (Math chapter, reliability): early-life
   failures are screened by a powered, elevated-temperature soak so they happen *in the
   factory*, not in a vehicle.

The stress techniques and — the part people get wrong — **where each belongs** (HALT is a
*design* tool; HASS/ESS/burn-in are *production* screens):

| Technique | Stress | Applied to | Where in flow |
|---|---|---|---|
| **HALT** (Highly Accelerated Life Test) | Temp + multi-axis vibration *beyond* spec, to destruction | Prototypes (DV) | NPI / reliability — finds the design's limits and *derives the HASS profile* |
| **HASS** (Highly Accelerated Stress Screen) | HALT-derived profile, near/just beyond operating limits | Production units | Production screen (post-assembly) |
| **ESS** (Environmental Stress Screening) | Thermal cycling + vibration *within* spec | Production units | Production screen — infant-mortality / workmanship escapes |
| **Burn-in** | Steady elevated temp, powered / under load, hours | Production units | Module/system — screens infant mortality |
| **Thermal cycling** | Repeated hot<->cold ramps | Both | DV reliability + production ESS — solder-fatigue / CTE-mismatch |

**Your contribution to every one of these is the in-soak functional monitor.** The screen
*precipitates* the latent defect (the oven/shaker supplies the stress); *your* test
supplies the **at-temperature link/error/throttle checks** — margin the lanes hot, watch
the AER/ECC/EDAC/SMART/XID deltas vs temperature — that turn "we baked it" into "we baked
it *and proved every interface still trains clean hot*." A thermal test that never
actually gets the part hot, or a soak with no functional monitor running during it, is a
test that cannot catch the defect it exists for.

**Electrostatic Discharge (ESD) discipline** belongs here too: a fixture without proper ESD grounding makes *you*
the failure mechanism — a board can pass test with latent ESD damage and die in the field.
Wrist-strap monitors, dissipative mats, controlled humidity, ESD-safe fixture contacts;
verify the strap monitors work on every station (Power/Safety chapter).

---

## 10. The CM Relationship: NPI to Mass-Production Ramp

Contract Manufacturers (CM) (EMS partners — Flex/Jabil-type) build at volume on units you may
never physically touch. Releasing a test program *to* a CM and supporting it remotely is a
large part of the job, and it spans the whole product life cycle: **New Product Introduction (NPI) → sustaining.**

### 10.1 NPI (New Product Introduction)

Test development, **first-article testing**, yield-target establishment. You work closely
with the CM to validate that your test coverage works on *real* boards built on *their*
line with *their* fixtures — not just on the golden unit in your lab. **FAI (First Article
Inspection)** is the production-side gate: the first unit(s) off a new line/process/revision
get a thorough, documented verification before volume is released, proving the line is set
up correctly.

### 10.2 Sustaining

Ongoing test maintenance, yield improvement, test-time reduction, handling field returns,
and updating tests for board revisions. The recurring ritual is the **yield meeting**:
review the CM's (CM) yield data, Pareto the failures (§9), trend-analyze, and assign corrective
actions. This is where the captured per-unit data (§5) and the Pareto/RCA discipline earn
their keep against a partner you cannot stand next to.

### 10.3 The release package

A test program is a **released artifact**, versioned and tagged like firmware (the
Bash/Linux chapter covers the git mechanics). What you hand a CM:

- **Versioned code + config** — config separate from code so the *same* code runs at Zoox
  and at the CM with different fixtures.
- **Setup / runbook** — how to provision a station, connect the fixture, run the program.
- **Fixture specification** — what hardware the station needs.
- **Acceptance criteria** — the limits and what each test proves.
- **Golden-unit correlation data** — the reference results their station must match (§5.4).
- **Triage guide** — symptom → likely cause → first moves, so the CM can self-serve common
  failures instead of escalating every one.

Robustness and clear logs matter *ten times more* when you cannot walk over to the station.
A failure must arrive with its evidence already attached (§5.1) so you can diagnose a
board in another country from the result row.

### 10.4 MES / OEE — the system the line runs on

The CM's (CM) floor runs on a **MES (Manufacturing Execution System)** that owns work-order
release, electronic work instructions, serialization, genealogy/traceability,
quality/NCR handling, and **OEE** (Overall Equipment Effectiveness = Availability ×
Performance × Quality). Your station typically **checks in/out** with MES (is this serial
allowed to test here? record the verdict back) and streams parameters to a
test-data-management/analytics layer. Note how your three metrics map onto OEE: a station
drags OEE down through **downtime** (Availability), **slow cycles** (Performance), and
**false-fails/retests** (Quality). The factory-automation/SCADA layer you may meet here
(Ignition, OPC-UA, PLC data) is the same distributed pattern as any station-dashboard
system: stations POST status to a central server, structured data lands in a SQL
database, a web frontend renders real-time yield/throughput/station-status dashboards.

**The MES check-in/out handshake** is worth making concrete, because it is the contract
between your station code and the factory:

```text
1. operator scans DUT serial
2. station -> MES: "may serial SN123 run test-program PCBA_v4.2 at station S07?"
3. MES -> station: ALLOW  (correct routing, not already passed, work order open)
                   or DENY (wrong step / already shipped / lot on hold / rework loop)
4. station runs the program
5. station -> MES: verdict + per-test results + program version + timestamp
6. MES advances the unit's route state (or routes a FAIL to rework/quarantine)
```

That handshake is what enforces **route control** (a unit cannot skip a station or be
tested out of order) and what makes the per-unit record auditable. If your station does
not check in, a unit can be re-tested until it passes by luck — the classic "test until
pass" escape that route control exists to kill.

**Why traceability is not optional here.** For automotive compute the genealogy chain is
a *compliance* requirement, not just a debugging convenience. Two standards drive it:

- **IATF 16949** (the automotive quality-management standard, built on ISO 9001) requires
  a traceability system that can identify product lots and tie them to manufacturing
  records, so a nonconforming population can be **contained** — i.e. given a bad component
  lot you can name every finished unit that received it, and given a failing unit you can
  name everything that went into it.
- **ISO 26262** (functional safety) requires end-to-end traceability across the safety
  lifecycle — each safety requirement linked through design, implementation, and
  verification. At the manufacturing layer this shows up as the demand that *every* unit's
  serial → genealogy → test-program version → measured results → disposition is an
  auditable, durable chain. A "we think it passed" with no record is a safety-case hole.

Practically: this is why the per-unit record (§5.1) must be **immutable and retained for
years** (often the vehicle's service life plus a margin), why test-program versions are
captured on every row (a result is meaningless without knowing which limits produced it),
and why "test until pass" is forbidden. The infrastructure that stores all this — the
parametric warehouse, the dashboards, the analytics — is the subject of the next two
sections.

---

## 11. The Test Station and Its Instruments

Everything above assumes a *station*: the physical + software assembly that holds a DUT,
applies stimulus, measures, and emits a verdict. Knowing the layers of a station — and
which instrument makes which measurement — is what lets you turn "test the board" into a
concrete bench.

### 11.1 The anatomy of a station

A production test station is a stack you can name top to bottom:

```text
operator UI / barcode scanner        # start-on-scan, PASS/FAIL light, retest control
   |
test executive (sequencer)           # runs steps, applies limits, logs results
   |
test code (Python / C / drivers)     # the measurement logic per step
   |
instrument layer (the bench)         # PSU, DMM, scope, BERT, thermal forcer, switch
   |  (controlled over GPIB / USB / LAN-VISA / PCIe / serial)
fixture / DUT carrier                # power, signal, thermal, ESD contact to the DUT
   |
DUT (the unit under test)
```

The **fixture** is where most station bugs live: a worn pogo pin, a marginal connector,
a ground loop, or an ESD-grounding gap turns into "intermittent fails" that look like a
DUT problem. When a station's yield drops with no design change, suspect the fixture
before the boards (it is a *Machine* cause on the fishbone, §8.2) and prove it with a
**golden unit** (§5.4): if the golden unit now fails or shifts, the station moved, not the
DUTs.

### 11.2 Instrument control — how the code talks to the bench

Bench instruments are almost universally driven over **SCPI** (Standard Commands for
Programmable Instruments — ASCII command strings like `MEAS:VOLT:DC?`) carried on a
transport: **GPIB/IEEE-488** (legacy but everywhere), **USB-TMC**, **LAN/LXI** (often via
**VISA** or raw sockets), or RS-232. In Python the common stack is **PyVISA** (or a
vendor SDK) wrapping VISA; the pattern is the same regardless of instrument:

```python
# open -> configure -> trigger -> read -> close, with explicit ranges and timeouts
import pyvisa
rm = pyvisa.ResourceManager()
dmm = rm.open_resource("TCPIP::192.168.1.50::INSTR")
dmm.timeout = 5000  # ms; a hung instrument must not hang the line
dmm.write("CONF:VOLT:DC 20,0.001")     # fixed range + resolution => repeatable, fast
rail_v = float(dmm.query("READ?"))
```

Two production rules that separate a robust station from a flaky one: **never leave an
instrument on autorange in production** (autorange re-hunts each reading — slow and
non-repeatable; fix the range from the expected value), and **always set a timeout and
handle the dead-instrument case** (a GPIB hang with no timeout stops the whole line).

### 11.3 Which instrument makes which measurement

| Instrument | Measures | Where it shows up in this guide |
|---|---|---|
| **Programmable PSU / electronic load** | Supply the DUT; sweep/limit V and I; sink current to load a rail | Bring-up power-on; power-under-load characterization |
| **DMM (6.5-digit)** | DC rail voltage, current (shunt), resistance | Rail-in-window checks, ICT-style continuity |
| **Oscilloscope** | Time-domain: ripple, rise/fall, clocks, glitches, eye diagrams (with the right probe/SW) | SI debug, ripple-vs-AER correlation, clock integrity |
| **BERT / built-in eye+margining** | Bit-error ratio and eye/timing margin on a SerDes lane | The PCIe/GMSL margining story |
| **Protocol analyzer/exerciser** | Decoded PCIe/CAN/Ethernet/USB traffic, inject + capture | Link bring-up, CAN bus-off, packet-level faults |
| **Thermal forcer / chamber** | Force the DUT (or a part) to a set temperature | The "passes at 25 C, fails at 85 C" screen |
| **Thermal/IR camera** | Surface temperature map; find the hot part | Power-on "what's getting hot," heatsink coverage |
| **Switch / multiplexer matrix** | Route one instrument to many nets or many DUTs | Multisite stations, sharing a costly instrument |
| **Power analyzer / DAQ** | Many channels of V/I/temp logged over a soak | Burn-in monitoring, power budgets |

The deep how-to for each (probing, bandwidth, eye reading, thermal-forcer setup) lives in
the **Instruments / Power** chapter; the point here is the *mapping* — a measurement
implies an instrument, and a station's bill of materials is just that mapping made
physical. A recurring failure-analysis move (§8.1) is reaching for the **scope + the
protocol decode together**: many "digital" failures are a power or SI
problem wearing a digital costume, and you only see it when the rail trace and the error
counter are on the same screen.

---

## 12. The Manufacturing-Test Tooling Landscape

A working test engineer does not write everything from scratch; you assemble a *stack* of
tools, and a large part of the job is knowing which layer each tool belongs to and where
the boundaries are. The mistakes here are category errors — running deep stats in the
wrong tool, or letting a Continuous Integration (CI) server think it is a production sequencer. The layers, top to
bottom:

```text
  yield analytics / SPC / deep stats     yieldWerx, PDF Exensio, JMP, notebooks
        ^   (reads the parametric warehouse, not the line directly)
        |
  dashboards / monitoring                Grafana
        ^
        |
  parametric data store + format         Postgres/TimescaleDB, a warehouse; STDF on ATE
        ^
        |
  MES + traceability/genealogy           route control, serialization, OEE
        ^
        |
  test executive (sequencer)             NI TestStand  -or-  custom Python sequencer
        ^
        |
  test code + instrument drivers         your measurement logic

  --- separate lifecycle, NOT in the per-unit path ---
  CI for the test *software*             GitLab CI / GitHub Actions / Jenkins
```

### 12.1 The test executive (sequencer) — buy vs build

The **test executive** is the layer that owns *sequencing*: run steps in order, branch on
results, apply limits, handle retries, log a structured result, and present an operator
UI. You either buy it or build it.

- **NI TestStand** is the dominant commercial test executive. It gives you sequencing,
  built-in limit evaluation, parallel/multi-UUT models (run N DUTs at once on one
  station), operator interfaces, and report/result generation (HTML/XML/ATML/ASCII, or a
  database) for free. It calls test code written in Python, C/C++, .NET, or LabVIEW — so
  "TestStand vs Python" is a false binary; the common pattern is **TestStand sequencing
  with Python step modules**. It is heavily used on automotive End-of-Line (EOL) lines (e.g. ECU End-of-Line
  testers commonly pair LabVIEW/TestStand for sequencing and reporting). The cost is
  licensing and a degree of lock-in.
- **A custom Python sequencer** trades that out-of-the-box machinery for full control and
  no license cost. You get to own the data model, the result schema, and the deployment
  story — but you must *build* the parts TestStand gives you: looping/branching/retry
  logic, parallel-UUT execution, the operator UI, and result logging. For a Python-first
  shop testing custom hardware (custom PCIe cards with no vendor test plan), this is often
  the chosen path precisely because the flexibility matters more than the prebuilt
  sequencer — `pytest` is sometimes bent into this role for its fixtures and parametrize,
  though `pytest` is a *developer* test runner and a production line wants an operator UI,
  hard takt behavior, and an immutable result record on top.

The decision is the classic build-vs-buy: buy when your need is standard and you value
time-to-line and vendor support; build when your hardware is unusual, your team is
software-strong, and you need to own the whole pipeline. Either way the executive's job is
the same — and either way it must emit the rich per-unit record of §5.1, not just a
PASS/FAIL.

### 12.2 Parametric data formats — STDF and the warehouse

The captured parameters have to land somewhere with a schema, or they are not analyzable
later. Two worlds meet here:

- **STDF (Standard Test Data Format)** is the semiconductor industry's near-universal
  *binary* format for ATE (Automated Test Equipment) results — it stores parametric,
  functional, and datalog records (part records, test results with limits, bin results)
  and is what wafer/package test equipment emits. If Zoox compute touches die/package-level
  test data from a silicon vendor, or runs any ATE-style station, STDF is the lingua
  franca, and every yield-analytics tool ingests it. For board/module functional test you
  more often emit your *own* structured record (§5.1) into a relational/time-series store
  rather than STDF, but you should recognize STDF on sight and know it is parseable
  (open-source and vendor parsers exist).
- **A parametric warehouse** is where per-unit records accumulate for analysis:
  commonly **Postgres** (with **TimescaleDB** when the access pattern is time-series:
  station heartbeats, yield-over-time, soak telemetry), a column store / data-lake table
  for large parametric history, and **Prometheus** for short-retention operational metrics
  (station up/down, cycle time, queue depth). The shape that matters: **one row per
  (unit, test, parameter)** with limits attached, so any later question — "show the timing-
  margin distribution for last week's lot," "is rail ripple drifting on station 7" — is a
  query, not a re-test. (This is the data-volume scale §6.2 sizes: a 30 s-takt station is
  ~100k records/month, ×N stations ×many parameters.)

### 12.3 Yield analytics and deep statistics

Above the warehouse sit the tools that turn stored parameters into yield decisions. These
read the warehouse; they are **not** in the per-unit test path.

- **yieldWerx** and **PDF Solutions Exensio** are commercial yield-management /
  test-data-analytics platforms (Exensio is the bigger, fab-oriented one, with a stated
  automotive-semiconductor push; both ingest STDF and other formats). They provide
  automated SPC, parametric outlier/bin rules, wafer-map and cross-lot correlation, and
  the alerting that flags a yield signature before it becomes scrap. A board/module shop
  may not run a full fab-grade platform, but the *capabilities* — automated SPC on every
  parameter, outlier detection, cross-site correlation — are the target, whether bought or
  built on the warehouse + Grafana + notebooks.
- **JMP** (from SAS) is the analyst's bench for *deep* statistics — the tool you open to
  do the work the dashboard cannot: a real Gage R&R study (§5.2), a DOE to find why a
  parameter drifts, distribution fitting and capability analysis, a regression to correlate
  a failure with a process variable. Grafana answers "is something wrong, now"; JMP (or a
  Python/`pandas`+`statsmodels` notebook) answers "*why*, with statistical rigor." They are
  complementary: monitoring is continuous and shallow, JMP is occasional and deep.

### 12.4 Version control for test programs

A test program is not a script you run once — it is a **released, versioned artifact** that deploys to multiple identical stations, at Zoox and at contract manufacturers you may never physically visit, and decides whether a safety-critical unit ships. Three forces make version control stricter here than in ordinary application development:

1. **Traceability (a safety-case requirement).** Every result row a station writes must record *which program version* produced it. When a field issue or a bad lot surfaces, you trace serial → genealogy → test-program version → measured results → disposition. If "the test version" is "whatever was on the laptop that day," that chain is broken and the safety argument collapses.
2. **Reproducibility across sites.** Many identical stations run the same version and report to the same dashboard. A yield difference between Zoox and a CM must be a *fixture/calibration* difference, not a *code* difference — which you can only assert if you can prove both ran the same tagged commit.
3. **Deployment and rollback are first-class.** "Push v2.4.1 to all stations, then revert to v2.4.0 if FPY drops" must be a one-command operation. A clean tag-and-release process is what makes that safe.

**Daily workflow.** Review before you stage — `git add -p` forces a hunk-by-hunk pass that stops a stray debug print or a hardcoded station IP from shipping.

```
git status                  # working-tree state: staged / unstaged / untracked
git diff / git diff --staged
git add -p                  # interactively stage hunks, REVIEWING each change
git commit -m "Raise NVMe fw-activate reset wait to 10s; Micron 7450 needs ~8s to re-enumerate"
git push origin feature/gmsl-timeout
```

Write commit messages a CM engineer can use at 2 a.m. during a line-down: *what changed and why the value is what it is.* "Fix bug" is useless; the message above is a debugging document.

**Branching and review discipline** for a manufacturing-test repo:

| Element | Practice | Why |
|---|---|---|
| **main** | always deployable to production | a station can be re-provisioned from `main` to a known-good state at any time |
| **feature branches** | one per driver, board revision, or test change | isolates in-progress work from the deployable tip |
| **pull requests** | review before merge | a second set of eyes on a change that can scrap good units or pass bad ones |
| **CI** | `pytest` + lint on every PR | a red gate blocks merge (see the next section) |
| **golden-unit gate** | re-run known-good + known-bad references before release | catches a too-tight (false-fail) or too-loose (escape) change *before* the line, not on it |
| **tags** | mark each version deployed to the line | the traceability anchor |

**Config over code.** Limits, bus/topology maps, station IDs, and CM-specific fixture settings live in versioned *config*, not in the Python. A retarget to a new CM or board revision is then a config change, not a code release that re-qualifies the whole program.

**Tags turn a commit into a release.** Prefer annotated tags (they carry tagger, date, and message, and are what you sign):

```
git tag -a v2.4.0 -m "Release 2.4.0: add MAX96712 quad-cam config, hot-margin path"
git push origin v2.4.0
git describe --tags         # "v2.4.0-3-gA1B2C3D" -- embed in every result row so even an
                            # unreleased dev build is uniquely identifiable
```

Semantic versioning maps cleanly onto test programs: **MAJOR** = incompatible change (new limit schema, dropped test, new result format the dashboard must understand), **MINOR** = added coverage or board config (backward compatible), **PATCH** = bug fix or limit re-tune within the same schema. The shipped artifact is more than code: versioned code + config + runbook + fixture spec + acceptance criteria + golden-unit correlation data + triage guide, with the git tag as the spine that proves what was shipped.

**Recovery and forensics:**

```
git log --oneline --graph        # branch/merge history at a glance
git blame limits.yaml            # who set this limit, when, in which commit
git revert <hash>                # NEW commit that undoes <hash> -- SAFE on shared branches
                                 # (does not rewrite history); how you roll back a bad release
git bisect start                 # binary-search the commit that introduced a regression
```

**Rule of thumb:** never `reset --hard` or force-push a branch a station or CM might be pulling from — use `revert` for shared history. Rewriting history is fine only on a private feature branch you have not shared.

---

### 12.5 CI for the test *software* — a separate lifecycle

This is the category error to avoid, so it gets its own callout. **Continuous Integration (CI — GitLab CI, GitHub Actions, Jenkins)** belongs to the *software development
lifecycle of the test code*, **not** to per-unit production execution. The test program is
a released artifact (§10.3); Continuous Integration is what builds, unit-tests, lints, packages, and versions
that artifact when you push a change — and ideally runs it against a **golden unit** on a
hardware-in-the-loop runner as a release gate (§5.4) before it is allowed to ship to a
station. What CI does **not** do is run on every DUT on the line: the **test executive**
(§12.1) does that, takt-bound, on the factory floor. Jenkins building your test program
nightly is correct; Jenkins being asked to test 10,000 units per the takt clock is a
category error. Keep the two mental models separate:

| | Test executive (TestStand / custom) | CI (Jenkins / GitLab CI / Actions) |
|---|---|---|
| Runs | Per DUT, on the line, at takt | Per code change / nightly |
| Triggers | Operator scans a serial | A git push / a schedule |
| Output | A per-unit PASS/FAIL + parametric record | A built, tested, versioned test-program release |
| Lives on | The station / factory floor | A build server |
| Hardware | The fixture + bench + DUT | Usually none, or one golden-unit HIL runner |

---

## 13. Dashboards and Grafana for Manufacturing Test

You cannot manage a line you cannot see. Once stations emit the per-unit record (§5.1)
into a store (§12.2), the **dashboard** is how the data becomes a live picture of fleet
health — and **Grafana** is the de-facto open-source tool for it. This section is concrete
because "we'll put up a dashboard" is where a lot of test-data value is won or lost.

### 13.1 What Grafana is, and exactly where it sits

Grafana is an open-source visualization-and-alerting front end that queries one or more
data sources and renders panels (time series, stat tiles, tables, bar/Pareto, heatmaps)
into dashboards, with an alerting engine on top. It **stores no data itself** — it sits on
top of whatever you already write results into. Place it precisely:

```text
station -> result record -> data store -> Grafana (read-only views + alerts)
                                  ^
                  MES owns route control & genealogy;
                  Grafana visualizes; it does NOT gate a unit.
```

The boundary that matters: **Grafana is observation, not control.** The **test executive**
(§12.1) decides PASS/FAIL on a unit; the **MES** (§10.4) decides whether a unit may
proceed; **Grafana** tells *humans* how the line and the fleet are doing so they can
intervene. A Grafana panel never passes or fails a board — confusing the dashboard with
the gate is a real mistake. It is the "web frontend renders real-time dashboards" layer
§10.4 names, made specific.

### 13.2 The data sources it sits on

Grafana speaks to several backends at once, and a real MFG-test setup uses more than one:

- **Postgres / TimescaleDB** — the primary parametric + result store. TimescaleDB (a
  Postgres extension) is the natural home for the *time-series* views (yield-over-time,
  station cycle time, soak telemetry) because it gives time-bucketing, continuous
  aggregates (pre-rolled-up summaries for fast long-range queries), and per-metric
  retention. Most yield/parametric panels are plain SQL against this.
- **Prometheus** — short-retention *operational* metrics scraped from stations: station
  up/down, cycle-time gauges, queue depth, instrument errors. Prometheus is for "is the
  line healthy right now"; it is not where you keep a year of parametric history (that is
  the warehouse). The Prometheus/Grafana pairing is the standard operational-monitoring
  stack.
- **A parametric warehouse / data lake** — for deep historical parametric queries across
  millions of units, sometimes fronted by its own SQL engine.
- **Loki (logs)** and occasionally other sources — to pull a failing unit's `dmesg`/log
  snippet next to its result row.

The practical pattern: **TimescaleDB/Postgres for parametric + yield, Prometheus for live
station ops, one Grafana** stitching them into role-specific dashboards.

### 13.3 The panels that actually matter

A useful MFG-test Grafana deployment is usually a few focused dashboards, not one giant
wall. The panels that earn their place:

- **Live fleet & per-station FPY.** Today's first-pass yield overall and broken
  out by station and by product, as stat tiles + a trend line. This is the number the line
  is run on (§6.1). Example (TimescaleDB SQL, last 24 h FPY by station):

```sql
-- first-pass yield = first-attempt PASS / first attempts, bucketed for a trend panel
SELECT time_bucket('1 hour', first_seen) AS t,
       station_id,
       100.0 * sum((first_result = 'PASS')::int) / count(*) AS fpy_pct
FROM (
  SELECT DISTINCT ON (dut_serial) dut_serial, station_id,
         result AS first_result, ts AS first_seen
  FROM test_runs
  WHERE ts > now() - interval '24 hours'
  ORDER BY dut_serial, ts            -- earliest attempt per unit = "first pass"
) first_attempts
GROUP BY t, station_id
ORDER BY t;
```

- **Station heartbeats / liveness.** When did each station last report a result? A station
  that has gone quiet is either starved (no units) or down (Availability, §10.4) — either
  way you want a tile that flips red. Example with Prometheus:

```text
# alert when a station hasn't pushed a result in 15 minutes during a shift
time() - max by (station_id) (mfgtest_last_result_timestamp_seconds) > 900
```

- **Failure Pareto.** A bar chart of failure modes by count for the selected window, so
  the tallest bar is obvious at a glance (§9). This is the yield-meeting (§10.2) view made
  live:

```sql
SELECT failure_mode, count(*) AS n
FROM test_runs
WHERE result = 'FAIL' AND ts > now() - interval '7 days'
GROUP BY failure_mode
ORDER BY n DESC;     -- render as a sorted bar panel; optionally add a cumulative line
```

- **Parametric SPC / control charts.** Per-parameter I-MR-style control charts with the
  process's own center line and ±3σ zones (§5.3) — *not* the spec limits. A timing-margin
  or rail-voltage panel with control limits drawn lets you *see* a Western Electric
  violation (a run, a 2-of-3-beyond-2σ) before it makes scrap.
- **Parametric drift / distribution.** A heatmap or time-series of a key parameter's
  distribution (e.g. PCIe per-lane margin, NVMe soak temperature) over days/lots. A slow
  slide of the mean is the *gradual* signal §5.3 calls out — tool wear, fixture aging,
  incoming-material shift — visible long before yield drops.
- **Throughput / cycle time vs takt.** Units/hour and per-station cycle time against the
  takt line (§6.2), so a station drifting toward the takt ceiling (a future bottleneck) is
  visible before it actually blocks the line.

### 13.4 Alerting — turn the dashboard into a pager

A dashboard nobody is staring at is useless at 2 a.m. Grafana's alerting engine evaluates
rules on the same queries and routes notifications (Slack/PagerDuty/email). The alerts a
MFG-test setup wants are the *Western-Electric-on-the-fleet* analogs:

- **Yield-drop alert** — FPY on any station falls below a floor (a *sudden* drop = process
  change / equipment / bad material, §5.3) → page the on-call test engineer.
- **Station-down alert** — no result in N minutes during a shift (the heartbeat query
  above).
- **SPC-violation alert** — a control parameter trips a Western Electric rule (point beyond
  3σ, or a run), catching *drift* before it becomes a yield event.
- **New-failure-mode alert** — a failure mode that was rare this month suddenly climbs the
  Pareto.

The discipline mirrors §6.3: tune alerts so they fire on *real* signal, not noise — a
flapping yield alert that pages on normal small-N variation gets muted, and then the real
event is missed. Alert thresholds are limits too, and they earn the same data-driven,
guard-banded treatment as a test limit (§4).

### 13.5 Grafana vs the test executive vs the MES — the one-paragraph map

Hold these three apart, because the interview-grade (and the on-the-job) clarity is in the
boundaries: the **test executive** (§12.1) runs the test and decides PASS/FAIL *per unit*;
the **MES** (§10.4) owns route control, serialization, and genealogy and decides whether a
unit may *proceed*; **Grafana** (this section) reads the resulting data and shows *humans*
how the line and fleet are trending, and pages them when something moves. Executive =
verdict, MES = routing + traceability, Grafana = visibility + alerting. They share the
per-unit record (§5.1) as the common substrate, but only the first two are *in* the
production control path.

### 13.6 What is publicly known about Zoox and AV-compute manufacturing test

A grounding note, deliberately hedged — treat the specific *internal* tool choices below
as **unverified**; what is public is the shape, not the stack:

- **Zoox builds in-house at scale.** Zoox opened a ~220,000 sq ft robotaxi production
  facility in Hayward, California (publicly reported 2025), described as a serial-production
  line designed to scale toward ~10,000 vehicles/year. Public descriptions say the site
  houses robotaxi engineering, hardware/software integration, component storage, and
  **end-of-line testing** before deployment — i.e. the vehicle/End-of-Line (EOL) phase (§1.4) is done by
  Zoox, on-site, which is consistent with the JD's "test solutions for manufacturing the
  compute platform."
- **Vehicle-level End-of-Line is physical and sensor-centric.** Reporting confirms a sensor
  **calibration bay** (aligning all sensors to one world model) and an **outdoor test
  track** (drive-quality / build verification). Beyond those two confirmed steps, *typical*
  automotive vehicle-EOL also includes optical/lighting checks and a water-ingress (rain)
  test — plausible here, but not something I could source for Zoox specifically, so treat
  them as the general EOL pattern rather than confirmed Zoox steps. Either way it maps onto
  the EOL realities this chapter names: real sensors, real harnesses, environmental checks —
  the GMSL-over-full-harness and end-to-end streaming that *only* exist at the vehicle (§1.4).
- **The compute itself is "data-center parts in a car."** The role and Zoox's public
  description point at server-grade compute assemblies, **custom PCIe devices**, networking,
  storage and memory — which is exactly the module/system test surface (§1.2-§1.3) the
  guide centers on.
- **Two real signals about the data layer — and the honest limits.** Two things *are*
  publicly visible. (1) A Zoox **Test Infrastructure Engineer** job posting describes
  **implementing RESTful APIs to provide access to manufacturing data** and integrating
  test infrastructure across teams — i.e. Zoox exposes its test/manufacturing data as a
  service (the same REST shape the companion `toolkit/` dashboard uses). (2) A
  **`zoox.grafana.net`** org instance exists, so Zoox runs Grafana *somewhere* — but it is
  access-gated and there is **no public confirmation it is used for manufacturing test
  specifically**, so treat "Zoox runs Grafana on the line" as a reasonable expectation, not
  a sourced fact. Beyond those two signals the stack is **not public**, so reason from
  verifiable industry practice: automotive EOL lines widely use a test executive (NI
  TestStand/LabVIEW common) for sequencing and reporting; automotive electronics
  manufacturing is governed by **IATF 16949** (quality/traceability) and **ISO 26262**
  (functional-safety traceability), which force the genealogy chain (§10.4); and Grafana on a
  Postgres/TimescaleDB + Prometheus stack is the standard pattern for live yield dashboards
  (§13.2). Honest framing in a design review: *"Zoox exposes manufacturing data via REST
  APIs (per their own job posts) and runs Grafana; the rest of the pipeline isn't disclosed,
  but the industry-standard shape is a sequencer → MES → parametric warehouse → Grafana
  under IATF 16949 / ISO 26262 traceability."*

Do not assert internal Zoox tool names you cannot source. "I'd expect *X* because it is the
industry norm, and here's why" is a strong answer; "Zoox uses *X*" (when you cannot cite it)
is a weak one.

---

## 14. Putting It Together — The Expert Mental Model

Strip the chapter to its load-bearing sentences:

- **Place each test at the earliest phase that can catch its defect** — coverage is a
  placement problem, governed by the 10× curve and by what is physically detectable where
  (§1-§2).
- **DV sets the limits; MT checks them — same measurement code.** Capture the parameter,
  not just the verdict, because the captured stream is what tunes the limits and proves the
  safety case (§3).
- **Set limits from data + a guard band, sized by the Gage R&R.** A low $C_{pk}$ is a
  design/process finding, not a reason to loosen the limit (§4-§5).
- **Yield is FPY/RTY; runtime is takt; deployment is config-over-code + rollback.** Buy
  yield by killing false-fails, never by raising the escape rate — because for a robotaxi
  the trade is asymmetric (§6).
- **Debug to a *physical* root cause** with the enumerate → dmesg → arm/stress/read →
  isolate → measure → decode reflex, and use Pareto/5-Whys/fishbone on a population (§8).
- **Bring-up characterizes and *sets* limits; production *checks* them, fast and
  correlated, at Zoox and at the CM across the NPI→sustaining life cycle** (§7, §10).
- **Know the stack and its boundaries:** a sequencer runs the test, the MES routes and
  traces the unit, the warehouse keeps every parameter, Grafana shows humans the trend,
  and Continuous Integration (CI) versions the test *software* — never the units (§11-§13). Most failures of a test
  *organization* are category errors between these layers.

That is the JD said back in one paragraph — and every later chapter is the depth behind
one of these sentences.
