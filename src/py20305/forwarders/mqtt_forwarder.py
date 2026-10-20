"""Async MQTT forwarder for publishing protocol messages.

This module provides the MQTTForwarder class that publishes IEEE 2030.5
protocol messages to an MQTT broker. Supports both mTLS connections
(e.g., AWS IoT Core) and plain MQTT brokers (e.g., RabbitMQ).

Reconnecting
------------

The publish loop owns the broker connection. It is the only code that
connects after start, so one reconnect runs at a time.

- **Loss.** Any ``aiomqtt.MqttError`` or ``OSError`` from a publish marks the
  link down, and so does the client's own disconnect signal while the loop is
  idle. The exception type decides, not its message text: the message for a
  closed connection is "The client is not currently connected.", which a test
  for the word "connection" misses.
- **Backoff.** The loop closes the old client, waits, and builds a new one with
  the same client ID. Each attempt loads the TLS files again, so a rotated
  certificate is used without a restart. The wait starts at
  ``reconnect_initial_delay_seconds``, doubles after each failed attempt up to
  ``reconnect_max_delay_seconds``, and is shortened by up to 20% at random so
  clients that lost one broker together do not retry in step. The backoff
  resets only once the new connection is stable: a publish succeeds on it, or
  it stays up for ``reconnect_max_delay_seconds``. A connection that drops
  sooner continues the outage, so a broker that accepts and then drops at once
  is retried at the growing delay, not every initial delay.
- **Buffering.** Nothing is published while the link is down. Items wait in
  the existing bounded buffers, and the item whose publish failed is published
  first after reconnecting. QoS 1 makes delivery at least once, so that item
  can arrive twice if the broker received it before the connection dropped.
  An item whose publish loses the connection ``_MAX_PUBLISH_ATTEMPTS`` times
  in a row is dropped and counted as a publish error. A broker can close the
  connection in answer to one specific publish, for example to a topic its
  policy denies, and that item must not block everything behind it.
- **Startup.** When the first connect fails, ``start()`` reports the failure
  and returns with the forwarder running and the link down, and the loop keeps
  retrying. ``ForwarderManager`` treats a forwarder whose ``start()`` raised as
  failed and drops what it routes to it until a caller retries it, so raising
  would lose everything queued during a broker outage at boot.
  ``require_initial_connection: true`` restores the raising behavior for a
  caller that wants it. A forwarder that cannot be set up at all, because
  aiomqtt is missing or the TLS files cannot be loaded, always raises.
- **Reporting.** A loss logs one warning with its reason and sends one
  connection failure event. While the broker stays unreachable, a further
  warning and failure event go out at most every five minutes. The connection
  event emitter never coalesces failures, so one event per attempt would add
  hundreds of records to an outage of a few hours. Reconnecting logs one info
  line with the downtime and the number of attempts, and sends one success
  event. A connection that drops again before it is stable is reported at the
  same five-minute rate, and the reconnect after it is reported only when a
  failure was reported. A broker that refuses the connection for bad
  credentials or missing authorization is reported at error level on the
  first refusal of an outage, and retried like any other failure.
- **Stop during a connect.** aiomqtt runs paho's blocking connect in an
  executor thread, and canceling the await does not stop that thread. When
  stop cancels a connect attempt, the forwarder closes that client once its
  thread returns, so a connect that completes late does not leave a session
  open. The thread itself runs until paho's connect returns or times out,
  which can delay interpreter exit by up to that long.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import ssl
import threading
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from py20305.forwarders.base import (
    AbstractForwarder,
    EventFrame,
    MessageFrame,
    TelemetryFrame,
)
from py20305.forwarders.config import PROTOCOL_MESSAGE_TOPIC_SUFFIX, TELEMETRY_TOPIC_SUFFIX
from py20305.forwarders.connection_telemetry import (
    SERVICE_MQTT,
    ConnectionRole,
    endpoint_for_host,
)
from py20305.forwarders.ocsf import Endpoint

if TYPE_CHECKING:
    from py20305.forwarders.config import MQTTForwarderConfig
    from py20305.forwarders.connection_telemetry import LinkObserver

logger = logging.getLogger(__name__)

# Queue size for buffering captured traffic before publish
_DEFAULT_QUEUE_SIZE = 1000

# How many devices may hold a pending telemetry frame. A map keyed by device is
# otherwise unbounded if device identifiers are.
_DEFAULT_TELEMETRY_DEVICES = 1000

# Telemetry frames published before the loop looks at capture again. Capture has
# priority, but an unbounded slice would let a large fleet delay it.
_TELEMETRY_SLICE = 8

# Captured items published before pending telemetry gets a turn. Bounded so
# sustained protocol traffic delays measurements rather than starving them.
_CAPTURE_RUN = 64

# Each reconnect delay is shortened by a random amount up to this fraction of
# itself, so the delay never exceeds the configured maximum.
_RECONNECT_JITTER = 0.2

# While the broker stays unreachable, repeat the warning and the connection
# failure event at most this often.
_FAILURE_REPORT_INTERVAL_SECONDS = 300.0

# Publish attempts an item gets when each one loses the broker connection. The
# item is then dropped, so it cannot block the items behind it.
_MAX_PUBLISH_ATTEMPTS = 3

# CONNACK reason codes that refuse a connection for its credentials or its
# authorization: bad user name or password, not authorized, banned, and bad
# authentication method. paho reports the MQTT 3.1.1 return codes 4 and 5 as
# 134 and 135.
_AUTH_REFUSAL_REASON_CODES = frozenset({134, 135, 138, 140})

# Longest wait for an old client to close before it is dropped.
_CLOSE_TIMEOUT_SECONDS = 5.0

# Longest wait for the publish loop to drain the buffers on stop.
_DRAIN_TIMEOUT_SECONDS = 10.0

# Longest idle wait in the publish loop before it checks its state again.
_IDLE_WAIT_SECONDS = 1.0


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
        telemetry_device_limit: int = _DEFAULT_TELEMETRY_DEVICES,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        """Initialize the MQTT forwarder.

        Args:
            config: MQTT forwarder configuration
            queue_size: Max captured messages and events to buffer before dropping
            message_converter: Optional callable to convert MessageFrame to dict
                             (defaults to using frame content directly)
            telemetry_device_limit: Max devices holding a pending telemetry frame
            sleep: Waits out a reconnect delay. Stop interrupts the wait.
            clock: Monotonic time in seconds, for downtime and report throttling.
            jitter: Returns a number in [0, 1) that sets how much a reconnect
                delay is shortened.
        """
        super().__init__(name="mqtt")
        self._config = config
        # Two buffers rather than one, because the kinds want opposite policies
        # under backpressure. A captured exchange or an event is a distinct
        # record that nothing will send again, so the capture buffer is lossless
        # until it is itself full. A measurement is superseded by the next
        # reading for the same device, so telemetry is held newest-per-device
        # and can never cost a capture its place.
        self._capture_queue: asyncio.Queue[MessageFrame | EventFrame] = asyncio.Queue(
            maxsize=queue_size
        )
        self._telemetry_pending: dict[str, TelemetryFrame] = {}
        self._telemetry_device_limit = telemetry_device_limit
        # Seeded like the kind counters on the base class: a consumer reading
        # what backpressure has cost should not have to tell a zero from a
        # missing key. These three describe this forwarder's buffer policy, so
        # they live here rather than on a base class that has no buffers.
        self._stats["messages_dropped"] = 0
        self._stats["telemetry_superseded"] = 0
        self._stats["telemetry_dropped"] = 0
        # Broker link history: losses detected, connections restored by the
        # background retry, and why the link last dropped.
        self._stats["disconnects"] = 0
        self._stats["reconnects"] = 0
        self._stats["last_disconnect_reason"] = None
        self._message_converter = message_converter
        self._telemetry_converter: Any | None = None
        self._client: Any = None  # aiomqtt.Client
        self._ssl_context: ssl.SSLContext | None = None
        # Exceptions that mean the broker connection is gone. Set from aiomqtt
        # in start(), so this module imports without the mqtt extra.
        self._link_errors: tuple[type[BaseException], ...] = (OSError,)
        self._publish_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()
        # Signals the publish loop that a buffer went from empty to occupied,
        # so an arrival publishes immediately rather than on the next poll.
        self._wakeup = asyncio.Event()
        self._connected = False
        # A captured item whose publish failed because the link dropped. It is
        # published first after reconnecting, ahead of the queue.
        self._retry_item: MessageFrame | EventFrame | None = None
        # The last item whose publish lost the link, and how many publishes of
        # it in a row did. A telemetry frame counts as the same item as a newer
        # frame for its device.
        self._failing_item: MessageFrame | TelemetryFrame | EventFrame | None = None
        self._failing_attempts = 0
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        # Outage state. The backoff is the base delay before the next
        # reconnect attempt, and zero while connected.
        self._down_since: float | None = None
        self._attempts = 0
        self._backoff = 0.0
        self._last_failure_report: float | None = None
        # Whether the current connection is stable: a publish succeeded on it,
        # or start() made it. A connection that stayed up for
        # reconnect_max_delay_seconds also counts, checked when it drops.
        self._link_stable = False
        self._connected_at = 0.0
        # Whether a failure was reported since the last reported reconnect, so
        # the next reconnect is reported too.
        self._restore_unreported = False
        # Whether a refusal for credentials or authorization was reported in
        # the current outage.
        self._refusal_reported = False
        # Whether the queue-full warning was logged since the queue last
        # drained. Later drops are counted without logging each one.
        self._queue_full_reported = False
        #: Receives this forwarder's broker connection outcomes, with the
        #: ``telemetry`` role. ``None`` reports nothing.
        self.connection_observer: LinkObserver | None = None

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
        """Return whether the broker connection is up.

        False from a detected loss, or a failed first connect, until the
        forwarder connects again.
        """
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
        """Start the forwarder, connect to the MQTT broker and start the publish task.

        A failed first connect is reported and then retried in the background,
        unless ``require_initial_connection`` is set, in which case it raises.
        A forwarder that cannot be set up (aiomqtt missing, TLS files that do
        not load) always raises.
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
            # Imported here so the mqtt extra stays optional.
            import aiomqtt
        except ImportError:
            from py20305.diagnostics import report

            report(
                "errors",
                "aiomqtt is not installed but mqtt.enabled=true. Install with: pip install aiomqtt",
                source="mqtt",
            )
            raise
        self._link_errors = (aiomqtt.MqttError, OSError)

        try:
            self._ssl_context = self._create_ssl_context()
        except Exception as e:
            # The TLS files did not load. Retrying the connection cannot fix that.
            self._report_connect_failure(e)
            raise

        try:
            await self._connect()
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Any failure to connect is retried, whatever aiomqtt raised.
            self._report_connect_failure(e)
            if self._config.require_initial_connection:
                raise
            self._begin_outage()
            # The refusal was just reported as an error, so do not repeat it.
            self._refusal_reported = self._is_auth_refusal(e)
            logger.info(
                "MQTT forwarder will keep trying to connect to %s:%d in the background",
                self._config.endpoint,
                self._config.port,
            )
        else:
            self._link_stable = True
            self._report_broker_connected()
            logger.info("MQTT forwarder connected successfully")

        self._running = True
        self._publish_task = asyncio.create_task(
            self._publish_loop(),
            name="mqtt-publish-loop",
        )

    async def _connect(self) -> None:
        """Open a new broker connection with the forwarder's TLS context and client ID."""
        import aiomqtt

        client = aiomqtt.Client(
            hostname=self._config.endpoint,
            port=self._config.port,
            tls_context=self._ssl_context,
            identifier=self._client_id,
        )
        abandoned = threading.Event()
        self._close_if_abandoned(client, abandoned)
        try:
            await client.__aenter__()
        except BaseException:
            # Set before the disconnect below, so a connect thread that
            # returns after this point sees it and closes the client again.
            abandoned.set()
            self._abandon_client(client)
            raise
        self._client = client
        self._connected = True
        self._connected_at = self._clock()

    def _close_if_abandoned(self, client: Any, abandoned: threading.Event) -> None:
        """Close ``client`` when its connect thread returns after the connect was abandoned.

        aiomqtt runs paho's blocking connect in an executor thread, and
        canceling the await does not stop the thread. A disconnect sent while
        the thread runs has no effect, because paho's connect resets the
        client's state. So the thread checks ``abandoned`` once the connect
        returns.
        """
        connect = getattr(client, "_client_connect", None)
        if not callable(connect):
            return
        loop = asyncio.get_running_loop()

        def connect_then_check() -> None:
            connect()
            if not abandoned.is_set():
                return
            try:
                loop.call_soon_threadsafe(self._abandon_client, client)
            except RuntimeError:
                # The event loop is closed. Close the socket from this thread.
                with contextlib.suppress(Exception):
                    client._client.socket().close()  # pylint: disable=protected-access

        client._client_connect = connect_then_check  # pylint: disable=protected-access

    @staticmethod
    def _abandon_client(client: Any) -> None:
        """Close the socket of a client whose connect did not complete. Errors are ignored.

        aiomqtt must not be exited after a failed enter, but a connect that
        timed out waiting for the broker's answer can leave its socket open.
        """
        with contextlib.suppress(Exception):
            client._client.disconnect()  # pylint: disable=protected-access

    async def _discard_client(self) -> None:
        """Close the current client and drop it. Errors and slow closes are ignored."""
        client, self._client = self._client, None
        if client is None:
            return
        lost = getattr(client, "_disconnected", None)
        if isinstance(lost, asyncio.Future) and lost.done() and not lost.cancelled():
            # Retrieve the error so asyncio does not log it as never retrieved.
            lost.exception()
        try:
            await asyncio.wait_for(
                client.__aexit__(None, None, None), timeout=_CLOSE_TIMEOUT_SECONDS
            )
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.debug("Error closing MQTT client: %s", e)

    def _report_connect_failure(self, exc: BaseException) -> None:
        """Report a failed connect at start as a diagnostics error and a failure event."""
        from py20305.diagnostics import report

        endpoint = self._config.endpoint
        port = self._config.port
        report(
            "errors",
            f"Failed to connect MQTT forwarder to {endpoint}:{port}: {exc}",
            source="mqtt",
            dedup_key=self._connect_key,
            details={"endpoint": endpoint, "port": port, "error": str(exc)},
        )
        self._connected = False
        self._report_broker_failure(exc)

    @property
    def _connect_key(self) -> str:
        """Return the diagnostics dedup key for a failed connect at start."""
        return f"mqtt_connect:{self._config.endpoint}:{self._config.port}"

    @property
    def _link_key(self) -> str:
        """Return the diagnostics dedup key for a lost or unreachable broker."""
        return f"mqtt_link:{self._config.endpoint}:{self._config.port}"

    @property
    def _refused_key(self) -> str:
        """Return the diagnostics dedup key for a broker refusing the credentials."""
        return f"mqtt_refused:{self._config.endpoint}:{self._config.port}"

    @property
    def _broker(self) -> str:
        """Return the broker as ``host:port`` for log messages."""
        return f"{self._config.endpoint}:{self._config.port}"

    @staticmethod
    def _describe(reason: BaseException | str) -> str:
        """Return a one-line reason for a log message or a statistic."""
        if isinstance(reason, str):
            return reason
        return str(reason) or type(reason).__name__

    def _begin_outage(self) -> None:
        """Record that the first connect failed, so the publish loop starts retrying."""
        now = self._clock()
        self._down_since = now
        self._attempts = 1
        self._backoff = self._config.reconnect_initial_delay_seconds
        self._last_failure_report = now
        self._restore_unreported = True
        self._refusal_reported = False

    def _link_down(self, reason: BaseException | str) -> None:
        """Mark the broker connection lost and report the loss.

        Does nothing when the link is already down, so a loss seen both by a
        publish and by the client counts and reports once. The loss of a
        stable connection starts a new outage. The loss of a connection that
        was not yet stable continues the last outage: the backoff keeps
        growing and the loss is reported only at the reminder rate.
        """
        if not self._connected:
            return
        from py20305.diagnostics import report

        self._connected = False
        text = self._describe(reason)
        now = self._clock()
        stable = (
            self._link_stable
            or now - self._connected_at >= self._config.reconnect_max_delay_seconds
        )
        self._down_since = now
        self._attempts = 0
        self._stats["disconnects"] += 1
        self._stats["last_disconnect_reason"] = text
        if stable:
            self._backoff = self._config.reconnect_initial_delay_seconds
            self._refusal_reported = False
        else:
            self._backoff = min(
                self._config.reconnect_max_delay_seconds,
                max(self._config.reconnect_initial_delay_seconds, self._backoff * 2),
            )
            if self._report_throttled(now):
                logger.debug(
                    "MQTT broker connection to %s dropped again before it was stable: %s",
                    self._broker,
                    text,
                )
                return
        self._last_failure_report = now
        self._restore_unreported = True
        report(
            "warnings",
            f"MQTT broker connection to {self._broker} lost: {text}. Reconnecting.",
            source="mqtt",
            dedup_key=self._link_key,
            details={
                "endpoint": self._config.endpoint,
                "port": self._config.port,
                "error": text,
            },
        )
        self._report_broker_failure(reason)

    def _client_reported_loss(self) -> BaseException | str | None:
        """Return why the client says its connection closed, or None while it is open.

        aiomqtt resolves the client's ``_disconnected`` future when the socket
        closes, with the error as its exception. A client without one is
        checked through publish errors only.
        """
        lost = getattr(self._client, "_disconnected", None)
        if not isinstance(lost, asyncio.Future) or not lost.done():
            return None
        if lost.cancelled():
            return "the connection closed"
        return lost.exception() or "the connection closed"

    async def _reconnect(self) -> None:
        """Connect again, waiting with exponential backoff between attempts.

        Returns once connected, or when shutdown starts.
        """
        await self._discard_client()
        while not self._shutdown_event.is_set():
            delay = self._backoff * (1.0 - _RECONNECT_JITTER * self._jitter())
            # stop() cancels this task while the link is down, which ends the wait.
            await self._sleep(delay)
            if self._shutdown_event.is_set():
                return
            try:
                # Load the TLS files again, so a rotated certificate is used.
                self._ssl_context = self._create_ssl_context()
                await self._connect()
            except Exception as e:  # pylint: disable=broad-exception-caught
                # The loop must keep retrying, whatever an attempt raised.
                self._attempt_failed(e)
                continue
            self._link_restored()
            return

    def _report_throttled(self, now: float) -> bool:
        """Return whether a failure was reported less than the reminder interval ago."""
        return (
            self._last_failure_report is not None
            and now - self._last_failure_report < _FAILURE_REPORT_INTERVAL_SECONDS
        )

    @staticmethod
    def _is_auth_refusal(exc: BaseException) -> bool:
        """Return whether ``exc`` is a CONNACK that refused the credentials or authorization.

        aiomqtt raises a refused CONNACK with paho's ``ReasonCode`` as ``rc``.
        Other connect errors carry no reason code.
        """
        rc = getattr(exc, "rc", None)
        return getattr(rc, "value", None) in _AUTH_REFUSAL_REASON_CODES

    def _attempt_failed(self, exc: BaseException) -> None:
        """Count one failed reconnect attempt, grow the backoff, and report at a throttled rate.

        The first refusal for credentials or authorization in an outage is
        reported at error level at once, because retrying does not fix it
        until the credentials or the broker's policy change.
        """
        self._attempts += 1
        self._backoff = min(self._config.reconnect_max_delay_seconds, self._backoff * 2)
        text = self._describe(exc)
        logger.debug(
            "MQTT reconnect attempt %d to %s failed: %s", self._attempts, self._broker, text
        )
        now = self._clock()
        refused = self._is_auth_refusal(exc)
        first_refusal = refused and not self._refusal_reported
        if self._report_throttled(now) and not first_refusal:
            return
        from py20305.diagnostics import report

        if refused:
            self._refusal_reported = True
        self._last_failure_report = now
        self._restore_unreported = True
        down_for = now - (self._down_since if self._down_since is not None else now)
        if refused:
            summary = f"MQTT broker {self._broker} refused the connection"
        else:
            summary = f"MQTT broker {self._broker} still unreachable"
        report(
            "errors" if refused else "warnings",
            f"{summary} after {self._attempts} attempts over {down_for:.0f} s: {text}",
            source="mqtt",
            dedup_key=self._refused_key if refused else self._link_key,
            details={
                "endpoint": self._config.endpoint,
                "port": self._config.port,
                "attempts": self._attempts,
                "error": text,
            },
        )
        self._report_broker_failure(exc)

    def _link_restored(self) -> None:
        """Record a connection made by the background retry, and report it.

        The backoff and the report throttle are kept until the connection is
        stable, so a broker that drops the connection right after accepting
        it does not reset them. The reconnect is reported only when a failure
        was reported since the last reported reconnect.
        """
        from py20305.diagnostics import report, resolve

        now = self._clock()
        down_for = now - (self._down_since if self._down_since is not None else now)
        attempts = self._attempts + 1
        self._stats["reconnects"] += 1
        self._down_since = None
        self._attempts = 0
        self._link_stable = False
        if not self._restore_unreported:
            logger.debug(
                "MQTT broker connection to %s restored after %.1f s and %d connection attempts",
                self._broker,
                down_for,
                attempts,
            )
            return
        self._restore_unreported = False
        resolve(self._link_key)
        resolve(self._connect_key)
        resolve(self._refused_key)
        report(
            "info",
            f"MQTT broker connection to {self._broker} restored after {down_for:.1f} s "
            f"and {attempts} connection attempts",
            source="mqtt",
            dedup_key=f"mqtt_link_restored:{self._config.endpoint}:{self._config.port}",
            details={
                "endpoint": self._config.endpoint,
                "port": self._config.port,
                "downtime_seconds": round(down_for, 1),
                "attempts": attempts,
            },
        )
        self._report_broker_connected()

    def _broker_endpoints(self) -> tuple[Endpoint | None, Endpoint | None]:
        """Return the broker connection's local and remote endpoints.

        Read from the live socket when it is reachable, so the record carries
        the local port. The remote endpoint keeps the configured host name
        beside the address it resolved to. Falls back to the configured broker
        when the socket cannot be read.
        """
        configured = endpoint_for_host(self._config.endpoint, self._config.port)
        try:
            sock = self._client._client.socket()
            local = sock.getsockname()
            remote = sock.getpeername()
        except Exception:
            return None, configured
        hostname = configured.hostname if configured is not None else None
        return (
            Endpoint(ip=local[0], port=local[1]),
            Endpoint(ip=remote[0], hostname=hostname, port=remote[1]),
        )

    def _report_broker_connected(self) -> None:
        """Report a successful broker connection to the connection observer."""
        observer = self.connection_observer
        if observer is None:
            return
        try:
            src, dst = self._broker_endpoints()
            observer.record_connection(
                role=ConnectionRole.TELEMETRY, service=SERVICE_MQTT, src=src, dst=dst
            )
        except Exception:
            logger.debug("Could not report the broker connection", exc_info=True)

    def _report_broker_failure(self, exc: BaseException | str) -> None:
        """Report a failed broker connection, with its reason, to the connection observer."""
        observer = self.connection_observer
        if observer is None:
            return
        try:
            observer.record_connection_failure(
                role=ConnectionRole.TELEMETRY,
                service=SERVICE_MQTT,
                reason=exc,
                dst=endpoint_for_host(self._config.endpoint, self._config.port),
            )
        except Exception:
            logger.debug("Could not report the broker connection failure", exc_info=True)

    async def stop(self) -> None:
        """Stop the forwarder gracefully.

        Signals shutdown, drains the buffers while the broker connection is
        up, then disconnects. While the link is down there is nothing to
        drain into, so a backoff wait or a reconnect attempt in progress is
        canceled and stop returns without waiting for it. A connect that
        completes after that is closed as soon as its thread returns.
        """
        if not self._running:
            return

        logger.info("Stopping MQTT forwarder (queue size: %d)", self._buffered())

        self._running = False
        self._shutdown_event.set()
        self._wakeup.set()

        task, self._publish_task = self._publish_task, None
        if task is not None:
            if not self._connected:
                task.cancel()
            try:
                await asyncio.wait_for(task, timeout=_DRAIN_TIMEOUT_SECONDS)
            except TimeoutError:
                from py20305.diagnostics import report

                report(
                    "warnings",
                    "MQTT publish task did not drain within timeout on shutdown",
                    source="mqtt",
                )
            except asyncio.CancelledError:
                # The task was canceled above. Re-raise only when stop()
                # itself is being canceled.
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise

        unpublished = self._buffered()
        if unpublished:
            logger.warning(
                "MQTT forwarder stopped with %d items not published to %s",
                unpublished,
                self._broker,
            )

        await self._discard_client()
        self._connected = False
        self._backoff = 0.0
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

        Held newest-per-device in its own buffer rather than on the capture
        queue. A second frame for a device supersedes the first -- a monitoring
        upstream wants the current reading, not a backlog of stale ones -- and
        a slow broker therefore costs a device its intermediate samples and
        never costs anyone a captured exchange.

        Counted as telemetry rather than as a message, for the same reason
        events are: one number that conflates the two says nothing about either.
        """
        if not self._accepting():
            return

        device = frame.device
        if device in self._telemetry_pending:
            # Assigning to an existing key keeps its position, so a device
            # reporting often cannot keep overtaking a quiet one in the drain.
            self._telemetry_pending[device] = frame
            self._stats["telemetry_superseded"] = self._stats.get("telemetry_superseded", 0) + 1
            self._wakeup.set()
            return

        if len(self._telemetry_pending) >= self._telemetry_device_limit:
            from py20305.diagnostics import report

            report(
                "warnings",
                "MQTT telemetry buffer at its device limit, dropping frame",
                source="mqtt",
                dedup_key="mqtt_telemetry_devices_full",
                details={
                    "endpoint": self._config.endpoint,
                    "device_limit": self._telemetry_device_limit,
                },
            )
            self._stats["telemetry_dropped"] = self._stats.get("telemetry_dropped", 0) + 1
            return

        self._telemetry_pending[device] = frame
        self._stats["telemetry_queued"] = self._stats.get("telemetry_queued", 0) + 1
        self._wakeup.set()

    def _accepting(self) -> bool:
        """Whether the forwarder is in a state to buffer anything at all."""
        if not self._running:
            logger.debug("MQTT forwarder not running, dropping message")
            return False

        if self._shutdown_event.is_set():
            logger.debug("MQTT forwarder shutting down, dropping message")
            return False

        return True

    def _enqueue(
        self, item: MessageFrame | EventFrame, *, counter: str = "messages_queued"
    ) -> None:
        """Put one captured item on the publish queue, dropping the oldest when full.

        ``counter`` names the statistic to credit, so an event is counted as an
        event rather than inflating the message count as well.
        """
        if not self._accepting():
            return

        try:
            self._capture_queue.put_nowait(item)
            self._stats[counter] = self._stats.get(counter, 0) + 1
            self._wakeup.set()
        except asyncio.QueueFull:
            from py20305.diagnostics import get_store, report

            # Single shared dedup key: backpressure is one ongoing condition;
            # the entry's count tells the operator how often the broker is
            # too slow rather than producing one entry per dropped message.
            # The warning is logged once until the queue drains again: during
            # a broker outage every new item is a drop, and logging each one
            # would flood the log.
            message = "MQTT queue full, dropping oldest message"
            details = {"endpoint": self._config.endpoint}
            if not self._queue_full_reported:
                self._queue_full_reported = True
                report(
                    "warnings", message, source="mqtt", dedup_key="mqtt_queue_full", details=details
                )
            else:
                logger.debug(message)
                store = get_store()
                if store is not None:
                    store.add(
                        "warnings",
                        message,
                        source="mqtt",
                        dedup_key="mqtt_queue_full",
                        details=details,
                    )
            try:
                self._capture_queue.get_nowait()  # Drop oldest
                self._stats["messages_dropped"] = self._stats.get("messages_dropped", 0) + 1
                self._capture_queue.put_nowait(item)
                self._stats[counter] = self._stats.get(counter, 0) + 1
            except asyncio.QueueEmpty:
                pass

    def _buffered(self) -> int:
        """Return how many items are waiting across both buffers and the retry slot."""
        retry = 1 if self._retry_item is not None else 0
        return self._capture_queue.qsize() + len(self._telemetry_pending) + retry

    async def _publish_loop(self) -> None:
        """Background task that publishes buffered items, capture first.

        Each pass drains up to ``_CAPTURE_RUN`` captured items and then up to
        ``_TELEMETRY_SLICE`` telemetry frames. Capture keeps priority because
        it is the payload nothing will send again, while the bound on the run
        means a client under sustained protocol load still reports measurements
        -- and since telemetry coalesces, starving it would leave the upstream
        with no reading at all for that period rather than a delayed one.

        The loop also owns the broker connection: while the link is down it
        reconnects instead of publishing, and on stop it drains only while
        the link is up.
        """
        message_topic = f"{self._config.topic_base}/{PROTOCOL_MESSAGE_TOPIC_SUFFIX}"
        # Its own topic rather than a shared one with a type marker inside: a
        # subscriber that wants only measurements, or only capture, should be able
        # to say so at the broker instead of filtering every message on arrival.
        telemetry_topic = f"{self._config.topic_base}/{TELEMETRY_TOPIC_SUFFIX}"

        while True:
            try:
                if not self._running and (not self._connected or not self._buffered()):
                    break

                if not self._connected:
                    await self._reconnect()
                    continue

                lost = self._client_reported_loss()
                if lost is not None:
                    self._link_down(lost)
                    continue

                if await self._publish_batch(message_topic, telemetry_topic):
                    continue
                if not self._connected:
                    continue

                # Nothing buffered. Wait for an arrival, shutdown, or a lost
                # connection, and check again after a second in case a wakeup
                # was missed.
                await self._wait_for_work()

            except asyncio.CancelledError:
                # Re-raised so a caller awaiting this task sees the cancellation.
                logger.debug("Publish loop canceled")
                raise
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

    async def _publish_batch(self, message_topic: str, telemetry_topic: str) -> int:
        """Publish one bounded run of buffered items and return how many were taken.

        Stops early when the broker connection is lost. The failed item is
        kept for the next run.
        """
        published = 0

        if self._retry_item is not None:
            item, self._retry_item = self._retry_item, None
            if not await self._publish_item(item, message_topic, telemetry_topic):
                return published
            published += 1

        while published < _CAPTURE_RUN:
            try:
                item = self._capture_queue.get_nowait()
            except asyncio.QueueEmpty:
                # The queue drained, so the next drop logs its warning again.
                self._queue_full_reported = False
                break
            if not await self._publish_item(item, message_topic, telemetry_topic):
                return published
            published += 1

        for _ in range(_TELEMETRY_SLICE):
            if not self._telemetry_pending:
                break
            # Oldest pending device first; a superseding frame kept its
            # device's place, so no device can overtake another.
            device = next(iter(self._telemetry_pending))
            frame = self._telemetry_pending.pop(device)
            if not await self._publish_item(frame, message_topic, telemetry_topic):
                return published
            published += 1

        return published

    async def _wait_for_work(self) -> None:
        """Wait until an item arrives, shutdown starts, or the client reports a lost connection.

        Returns after ``_IDLE_WAIT_SECONDS`` at most.
        """
        arrival = asyncio.ensure_future(self._wakeup.wait())
        waiters: set[asyncio.Future[Any]] = {arrival}
        lost = getattr(self._client, "_disconnected", None)
        if isinstance(lost, asyncio.Future):
            # Waited on, never canceled: the future belongs to the client.
            waiters.add(lost)
        try:
            await asyncio.wait(
                waiters, timeout=_IDLE_WAIT_SECONDS, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            arrival.cancel()
            await asyncio.gather(arrival, return_exceptions=True)
        self._wakeup.clear()

    def _keep_for_retry(self, item: MessageFrame | TelemetryFrame | EventFrame) -> None:
        """Keep an item whose publish failed on a lost connection, to publish after reconnect.

        A telemetry frame goes back to the front of the pending map, where it
        was taken from, unless a newer frame for its device arrived in the
        meantime, which supersedes it. It is dropped when frames for other
        devices filled the map to its device limit in the meantime.
        """
        if isinstance(item, TelemetryFrame):
            if item.device in self._telemetry_pending:
                self._stats["telemetry_superseded"] += 1
            elif len(self._telemetry_pending) >= self._telemetry_device_limit:
                self._stats["telemetry_dropped"] += 1
            else:
                self._telemetry_pending = {item.device: item, **self._telemetry_pending}
            return
        self._retry_item = item

    def _is_failing_item(self, item: MessageFrame | TelemetryFrame | EventFrame) -> bool:
        """Return whether ``item`` is the item whose last publish lost the link.

        A telemetry frame matches any frame for the same device, since a newer
        frame takes the older one's place.
        """
        last = self._failing_item
        if last is item:
            return True
        return (
            isinstance(item, TelemetryFrame)
            and isinstance(last, TelemetryFrame)
            and last.device == item.device
        )

    def _count_link_failure(self, item: MessageFrame | TelemetryFrame | EventFrame) -> int:
        """Count a publish of ``item`` that lost the link, and return the count in a row."""
        same = self._is_failing_item(item)
        self._failing_item = item
        self._failing_attempts = self._failing_attempts + 1 if same else 1
        return self._failing_attempts

    def _drop_failing_item(self, topic: str, error: BaseException) -> None:
        """Drop the item whose last publish attempts all lost the link, and report it."""
        from py20305.diagnostics import report

        attempts = self._failing_attempts
        text = self._describe(error)
        self._failing_item = None
        self._failing_attempts = 0
        report(
            "warnings",
            f"Dropped an MQTT message for topic {topic} after {attempts} publish attempts "
            f"that each lost the connection to {self._broker}: {text}",
            source="mqtt",
            dedup_key=f"mqtt_publish:{self._config.endpoint}:dropped",
            details={
                "endpoint": self._config.endpoint,
                "kind": "dropped",
                "topic": topic,
                "attempts": attempts,
                "error": text,
            },
        )

    async def _publish_item(
        self,
        item: MessageFrame | TelemetryFrame | EventFrame,
        message_topic: str,
        telemetry_topic: str,
    ) -> bool:
        """Convert one buffered item and publish it on the topic for its kind.

        Returns False when the broker connection is lost. The link is then
        marked down and the item is kept for the next attempt, unless the
        last ``_MAX_PUBLISH_ATTEMPTS`` attempts to publish it all lost the
        link. The item is then dropped. Any other failure drops the item,
        records the error, and returns True.
        """
        # An event is already in its wire form and carries its own topic; a
        # message frame is converted and goes on the protocol-message topic.
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
            return True

        if self._client is None:
            self._keep_for_retry(item)
            self._link_down("no client")
            return False

        try:
            await self._client.publish(
                topic,
                payload_json.encode("utf-8"),
                qos=1,
            )
        except Exception as e:
            if isinstance(e, self._link_errors):
                # The type decides, not the message text. aiomqtt reports a
                # closed connection as "The client is not currently connected."
                self._record_error(f"Publish error: {e}")
                if self._count_link_failure(item) >= _MAX_PUBLISH_ATTEMPTS:
                    self._drop_failing_item(topic, e)
                else:
                    self._keep_for_retry(item)
                self._link_down(e)
                return False

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
            return True

        self._record_success()
        self._link_stable = True
        if self._is_failing_item(item):
            self._failing_item = None
            self._failing_attempts = 0
        logger.debug("Published message to %s", topic)
        return True

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

        Fallback for a forwarder used without an envelope adapter; an adapter
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
                # Unchanged meaning: everything buffered, across both kinds.
                "queue_size": self._buffered(),
                "capture_queue_size": self._capture_queue.qsize(),
                "telemetry_pending": len(self._telemetry_pending),
                "topic": f"{self._config.topic_base}/{PROTOCOL_MESSAGE_TOPIC_SUFFIX}",
                # Base delay before the next reconnect attempt; 0 while connected.
                "reconnect_backoff_seconds": self._backoff if not self._connected else 0.0,
            }
        )
        return stats
