This is the working math of the test floor. Not interview trivia — the formulas you
reach for when you set a limit, size a sample, pick a confidence target, or defend a
yield/escape number in a quality review. Every section ties a method to a decision:
*where do I put the spec line, how many units do I pull, how long do I run the Bit Error Rate (BER)
soak, when do I stop the line.*

The companion **PCIe chapter** carries the link-test detail; this chapter carries the
statistics that turn a Bit Error Rate Test (BERT) run, a Cpk study, or an Statistical Process Control (SPC) chart into a pass/fail call you
can sign your name to. The opening sections — probability, distributions, and the sigma
bridge — fix the probability and distribution vocabulary once; read them first. The
middle sections (geometry/sensor FOV, logs and dB, the GT/s → GB/s bandwidth math,
vectors, and the core physical relationships) are supporting reference math you reach for
less often but want defensible when you do. The daily tools are the back half: **BER and
confidence**, **process capability and limits**, **SPC**, **gauge R&R**, **sampling and
AQL**, **yield and throughput**, and **reliability**. Sections cross-reference each other
by name throughout; the one-page formula sheet at the end collapses the whole chapter into
something you can pin to a station.

---

## Probability Fundamentals

Everything downstream — Cpk, control limits, AQL, BER confidence — is a probability
statement dressed in engineering units. Get the four rules right and the rest follows.

### The rules you actually use

- **Sample space $S$** — every outcome. One board test: $S = \{\text{pass}, \text{fail}\}$.
- **Probability** of equally likely outcomes: $P(A) = |A| / |S|$.
- **Axioms:** $0 \le P(A) \le 1$; $P(S) = 1$; $P(\varnothing) = 0$.
- **Complement:** $P(A^{c}) = 1 - P(A)$. If $P(\text{defective}) = 0.03$ then $P(\text{good}) = 0.97$. On a high-yield line you almost always compute the rare event through its complement — it is numerically stabler.

**Addition (OR).** General: $P(A \cup B) = P(A) + P(B) - P(A \cap B)$. Drop the overlap
term **only** when $A$ and $B$ are mutually exclusive.
*P(GPU fails OR Non-Volatile Memory Express (NVMe) fails)* subtracts P(both) so you do not double-count the boards
that fail both.

**Multiplication (AND).** General: $P(A \cap B) = P(A)\,P(B \mid A)$. Drop the
conditional **only** when $A$ and $B$ are independent.
*If GPU and NVMe failures are independent,* $P(\text{both}) = 0.02 \times 0.01 = 0.0002$.

**Independence is not mutual exclusivity.** Independent means knowing $A$ leaves
$P(B)$ unchanged. Mutually exclusive means $A$ happening forces $B$ not to ($P(A \cap B) = 0$).
Two mutually exclusive events are therefore *dependent* — learning $A$ occurred tells you
$B$ did not. Mixing these up is the single most common probability error in a review.

### Conditional probability and Bayes — the "defective given a FAIL" engine

$$P(A \mid B) = \frac{P(A \cap B)}{P(B)}.$$

This reversal is the workhorse of test statistics. The test gives you
$P(\text{FAIL} \mid \text{defective})$ (sensitivity) and $P(\text{FAIL} \mid \text{good})$
(false-reject rate). What you actually need to make a disposition is the *reverse*:
$P(\text{defective} \mid \text{FAIL})$. Bayes flips it:

$$P(A \mid B) = \frac{P(B \mid A)\,P(A)}{P(B)}, \qquad
P(B) = P(B \mid A)\,P(A) + P(B \mid A^{c})\,P(A^{c}).$$

**MSA vocabulary mapped to Bayes** (memorize this row of equivalences — quality and
medical-test language both show up at Zoox):

| Test term | Meaning | Bayes piece |
|---|---|---|
| Sensitivity (true-positive rate) | P(FAIL given truly defective) | P(B given A) |
| Specificity (true-negative rate) | P(PASS given truly good) | P(B^c given A^c) |
| False-positive / false-reject rate | 1 - specificity | P(B given A^c) |
| PPV (precision) | P(defective given FAIL) | the answer Bayes returns |
| Prevalence | true defect rate | P(A) |

**Worked — the PPV problem.** A test has 96% sensitivity and a 2% false-positive rate
($98\%$ specificity). True defect rate (prevalence) is 3%.

$$P(\text{FAIL}) = 0.96(0.03) + 0.02(0.97) = 0.0288 + 0.0194 = 0.0482,$$
$$\text{PPV} = P(\text{def} \mid \text{FAIL}) = \frac{0.0288}{0.0482} = 59.8\%.$$

**The lesson that runs a high-yield line:** even at 96% detection, only ~60% of your
FAILs are real defects. The 2% false-positive rate acting on the *large* good
population (97%) manufactures most of the alarms. On a mature line where prevalence is
low, **false-positive rate dominates PPV, not sensitivity** — it is what drives your
scrap-good cost and your retest burden. Tightening a flaky test limit to kill false
rejects often buys more than chasing the last 1% of detection.

**Worked — which station made the defect?** Station A makes 60% of boards at 2%
defect; Station B makes 40% at 5%. A board is defective — which station?
$$P(\text{def}) = 0.02(0.60) + 0.05(0.40) = 0.032, \qquad
P(B \mid \text{def}) = \frac{0.020}{0.032} = 62.5\%.$$
B makes fewer boards but the larger *share of the defects* — that is where you point the
containment.

### Counting (combinatorics)

- **Factorial:** $n! = n(n-1)\cdots 1$; $0! = 1$.
- **Permutations (order matters):** $P(n,k) = \dfrac{n!}{(n-k)!}$. *Assign 3 priority tests across 8 stations:* $8 \cdot 7 \cdot 6 = 336$.
- **Combinations (order does not):** $\binom{n}{k} = \dfrac{n!}{k!\,(n-k)!}$. *Pull 3 boards from 10 for teardown:* $\binom{10}{3} = 120$.
- **Identity:** $\binom{n}{k} = \binom{n}{n-k}$. **Handy:** $\binom{n}{2} = \tfrac{n(n-1)}{2}$; $\binom{5}{2} = 10$, $\binom{6}{3} = 20$.
- **Multiplication principle:** 4 GPU types $\times$ 3 NVMe $\times$ 2 Network Interface Card (NIC) $=24$ configs.

The binomial coefficient is the $\binom{n}{k}$ that shows up in the binomial
distribution and in every "how many of these $n$ boards fail" calculation below.

### Expected value and variance — and the rule that catches people

$$\mathbb{E}[X] = \sum_i x_i P(x_i), \qquad
\operatorname{Var}(X) = \mathbb{E}[X^2] - (\mathbb{E}[X])^2, \qquad \sigma = \sqrt{\operatorname{Var}(X)}.$$

Properties you will use to roll up multi-stage test times and stacked tolerances:
$$\mathbb{E}[aX + b] = a\,\mathbb{E}[X] + b, \qquad \mathbb{E}[X+Y] = \mathbb{E}[X] + \mathbb{E}[Y]\ (\text{always}),$$
$$\operatorname{Var}(aX + b) = a^2 \operatorname{Var}(X), \qquad \operatorname{Var}(X+Y) = \operatorname{Var}(X) + \operatorname{Var}(Y)\ (\text{independent}).$$

> **Variances add; standard deviations do not.** $\sigma_{X+Y} = \sqrt{\sigma_X^2 + \sigma_Y^2}$, never $\sigma_X + \sigma_Y$. This is the same root-sum-square that governs tolerance stack-ups and uncertainty propagation.

*Three independent test stages $(\mu, \sigma)$: $(5,1), (10,2), (5,1.5)$ min.* Total mean
$= 20$ min; total variance $= 1 + 4 + 2.25 = 7.25$; total $\sigma = 2.69$ min — **not**
$4.5$. If you quote the linear sum you over-budget your test-time spread by 67%.

---

## Distributions and When to Reach for Each

The whole game is matching the physical situation to the right distribution. Pick wrong
and your limits and confidence numbers are wrong. Here is the decision map, then the
formulas.

| Situation on the floor | Distribution | Why |
|---|---|---|
| Pass/fail of one unit | Bernoulli | single trial |
| # passing in a fixed batch of n | Binomial | n fixed, independent, constant p |
| # boards tested until first fail | Geometric | trials to first success |
| # rare events in a fixed window (defects/board, fails/shift, bit errors) | Poisson | rare, independent, constant rate |
| A measured continuous parameter (voltage, temp, time) | Normal | sum of many small effects (CLT) |
| Time between random failures (useful life) | Exponential | constant hazard rate |
| Time to wear-out failure (aging, NAND, fans) | Weibull | shape parameter bends the hazard |

### Binomial — counting failures in a batch

$$P(X = k) = \binom{n}{k} p^{k}(1-p)^{n-k}, \quad
\mathbb{E}[X] = np, \quad \operatorname{Var}(X) = np(1-p).$$

*Test 20 boards, each 95% pass. Exactly 18 pass?*
$\binom{20}{18}(0.95)^{18}(0.05)^{2} = 190(0.3972)(0.0025) = 0.189$ (18.9%).
*All 20?* $0.95^{20} = 0.359$. *At least 18?* $0.189 + 0.377 + 0.359 = 0.925$ (92.5%).
Use the binomial directly for accept-on-zero sampling and for redundancy/k-of-n
reliability (the *Reliability* section).

### Geometric — trials to the first event

$$P(X = k) = (1-p)^{k-1}p, \qquad \mathbb{E}[X] = \tfrac{1}{p}.$$
*Defect rate 3%. Expected boards until a defect?* $1/0.03 = 33.3$. *First defect on the
5th board?* $0.97^{4}(0.03) = 2.7\%$. This is how you reason about "how long until the
next escape" when the rate is steady.

### Poisson — the rare-event distribution behind BER and defect counts

$$P(X = k) = \frac{\lambda^{k} e^{-\lambda}}{k!}, \qquad
\mathbb{E}[X] = \operatorname{Var}(X) = \lambda.$$

Poisson is the most important distribution for a compute-test engineer because **bit
errors are Poisson** (*BER and Confidence*) and **defect counts per board are Poisson** ($c$-charts, *SPC*).
Use it whenever events are rare, independent, and arrive at a roughly constant rate.

*Station averages $\lambda = 2$ failures/shift. $P(0)$ ?* $e^{-2} = 0.135$. *$P(X \ge 5)$ ?*
with $P(0..4) = 0.135, 0.271, 0.271, 0.180, 0.090$ summing to $0.947$, $P(X \ge 5) = 0.053$ —
a $(5-2)/\sqrt{2} = 2.1\sigma$ excursion, worth an investigation.

> **Poisson approximates the binomial** when $n$ is large and $p$ small, with $\lambda = np$. *1000 solder joints at 0.01% each:* $\lambda = 0.1$, $P(\text{board clean}) = e^{-0.1} = 0.905$. At 5000 joints, $\lambda = 0.5$, only $e^{-0.5} = 0.607$ are clean. High-density boards demand tighter process control purely from the joint count.

### Normal — the reference for any measured parameter

$$f(x) = \frac{1}{\sigma\sqrt{2\pi}}\exp\!\left(-\frac{(x-\mu)^2}{2\sigma^2}\right), \qquad
Z = \frac{X - \mu}{\sigma}.$$

