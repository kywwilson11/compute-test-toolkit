## Control Systems

A compute test engineer is not a controls engineer, but test fixtures, the board under
test, and the vehicle systems around it all close control loops — thermal forcers,
fan speed regulators, power-supply feedback loops, and the higher-level servo
control in the AV stack. You need fluency: enough to hold a five-minute technical
conversation, justify a fixture's control design with engineering reasoning, and
recognize the control-loop signatures that show up in compute board behavior.

---

## Feedback, Open-Loop, and Closed-Loop

**Open-loop control** applies a command based purely on a model of the plant, with no
measurement of the actual output. A heater set to a fixed duty cycle is open-loop: it
does not know whether the board temperature is rising, falling, or already at target.
Open-loop is simple, fast, and noise-free — it is also blind to disturbances and plant
variation.

**Closed-loop (feedback) control** measures the output, compares it to a setpoint,
and generates a correction based on the error. A PWM fan controller that reads a
temperature sensor and adjusts duty cycle is closed-loop. Feedback rejects
disturbances (a sudden load increase on the board) and tolerates plant variation
(every board has slightly different thermal resistance). The cost is measurement
noise, the risk of instability, and the latency introduced by sensing and actuation.

Most real systems combine both: **feedforward** provides an open-loop estimate that
gets close immediately; **feedback** trims the residual error. This is the design
choice that appears in well-engineered test fixtures and in the regulators on the
board under test.

---

## PID — Intuition for Each Term

The three PID terms each address a different aspect of the error signal:

### Proportional — Present Error

```text
correction = Kp * error
```

The proportional term reacts to *right now*. Larger Kp means a stronger response to
error, which speeds up convergence but also drives toward oscillation as Kp increases.
The fundamental problem: as the error shrinks, so does the correction, and the system
settles *near* the setpoint but not exactly at it. That permanent residual offset is
called **steady-state error**, and proportional-only control always leaves it (unless
Kp is impractically large).

Tuning symptom: **Kp too low** — slow convergence, large steady-state error.
**Kp too high** — oscillates around the setpoint, possibly goes unstable.

### Integral — Accumulated Error

```text
integral += error * dt
correction = Ki * integral
```

The integral accumulates error over time. As long as any steady-state error remains,
the integral keeps growing and the correction keeps increasing, driving the output
until the error reaches zero. This is the only term that eliminates steady-state error
from constant disturbances (a constant load on a thermal system, a constant valve
bleed in a pneumatic press).

The pathology: **integral windup**. If the actuator saturates (maximum heater duty, or
maximum regulator voltage trim) while a large error persists, the integral keeps
accumulating even though additional integration changes nothing. When the error finally
clears, the over-wound integral drives a violent overshoot. **Anti-windup** prevents
this by clamping the integral to a physically meaningful range:

```python
integral += error * dt
integral = max(-max_integral, min(integral, max_integral))  # clamp
correction = Ki * integral
```

Tuning symptom: **Ki too low** — slow elimination of steady-state error, may never
fully converge. **Ki too high** — oscillates with a growing amplitude (unstable), or
winds up and overshoots badly. **Missing anti-windup** — well-tuned steady state but
dramatic overshoot after saturation periods.

### Derivative — Rate of Change of Error

```text
correction = Kd * d(error)/dt
```

The derivative predicts where the error is going and applies a braking correction.
It damps oscillation and speeds convergence near the setpoint. The two serious
problems: it **amplifies measurement noise** (high-frequency noise has a large
derivative), and it is **destabilizing in the presence of dead time** (transport
delay between command and response). With dead time, the derivative acts on stale
information and can drive the system toward instability at exactly the moment the
command is reaching the plant.

Tuning symptom: **Kd too high** — high-frequency chatter, sensor noise amplified into
actuator wear. **D in a dead-time-dominated plant** — makes stability worse, not
better. The correct response for significant dead time is to *omit* the derivative
term entirely.

**Practical design choice for fixture control:** a pneumatic force actuator with
100-200ms regulator dead time and noisy load cell signals uses feedforward + integral
only. The feedforward provides the initial estimate using the known cylinder area
model; the integral trims the constant valve-bleed offset. The derivative is
explicitly *left out* because dead time makes it harmful. This is the correct
engineering judgment: match the tool to the physics, and omit terms that hurt.

---

## Stability, Poles, Bandwidth, and Margins

### Transfer Functions and Poles (Conceptual)

