"""Tests for forwarders base types."""

from datetime import UTC, datetime

from py20305.forwarders.base import (
    AbstractForwarder,
    BaseForwarder,
    MessageDirection,
    MessageFrame,
)


class TestMessageDirection:
    def test_upstream_value(self) -> None:
        assert MessageDirection.UPSTREAM.value == "upstream"

    def test_downstream_value(self) -> None:
        assert MessageDirection.DOWNSTREAM.value == "downstream"


class TestMessageFrame:
    def test_minimal_construction(self) -> None:
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERControl",
            content={"test": "data"},
        )
        assert frame.direction == MessageDirection.UPSTREAM
        assert frame.message_type == "DERControl"
        assert frame.content == {"test": "data"}
        assert frame.is_valid is True
        assert frame.validation_error is None
        assert frame.http_method is None
        assert frame.uri is None
        assert frame.status_code is None
        assert frame.server_host is None
        assert frame.server_port is None
        assert frame.metadata == {}

    def test_full_construction(self) -> None:
        ts = datetime(2026, 2, 3, 12, 0, 0, tzinfo=UTC)
        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="EndDeviceList",
            content=[{"lfdi": "1231231231231231231231231231231231231231"}],
            timestamp=ts,
            is_valid=False,
            validation_error="Missing required field",
            http_method="GET",
            uri="/edev",
            status_code=200,
            server_host="iot.example.com",
            server_port=8443,
            metadata={"client_lfdi": "device123"},
        )
        assert frame.direction == MessageDirection.DOWNSTREAM
        assert frame.message_type == "EndDeviceList"
        assert frame.content == [{"lfdi": "1231231231231231231231231231231231231231"}]
        assert frame.timestamp == ts
        assert frame.is_valid is False
        assert frame.validation_error == "Missing required field"
        assert frame.http_method == "GET"
        assert frame.uri == "/edev"
        assert frame.status_code == 200
        assert frame.server_host == "iot.example.com"
        assert frame.server_port == 8443
        assert frame.metadata == {"client_lfdi": "device123"}

    def test_timestamp_defaults_to_now(self) -> None:
        before = datetime.now(UTC)
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        after = datetime.now(UTC)
        assert before <= frame.timestamp <= after

    def test_metadata_is_mutable(self) -> None:
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        frame.metadata["key"] = "value"
        assert frame.metadata["key"] == "value"

    def test_metadata_default_is_not_shared(self) -> None:
        frame1 = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        frame2 = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        frame1.metadata["key"] = "value1"
        assert "key" not in frame2.metadata


class TestBaseForwarderProtocol:
    def test_protocol_can_be_checked(self) -> None:
        assert isinstance(BaseForwarder, type)

    def test_protocol_has_required_methods(self) -> None:
        assert hasattr(BaseForwarder, "start")
        assert hasattr(BaseForwarder, "stop")
        assert hasattr(BaseForwarder, "queue_message")
        assert hasattr(BaseForwarder, "get_statistics")


class ConcreteForwarder(AbstractForwarder):
    """Concrete implementation for testing AbstractForwarder."""

    def __init__(self, name: str = "test") -> None:
        super().__init__(name)
        self.queued_messages: list[MessageFrame] = []

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def queue_message(self, frame: MessageFrame) -> None:
        self._record_queued()
        self.queued_messages.append(frame)


class TestAbstractForwarder:
    def test_name_property(self) -> None:
        forwarder = ConcreteForwarder("my-forwarder")
        assert forwarder.name == "my-forwarder"

    def test_running_initially_false(self) -> None:
        forwarder = ConcreteForwarder()
        assert forwarder.running is False

    async def test_start_sets_running(self) -> None:
        forwarder = ConcreteForwarder()
        await forwarder.start()
        assert forwarder.running is True

    async def test_stop_clears_running(self) -> None:
        forwarder = ConcreteForwarder()
        await forwarder.start()
        await forwarder.stop()
        assert forwarder.running is False

    def test_get_statistics_initial(self) -> None:
        forwarder = ConcreteForwarder("test")
        stats = forwarder.get_statistics()
        assert stats["messages_queued"] == 0
        assert stats["messages_published"] == 0
        assert stats["publish_errors"] == 0
        assert stats["last_publish_time"] is None
        assert stats["last_error"] is None
        assert stats["running"] is False
        assert stats["name"] == "test"

    def test_events_queued_starts_at_an_explicit_zero(self) -> None:
        """A forwarder that never carries events reports 0, not a missing key.

        An operator reading the stats dict must be able to tell "no events
        yet" from "this build does not count events at all".
        """
        forwarder = ConcreteForwarder("test")
        assert forwarder.get_statistics()["events_queued"] == 0

    def test_telemetry_queued_starts_at_an_explicit_zero(self) -> None:
        """The same for telemetry: a kind counter is seeded, not conjured."""
        forwarder = ConcreteForwarder("test")
        assert forwarder.get_statistics()["telemetry_queued"] == 0

    def test_record_queued_increments_counter(self) -> None:
        forwarder = ConcreteForwarder()
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        forwarder.queue_message(frame)
        forwarder.queue_message(frame)
        assert forwarder.get_statistics()["messages_queued"] == 2

    def test_record_success_updates_stats(self) -> None:
        forwarder = ConcreteForwarder()
        forwarder._record_success()
        stats = forwarder.get_statistics()
        assert stats["messages_published"] == 1
        assert stats["last_publish_time"] is not None

    def test_record_error_updates_stats(self) -> None:
        forwarder = ConcreteForwarder()
        forwarder._record_error("Connection failed")
        stats = forwarder.get_statistics()
        assert stats["publish_errors"] == 1
        assert stats["last_error"] == "Connection failed"

    def test_queue_message_stores_frames(self) -> None:
        forwarder = ConcreteForwarder()
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={"key": "value"},
        )
        forwarder.queue_message(frame)
        assert len(forwarder.queued_messages) == 1
        assert forwarder.queued_messages[0] is frame
