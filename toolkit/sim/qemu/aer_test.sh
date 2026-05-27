#!/usr/bin/env bash
# Phase 2 end-to-end binding test (docs/realpath-simulation.md).
#
# Proves the *unmodified* pcie_bert C engine reads, counts, and write-1-to-clears REAL,
# kernel-generated PCIe AER config space — the one thing a software mock structurally
# cannot prove. The flow is host-driven:
#
#   1. ship the engine sources into the running guest and build them there
#   2. start `pcie_bert -d <root-port> -t <window> --json` in the guest
#   3. inject N correctable AER errors from the host via QMP (inject.py)
#   4. assert the engine reported exactly N correctable errors
#
# Requires a guest already booted by `./run_guest.sh start`.
#
# Usage:   ./aer_test.sh [N]            # N injections (default 40)
# Env:     COUNT, AER_STATUS (default 0x40 = Bad TLP, bit 6), WINDOW (engine seconds)
source "$(dirname "${BASH_SOURCE[0]}")/config.sh"

COUNT="${1:-${COUNT:-40}}"
AER_STATUS="${AER_STATUS:-0x40}"        # correctable Bad TLP (bit 6) — matches AER_COR_BITS
WINDOW="${WINDOW:-8}"                    # engine sampling window; must outlast injection
ENGINE_DIR="/tmp/engine"
C_SRC="$(cd "$HERE/../../c" && pwd)"
OUT_JSON="$WORK/aer_out.json"

is_running || { echo "no guest running — './run_guest.sh start' first" >&2; exit 1; }

echo "==> shipping engine sources to guest:$ENGINE_DIR"
"${SSH[@]}" "mkdir -p $ENGINE_DIR"
"${SCP[@]}" "$C_SRC/pcie_bert.c" "$C_SRC/pcie_bert_core.c" "$C_SRC/pcie_bert_core.h" \
            "$C_SRC/Makefile" "$SSH_USER@$GUEST_HOST:$ENGINE_DIR/"

echo "==> building engine in guest"
"${SSH[@]}" "cd $ENGINE_DIR && make pcie_bert"

echo "==> running engine ($WINDOW s) while injecting $COUNT x AER $AER_STATUS into $TARGET_BDF"
# Engine needs root to read the AER extended-capability config space.
"${SSH[@]}" "cd $ENGINE_DIR && sudo ./pcie_bert -d $TARGET_BDF -t $WINDOW --json" \
    > "$OUT_JSON" 2> "$WORK/aer_err.log" &
engine=$!

sleep 2                                  # let it start and clear the W1C baseline
python3 "$HERE/inject.py" --qmp "$QMP_ADDR" --id "$ROOT_PORT_ID" \
        --correctable "$AER_STATUS" --count "$COUNT"
wait "$engine"

echo "==> engine output:"
cat "$OUT_JSON"
echo

python3 - "$OUT_JSON" "$COUNT" <<'PY'
import json, sys
out = json.load(open(sys.argv[1]))
want = int(sys.argv[2])
got = out.get("correctable")
if got == want:
    print(f"PASS: engine counted {got} correctable errors (expected {want})")
    sys.exit(0)
print(f"FAIL: engine counted {got} correctable errors, expected {want}", file=sys.stderr)
sys.exit(1)
PY
