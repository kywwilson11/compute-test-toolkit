# How to run `computetest` on real hardware

This guide assumes you've followed
[tutorials/01-first-bert.md](../tutorials/01-first-bert.md) against the
mock backend and want to point the toolkit at a physical PCIe device.

## Prerequisites

* A Linux host (kernel 5.4+ recommended; 6.x ideal).
* The target PCIe device enumerated under `/sys/bus/pci`.
* Root access (the AER capability writes go through config space).
* `nvme-cli` if you also want NVMe checks (`apt install nvme-cli`).

## Pick the backend

```bash
# Auto: the toolkit picks real if /sys/bus/pci exists, else mock.
computetest list

# Force real (errors if /sys/bus/pci is missing):
computetest --backend real list

# Force mock (deterministic, no hardware):
computetest --backend mock list
```

You can also set `COMPUTETEST_BACKEND=mock|real|auto` in the environment.

> The CLI prints `# backend: REAL hardware` or `# backend: MOCK (no hardware)`
> on stderr at the start of every command so the operator can see which path
> ran. The pipe-friendly stdout output is unaffected.

## Sanity-check AER is reachable

`computetest` requires either the PCIe AER extended capability or the
Device Status fallback to count errors. Probe for it:

```bash
sudo lspci -vv -s 0000:03:00.0 | grep -A 1 'AER'
```

If AER is missing on a downstream port, the BERT returns `SKIP` with
`note=no PCIe error source (neither AER nor Device Status)`. The mock
backend always exposes AER; real silicon sometimes hides it on
device-internal ports (NVMe endpoints, custom ASICs without root-port-style
capabilities).

## Run the BERT against one BDF

```bash
sudo computetest bert -d 0000:03:00.0 \
    --target-ber 1e-12 --max-seconds 30
```

The `--max-seconds` parameter caps how long the toolkit will exercise the
link looking for errors. At Gen5 x16 (≈ 504 Gb/s payload), 30 seconds
transfers ~1.5×10¹³ bits — enough to prove BER ≤ 1e-12 at 95% confidence
with zero observed errors.

## Use the `c` engine for high-rate links

The default Python engine polls AER from Python and is fine for a
laptop-or-mock workflow. On a real station polling at >10 kHz, switch to
the C conductor:

```bash
sudo computetest bert -d 0000:03:00.0 \
    --target-ber 1e-12 --max-seconds 30 --engine c
```

The C engine (built at `toolkit/c/pcie_bert`) does the tight polling loop in
compiled code and reports back to the Python conductor. Build with
`make -C c` if you haven't.

## Diagnose every device at once

```bash
sudo computetest diagnose --target-ber 1e-12 --max-seconds 10
```

This runs link-health + AER snapshot + BERT + lane-margining on every PCIe
device. For just the link layer (no exercise window), add `--no-bert`. For
just one device, add `-d <BDF>`.

## Pipe results into a results DB

```bash
sudo computetest plan configs/example_plan.json \
    --db /var/lib/computetest/results.db \
    --serial SN123 --station bay-3
```

`plan` runs the full set of checks the config calls for, records every
measurement (with the **measured value**, not just pass/fail) into the
SQLite at `--db`, and tags each row with the `--serial` and `--station`
you pass. See [howto/write-test-plan.md](write-test-plan.md) for the
config format.

## Emit ocp-diag-core alongside the run

```bash
sudo computetest plan configs/example_plan.json \
    --db results.db --serial SN123 --station bay-3 \
    --ocpdiag /var/log/computetest/SN123.jsonl
```

The `.jsonl` file is a complete OCP v2.0 stream — drops straight into the
hyperscaler MT pipeline ingest layer. See
[howto/emit-ocp-diag.md](emit-ocp-diag.md) for the streaming patterns.

## Common gotchas

* **AER status bits are cleared by the kernel before the engine polls.**
  On x86, the `pcieport` kernel module's interrupt handler races the
  engine's poll loop. The toolkit's QEMU lane shows this — `aer_test.sh`
  unbinds `pcieport` for this reason. On a real station, take the same
  precaution if the BERT runs read 0/N consistently.
* **`SKIP` is never `PASS`.** The toolkit refuses to claim PASS from a
  measurement it couldn't make. If you see SKIP, fix the AER source or the
  link rate read before treating the device as good.
* **Constant-fault detection ("stuck"):** the BERT reads errors at idle
  before and after the exercise window. If a bit is set with zero traffic,
  the verdict flips to FAIL with a `errors present at idle` note —
  regardless of the BER count. This catches a damaged retimer that
  generates a steady error stream.

## Where to go next

* [explanation/bert-confidence.md](../explanation/bert-confidence.md) —
  why the verdict is what it is.
* [howto/triage-bert-fail.md](triage-bert-fail.md) — what to do when the
  verdict is FAIL or SKIP.
* [reference/cli.md](../reference/cli.md) — every flag the BERT, diagnose,
  chain, and plan subcommands accept.
