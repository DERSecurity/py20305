"""Tests for MQTT forwarder."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305 import diagnostics
from py20305.diagnostics import DiagnosticsStore
from py20305.forwarders.base import MessageDirection, MessageFrame
from py20305.forwarders.config import MQTTForwarderConfig
from py20305.forwarders.mqtt_forwarder import MQTTForwarder


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
        assert forwarder._queue.empty()

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

        assert forwarder._queue.qsize() == 2
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