**68–95–99.7 rule:** $\mu \pm 1\sigma / 2\sigma / 3\sigma$ holds 68% / 95% / 99.7%. The
$Z$-table is how you turn "how far is the mean from the limit" into a defect fraction —
the bridge to Cpk in *Process Capability and Setting Limits*.

| Z | P(Z < z) | use |
|----:|---------:|---|
| 1.645 | 0.9500 | 95th percentile (one-sided) |
| 1.96 | 0.9750 | two-sided 95% |
| 2.326 | 0.9900 | 99th percentile |
| 2.576 | 0.9950 | two-sided 99% |
| 3.0 | 0.9987 | 1350 ppm one-sided tail |

*PCIe link speed $\sim N(15.98, 0.05^2)$ GT/s, spec $15.8$–$16.2$:* in-spec fraction
$= P(Z<4.4) - P(Z<-3.6) \approx 99.98\%$. Excellent capability (compute the Cpk in *Process Capability and Setting Limits*).

> **Why Normal is the default — the Central Limit Theorem.** Average $n$ independent samples from *any* finite-variance distribution and the sample mean tends to $N(\mu, \sigma^2/n)$ as $n$ grows ($n \ge 30$ is the rule of thumb). Test times are right-skewed by retests, yet *batch averages* are nearly normal — which is exactly what lets $\bar X$ charts and Cpk math work on real, non-normal data. *Times $\mu=20, \sigma=5$ min, batch of 50:* averages are $\approx N(20, 0.707^2)$; a 22-min batch is $Z=2.83$, flag it.

### Exponential — the flat bottom of the bathtub

$$f(x) = \lambda e^{-\lambda x}, \quad P(X > t) = e^{-\lambda t}, \quad \mathbb{E}[X] = \tfrac{1}{\lambda}.$$

Models time between *random* failures during useful life, where the hazard rate is
constant. **Memoryless:** $P(X > s+t \mid X > s) = P(X > t)$ — a unit that has survived
does not "age" in this regime, which is exactly why MTBF is meaningful only here.
*MTBF $= 500$ h $\Rightarrow \lambda = 0.002/$h:* $P(\text{survive 24 h}) = e^{-0.048} = 0.953$;
a 168-h week $= e^{-0.336} = 0.715$.

### Weibull — the wear-out distribution (and bathtub in one equation)

The exponential assumes a constant hazard. Real hardware does not: solder fatigues, NAND
wears, fans seize, capacitors dry out. **Weibull** generalizes the exponential with a
**shape parameter $\beta$** that lets the hazard rate rise or fall:

$$P(X > t) = \exp\!\left[-\left(\tfrac{t}{\eta}\right)^{\beta}\right], \qquad
h(t) = \frac{\beta}{\eta}\left(\frac{t}{\eta}\right)^{\beta - 1},$$

where $\eta$ is the **characteristic life** (the age by which 63.2% have failed) and
$h(t)$ is the instantaneous **hazard rate**. The single parameter $\beta$ tells you
*which region of the bathtub you are in*:

| beta | Hazard trend | Bathtub region | Physical cause |
|---:|---|---|---|
| < 1 | decreasing | infant mortality | latent manufacturing defects |
| = 1 | constant | useful life | random (= exponential exactly) |
| > 1 | increasing | wear-out | fatigue, electromigration, aging |

**Why a test engineer cares.** Plot field/reliability-lab failure times on Weibull
paper, read $\beta$ off the slope. $\beta < 1$ says your escapes are infant mortality —
**burn-in screens them out**, so add or extend burn-in. $\beta > 1$ says wear-out is
arriving — burn-in does nothing; you need a design fix or a life limit. Reading $\beta$
is how you decide whether more screening even helps. *Example:* a fan population fits
$\beta = 2.5$, $\eta = 40{,}000$ h — clearly wear-out, and burn-in would just consume
useful life. By contrast a connector batch fitting $\beta = 0.6$ is begging for a longer
burn-in to flush the weak ones before they ship.

---

## The "Sigma Level" Bridge

Before capability indices, fix the link between *number of sigmas to the nearest limit*
and *defect fraction*. This table is the spine of *Process Capability and Setting Limits* and *Sampling and AQL*.

| Sigma to spec | One-sided tail | ~PPM (one side) | Cpk equivalent |
|---:|---:|---:|---:|
| 1 sigma | 15.866% | 158,655 | 0.33 |
| 2 sigma | 2.275% | 22,750 | 0.67 |
| 3 sigma | 0.135% | 1,350 | 1.00 |
| 4 sigma | 0.0032% | 31.7 | 1.33 |
| 4.5 sigma | 0.00034% | 3.4 | 1.50 |
| 5 sigma | 0.000029% | 0.29 | 1.67 |
| 6 sigma | 0.0000001% | 0.001 | 2.00 |

> **The "Six Sigma = 3.4 PPM" reconciliation.** A perfectly centered $6\sigma$ process has a $0.001$ PPM tail. The famous **3.4 PPM** assumes a long-term **$1.5\sigma$ mean shift** (processes drift over weeks), leaving $4.5\sigma$ of effective margin. That $1.5\sigma$ shift is exactly the Cp-vs-Ppk gap in the worked capability examples — short-term potential minus long-term drift.

---

## Geometry and Spatial Reasoning

Not the daily statistics, but the reference geometry a compute-test engineer reaches for
when a fixture is laid out, a sensor pose has to be checked, or a field-of-view number
has to be sanity-checked against a datasheet. At Zoox the sensors (camera, lidar, radar)
live in 3D space with positions and orientations, so the coordinate-transform and
similar-triangle math below is the same math the perception fixtures use.

### Distance formulas

$$d_{2D}=\sqrt{(x_2-x_1)^2+(y_2-y_1)^2}, \qquad
d_{3D}=\sqrt{(x_2-x_1)^2+(y_2-y_1)^2+(z_2-z_1)^2}.$$

*Camera at $(2.0,0.5,1.8)$ m, lidar at $(0,0,2.1)$. Separation?*
$d=\sqrt{4.0+0.25+0.09}=\sqrt{4.34}=2.083$ m.

**Point to a line (2D).** Line $ax+by+c=0$, point $(x_0,y_0)$:
$$d=\frac{|a x_0 + b y_0 + c|}{\sqrt{a^2+b^2}}.$$
*Reference line $y=2x+1$ (i.e. $2x-y+1=0$), sensor at $(3,4)$:*
$d=\dfrac{|2(3)-4+1|}{\sqrt{5}}=\dfrac{3}{2.236}=1.342$.

### Areas

**Triangle by coordinates (shoelace):**
$$\text{Area}=\tfrac12\bigl|x_1(y_2-y_3)+x_2(y_3-y_1)+x_3(y_1-y_2)\bigr|.$$
*Points $(0,0),(4,0),(2,3)$:* $\tfrac12|0+12+0|=6$.

**Circle:** area $=\pi r^2$, circumference $=2\pi r$.
**Sector** (angle $\theta$ in radians): area $=\tfrac12 r^2\theta$, arc length $=r\theta$.
*A radar that covers a $60^\circ$ cone out to 100 m on a flat plane sweeps*
$\tfrac12 r^2\theta=\tfrac12(100^2)(\pi/3)=5236$ m² — the footprint you reason about when
laying out a calibration target field.

### Coordinate transforms (sensor placement)

Every sensor has a pose relative to the vehicle frame; you transform between frames with a
rotation then a translation.

**2D rotation by $\theta$:**
$$x'=x\cos\theta - y\sin\theta,\qquad y'=x\sin\theta + y\cos\theta.$$
*Object at $(5,3)$ in the sensor frame, sensor rotated $30^\circ$* ($\cos=0.866,\sin=0.5$):
$x'=5(0.866)-3(0.5)=2.83$, $y'=5(0.5)+3(0.866)=5.098$. If the sensor sits at $(1.5,0.8)$ in
the vehicle frame, add the offset: vehicle-frame position $=(4.33,5.898)$.

### Trigonometry quick reference

| Function | SOH-CAH-TOA | Key values at 0,30,45,60,90 deg |
|---|---|---|
| sin | opp/hyp | 0, 0.5, 0.707, 0.866, 1 |
| cos | adj/hyp | 1, 0.866, 0.707, 0.5, 0 |
| tan | opp/adj | 0, 0.577, 1, 1.732, inf |

- **Pythagoras (right triangles):** $a^2+b^2=c^2$.
- **Law of cosines (any triangle):** $c^2=a^2+b^2-2ab\cos C$ (reduces to Pythagoras at $C=90^\circ$).
- **Radians:** $360^\circ=2\pi$ rad; $\text{rad}=\text{deg}\times\pi/180$.

### Regular polygons — the hexagon pattern

Working *backwards* from area to side length is the geometry move that recurs whenever a
target tile, a panel cell, or a packing layout is specified by area rather than dimension.

**Equilateral triangle** (the building block), side $s$: height $h=\tfrac{\sqrt3}{2}s$,
area $=\tfrac{\sqrt3}{4}s^2$. Quick: $s{=}1\to0.433$, $s{=}2\to1.732$, $s{=}10\to43.3$.

**Regular hexagon** $=$ six equilateral triangles around a center: perimeter $=6s$,
$$\text{Area}=6\cdot\tfrac{\sqrt3}{4}s^2=\tfrac{3\sqrt3}{2}s^2\approx 2.598\,s^2,
\qquad \text{interior angle}=120^\circ.$$

**Working backwards:** given area $A$, each triangle is $A/6$, so
$$\tfrac{A}{6}=\tfrac{\sqrt3}{4}s^2 \;\Rightarrow\; s^2=\frac{2A}{3\sqrt3}=\frac{2A\sqrt3}{9}
\;\Rightarrow\; s=\sqrt{\frac{2A\sqrt3}{9}}, \qquad \text{Perimeter}=6s.$$
*Worked: $A=100$.* $s^2=2(100)(1.732)/9=38.49$, $s=6.204$, perimeter $=37.2$.
Check: $2.598(6.204^2)=99.99$. Correct.

Other regular polygons: square area $=s^2$ ($P{=}4s$); pentagon $\approx1.72\,s^2$;
octagon $=2(1+\sqrt2)s^2\approx4.828\,s^2$.

### Angular size and similar triangles — the FOV math

A camera's field of view is a similar-triangles problem, and so is any "how big does a
target need to be at range $R$" question on a perception fixture.

**Small-angle approximation** (angle $\lesssim 15^\circ$):
$$\theta\;(\text{rad})\approx\frac{\text{physical size}}{\text{distance}}
\quad\Longleftrightarrow\quad \text{distance}\approx\frac{\text{physical size}}{\theta}.$$

**Similar triangles:** if a near object (size $d_1$ at distance $D_1$) just covers a far
object (size $d_2$ at distance $D_2$), then $\tfrac{d_1}{D_1}=\tfrac{d_2}{D_2}$, so
$D_2=d_2\,\tfrac{D_1}{d_1}$.

