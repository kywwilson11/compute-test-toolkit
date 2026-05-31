"""Sprint 4.4.1: CXL CCI mailbox abstraction (mirrors nvme/mi)."""
from __future__ import annotations

import pytest

from computetest.cxl import (
    CciOpcode,
    CciReturnCode,
    CxlCommand,
    CxlResponse,
    MailboxError,
    MailboxTransport,
    MockMailbox,
)


class TestContract:
    def test_transport_is_abstract(self):
        with pytest.raises(TypeError):
            MailboxTransport()                            # type: ignore[abstract]

    def test_opcodes(self):
        assert CciOpcode.IDENTIFY == 0x0001
        assert CciOpcode.PERFORM_MAINTENANCE == 0x0600
        assert CciOpcode.GET_HEALTH_INFO == 0x4200
        # Memory Device command set: Identify Memory Device 4000h, Get Partition Info 4100h.
        assert CciOpcode.IDENTIFY_MEMORY_DEVICE == 0x4000
        assert CciOpcode.GET_PARTITION_INFO == 0x4100

    def test_busy_return_code(self):
        # CXL command return codes: Busy = 0006h (0009h is FW Transfer Out of Order).
        assert CciReturnCode.BUSY == 0x0006

    def test_response_ok_and_background(self):
        assert CxlResponse(CciReturnCode.SUCCESS).ok
        assert not CxlResponse(CciReturnCode.UNSUPPORTED).ok
        assert CxlResponse(CciReturnCode.BACKGROUND_STARTED).background_started


class TestMockMailbox:
    def test_queued_response_round_trip(self):
        mb = MockMailbox()
        mb.open()
        mb.queue_response(CxlResponse(CciReturnCode.SUCCESS, b"\x01"))
        resp = mb.send_command(CxlCommand(CciOpcode.IDENTIFY))
        assert resp.ok and resp.payload == b"\x01"
        assert mb.sent[0].opcode == CciOpcode.IDENTIFY

    def test_stateful_responder(self):
        def responder(cmd):
            if cmd.opcode == CciOpcode.GET_HEALTH_INFO:
                return CxlResponse(CciReturnCode.SUCCESS, b"\x00")
            return None
        mb = MockMailbox(responder=responder)
        assert mb.send_command(CxlCommand(CciOpcode.GET_HEALTH_INFO)).ok

    def test_no_response_raises(self):
        with pytest.raises(MailboxError, match="no response queued"):
            MockMailbox().send_command(CxlCommand(CciOpcode.IDENTIFY))

    def test_responder_falls_through_to_queue(self):
        # Responder returns None -> fall through to the queued FIFO response.
        mb = MockMailbox(responder=lambda cmd: None)
        mb.queue_response(CxlResponse(CciReturnCode.UNSUPPORTED))
        assert not mb.send_command(CxlCommand(CciOpcode.SANITIZE)).ok

    def test_context_manager_opens_and_closes(self):
        with MockMailbox() as mb:
            assert mb.opened
            mb.queue_response(CxlResponse(CciReturnCode.SUCCESS))
            assert mb.send_command(CxlCommand(CciOpcode.IDENTIFY)).ok
        assert mb.closed
