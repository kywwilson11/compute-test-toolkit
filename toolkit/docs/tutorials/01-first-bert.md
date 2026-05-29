# Tutorial 1 — Your first PCIe BERT in 10 minutes

You are going to install `computetest`, run the PCIe Bit Error Rate Test
against a simulated board, and read the verdict. No hardware required.

By the end you will understand: how a BERT verdict is shaped, what
"confidence" means in this toolkit, and where to look next for real-hardware
runs and the ocp-diag-core output stream.

## What you need

* Python 3.10 or newer.
* About 10 minutes.
* The `computetest` source tree (this repo, under `toolkit/`).

Nothing else. The toolkit ships a **mock backend** that simulates a
representative compute board (two Gen5 GPUs, two Gen4 NVMe drives, a Gen3
custom card, and a Gen6 accelerator) so the BERT runs end-to-end on a
laptop.

## Step 1 — Install in editable mode

```bash
cd toolkit
pip install -e .
```

Verify the install:

```bash
computetest --help
```

You should see seven subcommands: `list`, `bert`, `diagnose`, `chain`,
`nvme`, `gpu`, `gmsl`, `eth`, `can`, `plan`, `ber`, `lmt`.

> **Don't want to install?** Substitute
> `PYTHONPATH=src python3 -m computetest.cli` for `computetest` in every
> command below.

## Step 2 — Enumerate the simulated board

```bash
computetest list
```

Expected output (paraphrased):

```
# backend: MOCK (no hardware)
0000:03:00.0  NVIDIA            Gen5x16  class=0x030000 drv=nvidia
0000:04:00.0  NVIDIA            Gen5x16  class=0x030000 drv=nvidia
0000:05:00.0  Samsung           Gen4x4   class=0x010802 drv=nvme
0000:06:00.0  Samsung           Gen4x4   class=0x010802 drv=nvme
0000:07:00.0  Other             Gen3x8   class=0x088000 drv=zoox_custom
0000:08:00.0  NVIDIA            Gen6x8   class=0x030000 drv=nvidia
```

Six devices. The board is the same shape every time — the mock is seeded.
The `# backend: MOCK` banner goes to stderr, so it stays out of any
pipeline you pipe `stdout` into.

## Step 3 — Run the BERT on one device

Pick the first GPU:

```bash
computetest bert -d 0000:03:00.0 --target-ber 1e-12 --max-seconds 5
```

You'll see a single-line summary like:

```
0000:03:00.0 Gen5x16 5.0s n=2.52e+12 E=0 n=2.519e+12 | CL=1.0000 (target 0.95) | BER<=1.19e-12 | PASS
```

Translate the fields:

| Field | Meaning |
|-------|---------|
| `Gen5x16` | The link is PCIe Gen5 (32 GT/s) at 16 lanes. |
| `5.0s` | The exercise window ran for 5 seconds. |
| `n=2.52e+12` | 2.52 trillion bits transferred. |
| `E=0` | Zero correctable errors during the window. |
| `CL=1.0000 (target 0.95)` | The statistical confidence that BER ≤ target reached 1.0 against a 0.95 ask. |
| `BER<=1.19e-12` | The chi-squared upper bound on BER, given E and n. |
| `PASS` | A real fault would print `FAIL UNCORR:<type>`; a missing AER capability prints `SKIP`. |

The exit code is `0` for PASS, `1` for FAIL, `5` for SKIP. Pipe-friendly.

## Step 4 — Add `--explain` to read the reasoning

```bash
computetest bert -d 0000:03:00.0 --target-ber 1e-12 --max-seconds 5 --explain
```

Now the verdict comes with a multi-line breakdown of what was measured and
why the toolkit thinks the link is healthy. The `--explain` block also
prints the **Gen6 FEC caveat** when the device is a Gen6+ link — try it on
`0000:08:00.0`:

```bash
computetest bert -d 0000:08:00.0 --target-ber 1e-6 --max-seconds 2 --explain
```

The Gen6 case prints `pre_fec_symbol_errors`, `post_fec_flit_errors`, and
the post-FEC FBER alongside the AER count, and a four-paragraph note about
why PCIe 6.0 reliability is measured differently. See
[explanation/gen6-fec.md](../explanation/gen6-fec.md) for the full theory.

## Step 5 — Emit a portable result stream

`computetest` can emit its results in the
[OCP ocp-diag-core](https://github.com/opencomputeproject/ocp-diag-core) v2.0
JSONL schema — the shape hyperscale MT pipelines already ingest. Try:

```bash
computetest bert -d 0000:03:00.0 --target-ber 1e-12 --max-seconds 5 --ocpdiag - | jq -c '. | {seq: .sequenceNumber, kind: (keys[] | select(. != "sequenceNumber" and . != "timestamp"))}'
```

You'll see a handful of `schemaVersion`, `testRunArtifact`, and
`testStepArtifact` lines in monotonic sequence-number order — exactly what
the OCP spec describes. The human-readable BERT summary stays on stderr so
your `jq` pipe stays clean.

See [howto/emit-ocp-diag.md](../howto/emit-ocp-diag.md) for the full set
of `--ocpdiag` patterns.

## Step 6 — Diagnose the whole board at once

```bash
computetest diagnose --no-bert
```

This runs the link health + AER snapshot + lane-margining checks across
every device the mock backend exposes. Without `--no-bert` the diagnostic
also runs a BERT on each device, which is more thorough but slower.

## Where to go next

* **Real hardware** — [howto/run-on-real-hardware.md](../howto/run-on-real-hardware.md)
  walks through the privileged setup and the AER capability requirements.
* **Test plans** — [howto/write-test-plan.md](../howto/write-test-plan.md)
  covers the YAML config format the `plan` subcommand consumes.
* **Confidence math** —
  [explanation/bert-confidence.md](../explanation/bert-confidence.md) explains
  why the verdict is what it is.
* **CLI reference** — [reference/cli.md](../reference/cli.md) is the
  authoritative flag-by-flag surface.

If you got a `SKIP` verdict in step 3, the device's link rate was
unreadable from `/sys` (the mock never produces SKIP on the default board).
[howto/triage-bert-fail.md](../howto/triage-bert-fail.md) covers the SKIP
cases and the AER-source fallbacks.
