"""
Sprint 2.4: NVMe-MI v2.1 skeleton — MCTP framing, MI message encode/decode,
AEM subscription state machine, Controller Health Status Poll.

Each test is the pytest node referenced by one row of
``docs/nvme/unh_iol_v25_coverage_matrix.md``. Keeping the node names stable
matters — the coverage matrix is the traceability source-of-truth.
"""
from __future__ import annotations

import pytest

from computetest.nvme.mi import (
    AsyncEventType,
    ControllerHealthStatus,
    ControlPrimitive,
    EventSeverity,
    EventSubscription,
    MctpEndpointId,
    MctpError,
    MctpMessage,
    MessageType,
    MiMessage,
    MiMessageError,
    MockMctpTransport,
    Subscription,
    encode_mi_request,
    poll_controller_health,
)
from computetest.nvme.mi.mctp import MctpMessageType
from computetest.nvme.mi.messages import _crc32c, decode_mi_response


# ----------------------------------------------------------------------------
# MCTP — Group 1 (MCTP Base) and Group 2 (Control Messages)
# ----------------------------------------------------------------------------
class TestMctpEndpointId:
    """Covers UNH-IOL v25 test 1.1 (MCTP framing — EID range validation)."""
    def test_eid_range_validation(self):
        with pytest.raises(MctpError, match="out of 8-bit range"):
            MctpEndpointId(-1)
        with pytest.raises(MctpError, match="out of 8-bit range"):
            MctpEndpointId(256)

    def test_null_eid(self):
        eid = MctpEndpointId(0x00)
        assert eid.is_null and not eid.is_routable

    def test_broadcast_eid_is_broadcast(self):
        """UNH-IOL v25 test 1.2 — broadcast EID handling."""
        eid = MctpEndpointId(0xFF)
        assert eid.is_broadcast
        assert not eid.is_routable

    def test_routable_range(self):
        for v in (0x08, 0x55, 0xEF):
            assert MctpEndpointId(v).is_routable
        for v in (0x01, 0x07, 0xF0, 0xFE):
            assert MctpEndpointId(v).is_reserved


class TestMctpMessage:
    def test_message_tag_range(self):
        with pytest.raises(MctpError, match="message tag"):
            MctpMessage(
                source=MctpEndpointId(0x08),
                destination=MctpEndpointId(0x09),
                message_type=MctpMessageType.NVME_MI,
                message_tag=8,
            )

    def test_message_len_includes_type_byte(self):
        m = MctpMessage(
            source=MctpEndpointId(0x08),
            destination=MctpEndpointId(0x09),
            message_type=MctpMessageType.NVME_MI,
            body=b"\x01\x02\x03",
        )
        assert len(m) == 4                                  # 3 payload + 1 type byte

    def test_nvme_mi_type_value(self):
        # NVMe-MI uses MCTP message type 0x04 (DSP0239 §6.5).
        assert int(MctpMessageType.NVME_MI) == 0x04


class TestMctpMessageRoundTrip:
    """Covers UNH-IOL v25 test 2.1 (MCTP control — Get Endpoint ID via mock
    transport — the spine for control-message exchange testing)."""
    def test_get_endpoint_id_via_mock(self):
        t = MockMctpTransport(our_eid=0x08).__enter__()
        try:
            req = MctpMessage(
                source=MctpEndpointId(0x08),
                destination=MctpEndpointId(0x09),
                message_type=MctpMessageType.MCTP_CONTROL,
                body=b"\x02",                               # Get Endpoint ID opcode
            )
            t.queue_response(MctpMessage(
                source=MctpEndpointId(0x09),
                destination=MctpEndpointId(0x08),
                message_type=MctpMessageType.MCTP_CONTROL,
                body=b"\x09",                               # response payload (mock)
            ))
            t.send(req)
            resp = t.recv()
            assert resp.source.value == 0x09
            assert resp.body == b"\x09"
            assert t.sent == [req]
        finally:
            t.close()

    def test_recv_without_queued_response_raises(self):
        t = MockMctpTransport().__enter__()
        try:
            with pytest.raises(MctpError, match="no MCTP response queued"):
                t.recv()
        finally:
            t.close()


