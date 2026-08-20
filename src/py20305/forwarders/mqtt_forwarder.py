"""Async MQTT forwarder for publishing protocol messages.

This module provides the MQTTForwarder class that publishes IEEE 2030.5
protocol messages to an MQTT broker. Supports both mTLS connections
(e.g., AWS IoT Core) and plain MQTT brokers (e.g., RabbitMQ).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import ssl
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from py20305.forwarders.base import (
    AbstractForwarder,
    EventFrame,
    MessageFrame,
    TelemetryFrame,
)
from py20305.forwarders.config import PROTOCOL_MESSAGE_TOPIC_SUFFIX

if TYPE_CHECKING:
    from py20305.forwarders.config import MQTTForwarderConfig

logger = logging.getLogger(__name__)

# Queue size for buffering messages before publish
_DEFAULT_QUEUE_SIZE = 1000


class MQTTForwarder(AbstractForwarder):
    """Async MQTT forwarder for publishing protocol messages.

    Publishes IEEE 2030.5 protocol messages to the configured MQTT topic.
    Messages are queued and published asynchronously in a background task.
    Supports both mTLS (AWS IoT Core) and plain MQTT (RabbitMQ) connections.

    Attributes:
        config: MQTT configuration (endpoint, certs, topics)
        connected: Whether currently connected to broker
    """

    def __init__(
        self,
        config: MQTTForwarderConfig,
        queue_size: int = _DEFAULT_QUEUE_SIZE,
        message_converter: Any | None = None,
    ) -> None:
        """Initialize the MQTT forwarder.

        Args:
            config: MQTT forwarder configuration
            queue_size: Max messages to buffer before dropping
            message_converter: Optional callable to convert MessageFrame to dict
                             (defaults to using frame content directly)
        """
        super().__init__(name="mqtt")
        self._config = config
        self._queue: asyncio.Queue[MessageFrame | TelemetryFrame | EventFrame] = asyncio.Queue(
            maxsize=queue_size
        )
        self._message_converter = message_converter
        self._telemetry_converter: Any | None = None
        self._client: Any = None  # aiomqtt.Client
        self._publish_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()
        self._connected = False

        # Generate unique client ID
        self._client_id = f"{config.client_id_prefix}-{uuid.uuid4().hex[:8]}"

    @property
    def has_message_converter(self) -> bool:
        """Return whether a message converter is configured."""
        return self._message_converter is not None

    def set_message_converter(self, converter: Any) -> None:
        """Set a custom message converter for transforming frames before publishing."""
        self._message_converter = converter

    @property
    def has_telemetry_converter(self) -> bool:
        """Return whether a telemetry converter is configured."""
        return self._telemetry_converter is not None

    def set_telemetry_converter(self, converter: Any) -> None:
        """Set the converter turning a TelemetryFrame into a publishable dict."""
        self._telemetry_converter = converter

    @property
    def config(self) -> MQTTForwarderConfig:
        """Return configuration."""
        return self._config

    @property
    def connected(self) -> bool:
        """Return connection status."""
        return self._connected

    def _create_ssl_context(self) -> ssl.SSLContext | None:
        """Create SSL context for mTLS connection, or None for plain MQTT."""
        if not self._config.tls_enabled:
            return None

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2

        # Load client cert for mTLS
        ctx.load_cert_chain(
            certfile=str(self._config.cert_path),
            keyfile=str(self._config.key_path),
        )
        ctx.load_verify_locations(cafile=str(self._config.ca_path))

        # AWS IoT Core ALPN protocol selection:
        # - Port 443: requires "x-amzn-mqtt-ca" ALPN
        # - Port 8883: standard MQTT, no ALPN required
        if self._config.port == 443:
            ctx.set_alpn_protocols(["x-amzn-mqtt-ca"])

        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    async def start(self) -> None:
        """Start the forwarder and connect to MQTT broker.

        Establishes connection and starts the background publish task.
        """
        if self._running:
            logger.warning("MQTTForwarder already running")
            return

        tls_status = "mTLS" if self._config.tls_enabled else "plain"
        logger.info(
            "Starting MQTT forwarder: %s:%d (client: %s, %s)",
            self._config.endpoint,
            self._config.port,
            self._client_id,
            tls_status,
        )

        self._shutdown_event.clear()

        try:
            # Import aiomqtt here to make it optional
            import aiomqtt

            ssl_context = self._create_ssl_context()

            self._client = aiomqtt.Client(
                hostname=self._config.endpoint,
                port=self._config.port,
                tls_context=ssl_context,
                identifier=self._client_id,
            )

            await self._client.__aenter__()
            self._connected = True
            self._running = True

            # Start publish loop
            self._publish_task = asyncio.create_task(
                self._publish_loop(),
                name="mqtt-publish-loop",
            )

            logger.info("MQTT forwarder connected successfully")

        except ImportError:
            from py20305.diagnostics import report

            report(
                "errors",
                "aiomqtt is not installed but mqtt.enabled=true. Install with: pip install aiomqtt",
                source="mqtt",
            )
            raise
        except Exception as e:
            from py20305.diagnostics import report

            endpoint = self._config.endpoint
            port = self._config.port
            report(
                "errors",
                f"Failed to connect MQTT forwarder to {endpoint}:{port}: {e}",
                source="mqtt",
                dedup_key=f"mqtt_connect:{endpoint}:{port}",
                details={"endpoint": endpoint, "port": port, "error": str(e)},
            )
            self._connected = False
            raise

    async def stop(self) -> None:
        """Stop the forwarder gracefully.

        Signals shutdown, drains the queue, then disconnects.
        """
        if not self._running:
            return

        logger.info("Stopping MQTT forwarder (queue size: %d)", self._queue.qsize())

        self._running = False
        self._shutdown_event.set()

        # Wait for publish task to drain queue
        if self._publish_task:
            try:
                await asyncio.wait_for(self._publish_task, timeout=10.0)
            except TimeoutError:
                from py20305.diagnostics import report

                report(
                    "warnings",
                    "MQTT publish task did not drain within timeout on shutdown",
                    source="mqtt",
                )
                self._publish_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._publish_task
            self._publish_task = None

        # Disconnect client
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("Error disconnecting MQTT client: %s", e)
            self._client = None

        self._connected = False
        logger.info("MQTT forwarder stopped")

    def queue_message(self, frame: MessageFrame) -> None:
        """Queue a message for publishing.

        Messages are queued synchronously and published asynchronously.
        If the queue is full, the oldest message is dropped.

        Args:
            frame: Message frame to publish
        """
        self._enqueue(frame)

    def queue_event(self, event: EventFrame) -> None:
        """Queue an event for publishing on its own topic.

        Events share the message queue so they inherit its backpressure and
        drain-on-shutdown behavior rather than needing a second copy of it.

        Args:
            event: Event frame to publish
        """
        self._enqueue(event, counter="events_queued")

    def queue_telemetry(self, frame: TelemetryFrame) -> None:
        """Queue measured device state for publishing.

        Same queue and same backpressure as protocol capture: under a slow
        broker the newest telemetry is what a monitoring upstream wants, and
        the dropped frames are ones already superseded by a later reading.

        Counted as telemetry rather than as a message, for the same reason
        events are: one number that conflates the two says nothing about either.
        """
        self._enqueue(frame, counter="telemetry_queued")

    def _enqueue(
        self, item: MessageFrame | TelemetryFrame | EventFrame, *, counter: str = "messages_queued"
    ) -> None:
        """Put one item on the publish queue, dropping the oldest when full.

        ``counter`` names the statistic to credit, so an event is counted as an
        event rather than inflating the message count as well.
        """
        if not self._running:
            logger.debug("MQTT forwarder not running, dropping message")
            return

        if self._shutdown_event.is_set():
            logger.debug("MQTT forwarder shutting down, dropping message")
            return

        try:
            self._queue.put_nowait(item)
            self._stats[counter] = self._stats.get(counter, 0) + 1
        except asyncio.QueueFull:
            from py20305.diagnostics import report

            # Single shared dedup key: backpressure is one ongoing condition;
            # the entry's count tells the operator how often the broker is
            # too slow rather than producing one entry per dropped message.
            report(
                "warnings",
                "MQTT queue full, dropping oldest message",
                source="mqtt",
                dedup_key="mqtt_queue_full",
                details={"endpoint": self._config.endpoint},
            )
            try:
                self._queue.get_nowait()  # Drop oldest
                self._stats["messages_dropped"] = self._stats.get("messages_dropped", 0) + 1
                self._queue.put_nowait(item)
                self._stats[counter] = self._stats.get(counter, 0) + 1
            except asyncio.QueueEmpty:
                pass

    async def _publish_loop(self) -> None:
        """Background task that publishes queued messages."""
        message_topic = f"{self._config.topic_base}/{PROTOCOL_MESSAGE_TOPIC_SUFFIX}"
        # Its own topic rather than a shared one with a type marker inside: a
        # subscriber that wants only measurements, or only capture, should be able
        # to say so at the broker instead of filtering every message on arrival.
        telemetry_topic = f"{self._config.topic_base}/out/telemetry"

        while self._running or not self._queue.empty():
            try:
                # Wait for message with timeout to check shutdown
                try:
                    item = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0,
                    )
                except TimeoutError:
                    if not self._running and self._queue.empty():
                        break
                    continue

                # Convert to publishable format. An event is already in its
                # wire form and carries its own topic; a message frame is
                # converted and goes on the protocol-message topic.
                try:
                    if isinstance(item, EventFrame):
                        topic = f"{self._config.topic_base}/{item.topic_suffix}"
                        payload = item.payload
                    elif isinstance(item, TelemetryFrame):
                        topic = telemetry_topic
                        if self._telemetry_converter:
                            payload = self._telemetry_converter(item)
                        else:
                            payload = self._telemetry_to_dict(item)
                    else:
                        topic = message_topic
                        if self._message_converter:
                            payload = self._message_converter(item)
                        else:
                            payload = self._frame_to_dict(item)

                    payload_json = json.dumps(payload, default=str)
                except Exception as e:
                    from py20305.diagnostics import report

                    report(
                        "warnings",
                        f"Failed to serialize MQTT message for {self._config.endpoint}: {e}",
                        source="mqtt",
                        dedup_key=f"mqtt_publish:{self._config.endpoint}:serialize",
                        details={
                            "endpoint": self._config.endpoint,
                            "kind": "serialize",
                            "error": str(e),
                        },
                    )
                    self._record_error(f"Serialization error: {e}")
                    continue

                # Publish
                try:
                    if self._client:
                        await self._client.publish(
                            topic,
                            payload_json.encode("utf-8"),
                            qos=1,
                        )
                        self._record_success()
                        logger.debug("Published message to %s", topic)
                    else:
                        logger.warning("MQTT client not connected")
                        self._record_error("Client not connected")
                except Exception as e:
                    from py20305.diagnostics import report

                    report(
                        "warnings",
                        f"Failed to publish MQTT message to {self._config.endpoint}: {e}",
                        source="mqtt",
                        dedup_key=f"mqtt_publish:{self._config.endpoint}:publish",
                        details={
                            "endpoint": self._config.endpoint,
                            "kind": "publish",
                            "error": str(e),
                        },
                    )
                    self._record_error(f"Publish error: {e}")
                    # On connection error, attempt reconnect
                    if "connection" in str(e).lower():
                        self._connected = False

            except asyncio.CancelledError:
                logger.debug("Publish loop cancelled")
                break
            except Exception as e:
                from py20305.diagnostics import report

                report(
                    "warnings",
                    f"Unexpected error in MQTT publish loop for {self._config.endpoint}: {e}",
                    source="mqtt",
                    dedup_key=f"mqtt_publish:{self._config.endpoint}:loop",
                    details={
                        "endpoint": self._config.endpoint,
                        "kind": "loop",
                        "error": str(e),
                    },
                    exc_info=True,
                )
                self._record_error(f"Loop error: {e}")

    def _frame_to_dict(self, frame: MessageFrame) -> dict[str, Any]:
        """Convert MessageFrame to dict for publishing.

        This is a simple fallback; use MQTTForwarderAdapter for full
        ProtocolMessage v2.0 format.
        """
        return {
            "direction": frame.direction.value,
            "message_type": frame.message_type,
            "content": self._serialize_content(frame.content),
            "timestamp": frame.timestamp.isoformat(),
            "is_valid": frame.is_valid,
            "validation_error": frame.validation_error,
            "http_method": frame.http_method,
            "uri": frame.uri,
            "status_code": frame.status_code,
            "metadata": frame.metadata,
        }

    def _telemetry_to_dict(self, frame: TelemetryFrame) -> dict[str, Any]:
        """Convert a TelemetryFrame to a dict for publishing.

        Fallback for a forwarder used without the Sentry adapter; the adapter
        supplies a converter producing the ProtocolMessage v2.0 envelope.
        """
        return {
            "device": frame.device,
            "quality": frame.quality,
            "last_success": frame.last_success,
            "timestamp": frame.timestamp.isoformat(),
            "points": {
                key: {
                    "value": point.value,
                    "source_timestamp": point.source_timestamp,
                    "quality": point.quality,
                    "protocol_quality": point.protocol_quality,
                }
                for key, point in frame.points.items()
            },
            "metadata": frame.metadata,
        }

    @staticmethod
    def _serialize_content(content: Any) -> Any:
        """Serialize content to JSON-compatible format."""
        if content is None:
            return None

        if hasattr(content, "model_dump"):
            return content.model_dump(mode="json")

        if hasattr(content, "dict"):
            return content.dict()

        if isinstance(content, datetime):
            return content.isoformat()

        if isinstance(content, bytes):
            try:
                return content.decode("utf-8")
            except UnicodeDecodeError:
                return content.hex()

        return content

    def get_statistics(self) -> dict[str, Any]:
        """Return forwarder statistics including connection status."""
        stats = super().get_statistics()
        stats.update(
            {
                "connected": self._connected,
                "broker": f"{self._config.endpoint}:{self._config.port}",
                "client_id": self._client_id,
                "queue_size": self._queue.qsize(),
                "topic": f"{self._config.topic_base}/{PROTOCOL_MESSAGE_TOPIC_SUFFIX}",
            }
        )
        return stats
