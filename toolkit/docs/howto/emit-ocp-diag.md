# How to emit OCP ocp-diag-core output

`computetest` can emit its results as a
[OCP ocp-diag-core](https://github.com/opencomputeproject/ocp-diag-core) v2.0
JSONL stream — the portable schema hyperscale MT pipelines (Google, Meta,
the OCP DC NVMe lane) already ingest.

## The flag

Every subcommand accepts `--ocpdiag PATH`:

```bash
# Write to a file. Human output stays on stdout.
computetest bert -d 0000:03:00.0 --ocpdiag /tmp/run.jsonl

# Write to stdout. Human output reroutes to stderr so stdout is pure JSONL.
computetest bert -d 0000:03:00.0 --ocpdiag - | jq -c '.testStepArtifact?.measurement?.name // empty'

# Tag the DUT in the testRunStart.dutInfo.
computetest bert -d 0000:03:00.0 --ocpdiag run.jsonl \
    --ocpdiag-serial SN1234 --ocpdiag-station bay-3
```

## What the stream looks like

One JSON object per line, in this order:

1. `schemaVersion` — pins `{major: 2, minor: 0}`. Always first.
2. `testRunArtifact.testRunStart` — name + version + command line +
   parameters + DUT info.
3. `testStepArtifact.testStepStart` — opens a step.
4. `testStepArtifact.measurement` — one per measurand. Validators
   (e.g. `LESS_THAN_OR_EQUAL 1e-12`) attach to `ber_upper_bound` so the OCP
   verifier can confirm the verdict mechanically.
5. `testStepArtifact.diagnosis` — the verdict, with `type` in
   `PASS|FAIL|UNKNOWN`.
6. `testStepArtifact.testStepEnd` — closes the step with
   `status` in `COMPLETE|ERROR|SKIP`.
7. `testRunArtifact.testRunEnd` — closes the run with
   `result` in `PASS|FAIL|NOT_APPLICABLE`.

Every artifact carries `sequenceNumber` (monotonic from 0) and `timestamp`
(RFC-3339 with millisecond precision). The schemas are vendored at
`src/computetest/io/ocpdiag_schemas/` and the in-tree pytest validates
every emitted artifact against them — drift in our emitter fails the
build.

## Validate a stream against the official schemas

```python
import json, jsonschema
from referencing import Registry, Resource

# Load the vendored schemas
import pathlib
schema_dir = pathlib.Path("src/computetest/io/ocpdiag_schemas")
registry = Registry().with_resources([
    (json.loads(p.read_text())["$id"], Resource.from_contents(json.loads(p.read_text())))
    for p in schema_dir.glob("*.json")
])
root = registry.contents("https://github.com/opencomputeproject/ocp-diag-core/output")
validator = jsonschema.Draft202012Validator(root, registry=registry)

# Validate
with open("run.jsonl") as f:
    for line in f:
        artifact = json.loads(line)
        errors = list(validator.iter_errors(artifact))
        assert not errors, f"{artifact} -> {[e.message for e in errors]}"
```

## Recipe — diff two runs

The strictly ordered shape makes diff-of-runs trivial:

```bash
# Run twice
computetest bert -d 0000:03:00.0 --ocpdiag run1.jsonl
computetest bert -d 0000:03:00.0 --ocpdiag run2.jsonl

# Diff just the measurements and verdicts
jq -c '.testStepArtifact?.measurement // .testStepArtifact?.diagnosis // empty' run1.jsonl > /tmp/m1.jsonl
jq -c '.testStepArtifact?.measurement // .testStepArtifact?.diagnosis // empty' run2.jsonl > /tmp/m2.jsonl
diff /tmp/m1.jsonl /tmp/m2.jsonl
```

## Recipe — feed into BigQuery / Looker

The OCP shape is already on hyperscale ingest paths. Cast a per-run JSONL
to one row per measurement:

```bash
jq -c '
  select(.testStepArtifact?.measurement)
  | {
      ts: .timestamp,
      step_id: .testStepArtifact.testStepId,
      name: .testStepArtifact.measurement.name,
      value: .testStepArtifact.measurement.value,
      unit: .testStepArtifact.measurement.unit,
      validators: .testStepArtifact.measurement.validators
    }' run.jsonl
```

Pipe that into your warehouse loader of choice.

## Recipe — `plan` runs

`plan` emits one testStep per `TestRecord` (one per `pcie/<bdf>:<name>`,
one per `nvme/<dev>:smart`, ...). The `testRunStart.parameters` block
captures the full `--serial`/`--station`/`--db`/`--config` invocation, so a
downstream consumer can join runs across stations on serial without
parsing the file path.

```bash
sudo computetest plan configs/example_plan.json \
    --db results.db --serial SN1234 --station bay-3 \
    --ocpdiag /var/log/computetest/SN1234.jsonl
```

## Recipe — `lmt` runs

The `lmt` subcommand emits **both** its own pci_lmt-compatible payload on
stdout (JSON or CSV per `--format`) AND the ocp-diag stream (via
`--ocpdiag`). Per-lane measurements are emitted as
`lmt.<timing|voltage>.lane<N>.<column>` so a downstream consumer can
column-join against the pci_lmt schema.

```bash
sudo computetest lmt -d 0000:03:00.0 --format json \
    --ocpdiag /var/log/computetest/lmt-SN1234.jsonl > lmt-SN1234.json
```

## Limitations / honest signal

* The toolkit's `--ocpdiag` is **additive**: it doesn't replace `--json` or
  the human output. If you need a pure-OCP pipe, use `--ocpdiag -` (which
  reroutes human output to stderr) plus `2>/dev/null` to drop the noise.
* The vendored schemas are pinned to ocp-diag-core `main` as of
  2026-05-29. Refresh `src/computetest/io/ocpdiag_schemas/` when upstream
  cuts a new tag; the README in that directory has the curl recipe.
* Strings/booleans/numbers are the only measurement value types the OCP
  spec allows. Nested dicts and lists are not. The emitter's adapters
  flatten one level of `to_dict()` and otherwise raise — that's by design,
  not a bug.

## Where to go next

* [reference/ocp-diag-emitter.md](../reference/ocp-diag-emitter.md) —
  the full Emitter + adapter API.
* [explanation/bert-confidence.md](../explanation/bert-confidence.md) —
  what the `ber_upper_bound` measurement actually represents.
