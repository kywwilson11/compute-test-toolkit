# Real-Path Testing — Design

> **Status:** design, revised after a research pass (May 2026). This supersedes the
> earlier draft whose centerpiece was a FUSE config-space simulator. The reversals and
> their rationale are called out in [Key decisions](#key-decisions-and-why). The short
> version: a **C-level unit layer was missing and is now the cheapest, highest-value
> foundation**; the high-fidelity centerpiece is **QEMU + QMP AER injection, not FUSE**;
> and Phase 1 is reframed from "hand-authored fakes" to **verified fakes** (contract tests
> + a real-output corpus), because that is the only thing that catches the *version-drift*
> bug class we keep hitting.

## Purpose

Exercise the **real-hardware code paths** that are currently `# pragma: no cover` — the
sysfs reads, the vendor-tool output parsing, and the C engine's write-1-to-clear (W1C)
count loop — against *behaviorally faithful* inputs, so they **run in CI** instead of being
reviewed-but-never-executed.

This targets a specific, recurring bug class: every audit finding so far lived in real-path
code the mock bypasses, and none of our tests execute it. Concretely, in this repo today:

- `nvme.py:107` `_normalize_smart_keys()` (the `avail_spare`→`available_spare`,
  `percent_used`→`percentage_used` aliasing) is `# pragma: no cover`. **Delete the alias map
  and every mock test still passes** — because the mock emits canonical keys directly
  (`nvme.py:83`). That is the nvme-cli 2.11 JSON-shift bug reproduced as a coverage hole.
- `ethernet.py:84+` — the whole `ethtool -S` branch and `_stat()`'s exact-match-before-`:`
  guard (which defeats `rx_errors_phy` shadowing) are real-path-only, so the guard is
  **untested**.
- `backend.py:216` `RealBackend` — all of sysfs + config-space parsing — is one big
  `# pragma: no cover`.

The mock proves the *decisions* are right. This work proves the *glue that talks to Linux
and to vendor tools* is right. The bench proves the *electrons* are right. Three distinct
jobs; this doc is about the middle one — plus the one job nobody was doing (unit-testing the
C engine's algorithm).

## The layered model

| Layer | Proves | Where it runs | Status |
|---|---|---|---|
| **0 — Algorithm** | pure logic: C W1C loop, cap-list walks, parsers, confidence math | **macOS + Linux**, deterministic, no privilege | C side **missing today** (this adds it); Python side = `MockBackend`, ~99% |
| **1 — Verified fakes** | the *binding + drift*: sysfs read/parse, tool-output decode, Mock↔Real consistency | **macOS + Linux**, no privilege | the main gap we're closing |
| **2 — High fidelity** | the real kernel binding: unmodified binary + `RealBackend` vs. real kernel-generated sysfs/config, true W1C, injected AER | **Linux CI** (QEMU/TCG) or self-hosted | the gold-standard lane |
| **Bench** | electrons: real error rates, signal integrity, loop-latency-vs-undercount timing | the lab | out of scope, always |

The guiding principle: **maximize cheap, deterministic algorithm coverage (Layer 0–1, runs
on the laptop) and buy exactly one real-binding proof at the right fidelity (Layer 2).** Layers
0 and 1 retire most of the `# pragma: no cover` risk *before* we touch the heavier Layer 2.

## Key decisions (and why)

These are the substantive changes from the first draft.

1. **Add a C-level unit layer — and do it first.** The speed-critical C engine
   (`c/pcie_bert.c`) — the thing the whole tool is built around — has **zero** unit coverage
   of `read_and_clear()`, `find_ext_cap()`/`find_cap()`, the `UNC_EVERY` sampling cadence, or
   the popcount-once-per-type accounting, while `MockBackend` covers the *Python* equivalents
   at ~99%. That asymmetry is exactly where width-correct W1C (4-byte AER vs. 2-byte Device
   Status, `pcie_bert.c:98`) and linked-list-walk bugs hide. Refactor config access behind a
   `read_reg`/`write_reg` seam (the C mirror of the `Backend` ABC we already trust) and
   unit-test the loop against an in-memory register model. Deterministic, zero privilege,
   **runs on the macOS laptop.** Cheapest dollar-per-bug on the board.

2. **The high-fidelity centerpiece is QEMU + QMP AER injection — not FUSE.** A FUSE
   config-space sim is a *bespoke register model we maintain*, so it can encode the same wrong
   assumptions our code has, and it carries a page-cache gotcha (without `direct_io`, a read
   after a W1C write returns a stale cached value → a phantom "stuck bit"). QEMU's device-model
   AER is modeled *independently* of us and runs against a *real kernel*: injecting via the
   `pcie_aer_inject_error` monitor command sets `PCI_ERR_COR_STATUS`/`PCI_ERR_UNCOR_STATUS` in
   emulated config space, and `w1cmask` implements true W1C — so our **unmodified** C binary and
   `RealBackend` `pread`/`pwrite` the real kernel-generated `/sys/.../config` and the entire
   arm→poll→clear→count loop runs for real. FUSE drops to an *optional* fast bridge if Layer 2
   is slow to land.

3. **Phase 1 is "verified fakes," not "hand-authored fakes."** A hand-written `fakebin/nvme`
   shim **cannot** catch version drift by construction: it emits what we *think* the tool emits,
   and the drift bug is precisely the gap between that belief and reality (that is why a shim
   would have stayed green through the nvme-cli 2.11 shift). The drift defense is instead:
   (a) a **contract suite** running one behavioral test over *both* `MockBackend` and
   `RealBackend(sys_root=fixture)`, forcing them to agree; and (b) a **versioned corpus of real
   captured tool outputs** replayed through the parsers, refreshed on the tools' release cadence.
   The `fakebin/` shims stay, demoted to a *plumbing* smoke test (subprocess wiring,
   `shutil.which`, timeout/error handling).

4. **Rejected / demoted, with reasons:**
   - **`aer-inject` (kernel module):** wrong tool. It hooks the *kernel's* AER handler; a
     userspace `pread` of config space never sees the injected bits — which is exactly what our
     binary and `RealBackend` do. Easy to wire up expecting it to work; it won't.
   - **QEMU `edu` device:** legacy PCI, no AER, no extended config space — useless for this path.
   - **LD_PRELOAD shim:** more fragile than FUSE (silent breakage when `openat`/symbol coverage
     drifts), lower fidelity than QEMU, and risky to preload into `python3`. Dropped.
   - **FUSE:** demoted from centerpiece to optional fast bridge (see #2).

5. **CI reality reshapes the Linux lane.** GitHub-hosted runners **cannot `modprobe`** (no
   `CAP_SYS_MODULE`; `netdevsim` isn't even shipped, `vcan`/`nvmet`/`null_blk` won't load) and
   have **no KVM / nested virt**. So the original "Docker + vcan + nvmet" phases can't run on
   hosted runners at all — they need a **QEMU guest** (where we have root and can modprobe
   freely) or a self-hosted runner. The QEMU guest is the *same vehicle* as the AER lane, so the
   old Phases 2/3/4 collapse into **one QEMU Linux-guest lane**.

## Fidelity tiers — spend effort only where "real" buys coverage

| Interface | Worth making real (real kernel path) | Honest ceiling — just fake it |
|---|---|---|
| **PCIe BERT / AER** | QEMU device behind `pcie-root-port` + `pcie_aer_inject_error` → real config space, true W1C | — |
| **NVMe** | `nvme-loop` (nvmet) for real `nvme-cli` round-trip; **QEMU-NVMe** for controllable SMART fault *values* (`smart_critical_warning`, temp via QMP) | SMART *values* via `null_blk`/`scsi_debug` (wrong protocol — SCSI, no NVMe admin path) |
| **CAN** | `vcan`/`vxcan` + can-utils: real SocketCAN frame TX/RX, replay, **error-frame parsing** (`canerrsim`) | bus-state / TEC / REC / bus-off — impossible without silicon; fake the `ip -details link` text |
| **Ethernet** | `netdevsim`: real `ethtool -S` driver stats, FEC, pause, link settings | cable-test / TDR — PHY-driver only, no virtual device; fake the ethtool-netlink reply |
| **DRAM / EDAC** | **EINJ inside QEMU** → real `/sys/.../edac` event | EDAC on a generic VM (needs real Intel MC + BIOS) |
| **GPU (NVIDIA), GMSL** | — (no emulation exists; GSP firmware is mandatory, driver needs real silicon) | fake `nvidia-smi` / recorded `--query-gpu` CSV; fake `/sys` |

Takeaway: for GPU/GMSL, CAN bus-state, ethtool TDR, and EDAC-on-a-generic-VM, a well-built
fake is the *correct* choice — kernel realism buys no additional coverage. Keep those fakes
*honest* via the corpus (Phase 1), don't try to make them "real."

## Architecture — the injection points

Everything hinges on pointing the real paths at controllable inputs:

1. **sysfs root (Python)** — `RealBackend(sys_root=…)`, default `$COMPUTETEST_SYSROOT` then
   `/sys/bus/pci/devices`. Replaces the hardcoded `RealBackend.SYS` class attribute
   (`backend.py:219`). `select_backend()` passes the env through and auto-selects Real when the
   root exists. **This one change lets the entire real sysfs path run on macOS against a fixture
   tree.**
2. **sysfs root (C)** — replace the `SYS_PCI` macro (`pcie_bert.c:42`) with a runtime
   `const char *sys_root`, set by a new `-r <dir>` flag or `$COMPUTETEST_SYSROOT`.
3. **config-space access (C)** — route `cfg_read`/`cfg_write` (`pcie_bert.c:67,78`) through a
   `read_reg`/`write_reg` function-pointer seam, so Layer-0 tests inject an in-memory register
   model and the production path injects the real `pread`/`pwrite`.
4. **vendor tools** — already invoked by bare name (`nvme`, `ethtool`, …), so a fake on `PATH`
   overrides them with zero code change. Used for *plumbing* tests only.
5. **backend selection** — tests/containers set `COMPUTETEST_BACKEND=real` (or rely on
   auto-select against a fake `/sys`) so the pragma'd code actually runs.

## Phases

### Phase 0 — Algorithm coverage *(runs on macOS; do first)*
The missing foundation. No I/O, no privilege, fully deterministic.

- **C dependency-injection refactor:** introduce the `read_reg`/`write_reg` seam in
  `pcie_bert.c` (behavior-preserving; the default impls are today's `pread`/`pwrite`).
- **C unit tests:** vendor **Unity** (single-file C test framework — no dependency, builds on
  macOS and in CI; `cmocka` is the alternative if we later want call-sequencing mocks, but the
  function-pointer seam makes that unnecessary). Back the seam with `fake_cfgspace.c` — an
  in-memory `uint8_t cfg[4096]` with true W1C, a configurable stuck bit, and a scriptable
  latch — and test: `read_and_clear()` width-correctness (4B AER vs 2B Device Status, no
  adjacent-register clobber), `find_ext_cap()`/`find_cap()` walks (including self-pointing /
  looping next-pointers terminate), the `UNC_EVERY` cadence, popcount-once-per-type accounting,
  and `valid_bdf()`.
- **Python property tests (Hypothesis):** on the pure parsers — `_stat()` (assert exactness vs.
  injected `rx_errors_phy`/`_extra` decoys; never raises on `st.text()`, incl. the
  `str.isdigit()`-accepts-superscripts crash), `_parse_speed()` (`"2.5G"→2500`, `"1000Mb/s"→1000`),
  `_normalize_smart_keys()` (canonical and abbreviated key sets yield identical checks), and
  `find_ext_cap()` (arbitrary 4 KB blobs always terminate). Seed each with `@example(...)`
  pinning the three real bugs (`avail_spare`, `rx_errors_phy`, Kelvin temperature) as
  permanent regressions.
- **Outcome:** the C W1C algorithm and the parser internals are covered, deterministically,
  on the laptop.

### Phase 1 — Verified fakes *(runs on macOS; the main drift defense)*
- **Enabler refactor:** sysfs-root injection in `RealBackend`; `-r/--root` +
  `$COMPUTETEST_SYSROOT` in the C engine (defaults unchanged; all current tests stay green).
- **sysfs fixture builder:** `tests/sysfs_fixture.py` builds a fake `/sys` tree in `tmp_path`
  with **real symlinks** (so `link_chain`'s `realpath` nesting and the `driver` readlink work):
  byte-accurate config space (PCIe cap + AER ext-cap at realistic offsets with set/clear-able
  status bits), `current_link_speed`/`current_link_width`/`vendor`/`device`/`class`, a
  `driver` symlink, and a root→endpoint parent chain. `profile="golden"|"degraded"|"errored"`.
  Use `tmp_path` (real symlinks, cross-platform), **not pyfakefs** (can't touch the C path,
  doesn't emulate sysfs read semantics); pack any committed tree as a tarball/manifest (git
  mangles sysfs symlinks — this is why node_exporter uses `ttar`).
- **Contract suite (the linchpin):** `tests/test_backend_contract.py` — one behavioral suite
  parametrized over `[MockBackend(dev), RealBackend(sys_root=fixture_of_same_dev)]`, asserting
  identical observable results (`get_device`, `speed_str`, `find_ext_cap`, `link_chain` order,
  `read_link_status`, W1C clear). If `RealBackend`'s parser drifts from what the mock promises,
  this goes red — *on the laptop*. This is the structural fix for the `avail_spare` class.
- **Parser corpus:** `corpus/<tool>/<version>/<model>/…` holds **real captured outputs**
  (`nvme smart-log -o json`, `id-ctrl`, `self-test-log`; `ethtool`/`ethtool -S` incl. an
  `rx_errors_phy` decoy; `nvidia-smi --query-gpu`). `tests/test_parsers_corpus.py` stubs
  `subprocess.run` to return each fixture and asserts the decoded result equals the mock's for
  the same logical device. Regenerate deliberately (`--update`), review the *diff*; store
  provenance (tool `--version`, model, date) in the path. Use `syrupy`/`pytest-regressions` for
  the update ergonomics.
- **Demote `fakebin/`:** `sim/fakebin/{nvme,ethtool,nvidia-smi,ip}` stay as a *plumbing* smoke
  test (subprocess wiring, `which`, timeout/error paths) — no longer the drift defense.
- **Outcome:** the real sysfs + parser glue runs and is covered on macOS; Mock and Real are
  provably consistent; the drift-bug class is caught automatically.

### Phase 2 — QEMU high-fidelity lane *(Linux CI, TCG; the binding proof)*
- `sim/qemu/run_guest.sh` boots a minimal kernel + initramfs under **TCG** (no KVM/nested-virt
  needed on hosted runners; ~low-single-digit minutes for our short tests) with a device behind a
  `pcie-root-port` so the AER capability exists.
- `sim/qemu/inject.py` drives the QMP socket: `pcie_aer_inject_error` (correctable/uncorrectable,
  timed to land *while* the engine's loop runs so bits latch between read/clear).
- `sim/qemu/guest_tests.sh` runs, **inside the guest**, the *unmodified* C engine
  (`-r /sys/bus/pci/devices`) and `RealBackend` against the real kernel-generated sysfs/config;
  asserts measured `(errors, bits)`/verdict match the injected model and that a stuck bit is
  reported.
- Keep it a **separate / nightly** workflow if PR latency matters.
- **Honest limit:** real kernel binding + protocol + W1C — *not* real config transactions, link
  electricals, or timing. The bench owns those.

### Phase 3 — Other real-kernel-path interfaces *(inside the Phase-2 guest)*
Add only the interfaces from the [fidelity tiers](#fidelity-tiers--spend-effort-only-where-real-buys-coverage)
worth making real, all in `sim/vdev_setup.sh` run inside the guest:
- **NVMe:** `nvmet`/nvme-loop for a real `/dev/nvmeX` round-trip (real `nvme-cli`, real JSON —
  catches key/format drift); QEMU-NVMe with `smart_critical_warning` + QMP for fault *values*;
  kernel `nvme` fault-injection for command-failure paths.
- **Ethernet:** `netdevsim` for real `ethtool -S`/FEC/pause parsing.
- **CAN:** `vcan`/`vxcan` + `cangen`/`canplayer` + `canerrsim` for real frame I/O and
  error-frame decode.
- **EDAC (optional):** EINJ in QEMU for a real `/sys/.../edac` event.
- Gated to run only where privileges/modules exist (the guest); skipped elsewhere.

### Continuous — corpus refresh *(the honesty engine)*
`.github/workflows/corpus-refresh.yml` (scheduled) re-captures tool output from a container
with the latest `nvme-cli`/`ethtool` (and, manually, from a real Zoox test station) and opens a
PR on diff. The Phase-1 corpus tests then run against the new capture — so the *next* nvme-cli
schema shift surfaces as a reviewed red diff, on the tool's release rhythm, not ours.

## File layout (added / changed)

```
toolkit/
├── c/
│   ├── pcie_bert.c                 # Phase 0 — read_reg/write_reg seam; Phase 1 — -r/sys_root
│   └── test/
│       ├── unity.{c,h}             # Phase 0 — vendored single-file C test framework
│       ├── fake_cfgspace.c         # Phase 0 — in-memory register model (true W1C + injection)
│       └── test_pcie_bert.c        # Phase 0 — W1C loop, cap walks, UNC_EVERY, popcount
├── src/computetest/backend.py      # Phase 1 — RealBackend(sys_root=…), injectable root
├── tests/
│   ├── sysfs_fixture.py            # Phase 1 — tmp_path fake /sys builder (real symlinks)
│   ├── test_parsers_property.py    # Phase 0 — Hypothesis on the pure parsers
│   ├── test_backend_contract.py    # Phase 1 — same suite over [Mock, Real(fixture)]
│   ├── test_parsers_corpus.py      # Phase 1 — replay real captures through the parsers
│   └── test_realpath_qemu.py       # Phase 2/3 — gated; runs in/against the QEMU guest
├── corpus/<tool>/<version>/<model>/…   # Phase 1 — versioned REAL tool outputs (w/ provenance)
├── sim/
│   ├── fakebin/{nvme,nvidia-smi,ethtool,ip}   # Phase 1 — plumbing smoke shims (demoted)
│   ├── qemu/{run_guest.sh,inject.py,guest_tests.sh}   # Phase 2 — QEMU/TCG + QMP AER inject
│   └── vdev_setup.sh               # Phase 3 — nvmet / netdevsim / vcan / EINJ bring-up
├── Makefile                        # + ctest (C unit tests), sim (QEMU lane)
├── docs/realpath-simulation.md     # this doc
└── .github/workflows/
    ├── test.yml                    # Phase 0/1 — macOS + Linux, no privilege
    ├── qemu.yml                    # Phase 2/3 — QEMU/TCG guest (nightly/separate)
    └── corpus-refresh.yml          # scheduled re-capture → PR on diff
```

## What this is NOT (so the doc doesn't oversell)

It does not conjure PCIe/GPU silicon, real config transactions, real electricals/timing, real
bus errors, or real wear/thermal behavior. QEMU is faithful to the *protocol and W1C
semantics*; `vcan` to the *transport*; `nvmet` to the *command round-trip*; `netdevsim` to the
*driver stats interface* — none to the physics. GPU/GMSL have no emulation at all. The **bench
remains the final word** on anything electrical, timing-dependent, or truly-hardware. The value
here is that every line of real-path *software* gets executed against realistic inputs — most
of it on the laptop (Phases 0–1), the binding proof in the target OS (Phase 2) — on every
commit. That is exactly where our recent bugs were hiding.

## References

- QEMU device-model AER (sets real status bits + `w1cmask` W1C): `hw/pci/pcie_aer.c`;
  `pcie_aer_inject_error` monitor command. QEMU NVMe `smart_critical_warning`.
- Kernel: PCIe AER HOWTO and `drivers/pci/pcie/aer_inject.c` (why `aer-inject` is kernel-only);
  sysfs PCI (`config` is rw, **not mmappable**); NVMe fault injection; APEI EINJ; SocketCAN;
  netdevsim; ethtool-netlink.
- Patterns: node_exporter (`ttar` `/sys` fixtures + golden diff + `--update`); fwupd device
  emulation (record/replay); util-linux `tests/ts/lsblk/mk-input.sh` (seed a sysfs corpus);
  umockdev (Linux-only sysfs/ioctl record-replay, if we ever need true sysfs read semantics).
- Practice: Fowler *ContractTest*; Google TotT *Don't Mock Types You Don't Own*; Hypothesis
  (property-based testing); Unity / cmocka (C unit frameworks).
- CI limits: GitHub-hosted runners can't `modprobe` (no `CAP_SYS_MODULE`), no KVM/nested virt;
  the QEMU/TCG guest is the escape hatch. FUSE-in-Docker needs `--device /dev/fuse --cap-add
  SYS_ADMIN` (the bare runner VM does not).
```