# ----------------------------------------------------------------------------
# MI message encode/decode — Group 5 (MI Message Processing)
# ----------------------------------------------------------------------------
class TestMiMessageEncode:
    """Covers UNH-IOL v25 test 5.1 — NMP byte field decode."""
    def test_encode_decode_round_trip(self):
        original = MiMessage(
            message_type=MessageType.NVME_MI_COMMAND,
            opcode=0x02,
            csi=1,
            slot_id=2,
            nmhdr=b"\x00\x00\x00\x00",
            data=b"\xAB\xCD\xEF",
        )
        wire = encode_mi_request(original)
        decoded = decode_mi_response(wire)
        assert decoded.message_type == MessageType.NVME_MI_COMMAND
        assert decoded.opcode == 0x02
        assert decoded.csi == 1
        assert decoded.slot_id == 2
        assert decoded.data == b"\xAB\xCD\xEF"

    def test_csi_out_of_range_rejected(self):
        msg = MiMessage(
            message_type=MessageType.NVME_MI_COMMAND, opcode=0x02,
            csi=2, slot_id=0, nmhdr=b"\x00\x00\x00\x00")
        with pytest.raises(MiMessageError, match="CSI"):
            encode_mi_request(msg)

    def test_slot_id_out_of_range_rejected(self):
        msg = MiMessage(
            message_type=MessageType.NVME_MI_COMMAND, opcode=0x02,
            csi=0, slot_id=4, nmhdr=b"\x00\x00\x00\x00")
        with pytest.raises(MiMessageError, match="Slot ID"):
            encode_mi_request(msg)

    def test_nmhdr_must_be_four_bytes(self):
        with pytest.raises(MiMessageError, match="4 bytes"):
            MiMessage(message_type=MessageType.NVME_MI_COMMAND,
                       opcode=0x02, nmhdr=b"\x00\x00")


class TestMiCRC:
    """Covers UNH-IOL v25 test 5.2 — MIC (CRC-32C) verify."""
    def test_crc_round_trip_detects_corruption(self):
        msg = MiMessage(
            message_type=MessageType.NVME_MI_COMMAND, opcode=0x01,
            nmhdr=b"\x00\x00\x00\x00", data=b"\x11\x22\x33\x44")
        wire = bytearray(encode_mi_request(msg))
        # Corrupt one data byte.
        wire[6] ^= 0xFF
        with pytest.raises(MiMessageError, match="MIC mismatch"):
            decode_mi_response(bytes(wire))

    def test_crc32c_known_vector(self):
        # CRC-32C of an empty string is 0 (no input -> initial XOR cancels).
        assert _crc32c(b"") == 0x00000000
        # CRC-32C of b"123456789" is 0xe3069283 (RFC 3720 reference vector).
        assert _crc32c(b"123456789") == 0xE3069283


class TestControlPrimitive:
    """Covers UNH-IOL v25 tests 6.1 and 6.2 (Pause/Resume)."""
    def test_pause_opcode(self):
        assert int(ControlPrimitive.PAUSE) == 0x00
        msg = MiMessage(
            message_type=MessageType.CONTROL_PRIMITIVE,
            opcode=int(ControlPrimitive.PAUSE),
            nmhdr=b"\x00\x00\x00\x00")
        wire = encode_mi_request(msg)
        assert decode_mi_response(wire).opcode == 0x00

    def test_resume_opcode(self):
        assert int(ControlPrimitive.RESUME) == 0x01
        msg = MiMessage(
            message_type=MessageType.CONTROL_PRIMITIVE,
            opcode=int(ControlPrimitive.RESUME),
            nmhdr=b"\x00\x00\x00\x00")
        wire = encode_mi_request(msg)
        assert decode_mi_response(wire).opcode == 0x01


