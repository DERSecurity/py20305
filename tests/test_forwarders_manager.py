"""Tests for ForwarderManager."""

from pathlib import Path

from py20305.forwarders.base import MessageDirection, MessageFrame
from py20305.forwarders.config import ForwarderConfig, MQTTForwarderConfig
from py20305.forwarders.manager import ForwarderManager


class MockForwarder:
    """Mock forwarder for testing."""

    def __init__(self, name: str = "mock") -> None:
        self._name = name
        self.started = False
        self.stopped = False
        self.queued_messages: list[MessageFrame] = []
        self.client_lfdi: str | None = None

    @property
    def name(self) -> str:
        return self._name

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def queue_message(self, frame: MessageFrame) -> None:
        self.queued_messages.append(frame)

    def get_statistics(self) -> dict:
        return {
            "name": self._name,
            "messages_queued": len(self.queued_messages),
        }


class TestForwarderManager:
    def test_initialization(self) -> None:
        manager = ForwarderManager()
        assert manager.running is False
        assert manager.forwarders == []
        assert manager.client_lfdi is None

    def test_add_forwarder(self) -> None:
        manager = ForwarderManager()
        forwarder = MockForwarder("test1")

        manager.add_forwarder(forwarder)

        assert len(manager.forwarders) == 1
        assert manager.forwarders[0] is forwarder

    def test_add_duplicate_forwarder_ignored(self) -> None:
        manager = ForwarderManager()
        forwarder = MockForwarder()

        manager.add_forwarder(forwarder)
        manager.add_forwarder(forwarder)

        assert len(manager.forwarders) == 1

    def test_add_multiple_forwarders(self) -> None:
        manager = ForwarderManager()
        f1 = MockForwarder("first")
        f2 = MockForwarder("second")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)

        assert len(manager.forwarders) == 2

    def test_remove_forwarder(self) -> None:
        manager = ForwarderManager()
        forwarder = MockForwarder()

        manager.add_forwarder(forwarder)
        result = manager.remove_forwarder(forwarder)

        assert result is True
        assert len(manager.forwarders) == 0

    def test_remove_nonexistent_forwarder(self) -> None:
        manager = ForwarderManager()
        forwarder = MockForwarder()

        result = manager.remove_forwarder(forwarder)
        assert result is False

    def test_client_lfdi_propagation(self) -> None:
        manager = ForwarderManager()
        f1 = MockForwarder("f1")
        f2 = MockForwarder("f2")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)

        manager.client_lfdi = "test_lfdi"

        assert f1.client_lfdi == "test_lfdi"
        assert f2.client_lfdi == "test_lfdi"

    def test_client_lfdi_on_add(self) -> None:
        manager = ForwarderManager()
        manager.client_lfdi = "preset_lfdi"

        forwarder = MockForwarder()
        manager.add_forwarder(forwarder)

        assert forwarder.client_lfdi == "preset_lfdi"


class TestForwarderManagerLifecycle:
    async def test_start_all_forwarders(self) -> None:
        manager = ForwarderManager()
        f1 = MockForwarder("f1")
        f2 = MockForwarder("f2")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)

        await manager.start()

        assert manager.running is True
        assert f1.started is True
        assert f2.started is True

    async def test_start_when_already_running(self) -> None:
        manager = ForwarderManager()
        await manager.start()
        await manager.start()  # Should not raise

        assert manager.running is True

    async def test_stop_all_forwarders(self) -> None:
        manager = ForwarderManager()
        f1 = MockForwarder("f1")
        f2 = MockForwarder("f2")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)

        await manager.start()
        await manager.stop()

        assert manager.running is False
        assert f1.stopped is True
        assert f2.stopped is True

    async def test_stop_when_not_running(self) -> None:
        manager = ForwarderManager()
        await manager.stop()  # Should not raise

    async def test_forwarder_start_error_doesnt_stop_others(self) -> None:
        manager = ForwarderManager()

        f1 = MockForwarder("f1")
        f2 = MockForwarder("failing")

        async def failing_start() -> None:
            raise RuntimeError("Connection failed")

        f2.start = failing_start  # type: ignore[method-assign]

        f3 = MockForwarder("f3")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)
        manager.add_forwarder(f3)

        await manager.start()

        assert f1.started is True
        assert f3.started is True
        assert manager.running is True


class TestMessageRouting:
    async def test_queue_to_all_forwarders(self) -> None:
        manager = ForwarderManager()
        f1 = MockForwarder("f1")
        f2 = MockForwarder("f2")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)
        await manager.start()

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={"data": 123},
        )
        manager.queue_message(frame)

        assert len(f1.queued_messages) == 1
        assert len(f2.queued_messages) == 1
        assert f1.queued_messages[0] is frame
        assert f2.queued_messages[0] is frame

    def test_queue_when_not_running_drops_message(self) -> None:
        manager = ForwarderManager()
        forwarder = MockForwarder()
        manager.add_forwarder(forwarder)

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        manager.queue_message(frame)

        assert len(forwarder.queued_messages) == 0

    async def test_forwarder_error_doesnt_affect_others(self) -> None:
        manager = ForwarderManager()

        f1 = MockForwarder("f1")
        f2 = MockForwarder("failing")

        def failing_queue(frame: MessageFrame) -> None:
            raise RuntimeError("Queue error")

        f2.queue_message = failing_queue  # type: ignore[method-assign]

        f3 = MockForwarder("f3")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)
        manager.add_forwarder(f3)
        await manager.start()

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        manager.queue_message(frame)

        assert len(f1.queued_messages) == 1
        assert len(f3.queued_messages) == 1


class TestStatistics:
    async def test_get_statistics(self) -> None:
        manager = ForwarderManager()
        f1 = MockForwarder("f1")
        f2 = MockForwarder("f2")

        manager.add_forwarder(f1)
        manager.add_forwarder(f2)
        manager.client_lfdi = "test_lfdi"
        await manager.start()

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        manager.queue_message(frame)

        stats = manager.get_statistics()

        assert stats["running"] is True
        assert stats["forwarder_count"] == 2
        assert stats["client_lfdi"] == "test_lfdi"
        assert "f1" in stats["forwarders"]
        assert "f2" in stats["forwarders"]
        assert stats["forwarders"]["f1"]["messages_queued"] == 1


class TestFromConfig:
    async def test_from_config_with_mqtt(self, tmp_path: Path) -> None:
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        ca_path = tmp_path / "ca.pem"
        cert_path.write_text("cert")
        key_path.write_text("key")
        ca_path.write_text("ca")

        config = ForwarderConfig(
            mqtt=MQTTForwarderConfig(
                endpoint="xxx.iot.region.amazonaws.com",
                cert_path=cert_path,
                key_path=key_path,
                ca_path=ca_path,
            ),
        )

        manager = await ForwarderManager.from_config(config)

        assert len(manager.forwarders) == 1

    async def test_from_config_skips_disabled(self, tmp_path: Path) -> None:
        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        ca_path = tmp_path / "ca.pem"
        cert_path.write_text("cert")
        key_path.write_text("key")
        ca_path.write_text("ca")

        config = ForwarderConfig(
            mqtt=MQTTForwarderConfig(
                endpoint="xxx.iot.region.amazonaws.com",
                enabled=False,
                cert_path=cert_path,
                key_path=key_path,
                ca_path=ca_path,
            ),
        )

        manager = await ForwarderManager.from_config(config)

        assert len(manager.forwarders) == 0

    async def test_from_config_no_forwarders(self) -> None:
        config = ForwarderConfig()

        manager = await ForwarderManager.from_config(config)

        assert len(manager.forwarders) == 0
