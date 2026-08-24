"""Tests for MQTT forwarder."""

import asyncio
import json
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305 import diagnostics
from py20305.diagnostics import DiagnosticsStore
from py20305.forwarders.base import (
    EventFrame,
    MessageDirection,
    MessageFrame,
    TelemetryFrame,
    TelemetryPoint,
)
from py20305.forwarders.config import MQTTForwarderConfig
from py20305.forwarders.mqtt_forwarder import (
    _CAPTURE_RUN,
    _TELEMETRY_SLICE,
    MQTTForwarder,
)


@pytest.fixture
def mock_config(tmp_path: Path) -> MQTTForwarderConfig:
    """Create a mock MQTT config with temporary certificate files."""
    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    ca_path = tmp_path / "ca.pem"

    # Create dummy certificate files
    cert_path.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")
    key_path.write_text("-----BEGIN PRIVATE KEY-----\ntest\n-----END PRIVATE KEY-----")
    ca_path.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----")

    return MQTTForwarderConfig(
        endpoint="test.iot.amazonaws.com",
        port=8883,
        cert_path=cert_path,
        key_path=key_path,
        ca_path=ca_path,
        topic_base="test_topic",
        client_id_prefix="test_client",
    )


@pytest.fixture
def plain_mqtt_config() -> MQTTForwarderConfig:
    """Create a plain MQTT config without TLS (e.g., for RabbitMQ)."""
    return MQTTForwarderConfig(
        endpoint="rabbitmq",
        port=1883,
        topic_base="test_topic",
        client_id_prefix="test_client",
    )