A linear control system can be described by a transfer function — a ratio of polynomials
in the Laplace variable $s$. The roots of the denominator are **poles**. Where the
poles sit in the complex plane determines stability:

- Poles in the **left half-plane** (negative real part) → stable; the response decays
  exponentially.
- Poles on the **imaginary axis** → marginally stable; sustained oscillation.
- Poles in the **right half-plane** (positive real part) → unstable; growing
  oscillation.

You do not need to derive transfer functions for compute-board work. You need the
intuition: a system with poles close to the imaginary axis is "almost oscillating" and
has poor disturbance rejection; pole locations move as gains change, which is why
increasing Kp or Kd too far eventually crosses the imaginary axis and goes unstable.

### Bandwidth

Loop bandwidth is the frequency range over which the feedback loop effectively
rejects disturbances. A higher bandwidth means the loop responds faster to errors,
but also means it amplifies high-frequency measurement noise more. For a thermal
control loop (slow plant, slow sensor), the bandwidth is naturally low — a few Hz at
most. For a power-supply voltage regulation loop (fast plant), bandwidth can be tens
of kHz. The test engineer encounters bandwidth implicitly: if the thermal controller
is too slow to correct a sudden load change, the temperature overshoots; if the power
supply loop bandwidth is too low, load-transient response is sluggish and the rail
droops further than it should.

### Phase Margin and Gain Margin

Stability margins quantify how far the system is from instability:

- **Phase margin (PM):** at the frequency where the open-loop gain is 0 dB (the gain
  crossover frequency), how many more degrees of phase lag would cause instability?
  A PM of 45° is the conventional minimum for "adequate" damping; 60° is comfortable.
  PM below ~20° means the step response overshoots significantly and rings.
- **Gain margin (GM):** at the frequency where the open-loop phase is -180° (the phase
  crossover frequency), how much more gain increase would cause instability? Typically
  want GM >= 6 dB.

**Where this appears in practice:** a power supply's loop compensation is designed for
a specific output capacitance and load range. If the actual capacitance (from board
layout, parallel decoupling cap combinations) differs from the design target, the
phase margin changes. A supply that was stable on the original board may be marginally
stable or oscillating on a revised layout — which shows up as rail ripple at the
resonant frequency that does not go away at any load point, and that changes when you
add or remove bulk capacitors. Recognizing this signature is the practical use of the
stability-margin concept.

### Measuring Loop Stability on the Bench (Bode Plot)

For bring-up or debugging of a switching regulator, the loop gain can be measured
directly with a network analyzer or a function-generator-plus-oscilloscope setup:

1. Inject a small AC perturbation into the feedback network (typically through a 10-50 ohm
   resistor inserted in series with the feedback divider).
2. Sweep the perturbation frequency from below the LC resonance up to above the
   switching frequency (e.g., 1 kHz to 500 kHz for a 500 kHz switcher).
3. Measure the gain (Vout/Vin) and phase at each frequency.
4. Read phase margin at the 0 dB crossover; read gain margin at the -180 deg phase
   crossing.

Dedicated loop analyzers (Ridley AP300 series, AP Lab FRA-series) automate this sweep
and plot the Bode diagram directly. The result tells you whether the supply has
adequate stability margins with the actual capacitors on the board, not with whatever
the datasheet reference design assumed.

**Practical rule for test engineers:** you will rarely measure Bode plots in production.
You will recognize the *symptom* of a stability problem (sustained oscillation on the
rail at a frequency unrelated to the switching frequency, ripple that changes when you
clip a scope probe to the output, or rail that rings at a fixed frequency independent
of load). That recognition is what drives you to escalate to the power design team with
a specific, actionable observation rather than a vague "the rail looks noisy."

### Dead Time and Its Effect on Stability

**Dead time** (also called transport delay) is any fixed latency between when a control
command is issued and when the plant output begins to respond. It is a pure phase lag:

```text
phase lag from dead time = 360 * f * T_dead   (degrees, f in Hz, T_dead in seconds)
```

At a crossover frequency of 10 kHz with 10 us of dead time, the dead time alone
contributes 36 degrees of phase lag — enough to eat half of a 60 degree phase margin.
Sources of dead time in compute-board control loops:

- Pneumatic actuators in test fixtures: gas propagation delay from valve to cylinder
  (10-200 ms typical for long runs)
- PWM-to-mechanical lag in fans: the tachometer only reports speed after the next
  blade passes, introducing up to one revolution of delay