**Camera FOV.** A 50 mm lens on a 36 mm-wide sensor:
$$\text{FOV}=2\arctan\!\frac{36}{2(50)}=2\arctan(0.36)=2(19.8^\circ)\approx 39.6^\circ.$$
At 100 m it covers $2(100)\tan(19.8^\circ)=72$ m of width — the kind of number you check a
camera-aiming fixture against. For a lidar or radar the same arc-length relation
($\text{width}=2R\tan(\text{half-FOV})$) sizes the target board at a given standoff.

### 3D spatial counting

Organized counting beats guessing: categorize by direction, then sum. In a
$3\times3\times3$ grid the count of collinear triples (the same bookkeeping you use to
enumerate paths or positions in a 3D array of sensors or test points):

```text
Axis-aligned (parallel to an axis):
  X-direction: 3 (y) * 3 (z) = 9
  Y-direction: 3 (x) * 3 (z) = 9
  Z-direction: 3 (x) * 3 (y) = 9        subtotal 27
Face diagonals (within a 2D plane):
  XY planes: 2 diag * 3 layers = 6
  XZ planes: 2 diag * 3 layers = 6
  YZ planes: 2 diag * 3 layers = 6        subtotal 18
Space diagonals (corner to opposite corner): 4
                                          TOTAL 49
```

**General $n^3$ formula:** $3n^2+6n+4$; for $n=3$, $49$. The center cell lies on 13 lines,
corners on 7, edges on 4 — a consistency check on the count.

---

## Algebra and Signal Math

Practical algebra: log and dB scales, SNR, and the signaling-rate math you actually do on
a high-speed link. The GT/s → GB/s conversion below is the bridge between the BER work in
*BER and Confidence* and the bandwidth numbers quoted on a PCIe/NVMe spec sheet.

### Logarithms and decibels

A **decibel** is a log ratio. For **power**, $10\log_{10}$; for **amplitude/voltage**,
$20\log_{10}$ (because power $\propto V^2$, and $\log V^2 = 2\log V$):
$$\text{dB}_\text{power}=10\log_{10}\frac{P_1}{P_2}, \qquad
\text{dB}_\text{amp}=20\log_{10}\frac{V_1}{V_2}.$$

Memorize these and you can do most dB arithmetic in your head:

| Ratio | Power dB | Note |
|---:|---:|---|
| x2 | +3.01 | "3 dB = double the power" |
| x10 | +10 | one decade |
| x100 | +20 | two decades |
| x0.5 | -3 | half power |
| x1000 | +30 | |

dB **add** when ratios multiply: a x20 power gain is x2 then x10, so $3+10=13$ dB.
*A 30 dB amplifier on a 1 mW input:* $30/10=3$ decades $\Rightarrow$ x1000 $\Rightarrow$ 1 W.

**Log scales (Bode/decades).** A decade is x10 in frequency; an octave is x2. A
first-order low-pass rolls off $-20$ dB/decade ($=-6$ dB/octave) above its corner
$f_c=\tfrac{1}{2\pi RC}$.

### Signal-to-noise ratio

$$\text{SNR}=\frac{P_\text{signal}}{P_\text{noise}}, \qquad
\text{SNR}_\text{dB}=10\log_{10}\frac{P_\text{signal}}{P_\text{noise}}.$$
*3.3 V signal, 10 mV RMS noise* (amplitude ratio, so use $20\log$):
$\text{SNR}=3.3/0.01=330\Rightarrow 20\log_{10}330=50.4$ dB. (As a power ratio $330^2$,
$10\log_{10}(330^2)$ gives the same 50.4 dB — the two forms agree because a voltage ratio
of 330 *is* a power ratio of $330^2$.) On a SerDes link a higher SNR margin is what shows
up downstream as the lower BER you confidence-test in *BER and Confidence*.

### Bandwidth math: GT/s to GB/s

High-speed serial links quote a **raw symbol rate** in GT/s (giga-transfers per second).
Usable data rate is lower because of **line coding** overhead. The two encodings to know:

- **8b/10b** (PCIe Gen1/2, USB 3.0, SATA, older SerDes): 10 line bits carry 8 data bits, efficiency $=8/10=80\%$.
- **128b/130b** (PCIe Gen3/4/5/6): 130 line bits carry 128 data bits, efficiency $=128/130=98.46\%$.

Convert raw rate to usable bytes/s per lane:
$$\text{GB/s per lane}=\frac{\text{GT/s}\times\text{coding efficiency}}{8\ \text{bits/byte}}.$$

| PCIe Gen | GT/s | Coding | Bytes/s per lane | x16 (GB/s) |
|---|---:|---|---:|---:|
| 1 | 2.5 | 8b/10b | 0.25 | 4.0 |
| 2 | 5.0 | 8b/10b | 0.50 | 8.0 |
| 3 | 8.0 | 128b/130b | 0.985 | 15.75 |
| 4 | 16.0 | 128b/130b | 1.969 | 31.5 |
| 5 | 32.0 | 128b/130b | 3.938 | 63.0 |
| 6 | 64.0 | PAM4 + FEC | 7.563 | 121 |

*Worked: PCIe Gen4 x4 NVMe usable rate.* $16\times(128/130)/8=1.969$ GB/s per lane
$\times4=7.88$ GB/s. (Gen6 switches to Pulse Amplitude Modulation 4-level (PAM4) — 2 bits/symbol — plus Fixed-size Link Packet (FLIT)-mode Forward Error Correction (FEC), so a
"transfer" no longer equals one bit; the table value already accounts for the encoding.
This is also why Gen6 BER acceptance in *BER and Confidence* tests the *post-FEC* error count.)

### Averaging to reduce noise

Averaging $N$ independent readings of the same quantity reduces the random-noise standard
deviation by $\sqrt{N}$ (means add as $N$, noise variance adds as $N$, so $\sigma$ of the
mean scales as $1/\sqrt{N}$):
$$\sigma_\text{avg}=\frac{\sigma_\text{single}}{\sqrt{N}}.$$
*$\sigma=0.5^\circ$C/reading, average 25:* $\sigma_\text{avg}=0.5/5=0.1^\circ$C. To cut
noise 10x you need 100x the samples — diminishing returns, and the direct argument for
fixing a noisy gauge (the *Gauge R&R* section) rather than averaging around it.

### Propagation of uncertainty

$$f=a\pm b:\quad \sigma_f=\sqrt{\sigma_a^2+\sigma_b^2}\ \ (\text{add in quadrature}),$$
$$f=a\cdot b\ \text{or}\ a/b:\quad \frac{\sigma_f}{f}=\sqrt{\left(\tfrac{\sigma_a}{a}\right)^2+\left(\tfrac{\sigma_b}{b}\right)^2}\ \ (\text{relative, in quadrature}).$$
*$P=VI$, $V=48.0\pm0.2$, $I=10.0\pm0.1$, $P=480$ W:*
$\tfrac{\sigma_P}{P}=\sqrt{(0.2/48)^2+(0.1/10)^2}=0.0108$,
so $\sigma_P=480\times0.0108=\pm5.2$ W. This is the same root-sum-square that governs
tolerance stack-ups and the variance addition in *Expected value and variance*.

### Sampling and aliasing (Nyquist)

To capture a signal of frequency $f$ you must sample at $f_s\ge 2f$ (the Nyquist rate);
below that the signal **aliases** to a false lower frequency. *Vibration content to 500
Hz:* sample at $\ge 1$ kHz; in practice use 2.5x (~1.25 kHz) for margin against
imperfect anti-alias filtering. The same rule sets the minimum scan/strobe rate when a
moving part is sampled on a line — sample a 1000 rev/min fan below 33 Hz and a strobed
camera will show it crawling backward (a temporal alias), the rotational version of the
same trap.

---

## Vectors and Linear Algebra Basics

Sensor data is vector-valued — camera pixels, lidar point clouds, force/torque readings —
and sensor calibration is matrix algebra. Awareness level: enough to read a calibration
routine and reason about a pose.

### Vectors

A vector has magnitude and direction: $\mathbf v=(v_x,v_y,v_z)$.

- **Magnitude:** $|\mathbf v|=\sqrt{v_x^2+v_y^2+v_z^2}$.
- **Unit vector:** $\hat{\mathbf v}=\mathbf v/|\mathbf v|$.
- **Addition:** component-wise (tip-to-tail). **Scalar multiply:** scales length.

### Dot product

$$\mathbf a\cdot\mathbf b=a_1b_1+a_2b_2+a_3b_3=|\mathbf a|\,|\mathbf b|\cos\theta.$$
Uses: angle $\cos\theta=\tfrac{\mathbf a\cdot\mathbf b}{|\mathbf a||\mathbf b|}$; projection
of $\mathbf a$ onto $\mathbf b$ is $\tfrac{\mathbf a\cdot\mathbf b}{|\mathbf b|}$;
$\mathbf a\cdot\mathbf b=0\iff$ perpendicular.
*$\mathbf a=(3,4),\mathbf b=(4,-3)$:* $\mathbf a\cdot\mathbf b=12-12=0\Rightarrow$ perpendicular.
*$\mathbf a=(1,0),\mathbf b=(1,1)$:* $\cos\theta=1/\sqrt2=0.707\Rightarrow\theta=45^\circ$.

### Cross product (3D)

$$\mathbf a\times\mathbf b=(a_2b_3-a_3b_2,\ a_3b_1-a_1b_3,\ a_1b_2-a_2b_1),\qquad
|\mathbf a\times\mathbf b|=|\mathbf a||\mathbf b|\sin\theta.$$
The result is perpendicular to both. Uses: parallelogram area $=|\mathbf a\times\mathbf b|$;
triangle area $=\tfrac12|\mathbf a\times\mathbf b|$; surface normal from two edge vectors;
and shortest point-to-line distance in 3D, $\tfrac{|\mathbf{AP}\times\mathbf d|}{|\mathbf d|}$.

### Matrices (awareness level)

Matrix multiply: $(m\times n)(n\times p)=(m\times p)$, row-by-column dot products; order
matters ($AB\ne BA$ in general). **2D rotation matrix:**
$$R(\theta)=\begin{bmatrix}\cos\theta & -\sin\theta\\ \sin\theta & \cos\theta\end{bmatrix}.$$
A full sensor-to-vehicle transform is a $4\times4$ matrix combining rotation and
translation (homogeneous coordinates); calibration is the process of solving for those
matrices.

---

## Core Physical Relationships

A brief reference for the physics that backs the fixtures and the thermal/power test
conditions. Reconstruct these from first principles rather than recall them blind.

| Relationship | Formula | Floor use |
|---|---|---|
| Force / pressure / area | F = P * A  [lbf]=[psi]*[in^2] | linear / pneumatic fixture force |
| Ohm's law and power | V = I*R ; P = V*I = I^2*R = V^2/R | supply sizing, heat load |
| Torque | tau = F * d (moment arm) | air-brake / load-cell fixtures |
| Heat | Q = m*c*dT | thermal soak energy budget |