class TestMQTTForwarderConfig:
    def test_valid_config(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        ca = tmp_path / "ca.pem"
        cert.touch()
        key.touch()
        ca.touch()

        config = MQTTForwarderConfig(
            endpoint="iot.example.com",
            cert_path=cert,
            key_path=key,
            ca_path=ca,
        )
        assert config.endpoint == "iot.example.com"
        assert config.port == 8883
        assert config.topic_base == "csip_client"

    def test_missing_cert_file(self, tmp_path: Path) -> None:
        key = tmp_path / "key.pem"
        ca = tmp_path / "ca.pem"
        key.touch()
        ca.touch()

        with pytest.raises(ValueError, match="cert_path does not exist"):
            MQTTForwarderConfig(
                endpoint="iot.example.com",
                cert_path=tmp_path / "missing.pem",
                key_path=key,
                ca_path=ca,
            )

    def test_invalid_port(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        ca = tmp_path / "ca.pem"
        cert.touch()
        key.touch()
        ca.touch()

        with pytest.raises(ValueError, match="Port must be between"):
            MQTTForwarderConfig(
                endpoint="iot.example.com",
                port=0,
                cert_path=cert,
                key_path=key,
                ca_path=ca,
            )

    def test_disabled_config_skips_validation(self, tmp_path: Path) -> None:
        config = MQTTForwarderConfig(
            endpoint="iot.example.com",
            cert_path=tmp_path / "missing.pem",
            key_path=tmp_path / "missing.key",
            ca_path=tmp_path / "missing.ca",
            enabled=False,
        )
        assert config.enabled is False


class TestMQTTForwarder:
    def test_initialization(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config)
        assert forwarder.name == "mqtt"
        assert forwarder.running is False
        assert forwarder.connected is False
        assert "test_client" in forwarder._client_id

    def test_queue_message_when_not_running(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config)
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        forwarder.queue_message(frame)
        # Should not raise, just drop silently
        assert forwarder._capture_queue.empty()

    async def test_start_imports_aiomqtt(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config)

        with patch.dict("sys.modules", {"aiomqtt": None}), pytest.raises(ImportError):
            await forwarder.start()

    async def test_missing_aiomqtt_emits_diagnostic(
        self, mock_config: MQTTForwarderConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C10: missing aiomqtt surfaces as a UI error (not just a log line)."""
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)
        forwarder = MQTTForwarder(mock_config)

        with patch.dict("sys.modules", {"aiomqtt": None}), pytest.raises(ImportError):
            await forwarder.start()

        errors = fresh.snapshot()["errors"]
        assert len(errors) == 1
        assert "aiomqtt" in errors[0]["message"]
        assert errors[0]["source"] == "mqtt"

    async def test_queue_full_emits_deduped_backpressure_diagnostic(
        self, mock_config: MQTTForwarderConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B9: a saturated outbound queue surfaces as a single deduped warning."""
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)
        # Tiny queue so we trip the QueueFull path quickly.
        forwarder = MQTTForwarder(mock_config, queue_size=1)
        forwarder._running = True

        for _ in range(5):
            forwarder.queue_message(
                MessageFrame(
                    direction=MessageDirection.UPSTREAM,
                    message_type="DERStatus",
                    content={},
                )
            )

        warnings = fresh.snapshot()["warnings"]
        backpressure = [w for w in warnings if w["message"].startswith("MQTT queue full")]
        assert len(backpressure) == 1
        # Volume reflected in count, not in additional entries.
        assert backpressure[0].get("count", 1) >= 2

    async def test_connect_failure_emits_diagnostic(
        self, mock_config: MQTTForwarderConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C9: a broker connect failure surfaces as a deduped UI error."""
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)
        forwarder = MQTTForwarder(mock_config)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=ConnectionRefusedError("nope"))

        with (
            patch("py20305.forwarders.mqtt_forwarder.ssl.SSLContext"),
            patch("aiomqtt.Client", return_value=mock_client),
            pytest.raises(ConnectionRefusedError),
        ):
            await forwarder.start()

        errors = fresh.snapshot()["errors"]
        assert len(errors) == 1
        assert mock_config.endpoint in errors[0]["message"]
        assert errors[0]["source"] == "mqtt"
        assert errors[0]["details"]["endpoint"] == mock_config.endpoint
        assert errors[0]["details"]["port"] == mock_config.port

    async def test_start_and_stop(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config)

        # Mock aiomqtt
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.publish = AsyncMock()

        with (
            patch("py20305.forwarders.mqtt_forwarder.ssl.SSLContext"),
            patch("aiomqtt.Client", return_value=mock_client),
        ):
            await forwarder.start()
            assert forwarder.running is True
            assert forwarder.connected is True

            await forwarder.stop()
            assert forwarder.running is False
            assert forwarder.connected is False

    async def test_queue_and_publish(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.publish = AsyncMock()

        with (
            patch("py20305.forwarders.mqtt_forwarder.ssl.SSLContext"),
            patch("aiomqtt.Client", return_value=mock_client),
        ):
            await forwarder.start()

            frame = MessageFrame(
                direction=MessageDirection.UPSTREAM,
                message_type="TestMessage",
                content={"key": "value"},
            )
            forwarder.queue_message(frame)

            # Give publish loop time to process
            await asyncio.sleep(0.1)

            assert mock_client.publish.called
            call_args = mock_client.publish.call_args
            assert "test_topic/out/2030-5-raw" in str(call_args)

            await forwarder.stop()

    def test_get_statistics(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config)
        stats = forwarder.get_statistics()

        assert stats["name"] == "mqtt"
        assert stats["running"] is False
        assert stats["connected"] is False
        assert stats["queue_size"] == 0
        assert "test.iot.amazonaws.com:8883" in stats["broker"]
        assert "test_topic/out/2030-5-raw" in stats["topic"]

    def test_queue_full_drops_oldest(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config, queue_size=2)
        forwarder._running = True  # Simulate running state

        frame1 = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Msg1",
            content={},
        )
        frame2 = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Msg2",
            content={},
        )
        frame3 = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Msg3",
            content={},
        )

        forwarder.queue_message(frame1)
        forwarder.queue_message(frame2)
        forwarder.queue_message(frame3)

        assert forwarder._capture_queue.qsize() == 2
        stats = forwarder.get_statistics()
        assert stats.get("messages_dropped", 0) == 1

    def test_frame_to_dict(self, mock_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(mock_config)
        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="Response",
            content={"data": 123},
            timestamp=datetime(2026, 2, 3, 12, 0, 0, tzinfo=UTC),
            is_valid=True,
            http_method="GET",
            uri="/test",
            status_code=200,
            metadata={"extra": "info"},
        )

        result = forwarder._frame_to_dict(frame)

        assert result["direction"] == "downstream"
        assert result["message_type"] == "Response"
        assert result["content"] == {"data": 123}
        assert result["is_valid"] is True
        assert result["http_method"] == "GET"
        assert result["uri"] == "/test"
        assert result["status_code"] == 200


class TestBufferPolicyByKind:
    """What survives a slow broker, per payload kind.

    The three kinds want different policies. A captured exchange and an event
    are each a distinct record -- nothing will send them again -- so the
    capture buffer is lossless until it is itself full. A measurement is
    superseded by the next reading for the same device, so the telemetry
    buffer holds the newest frame per device and never costs a capture its
    place.
    """

    @staticmethod
    def _message(message_type: str) -> MessageFrame:
        return MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type=message_type,
            content={},
        )

    @staticmethod
    def _telemetry(device: str, watts: float) -> TelemetryFrame:
        return TelemetryFrame(
            device=device,
            points={"W": TelemetryPoint(value=watts, source_timestamp=100.0, quality="good")},
            quality="good",
            last_success=100.0,
        )

    @staticmethod
    async def _drain(forwarder: MQTTForwarder) -> list[tuple[str, dict]]:
        """Run the publish loop to exhaustion and return what it published.

        Drives the real loop rather than reading the buffers, so the assertion
        is about what reaches the broker rather than about internal shape.
        """
        forwarder._running = False
        await forwarder._publish_loop()
        return [
            (call.args[0], json.loads(call.args[1]))
            for call in forwarder._client.publish.call_args_list
        ]

    @staticmethod
    def _armed(config: MQTTForwarderConfig, **kwargs: int) -> MQTTForwarder:
        """A forwarder accepting frames, with a mock client and no live loop."""
        forwarder = MQTTForwarder(config, **kwargs)
        client = MagicMock()
        client.publish = AsyncMock()
        forwarder._client = client
        forwarder._running = True
        return forwarder

    async def test_telemetry_never_evicts_captured_traffic(
        self, mock_config: MQTTForwarderConfig
    ) -> None:
        """Issue #7: a telemetry flood must not cost a captured exchange."""
        forwarder = self._armed(mock_config, queue_size=3)

        for name in ("Msg1", "Msg2", "Msg3"):
            forwarder.queue_message(self._message(name))
        for index in range(5):
            forwarder.queue_telemetry(self._telemetry(f"device{index}", float(index)))

        published = await self._drain(forwarder)

        captured = [payload["message_type"] for topic, payload in published if "raw" in topic]
        assert captured == ["Msg1", "Msg2", "Msg3"]
        assert forwarder.get_statistics().get("messages_dropped", 0) == 0

    def test_the_buffer_counters_start_at_explicit_zeros(
        self, mock_config: MQTTForwarderConfig
    ) -> None:
        """What backpressure cost is readable before anything has cost it.

        A consumer must not have to tell a zero from a key that is absent
        until its first event.
        """
        stats = MQTTForwarder(mock_config).get_statistics()

        assert stats["messages_dropped"] == 0
        assert stats["telemetry_queued"] == 0
        assert stats["telemetry_superseded"] == 0
        assert stats["telemetry_dropped"] == 0
        assert stats["capture_queue_size"] == 0
        assert stats["telemetry_pending"] == 0

    async def test_an_event_is_never_evicted_by_telemetry(
        self, mock_config: MQTTForwarderConfig
    ) -> None:
        """An OCSF event carries a reason no later frame restates."""
        forwarder = self._armed(mock_config, queue_size=1)
        forwarder.queue_event(
            EventFrame(payload={"reason": "cert_expired"}, topic_suffix="out/ocsf")
        )

        for index in range(5):
            forwarder.queue_telemetry(self._telemetry(f"device{index}", float(index)))

        published = await self._drain(forwarder)

        assert ("test_topic/out/ocsf", {"reason": "cert_expired"}) in published

    async def test_a_second_frame_for_a_device_supersedes_the_first(
        self, mock_config: MQTTForwarderConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The newest reading is what a monitoring upstream wants, so no warning."""
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)
        forwarder = self._armed(mock_config)

        forwarder.queue_telemetry(self._telemetry("device0", 100.0))
        forwarder.queue_telemetry(self._telemetry("device0", 200.0))

        published = await self._drain(forwarder)

        assert len(published) == 1
        assert published[0][1]["points"]["W"]["value"] == 200.0

        stats = forwarder.get_statistics()
        assert stats["telemetry_queued"] == 1
        assert stats["telemetry_superseded"] == 1
        assert stats.get("messages_dropped", 0) == 0
        assert fresh.snapshot()["warnings"] == []

    async def test_one_device_never_supersedes_another(
        self, mock_config: MQTTForwarderConfig
    ) -> None:
        """Coalescing is per device: a chatty device costs a quiet one nothing."""
        forwarder = self._armed(mock_config)

        forwarder.queue_telemetry(self._telemetry("quiet", 1.0))
        for watts in (10.0, 20.0, 30.0):
            forwarder.queue_telemetry(self._telemetry("chatty", watts))

        published = await self._drain(forwarder)

        assert [
            (payload["device"], payload["points"]["W"]["value"]) for _, payload in published
        ] == [
            ("quiet", 1.0),
            ("chatty", 30.0),
        ]

    async def test_bounded_runs_give_capture_priority_without_starving_telemetry(
        self, mock_config: MQTTForwarderConfig
    ) -> None:
        """Both bounds, and where each hands over.

        Enough of each kind to exceed both limits, so the assertion pins the
        interleaving rather than only the fact that capture goes first: drop
        the capture bound and the first run swallows everything, drop the
        telemetry slice and the second run never happens.
        """
        capture_count = _CAPTURE_RUN + 4
        device_count = _TELEMETRY_SLICE + 2
        forwarder = self._armed(mock_config, queue_size=capture_count)

        for index in range(device_count):
            forwarder.queue_telemetry(self._telemetry(f"device{index}", float(index)))
        for index in range(capture_count):
            forwarder.queue_message(self._message(f"Msg{index}"))

        published = await self._drain(forwarder)

        kinds = (
            "telemetry" if topic.endswith("/telemetry") else "capture" for topic, _ in published
        )
        runs = [(kind, len(list(group))) for kind, group in groupby(kinds)]
        assert runs == [
            ("capture", _CAPTURE_RUN),
            ("telemetry", _TELEMETRY_SLICE),
            ("capture", capture_count - _CAPTURE_RUN),
            ("telemetry", device_count - _TELEMETRY_SLICE),
        ]

    async def test_pending_telemetry_is_published_on_shutdown(
        self, mock_config: MQTTForwarderConfig
    ) -> None:
        """The drain on stop covers both buffers, not just capture."""
        forwarder = MQTTForwarder(mock_config)

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.publish = AsyncMock()

        with (
            patch("py20305.forwarders.mqtt_forwarder.ssl.SSLContext"),
            patch("aiomqtt.Client", return_value=client),
        ):
            await forwarder.start()
            forwarder.queue_telemetry(self._telemetry("device0", 42.0))
            await forwarder.stop()

        topics = [call.args[0] for call in client.publish.call_args_list]
        assert topics == ["test_topic/out/telemetry"]

    async def test_a_new_device_at_the_limit_is_dropped_and_reported(
        self, mock_config: MQTTForwarderConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The map is keyed by device, so its bound is a device count."""
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)
        forwarder = self._armed(mock_config, telemetry_device_limit=2)

        for index in range(4):
            forwarder.queue_telemetry(self._telemetry(f"device{index}", float(index)))
        # A device already pending needs no slot and is still accepted.
        forwarder.queue_telemetry(self._telemetry("device0", 99.0))

        stats = forwarder.get_statistics()
        assert stats["telemetry_pending"] == 2
        assert stats["telemetry_dropped"] == 2
        assert stats["telemetry_superseded"] == 1

        warnings = fresh.snapshot()["warnings"]
        assert len(warnings) == 1
        assert warnings[0]["message"].startswith("MQTT telemetry buffer at its device limit")
        assert warnings[0]["details"]["device_limit"] == 2


class TestMQTTForwarderPlainMQTT:
    """Tests for plain MQTT (no TLS) connections."""

    def test_initialization_plain(self, plain_mqtt_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(plain_mqtt_config)
        assert forwarder.name == "mqtt"
        assert forwarder.connected is False
        stats = forwarder.get_statistics()
        assert "rabbitmq:1883" in stats["broker"]

    def test_create_ssl_context_returns_none(self, plain_mqtt_config: MQTTForwarderConfig) -> None:
        forwarder = MQTTForwarder(plain_mqtt_config)
        assert forwarder._create_ssl_context() is None

    async def test_start_plain_mqtt_no_ssl(self, plain_mqtt_config: MQTTForwarderConfig) -> None:
        """start() passes tls_context=None to aiomqtt.Client for plain MQTT."""
        forwarder = MQTTForwarder(plain_mqtt_config)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.publish = AsyncMock()

        with patch("aiomqtt.Client", return_value=mock_client) as mock_client_cls:
            await forwarder.start()

            # Verify no SSL context was passed
            call_kwargs = mock_client_cls.call_args.kwargs
            assert call_kwargs["tls_context"] is None
            assert call_kwargs["hostname"] == "rabbitmq"
            assert call_kwargs["port"] == 1883

            assert forwarder.running is True
            assert forwarder.connected is True

            await forwarder.stop()

    async def test_start_tls_mqtt_has_ssl(self, mock_config: MQTTForwarderConfig) -> None:
        """start() passes an SSLContext to aiomqtt.Client for mTLS config."""
        forwarder = MQTTForwarder(mock_config)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.publish = AsyncMock()

        with (
            patch("py20305.forwarders.mqtt_forwarder.ssl.SSLContext") as mock_ssl,
            patch("aiomqtt.Client", return_value=mock_client) as mock_client_cls,
        ):
            await forwarder.start()

            call_kwargs = mock_client_cls.call_args.kwargs
            assert call_kwargs["tls_context"] is not None
            assert mock_ssl.called

            await forwarder.stop()
