# Why the BERT verdict is what it is

A Bit Error Rate Test (BERT) is two things at once: a measurement (count
the errors during a bounded window of bits) and a hypothesis test (decide
whether the link's *true* BER is below a target). The two are joined by a
confidence interval, and the toolkit's verdict logic lives entirely in
that joint.

## The model

The link transports `n` bits, during which the toolkit counts `E`
correctable errors. The errors arrive as a Poisson process at rate
`λ = BER × bps` (errors per second, where `bps` is the link payload rate).

If the *true* BER is `p`, then:

* The expected error count in `n` bits is `μ = p × n`.
* The probability of observing exactly `k` errors is
  `P(E=k) = (μ^k × e^-μ) / k!`.

The toolkit doesn't see `p`. It sees `E` and `n`, and asks: what's the
*largest* BER consistent with this observation at the requested confidence?

## The chi-squared upper bound

The exact one-sided upper confidence limit for a Poisson rate, given `E`
errors observed, is

  `μ_upper = (1/2) × χ²_{2(E+1), 1 - α}`

where `α` is the significance level (= 1 - confidence) and
`χ²_{ν, p}` is the `p`-quantile of the chi-squared distribution with `ν`
degrees of freedom.

Translated to BER:

  `BER_upper = μ_upper / n`

This is `result.verdict.ber_upper` in the toolkit. It is the toolkit's
single most important number — the upper edge of the 95% CI on the true
BER, given everything we measured.

The verdict is then trivial: **PASS iff `BER_upper ≤ target_ber`**.

## Why the bits requirement grows with errors

If the toolkit observes zero errors, it needs

  `n = -ln(α) / target_ber`

bits to push `BER_upper` down to `target_ber`. At α = 0.05 (95%
confidence) and target BER = 1e-12 that's `≈ 3 × 10¹²` bits — about 6
seconds at Gen5 x16 (504 Gb/s payload).

If the toolkit observes one error, it needs more bits — the chi-squared
quantile grows. Two errors, more again. The toolkit's `bits_for_confidence`
function ships the exact relation; `computetest ber` exposes it on the CLI
without touching hardware:

```bash
computetest ber --target-ber 1e-12 --confidence 0.95 --errors 3
```

## Why the verdict can FAIL fast

The toolkit doesn't wait the full `max_seconds` if the evidence is already
overwhelming. After every poll cycle it asks a *sequential* version of
the same test:

* If the count is low enough that `BER_upper ≤ target_ber` even with the
  bits transferred so far → declare PASS, stop.
* If the count is high enough that *no plausible extension* of the window
  could push `BER_upper` back below the target → declare FAIL ("reject"),
  stop.
* Otherwise → keep going.

The "reject" branch is what catches a catastrophically broken link in
~50 ms instead of waiting 30 seconds. The "continue" branch is what
prevents the toolkit from over-claiming PASS prematurely.

## What about uncorrectable errors?

The Poisson model is for *correctable* errors — the high-volume, randomly-
distributed kind. Uncorrectable errors are a fundamentally different
signal: even one is a serious fault that no amount of additional traffic
will explain away. The toolkit treats them as immediate FAIL:

* Any non-zero `result.uncorrectable` → FAIL regardless of `BER_upper`.
* The decoded type names are surfaced in
  `result.uncorrectable_decode` for triage.

## Idle baseline (the constant-fault check)

Before opening the exercise window, the toolkit quiesces the link and
reads the AER status registers. If any bit is set with zero traffic, it's
*not a rate* — it's a constant fault (a damaged retimer, a stuck-at fault
on a lane). The toolkit records this as `result.stuck = True` and flips
the verdict to FAIL regardless of the BERT count.

At the end of the window the toolkit quiesces again and re-checks. A
persistent post-window bit is the same signal. Without these checks, a
flat-out broken link could read 0 errors during the window (because every
exercise cycle clears the AER status) and incorrectly PASS.

## Where the model breaks

The Poisson + chi-squared model assumes errors are independent. Two
real-world cases violate this:

1. **Saturated count under catastrophic SI.** When the poll rate can't keep
   up with the actual error rate, multiple errors per poll fold into one
   AER status latch — the count saturates. The toolkit detects this via
   the C engine's `poll_rate_hz` signal and surfaces a calibration note in
   `result.note`. The verdict stays correct (catastrophic fails get
   rejected by the sequential decision before saturation matters), but
   the *number* reported is a lower bound.
2. **PCIe Gen6+ FEC.** PAM-4 + RS-FEC means many physical-layer symbol
   errors are FEC-corrected before AER ever sees them. The AER count
   *under*-measures BER on Gen6+ links. The toolkit surfaces this via
   `pre_fec_symbol_errors` / `post_fec_flit_errors` / `fber_estimate`
   when the backend can read FEC counters, and a four-paragraph caveat
   via `--explain`. See [explanation/gen6-fec.md](gen6-fec.md) for the
   full story.

## Implementation pointers

* The chi-squared math is in `computetest.ber.bits_for_confidence` +
  `BertVerdict.assess`.
* The sequential decision is in `computetest.ber.sequential_decision`.
* The Python conductor lives in `computetest.bert.run_bert`; the C engine
  conductor in `run_conductor`. Both produce the same `BertResult` shape.
* Inject deterministic clock and `sleep` via the constructor kwargs to
  make the test suite reproducible.

## Further reading

* AIAG MSA Manual 4th Ed., §IV — the canonical reference for the
  measurement-model framing.
* `computetest ber --target-ber X --confidence Y --errors E` — pure-math
  CLI for the chi-squared calculation.
