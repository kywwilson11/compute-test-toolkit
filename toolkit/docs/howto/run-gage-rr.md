# How to run a Gage R&R study

A Gage Repeatability & Reproducibility (Gage R&R) study quantifies how much
of a measurement's variation is the **gage** (the test station) vs. how
much is the **parts**. Without it, a station's measurements are unsigned
data: you don't know if the trend you see is real or just the gage drifting.

`computetest.msa.gage_rr` ships a crossed-ANOVA Gage R&R that follows the
AIAG MSA 4th Edition convention. The harness is pure Python (no numpy or
scipy) so it drops into the existing CI without extra deps.

## When to run it

* Bringing up a new test station.
* After re-calibrating a major instrument.
* Investigating a yield drop that you can't pin to a specific failure mode
  — the station may be drifting under you.

## The data shape

A crossed Gage R&R needs:

* **Parts** — a small set (typically 5-10) of representative DUTs spanning
  the expected range.
* **Operators** — the people running the station (or, for an automated
  station, the discrete operational modes / shifts).
* **Trials** — each operator measures each part more than once
  (typically 2-3).

Total measurements = parts × operators × trials. The data is shaped as a
3-D nested sequence: `measurements[part][operator][trial]`.

## Run a study

```python
from computetest.msa import gage_rr_anova

# 5 parts, 3 operators, 3 trials each = 45 measurements
data = [
    # part 0
    [[10.01, 10.02, 10.00],   # operator 0
     [10.02, 10.03, 10.01],   # operator 1
     [10.00, 10.02, 10.01]],  # operator 2
    # part 1
    [[12.51, 12.52, 12.50],
     [12.52, 12.53, 12.51],
     [12.50, 12.52, 12.51]],
    # part 2
    [[15.01, 15.02, 15.00],
     [15.02, 15.03, 15.01],
     [15.00, 15.02, 15.01]],
    # part 3
    [[17.51, 17.52, 17.50],
     [17.52, 17.53, 17.51],
     [17.50, 17.52, 17.51]],
    # part 4
    [[20.01, 20.02, 20.00],
     [20.02, 20.03, 20.01],
     [20.00, 20.02, 20.01]],
]

result = gage_rr_anova(data)
print(result.summary())
# Gage R&R: p=5 o=3 r=3  %EV=0.4 %AV=0.2 %R&R=0.5  -> CAPABLE
```

The verdict follows the AIAG rule of thumb:

| %R&R | Verdict | What it means |
|------|---------|---------------|
| `< 10%`    | `capable`        | Gage's noise is well below the part variation. Use the data. |
| `10-30%`   | `marginal`       | Acceptable if reworking the gage is expensive AND parts vary much more than the gage. Document. |
| `>= 30%`   | `not_acceptable` | Gage swamps the signal. Find and fix the noise before using the data for limit-setting or process control. |

## Read the decomposition

```python
print(f"σ² Equipment Variation: {result.var_equipment:.4f}")
print(f"σ² Operator Variation:  {result.var_operator:.4f}")
print(f"σ² Operator×Part:       {result.var_interaction:.4f}")
print(f"σ² Part Variation:      {result.var_part:.4f}")
print(f"σ² Total:               {result.var_total:.4f}")
print(f"%EV={result.pct_ev:.1f}  %AV={result.pct_av:.1f}  %R&R={result.pct_rr:.1f}  %PV={result.pct_pv:.1f}")
print(f"Verdict: {result.verdict}")
```

* **EV (Equipment Variation)** — the gage's own noise floor, measured as
  σ² of replicate measurements by the same operator on the same part.
* **AV (Appraiser Variation)** — the operator effect plus the
  operator-by-part interaction.
* **PV (Part Variation)** — variation actually attributable to the parts.
* **R&R = EV + AV** — total measurement-system variation.
* **Total = PV + R&R**.

%-form is `σ_<component> / σ_total × 100` (the AIAG study-variation form).

## Emit the result into ocp-diag

The result dataclass has a `to_dict()` method that drops cleanly into the
ocp-diag emitter:

```python
from computetest.io import ocpdiag

em = ocpdiag.open_run(sys.stdout, program_version="0.1.0",
                       command_line="gage-rr", parameters={})
sid = em.step_start("msa.gage_rr")
em.measurement(name="pct_rr", value=result.pct_rr, unit="percent",
               validators=[ocpdiag.validator(
                   ocpdiag.LESS_THAN, 10.0, name="aiag_capable")])
em.measurement(name="pct_ev", value=result.pct_ev, unit="percent")
em.measurement(name="pct_av", value=result.pct_av, unit="percent")
em.diagnosis(verdict=f"msa.gage_rr.{result.verdict}",
             type_="PASS" if result.ok else "FAIL")
em.step_end("pass" if result.ok else "fail", step_id=sid)
em.run_end("pass" if result.ok else "fail")
```

## Station correlation across N stations

If you have N stations all measuring the same set of golden DUTs, run
Bland-Altman + Deming regression for every pair:

```python
from computetest.msa import station_correlation

# Same N golden DUTs measured on each of 3 stations.
stations = {
    "bay-3": [10.01, 12.50, 15.00, 17.50, 20.00],
    "bay-7": [10.02, 12.51, 15.01, 17.51, 20.01],
    "bay-9": [10.00, 12.49, 14.99, 17.49, 19.99],
}
pairs = station_correlation(stations)
for p in pairs:
    print(p.summary())
```

For each pair you get bias, 95% limits of agreement, Deming slope +
intercept with bootstrap 95% CIs, and a binary agreement verdict (the CI
on slope contains 1 AND the CI on intercept contains 0).

## Calibration registry

A station's instrument roster sits in `calibration_registry.yaml`. CI
gates a run by checking every required role has an unexpired entry:

```python
from datetime import date
from computetest.msa import load_registry

reg = load_registry("configs/calibration_registry.yaml")
reasons = reg.require(date.today(),
                       ["rx_eye_scope", "power_smu", "audit_dmm"])
if reasons:
    raise RuntimeError("station not ready to test:\n  " + "\n  ".join(reasons))
```

## Where to go next

* [explanation/why-msa.md](../explanation/why-msa.md) — the rationale
  for measurement-system analysis as a Mfg-Test discipline.
* The AIAG MSA Manual 4th Ed., §IV/D — the canonical reference for the
  crossed-ANOVA method this module implements.
