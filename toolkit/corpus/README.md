# Vendor-tool output corpus

Captured (or capture-shaped) output from the vendor CLIs the toolkit parses, replayed
through the **real** code path (`check_nvme`/`check_ethernet` with `mock=False`, the
subprocess stubbed to return these bytes) by `tests/test_parsers_corpus.py`.

Two jobs:
1. **Exercise the real parsers off-hardware** — the previously `# pragma: no cover` decode
   path (`subprocess` → `json.loads`/text parse → `_normalize_smart_keys`/`_stat` → checks)
   runs on every commit, on the laptop.
2. **Drift tripwire** — when a tool version changes a key/format the parser relies on,
   adding that version's capture turns this suite red, *before* it ships to a test station.

## Layout & provenance

```
corpus/<tool>/<version>/<model-or-scenario>/<artifact>
  nvme/2.11/samsung-pm9a3/{smart-log.json,id-ctrl.json}
  ethtool/6.7/bcm89xx-good/{link.txt,stats.txt}
```

The path *is* the provenance: tool, tool version, device/scenario. Keep one capture per
tool version you support so a cross-version diff is visible (e.g. `nvme/2.10` vs `nvme/2.11`).

## Honesty note — these seeds are documented-format, not live captures

These initial files are hand-built to match the **documented real output formats** (real
nvme-cli SMART keys: `avail_spare`/`spare_thresh`/`percent_used`, temperature in **Kelvin**;
real `ethtool -S` counter names incl. an `rx_errors_phy` superstring decoy). They are good
enough to pin the decode and the known bug classes, but they are **not** a substitute for
output captured from real tools on real silicon.

Replace/augment them with real captures via the scheduled re-capture job
(`.github/workflows/corpus-refresh.yml`, Phase 1 "continuous") or a manual capture on a
real Linux box / Zoox test station:

```sh
nvme smart-log /dev/nvme0 -o json  > corpus/nvme/<ver>/<model>/smart-log.json
nvme id-ctrl   /dev/nvme0 -o json  > corpus/nvme/<ver>/<model>/id-ctrl.json
ethtool eth0                       > corpus/ethtool/<ver>/<model>/link.txt
ethtool -S eth0                    > corpus/ethtool/<ver>/<model>/stats.txt
```

Regenerate deliberately and **review the diff** — a changed expected value is the signal.

## What the current seeds cover

- `nvme/2.10` vs `nvme/2.11` — same drive, 2.11 adds fields (`endurance_grp_critical_warning_summary`,
  `thm_temp*`); both must decode to the same verdict (forward-compatibility to added keys).
- `nvme/.../used-stock-drive` — high power-on-hours / unsafe-shutdowns / media-errors: fails the
  new-drive gates and raises used-stock **history** flags.
- `ethtool/.../bcm89xx-degraded` — `rx_errors: 40` alongside a decoy `rx_errors_phy: 999`: the real
  path must report 40 (the exact-match guard), end to end.
