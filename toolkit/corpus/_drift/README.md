# corpus/_drift/ — schema drift signatures (automated)

This directory is the **drift tripwire**: a small set of files whose content is the
*shape* (key set + tool version) of the vendor-tool outputs `computetest` parses.
A weekly GitHub Actions workflow (`.github/workflows/corpus-refresh.yml`) installs
the latest `nvme-cli` + `ethtool` on an Ubuntu runner, captures outputs against
**real Linux kernel devices** (nvme-loop for nvme; the runner's NIC for ethtool),
distills them to the sorted key list, and opens a PR if anything here changed.

Why key-lists, not full captures: the human-curated value corpus at
`corpus/nvme/<ver>/<model>/...` and `corpus/ethtool/<ver>/<model>/...` must only
change when a real engineer captures from a real device — its **values** matter
(a Samsung enterprise drive's `temperature` of 323K is real test data). The
**schema** signal — "the tool's output shape changed; you may need to update the
parser" — is exactly the sorted key list, automatable without a real device.

Files committed here (created on the first workflow run):

| File | What |
|------|------|
| `nvme-version.txt` | `nvme --version` from the runner's apt-latest |
| `ethtool-version.txt` | `ethtool --version` from the runner's apt-latest |
| `nvme-smart-log-keys.txt` | sorted top-level key list of `nvme smart-log -o json` |
| `nvme-id-ctrl-keys.txt` | sorted top-level key list of `nvme id-ctrl -o json` |
| `ethtool-stat-keys.txt` | sorted counter names from `ethtool -S <iface>` |

**What a drift PR means.** A diff to a `*-keys.txt` file means the tool's output
shape changed — usually a new counter or a renamed SMART key (e.g. nvme-cli's
2.10 → 2.11 `available_spare` → `avail_spare` rename, the exact bug class the
toolkit's `_normalize_smart_keys` handles). When you see a drift PR:

1. Read the diff. Is the change additive (a new key) or destructive (a renamed
   key)?
2. If destructive: does `tests/test_parsers_corpus.py` cover the old shape AND
   the new shape? If not, capture fresh value corpus from a real station,
   update the parser if needed.
3. Merge the PR to establish the new baseline.

A diff to a `*-version.txt` file alone (without a corresponding key-list diff) is
informational — the tool got a patch update without changing its schema.
