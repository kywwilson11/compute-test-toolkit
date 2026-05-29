# OCP `nvme-cli internal-log` corpus

Each subdirectory is one known drive family's verbatim
`nvme ocp internal-log -o json` output — a snapshot for regression-testing
the parser in `computetest/nvme/ocp_telemetry.py`. Drop a new directory in
when you want to pin a real drive's decode.

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
