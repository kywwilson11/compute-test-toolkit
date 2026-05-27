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

`.work/` (all generated assets, including the ~600 MB cloud image) is git‑ignored.

## Run it

```bash
cd toolkit/sim/qemu
./run_guest.sh start      # downloads the cloud image on first run, boots, waits until ready
./aer_test.sh 40          # build-in-guest + inject 40 + assert -> "PASS: ... 40 correctable"
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

## macOS (local) vs CI

* **macOS / Apple Silicon** runs `qemu-system-aarch64` on the `virt` machine accelerated by
  **HVF** — this is the locally verified path. (KVM is Linux‑only; macOS uses HVF.)
* **CI** has no `/dev/kvm`, so `config.sh` falls back to **TCG** (pure software emulation) and
  the workflow targets **x86_64 / q35** — see `.github/workflows/qemu.yml`. The scripts are
  arch‑parametrized (`ARCH=x86_64 ./run_guest.sh start`); the x86_64/TCG path is authored to
  mirror the aarch64 recipe but has not been run locally (no x86 host here).

## Gotchas (learned proving this)

* Reading the AER extended capability needs **root** in the guest (`aer_test.sh` uses `sudo`).
* QEMU's `pcie_aer_inject_error` echoes `OK id: …` on success; `inject.py` treats any other
  non‑empty output as an error.
* Inject into `rp0` (the root port), **not** the nvme endpoint — the endpoint has no AER cap.