- **Force.** *290 psi regulator, 20 in² piston cap:* $F=290\times20=5800$ lbf. The rod side has a smaller effective area (piston minus rod cross-section), so the same pressure makes *less* force in tension than in compression.
- **Power.** *Supply at 48 V, 25 A:* $P=1200$ W — hence active cooling on a high-power compute board.
- **Torque.** *Load cell, 6.6 in arm, 18.2 lbf:* $\tau=18.2\times(6.6/12)=10.0$ ft-lb. An air-brake fixture fits $\tau=\text{slope}\cdot\text{PSI}+\text{intercept}$, slope set by $\mu\cdot(\text{pad area})\cdot(\text{eff. radius})$ — a regression fit (*Regression and Correlation*) turned into a test limit.
- **Heat.** Thermal testing matters because at $-40^\circ$C switching is slower (higher $V_\text{th}$) and at $+85^\circ$C leakage grows and noise margins shrink — the extremes that catch timing and power-margin defects, and the basis for the burn-in and Arrhenius math in the *Reliability* section.

---

## BER and Confidence — the PCIe BERT Math

This is the centerpiece for a compute/high-speed-link test engineer, and the statistical
core of the PCIe BERT tool. The interview and the job both ask the same thing: **"You
ran a SerDes/PCIe link for $N$ bits and saw $E$ errors — what BER can you claim, and how
long must you run to prove a target?"** The link-layer detail lives in the **PCIe
chapter**; the statistics live here.

### Why bit errors are Poisson

Errors on a healthy link are rare and effectively independent, so the error count over
$n$ transmitted bits is **Poisson** with mean $\lambda = n p$, where $p$ is the true bit
error ratio (BER). A BERT runs a known pattern (PRBS) and counts mismatches; that count
is your Poisson observation.

**Confidence level (CL)** = the probability that, *if the true BER were as bad as your
target $p$*, you would have seen *more than* the $E$ errors you observed. High CL means a
truly bad link would almost certainly have shown more errors than you saw — so a clean
run is strong evidence the real BER is below the target.

$$\boxed{\ \text{CL} = 1 - \text{PoissonCDF}(E;\, np) = 1 - \sum_{k=0}^{E}\frac{(np)^k e^{-np}}{k!}\ }$$

> **Frame it correctly.** CL is *not* "the probability the link is good." It is the probability a link *at the target BER* would have produced at least $E+1$ errors. Stating it the wrong way in a design review is a credibility tell.

### The zero-error case — the "3/BER" rule you know cold

Most acceptance runs target zero errors. With $E = 0$ the sum collapses:
$$\text{CL} = 1 - e^{-np} \ \Longrightarrow\ np = -\ln(1 - \text{CL})
\ \Longrightarrow\ \boxed{\ n = \frac{-\ln(1 - \text{CL})}{p}\ } \quad (E = 0).$$

Memorize the constant $-\ln(1-\text{CL})$:

| CL | -ln(1-CL) |
|---|---:|
| 90% | 2.303 |
| 95% | 2.996 |
| 99% | 4.605 |
| 99.9% | 6.908 |

**The rule of thumb:** for 95% confidence with zero errors you need about **$3/p$ bits**;
for 99%, about $4.6/p$. This is the bit-domain twin of the *rule of three* — with 0
failures in $n$ trials, the upper 95% bound on the failure rate is $\approx 3/n$.

**Worked — bits to prove BER $\le 10^{-12}$ at 95% CL, zero errors.**
$$n = \frac{2.996}{10^{-12}} = 2.996 \times 10^{12}\ \text{bits} \approx 3 \times 10^{12}.$$
On one PCIe Gen5 lane at $32$ GT/s (use the raw $32 \times 10^{9}$ bit/s for the link
test): $t = 3 \times 10^{12} / 32 \times 10^{9} \approx 94$ s. So a ~95-second clean run
on one lane proves $\le 10^{-12}$ at 95% CL. For 99% CL scale by $4.605/2.996 = 1.54$ →
~145 s.

**Worked — what BER did I prove?** A run of $n = 10^{13}$ bits with $E = 0$:
$$p_{95} = \frac{2.996}{10^{13}} = 3.0 \times 10^{-13}.$$
You have demonstrated BER $\le 3.0 \times 10^{-13}$ at 95% confidence.

### Allowing observed errors — the chi-squared upper bound

If you saw $E > 0$ errors and still want a confidence-bounded BER, the exact one-sided
upper limit on a Poisson mean is a clean chi-squared expression:
$$\boxed{\ \text{BER}_\text{upper} = \frac{\chi^2_{1-\alpha,\ 2(E+1)}}{2n}\ }$$
where $\chi^2_{q,\nu}$ is the $q$-quantile with $\nu$ degrees of freedom. This is the
form lab software and the Telcordia/standards methods use. Two checks that it is the
right formula:

- For $E = 0$: $\nu = 2$, and $\chi^2_{1-\alpha,2} = -2\ln(\alpha) = -2\ln(1-\text{CL})$, so $\text{BER}_\text{upper} = -\ln(1-\text{CL})/n$ — **identical** to the zero-error "3/BER" rule.
- It is exact for any $E$, where the normal approximation $\hat p \pm z\sqrt{\hat p/n}$ falls apart in the small-count, low-$p$ regime BER lives in.

**Worked — $E = 2$ errors at 95% CL.** $n = 2 \times 10^{12}$, $\nu = 2(2+1) = 6$,
$\chi^2_{0.95,6} = 12.59$:
$$\text{BER}_\text{upper} = \frac{12.59}{2(2 \times 10^{12})} = 3.15 \times 10^{-12}.$$
Even with 2 errors you can claim BER $\le 3.15 \times 10^{-12}$ at 95% (the point estimate
is only $E/n = 10^{-12}$; the bound is higher because 2 errors is a tiny sample). Useful
$\chi^2_{0.95,\nu}$: $\nu=2{:}\,5.99$, $4{:}\,9.49$, $6{:}\,12.59$, $8{:}\,15.51$,
$10{:}\,18.31$. For 99% CL ($\alpha=0.01$): $\nu=2{:}\,9.21$, $4{:}\,13.28$, $6{:}\,16.81$.

### The CL-given-errors check

Going the other way — you fixed the run length and want the confidence achieved — use
the CDF form. *Target $p = 10^{-12}$, ran $n = 3 \times 10^{12}$ bits ($np = 3$), saw
$E = 1$:*
$$\text{CL} = 1 - [e^{-3} + 3e^{-3}] = 1 - 4e^{-3} = 1 - 0.199 = 0.801.$$
Only **80% CL** — one error in $3 \times 10^{12}$ bits does *not* clear $10^{-12}$ at
95%; run longer. To reach 95% with $E=1$, solve $1 - (1+np)e^{-np} = 0.95$ to get
$np = 4.74$, i.e. $n = 4.74 \times 10^{12}$ bits.

### Sequential test — pass / continue / reject

A fixed-$n$ acceptance run wastes time: a great link passes long before $n$, and a dead
link should be rejected almost immediately. **Wald's Sequential Probability Ratio Test
(SPRT)** evaluates after every error (or every block) and emits one of three verdicts —
**pass**, **continue**, or **reject** — drawing two parallel boundary lines in the
(bits, cumulative-errors) plane. This is what a good BERT does instead of always running
to the bitter end.

The test discriminates between an acceptable BER $p_0$ and a rejectable BER $p_1 > p_0$,
with producer's risk $\alpha$ (reject a good link) and consumer's risk $\beta$ (accept a
bad one). Plot cumulative errors $E$ against transmitted bits $n$; the two decision lines
are parallel with the same slope:

```text
slope  s = (p1 - p0) / ln(p1/p0)
accept (PASS) line:  E = s*n - h_a,   h_a = ln((1-a)/b)   / ln(p1/p0)
reject line:         E = s*n + h_r,   h_r = ln((1-b)/a)   / ln(p1/p0)

  E  cumulative errors
  ^                              . reject region (above)
  |                       ______/  reject line
  |                  ____/    ___/
  |  continue  _____/    ____/   accept line
  |       ____/     ____/
  | _____/     ____/   accept region (below)
  +----------------------------------> n bits
```

**Decision each step:** if $E$ crosses *above* the reject line, **stop and fail**; if it
stays *below* the accept line as $n$ grows, **stop and pass**; in between, **keep
running**. The payoff: a clean link earns its PASS in a fraction of the fixed-$n$ time,
and a marginal link gets caught early instead of soaking a station for 40 hours.

**Worked — SPRT boundaries for a PCIe lane.** Discriminate an acceptable $p_0 = 10^{-12}$
from a rejectable $p_1 = 10^{-11}$ (10x worse) with $\alpha = 0.05$, $\beta = 0.10$. Here
$\ln(p_1/p_0) = \ln 10 = 2.303$, so the slope is
$s = (10^{-11} - 10^{-12})/2.303 = 3.91\times10^{-12}$ errors/bit, with intercepts
$h_a = \ln(0.95/0.10)/2.303 = 0.978$ and $h_r = \ln(0.90/0.05)/2.303 = 1.255$. A perfectly
clean link ($E = 0$) crosses the accept line at $n = h_a/s = 2.5\times10^{11}$ bits — about
**8 s** on a $32$ GT/s lane, versus ~94 s for the fixed-$n$ 95% run, a ~12x time saving on
good links. A truly bad link ($\gg p_1$) accumulates errors fast and trips the reject line
($h_r \approx 1.3$, so as soon as a couple of early errors land) in seconds rather than
soaking the full run.

The tradeoff is variable test time — fine for engineering bring-up and margining, less
ideal for a fixed-takt production line where you usually pin the run length with the
zero-error "3/BER" rule instead.

### Practical notes (cross-ref the PCIe chapter)

- **Per-lane vs aggregate.** A x16 link is 16 lanes; testing them in parallel feels like a 16x speedup, and for *time* it is — to prove $10^{-12}$ at 95% CL you still need $3 \times 10^{12}$ bits **on each lane**, but 16 lanes deliver those bits simultaneously, so the ~94 s single-lane run covers all 16 at once. The trap is in the *accounting*: if you pool the 16 lanes' errors and divide by the aggregate bit count, you prove only the *aggregate* BER, which hides a single sick lane. Concretely, one lane running at $10^{-11}$ (10x over spec) alongside 15 clean lanes at $10^{-13}$ gives a pooled BER of $(10^{-11} + 15 \times 10^{-13})/16 \approx 7.2 \times 10^{-13}$ — under $10^{-12}$, so the aggregate **passes** while a lane is an order of magnitude out. Always **count errors per lane and margin the worst one**; report per-lane BER, never the link average.
- **Targets.** PCIe Gen1–5 spec raw BER $\le 10^{-12}$; **Gen6 (PAM4)** relaxes the *raw* target to $\le 10^{-6}$ and leans on **FEC** for an effective post-FEC BER $\le 10^{-12}$ — so for Gen6 you confidence-test the *post-FEC* error count, not the raw symbol errors. Note what the relaxed raw target does to run time: at $p = 10^{-6}$ the zero-error 95% run is only $n = 2.996/10^{-6} \approx 3 \times 10^{6}$ bits — microseconds — so the meaningful Gen6 acceptance run is the *post-FEC* one against the $10^{-12}$ effective target, back to the ~minutes-per-lane regime.

```text
BERT cheat-sheet (zero-error acceptance, single lane)
  bits needed:  n = -ln(1 - CL) / p
  time:         t = n / line_rate_bits_per_sec
  proven BER:   p = -ln(1 - CL) / n         (from a completed clean run)
  with errors:  BER_upper = chi2(1-alpha, 2*(E+1)) / (2*n)
  CL achieved:  CL = 1 - PoissonCDF(E; n*p)
  -ln(1-CL):    CL=90% ->2.303   95% ->2.996   99% ->4.605   99.9% ->6.908
```

