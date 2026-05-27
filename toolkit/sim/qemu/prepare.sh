#!/usr/bin/env bash
# Prepare the Phase 2 guest assets under .work/ (docs/realpath-simulation.md).
#
# Idempotent: each asset is created only if missing, so re-running is cheap and a single
# `rm -rf .work` resets everything. Produces:
#   base.img    pristine Ubuntu 24.04 cloud image (downloaded once)
#   id_qemu*    throwaway SSH keypair injected via cloud-init
#   seed.iso    cloud-init NoCloud datasource (CIDATA) carrying user-data/meta-data
#   boot.qcow2  writable overlay on base.img (the guest's root disk)
#   nvme.qcow2  backing store for the emulated nvme endpoint
#   vars.fd     per-guest writable UEFI varstore
#
# Usage:  ./prepare.sh           # host arch (aarch64 on Apple Silicon)
#         ARCH=x86_64 ./prepare.sh
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

mkdir -p "$WORK"

# --- 1. cloud image -------------------------------------------------------------------
if [ ! -f "$BASE_IMG" ]; then
    echo "==> downloading $CLOUD_IMG_URL"
    curl -fL --retry 3 -o "$BASE_IMG.part" "$CLOUD_IMG_URL"
    mv "$BASE_IMG.part" "$BASE_IMG"
fi

# --- 2. SSH keypair -------------------------------------------------------------------
if [ ! -f "$SSH_KEY" ]; then
    echo "==> generating throwaway SSH keypair"
    ssh-keygen -t ed25519 -N '' -f "$SSH_KEY" -C computetest-qemu >/dev/null
fi
PUBKEY="$(cat "$SSH_KEY.pub")"

# --- 3. cloud-init seed ISO -----------------------------------------------------------
if [ ! -f "$SEED_ISO" ]; then
    echo "==> building cloud-init seed.iso (CIDATA)"
    SEED_DIR="$WORK/seed"
    mkdir -p "$SEED_DIR"
    cat > "$SEED_DIR/meta-data" <<EOF
instance-id: zoox-phase2-01
local-hostname: phase2
EOF
    cat > "$SEED_DIR/user-data" <<EOF
#cloud-config
users:
  - name: $SSH_USER
    groups: [sudo]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - $PUBKEY
package_update: true
packages: [gcc, libc6-dev, pciutils, nvme-cli]
EOF
    # Build the ISO with whatever's available: cloud-localds / xorriso / genisoimage on
    # Linux, hdiutil on macOS. The volume label MUST be CIDATA for the NoCloud datasource.
    if command -v cloud-localds >/dev/null 2>&1; then
        cloud-localds "$SEED_ISO" "$SEED_DIR/user-data" "$SEED_DIR/meta-data"
    elif command -v xorriso >/dev/null 2>&1; then
        xorriso -as mkisofs -output "$SEED_ISO" -volid CIDATA -joliet -rock "$SEED_DIR"
    elif command -v genisoimage >/dev/null 2>&1; then
        genisoimage -output "$SEED_ISO" -volid CIDATA -joliet -rock "$SEED_DIR"
    elif command -v hdiutil >/dev/null 2>&1; then
        hdiutil makehybrid -iso -joliet -default-volume-name CIDATA -o "$SEED_ISO" "$SEED_DIR" >/dev/null
    else
        echo "prepare.sh: need one of cloud-localds, xorriso, genisoimage, or hdiutil" >&2
        exit 4
    fi
fi

# --- 4. disks -------------------------------------------------------------------------
if [ ! -f "$BOOT_IMG" ]; then
    echo "==> creating boot overlay (10G) on base image"
    qemu-img create -f qcow2 -F qcow2 -b "$BASE_IMG" "$BOOT_IMG" 10G >/dev/null
fi
if [ ! -f "$NVME_IMG" ]; then
    echo "==> creating nvme backing disk (1G)"
    qemu-img create -f qcow2 "$NVME_IMG" 1G >/dev/null
fi

# --- 5. UEFI varstore -----------------------------------------------------------------
if [ ! -f "$VARS_FW" ]; then
    echo "==> copying UEFI varstore from $VARS_TEMPLATE"
    cp "$VARS_TEMPLATE" "$VARS_FW"
fi

echo "==> assets ready in $WORK (arch=$ARCH)"
