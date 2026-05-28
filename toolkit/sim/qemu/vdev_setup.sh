#!/usr/bin/env bash
# Phase 3 — bring up real kernel virtual devices INSIDE the guest
# (docs/realpath-simulation.md). Run this *in the guest* (it needs root); the host driver
# is vdev_test.sh. Idempotent: safe to re-run. Sets up real kernel objects so the
# unmodified `computetest` real backend reads real kernel-generated output:
#
#   nvme-loop : nvmet file-backed namespace over the `loop` fabric -> a real /dev/nvmeX
#               (real nvme-cli JSON; exercises the SMART key-normalization real path)
#   vcan0     : a virtual CAN interface (real `ip -details -statistics link` parse)
#
# Ethernet is proven against the guest's existing real NIC (see vdev_test.sh): on the 6.8
# cloud kernel `netdevsim` exposes no Speed line and no `ethtool -S` stats, so it is a poor
# demonstrator; the real virtio NIC is a genuine kernel netdev with real ethtool output.
#
# Prints `KEY=VALUE` lines for the devices it brought up, so the caller can parse them.
set -euo pipefail

NQN="${NQN:-computetest-loop}"
NVME_BACK="${NVME_BACK:-/tmp/computetest-nvme-back.img}"
NVME_BACK_SIZE="${NVME_BACK_SIZE:-256M}"
VCAN="${VCAN:-vcan0}"
CFG=/sys/kernel/config/nvmet

log() { echo "[vdev] $*" >&2; }

# --- 0. modules: vcan and nvme_loop live in linux-modules-extra ----------------------
if ! sudo modprobe -n vcan >/dev/null 2>&1 || ! sudo modprobe -n nvme_loop >/dev/null 2>&1; then
    log "installing linux-modules-extra-$(uname -r) (vcan / nvme_loop)"
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "linux-modules-extra-$(uname -r)" >/dev/null
fi

# --- 1. nvme-loop: a real /dev/nvmeX via nvmet over the loop fabric -------------------
sudo modprobe nvmet nvme_loop nvme_fabrics
mountpoint -q /sys/kernel/config || sudo mount -t configfs none /sys/kernel/config

if ! sudo nvme list-subsys 2>/dev/null | grep -q "$NQN"; then
    log "creating nvmet subsystem '$NQN' (file-backed namespace)"
    sudo truncate -s "$NVME_BACK_SIZE" "$NVME_BACK"
    sudo mkdir -p "$CFG/subsystems/$NQN"
    echo 1 | sudo tee "$CFG/subsystems/$NQN/attr_allow_any_host" >/dev/null
    sudo mkdir -p "$CFG/subsystems/$NQN/namespaces/1"
    echo -n "$NVME_BACK" | sudo tee "$CFG/subsystems/$NQN/namespaces/1/device_path" >/dev/null
    echo 1 | sudo tee "$CFG/subsystems/$NQN/namespaces/1/enable" >/dev/null
    sudo mkdir -p "$CFG/ports/1"
    # addr_trtype is effectively write-once: only set it while the port is unconfigured.
    [ "$(sudo cat "$CFG/ports/1/addr_trtype" 2>/dev/null)" = loop ] || \
        echo loop | sudo tee "$CFG/ports/1/addr_trtype" >/dev/null
    sudo ln -sf "$CFG/subsystems/$NQN" "$CFG/ports/1/subsystems/$NQN"
    sudo nvme connect -t loop -n "$NQN" >&2
else
    log "nvmet subsystem '$NQN' already connected"
fi

# Resolve the loop controller's namespace block device (model 'Linux' = the nvmet target).
NVME_LOOP_DEV=""
for ctrl in /sys/class/nvme/nvme*; do
    [ -r "$ctrl/model" ] || continue
    if grep -q "Linux" "$ctrl/model"; then
        NVME_LOOP_DEV="/dev/$(basename "$ctrl")"
        break
    fi
done

# --- 2. vcan: a virtual CAN interface ------------------------------------------------
sudo modprobe vcan
if ! ip link show "$VCAN" >/dev/null 2>&1; then
    log "creating $VCAN"
    sudo ip link add dev "$VCAN" type vcan
fi
sudo ip link set up "$VCAN"

# --- summary (parseable by the caller) -----------------------------------------------
echo "NVME_QEMU_DEV=/dev/nvme0"
echo "NVME_LOOP_DEV=${NVME_LOOP_DEV}"
echo "VCAN=${VCAN}"
log "ready"