# ----------------------------------------------------------------------------
# Asynchronous Event Messages — Group 9 (Management Enhancement)
# ----------------------------------------------------------------------------
class TestAemSubscription:
    def test_subscribe_requires_event_set(self):
        """UNH-IOL v25 test 9.1 — Async Event Subscribe rejects empty set."""
        with pytest.raises(ValueError, match="at least one event type"):
            EventSubscription(event_types=frozenset())

    def test_invalid_window_rejected(self):
        with pytest.raises(ValueError, match="coalesce_window_ms"):
            EventSubscription(event_types=frozenset({AsyncEventType.HEALTH}),
                              coalesce_window_ms=-1)

    def test_invalid_rate_limit_rejected(self):
        with pytest.raises(ValueError, match="rate_limit_per_s"):
            EventSubscription(event_types=frozenset({AsyncEventType.HEALTH}),
                              rate_limit_per_s=0)

    def test_subscribed_event_delivered(self):
        sub = Subscription(request=EventSubscription(
            event_types=frozenset({AsyncEventType.HEALTH}),
            coalesce_window_ms=0,
            rate_limit_per_s=10))
        assert sub.deliver(AsyncEventType.HEALTH, EventSeverity.INFORMATIONAL,
                            timestamp_ms=0)
        assert len(sub.sent) == 1

    def test_unsubscribed_event_dropped(self):
        sub = Subscription(request=EventSubscription(
            event_types=frozenset({AsyncEventType.HEALTH})))
        assert not sub.deliver(
            AsyncEventType.TEMPERATURE_THRESHOLD, EventSeverity.WARNING,
            timestamp_ms=0)
        assert sub.sent == []

    def test_coalesce_within_window(self):
        """UNH-IOL v25 test 9.2 — AEM delivery coalescing."""
        sub = Subscription(request=EventSubscription(
            event_types=frozenset({AsyncEventType.HEALTH}),
            coalesce_window_ms=1000, rate_limit_per_s=10))
        assert sub.deliver(AsyncEventType.HEALTH, EventSeverity.INFORMATIONAL,
                            timestamp_ms=0)
        # Same type within window -> dropped.
        assert not sub.deliver(AsyncEventType.HEALTH,
                                EventSeverity.INFORMATIONAL,
                                timestamp_ms=500)
        # Past the window -> emitted.
        assert sub.deliver(AsyncEventType.HEALTH, EventSeverity.INFORMATIONAL,
                            timestamp_ms=1500)
        assert len(sub.sent) == 2

    def test_rate_limit_per_second(self):
        """UNH-IOL v25 test 9.3 — AEM delivery rate limiting."""
        sub = Subscription(request=EventSubscription(
            event_types=frozenset({AsyncEventType.HEALTH,
                                    AsyncEventType.TEMPERATURE_THRESHOLD,
                                    AsyncEventType.INVENTORY}),
            coalesce_window_ms=0, rate_limit_per_s=2))
        # Three different event types in <1s; rate limit caps at 2.
        delivered = [
            sub.deliver(AsyncEventType.HEALTH, EventSeverity.INFORMATIONAL,
                         timestamp_ms=0),
            sub.deliver(AsyncEventType.TEMPERATURE_THRESHOLD,
                         EventSeverity.INFORMATIONAL, timestamp_ms=100),
            sub.deliver(AsyncEventType.INVENTORY,
                         EventSeverity.INFORMATIONAL, timestamp_ms=200),
        ]
        assert delivered == [True, True, False]

    def test_sequence_numbers_monotonic(self):
        sub = Subscription(request=EventSubscription(
            event_types=frozenset({AsyncEventType.HEALTH,
                                    AsyncEventType.TEMPERATURE_THRESHOLD}),
            coalesce_window_ms=0,
            rate_limit_per_s=100))
        sub.deliver(AsyncEventType.HEALTH, EventSeverity.INFORMATIONAL, timestamp_ms=0)
        sub.deliver(AsyncEventType.TEMPERATURE_THRESHOLD,
                     EventSeverity.WARNING, timestamp_ms=10)
        sub.deliver(AsyncEventType.HEALTH, EventSeverity.CRITICAL, timestamp_ms=20)
        assert [e.sequence for e in sub.sent] == [0, 1, 2]


# ----------------------------------------------------------------------------
# Controller Health Status Poll — Group 7
# ----------------------------------------------------------------------------
class TestControllerHealthPoll:
    """Covers UNH-IOL v25 test 7.6 — Controller Health Status Poll."""
    def test_mock_poll_is_healthy(self):
        chsp = poll_controller_health(mock=True)
        assert isinstance(chsp, ControllerHealthStatus)
        assert chsp.healthy
        assert chsp.temperature_c == 41.0
        assert chsp.available_spare == 100
        assert chsp.critical_warning == 0

    def test_mock_poll_controller_id_propagates(self):
        chsp = poll_controller_health(mock=True, controller_id=3)
        assert chsp.controller_id == 3

    def test_chsp_to_dict_round_trip(self):
        chsp = poll_controller_health(mock=True)
        d = chsp.to_dict()
        assert d["healthy"] is True
        assert d["temperature_c"] == 41.0


# ----------------------------------------------------------------------------
# Module surface
# ----------------------------------------------------------------------------
def test_public_exports():
    expected = {"MctpEndpointId", "MctpError", "MctpMessage", "MctpTransport",
                "MockMctpTransport", "ControlPrimitive", "MessageType",
                "MiMessage", "MiMessageError", "encode_mi_request",
                "AsyncEventMessage", "AsyncEventType", "EventSeverity",
                "EventSubscription", "Subscription",
                "ControllerHealthStatus", "poll_controller_health"}
    import computetest.nvme.mi as mi
    for sym in expected:
        assert hasattr(mi, sym), f"missing public export: {sym}"