---

## Process Capability and Setting Limits

Capability indices compare the **voice of the process** (its spread) to the **voice of
the customer** (the spec width). They answer the question every limit review asks: *given
how this parameter actually behaves, will the spec line scrap good units or pass bad
ones?*

### Descriptive stats — and why n-1

For a sample $x_1, \dots, x_n$:
$$\bar x = \frac{1}{n}\sum x_i, \qquad s^2 = \frac{1}{n-1}\sum (x_i - \bar x)^2, \qquad s = \sqrt{s^2}.$$

> **Why $n-1$ (Bessel's correction)?** Deviations are taken from the *sample* mean, which is itself pulled toward the data, so dividing by $n$ underestimates spread. $n-1$ (the degrees of freedom) makes $s^2$ unbiased. Use $n$ only for a full population — test-floor data is always a sample, so always $n-1$.

The mean chases outliers; a single stuck reading moves $\bar x$ but barely moves the
median. Watch for that when a fixture glitches.

### The four indices

- **Cp / Cpk** use **short-term / within-subgroup** sigma $\hat\sigma_\text{ST}$, estimated from a control chart as $\hat\sigma = \bar R / d_2$. These describe process *potential* when stable.
- **Pp / Ppk** use the **overall / long-term** sample $s$ across all data. These describe *actual* performance including drift.

$$C_p = \frac{\text{USL} - \text{LSL}}{6\,\hat\sigma_\text{ST}}, \qquad
C_{pk} = \min\!\left(\frac{\text{USL} - \mu}{3\,\hat\sigma_\text{ST}},\ \frac{\mu - \text{LSL}}{3\,\hat\sigma_\text{ST}}\right),$$
$$P_p = \frac{\text{USL} - \text{LSL}}{6\,s}, \qquad
P_{pk} = \min\!\left(\frac{\text{USL} - \mu}{3\,s},\ \frac{\mu - \text{LSL}}{3\,s}\right).$$

**How to read them:**

- $C_p$ ignores centering — the *best you could do* if perfectly centered.
- $C_{pk} \le C_p$ always; the gap is the **centering penalty**, zero only when $\mu$ sits midway between the limits.
- $C_{pk} \gg P_{pk}$ means capable short-term but **drifting** between subgroups — chase the special cause (tool wear, shift-to-shift, ambient temperature), do not tighten the machine.
- One-sided spec (e.g. a max-temperature limit): use only the relevant half; $C_p$ is undefined.

| Cpk | Margin | One-sided defect | ~PPM | Verdict |
|---:|---:|---:|---:|---|
| 1.00 | 3 sigma | 0.135% | 1,350 | minimum / marginal |
| 1.33 | 4 sigma | 0.0032% | 31.7 | industry "capable" floor |
| 1.67 | 5 sigma | 0.000029% | 0.29 | strong |
| 2.00 | 6 sigma | ~1e-7 % | 0.001 | world-class |

### Worked capability examples

**One-sided (GPU temperature).** USL $= 85^\circ$C, no LSL, $\mu = 72$, $\sigma = 3$.
$C_{pk} = (85-72)/9 = 1.44$ — good; ~7 PPM exceed $85^\circ$C. Drift to $\mu = 76$:
$C_{pk} = 9/9 = 1.00$ — marginal, ~1,350 PPM. A $4^\circ$ mean shift (just over one sigma)
moved the escape rate ~184×. The leverage is brutal because the defect rate lives in the
*tail* of the normal: out there the curve is dropping near-exponentially, so a shift
measured in fractions of a sigma multiplies the escape count by orders of magnitude. This
is why thermal limits get guardbanded and why a slow $\bar X$ drift (caught by the SPC
charts in *SPC*) is worth chasing long before any single board
fails.

**Two-sided, off-center.** Spec $45$–$55$, $\mu = 50$, $\sigma = 2$:
$C_p = 10/12 = 0.833$, centered so $C_{pk} = 0.833$ — not capable; out-of-spec
$= 2P(Z > 2.5) = 1.24\%$. Shift to $\mu = 52$: $C_p$ unchanged, but
$C_{pk} = \min(0.5, 1.167) = 0.5$ — the centering penalty halved the index.

**Cpk vs Ppk (drift).** Within-subgroup $\hat\sigma_\text{ST} = 0.10$ but overall $s = 0.18$
because the mean wanders. With $\mu = 10.0$, USL $= 10.5$, LSL $= 9.5$:
$C_{pk} = 0.5/0.30 = 1.67$ but $P_{pk} = 0.5/0.54 = 0.93$. The process *could* be
world-class; right now it is below the capable floor. The fix is killing the
between-subgroup drift, not tightening the tool.

> **Cpk to PPM, fast.** $\text{PPM} \approx 10^{6}[\,P(Z > 3C_{pk,\text{upper}}) + P(Z < -3C_{pk,\text{lower}})\,]$. One-sided/symmetric: $\text{PPM} \approx 10^{6}(1 - \Phi(3C_{pk}))$. Memorize $3 \times 1.33 = 4\sigma \to 32$ ppm and $3 \times 1.67 = 5\sigma \to 0.3$ ppm and you can eyeball any Cpk.

### Setting limits and guardbands

A spec line is not just the customer number — it must account for *your measurement
uncertainty* so you do not pass parts that are actually out, or scrap parts that are
actually in.

- **Spec limits** come from the customer / design (the LSL/USL).
- **Control limits** come from the *process* and go on the SPC chart — never put spec limits on a control chart (*SPC*).
- **Test (guardband) limits** are the lines your tester actually uses, pulled *inside* the spec by a **guardband** $g$ to protect against gauge error:
$$\text{upper test limit} = \text{USL} - g, \qquad \text{lower test limit} = \text{LSL} + g.$$

A defensible guardband is tied to measurement uncertainty: a common rule is
$g = k \cdot U$ where $U$ is the gauge's expanded uncertainty (often the GR&R standard
deviation times a coverage factor), with $k$ chosen for the risk you will tolerate. The
tradeoff is direct and unavoidable:

| Guardband | Effect on escapes | Effect on yield (false rejects) |
|---|---|---|
| Wider (limits pulled in more) | fewer bad parts pass | more good parts scrapped |
| Narrower (toward spec) | more escapes | fewer good parts scrapped |

*Example.* USL $= 85^\circ$C, gauge GR&R $\sigma = 0.5^\circ$C. A $2\sigma$ guardband
$g = 1.0^\circ$C sets the **test limit at $84.0^\circ$C** — you fail anything reading
above 84 to be 95%-confident the true value is under 85. You knowingly scrap a sliver of
good parts between 84 and 85 to drive the escape rate down. That sliver is the price of a
noisy gauge — which is the direct argument for the *Gauge R&R* section: fix the gauge and you can move the
guardband back out and recover yield.

---

## Statistical Process Control (SPC)

