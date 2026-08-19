"""Tests for the message format the forwarder publishes.

Pins the serialization contract stated in
``py20305.forwarders.types``: which keys are always present, which
appear only when set, and how the loosely-typed constructor arguments are
normalized.
"""

from py20305.forwarders.types import (
    _VERSION,
    NetworkEndpoint,
    PayloadEnvelope,
    Protocol,
    ProtocolMessage,
    ProtocolMetadata,
    WireDirection,
)

# The envelope's `version` is the package version and moves with every
# release, so it is imported rather than written as a literal: a literal would
# assert nothing about the contract and would fail on each bump. What the value
# has to *look* like is pinned in test_extraction_regressions.py.
COMMON_VERSION = _VERSION


class TestProtocol:
    def test_ieee_2030_5_value(self) -> None:
        assert Protocol.IEEE_2030_5.value == "2030.5"

    def test_from_string(self) -> None:
        assert Protocol.from_string("2030.5") == Protocol.IEEE_2030_5


class TestWireDirection:
    def test_upstream_value(self) -> None:
        assert WireDirection.UPSTREAM.value == "upstream"

    def test_downstream_value(self) -> None:
        assert WireDirection.DOWNSTREAM.value == "downstream"


class TestNetworkEndpoint:
    def test_construction(self) -> None:
        endpoint = NetworkEndpoint(ip="192.168.1.1", port=8443)
        assert endpoint.ip == "192.168.1.1"
        assert endpoint.port == 8443

    def test_to_dict(self) -> None:
        endpoint = NetworkEndpoint(ip="10.0.0.1", port=443)
        result = endpoint.to_dict()
        assert result == {"ip": "10.0.0.1", "port": 443}

    def test_from_dict(self) -> None:
        data = {"ip": "10.0.0.1", "port": 443}
        endpoint = NetworkEndpoint.from_dict(data)
        assert endpoint.ip == "10.0.0.1"
        assert endpoint.port == 443

    def test_port_string_normalization(self) -> None:
        # NetworkEndpoint accepts string ports and normalizes to int
        endpoint = NetworkEndpoint(ip="10.0.0.1", port="8443")  # type: ignore[arg-type]
        assert endpoint.port == 8443


class TestProtocolMetadata:
    def test_construction_with_ieee_2030_5_fields(self) -> None:
        metadata = ProtocolMetadata(
            lfdi="abc123def456",
            message_type="DERControl",
            http_method="GET",
            uri="/edev/0/derp/0/derc",
        )
        assert metadata.lfdi == "abc123def456"
        assert metadata.message_type == "DERControl"
        assert metadata.http_method == "GET"
        assert metadata.uri == "/edev/0/derp/0/derc"

    def test_construction_with_defaults(self) -> None:
        metadata = ProtocolMetadata()
        assert metadata.lfdi is None
        assert metadata.message_type is None
        assert metadata.http_method is None
        assert metadata.uri is None

    def test_to_dict_excludes_none_values(self) -> None:
        metadata = ProtocolMetadata(
            lfdi="abc123",
            message_type="Test",
        )
        result = metadata.to_dict()
        assert result == {"lfdi": "abc123", "message_type": "Test"}
        assert "http_method" not in result
        assert "uri" not in result

    def test_to_dict_with_all_ieee_2030_5_fields(self) -> None:
        metadata = ProtocolMetadata(
            lfdi="abc",
            message_type="DER",
            http_method="POST",
            uri="/path",
        )
        result = metadata.to_dict()
        assert result == {
            "lfdi": "abc",
            "message_type": "DER",
            "http_method": "POST",
            "uri": "/path",
        }

    def test_from_dict(self) -> None:
        data = {"lfdi": "abc", "message_type": "Test", "uri": "/path"}
        metadata = ProtocolMetadata.from_dict(data)
        assert metadata.lfdi == "abc"
        assert metadata.message_type == "Test"
        assert metadata.uri == "/path"


class TestPayloadEnvelope:
    def test_from_dict(self) -> None:
        envelope = PayloadEnvelope.from_dict({"key": "value"})
        assert envelope.content_type == "application/json"
        assert envelope.encoding == "utf-8"
        assert envelope.is_json

    def test_from_xml(self) -> None:
        envelope = PayloadEnvelope.from_xml("<root><item>test</item></root>")
        assert envelope.content_type == "application/xml"
        assert envelope.is_xml

    def test_from_binary(self) -> None:
        envelope = PayloadEnvelope.from_binary(b"\x00\x01\xff")
        assert envelope.content_type == "application/octet-stream"
        assert envelope.encoding == "base64"
        assert envelope.is_binary

    def test_from_text(self) -> None:
        envelope = PayloadEnvelope.from_text("hello world")
        assert envelope.content_type == "text/plain"
        assert envelope.is_text

    def test_infer_from_string_json(self) -> None:
        envelope = PayloadEnvelope.infer_from_string('{"key": "value"}')
        assert envelope.is_json

    def test_infer_from_string_xml(self) -> None:
        envelope = PayloadEnvelope.infer_from_string("<root></root>")
        assert envelope.is_xml

    def test_serialize_deserialize(self) -> None:
        original = PayloadEnvelope.from_dict({"test": 123})
        serialized = original.serialize()
        restored = PayloadEnvelope.deserialize(serialized)
        assert restored.content_type == original.content_type
        assert restored.data == original.data
        assert restored.encoding == original.encoding


