# OCP `nvme-cli internal-log` corpus

Each subdirectory is a snapshot for regression-testing the parser in
`computetest/nvme/ocp_telemetry.py`. A real capture is verbatim
`nvme ocp internal-log -o json` output; the `synthetic-mock-1tb` case is the
in-module mock decode (`_MOCK_TELEMETRY` / `_MOCK_STRINGS`), kept as a
mock-only regression fixture — its key shape (`dataAreas`/`sizeBlocks`/
`header.version`) is the toolkit's projection, not the spaced human-readable
keys nvme-cli's OCP plugin actually emits. Drop a new directory in when you
want to pin a real drive's decode.

Layout:

```
<vendor-family>/
  telemetry.json   # `nvme ocp internal-log <dev> -o json --telemetry-log`
  strings.json     # `nvme ocp internal-log <dev> -o json --string-log`
  expected.json    # the toolkit's expected projection (asserted in tests)
```

Refresh cadence: when a new drive family enters the lab, OR when nvme-cli
gains a major version. The corpus-refresh CI job (Sprint 1 corpus-refresh
lane) already captures `nvme-cli` version drift; this corpus stays
manually curated until a drive-attached station feeds it.