Capability is a snapshot; SPC is the movie. A control chart plots a statistic over time
against a centerline (CL) and control limits (UCL/LCL) set at $\pm 3\sigma$ **of the
plotted statistic** — derived from the process, not the spec. A point outside the limits,
or any rule trip, signals an **assignable cause**: stop, investigate, correct. Random
scatter inside the limits is **common cause** — do *not* react to it (over-adjusting an
in-control process *adds* variance; Deming's funnel).

### Variables charts (measurements)

Run in pairs — one for location, one for spread:

- **$\bar X$ and $R$** (subgroup means and ranges, $n = 2$–~9): $$\text{UCL/LCL}_{\bar X} = \bar{\bar X} \pm A_2 \bar R, \quad \text{UCL}_R = D_4 \bar R, \quad \text{LCL}_R = D_3 \bar R, \quad \hat\sigma = \bar R / d_2.$$
- **$\bar X$ and $s$** (preferred for $n \gtrsim 10$): $\hat\sigma = \bar s / c_4$, with $B_3, B_4$ for the $s$-chart limits.
- **I-MR** (individuals + moving range) when $n = 1$ — one expensive board per run: $\hat\sigma = \overline{MR}/d_2$ with $d_2 = 1.128$.

| n | A2 | D3 | D4 | d2 | c4 |
|---:|---:|---:|---:|---:|---:|
| 2 | 1.880 | 0 | 3.267 | 1.128 | 0.7979 |
| 3 | 1.023 | 0 | 2.574 | 1.693 | 0.8862 |
| 4 | 0.729 | 0 | 2.282 | 2.059 | 0.9213 |
| 5 | 0.577 | 0 | 2.114 | 2.326 | 0.9400 |

*Worked $\bar X / R$.* $n = 5$, $\bar{\bar X} = 20.0$ min, $\bar R = 1.2$:
$\text{UCL}_{\bar X} = 20 + 0.577(1.2) = 20.69$, $\text{LCL} = 19.31$;
$\text{UCL}_R = 2.114(1.2) = 2.54$; $\hat\sigma = 1.2/2.326 = 0.516$ min — and *that*
$\hat\sigma$ is the short-term sigma you feed into Cp/Cpk.

### Attribute charts (counts / pass-fail)

- **$p$-chart** — fraction defective, variable subgroup size: limits $\bar p \pm 3\sqrt{\bar p(1-\bar p)/n}$.
- **$np$-chart** — number defective, fixed subgroup size.
- **$c$-chart** — defect *count* per unit, constant area of opportunity (Poisson): limits $\bar c \pm 3\sqrt{\bar c}$.
- **$u$-chart** — defects per unit, variable area.

The $\sqrt{\bar c}$ in the $c$-chart is the Poisson standard deviation from the Poisson distribution — the
same math that flags a bad-solder excursion on a board.

### CUSUM and EWMA — catching small, slow shifts

Shewhart $\pm 3\sigma$ limits are fast on big jumps but slow on small sustained drifts
(a $1\sigma$ shift can take ~40 points to trip). Two charts fix that by remembering
history:

- **CUSUM** accumulates the running sum of deviations from target; a small persistent bias builds a visible slope long before any single point goes out. Best for detecting a known shift size quickly.
- **EWMA** plots an exponentially weighted moving average $z_t = \lambda x_t + (1-\lambda)z_{t-1}$ (typically $\lambda = 0.2$); it smooths noise and reacts to gradual drift while staying robust to non-normality.

Use these on a mature, stable parameter where the failure mode is slow drift (tool wear,
calibration creep), not sudden breakage.

### Western Electric / Nelson rules

$\pm 3\sigma$ alone misses patterns. The WE rules add sensitivity by zone — **A** =
2–3σ, **B** = 1–2σ, **C** = 0–1σ:

```text
WE Rule 1: any 1 point beyond 3 sigma                      -> large shift / outlier
WE Rule 2: 2 of 3 consecutive in Zone A or beyond, same side-> moderate shift
WE Rule 3: 4 of 5 consecutive in Zone B or beyond, same side-> small sustained shift
WE Rule 4: 8 consecutive points on one side of CL          -> mean has shifted
```

Common Nelson additions: 6 steadily rising/falling (trend — tool wear); 14 alternating
(over-control or two interleaved streams); 15 in Zone C (unnaturally low spread — often a
*measurement* problem or wrong limits).

> **The false-alarm tradeoff.** Rule 1 alone gives $\alpha \approx 0.0027$ (1 in 370 points). Stacking all rules pushes the combined false-alarm rate past ~1%, so pick a rule set deliberately. On a stable, mature line many teams run Rules 1, 2, 3, and the 8-in-a-row.

### Part Average Testing (PAT) — outlier screening

SPC watches the *process*; **Part Average Testing (PAT)** watches the *part*. PAT is an
outlier screen that flags a unit which passes every spec limit but sits far from its
peers — the classic latent-defect signature on a compute board (a marginal solder joint,
a leaky cap, a device drawing slightly more current than the population). The unit is "in
spec but not like its neighbors," and on a safety-critical platform that is exactly the
part you want to pull before it ships. PAT was formalized in automotive (AEC-Q001) and
maps directly onto Zoox compute boards.

**Robust statistics first.** PAT limits are built on **robust** estimators so a few
outliers do not inflate the very limits meant to catch them:

- **Median** (50th percentile) for center — unmoved by a stuck or extreme reading.
- **MAD** (median absolute deviation) for spread: $\text{MAD}=\text{median}(|x_i-\tilde x|)$, scaled to a robust sigma by $\hat\sigma_\text{robust}=1.4826\cdot\text{MAD}$ (the $1.4826$ makes it match $\sigma$ for normal data).

**Static PAT** sets fixed limits from a qualified characterization population, placed
*inside* the spec to catch latent outliers:
$$\text{PAT limits} = \text{robust mean} \pm 6\,\hat\sigma_\text{robust}
\quad(\text{tightened inside the spec line}).$$
The $\pm6$ robust-sigma band is the common default; if it falls outside the spec the spec
governs (PAT never loosens a spec). Anything inside spec but outside the PAT band is a
PAT reject.

**Dynamic PAT** recomputes the center and limits **per lot / per panel / per wafer** from
that population's own robust mean and MAD, so the screen tracks normal lot-to-lot shifts
(a different reel of caps, a new solder lot) instead of false-failing a whole good lot
that simply ran a hair off the historical center. Use dynamic PAT when the part-to-part
distribution legitimately moves between lots but the *within-lot* outliers are still the
defect signal.

*Worked — board supply current.* Characterization gives robust mean $=2.00$ A,
$\text{MAD}=0.012$ A, so $\hat\sigma_\text{robust}=1.4826(0.012)=0.0178$ A and the static
PAT band is $2.00\pm6(0.0178)=2.00\pm0.107=[1.893,\ 2.107]$ A. The customer spec is
$1.7$–$2.3$ A. A board reading $2.18$ A **passes spec** but **fails PAT** — it is a
$10\sigma$-robust outlier from its peers, pulled as a latent-defect risk. On the next lot,
dynamic PAT recomputes the center (say robust mean $2.03$ A) and re-centers the band so a
uniformly higher-but-tight lot is not wrongly scrapped.

> **PAT vs SPC vs spec.** Spec limits protect the *customer* (fixed, from design). Control
> limits (the variables-charts discussion) protect the *process* (from $\bar R/d_2$, on subgroup statistics). PAT
> limits protect against the *individual latent outlier* (from robust part-population
> statistics, tightened inside spec). Three different jobs — do not substitute one for
> another.

---

## Gauge R&R / Measurement Systems Analysis

Before trusting a single measurement, prove the *measurement system* itself is capable.
A wandering fixture inflates apparent part variation and tanks your Cpk — so you GR&R the
station *before* you blame the product. Observed variance decomposes as

$$\sigma^2_\text{total} = \sigma^2_\text{part} + \sigma^2_\text{measurement}, \qquad
\sigma^2_\text{measurement} = \sigma^2_\text{repeatability} + \sigma^2_\text{reproducibility}.$$

- **Repeatability (EV, equipment variation):** same operator, same part, same gauge, repeated — the gauge's own scatter.
- **Reproducibility (AV, appraiser variation):** different operators / stations / fixtures on the same parts — setup-to-setup variation.
- **Gauge R&R** $= \sqrt{\text{EV}^2 + \text{AV}^2}$.

A standard crossed study: 10 parts $\times$ 3 operators $\times$ 3 trials $= 90$
measurements (ANOVA method preferred). Two acceptance metrics:

$$\%\text{GR\&R} = \frac{\sigma_\text{GRR}}{\sigma_\text{total}} \times 100\%, \qquad
\text{ndc} = 1.41\,\frac{\sigma_\text{part}}{\sigma_\text{GRR}}.$$

| %GR&R (study var) | Verdict |
|---|---|
| < 10% | acceptable |
| 10 - 30% | marginal - accept on cost/criticality |
| > 30% | unacceptable - fix the gauge/fixture first |

**ndc** (number of distinct categories) should be $\ge 5$ — fewer and the gauge can
barely tell parts apart.

*Worked.* $\sigma_\text{total} = 1.00$, $\sigma_\text{GRR} = 0.25$:
$\%\text{GR\&R} = 25\%$ (marginal). $\sigma_\text{part} = \sqrt{1.00^2 - 0.25^2} = 0.968$,
$\text{ndc} = 1.41(0.968/0.25) = 5.46 \to 5$ — just acceptable. The lesson connects
straight to *Setting limits and guardbands*: that 0.25 of gauge sigma is the uncertainty your guardband must cover.

### Bland-Altman and tester-to-tester / Contract Manufacturer correlation

GR&R answers "is this one station's measurement system capable?" The next question is
"do two stations — or my station and the Contract Manufacturer (CM)'s (CM) — *agree*?" When
the same boards run on tester A and tester B (or in-house vs CM), you must prove the two
read the same value before you trust a number that crosses sites. A naive correlation
coefficient $r$ is the wrong tool: two testers can have $r=0.99$ yet a constant 0.3 A
offset, which $r$ is blind to. The right tool is the **Bland-Altman** (difference-vs-mean)
plot.

For each part $i$ measured on both, compute the **difference** and the **mean**:
$$d_i = A_i - B_i, \qquad m_i = \tfrac{1}{2}(A_i + B_i).$$
Plot $d_i$ against $m_i$. Three numbers come off it:

- **Bias** (mean difference) $\bar d$ — the systematic offset between the two testers. A non-zero $\bar d$ is a calibration difference to fix, not noise.
- **Limits of agreement (LoA):** $\bar d \pm 1.96\,s_d$, where $s_d$ is the standard deviation of the differences. ~95% of part-to-part disagreements fall inside this band.
- **Trend:** if $d_i$ grows with $m_i$ (a fan-out or slope on the plot), the testers disagree more at one end of the range — a gain/scale mismatch, not just an offset.

You then ask the engineering question the plot frames: **is the LoA band narrow enough to
be acceptable** relative to the spec width and the guardband (*Setting limits and guardbands*)? If the limits of
agreement eat a meaningful fraction of the tolerance, a board passing at the CM could fail
in-house (or vice versa) — an escape or a yield-loss source purely from measurement
disagreement.

*Worked — in-house vs CM, board supply current.* Across 30 boards the differences
(in-house minus CM) average $\bar d = +0.04$ A with $s_d = 0.05$ A. Bias $=+0.04$ A
(in-house reads high — chase the shunt calibration), and LoA $=0.04\pm1.96(0.05)=[-0.058,
\ +0.138]$ A. Against a $1.7$–$2.3$ A spec ($0.6$ A wide), the LoA spans about $0.20$ A —
roughly **a third of the full tolerance** (each half-band is ~0.1 A, ~17% of the spec
width), and the $s_d = 0.05$ A of disagreement is itself ~2x the $0.025$ A GR&R sigma of a
single station. That is large enough that the two sites need a correlation offset applied
(or the in-house shunt re-calibrated) before either site's pass is honored at the other:
with the bias uncorrected, a board reading $2.28$ A in-house ships, while the same board
at the CM reads ~$2.24$ A and also ships — but a board near the low edge can straddle the
limit and pass at one site, fail at the other. The same difference-vs-mean method validates
a new tester against the incumbent before it joins the fleet.

> **Why not just $r$?** Correlation measures *association*, agreement measures *sameness*.
> Two testers tracking each other perfectly with a fixed offset have $r\approx1$ and a
> non-zero bias; Bland-Altman shows the offset, $r$ hides it. Report bias and LoA for
> tester-to-tester and CM correlation, not $r$.

---

## Sampling and AQL

You rarely test 100% — you pull a sample and infer the lot. Sampling plans formalize the
risk you take by *not* inspecting everything.

### Confidence intervals on a proportion (yield)

For yield $\hat p = x/n$, large-sample normal approximation:
$$\hat p \pm z_{\alpha/2}\sqrt{\frac{\hat p(1-\hat p)}{n}}.$$
*Yield $\hat p = 0.95$ on $n = 400$:* SE $= 0.0109$, 95% CI $= (0.929, 0.971)$. You
cannot honestly claim "96% yield" from this lot at 95% confidence. For small $x$ or $p$
near 0/1, switch to an exact method — the zero-failure case is exactly the **rule of
three**: 0 fails in $n$ gives an upper 95% bound $\approx 3/n$ (the bit-domain twin of the
zero-error "3/BER" rule in *BER and Confidence*).

**Sample size to hit a margin $E$:** $n \approx z^2 p(1-p)/E^2$, worst case at $p = 0.5$.
*95% CI, $\pm 3\%$:* $n = 1.96^2(0.25)/0.03^2 \approx 1068$. Note margin shrinks only as
$\sqrt n$ — halving the CI width needs 4× the units.

### AQL, LTPD, and the OC curve

An **acceptance sampling plan** is defined by sample size $n$ and accept number $c$:
inspect $n$ units, accept the lot if defects $\le c$. Its behavior is the **Operating
Characteristic (OC) curve** — P(accept) vs the true lot defect rate:

```text
P(accept)
  1.0 |******__              <- AQL: good lots accepted (high P)
      |        *_
      |          *_          <- steeper c=0 curve discriminates harder
      |            *_
      |              *__
  0.0 |_________________*****----> lot fraction defective
        AQL          LTPD
```

- **AQL (Acceptable Quality Level):** the defect rate you want *accepted* almost always. The **producer's risk $\alpha$** ($\approx 5\%$) is rejecting a lot that is actually at the AQL.
- **LTPD (Lot Tolerance Percent Defective):** the bad rate you want *rejected* almost always. The **consumer's risk $\beta$** ($\approx 10\%$) is accepting a lot at the LTPD.
- The plan $(n, c)$ is chosen so the OC curve passes near $(\text{AQL}, 1-\alpha)$ and $(\text{LTPD}, \beta)$.

**P(accept) is just a binomial tail:**
$P(\text{accept}) = \sum_{k=0}^{c} \binom{n}{k} p^{k}(1-p)^{n-k}$. *Plan $n = 50$, $c = 1$,
lot at $p = 2\%$:* $P(\text{accept}) = 0.98^{50} + 50(0.02)(0.98^{49}) = 0.364 + 0.372 = 0.736$.

> **The c=0 plan (accept-on-zero).** Setting $c = 0$ gives the steepest possible OC curve for a given $n$ and is the modern default for safety-critical hardware: a single defect rejects the lot. $P(\text{accept}) = (1-p)^n$, so to be 95% sure of catching a 1% lot you need $n$ with $(0.99)^n \le 0.05 \Rightarrow n \approx 300$. Zero-acceptance is unforgiving by design — which is the point on a robotaxi compute board.

---

## Yield: FPY, RTY, Throughput, and Cost of Test

Yield is where probability meets the P&L, and it sits right next to capability (*Process Capability and Setting Limits*) and
sampling (*Sampling and AQL*) because the same defect fractions drive all three. The trap to avoid:
**step yields multiply, they do not average.**

### First Pass Yield and Rolled Throughput Yield

- **First Pass Yield (FPY)** of one step $=\dfrac{\text{units passing without rework}}{\text{units in}}$.
- **Rolled Throughput Yield (RTY)** across $k$ independent steps $=\prod_{i=1}^{k}\text{FPY}_i$ — the probability a unit clears the *entire* line clean the first time.
- **Final / Test Yield** counts units that eventually pass (after rework). FPY $\le$ final yield.
- **Normalized Yield** $=\sqrt[k]{\text{RTY}}$ — the average per-step yield, for comparing lines with different step counts.

*Worked.* Two independent steps fail 3% and 5%: $\text{RTY}=0.97\times0.95=0.9215$
(92.15%). Five steps each at 99%: $\text{RTY}=0.99^{5}=0.951$ — a line that is "99% at
every step" still loses ~5% end-to-end. Ten steps at 99%: $0.99^{10}=0.904$. Step count is
a yield tax. *On a Zoox compute board* the in-line gates (PCIe link, GPU Error-Correcting Code (ECC) scrub,
NVMe/Double Data Rate (DDR) soak, Gigabit Multimedia Serial Link (GMSL) camera-lock) each carry an FPY; their product is what you start-quantity
against to hit a ship target.

### DPMO and DPPM — defects per million

Two related "per million" metrics convert defect counts into a comparable scale and bridge
straight to the Cpk-to-PPM table in *The "Sigma Level" Bridge* and the four-indices discussion:

- **DPMO (defects per million opportunities).** A unit can fail in several *independent ways* (opportunities); DPMO normalizes by them. With $D$ defects over $U$ units at $O$ opportunities each:
$$\text{DPMO} = \frac{D}{U\cdot O}\times 10^{6}.$$
- **DPPM / DPM (defective parts per million).** Counts *defective units*, not defects, regardless of how many ways each could fail:
$$\text{DPPM} = \frac{\text{defective units}}{\text{total units}}\times 10^{6}.$$

A board with many components has many opportunities, so its DPMO is far smaller than its
DPPM — quoting the wrong one flatters or damns a line unfairly. Use **DPPM** for "what
fraction of boards are bad" (the customer's view) and **DPMO** for "how clean is each
solder joint / placement / net" (the process view).

*Worked.* 2 defects across 100 boards, 50 opportunities each:
$\text{DPMO}=2/(100\cdot50)\times10^{6}=400$. If those 2 defects landed on 2 distinct
boards, $\text{DPPM}=2/100\times10^{6}=20{,}000$ — same data, two very different headline
numbers.

**The bridge to capability.** A DPMO (or one-sided PPM) is just a normal-tail defect
fraction, so it maps onto the sigma/Cpk table in *The "Sigma Level" Bridge* directly. With
the **$1.5\sigma$ long-term shift** convention, the *long-term sigma level* is
$$\text{sigma level} \approx 0.8406 + \sqrt{29.37 - 2.221\ln(\text{DPMO})}.$$
This is the inverse of the conversion table, but note the convention: the **sigma level it
returns already includes the $1.5\sigma$ shift**, so it is *not* the same number as the
short-term "sigma to spec" column in that table. The canonical anchor points (long-term
sigma level, then DPMO):
$$3\sigma \leftrightarrow 66{,}800, \qquad 4\sigma \leftrightarrow 6210,
\qquad 5\sigma \leftrightarrow 233, \qquad 6\sigma \leftrightarrow 3.4.$$
Strip the $1.5\sigma$ shift back out and the famous **$6\sigma$ level = $4.5\sigma$
short-term = $C_{pk}$ of $1.5$ = 3.4 PPM** reconciliation falls right out — the same
$1.5\sigma$-shift logic as the Cp-vs-Ppk gap in *Process Capability and Setting Limits*.
So a DPMO target and a Cpk target are the same requirement in two dialects — but confirm
*which* convention (short-term Cpk vs shifted sigma level) the other party is quoting
before you agree to a number.

*Worked — a GMSL camera-lock station.* A new station logs 18 camera-lock failures across
60 boards, each board exercising 4 GMSL links (so 4 lock opportunities per board):
$\text{DPMO} = 18/(60 \cdot 4) \times 10^{6} = 75{,}000$. Plugging in,
sigma level $= 0.8406 + \sqrt{29.37 - 2.221\ln(75{,}000)} = 0.8406 + \sqrt{29.37 - 24.93}
= 0.8406 + 2.11 = 2.95$ — call it a **$3\sigma$-level** station, squarely in
"needs-improvement" territory and nowhere near the $4\sigma$ ($C_{pk}\approx1.33$) capable
floor you would gate a robotaxi compute board at.

### Throughput, takt, and station count

Capacity per station per shift $=\dfrac{\text{available minutes}}{\text{cycle time}}$.
Stations needed $=\big\lceil \dfrac{\text{demand}\times\text{cycle time}}{\text{available minutes}}\big\rceil$.
**Takt time** $=\dfrac{\text{available time}}{\text{demand}}$ is the drumbeat the line must hit.

*Worked.* Test $=20$ min, 3% fail and get a 10 min retest, demand 200 boards in an 8-h
shift, 2 stations. Per-station capacity $=480/20=24$ first-pass tests; total
$2\times480=960$ station-minutes vs first-pass need $200\times20=4000$ min.
$4000/960=4.17$ shifts of work — **you cannot do 200 in one shift with 2 stations.**
Minimum stations $=\lceil 200\times20/480\rceil=\lceil8.33\rceil=9$ (before even counting
the 3% retests). A long BER or NVMe/DDR soak (*BER and Confidence*, the *Reliability* section) blows up the cycle time, so a soak
station is usually parallelized — many DUTs per station — rather than run in series at takt.

### Cost of test and where to put the gate

False-reject (scrap good, the $\alpha$ cost) and false-accept (ship bad, the
$\beta$/escape cost) trade against test time. The escape cost compounds downstream:
catching a defect at board test might cost \$50; the same defect found at vehicle
integration costs orders of magnitude more (the classic 10x-per-stage rule of thumb). That
asymmetry is why the Bayes math in *Conditional probability and Bayes* matters — and why on a high-yield line a high
false-positive rate, not low sensitivity, is usually what bleeds money. Place the gate
where the marginal escape cost first exceeds the marginal test cost: cheap, high-coverage
screens early; expensive soaks (BER, HTOL) reserved for what the cheap screens cannot see.

---

## Reliability: Bathtub, MTBF, FIT, and Acceleration

Reliability is where probability meets the field-return rate. The failure rate over life
follows the **bathtub curve** (the three Weibull regions from the Weibull distribution):

1. **Infant mortality** (decreasing rate): manufacturing defects. **Burn-in** (operate hot, 55–85$^\circ$C, hours–days) accelerates and screens these so survivors land on the flat.
2. **Useful life** (constant rate): random failures — exponential applies, MTBF is meaningful.
3. **Wear-out** (increasing rate): electromigration, NAND wear, aging — Weibull, $\beta > 1$.

### MTBF and FIT

For the constant-hazard region, $\text{MTBF} = 1/\lambda$. The industry quotes the same
$\lambda$ as a **FIT rate** — **F**ailures **I**n **T**ime, failures per $10^{9}$
device-hours:
$$\text{FIT} = \lambda \times 10^{9}\ \frac{\text{failures}}{\text{device-hour}}, \qquad
\text{MTBF (h)} = \frac{10^{9}}{\text{FIT}}.$$
*A part rated 100 FIT:* $\lambda = 10^{-7}/$h, MTBF $= 10^{7}$ h. FIT is additive across
parts (like $\lambda$), which is why component datasheets quote it — you sum the board.

**FIT to the number that matters: field returns.** MTBF in hours is abstract; the program
manager wants an **annualized failure rate (AFR)** and a spares budget. For a small annual
hazard, $\text{AFR} = 1 - e^{-\lambda \cdot 8760} \approx \lambda \cdot 8760$ (8760 h/yr),
and the expected returns from a fleet of $N$ continuously-running boards over a year is
just $N \cdot \lambda \cdot 8760$. *A board summing to $50$ FIT* ($\lambda = 5\times10^{-8}$):
$\text{AFR} \approx 5\times10^{-8} \times 8760 = 4.4\times10^{-4}$, i.e. ~0.044%/yr, or
~4.4 returns per 10,000 boards per year — the number you size the Return Merchandise Authorization (RMA) pipeline against. The
inverse direction sets a *requirement*: "no more than 100 returns/yr across a 50,000-board
fleet" is $\lambda \le 100/(50{,}000 \times 8760) = 2.3\times10^{-7}/$h, i.e. a **board FIT
budget of ~228** that you then allocate down to components.

### Series and redundant systems

**Series** (any one failure kills the system) — failure rates add:
$$\lambda_\text{sys} = \sum_i \lambda_i, \qquad \text{MTBF}_\text{sys} = 1/\lambda_\text{sys}.$$
*Board: 4 GPU (50,000 h each), 2 NVMe (200,000 h), 1 NIC (500,000 h):*
$\lambda = 8\times10^{-5} + 1\times10^{-5} + 2\times10^{-6} = 9.2\times10^{-5}/$h,
MTBF $= 10{,}870$ h $\approx 1.24$ yr continuous. In FIT: $92{,}000$ FIT for the board.
Run that through the AFR bridge above and it is sobering: $\text{AFR} = 1 - e^{-9.2\times10^{-5}\times8760} = 55\%$ — more than half of these boards would fail
within a year of continuous operation. The 4 GPUs at 50,000 h each dominate (8 of the
9.2 in the $\lambda$ sum), and *that* is the quantitative argument for the redundancy below:
a serial stack of high-power parts simply cannot hit a robotaxi availability target on its
own.

**Redundant / k-of-n** — system works if enough units do (binomial, the binomial distribution):
$R_\text{sys} = 1 - \prod_i (1 - R_i)$ for full parallel. *Need $\ge 3$ of 5 GPUs, each
99%:* $P(\ge 3) = 0.9510 + 0.0480 + 0.00097 = 0.9999$. Redundancy crushes the failure
probability — the architectural reason a safety-critical compute platform carries spare
lanes and compute.

### Acceleration models — Arrhenius

Reliability lab tests run hot to fail parts faster, then you extrapolate to use
temperature. The **Arrhenius model** governs temperature-driven (chemical/diffusion)
failure mechanisms:
$$\text{AF} = \exp\!\left[\frac{E_a}{k}\left(\frac{1}{T_\text{use}} - \frac{1}{T_\text{stress}}\right)\right],$$
with activation energy $E_a$ (eV), Boltzmann $k = 8.617\times10^{-5}$ eV/K, and
temperatures in **kelvin**. AF is the multiplier by which life shrinks at the stress
temperature.

*Worked.* $E_a = 0.7$ eV, use $55^\circ$C $= 328$ K, stress $125^\circ$C $= 398$ K:
$$\text{AF} = \exp\!\left[\frac{0.7}{8.617\times10^{-5}}\left(\tfrac{1}{328} - \tfrac{1}{398}\right)\right]
= \exp[8124 \times 5.36\times10^{-4}] = e^{4.36} \approx 78.$$
So **1 hour at $125^\circ$C $\approx 78$ hours at $55^\circ$C** — a 1000-hour HTOL soak
demonstrates ~78,000 use-hours (~9 years) of thermal life. That single number is how a
burn-in or HTOL plan gets justified to a program manager. (Voltage and humidity stresses
use companion models — Eyring, Coffin-Manson for thermal-cycle fatigue — but Arrhenius
is the one you will quote most.)

> **AF is exponentially sensitive to $E_a$ — pick it honestly.** Hold the same
> $55 \to 125^\circ$C but vary the activation energy: $E_a = 0.3$ eV gives $\text{AF} = 6.5$,
> $0.7$ eV gives $78$, $1.0$ eV gives $504$. The *same* 1000-hour soak then "demonstrates"
> anywhere from 0.74 to 57 years of life depending on a number you assumed. Use the
> *mechanism's* published $E_a$ (electromigration ~0.7 eV, oxide/dielectric breakdown
> ~0.3–0.7 eV, some ionic-contamination mechanisms ~1.0 eV), and when a failure mode is
> unknown, use a **conservative low** $E_a$ — guessing high inflates your demonstrated life
> and is exactly how an under-screened part reaches a robotaxi. A NVMe or DDR soak gated
> on Arrhenius is only as trustworthy as the $E_a$ behind it.

