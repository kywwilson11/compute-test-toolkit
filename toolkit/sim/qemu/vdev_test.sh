#!/usr/bin/env bash
# Phase 3 host-driven binding test (docs/realpath-simulation.md).
#
# Proves the unmodified `computetest` real backend reads REAL kernel-generated output for
# the non-PCIe interfaces, by standing up real kernel virtual devices in the guest
# (vdev_setup.sh) and asserting on the toolkit's parsed result:
#
#   NVMe  /dev/nvme0 (QEMU)  : Kelvin->C (323->50) and abbreviated SMART keys normalized
#                              (avail_spare -> available_spare) on real `nvme smart-log` JSON
#   NVMe  /dev/nvme1 (loop)  : real NVMe-over-fabrics round-trip (nvmet target, model "Linux")
#   Eth   <primary NIC>      : link_up + rx/tx_errors parsed from real `ethtool`/`-S`
#   CAN   vcan0              : state parsed from real `ip -details -statistics link`
#
# Requires a guest already booted by `./run_guest.sh start`.  Usage:  ./vdev_test.sh
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

SRC_DIR="$(cd "$HERE/../../src" && pwd)"
REMOTE=/tmp/ct

is_running || { echo "no guest running — './run_guest.sh start' first" >&2; exit 1; }

echo "==> shipping computetest + vdev_setup.sh to guest:$REMOTE"
"${SSH[@]}" "rm -rf $REMOTE && mkdir -p $REMOTE"
"${SCP[@]}" -r "$SRC_DIR/computetest" "$SSH_USER@$GUEST_HOST:$REMOTE/computetest"
"${SCP[@]}" "$HERE/vdev_setup.sh" "$SSH_USER@$GUEST_HOST:$REMOTE/vdev_setup.sh"

echo "==> bringing up virtual devices in guest"
# vdev_setup.sh prints KEY=VALUE on stdout (logs go to stderr); import them here.
SETUP="$("${SSH[@]}" "bash $REMOTE/vdev_setup.sh")"
echo "$SETUP" | sed 's/^/    /'
eval "$SETUP"               # -> NVME_QEMU_DEV, NVME_LOOP_DEV, VCAN

# The Ethernet binding is proven against the guest's real NIC (default-route device).
NIC="$("${SSH[@]}" "ip -o -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* dev \\([^ ]*\\).*/\\1/p' | head -1")"
[ -n "$NIC" ] || NIC="$("${SSH[@]}" "ip -o link show up | awk -F': ' '\$2!~/^(lo|vcan|eni)/{print \$2; exit}'")"
echo "    PRIMARY_NIC=$NIC"

# Run each check with the REAL backend (root: nvme/ethtool config reads need it) and save JSON.
run_check() {  # run_check <out-file> <cli args...>
    local out="$1"; shift
    # The CLI exits non-zero on a FAIL verdict (e.g. QEMU's avail_spare=0) — that's a real
    # verdict, not a harness error. We assert on the parsed JSON, so ignore the exit code.
    "${SSH[@]}" "sudo env PYTHONPATH=$REMOTE python3 -m computetest.cli $* --backend real --json" \
        2>/dev/null > "$out" || true
}
echo "==> running toolkit real-backend checks"
run_check "$WORK/p3_nvme_qemu.json" nvme "$NVME_QEMU_DEV"
run_check "$WORK/p3_nvme_loop.json" nvme "$NVME_LOOP_DEV"
run_check "$WORK/p3_eth.json"       eth  "$NIC"
run_check "$WORK/p3_can.json"       can  "$VCAN"

python3 - "$WORK/p3_nvme_qemu.json" "$WORK/p3_nvme_loop.json" "$WORK/p3_eth.json" "$WORK/p3_can.json" <<'PY'
import json, sys
qemu, loop, eth, can = (json.load(open(p)) for p in sys.argv[1:5])
fails = []
def check(name, cond, got):
    print(f"  {'PASS' if cond else 'FAIL'}: {name} ({got})")
    if not cond: fails.append(name)

# NVMe (QEMU): the real key-normalization + Kelvin-conversion path
check("nvme/qemu model read", qemu["model"] == "QEMU NVMe Ctrl", qemu["model"])
check("nvme/qemu Kelvin->C (323->50)", qemu["smart"].get("temperature") == 50,
      f"temperature={qemu['smart'].get('temperature')}")
check("nvme/qemu SMART keys normalized", "available_spare" in qemu["smart"],
      "available_spare present" if "available_spare" in qemu["smart"] else "MISSING")
# NVMe (loop): the real fabrics round-trip
check("nvme/loop fabrics round-trip", loop["model"] == "Linux", loop["model"])
# Ethernet: real ethtool parse
check("eth link_up parsed", eth["checks"]["link_up"] is True, f"link_up={eth['checks']['link_up']}")
check("eth errors parsed", eth["checks"]["low_errors"] is True,
      f"rx={eth['rx_errors']} tx={eth['tx_errors']}")
# CAN: real ip parse
check("can state parsed", can["state"] == "ERROR-ACTIVE", can["state"])
check("can ok", can["ok"] is True, f"ok={can['ok']}")

if fails:
    print(f"\nFAIL: {len(fails)} check(s) failed: {', '.join(fails)}", file=sys.stderr)
    sys.exit(1)
print("\nPASS: all Phase 3 real-kernel-path checks bound correctly")
PY
