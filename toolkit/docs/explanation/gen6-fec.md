# Why PCIe 6.0 reliability is measured differently

If you ran the BERT in [tutorials/01-first-bert.md](../tutorials/01-first-bert.md)
on the Gen6 mock device (`0000:08:00.0`), you saw four extra fields the
Gen5 device didn't produce: `pre_fec_symbol_errors`,
`post_fec_flit_errors`, `fber_estimate`, and `burst_length_histogram`. This
document explains why those exist — and why a Gen6 "correctable" count
cannot be compared apples-to-apples with a Gen5 "correctable" count.

## Gen1-5: NRZ + AER, the comfortable world

PCIe Gen1 through Gen5 transports bits using NRZ (one bit per symbol). The
Advanced Error Reporting (AER) capability counts correctable events at
the transaction layer: a Bad TLP, a replay timer expiration, a CRC
mismatch. Each event is one symbol error (or thereabouts). The toolkit's
chi-squared BERT model assumes errors arrive independently, and the AER
count is a faithful population of that model.

Result: on Gen1-5, `correctable` is the right number to count, the BER
upper bound is the right number to verdict on, and the Poisson model
holds.

## Gen6: PAM-4 + FLIT + FEC, the new world

PCIe Gen6 uses PAM-4 (two bits per symbol) at 64 GT/s. Two consequences:

1. **The raw symbol error rate is much higher.** A pre-FEC BER ~1e-6 is
   normal for the PHY at Gen6. The 1e-12 NRZ Gen5 target is no longer
   physically achievable at the symbol layer.
2. **Mandatory Forward Error Correction.** PCIe 6.0 wraps every FLIT
   (256-byte framing unit) in a Reed-Solomon code over GF(2⁸), three-way
   interleaved. Bursts up to 16 bits affect at most one byte per ECC
   group; the FEC corrects them transparently. Only when more than
   N symbol errors land in a FLIT does the FEC fail to correct, and only
   then does an AER LCRC-retry surface.

The compliance metric the spec defines is **FBER ≈ 10⁻⁶** — the post-FEC
FLIT error rate. NOT a bit error rate. NOT comparable to Gen5's 10⁻¹²
target.

## What the toolkit emits

When the backend can read FEC counters (the Gen6 mock backend
synthesizes them deterministically; a vendor-specific real-hardware
backend would read them from the retimer / SerDes registers), the toolkit
populates four fields on the `BertResult`:

* `pre_fec_symbol_errors` — PAM-4 symbol errors BEFORE FEC correction.
  This is the **raw signaling quality** number; ~1e-6 per symbol is
  normal.
* `post_fec_flit_errors` — FLIT errors AFTER FEC correction. This is the
  **effective reliability** number; should be ≤ 1e-6 / FLIT (the FBER
  target).
* `fber_estimate` — `post_fec_flit_errors / total_flits`. The OCP
  compliance metric.
* `burst_length_histogram` — distribution of error burst lengths in
  symbol counts. Bursts up to length-16 (bits) are FEC-correctable; longer
  bursts indicate marginal eye structure that FEC cannot rescue.

The toolkit also emits the four fields through the OCP ocp-diag-core
stream as `fec.pre_fec_symbol_errors`, `fec.post_fec_flit_errors`,
`fec.fber_estimate` (with a `LESS_THAN_OR_EQUAL 1e-6 fber_target`
validator), and `fec.burst_len_N` measurements.

## What the toolkit does NOT do

The toolkit deliberately does **not** stop emitting the Gen5-style
`correctable` count on Gen6 links. It's still a useful fault signal — a
constant non-zero count means the FEC is failing routinely, which is a
real problem. But the toolkit also does not change the BERT verdict
threshold based on Gen. A Gen6 link still has to prove BER ≤ `target_ber`
in the chi-squared sense to get a PASS. The toolkit's `--explain` mode
prints the four-paragraph caveat explaining why that comparison is
limited — read it.

The honest signal: on a Gen6 link, the *primary* reliability metric is
the FBER, and AER is a *secondary* signal that catches FEC-uncorrectable
events. The toolkit surfaces both.

## When to act

| Observation | What it means |
|-------------|---------------|
| `pre_fec_symbol_errors` ≈ expected | PHY is operating in spec. |
| `pre_fec_symbol_errors` 10× expected | Marginal channel. Check the retimer's eye telemetry; consider an `lmt` run for the lane-by-lane breakdown. |
| `post_fec_flit_errors` > 0 | FEC is failing to correct some FLITs. Compare to FBER target. |
| `fber_estimate` > 1e-6 | Above the OCP compliance target. Failed link. |
| `burst_length_histogram[N]` skewed long | A specific burst-error mechanism is exceeding FEC's correction capacity. Investigate the silicon (eye margin, equalization). |

## Implementation pointers

* `computetest.backend.FecStats` defines the per-window read.
* `computetest.backend.MockBackend.read_fec_stats` synthesizes
  deterministic data from injected pre/post-FEC rates.
* Real backends override `Backend.read_fec_stats(bdf, elapsed_s)`. There
  is no industry-standard register set; every Gen6 retimer / SerDes
  vendor (Astera, Synopsys, Cadence, Marvell) has their own. The toolkit
  provides the boundary; the per-vendor read is wired up per-station.
* See `computetest.pcie.retimer` for the retimer abstraction that owns
  the per-vendor SDK boundary.

## Further reading

* Synopsys, *PCIe 6 Verification: FEC and CRC* (blog post). Source for
  the 3-way-interleaved RS-over-GF(2⁸) framing and the 16-bit burst
  threshold.
* PCIe 6.0 Base Specification, §3.5 ("Forward Error Correction").
* `computetest bert -d 0000:08:00.0 --target-ber 1e-6 --max-seconds 2 --explain`
  prints the four-paragraph caveat that's embedded in the verdict
  output.
