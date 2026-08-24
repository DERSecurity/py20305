"""Base types and protocols for message forwarding.

This module defines the core abstractions for capturing IEEE 2030.5 HTTP
exchanges and forwarding them to external systems (e.g., AWS IoT Core for
security monitoring).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class MessageDirection(Enum):
    """Direction of message flow in IEEE 2030.5 HTTP exchange."""

    UPSTREAM = "upstream"  # Client -> Server (requests: POST, PUT, DELETE)
    DOWNSTREAM = "downstream"  # Server -> Client (responses: GET results, status codes)


@dataclass
class MessageFrame:
    """Captures a single IEEE 2030.5 HTTP exchange message.

    This is the internal format used to capture HTTP request/response pairs
    before conversion to the ProtocolMessage format for external forwarding.

    Attributes:
        direction: Whether this is an upstream (request) or downstream (response) message
        message_type: The IEEE 2030.5 resource type (e.g., "EndDeviceList", "DerControl")
        content: The parsed Pydantic model or dict representing the message payload
        timestamp: When the message was captured (defaults to now)
        is_valid: Whether the message passed schema validation
        validation_error: Error message if validation failed
        http_method: The HTTP method used (GET, POST, PUT, DELETE)
        uri: The request URI path
        status_code: HTTP response status code (for downstream messages)
        server_host: IEEE 2030.5 server hostname or IP
        server_port: IEEE 2030.5 server port
        metadata: Additional context (LFDI, client info, etc.)
    """

    direction: MessageDirection
    message_type: str
    content: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_valid: bool = True
    validation_error: str | None = None
    http_method: str | None = None
    uri: str | None = None
    status_code: int | None = None
    server_host: str | None = None
    server_port: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TelemetryPoint:
    """One measured value as published upstream."""

    value: Any
    #: When the device was read, not when this was published. A consumer
    #: judging freshness needs the former; the latter only says when we got
    #: around to sending it.
    source_timestamp: float
    #: Store quality -- ``good``, ``stale`` or ``comm_lost``.
    quality: str
    #: The connector's own quality bits for this point, when it supplied any.
    #: Kept separate from :attr:`quality` because they answer different
    #: questions: whether the device trusts the reading, and whether we read it
    #: recently enough.
    protocol_quality: int | None = None


@dataclass
class TelemetryFrame:
    """A device's measured state, for publication to a monitoring upstream.

    Deliberately not a :class:`MessageFrame`. Nine of that type's twelve fields
    describe an HTTP exchange -- method, URI, status code, host, port,
    direction, resource type, validity -- and none of them mean anything for a
    measurement. Reusing it would leave every telemetry message carrying nine
    empty fields and a ``message_type`` that lies about what it holds.
    """

    device: str
    points: dict[str, TelemetryPoint]
    #: Device-level quality. ``comm_lost`` means the points are last-known
    #: values retained deliberately, not current ones -- a consumer that
    #: ignores this republishes stale numbers as though they were fresh.
    quality: str
    #: Epoch seconds of the last acquisition that succeeded, or None if none
    #: ever has.
    last_success: float | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventFrame:
    """A pre-serialized event carried on the same transport as message frames.

    The forwarder transport was built to carry captured protocol exchanges.
    Southbound device telemetry is a second kind of payload on that same
    transport rather than a second egress path -- the manager already handles
    queueing, connection state and mTLS-or-plain-broker selection, and a second
    path would duplicate all of it.

    The two kinds are distinguished by type at the queue rather than by
    inspecting the payload, and each carries the topic it belongs on, so adding
    a third kind later needs no change to the publish loop.

    Attributes:
        payload: The event, already in its wire form. Forwarders publish it
            as-is; the shape is the emitting subsystem's contract, not the
            transport's.
        topic_suffix: Topic to publish on, relative to the forwarder's
            configured topic base.
        kind: Short label for logs and statistics.
    """

    payload: dict[str, Any]
    topic_suffix: str
    kind: str = "event"


@runtime_checkable
class BaseForwarder(Protocol):
    """Protocol for message forwarders.

    Implementations should handle:
    - Async message queuing for non-blocking operation
    - Connection lifecycle management (start/stop)
    - Graceful shutdown with message draining
    - Statistics tracking for monitoring

    The queue_message method should be non-blocking. Actual forwarding
    happens asynchronously in the background.
    """

    @property
    def name(self) -> str:
        """Identifier for this forwarder instance."""
        ...

    async def start(self) -> None:
        """Initialize the forwarder and start background processing.

        Should establish connections and begin the async publish loop.
        """
        ...

    async def stop(self) -> None:
        """Stop the forwarder gracefully.

        Should drain any queued messages before disconnecting.
        """
        ...

    def queue_message(self, frame: MessageFrame) -> None:
        """Queue a message for forwarding.

        This method should be non-blocking. Messages are processed
        asynchronously by the forwarder's background task.

        Args:
            frame: The captured HTTP exchange to forward
        """
        ...

    def queue_event(self, event: EventFrame) -> None:
        """Queue an event for forwarding.

        Non-blocking, like :meth:`queue_message`. A forwarder that carries only
        protocol messages may leave the default no-op.

        Args:
            event: The event to forward
        """
        ...

    def queue_telemetry(self, frame: TelemetryFrame) -> None:
        """Queue measured device state for forwarding.

        A separate entry point rather than an overload of
        :meth:`queue_message`, so a forwarder that carries protocol capture and
        nothing else can decline telemetry by simply not implementing it.
        Non-blocking, like ``queue_message``.
        """
        ...

    def get_statistics(self) -> dict[str, Any]:
        """Return forwarding statistics.

        Should include at minimum:
        - messages_queued: Total messages received
        - messages_published: Successfully forwarded messages
        - publish_errors: Number of forwarding failures
        - connected: Current connection status
        """
        ...


class AbstractForwarder(ABC):
    """Abstract base class providing common forwarder functionality.

    Provides default implementations for statistics and state tracking.
    Subclasses must implement the async start/stop and queue_message methods.
    """

    def __init__(self, name: str) -> None:
        self._name = name
        self._running = False
        self._stats: dict[str, Any] = {
            "messages_queued": 0,
            # Seeded so a forwarder that never carries a kind reports an
            # explicit zero rather than omitting the key, which would leave the
            # statistics schema dependent on what has happened to arrive.
            "events_queued": 0,
            "telemetry_queued": 0,
            "messages_published": 0,
            "publish_errors": 0,
            "last_publish_time": None,
            "last_error": None,
        }

    @property
    def name(self) -> str:
        return self._name

    @property
    def running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None:
        """Start the forwarder."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Stop the forwarder."""
        ...

    @abstractmethod
    def queue_message(self, frame: MessageFrame) -> None:
        """Queue a message for forwarding."""
        ...

    def queue_event(self, event: EventFrame) -> None:
        """Queue an event for forwarding.

        Defaults to dropping the event, so a forwarder that only carries
        protocol messages is unaffected by a subsystem that emits events.
        """
        logger.debug("%s does not carry events; dropping %s", self._name, event.kind)

    def queue_telemetry(self, frame: TelemetryFrame) -> None:
        """Discard telemetry by default.

        Concrete rather than abstract: telemetry arrived after these forwarders
        did, and a forwarder built to carry protocol capture is not wrong to
        ignore it. Making it abstract would break every existing implementation
        to no benefit.
        """
        logger.debug("%s does not carry telemetry; dropping frame", self._name)

    def get_statistics(self) -> dict[str, Any]:
        """Return forwarding statistics."""
        return {
            **self._stats,
            "running": self._running,
            "name": self._name,
        }

    def _record_success(self) -> None:
        """Record a successful publish."""
        self._stats["messages_published"] += 1
        self._stats["last_publish_time"] = datetime.now(UTC).isoformat()

    def _record_error(self, error: str) -> None:
        """Record a publish error."""
        self._stats["publish_errors"] += 1
        self._stats["last_error"] = error

    def _record_queued(self) -> None:
        """Record a message being queued."""
        self._stats["messages_queued"] += 1