---

## Regression and Correlation

When you need to relate two test parameters — predict a final value from an in-line
reading, or check whether a fixture knob actually moves a measurement — you reach for
correlation and linear regression.

**Correlation $r$** measures linear association, $-1 \le r \le 1$:
$$r = \frac{\sum (x_i - \bar x)(y_i - \bar y)}{\sqrt{\sum (x_i - \bar x)^2}\,\sqrt{\sum (y_i - \bar y)^2}}.$$
$r = 0$ means no *linear* relationship (it can still be curved); $|r|$ near 1 means tight
linear fit. $r^2$ is the fraction of variance in $y$ explained by $x$.

**Least-squares line** $\hat y = b_0 + b_1 x$:
$$b_1 = \frac{\sum (x_i - \bar x)(y_i - \bar y)}{\sum (x_i - \bar x)^2} = r\,\frac{s_y}{s_x}, \qquad
b_0 = \bar y - b_1 \bar x.$$

*Use on the floor.* Fit final post-burn-in leakage against an in-line room-temperature
reading; if $r^2 = 0.9$ the cheap early measurement predicts the expensive late one well
enough to screen early and save burn-in slots. The air-brake fixture model
$\tau = \text{slope}\cdot\text{PSI} + \text{intercept}$ from the platform chapter is
exactly this — a regression fit turned into a test limit.

