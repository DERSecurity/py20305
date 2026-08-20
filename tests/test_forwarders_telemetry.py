"""Measured device state as a third payload kind on the forwarder transport.

The transport already carries captured protocol exchanges and pre-serialized
events. Telemetry joins them rather than opening a second egress path: the
manager already handles queueing, connection state and broker selection, and a
parallel path would duplicate all of it.

What these tests pin is the direction of the dependency and the separation of
the kinds. A forwarder is a sink -- it is fed, and it never reads from whatever
produced the frame -- and each kind lands on its own topic so a subscriber can
choose at the broker rather than filtering on arrival.
"""

from __future__ import annotations

from py20305.forwarders.base import (
    AbstractForwarder,
    EventFrame,
    MessageFrame,
    TelemetryFrame,
    TelemetryPoint,
)
from py20305.forwarders.manager import ForwarderManager

DEVICE = "ab" * 20


def _frame(**points: float) -> TelemetryFrame:
    return TelemetryFrame(
        device=DEVICE,
        points={
            key: TelemetryPoint(value=value, source_timestamp=100.0, quality="good")
            for key, value in points.items()
        },
        quality="good",
        last_success=100.0,
    )


class _Sink:
    """A forwarder that records what it was handed, and nothing else."""

    def __init__(self, name: str = "sink") -> None:
        self._name = name
        self.telemetry: list[TelemetryFrame] = []
        self.events: list[EventFrame] = []
        self.messages: list[MessageFrame] = []

    @property
    def name(self) -> str:
        return self._name

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def queue_message(self, frame: MessageFrame) -> None:
        self.messages.append(frame)

    def queue_event(self, event: EventFrame) -> None:
        self.events.append(event)

    def queue_telemetry(self, frame: TelemetryFrame) -> None:
        self.telemetry.append(frame)


class TestTelemetryFrame:
    def test_a_point_carries_when_the_device_was_read(self) -> None:
        """Not when the frame was published. A consumer judging freshness needs
        the former, and arrival time cannot supply it -- a retained value
        arrives just as promptly as a fresh one."""
        frame = _frame(W=4200)

        assert frame.points["W"].source_timestamp == 100.0
        assert frame.points["W"].quality == "good"

    def test_connector_quality_is_separate_from_store_quality(self) -> None:
        """They answer different questions: whether the device trusts the
        reading, and whether it was read recently enough."""
        point = TelemetryPoint(
            value=4200, source_timestamp=100.0, quality="stale", protocol_quality=0b1
        )

        assert point.quality == "stale"
        assert point.protocol_quality == 0b1


class TestTelemetryRouting:
    async def test_telemetry_reaches_every_forwarder(self) -> None:
        manager = ForwarderManager()
        first, second = _Sink("first"), _Sink("second")
        manager.add_forwarder(first)
        manager.add_forwarder(second)
        await manager.start()

        frame = _frame(W=4200)
        manager.queue_telemetry(frame)

        assert first.telemetry == [frame]
        assert second.telemetry == [frame]

    async def test_the_kinds_stay_separate(self) -> None:
        """A telemetry frame must not arrive as a protocol message: nine of
        MessageFrame's fields describe an HTTP exchange and mean nothing for a
        measurement."""
        manager = ForwarderManager()
        sink = _Sink()
        manager.add_forwarder(sink)
        await manager.start()

        manager.queue_telemetry(_frame(W=4200))

        assert len(sink.telemetry) == 1
        assert sink.messages == []
        assert sink.events == []

    def test_telemetry_is_dropped_when_not_running(self) -> None:
        manager = ForwarderManager()
        sink = _Sink()
        manager.add_forwarder(sink)

        manager.queue_telemetry(_frame(W=4200))

        assert sink.telemetry == []

    async def test_one_forwarder_raising_does_not_stop_the_others(self) -> None:
        class Exploding(_Sink):
            def queue_telemetry(self, frame: TelemetryFrame) -> None:
                raise RuntimeError("sink is broken")

        manager = ForwarderManager()
        broken, healthy = Exploding("broken"), _Sink("healthy")
        manager.add_forwarder(broken)
        manager.add_forwarder(healthy)
        await manager.start()

        manager.queue_telemetry(_frame(W=4200))

        assert len(healthy.telemetry) == 1


class TestDefaultIsToDecline:
    def test_a_forwarder_that_carries_only_messages_drops_telemetry(self) -> None:
        """Concrete rather than abstract: telemetry arrived after these
        forwarders did, and one built to carry protocol capture is not wrong to
        ignore it. Making it abstract would break every implementation."""

        class OnlyMessages(AbstractForwarder):
            def __init__(self) -> None:
                super().__init__(name="only-messages")

            async def start(self) -> None:
                pass

            async def stop(self) -> None:
                pass

            def queue_message(self, frame: MessageFrame) -> None:
                pass

            def get_statistics(self) -> dict:
                return {}

        forwarder = OnlyMessages()

        # Declines without raising, which is what keeps it a valid sink.
        forwarder.queue_telemetry(_frame(W=4200))
