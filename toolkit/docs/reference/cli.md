# CLI reference

Authoritative reference for every `computetest` subcommand, flag, and exit
code. This is information, not narrative — for narrative, see
[tutorials/01-first-bert.md](../tutorials/01-first-bert.md) or the
relevant [how-to](../howto/).

## Invocation

```
computetest [GLOBAL FLAGS] <subcommand> [SUBCOMMAND FLAGS]
```

Subcommand flags must appear AFTER the subcommand name (argparse-with-parents
quirk). `computetest --json bert -d X` won't capture `--json`;
`computetest bert -d X --json` will.

If you have not run `pip install -e .`, substitute
`PYTHONPATH=src python3 -m computetest.cli` for `computetest`.

## Common flags

Every subcommand accepts these:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--json` | switch | off | Emit JSON to stdout instead of human text. |
| `--backend {auto,mock,real}` | choice | `auto` | Force the backend. `auto` selects `real` iff `/sys/bus/pci` exists, else `mock`. Overrideable via `$COMPUTETEST_BACKEND`. |
| `--ocpdiag PATH` | path | none | Also emit an OCP ocp-diag-core JSONL stream to PATH (`-` for stdout, which reroutes human output to stderr). |
| `--ocpdiag-serial DUT` | string | `UNKNOWN` | DUT serial for the ocp-diag `testRunStart.dutInfo`. |
| `--ocpdiag-station STATION` | string | `station-1` | Test station name for `testRunStart.dutInfo`. |

## Exit codes

The toolkit follows a consistent exit-code contract across every subcommand:

| Code | Symbol | Meaning |
|------|--------|---------|
| 0 | `EXIT_PASS` | The verdict was `pass`. |
| 1 | `EXIT_FAIL` | The verdict was `fail` (DUT failed, not the program). |
| 2 | `EXIT_USAGE` | Usage error — bad flag, bad value, missing argument. |
| 3 | `EXIT_NOTFOUND` | Device or target not found (`KeyError` at lookup). |
| 4 | `EXIT_IO` | Config or I/O failure (file not found, JSON parse error, DB error). |
| 5 | `EXIT_UNAVAIL` | Capability unavailable on this backend/hardware — verdict was `skip` (we couldn't measure). |
| 130 | (SIGINT) | Ctrl-C interrupt. |

A run that produces *several* verdicts (`diagnose`, `plan`) aggregates: any
fail wins, else any skip wins, else pass.

## Subcommands

### `list`

Enumerate the PCIe devices the backend can see.

```
computetest list [--json]
```

* Without `--json`: a human-readable table (BDF, vendor name, link
  speed/width, class code, driver).
* With `--json`: a list of `PciDevice.to_dict()` records.

### `bert`

Run the PCIe Bit Error Rate Test on one device.

```
computetest bert -d BDF [--target-ber 1e-12] [--confidence 0.95]
                  [--max-seconds 30.0] [--engine python|c] [--explain]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-d`, `--bdf` | str (required) | — | Device BDF (e.g. `0000:03:00.0`). |
| `--target-ber` | float | `1e-12` | BER ceiling the verdict is asked to prove. |
| `--confidence` | float | `0.95` | Confidence the verdict targets. |
| `--max-seconds` | float | `30.0` | Cap on the exercise window. |
| `--engine` | choice | `python` | `python` (Python conductor) or `c` (compiled C engine for high poll rates). |
| `--explain` | switch | off | Print the multi-line verdict reasoning + Gen6 FEC caveat when relevant. |

Returns a `BertResult` (see [reference/result-shapes.md](result-shapes.md)).

### `diagnose`

Full PCIe diagnostic across all devices (or one).

```
computetest diagnose [-d BDF] [--target-ber 1e-12]
                     [--no-bert] [--no-margin] [--max-seconds 30.0]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-d`, `--bdf` | str | (all) | Limit to one device. |
| `--target-ber` | float | `1e-12` | BER target for the included BERT. |
| `--no-bert` | switch | off | Skip the BERT step (link state + AER snapshot + margining only). |
| `--no-margin` | switch | off | Skip lane margining. |
| `--max-seconds` | float | `30.0` | Per-device BERT exercise cap. |

Returns a list of `PcieDiagnostic`s.

### `chain`

Diagnose every link in an endpoint's path (root port → endpoint).

```
computetest chain ENDPOINT_BDF [--target-ber 1e-12] [--confidence 0.95]
                  [--max-seconds 30.0]
                  [--expected-speed N] [--expected-width N]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `endpoint` | str (positional, required) | — | Endpoint BDF; the chain expands automatically. |
