"""
Sprint 4.4.11: QEMU cxl_type3 injection client (sim/qemu/cxl_inject.py).

Exercises the cxl-inject-* QMP command construction against the same fake QMP
server used by the PCIe AER injector, so it runs on a laptop with no QEMU.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest
from test_qmp_inject import FakeQMPServer  # reuse the fake QMP server + import path

_SIM = pathlib.Path(__file__).resolve().parent.parent / "sim" / "qemu"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SIM / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


inject = _load("inject")
cxl_inject = _load("cxl_inject")


@pytest.fixture
def server():
    s = FakeQMPServer()
    yield s
    s.close()


def _cmd(server, execute):
    return next(c for c in server.commands if c.get("execute") == execute)


class TestCxlInject:
    def test_inject_poison_builds_qmp(self, server):
        with inject.QMPClient(server.addr) as q:
            cxl_inject.CxlInjector(q).inject_poison("/machine/cxl", start=0x1000,
                                                    length=64)
        cmd = _cmd(server, "cxl-inject-poison")
        assert cmd["arguments"] == {"path": "/machine/cxl", "start": 0x1000,
                                    "length": 64}

    def test_inject_uncorrectable(self, server):
        with inject.QMPClient(server.addr) as q:
            cxl_inject.CxlInjector(q).inject_uncorrectable(
                "/dev", errors=[{"type": "cache-data-parity"}])
        cmd = _cmd(server, "cxl-inject-uncorrectable-errors")
        assert cmd["arguments"]["errors"] == [{"type": "cache-data-parity"}]

    def test_inject_correctable_and_event(self, server):
        with inject.QMPClient(server.addr) as q:
            inj = cxl_inject.CxlInjector(q)
            inj.inject_correctable("/dev", error_type="crc-threshold")
            inj.inject_general_media_event("/dev", log="informational", flags=0,
                                           dpa=0x2000, descriptor=0x01,
                                           type_=0x00, transaction_type=0x01,
                                           sub_type=0x00)
        execs = [c.get("execute") for c in server.commands]
        assert "cxl-inject-correctable-error" in execs
        assert "cxl-inject-general-media-event" in execs
        # Assert the COMPLETE required-arg set per qapi/cxl.json@v11.0.0
        # (CXLGeneralMediaEvent over CXLCommonEventBase): a missing required
        # key is what real QEMU rejects, so the old dpa-only assertion was
        # tautological. Optionals (channel/rank/device/component-id) omitted.
        assert _cmd(server, "cxl-inject-general-media-event")["arguments"] == {
            "path": "/dev", "log": "informational", "flags": 0, "dpa": 0x2000,
            "descriptor": 0x01, "type": 0x00, "transaction-type": 0x01,
            "sub-type": 0x00}
