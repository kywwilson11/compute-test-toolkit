"""
NVMe-MI 2.1 Asynchronous Event Messages (AEM).

NVMe-MI 2.1 adds OOB AEM — a BMC subscribes to a set of event categories,
and the management endpoint pushes notifications (health, temperature,
inventory change, security state) as they happen, with coalescing + rate
limiting so a noisy drive doesn't DoS the BMC.

This module models the subscription state machine + the AEM payload
dataclass + a deterministic mock subscription so the rest of the toolkit
can program against the surface today. Real bus interaction is via
``MctpTransport`` once a station provides ``libmctp`` + Aardvark/I3C.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class EventSeverity(IntEnum):
    """AEM severity field — INFO/WARNING/CRITICAL, per NVMe-MI 2.1 §6.6."""
    INFORMATIONAL = 0
    WARNING = 1
    CRITICAL = 2


class AsyncEventType(IntEnum):
    """A subset of the AEM event categories defined in NVMe-MI 2.1 §6.6
    (full enumeration is the spec's table 84 — extend here as the toolkit
    starts consuming each one)."""
    HEALTH = 0x00
    TEMPERATURE_THRESHOLD = 0x01
    INVENTORY = 0x02
    SECURITY_STATE = 0x03
    CONTROLLER_LIST_CHANGE = 0x04
    NS_LIST_CHANGE = 0x05
    AVAILABLE_SPARE_BELOW_THRESHOLD = 0x06
    VPD_CHANGE = 0x07
    FIRMWARE_ACTIVATION = 0x08


@dataclass(frozen=True)
class EventSubscription:
    """One subscription request: which categories the BMC wants, with what
    coalescing window."""
    event_types: frozenset[AsyncEventType]
    coalesce_window_ms: int = 1000   # 1s default
    rate_limit_per_s: int = 10       # cap pushed events per second

    def __post_init__(self) -> None:
        # frozenset() must be derived from an iterable; accept iterables in
        # constructor calls.
        if not self.event_types:
            raise ValueError("EventSubscription needs at least one event type")
        if self.coalesce_window_ms < 0:
            raise ValueError("coalesce_window_ms must be >= 0")
        if self.rate_limit_per_s <= 0:
            raise ValueError("rate_limit_per_s must be > 0")


@dataclass
class AsyncEventMessage:
    """One push from the management endpoint to the BMC."""
    event_type: AsyncEventType
    severity: EventSeverity
    timestamp_ms: int                # monotonic ms since subscription open
    payload: dict = field(default_factory=dict)
    sequence: int = 0                # monotonic per-subscription sequence


@dataclass
class Subscription:
    """The runtime state of one subscription — a small lifecycle wrapper that
    records sent events, applies coalescing + rate limits, and gives tests a
    deterministic way to assert what the BMC saw.

    Real impl: the management endpoint owns this state; the BMC ingests
    ``AsyncEventMessage`` deliveries via the MCTP transport. The mock impl
    here keeps everything in-process so the subscription state machine can
    be tested.
    """
    request: EventSubscription
    sent: list[AsyncEventMessage] = field(default_factory=list)
    next_sequence: int = 0
    _last_emit_by_type: dict[AsyncEventType, int] = field(default_factory=dict)
    _emitted_this_second: int = 0
    _window_start_ms: int = 0

    def deliver(self, event_type: AsyncEventType, severity: EventSeverity,
                 *, timestamp_ms: int, payload: dict | None = None) -> bool:
        """Try to deliver one event. Returns True iff it was emitted (False on
        type-not-subscribed / rate-limited / coalesced)."""
        if event_type not in self.request.event_types:
            return False
        # Rate limit: per-second window.
        if timestamp_ms - self._window_start_ms >= 1000:
            self._window_start_ms = timestamp_ms
            self._emitted_this_second = 0
        if self._emitted_this_second >= self.request.rate_limit_per_s:
            return False
        # Coalesce per event TYPE: skip if THIS type was emitted within its window.
        # Keying on the type (not just sent[-1]) means an interleaved A,B,A burst
        # still coalesces the second A, matching the per-event AEM throttling model.
        last = self._last_emit_by_type.get(event_type)
        if (self.request.coalesce_window_ms > 0
                and last is not None
                and timestamp_ms - last < self.request.coalesce_window_ms):
            return False
        seq = self.next_sequence
        self.next_sequence += 1
        self.sent.append(AsyncEventMessage(
            event_type=event_type, severity=severity,
            timestamp_ms=timestamp_ms, payload=payload or {},
            sequence=seq,
        ))
        self._last_emit_by_type[event_type] = timestamp_ms
        self._emitted_this_second += 1
        return True
