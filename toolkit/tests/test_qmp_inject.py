"""
Unit tests for the QMP injection client (sim/qemu/inject.py) — Phase 2.

Exercises the QMP handshake and the `pcie_aer_inject_error` command construction against a
fake in-process QMP server, so it runs on the laptop with no QEMU. The real end-to-end
injection (against a booted guest) runs in the QEMU lane on Linux/CI.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import socket
import threading

import pytest

# Load sim/qemu/inject.py directly (it's a standalone script, not an installed package).
_INJECT_PATH = pathlib.Path(__file__).resolve().parent.parent / "sim" / "qemu" / "inject.py"
_spec = importlib.util.spec_from_file_location("inject", _INJECT_PATH)
inject = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inject)


class FakeQMPServer:
    """Minimal QMP server: sends the greeting, answers qmp_capabilities, records every command,
    returns "" for human-monitor-command (QEMU's success = no output), and errors on "boom"."""

    GREETING = {"QMP": {"version": {"qemu": {"major": 11, "minor": 0, "micro": 0},
                                    "package": "fake"}, "capabilities": []}}

    def __init__(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.addr = f"127.0.0.1:{self._srv.getsockname()[1]}"
        self.commands: list[dict] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        conn, _ = self._srv.accept()
        with conn:
            conn.sendall((json.dumps(self.GREETING) + "\r\n").encode())
            buf = b""
            while True:
                while b"\n" not in buf:
                    chunk = conn.recv(4096)
                    if not chunk:
                        return
                    buf += chunk
                line, buf = buf.split(b"\n", 1)
                msg = json.loads(line.decode())
                self.commands.append(msg)
                if msg.get("execute") == "boom":
                    conn.sendall(b'{"error": {"class": "GenericError", "desc": "boom"}}\r\n')
                elif msg.get("execute") == "human-monitor-command":
                    cl = msg["arguments"]["command-line"]
                    if "nonexistent" in cl:        # mimic a QEMU error report (no leading "OK")
                        conn.sendall(b'{"return": "invalid id: nonexistent"}\r\n')
                    else:                          # QEMU success echoes "OK id: <id> ..."
                        conn.sendall(b'{"return": "OK id: dev root bus: 0000:00, '
                                     b'bus: 0 devfn: 3.0"}\r\n')
                else:
                    conn.sendall(b'{"return": {}}\r\n')

    def hmp_lines(self) -> list[str]:
        return [c["arguments"]["command-line"] for c in self.commands
                if c.get("execute") == "human-monitor-command"]

    def close(self):
        self._srv.close()


@pytest.fixture
def server():
    s = FakeQMPServer()
    yield s
    s.close()


def test_handshake_negotiates_capabilities(server):
    with inject.QMPClient(server.addr):
        pass
    assert server.commands[0] == {"execute": "qmp_capabilities"}


def test_inject_correctable_builds_hmp_command(server):
    with inject.QMPClient(server.addr) as q:
        q.inject_correctable("nvme0", inject.COR_BAD_TLP)
    assert server.hmp_lines() == ["pcie_aer_inject_error -c nvme0 0x40"]      # bit 6 = 0x40


def test_inject_uncorrectable_builds_hmp_command(server):
    with inject.QMPClient(server.addr) as q:
        q.inject_uncorrectable("nvme0", inject.UNC_MALFORMED_TLP)
    assert server.hmp_lines() == ["pcie_aer_inject_error nvme0 0x40000"]      # bit 18 = 0x40000


def test_inject_advisory_flag(server):
    with inject.QMPClient(server.addr) as q:
        q.inject_aer("nvme0", inject.COR_BAD_DLLP, correctable=True, advisory=True)
    assert server.hmp_lines() == ["pcie_aer_inject_error -a -c nvme0 0x80"]   # bit 7 = 0x80


def test_correctable_bit_values_match_engine():
    # must equal the AER_COR_BITS positions in c/pcie_bert_core.h
    assert (inject.COR_RX_ERR, inject.COR_BAD_TLP, inject.COR_BAD_DLLP,
            inject.COR_HDR_LOG_OVF) == (1 << 0, 1 << 6, 1 << 7, 1 << 15)


def test_execute_raises_on_qmp_error(server):
    with inject.QMPClient(server.addr) as q:
        with pytest.raises(inject.QMPError, match="boom"):
            q.execute("boom")


def test_inject_accepts_ok_output_and_raises_on_error(server):
    with inject.QMPClient(server.addr) as q:
        # fake replies "OK id: ..." -> no raise
        q.inject_correctable("nvme0", inject.COR_BAD_TLP)
        with pytest.raises(inject.QMPError, match="failed"):
            # fake replies error string -> raise
            q.inject_correctable("nonexistent", inject.COR_BAD_TLP)


def test_main_count_injects_n_times_over_one_connection(server):
    rc = inject.main(["--qmp", server.addr, "--id", "rp0", "--correctable", "0x40", "--count", "5"])
    assert rc == 0
    # one connection, five identical injections (this is what aer_test.sh drives)
    assert server.hmp_lines() == ["pcie_aer_inject_error -c rp0 0x40"] * 5


def test_main_rejects_nonpositive_count():
    # argparse error() exits before any connection, so no server is needed
    with pytest.raises(SystemExit):
        inject.main(["--qmp", "127.0.0.1:1", "--id", "rp0",
                     "--correctable", "0x40", "--count", "0"])
