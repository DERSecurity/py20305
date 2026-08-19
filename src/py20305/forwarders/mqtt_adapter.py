"""MQTT forwarder adapter for ProtocolMessage v2.0 format.

This module converts MessageFrame objects to the ProtocolMessage format
expected by a security monitoring system ingesting IEEE 2030.5 traffic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from py20305.forwarders.base import (
    AbstractForwarder,
    EventFrame,
    MessageDirection,
    MessageFrame,
)
from py20305.forwarders.lfdi_extraction import extract_client_id, extract_lfdi
from py20305.forwarders.types import (
    NetworkEndpoint,
    PayloadEnvelope,
    Protocol,
    ProtocolMessage,
    ProtocolMetadata,
    WireDirection,
)

if TYPE_CHECKING:
    from py20305.forwarders.mqtt_forwarder import MQTTForwarder

logger = logging.getLogger(__name__)


def _clean_dict(obj: Any) -> Any:
    """Recursively clean a model dict for readable JSON serialization.

    - Converts bytes to hex strings
    - Strips None values and empty dicts/lists
    - Unwraps single-value wrapper objects like {"value": X, "any_attributes": {}}
    """
    if isinstance(obj, dict):
        # Unwrap xsdata value wrappers: {"value": X, "any_attributes": {}} -> X
        keys = set(obj.keys())
        if keys == {"value", "any_attributes"} or keys == {"value"}:
            return _clean_dict(obj["value"])

        cleaned = {}
        for k, v in obj.items():
            if k in ("any_attributes", "other_element") and not v:
                continue
            # xsdata generates revision-suffixed aliases (e.g. ``opModFixedW_r2_3``)
            # for fields that changed between IEEE 2030.5 schema revisions.
            # These duplicates are never populated at runtime and must be stripped
            # to keep serialized payloads clean.
            if k.endswith("_r2_3"):
                continue
            v = _clean_dict(v)
            if v is None:
                continue
            if isinstance(v, (dict, list)) and not v:
                continue
            cleaned[k] = v
        return cleaned
    if isinstance(obj, list):
        cleaned_list = []
        for v in obj:
            v = _clean_dict(v)
            if v is None:
                continue
            if isinstance(v, (dict, list)) and not v:
                continue
            cleaned_list.append(v)
        return cleaned_list
    if isinstance(obj, bytes):
        return obj.hex()
    return obj


class MQTTForwarderAdapter(AbstractForwarder):
    """Adapter that wraps MQTTForwarder with ProtocolMessage v2.0 conversion.

    Responsibilities:
    - Convert MessageFrame to ProtocolMessage v2.0 format
    - Extract LFDI using multi-level fallback chain
    - Forward both valid and invalid messages for security auditing
    - Route messages to underlying MQTTForwarder

    This adapter implements the BaseForwarder protocol and can be used
    interchangeably with other forwarder implementations.
    """

    def __init__(
        self,
        mqtt_forwarder: MQTTForwarder,
        client_lfdi: str | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            mqtt_forwarder: Underlying MQTT forwarder for publishing
            client_lfdi: This client's own LFDI, used when a message names no device
        """
        super().__init__(name="mqtt-adapter")
        self._forwarder = mqtt_forwarder
        self._client_lfdi = client_lfdi
        self._forwarder_id: str | None = None
        self._source_host: str | None = None

        # Only set the converter if the forwarder doesn't already have one,
        # to avoid clobbering a converter supplied at construction.
        if not self._forwarder.has_message_converter:
            self._forwarder.set_message_converter(lambda frame: frame.content)

    @property
    def client_lfdi(self) -> str | None:
        """Return this client's own LFDI, used as an identifier fallback."""
        return self._client_lfdi

    @client_lfdi.setter
    def client_lfdi(self, value: str | None) -> None:
        """Set this client's own LFDI, used when a message names no device."""
        self._client_lfdi = value

    @property
    def forwarder_id(self) -> str | None:
        """Return the configured forwarder ID."""
        return self._forwarder_id

    @forwarder_id.setter
    def forwarder_id(self, value: str | None) -> None:
        """Set forwarder ID for inclusion in ProtocolMessages."""
        self._forwarder_id = value

    @property
    def source_host(self) -> str | None:
        """Return source hostname for forwarded messages."""
        return self._source_host

    @source_host.setter
    def source_host(self, value: str | None) -> None:
        """Set source hostname for client endpoint in ProtocolMessages."""
        self._source_host = value

    async def start(self) -> None:
        """Start the adapter and underlying forwarder."""
        await self._forwarder.start()
        self._running = True
        logger.info("MQTT adapter started")

    async def stop(self) -> None:
        """Stop the adapter and underlying forwarder."""
        self._running = False
        await self._forwarder.stop()
        logger.info("MQTT adapter stopped")

    def queue_event(self, event: EventFrame) -> None:
        """Hand an already-serialized event to the underlying forwarder.

        The base class drops events, which is right for a forwarder carrying
        only protocol messages -- but this one wraps a forwarder that does
        carry them, so failing to delegate here silently discards every event
        the manager routes to it.

        Unlike a message frame there is nothing to convert: the payload is
        already in its wire form and the topic travels with it.

        Args:
            event: Event frame to publish
        """
        if not self._running:
            return
        # Counted here as well as on the wrapped forwarder: the adapter is
        # what the manager registers, so its stats are what an operator sees;
        # the wrapped forwarder's own count appears under underlying_forwarder.
        self._stats["events_queued"] = self._stats.get("events_queued", 0) + 1
        self._forwarder.queue_event(event)

    def queue_message(self, frame: MessageFrame) -> None:
        """Convert frame to ProtocolMessage and queue for publishing.

        Both valid and invalid messages are forwarded for security auditing.
        The is_valid flag is populated by the HTTP client's XSD validation.

        Args:
            frame: Message frame to convert and publish
        """
        if not self._running:
            return

        self._record_queued()

        # Convert to ProtocolMessage
        try:
            protocol_msg = self._convert_to_protocol_message(frame)
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Failed to convert {frame.message_type} frame to ProtocolMessage: {e}",
                source="mqtt",
                dedup_key=f"pm_convert:{frame.message_type}",
                details={"message_type": frame.message_type, "error": str(e)},
            )
            self._record_error(f"Conversion error: {e}")
            return

        # We wrap it in a MessageFrame with content set to the v2 dict;
        # the message_converter extracts it directly to avoid double-wrapping.
        converted_frame = MessageFrame(
            direction=frame.direction,
            message_type=frame.message_type,
            content=protocol_msg.to_dict(),
            timestamp=frame.timestamp,
            is_valid=frame.is_valid,
        )

        self._forwarder.queue_message(converted_frame)

    def _convert_to_protocol_message(self, frame: MessageFrame) -> ProtocolMessage:
        """Convert MessageFrame to ProtocolMessage v2.0 format.

        Args:
            frame: Source message frame

        Returns:
            ProtocolMessage in the wire format described in `types.py`
        """
        metadata = frame.metadata or {}

        # Extract identifiers
        client_id = extract_client_id(metadata, frame.content, self._client_lfdi)
        lfdi = extract_lfdi(metadata, frame.content, self._client_lfdi)

        # Build network endpoints
        source, destination = self._build_endpoints(frame, metadata)

        # Map direction
        direction = (
            WireDirection.UPSTREAM
            if frame.direction == MessageDirection.UPSTREAM
            else WireDirection.DOWNSTREAM
        )

        # Build protocol metadata
        protocol_data = ProtocolMetadata(
            lfdi=lfdi if lfdi != "Unknown" else None,
            message_type=frame.message_type,
            http_method=frame.http_method or metadata.get("http_method"),
            uri=frame.uri or metadata.get("uri"),
        )

        # Serialize content to PayloadEnvelope
        payload = self._serialize_content(frame.content)

        return ProtocolMessage(
            protocol=Protocol.IEEE_2030_5,
            direction=direction,
            client_id=client_id,
            payload=payload,
            source=source,
            destination=destination,
            forwarder_id=self._forwarder_id or "",
            protocol_data=protocol_data,
            timestamp=frame.timestamp.isoformat(),
            is_valid=frame.is_valid,
            validation_error=frame.validation_error,
        )

    @staticmethod
    def _serialize_content(content: Any) -> PayloadEnvelope:
        """Serialize content to PayloadEnvelope for ProtocolMessage.

        Args:
            content: The content to serialize (Pydantic model, dict, bytes, etc.)

        Returns:
            PayloadEnvelope with appropriate content type
        """
        if content is None:
            return PayloadEnvelope.from_dict({})

        # Handle xsdata/Pydantic models — serialize back to XML to match
        # the ProtocolMessage format (application/xml payload).
        if hasattr(content, "model_dump") or hasattr(content, "dict"):
            try:
                from py20305.xml.serialization import to_xml

                xml_bytes = to_xml(content)
                return PayloadEnvelope.from_xml(xml_bytes.decode("utf-8"))
            except Exception:
                # Model can't be serialized to XML (not an xsdata model);
                # fall back to cleaned JSON dict.  Use mode="json" so
                # datetime, bytes, enums etc. are already JSON-safe before
                # _clean_dict processes the result.
                if hasattr(content, "model_dump"):
                    return PayloadEnvelope.from_dict(_clean_dict(content.model_dump(mode="json")))
                return PayloadEnvelope.from_dict(_clean_dict(content.dict()))

        # Handle bytes (raw XML body from POST/PUT)
        if isinstance(content, bytes):
            try:
                return PayloadEnvelope.from_xml(content.decode("utf-8"))
            except UnicodeDecodeError:
                return PayloadEnvelope.from_binary(content)

        # Handle strings
        if isinstance(content, str):
            return PayloadEnvelope.infer_from_string(content)

        # Handle dicts
        if isinstance(content, dict):
            return PayloadEnvelope.from_dict(content)

        # Handle lists (serialize as JSON)
        if isinstance(content, list):
            return PayloadEnvelope.from_dict({"items": content})

        # Fallback: convert to string
        return PayloadEnvelope.from_text(str(content))

    def _build_endpoints(
        self,
        frame: MessageFrame,
        metadata: dict[str, Any],
    ) -> tuple[NetworkEndpoint, NetworkEndpoint]:
        """Build source and destination endpoints from frame data.

        For UPSTREAM messages: source=client, destination=server
        For DOWNSTREAM messages: source=server, destination=client

        The client IP uses source_host (this client's hostname) as the primary
        value, since the client is the client in the 2030.5 exchange.
        """
        server_ip = frame.server_host or metadata.get("server_ip") or "0.0.0.0"
        server_port = frame.server_port or metadata.get("server_port") or 0

        client_ip = (
            self._source_host or metadata.get("client_ip") or metadata.get("source_ip") or "0.0.0.0"
        )
        client_port = metadata.get("client_port") or metadata.get("source_port") or 0

        server_endpoint = NetworkEndpoint(ip=str(server_ip), port=int(server_port))
        client_endpoint = NetworkEndpoint(ip=str(client_ip), port=int(client_port))

        if frame.direction == MessageDirection.UPSTREAM:
            return client_endpoint, server_endpoint
        else:
            return server_endpoint, client_endpoint

    def get_statistics(self) -> dict[str, Any]:
        """Return adapter statistics combined with forwarder stats."""
        stats = super().get_statistics()
        stats["client_lfdi"] = self._client_lfdi
        stats["underlying_forwarder"] = self._forwarder.get_statistics()
        return stats
