## Power, Bring-up, and Functional Safety

The most common pattern in compute-board debugging is this: what looks like a PCIe
enumeration failure, a SerDes lock problem, or an Error-Correcting Code (ECC) storm turns out to be a power
problem in disguise. A 0.8V core rail that sequenced 2 ms late, a 1.1V Double Data Rate (DDR) rail
drooping 6% under load, a switching regulator coupling 40 mVpp into an analog Phase-Locked Loop (PLL)
supply — all of these manifest first as link errors and GPU faults. They do not look
like power problems because the digital subsystem is the part that screams. This
chapter builds the instincts to reach for the right tool — scope, DMM, IPMI, `dmesg`,
the sequencing diagram — and name the physical cause rather than chasing the symptom.

The functional safety framing at the end explains *why* the test coverage has to be as
thorough as it is, and why the records you keep are themselves part of the safety
argument. Neither section is purely theoretical: on a real Zoox board these disciplines
interact daily.

---

## Board Power Architecture

### The Power Distribution Network

A compute board is not powered from one voltage; it is powered from a *tree* of
domains that each feed a different set of loads with different tolerance requirements.
Understanding the topology is the starting point for both hardware bring-up and
manufacturing test.

**Typical domain stack on an automotive compute board:**

| Domain | Nominal | Typical Tolerance | Load Character |
|---|---|---|---|
| Primary input | 12V (or 48V in newer designs) | +/-5% | From vehicle power bus via connector |
| 5V standby | 5.0V | +/-3% | Always-on for BMC, RTC, wake logic |
| 3.3V peripheral | 3.3V | +/-5% | PHYs, SerDes, flash, GPIOs |
| 1.8V I/O | 1.8V | +/-3% | SoC I/O, LPDDR support, PCIe side-band |
| 1.1V DDR | 1.1V | +/-3% | DDR5 VDD; critical for array stability |
| 0.8-1.0V core | varies by chip | +/-3% | SoC/GPU/FPGA compute core; highest current |
| PLL / analog | 1.0-1.8V (clean) | +/-2% | SerDes, PLL; ripple spec tighter than logic |
| PoC (Power over Coax) | 6-12V | +/-10% | GMSL camera supply through the coax link |

The primary input arrives from the vehicle power bus (12V in traditional vehicle
architectures, 48V in some newer designs) through a connector, possibly an eFuse or
hot-swap controller, and into the input bulk capacitors. From there a set of switching
regulators (buck converters, multi-phase VRMs) step down to the intermediate and load
rails. Each rail then feeds one or more domains, often gated by a load switch so the
sequencer can power them on and off independently.

The tighter-tolerance rails — core, DDR, PLL — are almost always the last to come up
and the first to expose a process problem. They are also the rails where a 4-wire DMM
measurement and an AC-coupled scope are mandatory tools, not optional.

### Rail Tolerances and Guard-Banding in Test

Every rail has a tolerance stated in the component datasheet and system requirements:
commonly +/-3% for core/DDR and +/-5% for less sensitive domains. Those are the
*specification limits*. Manufacturing test typically uses **guard-banded test limits**
that sit inside the spec window to account for DMM uncertainty, fixture contact
resistance, and process drift:

```text
Spec limit:  [V_nom * (1 - 0.03), V_nom * (1 + 0.03)]
Test limit:  [V_nom * (1 - 0.025), V_nom * (1 + 0.025)]   -- 2.5% guard vs 3% spec
```

The guard band is not arbitrary; it follows from the measurement system analysis
(MSA) that characterizes the meter, the fixture, and the combined measurement
uncertainty. Any limit change must go through that analysis — loosening a rail limit
to recover yield without understanding the measurement uncertainty is how you ship
boards with marginal power delivery.

### PMICs, VRMs, and Point-of-Load Regulators

**PMIC (Power Management IC):** A single chip that integrates multiple regulated
outputs, the sequencing logic, and fault monitoring. Common in lower-power SoCs
(automotive-grade Maxim/Analog Devices, TI, Renesas PMICs). The PMIC receives an
enable from a supervisor or CPLD and raises its outputs in a programmed order per
internal sequencing registers. In manufacturing test, the PMIC's fault status registers
are readable over I2C or Power Management Bus (PMBus) — they tell you *which* rail tripped and *why* (OV/UV/OC
event) rather than just that PWRGOOD went low.

**VRM / multi-phase buck (Voltage Regulator Module):** For high-current rails (GPU/System-on-Chip (SoC)
core can demand 50-200A), the board uses a multi-phase synchronous buck VRM. Each
phase delivers a fraction of the total current and interleaves switching at an offset
phase angle, which multiplies the effective ripple frequency and reduces per-phase
inductor size. The output LC filter plus the Power Delivery Network (PDN) (power delivery network: planes,
vias, package inductance, die capacitance) determines load-transient response and
ripple at the load.

**Point-of-load regulators (POLs):** Small LDOs or single-phase bucks close to a
sensitive load (SerDes PLL, Dynamic Random-Access Memory (DRAM) VTT, Field-Programmable Gate Array (FPGA) analog supply). They often have tighter
ripple specs than the main rails and are the ones where a bad probe tip with a long
ground clip will fool you into thinking there is more noise than there actually is.

**Load switches and eFuses:** Load switches (FET + driver) gate a rail on/off under
sequencer control. eFuses (electronic fuses with programmable OCP and slew-rate
control) sit at the input and handle inrush limiting and over-current protection
without a one-time-blow physical fuse. An eFuse latching off means an OCP event
occurred — the board is asserting it cannot draw the current it needs, which is
distinct from a regulator fault.

### Power Sequencing — The Datasheet Is Law

Multi-rail devices require rails to power on and off in a **specified order with
specified inter-rail delays**. The sequencing diagram in the SoC/GPU/DDR datasheet is
not a suggestion. Violating it causes:

- **Latch-up:** I/O rail present before core → the I/O Electrostatic Discharge (ESD) protection diodes forward-bias
  into the unpowered core supply, injecting current into the substrate and N-wells and
  triggering the parasitic PNPN thyristor structure inherent in CMOS processes. Once
  the thyristor fires, it latches into a low-impedance short between VDD and GND that
  sustains itself — even if the triggering condition is removed — until power is cycled.
  The classic bench sign is a rail that was in spec at initial warm-up and now reads
  0.2V while the bench supply current-limits at 5A: that is a latched device drawing
  the supply's maximum current through the parasitic short. Apply no further power
  until you understand which rail violated sequence; the part may already be destroyed.
