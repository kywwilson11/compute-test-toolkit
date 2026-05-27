# Shared configuration for the Phase 2 QEMU AER lane (docs/realpath-simulation.md).
# Sourced by prepare.sh / run_guest.sh / aer_test.sh — not meant to be run directly.
# Every value can be overridden from the environment, e.g. ARCH=x86_64 ./run_guest.sh start
#
# shellcheck shell=bash

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${COMPUTETEST_QEMU_WORK:-$HERE/.work}"

# --- guest arch -----------------------------------------------------------------------
# Default to the host arch: aarch64 on Apple Silicon (the locally-proven path, accelerated
# by HVF), x86_64 on Intel/CI (accelerated by KVM where available, else plain TCG).
ARCH="${ARCH:-$(uname -m)}"
case "$ARCH" in
  arm64 | aarch64) ARCH=aarch64 ;;
  x86_64 | amd64)  ARCH=x86_64 ;;
  *) echo "config.sh: unsupported ARCH '$ARCH' (use aarch64 or x86_64)" >&2; exit 2 ;;
esac

# --- control plane: host-forwarded SSH + the QMP socket inject.py drives --------------
SSH_PORT="${SSH_PORT:-2222}"
QMP_PORT="${QMP_PORT:-4444}"
QMP_ADDR="127.0.0.1:${QMP_PORT}"
SSH_KEY="$WORK/id_qemu"
SSH_USER="${SSH_USER:-ubuntu}"
GUEST_HOST="127.0.0.1"
# Throwaway-VM SSH: no host-key checking, quiet, key-only.
SSH_COMMON=(-i "$SSH_KEY" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
            -o LogLevel=ERROR -o ConnectTimeout=5)
SSH=(ssh -p "$SSH_PORT" "${SSH_COMMON[@]}" "$SSH_USER@$GUEST_HOST")
SCP=(scp -P "$SSH_PORT" "${SSH_COMMON[@]}")

# --- the injection target -------------------------------------------------------------
# We attach an emulated NVMe behind a pcie-root-port. The *root port* carries the AER
# capability (the nvme endpoint does not), so we inject into it and point the engine at it.
# slot/addr 3 on pcie.0 => guest BDF 0000:00:03.0, qdev id rp0.
ROOT_PORT_ID="${ROOT_PORT_ID:-rp0}"
ROOT_PORT_ADDR="${ROOT_PORT_ADDR:-3}"
TARGET_BDF="${TARGET_BDF:-0000:00:03.0}"

# --- disk images, seed, firmware ------------------------------------------------------
BOOT_IMG="$WORK/boot.qcow2"      # writable qcow2 overlay on the pristine cloud image
BASE_IMG="$WORK/base.img"        # downloaded Ubuntu cloud image (qcow2, kept read-only)
NVME_IMG="$WORK/nvme.qcow2"      # backing store for the emulated nvme endpoint
SEED_ISO="$WORK/seed.iso"        # cloud-init NoCloud datasource (CIDATA-labelled ISO)
VARS_FW="$WORK/vars.fd"          # writable UEFI varstore (per-guest copy of the template)
SERIAL_LOG="$WORK/serial.log"
PIDFILE="$WORK/qemu.pid"

MEM_MB="${MEM_MB:-2048}"
SMP="${SMP:-2}"

# Ubuntu 24.04 "noble" cloud image for the chosen arch.
case "$ARCH" in
  aarch64) CLOUD_IMG_URL="${CLOUD_IMG_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-arm64.img}" ;;
  x86_64)  CLOUD_IMG_URL="${CLOUD_IMG_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img}" ;;
esac

# Locate the QEMU binary and the UEFI firmware for this arch. UEFI is required: the cloud
# images are GPT/UEFI, and aarch64 `virt` has no built-in BIOS that boots them.
QEMU_BIN="${QEMU_BIN:-qemu-system-$ARCH}"

_find_fw() {  # _find_fw VAR_NAME candidate...
    local var="$1"; shift
    local f
    for f in "$@"; do
        [ -f "$f" ] && { printf -v "$var" '%s' "$f"; return 0; }
    done
    return 1
}

# CODE_FW = read-only UEFI code; VARS_TEMPLATE = the pristine varstore we copy per guest.
case "$ARCH" in
  aarch64)
    _find_fw CODE_FW \
        /opt/homebrew/share/qemu/edk2-aarch64-code.fd \
        /usr/local/share/qemu/edk2-aarch64-code.fd \
        /usr/share/AAVMF/AAVMF_CODE.fd \
        /usr/share/qemu/edk2-aarch64-code.fd \
      || { echo "config.sh: no aarch64 UEFI code firmware found (install qemu / AAVMF)" >&2; exit 3; }
    _find_fw VARS_TEMPLATE \
        /opt/homebrew/share/qemu/edk2-arm-vars.fd \
        /usr/local/share/qemu/edk2-arm-vars.fd \
        /usr/share/AAVMF/AAVMF_VARS.fd \
        /usr/share/qemu/edk2-arm-vars.fd \
      || { echo "config.sh: no aarch64 UEFI vars template found" >&2; exit 3; }
    ;;
  x86_64)
    _find_fw CODE_FW \
        /usr/share/OVMF/OVMF_CODE.fd \
        /usr/share/OVMF/OVMF_CODE_4M.fd \
        /opt/homebrew/share/qemu/edk2-x86_64-code.fd \
        /usr/local/share/qemu/edk2-x86_64-code.fd \
        /usr/share/qemu/edk2-x86_64-code.fd \
      || { echo "config.sh: no x86_64 UEFI code firmware found (apt-get install ovmf)" >&2; exit 3; }
    _find_fw VARS_TEMPLATE \
        /usr/share/OVMF/OVMF_VARS.fd \
        /usr/share/OVMF/OVMF_VARS_4M.fd \
        /opt/homebrew/share/qemu/edk2-i386-vars.fd \
        /usr/local/share/qemu/edk2-i386-vars.fd \
        /usr/share/qemu/edk2-i386-vars.fd \
      || { echo "config.sh: no x86_64 UEFI vars template found" >&2; exit 3; }
    ;;
esac

# --- acceleration + machine/cpu -------------------------------------------------------
# Pick the best available accelerator unless ACCEL is set: HVF on macOS, KVM on Linux
# with /dev/kvm, else TCG (pure software — what CI uses).
if [ -z "${ACCEL:-}" ]; then
    if [ "$(uname -s)" = "Darwin" ]; then
        ACCEL=hvf
    elif [ -w /dev/kvm ]; then
        ACCEL=kvm
    else
        ACCEL=tcg
    fi
fi

# With a hardware accelerator the guest must use the host CPU model; TCG uses 'max'.
if [ "$ACCEL" = "tcg" ]; then
    CPU="${CPU:-max}"
else
    CPU="${CPU:-host}"
fi

case "$ARCH" in
  aarch64) MACHINE="${MACHINE:-virt,gic-version=3}" ;;
  x86_64)  MACHINE="${MACHINE:-q35}" ;;
esac

# --- shared guest-lifecycle helpers ---------------------------------------------------
guest_pid()   { [ -f "$PIDFILE" ] && cat "$PIDFILE" 2>/dev/null || true; }
is_running()  { local p; p="$(guest_pid)"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }
