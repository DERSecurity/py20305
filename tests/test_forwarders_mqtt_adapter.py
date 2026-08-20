"""Tests for MQTT forwarder adapter."""
# mypy: disable-error-code="method-assign"

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
from py20305.forwarders.mqtt_adapter import MQTTForwarderAdapter
from py20305.forwarders.mqtt_forwarder import MQTTForwarder
from py20305.forwarders.types import _VERSION, ProtocolMessage, WireDirection

# The envelope's `version` moves with every release, so it is imported rather
# than written as a literal: a literal would assert nothing and would fail on
# each bump. It is a *validated* field rather than free text -- consumers
# constrain its value, not just its presence -- and what it has to look like is
# pinned in test_extraction_regressions.py.
COMMON_VERSION = _VERSION


@pytest.fixture
def mock_mqtt_forwarder(tmp_path: Path) -> MQTTForwarder:
    """Create a mock MQTT forwarder."""
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    ca = tmp_path / "ca.pem"
    cert.touch()
    key.touch()
    ca.touch()

    config = MQTTForwarderConfig(
        endpoint="test.iot.example.com",
        cert_path=cert,
        key_path=key,
        ca_path=ca,
    )
    forwarder = MQTTForwarder(config)
    forwarder.queue_message = MagicMock()
    return forwarder


