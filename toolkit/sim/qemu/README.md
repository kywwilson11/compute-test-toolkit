# Phase 2 — QEMU + QMP AER injection lane

This lane proves the part of the toolkit a software mock structurally **cannot**: that the
unmodified `pcie_bert` C engine reads, counts, and write‑1‑to‑clears *real, kernel‑generated*
PCIe AER (Advanced Error Reporting) config space. We boot a Linux guest under QEMU, inject
AER errors into an emulated device from the host over QEMU's QMP socket, and assert the
engine — running inside the guest against the real kernel — reports exactly what we injected.

See `../../docs/realpath-simulation.md` for where this sits in the layered testing plan
(Phase 0 C unit tests + Hypothesis, Phase 1 verified fakes/contract/corpus, **Phase 2 this**).

## Why a root port, not the nvme endpoint

We attach an emulated `nvme` device **behind a `pcie-root-port`**. The root port carries the
AER capability; QEMU's `nvme` endpoint does not. So we inject into the root port and point the
engine at it:

```
pcie.0
  └─ pcie-root-port   id=rp0   guest BDF 0000:00:03.0   ← AER lives here; inject + measure here
       └─ nvme         (zooxnvme0)        0000:01:00.0
```

## Files

| File | Role |
|------|------|
| `config.sh`    | Shared, arch‑parametrized config (paths, ports, firmware, topology). Sourced, not run. |
| `prepare.sh`   | One‑time idempotent asset prep under `.work/` (cloud image, SSH key, seed ISO, disks, varstore). |
| `run_guest.sh` | `start` / `stop` / `status` / `ssh` the guest with the topology above. |
| `inject.py`    | QMP client + AER injection (`pcie_aer_inject_error`). Pure Python; unit‑tested off‑hardware. |
| `aer_test.sh`  | Host‑driven end‑to‑end: build the engine in the guest, inject N from the host, assert the count. |
| `vdev_setup.sh`| **Phase 3** — in‑guest bring‑up of real kernel virtual devices (nvme‑loop, vcan). Idempotent. |
| `vdev_test.sh` | **Phase 3** — host‑driven: stand up the vdevs, run the toolkit's real backend, assert the parse. |

`.work/` (all generated assets, including the ~600 MB cloud image) is git‑ignored.

## Run it

```bash
cd toolkit/sim/qemu
./run_guest.sh start      # downloads the cloud image on first run, boots, waits until ready
./aer_test.sh 40          # Phase 2: build-in-guest + inject 40 + assert -> "PASS: ... 40 correctable"
./vdev_test.sh            # Phase 3: nvme-loop/vcan + toolkit real backend -> "PASS: all ... bound"
./run_guest.sh stop
```

Inject manually against a running guest:

```bash
python3 inject.py --qmp 127.0.0.1:4444 --id rp0 --correctable 0x40 --count 10
```

## Proven result (2026‑05‑27, Apple Silicon / HVF)

Injected 40 correctable **Bad TLP** (status bit 6 = `0x40`) into `rp0` while
`pcie_bert -d 0000:00:03.0 -t 8 --json` ran in an Ubuntu 24.04 aarch64 guest:

```json
{"bdf":"0000:00:03.0", ... ,"correctable":40,"per_correctable":{"BadTLP":40}}
```

Exactly 40, decoded onto the right bit — the engine's real read/decode/clear path, end to end.

## Phase 3 — other real‑kernel‑path interfaces

`vdev_test.sh` extends the binding proof to the non‑PCIe modules by standing up real kernel
virtual devices in the guest and asserting the *unmodified* `computetest` real backend parses
real kernel output. Proven (2026‑05‑27, aarch64 / HVF), all 8 checks green:

| Interface | Real device | What the real path proves |
|-----------|-------------|---------------------------|
| **NVMe** | `/dev/nvme0` (emulated QEMU nvme) | real `nvme smart-log -o json` → Kelvin→°C (`323`→`50`) and the abbreviated‑SMART‑key normalization (`avail_spare`→`available_spare`) — the exact drift bug class Phase 0/1 pin |
| **NVMe** | `/dev/nvme1` (nvmet **nvme‑loop**) | a real NVMe‑over‑fabrics round‑trip (`nvme connect -t loop`); `id-ctrl` model `Linux` |
| **Ethernet** | the guest's real virtio NIC | `link_up` + `rx/tx_errors` parsed from real `ethtool`/`ethtool -S` |
| **CAN** | `vcan0` | state parsed from real `ip -details -statistics link show` |

**Why the real NIC and not `netdevsim`:** the design doc suggested `netdevsim`, but on the 6.8
cloud kernel it exposes neither a `Speed:` line nor `ethtool -S` stats, so it's a poor
demonstrator. The guest's real virtio NIC is a genuine kernel netdev with real `ethtool`
output, so the Ethernet binding is proven there instead.

**Honest limits:** `vcan` has no CAN error states (no bus‑off / berr‑counter), and virtio
reports no link speed — so `speed_ok` and the CAN error‑state branches aren't exercised here;
those are covered by the Phase 0/1 parser tests and ultimately the bench. Phase 3 proves the
*binding* (real subprocess → real kernel output → real parse → verdict).

## macOS (local) vs CI

* **macOS / Apple Silicon** runs `qemu-system-aarch64` on the `virt` machine accelerated by
  **HVF** — this is the locally verified path. (KVM is Linux‑only; macOS uses HVF.)
* **CI** has no `/dev/kvm`, so `config.sh` falls back to **TCG** (pure software emulation) and
  the workflow targets **x86_64 / q35** — see `.github/workflows/qemu.yml`. The scripts are
  arch‑parametrized (`ARCH=x86_64 ./run_guest.sh start`); the x86_64/TCG path is authored to
  mirror the aarch64 recipe.

### Known: Phase 2/3 e2e is non‑gating on the x86_64/TCG CI lane

The CI workflow's e2e steps run under `continue-on-error: true`. Both succeed on
aarch64/HVF locally (40/40 AER, 8/8 Phase 3 binding checks) but on the GitHub Actions
x86_64/TCG runner the engine sees **0/40** correctable errors despite the QMP injection
echoing success — the kernel's `pcieport` AER IRQ handler appears to clear the W1C
status bits faster than our engine polls. Unbinding `pcieport` from the root port (the
obvious fix) did not resolve it, so the real fix likely needs `pci=noaer` injected via
the guest kernel cmdline (cloud‑init / grub modification + reboot). That's best
attempted from a real x86 dev box where iteration is sub‑minute rather than 8 minutes
per CI round trip; until then the unit gate + the aarch64/HVF local proof are the
binding signal, and the CI e2e is best‑effort.

## Gotchas (learned proving this)

* Reading the AER extended capability needs **root** in the guest (`aer_test.sh` uses `sudo`).
* QEMU's `pcie_aer_inject_error` echoes `OK id: …` on success; `inject.py` treats any other
  non‑empty output as an error.
* Inject into `rp0` (the root port), **not** the nvme endpoint — the endpoint has no AER cap.
* `vcan` and `nvme_loop` live in `linux-modules-extra-$(uname -r)`; `vdev_setup.sh` installs it.
* The toolkit CLI exits non‑zero on a **FAIL verdict** (e.g. QEMU's `avail_spare=0`); that's a
  real verdict, so `vdev_test.sh` asserts on the parsed JSON and ignores the exit code.