- Digital control loop computation: the ADC conversion time plus the controller
  computation cycle plus the DAC/PWM update latency

When dead time is significant relative to the loop time constant, the derivative term
makes stability *worse* (not better) because it acts on stale information. The correct
responses are: reduce dead time where possible (shorten pneumatic lines, increase
sampling rate), reduce loop bandwidth to stay well below the frequency where dead-time
phase lag dominates, or for large fixed dead times consider a Smith predictor — an
inner-loop model that predicts the plant output ahead of the dead time and feeds a
corrected error signal to the controller. The Smith predictor is advanced fixture
control design territory, but knowing the concept prevents the mistake of tuning a
high-gain controller on a dead-time-dominated plant and wondering why it is unstable.

---

## Sampling and the Nyquist Rate

A digital controller samples the measured output at a discrete time interval $T_s$
(sampling period); the sampling frequency is $f_s = 1/T_s$.

**Nyquist theorem:** to represent a signal of frequency $f$ without aliasing, the
sampling rate must be at least $2f$. The **Nyquist frequency** is $f_s / 2$ — the
highest frequency that can be unambiguously represented in the sampled signal.

For control systems, the practical rule is stricter: sample at least **10-20x the
closed-loop bandwidth** to avoid the degradation in phase margin that discrete sampling
introduces. A digital thermal controller with a 1 Hz bandwidth loop should sample at
10-20 Hz minimum (not 2 Hz, even though Nyquist would technically allow it).

**Aliasing:** if the sensor signal contains frequency content above $f_s / 2$, those
components fold back (alias) into the sampled data as false low-frequency content. An
anti-aliasing filter before the ADC removes signal content above $f_s / 2$ before
sampling. The 20 MHz bandwidth limit on the scope for ripple measurement is the
analog of this: you remove content above the relevant frequency before drawing
conclusions.

### Discrete-Time Control and the Z-Domain (Conceptual)

Continuous-time controllers are analyzed with the Laplace variable $s$. When a
controller is implemented digitally — sampled, computed, and output at discrete time
steps — the equivalent analysis uses the Z-transform variable $z$. The relationship is:

```text
z = exp(s * Ts)     where Ts is the sampling period
```

For conceptual fluency, the key facts are:

- The left-half s-plane (stable continuous poles) maps to the *inside* of the unit
  circle in the z-plane. A discrete controller is stable if all its poles lie strictly
  inside the unit circle.
- Sampling adds a computational delay of at least one sample period, which shows up
  as additional phase lag in the discrete loop — this is part of why the 10-20x
  oversampling rule exists.
- A continuous PID translated naively to discrete form (the "Euler backward" or
  "bilinear / Tustin" approximation) is generally stable when the sampling rate is
  well above the loop bandwidth, and degrades in performance as the sampling rate
  approaches the Nyquist minimum.

You will not need to derive z-domain transfer functions in daily work. What you need is
the intuition: a digital controller sampled too slowly does not just fail to track fast
disturbances; it can become unstable in ways that have no analogue in the continuous
design it was based on.

**Fan controller example:** a BMC fan controller implemented in firmware samples the
DTS temperature sensor at 1 Hz and adjusts PWM duty. At 1 Hz sampling, the controller
can only respond to thermal events at timescales of seconds — appropriate for a slow
thermal plant. If someone "improves" the controller by increasing the gain to speed up
response without increasing the sample rate, the discrete phase lag from the 1 Hz
sample may push the loop into oscillation: fan hunts between high and low speed,
temperature oscillates, and the root cause is the combination of high gain and
inadequate sampling rate. The fix is to increase the sampling rate before increasing
the gain, not the other way around.

---

## Sensors and Actuators in an Autonomous-Vehicle Context

**Sensors relevant to compute-board test:**

| Sensor | Physical Quantity | Interface | Test Relevance |
|---|---|---|---|
| On-die DTS | Junction temperature | MMIO / hwmon sysfs | Primary thermal monitoring |
| Thermistor / thermocouple | Board ambient / case temp | ADC / I2C | Heatsink and case temperature |
| Shunt + INA-class IC | Rail current | I2C / PMBus | Power consumption monitoring |
| Hall-effect / tachometer | Fan speed (RPM) | GPIO pulse count | Fan health and speed control |
| Voltage monitor (INA219) | Rail voltage | I2C | In-circuit rail monitoring |
| Load cell | Applied force | Strain-gauge bridge / ADC | Fixture force control |

**Actuators:**

