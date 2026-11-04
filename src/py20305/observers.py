"""Structured records of what the client does on the wire, for the host application.

The client logs what happens to its exchanges and connections. A log line is
for a person. A host application that keeps a record of every exchange, that
passes the client's connections to a monitoring system, or that watches for
protocol anomalies needs the same facts as fields. This module defines those
records and the type of the callable that receives each one.

- :class:`ExchangeRecord` is one HTTP exchange: a request the client sent and
  the response it received, a request that received no response, or a
  notification the server POSTed to the client's listener.
- :class:`ConnectionEvent` is one thing that happened to a connection: the
  client connected, failed to connect or lost a connection, or the listener
  accepted or refused a notification request.
- :class:`ProtocolAnomaly` is one body the client could not use, one response
  with a status it did not expect, or one notification it dropped.
- :class:`DeviceRecord` is one read from or write to a device through a
  connector.
- :class:`AuditRecord` is one event lifecycle, loss-of-communications or late
  dispatch record of the control audit trail.

Every observer is optional and off by default. Attach the protocol observers
with :class:`Observers` (``CsipClient.attach_observers``), the device observer
through :class:`~py20305.connectors.device_telemetry.DeviceTelemetryEmitter`,
and the audit observer with ``CsipClient.audit.attach_observer``.

Stability. These records are a public contract:

- Every record carries ``record_version``, currently :data:`RECORD_VERSION`.
- Fields are only added. A field is removed only after one minor release in
  which reading it raises a :class:`DeprecationWarning`, and only in the next
  major release. ``record_version`` is raised when a field is removed or its
  meaning changes.
- Records are frozen dataclasses with keyword-only fields. Build them by
  keyword, and read them by name, so an added field breaks nothing.

An observer is called on the event loop, or for a device record on whatever
thread called the connector, and must return quickly. Hand records to
:class:`QueuedObserver` to process them on a background thread. An observer
that raises does not affect the exchange: :class:`ObserverGuard` catches the
exception, counts it and logs it.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Generic, TypeVar

logger = logging.getLogger(__name__)

#: The version of every record shape in this module.
RECORD_VERSION = 1

#: Header names whose values are replaced by :data:`REDACTED` by default.
DEFAULT_REDACTED_HEADERS: frozenset[str] = frozenset({"authorization", "cookie", "set-cookie"})

#: The value a redacted header carries.
REDACTED = "[redacted]"

#: Headers as an ordered tuple of ``(name, value)`` pairs, as sent or received.
Headers = tuple[tuple[str, str], ...]


def redact_headers(
    headers: Iterable[tuple[str, str]], redacted: Iterable[str] = DEFAULT_REDACTED_HEADERS
) -> Headers:
    """Return ``headers`` with the value of each header named in ``redacted`` replaced.

    Names are compared without regard to case. Order and repeated headers are
    kept.
    """
    names = {name.lower() for name in redacted}
    return tuple(
        (str(name), REDACTED if str(name).lower() in names else str(value))
        for name, value in headers
    )


@dataclass(frozen=True)
class Endpoint:
    """An address and port at one end of a connection."""

    host: str
    port: int

    @classmethod
    def of(cls, name: Any) -> Endpoint | None:
        """Return the endpoint of a socket name, or None when the name holds none.

        ``name`` is what ``getpeername`` or ``getsockname`` returns: a tuple
        whose first two items are the address and the port.
        """
        if isinstance(name, tuple) and len(name) >= 2:
            try:
                return cls(str(name[0]), int(name[1]))
            except (TypeError, ValueError):
                return None
        return None

    def __str__(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"


class ExchangeDirection(StrEnum):
    """Which end started an exchange."""

    #: The client sent the request to the server.
    CLIENT_REQUEST = "client_request"
    #: The server POSTed a notification to the client's listener.
    NOTIFICATION = "notification"


class ErrorKind(StrEnum):
    """Why an exchange received no response, or a connection failed. The values are stable."""

    #: The TCP connection could not be made.
    CONNECT = "connect"
    #: The TLS handshake or the certificate check failed.
    TLS = "tls"
    #: No answer arrived in time.
    TIMEOUT = "timeout"
    #: The connection closed before the response was complete.
    DISCONNECTED = "disconnected"
    #: The request URL is not valid.
    INVALID_URL = "invalid_url"
    #: Another failure.
    OTHER = "other"


@dataclass(frozen=True, kw_only=True)
class ExchangeRecord:
    """One HTTP exchange.

    A request the client sent is reported once per attempt, retries included,
    with the response it received or, when none arrived, with ``error_kind``
    and ``error``. A notification is reported once, with the response the
    listener sent back.

    Attributes:
        record_version: The record shape version.
        exchange_id: A unique identifier of this exchange. It correlates the
            request with its response, and anomalies with the exchange.
        direction: Which end started the exchange.
        method: The HTTP method.
        url: The full request URL. For a notification, the URL the listener
            received the request on.
        path: The path and query of the request.
        request_headers: The request headers, with the redacted ones replaced.
        request_body: The request body exactly as sent, or None when the
            request had no body or body capture is off.
        status: The response status, or None when no response arrived.
        response_headers: The response headers, with the redacted ones replaced.
        response_body: The response body exactly as received, or None when
            there was no response, it had no body, or body capture is off.
        sent_at: When the request was sent, in seconds since the Unix epoch.
        received_at: When the response arrived, in seconds since the Unix
            epoch, or None.
        sent_monotonic: ``time.monotonic()`` when the request was sent.
        received_monotonic: ``time.monotonic()`` when the response arrived, or
            None.
        duration: Seconds from sending the request to receiving the response,
            or to the failure. None when unknown.
        attempt: The attempt number within one logical request, from 1.
        error_kind: Why no response arrived, or None.
        error: The failure, in a few words, or None.
        local: This end of the connection, or None when unknown.
        remote: The other end of the connection, or None when unknown.
        server_host: The IEEE 2030.5 server's host name as configured.
        tls_version: The negotiated TLS version, or None.
        tls_cipher: The negotiated cipher suite, or None.
        server_cert_sha256: The SHA-256 fingerprint of the certificate the peer
            presented, as lowercase hex: the server's for a client request,
            the notifying server's client certificate for a notification. None
            when there was none or it could not be read.
        client_lfdi: The LFDI of this client's certificate, or None.
        client_sfdi: The SFDI of this client's certificate, or None.
        device_lfdi: The LFDI of the device the exchange concerns, when the
            client can attribute it, or None.
        end_device_href: The href of that device's EndDevice, or None.
        resource_type: The IEEE 2030.5 element of the body, such as
            ``DERControlList``, or None.
        request_valid: Whether the request body passed schema validation, or
            None when it was not checked.
        request_validation_error: The first validation error, or None.
        response_valid: Whether the response body passed schema validation,
            or None when it was not checked.
        response_validation_error: The first validation error, or None.
    """

    record_version: int = RECORD_VERSION
    exchange_id: str
    direction: ExchangeDirection
    method: str
    url: str
    path: str
    request_headers: Headers = ()
    request_body: bytes | None = None
    status: int | None = None
    response_headers: Headers = ()
    response_body: bytes | None = None
    sent_at: float
    received_at: float | None = None
    sent_monotonic: float
    received_monotonic: float | None = None
    duration: float | None = None
    attempt: int = 1
    error_kind: ErrorKind | None = None
    error: str | None = None
    local: Endpoint | None = None
    remote: Endpoint | None = None
    server_host: str = ""
    tls_version: str | None = None
    tls_cipher: str | None = None
    server_cert_sha256: str | None = None
    client_lfdi: str | None = None
    client_sfdi: int | None = None
    device_lfdi: str | None = None
    end_device_href: str | None = None
    resource_type: str | None = None
    request_valid: bool | None = None
    request_validation_error: str | None = None
    response_valid: bool | None = None
    response_validation_error: str | None = None


class ConnectionRole(StrEnum):
    """Which part of the client reported a connection event."""

    #: The client's connection to the IEEE 2030.5 server.
    CLIENT = "client"
    #: The notification listener, which the server connects to.
    LISTENER = "listener"


class ConnectionDirection(StrEnum):
    """Which end opened the connection, seen from the client."""

    #: The remote end opened it. Every connection the listener reports.
    INBOUND = "inbound"
    #: The client opened it. Every connection the client reports.
    OUTBOUND = "outbound"


class ConnectionTransport(StrEnum):
    """What the connection is carried over."""

    TCP = "tcp"
    TLS = "tls"


class ConnectionOutcome(StrEnum):
    """What happened to a connection. The values are stable."""

    #: The client made the connection, and the TLS handshake and the IEEE
    #: 2030.5 certificate profile check passed.
    CONNECTED = "connected"
    #: The client could not make the connection: the TCP connection, the TLS
    #: handshake or the certificate check failed.
    FAILED = "failed"
    #: The client lost a connection while an exchange was in progress.
    LOST = "lost"
    #: The listener received a notification request and admitted it.
    ACCEPTED = "accepted"
    #: The listener received a notification request and refused it.
    REFUSED = "refused"


@dataclass(frozen=True, kw_only=True)
class ConnectionEvent:
    """One thing that happened to one connection.

    Attributes:
        record_version: The record shape version.
        role: The part of the client that reports the event.
        direction: Which end opened the connection.
        outcome: What happened.
        transport: TCP, or TLS over TCP.
        remote: The other end. For a failed attempt, the host and port tried.
        local: This end, or None when no socket was made.
        server_host: The IEEE 2030.5 server's host name as configured, for the
            client role. Empty for the listener.
        reason: Why, in a few words, for every outcome except ``CONNECTED`` and
            ``ACCEPTED``. It holds no secret.
        error_kind: The kind of failure, for ``FAILED`` and ``LOST``.
        status: The HTTP status the listener answered a refused request with.
        tls_version: The negotiated TLS version, or None.
        tls_cipher: The negotiated cipher suite, or None.
        peer_cert_sha256: The SHA-256 fingerprint of the certificate the peer
            presented, as lowercase hex, or None.
        timestamp: When the event was reported, in seconds since the Unix
            epoch.
    """

    record_version: int = RECORD_VERSION
    role: ConnectionRole
    direction: ConnectionDirection
    outcome: ConnectionOutcome
    transport: ConnectionTransport
    remote: Endpoint | None
    local: Endpoint | None = None
    server_host: str = ""
    reason: str = ""
    error_kind: ErrorKind | None = None
    status: int | None = None
    tls_version: str | None = None
    tls_cipher: str | None = None
    peer_cert_sha256: str | None = None
    timestamp: float = field(default_factory=time.time)


class AnomalyCode(StrEnum):
    """What the client could not use or did not expect. The values are stable."""

    #: A body that is not well-formed XML or not the element expected.
    UNPARSEABLE_BODY = "unparseable_body"
    #: A body that failed schema validation. It was still used.
    INVALID_BODY = "invalid_body"
    #: A response status the client did not expect for the request.
    UNEXPECTED_STATUS = "unexpected_status"
    #: A notification request whose body could not be read.
    UNREADABLE_NOTIFICATION = "unreadable_notification"
    #: A notification the listener refused: no client certificate, or a
    #: body that failed its checks.
    REJECTED_NOTIFICATION = "rejected_notification"
    #: A notification the listener accepted and did not act on, while a
    #: loss-of-communications simulation runs.
    DROPPED_NOTIFICATION = "dropped_notification"


class AnomalySeverity(StrEnum):
    """How much an anomaly says about the peer, from least to most."""

    #: Expected in normal operation, and recorded for completeness.
    INFO = "info"
    #: Explained by a server's ordinary behavior as easily as by a fault.
    LOW = "low"
    #: A malformed body or an unexpected answer: a defective or
    #: misconfigured peer, or one probing the client.
    MEDIUM = "medium"


#: The severity each anomaly code is reported with.
SEVERITY: Mapping[AnomalyCode, AnomalySeverity] = MappingProxyType(
    {
        AnomalyCode.UNPARSEABLE_BODY: AnomalySeverity.MEDIUM,
        AnomalyCode.INVALID_BODY: AnomalySeverity.LOW,
        AnomalyCode.UNEXPECTED_STATUS: AnomalySeverity.LOW,
        AnomalyCode.UNREADABLE_NOTIFICATION: AnomalySeverity.MEDIUM,
        AnomalyCode.REJECTED_NOTIFICATION: AnomalySeverity.MEDIUM,
        AnomalyCode.DROPPED_NOTIFICATION: AnomalySeverity.INFO,
    }
)


@dataclass(frozen=True, kw_only=True)
class ProtocolAnomaly:
    """One body the client could not use, unexpected status, or dropped notification.

    Attributes:
        record_version: The record shape version.
        code: What happened.
        severity: How much it says about the peer. Always ``SEVERITY[code]``.
        message: What happened, in a few words.
        exchange_id: The :class:`ExchangeRecord` the anomaly belongs to, or
            None when no exchange record was made.
        method: The HTTP method of the exchange, or None.
        path: The path of the exchange, or None.
        status: The response status, or None.
        peer: The other end of the connection, when known.
        timestamp: When the anomaly was reported, in seconds since the Unix
            epoch.
    """

    record_version: int = RECORD_VERSION
    code: AnomalyCode
    severity: AnomalySeverity
    message: str
    exchange_id: str | None = None
    method: str | None = None
    path: str | None = None
    status: int | None = None
    peer: Endpoint | None = None
    timestamp: float = field(default_factory=time.time)

    @classmethod
    def of(cls, code: AnomalyCode, message: str, **fields: Any) -> ProtocolAnomaly:
        """Return an anomaly with the severity its code is reported with."""
        return cls(code=code, severity=SEVERITY[code], message=message, **fields)


class DeviceOperation(StrEnum):
    """What a device record reports."""

    READ = "read"
    WRITE = "write"


class DeviceOutcome(StrEnum):
    """How a device read or write ended."""

    #: The read or write completed.
    OK = "ok"
    #: The connector raised.
    FAILED = "failed"
    #: The write was not sent, because the command authority refused it.
    REFUSED = "refused"


@dataclass(frozen=True, kw_only=True)
class DeviceRecord:
    """One read from or write to a device through its connector.

    Attributes:
        record_version: The record shape version.
        operation: Read or write.
        device: The identifier the client knows the device by.
        lfdi: The device's LFDI, when known.
        resource: For a read, what was read: ``monitoring``, ``nameplate``,
            ``configuration``, ``status`` or ``availability``. For a write,
            the control written, in the same vocabulary the management API
            uses.
        values: For a read, the values read. For a write, the parameters
            written.
        outcome: How it ended.
        error: The failure, cut to :data:`MAX_ERROR_CHARS`, or None.
        error_type: The failure's exception class name, or None.
        origin: Who issued a write, such as ``ieee2030_5``, or None.
        applied_mrid: The mRID of the control written, as uppercase hex.
        cause_mrid: The mRID of the event whose lifecycle produced the write,
            as uppercase hex.
        protocol: What the connector speaks to the device, as the connector
            declares it (``modbus``, ``generic``, ...).
        endpoint: The connector's device address, such as ``host:port`` or
            ``rtu:/dev/ttyUSB0``, or None.
        timestamp: When the record was made, in seconds since the Unix epoch.
    """

    record_version: int = RECORD_VERSION
    operation: DeviceOperation
    device: str
    lfdi: str | None = None
    resource: str
    values: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    outcome: DeviceOutcome = DeviceOutcome.OK
    error: str | None = None
    error_type: str | None = None
    origin: str | None = None
    applied_mrid: str | None = None
    cause_mrid: str | None = None
    protocol: str = "generic"
    endpoint: str | None = None
    timestamp: float = field(default_factory=time.time)


#: Longest device error text a :class:`DeviceRecord` carries.
MAX_ERROR_CHARS = 512


@dataclass(frozen=True, kw_only=True)
class AuditRecord:
    """One record of the control audit trail.

    Attributes:
        record_version: The record shape version.
        kind: ``der_event``, ``comms_loss`` or ``late_dispatch``.
        data: The record's fields, the same document the operations history
            stores. See the audit trail section of the observers guide.
    """

    record_version: int = RECORD_VERSION
    kind: str
    data: Mapping[str, Any]


#: Receives each exchange record.
ExchangeObserver = Callable[[ExchangeRecord], None]

#: Receives each connection event.
ConnectionObserver = Callable[[ConnectionEvent], None]

#: Receives each protocol anomaly.
AnomalyObserver = Callable[[ProtocolAnomaly], None]

#: Receives each device record.
DeviceObserver = Callable[[DeviceRecord], None]

#: Receives each audit record.
AuditObserver = Callable[[AuditRecord], None]


@dataclass(frozen=True, kw_only=True)
class Observers:
    """The protocol observers of one client, and how exchanges are captured.

    Attributes:
        exchange: Receives each :class:`ExchangeRecord`, or None.
        connection: Receives each :class:`ConnectionEvent`, or None.
        anomaly: Receives each :class:`ProtocolAnomaly`, or None.
        capture_bodies: Put request and response bodies in exchange records.
            Off, ``request_body`` and ``response_body`` are None.
        redacted_headers: Header names whose values are replaced by
            :data:`REDACTED` in exchange records. Compared without regard to
            case.
        validate: Validate each body against the schemas and report the
            result in the exchange record. Runs only with an exchange or
            anomaly observer attached.
        schema_dir: Directory holding the schema files to validate with; the
            packaged schemas fill in any it lacks.
    """

    exchange: ExchangeObserver | None = None
    connection: ConnectionObserver | None = None
    anomaly: AnomalyObserver | None = None
    capture_bodies: bool = True
    redacted_headers: frozenset[str] = DEFAULT_REDACTED_HEADERS
    validate: bool = True
    schema_dir: Path | None = None

    @property
    def any(self) -> bool:
        """Whether any observer is attached."""
        return any(o is not None for o in (self.exchange, self.connection, self.anomaly))


#: No observers. The default of every client.
NO_OBSERVERS = Observers()

_Record = TypeVar("_Record")


class ObserverGuard:
    """Call an observer and keep its exceptions from reaching the caller.

    Each exception is counted in :attr:`failures`. The first exception of an
    observer is logged with its traceback. Later exceptions of the same
    observer are logged at DEBUG, so an observer that fails on every record
    does not fill the log. A different observer starts over.
    """

    def __init__(self, what: str) -> None:
        """Make a guard for the observer named ``what`` in log records."""
        self._what = what
        self._failed: object | None = None
        #: How many times the guarded observers raised.
        self.failures = 0

    def deliver(self, observer: Callable[[_Record], None] | None, record: _Record) -> None:
        """Call ``observer`` with ``record``, unless there is no observer."""
        if observer is None:
            return
        try:
            observer(record)
        except Exception:  # pylint: disable=broad-exception-caught
            # The observer belongs to the host application. Its failure says
            # nothing about the exchange, which goes on.
            self.failures += 1
            if self._failed is observer:
                logger.debug("the %s failed again", self._what, exc_info=True)
            else:
                self._failed = observer
                logger.exception("the %s failed; further failures are logged at DEBUG", self._what)


class QueuedObserver(Generic[_Record]):
    """Hand records to another observer on a background thread, through a bounded queue.

    Calling an instance puts the record on the queue and returns at once. A
    record that arrives while the queue is full is dropped and counted in
    :attr:`dropped`. The target runs on one daemon thread, in the order the
    records arrived; its exceptions are counted in :attr:`failures` and never
    reach the caller.

    Call :meth:`close` to deliver what is queued and stop the thread.
    """

    _STOP = object()

    def __init__(
        self, target: Callable[[_Record], None], *, maxsize: int = 10_000, name: str = "observer"
    ) -> None:
        """Start the thread that delivers to ``target``.

        Args:
            target: Receives each record.
            maxsize: Most records held; at least 1.
            name: Names the thread and the log records.
        """
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self._target = target
        self._queue: queue.Queue[object] = queue.Queue(maxsize=maxsize)
        self._guard = ObserverGuard(name)
        self._lock = threading.Lock()
        self._closed = False
        #: How many records were dropped because the queue was full or closed.
        self.dropped = 0
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    @property
    def failures(self) -> int:
        """How many times the target raised."""
        return self._guard.failures

    @property
    def pending(self) -> int:
        """How many records wait to be delivered."""
        return self._queue.qsize()

    def __call__(self, record: _Record) -> None:
        """Queue ``record`` for delivery, or drop and count it when the queue is full."""
        if self._closed:
            self._count_drop()
            return
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            self._count_drop()

    def close(self, timeout: float | None = 5.0) -> None:
        """Deliver the queued records, then stop the thread.

        Records that arrive after this call are dropped and counted. Waits up
        to ``timeout`` seconds for the thread; None waits until it finishes.
        """
        if self._closed:
            return
        self._closed = True
        # A blocking put: the stop marker has to reach the thread even when
        # the queue is full.
        self._queue.put(self._STOP)
        self._thread.join(timeout)

    def _count_drop(self) -> None:
        with self._lock:
            self.dropped += 1

    def _run(self) -> None:
        while True:
            record = self._queue.get()
            if record is self._STOP:
                return
            self._guard.deliver(self._target, record)  # type: ignore[arg-type]


__all__ = [
    "DEFAULT_REDACTED_HEADERS",
    "MAX_ERROR_CHARS",
    "NO_OBSERVERS",
    "RECORD_VERSION",
    "REDACTED",
    "SEVERITY",
    "AnomalyCode",
    "AnomalyObserver",
    "AnomalySeverity",
    "AuditObserver",
    "AuditRecord",
    "ConnectionDirection",
    "ConnectionEvent",
    "ConnectionObserver",
    "ConnectionOutcome",
    "ConnectionRole",
    "ConnectionTransport",
    "DeviceObserver",
    "DeviceOperation",
    "DeviceOutcome",
    "DeviceRecord",
    "Endpoint",
    "ErrorKind",
    "ExchangeDirection",
    "ExchangeObserver",
    "ExchangeRecord",
    "Headers",
    "ObserverGuard",
    "Observers",
    "ProtocolAnomaly",
    "QueuedObserver",
    "redact_headers",
]