class TestProtocolMessage:
    def test_minimal_construction(self) -> None:
        msg = ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=WireDirection.UPSTREAM,
            client_id="device123",
            payload={"test": "data"},
            source=NetworkEndpoint(ip="192.168.1.1", port=8080),
            destination=NetworkEndpoint(ip="10.0.0.1", port=8443),
            protocol_data=ProtocolMetadata(message_type="Test"),
        )
        assert msg.protocol == Protocol.IEEE_2030_5
        assert msg.direction == WireDirection.UPSTREAM
        assert msg.client_id == "device123"
        assert msg.is_valid is True
        assert msg.validation_error is None

    def test_full_construction(self) -> None:
        ts = "2026-02-03T12:00:00+00:00"
        msg = ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=WireDirection.DOWNSTREAM,
            client_id="device456",
            payload={"key": "value"},
            source=NetworkEndpoint(ip="1.2.3.4", port=443),
            destination=NetworkEndpoint(ip="5.6.7.8", port=8443),
            protocol_data=ProtocolMetadata(
                lfdi="abcdef1234567890",
                message_type="EndDeviceList",
                http_method="GET",
                uri="/edev",
            ),
            timestamp=ts,
            is_valid=False,
            validation_error="Schema mismatch",
        )
        assert msg.timestamp == ts
        assert msg.is_valid is False
        assert msg.validation_error == "Schema mismatch"

    def test_to_dict_basic(self) -> None:
        msg = ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=WireDirection.UPSTREAM,
            client_id="device",
            payload={"data": 123},
            source=NetworkEndpoint(ip="1.1.1.1", port=80),
            destination=NetworkEndpoint(ip="2.2.2.2", port=443),
            protocol_data=ProtocolMetadata(message_type="Test"),
            timestamp="2026-02-03T12:00:00+00:00",
        )
        result = msg.to_dict()

        assert result["protocol"] == "2030.5"
        assert result["version"] == COMMON_VERSION
        assert result["direction"] == "upstream"
        assert result["client_id"] == "device"
        assert result["source"] == {"ip": "1.1.1.1", "port": 80}
        assert result["destination"] == {"ip": "2.2.2.2", "port": 443}
        assert result["protocol_data"] == {"message_type": "Test"}
        assert result["is_valid"] is True
        assert "hash" in result  # Auto-generated hash
        # Payload is wrapped in PayloadEnvelope format
        assert "payload" in result

    def test_to_dict_with_validation_error(self) -> None:
        msg = ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=WireDirection.DOWNSTREAM,
            client_id="dev",
            payload={},
            source=NetworkEndpoint(ip="0.0.0.0", port=0),
            destination=NetworkEndpoint(ip="0.0.0.0", port=0),
            protocol_data=ProtocolMetadata(),
            is_valid=False,
            validation_error="Invalid payload",
        )
        result = msg.to_dict()
        assert result["is_valid"] is False
        assert result["validation_error"] == "Invalid payload"

    def test_payload_normalization_to_envelope(self) -> None:
        """ProtocolMessage normalizes dict payloads to PayloadEnvelope."""
        msg = ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=WireDirection.UPSTREAM,
            client_id="device",
            payload={"key": "value"},
            source=NetworkEndpoint(ip="1.1.1.1", port=80),
        )
        # After __post_init__, payload should be a PayloadEnvelope
        assert isinstance(msg.payload, PayloadEnvelope)
        assert msg.payload.is_json

    def test_payload_with_envelope(self) -> None:
        """ProtocolMessage accepts PayloadEnvelope directly."""
        envelope = PayloadEnvelope.from_dict({"test": "data"})
        msg = ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=WireDirection.UPSTREAM,
            client_id="device",
            payload=envelope,
            source=NetworkEndpoint(ip="1.1.1.1", port=80),
        )
        assert msg.payload is envelope

    def test_from_dict_roundtrip(self) -> None:
        """Test serialization and deserialization roundtrip."""
        original = ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=WireDirection.UPSTREAM,
            client_id="device123",
            payload={"test": "data"},
            source=NetworkEndpoint(ip="192.168.1.1", port=8080),
            destination=NetworkEndpoint(ip="10.0.0.1", port=8443),
            protocol_data=ProtocolMetadata(message_type="Test", lfdi="abc123"),
        )

        # Serialize and deserialize
        data = original.to_dict()
        restored = ProtocolMessage.from_dict(data)

        assert restored.protocol == original.protocol
        assert restored.direction == original.direction
        assert restored.client_id == original.client_id
        assert restored.source.ip == original.source.ip
        assert restored.source.port == original.source.port