| Actuator | Physical Action | Control Signal | Used In |
|---|---|---|---|
| PWM fan | Air cooling | PWM duty cycle (25 kHz typical) | Thermal control loop |
| Heater element | Heat application | PWM or switched DC | Thermal soaker / chamber |
| Power supply trim | VRM output voltage | PMBus / DAC feedback | DC margining |
| Load switch (FET) | Rail enable/disable | GPIO | Sequencing and fault injection |
| Pneumatic regulator | Applied force | Proportional valve current | Fixture press |

**In the AV stack above the compute board**, sensors (cameras, radar, lidar, IMU) feed
perception algorithms, which output state estimates that feed planning and control.
The actuators are the vehicle's steering motor, brake actuators, and throttle. The
compute board is the central processing node in this chain. A test engineer's job is
to verify that the board correctly receives sensor data and outputs commands in the
required format — the control loop runs in software, and the test verifies the I/O
correctness at the boundaries.

---

## Where Test Engineers Touch Control Loops

### Thermal Control and Fan PWM

The board's BMC or System-on-Chip (SoC) implements a closed-loop thermal controller that adjusts fan
PWM to maintain die temperature within operating limits. The control loop is:

```text
setpoint (T_target) --> [+] --> [controller] --> [PWM fan] --> board thermal plant
                         ^                                             |
                         |______[temperature sensor (DTS)]____________|
```

In manufacturing test, verify:
- Fan responds to a PWM command: read the tachometer back and confirm RPM vs duty
  curve is within spec.
- The thermal control loop converges: under a fixed thermal load, the temperature
  settles and does not oscillate.
- Throttling does not occur at the nominal test ambient temperature and load — if it
  does, the thermal solution is defective.

```bash
# Write fan PWM via hwmon (typical paths vary by board)
echo 200 > /sys/class/hwmon/hwmon3/pwm1      # set PWM level (0-255)
cat /sys/class/hwmon/hwmon3/fan1_input        # read RPM
cat /sys/class/hwmon/hwmon3/temp1_input       # read temperature (millidegrees C)
```

### Power Regulation Loops

Every switching regulator is a closed-loop controller: the output voltage is the
measured variable; the PWM duty cycle is the control output. The loop compensator
(Type II or Type III op-amp compensator, or a digital controller in a Power Management Bus (PMBus) VRM) is
designed for a specific phase margin and transient response. From a test perspective:

- **Steady-state rail accuracy** is the DC gain of the loop — verify with DMM.
- **Ripple** is the loop's rejection of the switching frequency — verify with AC-
  coupled scope at 20 MHz BW.
- **Load transient response** is the loop's dynamic tracking — verify with scope,
  trigger on load step, measure droop and recovery time.
- **Loop instability** appears as sustained oscillation on the rail at a frequency
  unrelated to the switcher frequency — typically caused by a capacitance out of the
  compensation design range. It shows up as a ring on the scope that does not
  correlate with switching frequency or load events.

### Fixture Control Loops — Design Fluency for Test Engineers

A production test fixture commonly contains one or more explicit control loops:

**Thermal forcing loop:** a Peltier or resistive heater controlled by a PID to drive
the board to a target temperature for hot-corner testing. Design considerations:
- The plant (board thermal mass) is slow and first-order-like; integral action is
  needed to eliminate steady-state offset against a fixed ambient.
- Derivative action may help if the sensor is low-noise and the plant has no significant
  dead time; omit it if the thermocouple signal is noisy or the heater-to-sensor path
  has a transport lag.
- Overshoot on heat-up is a real concern for thermal-sensitive components; limit the
  heat-up rate (ramp-rate limiter on the setpoint, not just on the actuator) to avoid
  overshooting the target corner by 10-15 deg.

**Pneumatic press loop:** a proportional valve controlled by a PI loop to maintain a
target contact force on a board-under-test during pogo-pin engagement. Design
considerations:
- Dead time from valve-to-cylinder gas transit (often 50-200 ms) dictates that
  bandwidth must stay well below 1/(2 * T_dead).
- Use integral-only or feedforward+integral; derivative is counterproductive.
- Implement an anti-windup clamp at the valve's physical limits (0% to 100% open);
  windup during large force steps produces violent overshoot that can crack boards.

**Fan speed loop in the fixture enclosure:** a closed-loop fan controller maintaining
airflow to simulate field conditions during test. The main failure modes are:
- Fan fault (stall or bearing failure): tachometer reads zero or drops below expected
  RPM for the commanded duty. Screen by confirming tachometer reads within +-20% of
  the duty-RPM curve at each tested duty point.