- **Strap miscapture:** Strapping pins and configuration inputs are sampled at
  de-reset. If the supply providing pull-up or pull-down voltage on strap pins is not
  stable before reset deasserts, the device boots with the wrong PCIe width, wrong bus
  ID, or wrong SerDes mode.
- **DDR training failures:** DDR controllers must see VDD, then VPP/VTT, then begin
  training against a stable array. Out-of-order rail arrival causes training to run
  against an unstable reference → intermittent ECC events that look random because
  they only appear under combinations of temperature and load that stress the
  marginally-trained DDR.

**A typical compute-board sequence (illustrative):**

```text
              t0       t1       t2        t3           t4          t5
12V_in   _____/^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   input present
5V_stby  ___________/^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  standby first
3.3V     _________________/^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  peripherals
1.8V_IO  _______________________/^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  I/O before core
1.1V_DDR _____________________________/^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  DDR before core
0.8V_core ___________________________________/^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  core last
PWRGOOD  ___________________________________________/^^^^^^^^^^^^^^^^^^^^   all-rails-good
RESET#   __________________________________________________/^^^^^^^^^^^^^^^  after PWRGOOD + Tsettle
         <--inrush--><-------ramps------->         <--Tpg-->  <--Tsettle-->
```

PWRGOOD is the AND of all individual rail power-good outputs. RESET# deasserts only
after PWRGOOD has been asserted for a minimum settling time (Tsettle, often 1-5ms).

**What to probe on the bench to verify sequencing:**

Set up four to eight scope channels simultaneously (multi-channel scope or two
scopes synced via trigger-out). Assign one channel each to the primary input,
the two or three critical mid-level rails, core, PWRGOOD, and RESET#. Capture on a
single shot triggered by the 12V edge. Then:

1. Verify each rail rises before the one that depends on it.
2. Measure the inter-rail delays and compare against the datasheet min/max windows.
3. Verify PWRGOOD does not assert until *all* monitored rails are in regulation.
4. Verify RESET# deasserts only after PWRGOOD + Tsettle.

A sequencing fault is the kind of defect that passes at room temperature and fails in
the field after a cold soak, because the regulator startup times shift with
temperature. That is why sequencing must be verified at both cold and hot temperature
extremes, not just ambient.

### Inrush Current

At power-on, the board's bulk and decoupling capacitance charges from approximately
zero, drawing a large transient current that can be 5-20x the steady-state idle
current. Concerns:

- **Tripping the supply's current limit:** the supply folds back, the rail never
  reaches nominal, and the board appears dead. On a bench supply this is easy to
  diagnose (voltage collapses, current pegs the limit). On a vehicle bus the symptom
  is a connector-level voltage droop during startup.
- **Blowing eFuses:** an eFuse programmed too aggressively for the actual inrush will
  latch off, leaving the board dead until reset.
- **Connector stress:** repeated high inrush cycles stress the connector contacts.

Mitigation is usually a soft-start (the VRM ramps its output over a set period,
limiting dV/dt and therefore dI/dt on the input), or an inrush-limiting circuit on
the eFuse controller. On boards with a hot-swap controller (e.g., LTC4260-class), the
controller explicitly limits inrush to a programmed current.