| `--target-ber` | float | `1e-12` | BER target. |
| `--confidence` | float | `0.95` | Confidence target. |
| `--max-seconds` | float | `30.0` | BERT cap. |
| `--expected-speed` | int | none | Expected Gen number (1–6). Mismatch fails. |
| `--expected-width` | int | none | Expected lane width. Mismatch fails. |

Returns a `ChainDiagnostic`.

### `nvme`, `gpu`, `gmsl`, `eth`, `can`

Per-subsystem health checks. Each takes a positional `target`
(`/dev/nvme0`, GPU index, GMSL link string, network interface, CAN
interface respectively):

```
computetest nvme /dev/nvme0
computetest gpu  0
computetest gmsl 1-0029
computetest eth  eth0
computetest can  can0
```

Each returns a subsystem-specific Health dataclass. Exit code 1 if `.ok`
is False.

### `plan`

Run a full test plan from a config.

```
computetest plan CONFIG [--db PATH] [--serial DUT_SERIAL] [--station NAME]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `config` | str (positional, required) | — | YAML or JSON test plan. See [howto/write-test-plan.md](../howto/write-test-plan.md). |
| `--db` | path | `:memory:` | SQLite path for the results store. |
| `--serial` | str | `DUT-DEMO` | DUT serial tagged on every recorded row. |
| `--station` | str | `station-1` | Station name tagged on every row. |

Returns a `TestReport`. Exit code aggregates per-test verdicts.

### `ber`

BER confidence math only — pure calculation, no hardware.

```
computetest ber [--target-ber 1e-12] [--confidence 0.95]
                [--errors N] [--bits N]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--target-ber` | float | `1e-12` | Target. |
| `--confidence` | float | `0.95` | Confidence. |
| `--errors` | int | `0` | Observed error count. |
| `--bits` | float | none | Transferred bit count. With `--bits`, returns the `BertVerdict`. Without, returns the bits-needed-to-prove number. |

### `lmt`

PCIe Lane Margining — pci_lmt-compatible output.

```
computetest lmt -d BDF [--limit-ui 0.25] [--error-count-limit 63]
                [--dwell-time 5] [--receiver-number 1]
                [--format json|csv] [--via-pci-lmt CONFIG]
```

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-d`, `--bdf` | str (required) | — | Device BDF. |
| `--limit-ui` | float | `0.25` | Per-lane minimum timing margin in UI. |
| `--error-count-limit` | int | `63` | OCP `pci_lmt -e`: stop a lane at this many errors. |
| `--dwell-time` | int | `5` | OCP `pci_lmt -d`: seconds per margining step. |
| `--receiver-number` | int | `1` | PCIe LMT receiver number (1–6). |
| `--format` | choice | `json` | `pci_lmt -o`: `json` or `csv`. |
| `--via-pci-lmt` | path | none | Shell out to the OCP `pci_lmt` binary with this YAML config (requires `pip install ocptv-pci_lmt`). |

Returns a list of `LmtLaneRecord`s in the canonical 24-column OCP pci_lmt
schema. See [howto/emit-ocp-diag.md](../howto/emit-ocp-diag.md) for the
ocp-diag emission shape.

## Environment variables

| Name | Effect |
|------|--------|
| `COMPUTETEST_BACKEND` | Same as `--backend`. `mock`/`real`/`auto`. |
| `COMPUTETEST_SYSROOT` | Override `/sys/bus/pci/devices` (testing). |

## Examples

```bash
# Mock board, every device
computetest --backend mock list

# BERT on a Gen5 GPU, full explain + ocp-diag stream
computetest bert -d 0000:03:00.0 --target-ber 1e-12 --max-seconds 30 \
    --explain --ocpdiag run.jsonl

# Diagnose every device on the board, no BERT
sudo computetest diagnose --no-bert --json | jq .

# Chain diagnostic with strict Gen5 x16 expectation
sudo computetest chain 0000:03:00.0 \
    --expected-speed 5 --expected-width 16 --max-seconds 30

# Lane margining in pci_lmt CSV shape
sudo computetest lmt -d 0000:03:00.0 --format csv > lmt.csv

# Full plan, recording to SQLite + OCP stream
sudo computetest plan configs/example_plan.json \
    --db /var/lib/computetest/results.db \
    --serial SN1234 --station bay-3 \
    --ocpdiag /var/log/computetest/SN1234.jsonl
```