class TestMQTTForwarderAdapter:
    def test_initialization(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        assert adapter.name == "mqtt-adapter"
        assert adapter.running is False
        assert adapter.client_lfdi is None

    def test_client_lfdi_property(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder, client_lfdi="initial")
        assert adapter.client_lfdi == "initial"

        adapter.client_lfdi = "updated"
        assert adapter.client_lfdi == "updated"

    async def test_start_and_stop(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        mock_mqtt_forwarder.start = MagicMock()
        mock_mqtt_forwarder.stop = MagicMock()

        # Make start/stop async
        async def async_start() -> None:
            pass

        async def async_stop() -> None:
            pass

        mock_mqtt_forwarder.start = async_start
        mock_mqtt_forwarder.stop = async_stop
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        await adapter.start()
        assert adapter.running is True

        await adapter.stop()
        assert adapter.running is False

    def test_queue_message_when_not_running(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        adapter.queue_message(frame)
        mock_mqtt_forwarder.queue_message.assert_not_called()

    def test_queue_valid_message(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERControl",
            content={"mode": "active"},
            is_valid=True,
        )
        adapter.queue_message(frame)

        mock_mqtt_forwarder.queue_message.assert_called_once()
        queued_frame = mock_mqtt_forwarder.queue_message.call_args[0][0]
        assert isinstance(queued_frame, MessageFrame)
        assert isinstance(queued_frame.content, dict)

    def test_forwards_invalid_messages_for_security_inspection(
        self, mock_mqtt_forwarder: MQTTForwarder
    ) -> None:
        """Invalid messages are always forwarded for deep packet inspection."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="Response",
            content={},
            is_valid=False,
            validation_error="Schema mismatch",
        )
        adapter.queue_message(frame)

        mock_mqtt_forwarder.queue_message.assert_called_once()

    def test_forwards_invalid_with_validation_error_preserved(
        self,
        mock_mqtt_forwarder: MQTTForwarder,
    ) -> None:
        """Invalid messages include validation_error in forwarded content."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="Response",
            content={},
            is_valid=False,
            validation_error="Missing required element",
        )
        adapter.queue_message(frame)

        mock_mqtt_forwarder.queue_message.assert_called_once()
        forwarded = mock_mqtt_forwarder.queue_message.call_args[0][0]
        assert forwarded.content["is_valid"] is False
        assert forwarded.content["validation_error"] == "Missing required element"


class TestProtocolMessageConversion:
    def test_upstream_direction_mapping(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERStatus",
            content={},
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        assert queued.content["direction"] == "upstream"

    def test_downstream_direction_mapping(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="EndDeviceList",
            content={},
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        assert queued.content["direction"] == "downstream"

    def test_protocol_message_fields(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        ts = datetime(2026, 2, 3, 12, 0, 0, tzinfo=UTC)
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERControl",
            content={"power": 1000},
            timestamp=ts,
            is_valid=True,
            http_method="POST",
            uri="/edev/0/derp/0/derc",
            server_host="iot.example.com",
            server_port=8443,
            metadata={"lfdi": "device123"},
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        msg = queued.content

        assert msg["protocol"] == "2030.5"
        assert msg["version"] == COMMON_VERSION
        assert msg["client_id"] == "device123"
        assert msg["is_valid"] is True
        assert msg["protocol_data"]["message_type"] == "DERControl"
        assert msg["protocol_data"]["http_method"] == "POST"
        assert msg["protocol_data"]["uri"] == "/edev/0/derp/0/derc"

    def test_lfdi_extraction_from_metadata(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
            metadata={"lfdi": "metadata_lfdi_value"},
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        assert queued.content["client_id"] == "metadata_lfdi_value"
        assert queued.content["protocol_data"]["lfdi"] == "metadata_lfdi_value"

    def test_lfdi_extraction_from_payload(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={"mRID": "payload_mrid"},
            metadata={},
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        assert queued.content["client_id"] == "payload_mrid"

    def test_client_lfdi_fallback(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(
            mock_mqtt_forwarder,
            client_lfdi="aggregator_fallback",
        )
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
            metadata={},
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        assert queued.content["protocol_data"]["lfdi"] == "aggregator_fallback"

    def test_conversion_failure_emits_diagnostic(
        self,
        mock_mqtt_forwarder: MQTTForwarder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """U15: a ProtocolMessage conversion failure surfaces as a UI warning."""
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True
        adapter._convert_to_protocol_message = MagicMock(side_effect=RuntimeError("nope"))

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERStatus",
            content={},
        )
        adapter.queue_message(frame)

        # Adapter should have swallowed the error and not enqueued anything.
        assert mock_mqtt_forwarder.queue_message.call_count == 0
        warnings = fresh.snapshot()["warnings"]
        assert len(warnings) == 1
        assert "DERStatus" in warnings[0]["message"]
        assert warnings[0]["source"] == "mqtt"
        assert warnings[0]["details"]["message_type"] == "DERStatus"


class TestNetworkEndpoints:
    def test_upstream_endpoints(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Request",
            content={},
            server_host="server.example.com",
            server_port=8443,
            metadata={
                "client_ip": "192.168.1.100",
                "client_port": 54321,
            },
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        msg = queued.content

        # For upstream: source=client, destination=server
        assert msg["source"]["ip"] == "192.168.1.100"
        assert msg["source"]["port"] == 54321
        assert msg["destination"]["ip"] == "server.example.com"
        assert msg["destination"]["port"] == 8443

    def test_downstream_endpoints(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="Response",
            content={},
            server_host="server.example.com",
            server_port=8443,
            metadata={
                "client_ip": "192.168.1.100",
                "client_port": 54321,
            },
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        msg = queued.content

        # For downstream: source=server, destination=client
        assert msg["source"]["ip"] == "server.example.com"
        assert msg["source"]["port"] == 8443
        assert msg["destination"]["ip"] == "192.168.1.100"
        assert msg["destination"]["port"] == 54321

    def test_default_endpoints_when_missing(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
        )
        adapter.queue_message(frame)

        queued = mock_mqtt_forwarder.queue_message.call_args[0][0]
        msg = queued.content

        assert msg["source"]["ip"] == "0.0.0.0"
        assert msg["source"]["port"] == 0
        assert msg["destination"]["ip"] == "0.0.0.0"
        assert msg["destination"]["port"] == 0


class TestStatistics:
    def test_get_statistics(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(
            mock_mqtt_forwarder,
            client_lfdi="test_agg",
        )
        adapter._running = True

        # Queue some messages
        valid_frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Valid",
            content={},
            is_valid=True,
        )
        invalid_frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Invalid",
            content={},
            is_valid=False,
        )

        adapter.queue_message(valid_frame)
        adapter.queue_message(invalid_frame)

        stats = adapter.get_statistics()

        assert stats["messages_queued"] == 2  # both valid and invalid forwarded
        assert stats["client_lfdi"] == "test_agg"
        assert "underlying_forwarder" in stats

    def test_queue_event_counts_on_the_adapter_itself(
        self, mock_mqtt_forwarder: MQTTForwarder
    ) -> None:
        """The adapter is what the manager registers, so its own stats are
        what an operator sees; counting only on the wrapped forwarder would
        bury the number under ``underlying_forwarder``."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True
        mock_mqtt_forwarder.queue_event = MagicMock()

        event = EventFrame(payload={"protocol": "modbus"}, topic_suffix="out/device")
        adapter.queue_event(event)
        adapter.queue_event(event)

        assert adapter.get_statistics()["events_queued"] == 2
        assert mock_mqtt_forwarder.queue_event.call_count == 2

    def test_events_queued_reports_zero_before_any_event(
        self, mock_mqtt_forwarder: MQTTForwarder
    ) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        assert adapter.get_statistics()["events_queued"] == 0


class TestWireFormatRoundTrip:
    """Verify what the adapter publishes is what the wire format declares.

    Catches the mismatches a consumer would hit on deserialization -- a
    double-wrapped payload, a missing required key -- at the point they are
    introduced rather than at the far end of an MQTT topic.
    """

    def _get_published_v2_dict(
        self,
        mock_mqtt_forwarder: MQTTForwarder,
        frame: MessageFrame,
        **adapter_kwargs: object,
    ) -> dict[str, Any]:
        """Run frame through adapter and return the v2 dict that would be published."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder, **adapter_kwargs)
        adapter._running = True
        adapter.queue_message(frame)
        queued_frame = mock_mqtt_forwarder.queue_message.call_args[0][0]
        # Simulate what MQTTForwarder._publish_loop does with the message_converter
        converter = mock_mqtt_forwarder._message_converter
        if converter:
            result: dict[str, Any] = converter(queued_frame)
            return result
        content: dict[str, Any] = queued_frame.content
        return content

    def test_from_dict_succeeds_for_upstream(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """ProtocolMessage.from_dict() must parse an upstream adapter payload without error."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERStatus",
            content={"opModFixedW": 5000},
            timestamp=datetime(2026, 3, 17, 12, 0, 0, tzinfo=UTC),
            is_valid=True,
            http_method="PUT",
            uri="/edev/1/ders",
            server_host="10.0.0.1",
            server_port=8443,
            metadata={"lfdi": "AABBCCDD11223344", "client_ip": "192.168.1.10", "client_port": 5000},
        )

        v2_dict = self._get_published_v2_dict(mock_mqtt_forwarder, frame)

        parsed = ProtocolMessage.from_dict(v2_dict)

        assert parsed.direction.value == "upstream"
        assert parsed.protocol.value == "2030.5"
        assert parsed.client_id == "AABBCCDD11223344"
        assert parsed.source.ip == "192.168.1.10"
        assert parsed.source.port == 5000
        assert parsed.is_valid is True
        assert parsed.protocol_data.message_type == "DERStatus"
        assert parsed.protocol_data.uri == "/edev/1/ders"

    def test_from_dict_succeeds_for_downstream(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """ProtocolMessage.from_dict() must parse a downstream adapter payload without error."""
        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="DERControlList",
            content={"DERControl": [{"mRID": "0F5CFC7812035770"}]},
            timestamp=datetime(2026, 3, 17, 12, 0, 0, tzinfo=UTC),
            is_valid=True,
            http_method="GET",
            uri="/edev/1/fsa/1/derp/1/derc",
            server_host="localhost",
            server_port=8443,
            metadata={},
        )

        v2_dict = self._get_published_v2_dict(mock_mqtt_forwarder, frame)

        parsed = ProtocolMessage.from_dict(v2_dict)

        assert parsed.direction.value == "downstream"
        assert parsed.source.ip == "localhost"
        assert parsed.source.port == 8443
        assert parsed.protocol_data.message_type == "DERControlList"

    def test_from_dict_succeeds_with_validation_error(
        self,
        mock_mqtt_forwarder: MQTTForwarder,
    ) -> None:
        """ProtocolMessage.from_dict() must handle invalid messages with validation errors."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERSettings",
            content={"bad": "data"},
            is_valid=False,
            validation_error="Schema validation failed: missing required field 'setGradW'",
            metadata={"lfdi": "device_abc"},
        )

        v2_dict = self._get_published_v2_dict(mock_mqtt_forwarder, frame)

        parsed = ProtocolMessage.from_dict(v2_dict)

        assert parsed.is_valid is False
        assert "setGradW" in parsed.validation_error

    def test_from_dict_succeeds_with_client_lfdi_fallback(
        self, mock_mqtt_forwarder: MQTTForwarder
    ) -> None:
        """ProtocolMessage.from_dict() must work when LFDI comes from aggregator fallback."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="MirrorUsagePoint",
            content={},
            metadata={},
        )

        v2_dict = self._get_published_v2_dict(
            mock_mqtt_forwarder, frame, client_lfdi="agg_fallback_lfdi"
        )

        parsed = ProtocolMessage.from_dict(v2_dict)

        assert parsed.client_id == "agg_fallback_lfdi"
        assert parsed.protocol_data.lfdi == "agg_fallback_lfdi"

    def test_v2_dict_has_version_key(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """The published dict must have 'version' at top level so from_dict detects v2 format."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
            metadata={"lfdi": "dev1"},
        )

        v2_dict = self._get_published_v2_dict(mock_mqtt_forwarder, frame)

        # Presence is what from_dict() dispatches on; the value rides along.
        assert "version" in v2_dict
        assert v2_dict["version"] == COMMON_VERSION
        assert "source" in v2_dict
        assert "protocol" in v2_dict


class TestForwarderIdAndSourceHost:
    """Tests for forwarder_id injection and source_host in endpoints."""

    def _publish_frame(
        self,
        mock_mqtt_forwarder: MQTTForwarder,
        frame: MessageFrame,
        forwarder_id: str | None = None,
        source_host: str | None = None,
    ) -> dict[str, Any]:
        """Run frame through adapter with forwarder_id/source_host set."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True
        if forwarder_id is not None:
            adapter.forwarder_id = forwarder_id
        if source_host is not None:
            adapter.source_host = source_host
        adapter.queue_message(frame)
        queued_frame = mock_mqtt_forwarder.queue_message.call_args[0][0]
        converter = mock_mqtt_forwarder._message_converter
        result: dict[str, Any] = converter(queued_frame)
        return result

    def test_forwarder_id_injected(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
            metadata={"lfdi": "dev1"},
        )
        v2_dict = self._publish_frame(mock_mqtt_forwarder, frame, forwarder_id="site-alpha-agg-01")
        assert v2_dict["forwarder_id"] == "site-alpha-agg-01"

    def test_forwarder_id_empty_when_not_set(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """Without forwarder_id configured, it defaults to empty string."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
            metadata={"lfdi": "dev1"},
        )
        v2_dict = self._publish_frame(mock_mqtt_forwarder, frame)
        assert v2_dict["forwarder_id"] == ""

    def test_source_host_used_as_client_ip(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """source_host should appear as the client IP in the source endpoint."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERStatus",
            content={},
            metadata={"lfdi": "dev1"},
            server_host="test-server",
            server_port=8443,
        )
        v2_dict = self._publish_frame(mock_mqtt_forwarder, frame, source_host="aggregator")
        # UPSTREAM: source=client, destination=server
        assert v2_dict["source"]["ip"] == "aggregator"
        assert v2_dict["destination"]["ip"] == "test-server"

    def test_source_host_in_downstream(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """For DOWNSTREAM, source=server and destination=client (aggregator)."""
        frame = MessageFrame(
            direction=MessageDirection.DOWNSTREAM,
            message_type="DERControlList",
            content={},
            metadata={"lfdi": "dev1"},
            server_host="test-server",
            server_port=8443,
        )
        v2_dict = self._publish_frame(mock_mqtt_forwarder, frame, source_host="aggregator")
        # DOWNSTREAM: source=server, destination=client
        assert v2_dict["source"]["ip"] == "test-server"
        assert v2_dict["destination"]["ip"] == "aggregator"

    def test_source_host_fallback_to_0_0_0_0(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """Without source_host, client IP falls back to 0.0.0.0."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="Test",
            content={},
            metadata={"lfdi": "dev1"},
        )
        v2_dict = self._publish_frame(mock_mqtt_forwarder, frame)
        assert v2_dict["source"]["ip"] == "0.0.0.0"

    def test_forwarder_id_and_source_host_together(
        self, mock_mqtt_forwarder: MQTTForwarder
    ) -> None:
        """Both forwarder_id and source_host set correctly in same message."""
        frame = MessageFrame(
            direction=MessageDirection.UPSTREAM,
            message_type="DERStatus",
            content={},
            metadata={"lfdi": "dev1"},
            server_host="test-server",
            server_port=8443,
        )
        v2_dict = self._publish_frame(
            mock_mqtt_forwarder,
            frame,
            forwarder_id="site-alpha-agg-01",
            source_host="aggregator",
        )
        assert v2_dict["forwarder_id"] == "site-alpha-agg-01"
        assert v2_dict["source"]["ip"] == "aggregator"
        assert v2_dict["destination"]["ip"] == "test-server"


class TestTelemetryConversion:
    """Measured device state, wrapped in the same envelope as everything else.

    The adapter is what turns each payload kind into the wire form. Telemetry
    rides the envelope rather than defining its own, so a consumer parses one
    shape regardless of which subsystem produced the payload.
    """

    @staticmethod
    def _frame() -> TelemetryFrame:
        return TelemetryFrame(
            device="ab" * 20,
            points={
                "W": TelemetryPoint(value=4200, source_timestamp=100.0, quality="good"),
                "Hz": TelemetryPoint(
                    value=60.0, source_timestamp=100.0, quality="good", protocol_quality=0
                ),
            },
            quality="good",
            last_success=100.0,
        )

    def test_a_frame_becomes_a_protocol_message(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder, client_lfdi="cd" * 20)

        message = adapter._convert_telemetry(self._frame())

        assert isinstance(message, ProtocolMessage)
        assert message.to_dict()["version"] == COMMON_VERSION

    def test_the_device_identifies_the_reading(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """The client id is the device the values came from, not the aggregator:
        a consumer correlating readings needs to know which DER produced them."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder, client_lfdi="cd" * 20)

        message = adapter._convert_telemetry(self._frame())

        assert message.client_id == "ab" * 20

    def test_telemetry_is_always_upstream(self, mock_mqtt_forwarder: MQTTForwarder) -> None:
        """Measurements only ever flow outward. There is no downstream case to
        represent, so the direction is not derived from anything."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)

        message = adapter._convert_telemetry(self._frame())

        assert message.direction == WireDirection.UPSTREAM

    def test_the_measurements_survive_the_envelope(
        self, mock_mqtt_forwarder: MQTTForwarder
    ) -> None:
        """What a consumer actually reads: the points, their values, and the
        time the device was read rather than the time this was published."""
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)

        message = adapter._convert_telemetry(self._frame())
        payload = json.loads(message.payload.data)

        assert payload["device"] == "ab" * 20
        assert payload["points"]["W"]["value"] == 4200
        assert payload["points"]["W"]["source_timestamp"] == 100.0
        assert payload["quality"] == "good"

    def test_queueing_telemetry_reaches_the_forwarder(
        self, mock_mqtt_forwarder: MQTTForwarder
    ) -> None:
        mock_mqtt_forwarder.queue_telemetry = MagicMock()
        adapter = MQTTForwarderAdapter(mock_mqtt_forwarder)
        adapter._running = True

        adapter.queue_telemetry(self._frame())

        mock_mqtt_forwarder.queue_telemetry.assert_called_once()