**Measuring inrush:** current probe clipped over the 12V input wire, scope
single-shot triggered on the rising edge of the 12V rail. Measure peak amplitude
(must be below the PSU's limit and the eFuse threshold) and duration. On a current-
limited bench supply during bring-up, set the current limit just above the expected
inrush: inrush trips it harmlessly instead of letting a latent short survive.

---

## Measuring Rails Correctly

Getting a rail measurement right requires the right instrument for the right quantity
and disciplined technique. Using the wrong instrument or wrong technique for the
physical quantity being measured is one of the most common engineering mistakes.

### DMM for DC Accuracy

A calibrated 6.5-digit DMM gives you the accurate DC value. For low-voltage, high-
current rails, the series resistance of your test leads (a typical DMM lead pair may
have 50-200mΩ) introduces a voltage drop that is significant at 0.8V with a 100A
load. **Use 4-wire (Kelvin) sensing:**

```text
2-wire (incorrect for low-V/high-I rails):
  DMM + o--[R_lead]--+--[load]--+
                     | (meas)   |        reads V_nominal - I*R_lead
  DMM - o--[R_lead]--+----------+        ERROR proportional to I

4-wire / Kelvin (correct):
  Force+ o--[R_lead]--+--[load]--+
  Sense+ o------------+          |        Sense leads carry no current
  Sense- o------------+          |        reads true V_load (at the device)
  Force- o--[R_lead]--+----------+
```

At 0.8V nominal with 100A draw and 100mΩ lead resistance, the 2-wire error is 10mV —
a non-trivial fraction of a 24mV spec window (3% of 0.8V). 4-wire eliminates it.

Additional DMM discipline:

- **Let the reading settle** (1-3 seconds on autorange; faster on a fixed range).
- **Use a fixed range** rather than autorange for repeatable measurement timing and to
  avoid the transient glitch when the meter range-switches.
- **Log the instrument IDN string and calibration due date** alongside every measured
  value. NIST-traceable calibration is part of the safety case.
- The DMM tells you the DC value. It tells you nothing about ripple or transient
  events — that is the scope's job.

### Oscilloscope for Everything Time-Varying

The scope is the right instrument for sequencing order and timing, inrush current (with
a current probe), ripple and noise on a rail, and load-transient droop and recovery.

**Measuring ripple — the discipline that separates production-ready measurements from
bench hacks:**

1. **AC-couple the channel.** The DC level (e.g., 0.8V) forces a coarse V/div setting
   that buries millivolt ripple. AC coupling removes the DC offset so you can use
   1-5 mV/div to see the ripple directly.
2. **Enable the 20 MHz bandwidth limit.** This is the most commonly omitted step.
   Without it, the probe tip acts as an antenna and couples radiated RF into the
   measurement, making clean rails look noisy. Rail ripple specs are defined up to
   20 MHz; broadband noise above 20 MHz is not part of the specification and will
   make a passing rail appear to fail.
3. **Use the shortest possible ground return.** A long ground-clip lead is a loop
   antenna. The loop area times dB/dt of nearby switching currents induces a voltage
   into your measurement. For mV-level work, use a spring-tip barrel that grounds
   at the probe tip, or clip the ground barrel directly across the decoupling cap
   pads near the load.
4. **Probe at the load's bulk decoupling capacitor**, not at the regulator output. The
   spec is what the device sees, which is at its own decoupling caps, downstream of
   the PDN inductance.
5. **Measure Vpp and note the frequency.** Ripple at the regulator's switching
   frequency is normal switching ripple. Ripple synchronous with a load event is
   droop from insufficient bulk capacitance or high PDN impedance. Broadband fuzz
   that disappears when you improve grounding is a measurement artifact.

```text
Poor probing (long ground clip, no BW limit):
  Vpp reads 85 mV -- probe loop antenna + RF coupling dominate
  "FAILS 30 mVpp spec"  <-- the measurement is lying

Correct probing (AC coupled, 20 MHz BW, barrel ground at cap):
  Vpp reads 18 mV -- actual switcher ripple at fSW, clean and periodic
  "PASSES 30 mVpp spec"  <-- true result
```

**Load transient measurement:** trigger the scope on a GPIO that fires when the GPU
stress-test starts. Capture the core rail droop as the load steps from idle to full.
Measure:
- Peak droop (how far below nominal)
- Recovery time (time from step to return within 1% of nominal)
- Overshoot after recovery

A well-designed VRM with adequate bulk capacitance droops less than 3% and recovers
within a few microseconds. A weak PDN droops through the spec limit and may not
recover before the next load step — which is exactly when the GPU starts reporting
PCIe errors.

### Probing Checklist for a Rail Under Test

A concise field reference for scope work on a low-voltage rail. Each item has a reason;
omitting any one of them produces a measurement you cannot defend.

| Step | Action | Why |
|---|---|---|
| 1 | AC-couple the channel | Removes the DC offset so you can use 1-5 mV/div and see ripple directly |
| 2 | Enable 20 MHz BW limit | Rail ripple specs are defined to 20 MHz; RF above that is measurement artifact |
| 3 | Shorten the ground return to the probe tip | Long ground clips are loop antennas; a 1 cm spring-tip barrel reduces loop area by ~100x vs a 15 cm clip |
| 4 | Probe at the bulk cap nearest the load pins | The spec is at the device, not at the regulator output; PDN inductance between the two is real |
| 5 | Note the ripple frequency and character | Switching-frequency ripple = normal; load-synchronous droop = PDN impedance problem; fixed-frequency ring = loop instability |
| 6 | Record Vpp, frequency, and load state | A passing Vpp at idle and a failing Vpp under load are different results; document both |
| 7 | If measuring DC: switch to DC-couple, widen V/div | AC coupling blocks DC; never report a DC rail value from an AC-coupled channel |
| 8 | Log scope settings and probe type with the result | Calibration and measurement context are part of the safety record |

**Power-rail probes vs. standard passive probes:** Dedicated power-rail probes (Tektronix
TPR series, Keysight N7020A-class) have 50 ohm input impedance, built-in DC offset
range of +-60V or more, and extremely low probe tip inductance. They eliminate the
AC-coupling step and let you see DC level and AC ripple simultaneously on a single
channel. On a production test station where you are measuring many rails repeatedly,
the power-rail probe is the right tool; the passive-probe-with-AC-coupling technique
is acceptable for bench bring-up when a dedicated probe is unavailable.

### Power Analyzer for Efficiency and Long-Term Telemetry

A power analyzer (Yokogawa WT series, Keysight N6700-class source-measure units) adds
continuous input power, output power, and efficiency measurements. The source-measure
units also serve as programmable loads for automated DC margining. On a test station
for a power-sensitive compute board, the power analyzer data feeds into the production
result record alongside the rail measurements — efficiency at full load is itself a
quality screen and a thermal predictor.

### Reading Rails via BMC and sysfs (Unattended Line Use)

Probing every rail with a DMM on a production line is impractical. Instead, the board's
power monitors (typically PMBus or INA-class ICs hanging off I2C) report rail voltages
and currents digitally, readable through the BMC via IPMI or through the kernel hwmon
subsystem:

```bash
# IPMI (if a BMC is present)
ipmitool sensor list             # all sensors: voltage rails, currents, temps
ipmitool sdr list                # sensor data repository with threshold values

# hwmon sysfs
cat /sys/class/hwmon/hwmon*/name          # identify the power-monitor IC
cat /sys/class/hwmon/hwmon0/in1_input     # voltage in millivolts
cat /sys/class/hwmon/hwmon0/in1_min       # low threshold
cat /sys/class/hwmon/hwmon0/in1_max       # high threshold
cat /sys/class/hwmon/hwmon0/curr1_input   # rail current in milliamps
```

These power monitor ICs live on I2C — closing the loop that a "digital" power reading
is itself a low-speed bus transaction. The station reads them at idle, then under load,
and compares to the guard-banded test limits:

```python
def verify_power_rails(expected_rails: dict) -> dict:
    """
    expected_rails: {'12V': (11.4, 12.6), '0.8V_core': (0.776, 0.824)}
    Tuple is (spec_min, spec_max) in volts.
    Capture measured value always -- pass/fail alone loses the distribution.
    """
    results = {}
    for name, (vmin, vmax) in expected_rails.items():
        measured = read_voltage_from_hwmon(name)
        midpoint = (vmin + vmax) / 2.0
        half_window = (vmax - vmin) / 2.0
        results[name] = {
            "measured": measured,
            "spec_min": vmin,
            "spec_max": vmax,
            "pass": vmin <= measured <= vmax,
            "margin_pct": round(100.0 * (measured - midpoint) / half_window, 1),
        }
    return results
```

Capture the measured value, not just the pass/fail verdict. A rail that sits at 0.780V
today and 0.776V next week is on a trajectory, and you only see it if you kept the
numbers. The measured values feed the Statistical Process Control (SPC) charts and the data-driven-limit review that
is part of the ongoing safety argument.

---

## Thermal Management and Thermal Test

### Junction Temperature and Thermal Resistance

Every semiconductor die has a maximum rated junction temperature ($T_J$) — the
temperature inside the chip at the active junctions. Die junction temperature is not
directly measurable in the field; it is inferred from the package case or heatsink
temperature using the thermal resistance network:

```text
P_die (heat source)
     |
 theta_jc (junction-to-case resistance, per datasheet)
     |
T_case (measurable on the package lid)
     |
 theta_cs (case-to-heatsink: TIM + mounting)
     |
T_heatsink
     |
 theta_sa (heatsink-to-ambient: fin area, airflow)
     |
T_ambient
```

$T_J = T_{ambient} + P \times (\theta_{jc} + \theta_{cs} + \theta_{sa})$

On a compute board under GPU stress at 200W with $\theta_{jc}$ of 0.1°C/W and a
well-mounted heatsink, junction temperature can be 20-30°C above the measurable case
temperature. The manufacturing test must therefore measure the *case* temperature
and use the thermal resistance chain to infer junction temperature, or rely on the
die's on-chip temperature sensor (the GPU's DTS, readable via `nvidia-smi`).