- Thermal leak in fixture: fan at full duty but board temperature rising — the fixture
  thermal design is inadequate for the board's power level. Flag as a fixture
  maintenance issue, not a board failure.

**Commissioning a fixture control loop:** before using a new fixture for production
test, verify the control loops independently:

```text
1. Step-response test: command a step to the target setpoint from ambient;
   measure rise time, overshoot, and settling time. Confirm overshoot < 5 deg
   for thermal, < 5% for force.
2. Disturbance rejection: with the loop at setpoint, apply a known disturbance
   (increase board load for thermal; add a fixed weight for force). Verify the
   loop corrects within the specified time.
3. Saturation recovery: drive the actuator to saturation (100% duty or max valve),
   then step to setpoint. Verify the anti-windup clamp prevents runaway overshoot.
4. Long-duration stability: run the loop for 30 minutes at setpoint under nominal
   load. Temperature or force should not drift or oscillate. Any slow drift indicates
   either a calibration error in the sensor or a leak/loss in the actuator.
```

These tests are part of fixture validation and are documented alongside the board test
procedure. A fixture with a poorly tuned or uncalibrated control loop introduces
process variation that cannot be distinguished from board-level variation — it becomes
a phantom source of yield loss.

### Control Loop Tuning Symptoms — Quick Reference

| Observed Symptom | Likely Cause | First Fix |
|---|---|---|
| Slow convergence, permanent offset | Ki too low; or missing integral term | Increase Ki gradually; confirm integral is active |
| Overshoot then slow settle | Kp or Ki too high relative to system time constant | Reduce Ki first; then Kp if still overshooting |
| High-frequency chatter on actuator | Kd too high; sensor noise amplified | Reduce Kd; add low-pass filter on derivative path |
| Growing oscillation (unstable) | Gain too high; phase margin exhausted | Reduce Kp until oscillation stops; then retune |
| Correct at idle; oscillates under load | Phase margin collapses with added capacitance; or supply loop bandwidth near resonance | Check for capacitance added at load; reduce loop bandwidth |
| Rail oscillates at one frequency regardless of load | Loop instability (not switching ripple) | Identify frequency; measure vs switcher frequency; consult power design if different |
| Overshoot only after long saturation periods | Integral windup | Add or tighten anti-windup clamp to actuator physical limits |
| Temperature never reaches setpoint | Fan at max duty; thermal solution inadequate; thermistor fault | Check fan RPM at max duty; verify thermistor calibration; inspect TIM |
| Correct steady-state but sluggish load response | Loop bandwidth too low for load step rate | Increase Kp or reduce derivative filter; or increase switching regulator bandwidth |
| Intermittent oscillation correlated with load steps | Dead time + high derivative gain | Reduce or remove derivative term; increase damping |

---

## Control Concepts Applied to the Compute Board — Summary

The control-systems vocabulary is not abstract decoration in a compute test context.
Every closed loop that runs through or around the board under test has a gain, a
bandwidth, and stability margins, and when any of those are wrong the symptom appears
in the data you are collecting:

- A rail that oscillates at 8 kHz independent of switching events is a regulator loop
  instability problem, diagnosable with a scope and the stability-margin framework.
- A thermal forcer that overshoots by 15 deg on every heat-up cycle is an anti-windup
  problem in the fixture PID, not a board defect — but if you do not distinguish the
  two, you will spend time chasing phantom thermal failures.
- A Gigabit Multimedia Serial Link (GMSL) camera that intermittently loses lock during high GPU load is a PoC voltage
  control problem: the PoC regulator's load-transient response is insufficient, which
  is a loop-bandwidth problem in the power supply.
- A fan controller that hunts between 3000 and 6000 RPM under steady load is a
  discrete-time instability in the BMC thermal loop — too much gain at too low a
  sampling rate.

Fluency here means you can name the control-loop property that is failing, produce the
evidence (scope trace, temperature log, RPM log, frequency annotation), and hand the
right problem to the right team with the right framing. That is the practical boundary
between someone who says "the board is unstable" and someone who says "the 1.0V SerDes
supply has a 7 kHz sustained oscillation that is not at the 500 kHz switching frequency,
onset correlates with adding 47 uF of bulk capacitance on the revised layout, and the
regulator datasheet shows a minimum capacitance of 22 uF for stability at the current
compensation setting."
