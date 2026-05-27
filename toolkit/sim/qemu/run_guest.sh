#!/usr/bin/env bash
# Boot (or control) the Phase 2 guest (docs/realpath-simulation.md).
#
# Topology that makes the lane work: an emulated nvme endpoint sits behind a
# pcie-root-port, and the *root port* (qdev id rp0, guest BDF 0000:00:03.0) carries the
# AER capability we inject into. The QMP control socket and a host-forwarded SSH port are
# the two seams the host side drives.
#
# Usage:
#   ./run_guest.sh start     boot, wait until SSH + cloud-init are ready  (default)
#   ./run_guest.sh stop      shut the guest down (kills the pidfile process)
#   ./run_guest.sh status    report whether the guest is up / reachable
#   ./run_guest.sh ssh [..]  ssh into the guest (extra args are passed to the remote shell)
#
# Override arch/accel/ports via the environment, e.g. ARCH=x86_64 ACCEL=tcg ./run_guest.sh start
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"   # provides guest_pid / is_running

start() {
    if is_running; then
        echo "guest already running (pid $(guest_pid)); 'stop' it first" >&2
        exit 1
    fi
    # Auto-prepare assets on first run (downloads the cloud image once).
    [ -f "$BOOT_IMG" ] || "$HERE/prepare.sh"

    echo "==> booting $QEMU_BIN ($ARCH, accel=$ACCEL, cpu=$CPU)"
    : > "$SERIAL_LOG"
    "$QEMU_BIN" \
        -name zoox-phase2 \
        -machine "$MACHINE" \
        -accel "$ACCEL" \
        -cpu "$CPU" \
        -smp "$SMP" \
        -m "$MEM_MB" \
        -drive "if=pflash,format=raw,readonly=on,file=$CODE_FW" \
        -drive "if=pflash,format=raw,file=$VARS_FW" \
        -drive "if=virtio,format=qcow2,file=$BOOT_IMG" \
        -drive "if=none,id=nvmedrv,format=qcow2,file=$NVME_IMG" \
        -device "pcie-root-port,id=$ROOT_PORT_ID,bus=pcie.0,addr=$ROOT_PORT_ADDR" \
        -device "nvme,bus=$ROOT_PORT_ID,drive=nvmedrv,serial=zooxnvme0" \
        -cdrom "$SEED_ISO" \
        -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$SSH_PORT-:22" \
        -device "virtio-net-pci,netdev=net0" \
        -qmp "tcp:$QMP_ADDR,server=on,wait=off" \
        -serial "file:$SERIAL_LOG" \
        -display none \
        -daemonize -pidfile "$PIDFILE"

    echo -n "==> waiting for SSH on $GUEST_HOST:$SSH_PORT "
    # ~3 min of 2 s polls under HVF; CI sets BOOT_TRIES higher because TCG boots slower.
    for _ in $(seq 1 "${BOOT_TRIES:-90}"); do
        if "${SSH[@]}" true 2>/dev/null; then
            echo " up"
            break
        fi
        is_running || { echo; echo "qemu exited during boot; see $SERIAL_LOG" >&2; exit 1; }
        echo -n .; sleep 2
    done
    if ! "${SSH[@]}" true 2>/dev/null; then
        echo; echo "timed out waiting for SSH; see $SERIAL_LOG" >&2; exit 1
    fi

    # Block until cloud-init has finished so the toolchain (gcc, pciutils) is present.
    echo "==> waiting for cloud-init to finish (installing gcc, pciutils, nvme-cli)"
    "${SSH[@]}" "sudo cloud-init status --wait" || true
    echo "==> guest ready (pid $(guest_pid)); QMP at $QMP_ADDR, target $TARGET_BDF (id $ROOT_PORT_ID)"
}

stop() {
    local p; p="$(guest_pid)"
    if [ -z "$p" ] || ! kill -0 "$p" 2>/dev/null; then
        echo "guest not running"; rm -f "$PIDFILE"; return 0
    fi
    echo "==> stopping guest (pid $p)"
    kill "$p" 2>/dev/null || true
    for _ in $(seq 1 20); do kill -0 "$p" 2>/dev/null || break; sleep 0.5; done
    kill -9 "$p" 2>/dev/null || true
    rm -f "$PIDFILE"
}

status() {
    if is_running; then
        echo "guest UP (pid $(guest_pid))"
        "${SSH[@]}" true 2>/dev/null && echo "ssh: reachable" || echo "ssh: not reachable yet"
    else
        echo "guest DOWN"
    fi
}

case "${1:-start}" in
    start)  start ;;
    stop)   stop ;;
    status) status ;;
    ssh)    shift; exec "${SSH[@]}" "$@" ;;
    *) echo "usage: $0 {start|stop|status|ssh}" >&2; exit 2 ;;
esac