**Thermal resistance and the test engineer:**

- $\theta_{jc}$ is a silicon and package property. It varies unit-to-unit but you
  cannot change it.
- $\theta_{cs}$ (case-to-heatsink) is dominated by thermal interface material (TIM)
  quality and quantity, mounting pressure, and surface flatness. A poorly applied TIM
  pad or a heatsink not seated to torque spec will show up as an elevated case
  temperature at a given power level — a manufacturing defect screenable by measuring
  case temperature under a fixed load.
- $\theta_{sa}$ is an airflow and heatsink design problem. On a compute board tested
  in a fixture, the airflow conditions must match or bracket what the board sees in
  service.

### Thermal Throttling

All modern compute SoCs and GPUs implement a thermal-throttle hierarchy. As the die
temperature approaches limits, the firmware/hardware steps down clock frequency and/or
voltage to reduce power and protect the junction:

- **First throttle level:** reduce GPU clock to a lower P-state; reduce CPU boost
  clocks.
- **Second throttle level:** shut off further cores; reduce memory bandwidth.
- **Thermal shutdown:** at the absolute maximum junction temperature, the device
  asserts a thermal trip signal and the system shuts down.

In manufacturing test, **throttling under the qualification load is a defect signal**.
A board that throttles at ambient temperature under a load that field vehicles will
sustain means the thermal solution is inadequate — bad TIM application, missing thermal
pad, partial heatsink contact. The test must run a fixed, repeatable compute load
(e.g., GPU matrix-multiply loop) and verify that neither throttling events (readable
via `nvidia-smi -q | grep Throttle` or the `hwmon` throttle sysfs) nor junction
temperature limits are exceeded under the expected field environment.

```bash
# Monitor GPU temperature and throttling in real time during stress
nvidia-smi --query-gpu=temperature.gpu,clocks_throttle_reasons.active,\
clocks.gr,power.draw --format=csv --loop=1

# Check hwmon for SoC die temperature
cat /sys/class/hwmon/hwmon2/temp1_input   # millidegrees C
cat /sys/class/hwmon/hwmon2/temp1_crit    # critical trip point

# Check kernel thermal zone and throttle state
cat /sys/class/thermal/thermal_zone*/temp
cat /sys/class/thermal/cooling_device*/cur_state
```

A test that measures clocks and computes performed while the device is throttling is
measuring a degraded state, not the nominal state — the result is meaningless for its
intended purpose. The test must confirm the device is running unthrottled before
capturing performance metrics.

### Thermal Soak and Temperature Corners

**Thermal soak** refers to stabilizing the board at a target temperature (hot or cold)
before running a test, to ensure the entire board — not just the die surface — is at
temperature. A soak time of 10-30 minutes is typical for a large, multi-chip board.
Without a soak, the DRAM and peripheral ICs may still be at ambient even though the
GPU die has reached its target temperature, producing results that do not represent a
truly hot or cold board.

**Temperature corners for manufacturing test:**

| Corner | Typical Target | What It Stresses |
|---|---|---|
| Cold soak | -40°C to -20°C | Crystal oscillator frequency, PLL lock range, DDR training window, regulator dropout at low Vout |
| Ambient | 25°C | Baseline; characterization reference point |
| Hot (operating limit) | 85-105°C (board ambient) | Thermal throttle threshold, rail droop under load+heat, electromigration onset, SerDes eye closure at temperature |

The Arrhenius rule of thumb is that failure rate roughly doubles per 10°C rise in
junction temperature. This is why hot-corner screening is non-negotiable for a
safety-critical compute board: defects that do not manifest at ambient become infant
mortalities and field failures at operating temperature.

---

## DC and Transient Margining (Shmoo Testing)

### Voltage Margining

Voltage margining deliberately shifts a rail above and below nominal to find the
functional operating margin. The VRM output is adjusted either through a PMBus command
or by changing the feedback-resistor DAC on a supported regulator. A shmoo sweeps both
voltage and (where possible) temperature simultaneously:

- **Pass/fail shmoo:** for each (V, T) point, run the critical test (PCIe link train,
  DDR ECC test, Gigabit Multimedia Serial Link (GMSL) lock). Mark pass or fail. The boundary of the passing region is
  the functional operating region — compare it to the spec window.
- **Margin shmoo:** instead of pass/fail, capture a margin metric (PCIe eye height,
  Bit Error Rate Test (BERT) error count, Advanced Error Reporting (AER) correctable-error count) as a function of V and T. The margin
  degrades continuously with voltage and temperature; the test limit is set where the
  margin has headroom to the spec boundary.

On a compute board, the SoC core rail shmoo is the primary screen: confirm that the
device meets its functional requirements with the core rail at +5% and -5% of nominal
across the full temperature range. A unit that fails at -5% core voltage at 85°C has
a silicon speed bin problem — it passed at nominal but has insufficient margin. That is
a screen-and-reject decision, not a limit-relaxation decision.

**The shmoo is a characterization tool at bringup**, producing the distribution from
which limits are set. On a production line, the shmoo itself is too slow; instead the
production test exercises the nominal voltage at both temperature corners, plus a
brief voltage-step check (margin in, hold for 100ms, verify no errors) to screen for
units near the margin cliff.

### Transient Margining

Transient margining adds controlled perturbations to test stability under dynamic
conditions:

- **Load transients:** step the compute load from idle to full and back while monitoring
  the rail. The load-step is triggered programmatically (launch GPU kernel; measure
  before and after).
- **VRM enable/disable cycling:** cycle the rail on and off under the sequencer and
  verify the sequencing timing and PWRGOOD behavior repeat within spec for 10-100
  cycles.
- **Ripple injection:** a stimulus injection network adds a known-amplitude AC component
  to the rail feedback path to characterize loop bandwidth. This is advanced bench
  characterization, not production test, but the Design Verification (DV) results set the bounds on what
  the production ripple test must catch.

---

## Board Bring-up — Structured and Gated

The goal of bring-up is to take a bare Printed Circuit Board Assembly (PCBA) from powered-off on the bench to
fully-enumerated and characterized, while never letting a defect destroy the board.
The method is cheapest-safest-first, gated: you do not move to the next phase until
the current phase passes cleanly.

### Phase 0 — Documentation and Bench Preparation

Have the **schematic, board layout, BOM, power-sequencing diagram, and all connector
pinouts** open before touching the board. Know the expected device list: which PCIe
endpoints at which root ports, bifurcation settings, retimers, Network Interface Card (NIC)/Non-Volatile Memory Express (NVMe)/GPU devices,
which I2C addresses and buses, which rails and their tolerances. Set up at an ESD-safe
station: wrist strap on and verified, dissipative mat grounded to the station common
point. Stage: current-limited bench supply, 4-wire DMM, scope with current probe,
USB-TTL console adapter at the correct logic voltage (3.3V or 1.8V — confirm before
connecting), and thermal camera if available.

**Do not skip Phase 0.** The engineer who brings up a board without the schematic in
hand and discovers a dead short at power-on has no idea which net is shorted, which
IC is damaged, or how to find it. Phase 0 is the difference between a two-hour
bring-up and a two-week archaeology project.

### Phase 1 — Visual Inspection and Continuity

Before any power:

- Inspect under magnification for assembly defects: **missing components, solder bridges
  (especially on BGA escape routes and fine-pitch connectors), tombstoned passives,
  wrong orientation** (pin-1 markers on ICs, polarity marks on electrolytics and
  tantalums, diode direction), bent or missing connector pins.
- Cross-check placed parts against the BOM for obvious wrong values where it matters
  (0Ω jumpers, filter resistors, key passives on regulated rails).
- With the DMM in resistance/continuity mode: **measure each power rail to GND.** A
  near-zero ohm reading on a rail before power is applied means do not power up — find
  the short first. This check catches shorted bulk caps, solder bridges to GND, and
  dead-shorted ICs. It takes five minutes and prevents the most expensive possible
  bring-up mistake.

### Phase 2 — Controlled Power-On

Apply power from a **current-limited bench supply** with the limit set just above the
expected inrush-plus-idle current. Watch the current draw constantly:

- **Dead short:** current hits the limit immediately, voltage folds back to near zero.
  Kill power. A short on the 12V input looks like this immediately; a short on a
  secondary rail may let the primary come up but that rail's regulator current-limits.
  Find the short (thermal camera, resistance measurement with supply off) before
  re-applying power.
- **Normal power-on:** a brief inrush spike (a few milliseconds), then the current
  settles to the expected idle level.
- **Excessive idle current:** primary came up, but idle draw is 2-3x expected. A short
  or damage is present even if the voltage reads nominal — the regulator is just strong
  enough to hold the rail while sourcing fault current.

Once the current looks right, verify **every rail** with the DMM (4-wire on any rail
at or below 1.8V), and verify **sequencing** with the scope (capture all rails
simultaneously on a single shot triggered by the 12V edge). Check PWRGOOD and RESET#
timing against the datasheet. Run a hand or thermal camera over the board to find any
hot parts — a regulator that should be warm is fine; an IC that has no reason to be
hot is a fault even if all rails read in spec.

Do not leave Phase 2 until rails, sequencing, and thermal are clean. Every sequencing
bug you do not fix here will show up as a boot failure or an intermittent hard-to-
reproduce fault later, at exactly the wrong time.

### Phase 3 — Boot to Console

Connect the **UART debug console** at the correct voltage (almost always 3.3V for a
debug header, sometimes 1.8V on tight-packaged SoCs). Settings: 115200 baud, 8 data
bits, no parity, 1 stop bit (115200 8N1) is the default for nearly every embedded
Linux platform; confirm from the schematic or reference design. Use `picocom`
(`picocom -b 115200 /dev/ttyUSB0`) for a lightweight interactive terminal that exits
cleanly with Ctrl-A Ctrl-X.

Power on and watch the boot messages. Every line that scrolls past represents a
subsystem initializing successfully. The bring-up log is a record of the board's
health — save it. When the boot stops:

| Where it stops | What failed |
|---|---|
| Immediately, blank screen | No clock, no reset release, or a severe sequencing problem |
| During POST/BIOS self-test | Firmware crash; CPU or chip-level fault |
| "Initializing memory controller" hang | DDR rail marginal, DDR training failure; check 1.1V rail |
| "PCI probe" hang | PCIe root port or endpoint power problem; check that device's rail and PERST# (PERST#) |
| Kernel panic on "loading initramfs" | Storage (NVMe/eMMC) issue; check NVMe rail and reset |
| After full kernel boot, hang on device driver | Driver probe failure; read dmesg carefully |

The console is the most valuable single instrument in a bring-up. Every other
measurement is cross-referenced against what the console reported.

### Phase 4 — Device Enumeration

Once at a Linux prompt, verify every device the schematic says should be present
actually appears:

```bash
lspci -nnvvv           # PCIe: present at expected BDF? Link speed/width correct? AER clean?
lsusb -t               # USB topology
ip link show           # NICs enumerated and reported up?
lsblk                  # block devices (NVMe, eMMC)
nvme list              # NVMe drives: model, serial, firmware version
i2cdetect -y 1         # I2C bus 1: expected sensors ACKing at expected addresses?
v4l2-ctl --list-devices  # GMSL camera pipelines: deserializers enumerated?
dmesg --level=err,warn   # kernel errors during boot (probe failures, timeouts, -ENXIO)
lshw -short            # full hardware inventory
dmidecode              # SMBIOS: board revision, memory config, firmware versions
```

