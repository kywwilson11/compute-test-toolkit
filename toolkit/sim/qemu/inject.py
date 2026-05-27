"""
QMP client + PCIe AER error injection for the Phase 2 QEMU lane (docs/realpath-simulation.md).

Drives a running qemu-system over its QMP socket to inject AER correctable/uncorrectable
status bits into an emulated PCIe device (an `nvme` behind a `pcie-root-port`), so the
*unmodified* pcie_bert C engine and RealBackend read and write-1-to-clear real, kernel-
generated config space. Injection uses QEMU's HMP `pcie_aer_inject_error` command, wrapped
via the QMP `human-monitor-command`. Ground-truth syntax (from `help pcie_aer_inject_error`
in qemu-system 11.0):

    pcie_aer_inject_error [-a] [-c] <id> <error_status> [<tlp header> [<tlp header prefix>]]
      -a = advisory non-fatal,  -c = correctable,  <id> = qdev id,
      <error_status> = a QEMU error string OR a 32-bit value.

We inject by **raw 32-bit status value** so each bit maps exactly onto the AER status
register the engine decodes (e.g. correctable Bad TLP = bit 6 = 0x40), keeping the
host-side injection and the guest-side count deterministically comparable.

This module is pure Python and has no QEMU dependency for import/test — the QMP framing and
command construction are exercised by tests/test_qmp_inject.py against a fake QMP server.
"""
from __future__ import annotations

import argparse
import json
import socket

# --- AER Correctable Error Status bits (match c/pcie_bert_core.h AER_COR_BITS) --------
COR_RX_ERR       = 1 << 0    # Receiver Error
COR_BAD_TLP      = 1 << 6    # Bad TLP
COR_BAD_DLLP     = 1 << 7    # Bad DLLP
COR_REPLAY_ROLL  = 1 << 8    # REPLAY_NUM Rollover
COR_REPLAY_TIMER = 1 << 12   # Replay Timer Timeout
COR_ADV_NONFATAL = 1 << 13   # Advisory Non-Fatal Error
COR_INTERNAL     = 1 << 14   # Corrected Internal Error
COR_HDR_LOG_OVF  = 1 << 15   # Header Log Overflow

# --- AER Uncorrectable Error Status bits ---------------------------------------------
UNC_DLP          = 1 << 4    # Data Link Protocol Error
UNC_POISONED_TLP = 1 << 12   # Poisoned TLP
UNC_COMPLETION_TO = 1 << 14  # Completion Timeout
UNC_MALFORMED_TLP = 1 << 18  # Malformed TLP


class QMPError(RuntimeError):
    pass


class QMPClient:
    """Minimal QMP client: connect, negotiate capabilities, execute commands, run HMP.

    ``addr`` is a unix-socket path, an ``"host:port"`` string, or a ``(host, port)`` tuple
    — matching ``-qmp unix:PATH,server,nowait`` or ``-qmp tcp:HOST:PORT,server,nowait``.
    """

    def __init__(self, addr):
        self._addr = addr
        self._sock: socket.socket | None = None
        self._buf = b""

    def __enter__(self) -> "QMPClient":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def connect(self, timeout: float = 10.0) -> None:
        self._sock = self._open(self._addr, timeout)
        self._sock.settimeout(timeout)
        greeting = self._recv_json()                  # QEMU sends {"QMP": {...}} on connect
        if "QMP" not in greeting:
            raise QMPError(f"expected a QMP greeting, got {greeting!r}")
        self.execute("qmp_capabilities")              # leave negotiation -> command mode

    @staticmethod
    def _open(addr, timeout: float) -> socket.socket:
        if isinstance(addr, tuple):
            return socket.create_connection(addr, timeout=timeout)
        if isinstance(addr, str) and ":" in addr and not addr.startswith("/"):
            host, port = addr.rsplit(":", 1)
            return socket.create_connection((host, int(port)), timeout=timeout)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)   # unix socket path
        s.settimeout(timeout)
        s.connect(addr)
        return s

    def _recv_json(self) -> dict:
        while b"\n" not in self._buf:                 # QMP is newline-delimited JSON
            chunk = self._sock.recv(4096)
            if not chunk:
                raise QMPError("QMP connection closed by peer")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line.decode())              # trailing \r is JSON whitespace

    def _send(self, obj: dict) -> None:
        self._sock.sendall((json.dumps(obj) + "\n").encode())

    def execute(self, command: str, **arguments) -> object:
        msg: dict = {"execute": command}
        if arguments:
            msg["arguments"] = arguments
        self._send(msg)
        while True:                                   # skip async events; wait for return/error
            reply = self._recv_json()
            if "return" in reply:
                return reply["return"]
            if "error" in reply:
                err = reply["error"]
                raise QMPError(err.get("desc") or str(err))

    def hmp(self, command_line: str) -> str:
        """Run a human-monitor (HMP) command via QMP; returns its text output."""
        return self.execute("human-monitor-command", **{"command-line": command_line})

    def inject_aer(self, qdev_id: str, status: int, *, correctable: bool,
                   advisory: bool = False) -> str:
        """Inject one AER error (raw 32-bit status) into the device with qdev id ``qdev_id``."""
        flags = ("-a " if advisory else "") + ("-c " if correctable else "")
        out = self.hmp(f"pcie_aer_inject_error {flags}{qdev_id} 0x{status:x}")
        # QEMU echoes "OK id: <id> ..." on success; any other non-empty text is an error report.
        if out.strip() and not out.strip().startswith("OK"):
            raise QMPError(f"pcie_aer_inject_error failed: {out.strip()}")
        return out

    def inject_correctable(self, qdev_id: str, status: int = COR_BAD_TLP) -> str:
        return self.inject_aer(qdev_id, status, correctable=True)

    def inject_uncorrectable(self, qdev_id: str, status: int = UNC_MALFORMED_TLP) -> str:
        return self.inject_aer(qdev_id, status, correctable=False)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Inject a PCIe AER error into a QEMU device via QMP.")
    ap.add_argument("--qmp", required=True, help="QMP address: host:port or a unix socket path")
    ap.add_argument("--id", required=True, dest="qdev_id", help="target device qdev id (-device ...,id=)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--correctable", metavar="STATUS", help="correctable AER status (hex/dec, e.g. 0x40)")
    g.add_argument("--uncorrectable", metavar="STATUS", help="uncorrectable AER status (hex/dec)")
    ap.add_argument("--advisory", action="store_true", help="mark correctable as advisory non-fatal")
    ap.add_argument("--count", type=int, default=1, help="inject this many times over one connection (default 1)")
    args = ap.parse_args(argv)

    if args.count < 1:
        ap.error("--count must be >= 1")
    status = int(args.correctable or args.uncorrectable, 0)   # base 0 -> accepts 0x..
    correctable = bool(args.correctable)
    with QMPClient(args.qmp) as q:
        for _ in range(args.count):
            q.inject_aer(args.qdev_id, status, correctable=correctable, advisory=args.advisory)
    kind = "correctable" if correctable else "uncorrectable"
    print(f"injected {args.count}x {kind} AER 0x{status:x} into {args.qdev_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