> **Correlation is not causation, and watch your range.** A strong $r$ over a narrow tested range can vanish or flip outside it; never extrapolate a fit past the data that built it. And a lurking variable (ambient temperature drifting with time of day) can manufacture correlation between two unrelated readings.

---

## Fast Estimation and Mental Math for the Floor

Half the math you do at a station is a 10-second sanity check, not a derivation. These
are the shortcuts that keep you from chasing a phantom or quoting a number that is off by
a decade.

**Orders of magnitude and the rule of 72-ish.**
- Powers of two: $2^{10} \approx 10^3$, so $2^{20} \approx 10^6$, $2^{30} \approx 10^9$. A 32-bit counter wraps at ~$4\times10^9$.
- dB shortcuts: $\times 2 = +3$ dB, $\times 10 = +10$ dB. A $\times 8$ gain is $3\times3 = 9$ dB.
- "3/BER" for run time (the zero-error "3/BER" rule); "3/n" rule of three for a zero-failure upper bound (the proportion-CI discussion).

**BER run time in your head.** $n \approx 3/p$ for 95% CL, then divide by the line rate.
$10^{-12}$ at $32$ Gb/s: $3\times10^{12}$ bits $/ 3\times10^{10}$ bit/s $\approx 100$ s.
Deeper targets scale linearly: $10^{-15}$ is 1000× longer — tens of hours, or run many
lanes in parallel.

**Yield multiplies, it never averages.** Step yields *compound*: ten steps at 99% is
$0.99^{10} \approx 0.90$, not 99%. Quick rule for small loss: total loss $\approx$ sum of
per-step losses, so $10 \times 1\% \approx 10\%$ — close to the exact 9.6%. To ship $N$
good units, *start* $N / \text{RTY}$ and budget the scrap.

**Poisson zero-defect feel.** $P(\text{zero}) = e^{-\lambda}$ with $\lambda = np$. At
$\lambda = 1$ you have a 37% chance of a clean unit; at $\lambda = 0.1$, 90%; at
$\lambda = 3$, only 5%. Lets you eyeball "is a defect-free lot plausible?" instantly.

**Sigma-to-ppm anchors.** $3\sigma \to 1350$ ppm, $4\sigma \to 32$ ppm, $5\sigma \to 0.3$
ppm — each extra sigma is roughly a 30–100× drop. So Cpk $1.0 \to 1.33$ is two orders of
magnitude fewer escapes; that is the payoff you cite when arguing for a process
improvement.

**Throughput / takt.** Capacity per station $= \text{available min} / \text{cycle time}$;
stations needed $= \lceil \text{demand} \times \text{cycle} / \text{available} \rceil$.
*20-min test, 480-min shift, 200 boards:* $200 \times 20 / 480 = 8.3 \to$ **9 stations** —
before retests. Do this before promising a build rate.

**Sanity-check every answer.** Units, order of magnitude, and direction. "Cpk went *up*
when I widened the spec" — correct. "BER bound got *lower* when I saw *more* errors" —
wrong, recheck. Catching your own decade error is the cheapest quality gate on the floor.

---

## One-Page Formula Sheet

```text
PROBABILITY / BAYES
  P(A or B)=P(A)+P(B)-P(A and B)     P(A and B)=P(A)P(B|A)
  Bayes:  P(A|B)=P(B|A)P(A)/P(B)     P(B)=P(B|A)P(A)+P(B|A')P(A')
  PPV = P(defective | FAIL)          (sensitivity, specificity, prevalence)
  E[aX+b]=aE[X]+b   Var(aX+b)=a^2 Var(X)   sigma_sum=sqrt(sum sigma_i^2)

DISTRIBUTIONS
  Binomial: C(n,k) p^k (1-p)^(n-k)   mean np
  Poisson:  lam^k e^-lam / k!        mean=var=lam   (BER, defect counts)
  Geometric:(1-p)^(k-1) p            mean 1/p
  Normal:   Z=(X-mu)/sigma           68-95-99.7
  Exponential: P(X>t)=e^-(lam t)     mean 1/lam, memoryless (useful life)
  Weibull:  P(X>t)=exp(-(t/eta)^beta)  beta<1 infant, =1 random, >1 wear-out

GEOMETRY
  hexagon area = (3 sqrt3 / 2) s^2 ~ 2.598 s^2     perimeter 6s
  equilateral triangle area = (sqrt3/4) s^2
  small angle: distance = size / angle(rad)
  camera FOV = 2 atan(sensor_width / (2 f));  width@R = 2 R tan(half-FOV)
  3D n^3 collinear-triple lines = 3n^2 + 6n + 4   (n=3 -> 49)
  point-to-line (3D): |AP x d| / |d|
  2D rotation: x'=x cos- y sin, y'=x sin+ y cos

SIGNAL / ALGEBRA
  dB_power=10 log10(P1/P2)   dB_amp=20 log10(V1/V2)   x2=3dB  x10=10dB
  GB/s per lane = GT/s * coding_eff / 8   (8b10b=0.8, 128b130b=0.9846)
  noise averaging: sigma_avg = sigma / sqrt(N)
  uncertainty: add (sigma) or relative-(sigma) in quadrature
  Nyquist: fs >= 2 f
  vectors: a.b=|a||b|cos th ;  |axb|=|a||b|sin th
  physical: F=P*A ; V=I*R, P=VI=I^2 R=V^2/R ; tau=F*d ; Q=m c dT

BER / BERT (zero-error acceptance)
  n = -ln(1-CL)/p      t = n/line_rate      proven BER = -ln(1-CL)/n
  with E errors: BER_upper = chi2(1-alpha, 2(E+1)) / (2n)
  CL = 1 - PoissonCDF(E; n*p)
  -ln(1-CL): 90%=2.303  95%=2.996  99%=4.605  99.9%=6.908   (~3/p rule)
  SPRT: pass below accept line, fail above reject line, else continue

CAPABILITY / LIMITS / OUTLIERS
  Cp = (USL-LSL)/(6 sigma_ST)
  Cpk= min(USL-mu, mu-LSL)/(3 sigma_ST)     Pp/Ppk use overall s
  Cpk 1.33 ~ 32ppm  1.67 ~ 0.3ppm  2.0 ~ 0.001ppm (3.4 w/1.5 shift)
  test limit = spec -/+ guardband;  guardband ~ k * gauge uncertainty
  robust sigma = 1.4826 * MAD       MAD = median(|x - median|)
  static PAT = robust_mean +- 6 robust_sigma (tightened inside spec)
  dynamic PAT: recompute center/limits per lot/panel

SPC
  Xbar: xbarbar +- A2 Rbar    R: D4 Rbar, D3 Rbar    sigma_ST = Rbar/d2
  c-chart: cbar +- 3 sqrt(cbar)    p-chart: pbar +- 3 sqrt(pbar(1-pbar)/n)
  CUSUM / EWMA catch small slow shifts;  WE rules: 1past3 / 2of3 A / 4of5 B / 8side

YIELD
  RTY = product of step FPYs        normalized = RTY^(1/k)    start N/RTY to ship N
  DPMO = D/(U*O) * 1e6              DPPM = defective_units/total * 1e6
  capacity/station = avail_min / cycle    stations = ceil(demand*cycle / avail)
  takt = available_time / demand

GAUGE / MSA / SAMPLING / RELIABILITY
  GR&R = sqrt(EV^2+AV^2)   %GRR<10 good >30 bad   ndc=1.41 part/GRR >=5
  Bland-Altman: d=A-B, m=(A+B)/2;  bias=mean(d), LoA = mean(d) +- 1.96 sd(d)
  CI(prop) = phat +- z sqrt(phat(1-phat)/n)     n ~ z^2 p(1-p)/E^2
  AQL plan (n,c): P(accept)=sum_{k=0..c} C(n,k)p^k(1-p)^(n-k)  c=0 = accept-on-zero
  series: lam_sys=sum lam_i   MTBF=1/lam   FIT=lam*1e9   MTBF=1e9/FIT
  Arrhenius AF = exp[(Ea/k)(1/Tuse - 1/Tstress)]   k=8.617e-5 eV/K, T in kelvin

REGRESSION
  r = cov / (sx sy)    b1 = r sy/sx    b0 = ybar - b1 xbar    r^2 = var explained
```