A missing device is the thread to pull. Cross-reference its power rail (is it in spec?),
its reset line (PERST# / RESET# asserted or deasserted correctly at the right time?),
its configuration straps (PCIe width, address), and its I2C presence signal. The
absence almost always traces to power, reset timing, a wrong strap, or a solder defect
on that specific block. Start with the schematic, not with guessing.

### Phase 5 — Subsystem Functional Tests

Exercise each major block individually so a failure is unambiguous and not attributed
to an interaction:

```bash
nvidia-smi                             # GPU: driver bound, PCIe link speed, ECC mode, temp
nvidia-smi -q | grep -E "ECC|Error"   # GPU ECC errors (should be zero on a new unit)
nvme smart-log /dev/nvme0n1            # NVMe SMART: media errors, unsafe shutdowns, temp
fio --name=seqread --rw=read --bs=128k \
    --filename=/dev/nvme0n1 --direct=1 \
    --size=4G --numjobs=1              # NVMe sequential read: throughput check
ethtool eth0                           # NIC: link speed, duplex, negotiated mode
iperf3 -c <test-server> -t 10          # NIC throughput: must hit line rate or near it
free -h                                # memory size matches expected?
memtester 1G 1                         # basic DRAM integrity check
cansend can0 123#DEADBEEF              # CAN: inject frame
candump can0 &                         # CAN: verify it loops back
v4l2-ctl -d /dev/video0 --stream-mmap \
    --stream-count=120 \
    --stream-to=/dev/null              # GMSL: capture frames; proves ser->coax->deser->SoC
```

Run each test to completion and verify the output matches the expected result before
moving to the next. A thermal camera pass at the end of Phase 5 (under load) is a good
practice: the thermal picture under functional load is more informative than the
idle-power-on picture from Phase 2.

### Phase 6 — Characterization (Data Drives Limits)

This phase generates the distribution data that sets production test limits. You cannot
set a defensible limit from a guess; you set it from a distribution of known-good units
measured under the full range of expected conditions.

- Run **CPU+GPU stress** (`stress-ng`, CUDA matrix-multiply loops) and capture: core
  temperatures, throttle onset (note the temperature and clock-step), rail voltages
  under load (AC-coupled ripple + DC droop), power consumption.
- **Re-verify all rails under full load:** check that no rail droops below its spec
  window at maximum draw. The rails-under-load measurement is the load-regulation
  screen; idle measurements alone are insufficient.
- **PCIe margin characterization:** run `lspci` to confirm link width and speed under
  load, use PCIe lane-margining (PCIe 4.0+ receivers support `PCIeLinkMarginReq`
  via the margining registers) to measure voltage and timing margin per lane, capture
  AER counters before and after a 30-minute stress run.
- **Temperature corners:** soak at cold, run stress, capture; soak at hot, run stress,
  capture. The distribution at each corner is the input to the hot and cold test limits.
- **Inrush characterization:** measure peak current and duration; compare to eFuse
  threshold and PSU limit.

The output of Phase 6 is a set of measured distributions. Guard-banded test limits are
derived from those distributions (mean +/- N-sigma, or a percentile plus tolerance
accounting for measurement uncertainty). Any limit that is not traceable to this
measured data is not a defensible limit for a safety-critical product.

### Bring-up Checklist

```text
[ ] Phase 0  Schematic/BOM/sequence/pinout in hand; ESD station verified
[ ] Phase 1  Visual: no bridges/shorts/missing/mis-oriented; rail-to-GND resistance OK
[ ] Phase 2  Current-limited power-on; I draw normal; all rails in spec; sequencing OK;
             nothing hot
[ ] Phase 3  UART console: boots to login; log saved; (stop = last line names the fault)
[ ] Phase 4  lspci/lsusb/ip/nvme/i2cdetect/v4l2/dmesg match expected; no errors
[ ] Phase 5  GPU/NVMe/NIC/CAN/memory/GMSL each exercised individually; pass
[ ] Phase 6  Stress + thermals; rails under load + ripple; PCIe margin hot+cold;
             power/inrush; corner sweep -> distributions -> guard-banded limits
```

---

## Functional Safety (ISO 26262) — What a Compute Test Engineer Must Know

### The Standard and Its Purpose

**ISO 26262** is the international standard for functional safety of automotive
electrical/electronic (E/E) systems, derived from IEC 61508 and adapted for the
vehicle domain. Its 2018 revision (second edition) added coverage for semiconductors
(Part 11) and motorcycles (Part 12). The standard addresses the *entire safety
lifecycle*: concept, system design, hardware design, software design, integration,
verification, validation, and — critically for manufacturing engineers —
**production and field operation**. Safety does not stop at the end of design
verification; it continues through every board that ships.

The central concept is **Automotive Safety Integrity Level (ASIL)**, ranging from A
(least stringent) through D (most stringent):

| ASIL | Hazard Class | Requirements Rigor | Illustrative Example |
|---|---|---|---|
| QM | Quality management only; no specific FuSa requirement | Normal quality processes | Infotainment cosmetic display |
| A | Lowest FuSa level | Defined coverage, documentation | Seat heater control |
| B | Moderate | Increased coverage, FMEDA | Power steering assist monitoring |
| C | High | Significant coverage, diagnostic requirements | Automated emergency braking component |
| D | Maximum | Maximum coverage, fault injection evidence, full traceability | AV compute path: loss of vehicle control |

A failure in Zoox's compute platform that could contribute to **loss of vehicle
control** is ASIL-D. That single fact explains why every test limit must be
defensible, every result must be traceable, and every safety mechanism must be
verified — not just the happy-path functionality.

### The Safety Lifecycle

ISO 26262 defines a V-model lifecycle. The left side is decomposition (concept →
system → hardware/software); the right side is integration and verification at
increasing levels of integration. Manufacturing test sits at the bottom-right of the
V: it is the final verification step before a unit enters the field, and it must
produce evidence that the unit as built meets the requirements defined on the left side.

The safety lifecycle phases relevant to a test engineer:

1. **Hazard Analysis and Risk Assessment (HARA):** defines the ASIL for each safety
   goal. You consume the ASIL classification; you do not derive it. But you need to
   understand it because it determines what your test must prove.

2. **Safety Requirements:** derived from the safety goals. System-level safety
   requirements flow down to hardware and software. Hardware safety requirements
   include things like "the ECC memory shall detect and correct single-bit errors" and
   "the compute module shall detect loss of a GMSL camera within 50ms."

3. **Failure Mode and Effects Analysis (FMEA) / FMEDA (FMEA / Failure Modes, Effects, and
   Diagnostic Analysis):** a systematic enumeration of all hardware failure modes, their
   effects at the system level, and the safety mechanisms that detect or control them.
   FMEDA also computes:
   - **Diagnostic coverage (DC):** the fraction of the failure mode's random hardware
     failure rate that the safety mechanisms detect. ISO 26262-5 Table 14 defines three
     reference levels: Low (<60%), Medium (60% to <90%), and High (>=90%) per the
     standard. The word "high" in FMEDA reports corresponds to the >=90% tier; claims
     of high DC require design evidence (architecture, test result, or analysis) that
     actually achieves that coverage in the fielded hardware.
   - **SPFM (Single-Point Fault Metric)** and **LFM (Latent Fault Metric):** fractions
     of random hardware failures that do not cause a safety violation (either because
     they are covered by a safety mechanism or because they are not safety-relevant).
     ASIL-D targets SPFM >= 99% and LFM >= 90% (and a PMHF below 10 FIT =
     10^-8 failures/hour). These are hardware architectural metrics computed from
     the FMEDA failure rate table, not things you measure at a test station — but
     the validity of the calculation depends entirely on the safety mechanisms being
     functional in every shipped unit, which is what manufacturing test validates.

4. **Safety Mechanisms:** the hardware and software features that detect or tolerate
   faults. Examples on a compute board:
   - **ECC / Error Detection and Correction (EDAC):** detects and corrects single-bit DRAM errors; detects (but does not
     correct) double-bit errors.
   - **PCIe AER:** detects correctable and uncorrectable PCIe
     errors.
   - **Watchdog timers:** detect software hangs by requiring a periodic heartbeat.
   - **Redundant GMSL links:** tolerate a single coax failure.
   - **Voltage monitors / PWRGOOD:** detect rail out-of-spec events.
   - **Thermal trip logic:** prevents thermal runaway from reaching catastrophic
     junction temperatures.
   - **CRC / HMAC on safety-relevant data:** detects data corruption.

5. **Fault Injection Testing:** at the hardware verification stage, faults are
   deliberately induced (bit-flips in DRAM, signal disruptions on PCIe, power glitches)
   to confirm the safety mechanisms respond as designed. Manufacturing test may include
   a lightweight version of this (inject a correctable ECC error, verify the correction
   counter increments) as part of the safety-mechanism verification coverage.

### FMEA and FMEDA — What They Demand of Manufacturing Test

The FMEDA produces a list of failure modes and their safety mechanism coverages. For
each safety mechanism, there must be evidence that it actually functions in every
shipped unit. That evidence is produced by manufacturing test. The connection is direct:

- **FMEDA says:** "ECC provides high DC (>90%) for single-bit DRAM failures; ECC must
  function to achieve SPFM >= 99%."
- **Manufacturing Test (MT) must prove:** ECC is enabled, ECC detects a correctable error (inject one or
  observe during memtest), and the EDAC driver reports it. Reading the corrected-error
  counter is not sufficient — you must verify it increments in response to an actual
  error event during test.

- **FMEDA says:** "Redundant GMSL links provide fault tolerance for coax failure; both
  paths must be independently functional."
- **MT must prove:** both paths carry error-free video independently — not just that
  the active path works, but that if you disable the primary, the secondary is already
  functional (not just present).

- **FMEDA says:** "Watchdog timer provides DC for software hangs."
- **MT must prove:** the watchdog is enabled at the firmware revision shipped, its
  timeout is set within the required interval, and it fires correctly when not serviced.

The point for daily work: your test does not only find manufacturing defects; it
**verifies that the safety mechanisms enumerated in the FMEDA are functioning in this
specific unit**. Testing the safety features is as important as testing the
functionality, because a robotaxi depends on those mechanisms to fail safe.

### ASIL Decomposition and Dual-Channel Architectures

When a single component cannot meet the required ASIL alone, the standard allows **ASIL
decomposition**: split the safety requirement between two independent channels, each
achieving a lower ASIL, such that the combination meets the original:

```text
ASIL-D  ->  ASIL-B (channel A) + ASIL-B (channel B)   -- most common
ASIL-D  ->  ASIL-C (channel A) + ASIL-A (channel B)
ASIL-D  ->  ASIL-D (channel A) + QM    (channel B)    -- asymmetric; QM channel is monitoring only
```

The notation in ISO 26262-9 is ASIL X(Y), where Y is the parent safety goal ASIL and X
is the reduced ASIL of the decomposed element (e.g., ASIL-B(D) means "ASIL-B methods
applied to a requirement that originated from an ASIL-D safety goal"). The key constraint
is **independence**: decomposition is invalid if the two channels share a power supply,
clock, common-cause failure path, or any single point of failure. A shared 12V input
without independent over-current protection is enough to invalidate a decomposition.

Zoox-style compute platforms frequently decompose safety requirements across redundant
compute modules or across primary and secondary compute paths. This means:

- Manufacturing test must verify **both channels independently** — a dual-channel
  decomposition with one dead channel is effectively undecomposed and does not meet
  the ASIL-D requirement.
- Failure of the interface between channels (the cross-channel monitoring link) is
  itself a safety-relevant failure mode that must have a safety mechanism and be tested.

### What ISO 26262 Means for Records and Traceability

ISO 26262 defines the required traceability chain for a safety-critical unit. This is
not merely a quality-management aspiration; it is an audit requirement with legal
liability implications for a vehicle in service:

- **Unit serial number** → assembly genealogy (which lot of each component went into
  this unit) → **test-program version** → **measured test results** → **disposition**
  (pass/reject/rework).
- Every link in this chain must be auditable and queryable. If a field failure occurs
  or a bad component lot is discovered, you must be able to identify *every unit that
  contains a component from that lot* within hours, not weeks.
- The test-program version is part of the traceability record because a limit change
  in the test program is a change to the safety argument. The history of what limits
  were in force when a unit was tested is permanently relevant.

**Change control under ISO 26262:** a change to a test limit, a test procedure, or
the test program itself touches the safety argument and requires:

- Version control and a tagged release (see the version-control discipline in the
  companion embedded-buses chapter).
- A golden-unit gate: re-test a known-good unit and a known-marginal unit with the new
  limits to confirm they still pass and still fail respectively.
- Documentation of the rationale for the change, the analysis supporting it, and who
  approved it.

"We loosened a limit to recover yield" is not an acceptable rationale under
ISO 26262 unless accompanied by analysis showing the loosened limit does not increase
the escape rate for safety-relevant failures. The standing principle: **ship only good
units; improve yield by reducing false fails, never by accepting more real fails**.

### Diagnostic Coverage and What it Implies for Screening

Diagnostic coverage (DC) quantifies how thoroughly safety mechanisms detect the random
hardware failure modes they are supposed to cover. DC is a calculation, not a
measurement — but the *inputs* to that calculation must be validated by test:

- If the FMEDA claims high DC (>=90%) for ECC on DRAM, that claim depends on ECC being
  enabled, functional, and correctly configured in every shipped unit. Manufacturing
  test provides that validation.
- If the FMEDA claims DC for a voltage monitor detecting rail out-of-spec events, that
  depends on the voltage monitor thresholds being set correctly and the monitor being
  accessible (not hung/dead) in every shipped unit.

A safety mechanism that exists in the design but is disabled or misconfigured in a
specific unit contributes zero DC for that unit, regardless of what the FMEDA says.
Manufacturing test is the gate that ensures the design's safety-mechanism coverage
is actually realized in every unit.

**FMEDA-to-test mapping in practice:** the FMEDA document is the specification for
what your test must exercise. For each safety mechanism row in the FMEDA, ask:
(a) is there a test step that confirms this mechanism is present and functional in
this unit? (b) does that step produce a binary result (mechanism fires / does not fire)
or only a proxy (register readable but never stimulated)? The standard distinguishes
between diagnostic coverage claimed because a mechanism *exists in the design* and
coverage claimed because the mechanism *is proven functional at test*. For ASIL-D,
only the latter counts. A practical cross-reference table format:

```text
FMEDA Failure Mode        | Safety Mechanism        | MT Step    | Acceptance Criterion
--------------------------|-------------------------|------------|---------------------
DRAM single-bit error     | ECC SECDED              | ECC-inject | EDAC counter +1, no crash
Rail undervoltage event   | PMBus UV threshold      | MV-margin  | FAULT# asserts at V_nom-4%
Watchdog expiry           | WDT reset               | WDT-test   | Reset occurs within timeout+10%
GMSL coax open            | Redundant path failover | SI-fault   | Secondary stream valid <100ms
```

Build this table at bringup and keep it alive. Every FMEDA revision that adds or changes
a safety mechanism requires a corresponding MT step change — that linkage is part of
the change-control argument.

### RAS Requirements and Their Interaction with Manufacturing Test

**Reliability, Availability, Serviceability (RAS)** — captures the operational
requirements that sit alongside and overlap with functional safety:

- **Reliability** requirements (e.g., MTTF targets, infant-mortality budgets) are
  supported by thermal screening (burn-in, HTOL) and voltage-margining screens that
  accelerate and expose weak or damaged devices before they reach the field.
- **Availability** requirements (the system must be available >99.X% of operating hours)
  drive the redundancy architecture that MT must verify both paths of, as discussed
  above.
- **Serviceability** requirements (failures must be diagnosable without returning
  the vehicle to a depot) drive the need for the Design for Testability (DFT) hooks —
  JTAG, UART console, IPMI, in-system test points — that manufacturing test also uses.
  These hooks must work in every unit.

The interplay at manufacturing test: a unit that passes all functional tests but has
a dead UART console debug port, a disabled BMC IPMI interface, or a non-functional
JTAG chain has reduced serviceability in the field, which means failures will be
harder to diagnose and repair. These are screenable defects at manufacturing test, not
field-discovery failures.

### The Manufacturing Test Safety Case Contribution

The total argument that a compute board is safe to ship is a **safety case** — a
structured argument with evidence. Manufacturing test contributes evidence at several
levels:

1. **Hardware conformance:** the board as built matches the design (correct components,
   correct assembly, no manufacturing defects).
2. **Functional correctness:** all functions operate within specification under the
   full range of environmental conditions (voltage corners, temperature corners).
3. **Safety mechanism verification:** each safety mechanism enumerated in the FMEDA
   is functioning in this specific unit.
4. **Parametric traceability:** measured values for safety-relevant parameters are
   recorded and traceable to the unit serial number, component genealogy, and test
   program version.

These four contributions, taken together, are the manufacturing test's contribution to
the safety case. A test program that only checks pass/fail on happy-path functionality
makes no contribution to items 3 and 4, and an incomplete contribution to item 2. The
test engineer who understands ISO 26262 designs a test that covers all four, and
defends that design against the "we only need to test what breaks during assembly"
objection with the argument that the standard requires it.

**What a safety auditor looks for in a manufacturing test record:**

- Test-program version number and the git commit or release tag it corresponds to.
- A link from each test step to the requirement it satisfies (requirement ID from the
  safety requirements document, or at minimum the FMEDA row it validates).
- Measured values, not just PASS/FAIL verdicts, for all safety-relevant parametric tests.
- Evidence that safety mechanism activation tests (ECC inject, watchdog fire, GMSL
  failover) produced the correct observable response — not just that the test step ran.
- Re-test traceability: if a unit was reworked and re-tested, the record must show
  which steps were re-run, with the new results linked to the same serial number, with
  the rework action documented.

A test record that cannot answer "what firmware version was loaded, which test-program
version ran, and what did each safety-relevant rail measure at hot-corner full load?"
for an arbitrary serial number is not a compliant safety case contribution.

**Concession and deviation discipline:** if a unit is dispositioned as acceptable with
a test result outside the normal pass window (a "concession"), the concession document
must include the safety analysis showing the out-of-window result does not increase
safety risk. Blanket "use-as-is" dispositions without analysis are a common audit
finding and a liability exposure.

---

## Correlating Rail Problems with Digital Failures

This is the "digital failures are often power failures" instinct made concrete and
operational. The pattern occurs regularly enough that it should be reflexive:

**Symptom: PCIe link retrains or falls back in width/speed under load.**
Look first at the core rail of the endpoint device under load. AC-couple the scope and
measure ripple; DC-couple and measure droop during the load step. A rail that sags 60mV
at the moment of link retrain is the Root Cause Analysis (RCA). Also check the SerDes PLL supply: a
switching regulator near the SerDes that couples into the PLL reference creates
deterministic jitter at the switcher frequency, visible as a periodic component in the
PCIe eye.

**Symptom: GMSL camera loses lock under heavy load or at temperature.**
Check the PoC (Power over Coax) voltage at the connector: it may be drooping if the
PoC regulator is shared with a heavily loaded domain. Check the serializer supply
at the camera end (read via I2C through the GMSL reverse channel). A serializer
undervoltage causes re-initialization events that look identical to a coax signal-
integrity problem.

**Symptom: ECC correctable errors increasing with temperature and time under load.**
Check the DDR VDD and VTT rails under load. DDR5 at 1.1V with insufficient bulk
capacitance or a weak VRM droops during burst accesses — that droop widens the DDR
timing eye and causes correctable (then uncorrectable) errors. Also check the DDR
reference voltage (VREF) stability. If the ECC errors appear exclusively at
temperature, scope the rail hot: a rail that passes at ambient may droop to
$V_{nominal} - 5\%$ at 85°C due to regulator thermal derating.

**Symptom: Intermittent boot failures, especially after cold starts.**
Scope the 3.3V peripheral rail and the 1.8V I/O rail during cold power-on. LDOs and
linear regulators can have sluggish startup at -40°C; a rail that comes up 5ms late
at cold causes the SoC to sample incorrect strap pins or begin DDR training against an
unstable reference. The failure is inconsistent because most cold soaks only go to
-20°C, not the -40°C corner where the startup time shifts enough to violate the
sequencing window.

The diagnostic discipline: when a digital symptom appears, check the relevant rails
under the same conditions (load, temperature, time-into-test) that produce the
symptom. Correlate the scope timebase with the error log timestamp. A rail that droops
at the exact moment the error log records a correctable error is root-cause evidence,
not circumstantial evidence.
