"""Connection telemetry -- reporting this client's own connection outcomes.

The forwarder already carries this client's captured IEEE 2030.5 exchanges to
a security-monitoring system, and the device-telemetry channel carries its
southbound reads and writes. This module adds the third channel: the client's
own connection outcomes. A network sensor on a span port can approximate the
connection attempts a monitoring platform wants logged. It cannot produce the
error log at all -- a certificate that fails validation, a redirect loop, a
500 from the server are outcomes this client knows and a capture cannot
recover from inside TLS.

This module turns those outcomes into OCSF Network Activity events (the
vendored ``ocsf`` module beside this one) and publishes them on their own
topic. What lives here is the mapping from this client's error taxonomy onto
the schema, and the coalescing the volume of a polling client requires.

Two rules come from the compliance argument rather than from the schema, and
the event class enforces both at construction:

- A failure carries its reason. That reason is the record's entire value.
- Failures are never coalesced. Successes may be collapsed into a window
  carrying bounds and a count; collapsing failures would discard the reasons.

The emitter implements the client's
:class:`~py20305.client.observer.ConnectionObserver` seam, so wiring it up is
one assignment: ``client.http.connection_observer = emitter``.

A host application can report its other links through the same emitter:
:meth:`ConnectionTelemetryEmitter.record_connection` and
:meth:`ConnectionTelemetryEmitter.record_connection_failure` take the link's
role, service, direction and endpoints. Every event carries one
``role:<role>`` label in ``metadata.labels`` (see :class:`ConnectionRole`)
and the connection's direction in ``connection_info.direction_id``, so a
consumer can tell the links apart.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import ssl
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from py20305.client.connector import SocketPair
from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2NoContentError,
    Sep2PayloadError,
    Sep2ProtocolError,
    Sep2RateLimitError,
    Sep2RedirectError,
    Sep2TlsError,
)
from py20305.forwarders.base import EventFrame
from py20305.forwarders.ocsf import (
    DEFAULT_VENDOR_NAME,
    ConnectionDirectionId,
    Endpoint,
    Metadata,
    NetworkActivity,
    NetworkActivityId,
    Product,
)
from py20305.forwarders.ocsf import now_epoch_ms as _now_ms

if TYPE_CHECKING:
    from py20305.forwarders.config import ConnectionTelemetryConfig
    from py20305.forwarders.manager import ForwarderManager

logger = logging.getLogger(__name__)

PRODUCT_NAME = "py20305"


class ConnectionRole(StrEnum):
    """Which of the host application's links a connection event describes.

    Serialized as one ``role:<value>`` label in ``metadata.labels`` (see
    :func:`role_label`). A consumer keys on these strings, so they are part of
    the wire contract: add a member rather than renaming one.
    """

    #: The IEEE 2030.5 client's link to the utility server.
    NORTHBOUND = "northbound"
    #: A connection to a DER device (SunSpec Modbus, DNP3, OCPP and others).
    SOUTHBOUND = "southbound"
    #: An inbound connection to the host's own management console or API.
    MANAGEMENT = "management"
    #: An inbound connection to one of the host's own protocol listeners.
    SERVER = "server"
    #: The connection to the broker that carries this telemetry.
    TELEMETRY = "telemetry"


#: Prefix of the role label in ``metadata.labels``.
ROLE_LABEL_PREFIX = "role:"


def role_label(role: ConnectionRole | str) -> str:
    """Return the ``metadata.labels`` entry that names ``role``."""
    return f"{ROLE_LABEL_PREFIX}{ConnectionRole(role).value}"


# Service labels, carried on ``dst_endpoint.svc_name``. A host may pass any
# string; these are the ones this package and its known hosts use, kept here
# so a consumer has one list to match against.
SERVICE_IEEE2030_5 = "ieee2030.5"
SERVICE_MODBUS = "modbus"
SERVICE_DNP3 = "dnp3"
SERVICE_OCPP = "ocpp"
SERVICE_MQTT = "mqtt"
SERVICE_HTTP = "http"
SERVICE_HTTPS = "https"

#: The service label of the IEEE 2030.5 client's own connections.
SERVICE_LABEL = SERVICE_IEEE2030_5

# Passive capture pipelines commonly dedupe flows over 60 seconds; matching
# that keeps a coalesced record aligned with the capture beside it rather than
# inventing a second notion of "the same flow".
DEFAULT_COALESCE_WINDOW_SECONDS = 60.0

# Most success windows open at once, one per link. A host that accepts inbound
# connections from many addresses would otherwise hold one window per address
# for a full window length. At the cap, the oldest window is emitted early to
# make room: its record is still complete, only shorter.
MAX_OPEN_WINDOWS = 256

# Most events held while the transport cannot deliver them, for example while
# the broker is unreachable. The oldest held event is dropped first.
DEFAULT_MAX_HELD_EVENTS = 1000

# The socket of the connection *this* request established, if it established
# one. A ContextVar rather than a field on the emitter: the client runs many
# requests concurrently over one session, and a single shared "last connected"
# field is overwritten by whichever connect happens to finish last -- so a
# concurrent request reports another connection's local port, which is worse
# than reporting none.
#
# The connector's ``on_connect`` is awaited from inside the requesting task,
# so a value set there belongs to that request and no other. A request that
# reused a pooled connection never runs it, reads ``None``, and reports no
# source endpoint rather than guessing at one.
_request_socket: ContextVar[SocketPair | None] = ContextVar("_request_socket", default=None)


# Upper bound on the reason text. A protocol-error exception embeds the peer's
# whole response body, and this event is published to a topic an operator may
# have scoped for connection metadata rather than payload content. Enough to
# identify the failure, not enough to ship a response body through it.
MAX_STATUS_DETAIL_CHARS = 512


def _safe_url_reference(url: str) -> str:
    """A peer-controlled URL reduced to what identifies it: scheme, host, path.

    Userinfo and the query string are dropped, not just the password:
    a redirect ``Location`` is chosen by the peer and can carry credentials
    or signed query parameters, and this reference lands on a topic scoped
    for connection metadata.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        # The value is peer-controlled, so a malformed one must neither raise
        # into the caller nor pass through verbatim -- either would let the
        # peer shape the record. An opaque marker says what happened without
        # carrying any of it.
        return "<unparseable url>"
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))


def _redact_url(url: str | None) -> str | None:
    """Reduce a URL to connection metadata before it can reach the wire.

    The configured server URL is serialized into every event's
    ``url.url_string``, and the configuration accepts any string -- so
    userinfo (``https://user:password@host``) and query parameters
    (``?token=...``) would both publish secrets to the broker. What
    identifies the interface is scheme, host, port and path; nothing else
    is retained.
    """
    if url is None:
        return None
    return _safe_url_reference(url)


def _bounded(detail: str) -> str:
    """Clip reason text, marking it so a reader knows it was clipped.

    The cap is the total length on the wire, marker included -- appending the
    marker after keeping the full cap would exceed the documented bound by
    exactly the marker's length.
    """
    if len(detail) <= MAX_STATUS_DETAIL_CHARS:
        return detail
    marker = f"... [truncated, {len(detail)} chars]"
    return detail[: MAX_STATUS_DETAIL_CHARS - len(marker)] + marker


@dataclass(frozen=True)
class Window:
    """A closed window of successful connections, ready to emit.

    Attributes:
        count: Attempts the window represents.
        start_ms: First attempt, as an OCSF timestamp.
        end_ms: Last attempt, as an OCSF timestamp.
        src: The source endpoint observed when the window opened, or the
            first one seen inside it. Carried with the window rather than
            re-read at emission, because the success that closes a window
            belongs to a different attempt than the one that opened it.
        dst: The destination endpoint observed together with ``src``.
    """

    count: int
    start_ms: int
    end_ms: int
    src: Endpoint | None = None
    dst: Endpoint | None = None


@dataclass(frozen=True)
class Outcome:
    """A connection outcome classified for OCSF.

    Attributes:
        activity_id: ``FAIL`` / ``REFUSE`` when nothing was established;
            ``OPEN`` when the connection worked and the exchange over it did
            not.
        detail: Why it happened. Required on every failure.
        status_code: The peer's own code, when it gave one.
    """

    activity_id: NetworkActivityId
    detail: str
    status_code: str | None = None

    def __post_init__(self) -> None:
        """Remove URL credentials from the reason text, then bound it.

        Done at the boundary so no caller can bypass it. The order matters:
        clipping first can cut off the ``@`` that ends a URL's userinfo, and
        the scrubber then no longer recognizes the URL and leaves the
        password in place.
        """
        object.__setattr__(self, "detail", _bounded(_scrub_credentials(self.detail)))


def classify(exc: BaseException) -> Outcome | None:
    """Classify an exception from the request path into an OCSF outcome.

    The distinction that matters to whoever reads the log is whether the
    connection was ever established:

    - A transport failure -- connect, timeout, TLS handshake -- means nothing
      was exchanged, and reports ``FAIL``. A refused connection reports
      ``REFUSE``, which is more specific and is what the peer actually did.
    - An application-layer failure over a connection that *did* open -- a 500,
      a 429, a redirect loop, an unusable body -- reports ``OPEN`` with a
      failure status. Reporting it as ``FAIL`` would tell a reader the client
      never reached the server, which would send an investigation the wrong
      way.

    Returns:
        The classified outcome, or ``None`` when the exception is not a
        connection outcome at all and nothing should be reported.
    """
    # A 204 is a success with no body, not an error. It subclasses
    # Sep2ProtocolError, so it has to be tested before its parent or every
    # empty optional resource would be logged as a failed exchange.
    if isinstance(exc, Sep2NoContentError):
        return None

    if isinstance(exc, Sep2TlsError):
        return Outcome(NetworkActivityId.FAIL, f"TLS failure: {exc}")

    if isinstance(exc, Sep2ConnectionError):
        # The cause chain distinguishes the transport failures OCSF names.
        # A refusal means the host answered and declined; a disconnect or
        # reset means a connection *opened* and was torn down -- reporting
        # that as Fail would tell a reader the client never reached the
        # server, which is not what the socket saw.
        if _has_cause(exc, ConnectionRefusedError):
            return Outcome(NetworkActivityId.REFUSE, f"Connection refused: {exc}")
        if _has_cause(exc, (aiohttp.ServerDisconnectedError, ConnectionResetError)):
            return Outcome(NetworkActivityId.RESET, f"Connection reset by the server: {exc}")
        return Outcome(NetworkActivityId.FAIL, f"Connection failure: {exc}")

    if isinstance(exc, Sep2RateLimitError):
        retry_after = getattr(exc, "retry_after", None)
        suffix = f", retry after {retry_after}s" if retry_after is not None else ""
        return Outcome(NetworkActivityId.OPEN, f"Rate limited by the server{suffix}: {exc}", "429")

    if isinstance(exc, Sep2RedirectError):
        # The Location header is peer-controlled: it can carry userinfo or
        # signed query parameters, so only a stripped-down reference to it is
        # reported -- and the exception text, which embeds the raw header, is
        # not interpolated at all.
        status = getattr(exc, "status_code", None)
        location = getattr(exc, "location", None)
        where = f" to {_safe_url_reference(location)}" if location else ""
        return Outcome(
            NetworkActivityId.OPEN,
            f"Redirected{where}, triggering re-discovery",
            str(status) if status is not None else None,
        )

    if isinstance(exc, Sep2PayloadError):
        # The exception text can embed the unparseable body; identify the
        # failure by where and how big instead of by content.
        path = getattr(exc, "path", None)
        size = getattr(exc, "body_length", None)
        at = f" from GET {path}" if path else ""
        of = f" ({size} bytes)" if size is not None else ""
        return Outcome(NetworkActivityId.OPEN, f"Unusable response body{at}{of}")

    if isinstance(exc, Sep2ProtocolError):
        # The exception text embeds the peer's response body, and a length
        # cap limits volume but not content -- the status code alone is what
        # identifies this failure, so nothing else is reported.
        status = getattr(exc, "status_code", None)
        detail = "Server returned an unexpected status"
        if status is not None:
            detail += f": HTTP {status}"
        return Outcome(
            NetworkActivityId.OPEN,
            detail,
            str(status) if status is not None else None,
        )

    if isinstance(exc, ssl.SSLError):
        return Outcome(NetworkActivityId.FAIL, f"TLS failure: {exc}")

    if isinstance(exc, ConnectionRefusedError):
        return Outcome(NetworkActivityId.REFUSE, f"Connection refused: {exc}")

    if isinstance(exc, ConnectionResetError):
        return Outcome(NetworkActivityId.RESET, f"Connection reset by the server: {exc}")

    if isinstance(exc, TimeoutError):
        return Outcome(NetworkActivityId.FAIL, f"Connection timed out: {exc}")

    if isinstance(exc, OSError):
        return Outcome(NetworkActivityId.FAIL, f"Connection failure: {exc}")

    return None


def _has_cause(
    exc: BaseException, wanted: type[BaseException] | tuple[type[BaseException], ...]
) -> bool:
    """Whether ``wanted`` appears anywhere in the exception's cause chain.

    ``with_retry`` wraps an exhausted transport failure in
    ``Sep2ConnectionError`` with the original attached as ``__cause__``, so
    the specific errno that distinguishes a refusal from a timeout is one or
    more links down the chain.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, wanted):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


class CoalescingWindow:
    """Collapses successful connections on one link into one event per window.

    A polling client would otherwise put more volume on the collector's flow
    path than the passive capture beside it. A coalesced record still has to
    answer the question a connection log asks -- was there an attempt at time
    T -- so it carries the window's bounds and the attempt count, and every
    attempt in it shared one link: role, direction, service and peer. It is
    not a sample.

    Only successes are coalesced. Failures never enter here.

    A window keeps the endpoints observed when it opened. The success that
    *closes* a window is a different attempt from the one that opened it, so
    reading the current endpoints at close time would report a connection
    that carried none of the successes being described. A coalesced record
    therefore names the source port of the first connection in the window
    only.

    Not synchronized: every caller runs on the event loop, and none of the
    methods here await, so a window update cannot be interleaved with another.
    """

    def __init__(self, window_seconds: float = DEFAULT_COALESCE_WINDOW_SECONDS) -> None:
        """Create a window.

        Args:
            window_seconds: How long a window stays open. Zero disables
                coalescing, emitting one event per success.
        """
        if window_seconds < 0:
            raise ValueError(f"window_seconds must not be negative, got {window_seconds}")
        self._window_ms = int(window_seconds * 1000)
        self._count = 0
        self._start_ms: int | None = None
        self._end_ms: int | None = None
        self._src: Endpoint | None = None
        self._dst: Endpoint | None = None

    @property
    def window_ms(self) -> int:
        """Window length in milliseconds."""
        return self._window_ms

    @property
    def pending(self) -> int:
        """Successes recorded but not yet emitted."""
        return self._count

    @property
    def start_ms(self) -> int | None:
        """Start of the open window, or ``None`` when no window is open."""
        return self._start_ms

    def record(
        self, now_ms: int, src: Endpoint | None = None, dst: Endpoint | None = None
    ) -> Window | None:
        """Record one success, returning a window to emit when one closes.

        Args:
            now_ms: The time of the success, in epoch milliseconds.
            src: The source endpoint of the connection this success was
                carried on, when it is known.
            dst: The destination endpoint of that connection, when it is known.

        Returns:
            The closed window when this success closed one, otherwise ``None``.
            The returned window never includes the success that closed it --
            that one opens the next window, so no attempt is counted twice or
            dropped -- and it carries the endpoints observed when it opened,
            not the ones live at close time.
        """
        if self._window_ms == 0:
            return Window(1, now_ms, now_ms, src, dst)

        if self._start_ms is None:
            self._open(now_ms, src, dst)
            return None

        if now_ms - self._start_ms >= self._window_ms:
            closed = self._snapshot()
            self._open(now_ms, src, dst)
            return closed

        self._count += 1
        self._end_ms = now_ms
        # A window that opened without a source endpoint adopts the first one
        # seen inside it: better a connection that carried some of these
        # successes than none at all. Its destination comes with it, since
        # both describe the same connection.
        if self._src is None and src is not None:
            self._src = src
            self._dst = dst if dst is not None else self._dst
        elif self._dst is None:
            self._dst = dst
        return None

    def _open(self, now_ms: int, src: Endpoint | None, dst: Endpoint | None) -> None:
        """Start a new window at ``now_ms`` with the given endpoints."""
        self._start_ms = now_ms
        self._end_ms = now_ms
        self._count = 1
        self._src = src
        self._dst = dst

    def _snapshot(self) -> Window:
        """Return the current window as it stands, for emission."""
        assert self._start_ms is not None
        return Window(
            self._count, self._start_ms, self._end_ms or self._start_ms, self._src, self._dst
        )

    def take_expired(self, now_ms: int) -> Window | None:
        """Close the window if its configured length has elapsed.

        The window normally closes when the success *after* it arrives. A link
        whose successes stop -- it starts failing, or goes idle -- would
        otherwise hold its last successes unreported until shutdown, so other
        activity through the emitter offers the clock a chance to close an
        expired window.

        Returns:
            The expired window, or ``None`` when nothing is open or it is
            still inside its length.
        """
        if self._window_ms == 0 or self._start_ms is None or self._count == 0:
            return None
        if now_ms - self._start_ms < self._window_ms:
            return None
        return self.flush()

    def flush(self) -> Window | None:
        """Close the open window, if any.

        Called on shutdown so the successes accumulated since the last close
        are reported rather than discarded -- an unreported attempt is a gap
        in the connection log.

        Returns:
            The pending window, or ``None`` when nothing is pending.
        """
        if self._start_ms is None or self._count == 0:
            return None
        closed = self._snapshot()
        self._start_ms = None
        self._end_ms = None
        self._count = 0
        self._src = None
        self._dst = None
        return closed


def build_metadata(
    product_version: str | None = None,
    *,
    role: ConnectionRole | str = ConnectionRole.NORTHBOUND,
    product_name: str = PRODUCT_NAME,
    vendor_name: str = DEFAULT_VENDOR_NAME,
) -> Metadata:
    """Build the OCSF ``metadata`` block naming the producer and the link's role.

    Args:
        product_version: Version of the producing application.
        role: The link the event describes, recorded as one ``role:<role>``
            label.
        product_name: Name of the producing application. Defaults to this
            library; a host application passes its own name so the event
            identifies the product that actually emitted it.
        vendor_name: Publisher of the producing application.
    """
    return Metadata(
        product=Product(name=product_name, vendor_name=vendor_name, version=product_version),
        labels=[role_label(role)],
    )


def endpoint_for_host(host: str | None, port: int | None) -> Endpoint | None:
    """Build an endpoint for a configured host, which may be an address or a name.

    OCSF types the endpoint's ``ip`` as an IP address. A configured host is
    often a DNS name, and a name in ``ip`` is a schema violation a consumer may
    reject, so a name goes in ``hostname``.

    Returns:
        The endpoint, or ``None`` when ``host`` is empty.
    """
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return Endpoint(hostname=host, port=port)
    return Endpoint(ip=host, port=port)


def _endpoints(
    socket_pair: SocketPair | None, fallback_remote: Endpoint | None
) -> tuple[Endpoint | None, Endpoint | None]:
    """Resolve the source and destination endpoints for an event.

    The connection's own socket is preferred, since it carries the local port
    a connection log asks for. When no connection was ever established there
    is no local socket to report, and the destination falls back to the
    address the client was trying to reach -- which is exactly the case a
    failure record describes.
    """
    if socket_pair is not None:
        local = socket_pair.local
        remote = socket_pair.remote
        src = Endpoint(ip=local.ip, port=local.port) if local else None
        dst = Endpoint(ip=remote.ip, port=remote.port) if remote else fallback_remote
        return src, dst
    return None, fallback_remote


def _failure_event(
    outcome: Outcome,
    *,
    metadata: Metadata,
    src: Endpoint | None,
    dst: Endpoint | None,
    url: str | None,
    service: str | None,
    direction: ConnectionDirectionId,
    time: int | None,
) -> NetworkActivity | None:
    """Build one failure event from resolved endpoints."""
    if dst is None:
        logger.debug("No destination endpoint for connection failure; not reporting")
        return None

    # The two factories split on which layer failed: the connection itself,
    # or an exchange over a connection that stayed up.
    if outcome.activity_id is NetworkActivityId.OPEN:
        return NetworkActivity.for_exchange_failure(
            metadata=metadata,
            dst_endpoint=dst,
            src_endpoint=src,
            status_detail=outcome.detail,
            status_code=outcome.status_code,
            service=service,
            url=url,
            time=time,
            connection_direction=direction,
        )

    return NetworkActivity.for_connection_failure(
        activity_id=outcome.activity_id,
        metadata=metadata,
        dst_endpoint=dst,
        src_endpoint=src,
        status_detail=outcome.detail,
        status_code=outcome.status_code,
        service=service,
        url=url,
        time=time,
        connection_direction=direction,
    )


def build_failure_event(
    outcome: Outcome,
    *,
    metadata: Metadata,
    socket_pair: SocketPair | None,
    server_endpoint: Endpoint | None,
    url: str | None = None,
    service: str | None = SERVICE_LABEL,
    direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
    time: int | None = None,
) -> NetworkActivity | None:
    """Build one failure event. Failures are never coalesced.

    Returns ``None`` when there is no destination to report -- an event with
    neither endpoint says nothing about a connection, and the event class
    rejects it.
    """
    src, dst = _endpoints(socket_pair, server_endpoint)
    return _failure_event(
        outcome,
        metadata=metadata,
        src=src,
        dst=dst,
        url=url,
        service=service,
        direction=direction,
        time=time,
    )


def build_coalesced_success_event(
    window: Window,
    *,
    metadata: Metadata,
    server_endpoint: Endpoint | None,
    url: str | None = None,
    service: str | None = SERVICE_LABEL,
    direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
) -> NetworkActivity | None:
    """Build one event standing for a window of successful connections.

    ``server_endpoint`` is the destination reported when the window observed
    none, as for a request that reused a pooled connection.
    """
    count, start_ms, end_ms = window.count, window.start_ms, window.end_ms
    src = window.src
    dst = window.dst if window.dst is not None else server_endpoint
    if dst is None:
        logger.debug("No destination endpoint for connection successes; not reporting")
        return None
    if count == 1:
        return NetworkActivity.for_connection_success(
            metadata=metadata,
            dst_endpoint=dst,
            src_endpoint=src,
            service=service,
            url=url,
            time=end_ms,
            connection_direction=direction,
        )
    return NetworkActivity.for_coalesced_successes(
        metadata=metadata,
        dst_endpoint=dst,
        src_endpoint=src,
        count=count,
        start_time=start_ms,
        end_time=end_ms,
        service=service,
        url=url,
        connection_direction=direction,
    )


# Userinfo in any URL embedded in free text, such as an exception message.
_URL_USERINFO = re.compile(r"(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@]+@")


def _scrub_credentials(text: str) -> str:
    """Remove the userinfo part of every URL in ``text``."""
    return _URL_USERINFO.sub(r"\g<scheme>", text)


def _describe(exc: BaseException) -> str:
    """Return an exception's type and message as one line of reason text."""
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {message}" if message else name


def _classify_chain(exc: BaseException) -> Outcome | None:
    """Classify ``exc``, or the first exception in its cause chain that can be classified.

    Libraries often wrap a socket error in their own exception type, keeping
    the original as the cause or context. The original says whether the
    connection was refused, timed out or failed its TLS handshake.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        outcome = classify(current)
        if outcome is not None:
            return outcome
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


_REPORTABLE_ACTIVITIES = frozenset(
    {
        NetworkActivityId.OPEN,
        NetworkActivityId.RESET,
        NetworkActivityId.FAIL,
        NetworkActivityId.REFUSE,
    }
)


def link_outcome(
    reason: BaseException | str,
    *,
    activity_id: NetworkActivityId | None = None,
    status_code: str | None = None,
) -> Outcome:
    """Classify a failure the host reported on one of its own links.

    Unlike :func:`classify`, this always returns an outcome: the caller has
    already decided the exception is a connection failure. An exception the
    IEEE 2030.5 taxonomy does not know is reported as ``FAIL`` with its type
    and message. Credentials in any URL inside the reason are removed.

    Args:
        reason: The exception, or the reason text.
        activity_id: Overrides the classified activity. ``OPEN`` reports a
            connection that opened and whose exchange then failed.
        status_code: The peer's own code, when it gave one.

    Raises:
        ValueError: If ``activity_id`` is not a failure activity or ``OPEN``.
    """
    if activity_id is not None and NetworkActivityId(activity_id) not in _REPORTABLE_ACTIVITIES:
        raise ValueError(f"activity_id {activity_id!r} does not describe a failed connection")
    if isinstance(reason, BaseException):
        classified = _classify_chain(reason)
        if classified is None:
            classified = Outcome(NetworkActivityId.FAIL, f"Connection failure: {_describe(reason)}")
        detail = classified.detail
        activity = classified.activity_id
        code = classified.status_code
    else:
        detail = reason.strip() or "Connection failure"
        activity = NetworkActivityId.FAIL
        code = None
    return Outcome(
        NetworkActivityId(activity_id) if activity_id is not None else activity,
        detail,
        status_code if status_code is not None else code,
    )


@dataclass(frozen=True)
class _LinkKey:
    """What makes two successful connections the same link for coalescing.

    An outbound link is identified by its destination. An inbound link is
    identified by the remote source host and the local listener, so
    connections from different clients are never merged into one record. The
    source port is never part of the key: it changes on every connection.
    """

    role: ConnectionRole
    direction: ConnectionDirectionId
    service: str | None
    src_host: str | None
    dst_host: str | None
    dst_port: int | None


# The IEEE 2030.5 client's successes share one window regardless of which
# pooled connection carried them, so its key names no address at all rather
# than whichever destination a request observed.
_NORTHBOUND_KEY = _LinkKey(
    role=ConnectionRole.NORTHBOUND,
    direction=ConnectionDirectionId.OUTBOUND,
    service=SERVICE_LABEL,
    src_host=None,
    dst_host=None,
    dst_port=None,
)


def _host(endpoint: Endpoint | None) -> str | None:
    """Return the address or name that identifies ``endpoint``'s host."""
    if endpoint is None:
        return None
    return endpoint.ip if endpoint.ip is not None else endpoint.hostname


def _link_key(
    role: ConnectionRole,
    direction: ConnectionDirectionId,
    service: str | None,
    src: Endpoint | None,
    dst: Endpoint | None,
) -> _LinkKey:
    """Build the coalescing key for one connection."""
    src_host = _host(src) if direction is ConnectionDirectionId.INBOUND else None
    return _LinkKey(
        role=role,
        direction=direction,
        service=service,
        src_host=src_host,
        dst_host=_host(dst),
        dst_port=dst.port if dst is not None else None,
    )


class LinkObserver(Protocol):
    """Receives connection outcomes from the links this package opens or accepts.

    :class:`ConnectionTelemetryEmitter` implements it. A host application can
    put its own object in front of the emitter, for example to replace the
    emitter at runtime or to pass calls from worker threads to the event loop.
    Implementations must never raise.
    """

    def record_connection(
        self,
        *,
        role: ConnectionRole | str,
        service: str | None,
        direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        dst: Endpoint | None = None,
        src: Endpoint | None = None,
        url: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Record one successful connection."""

    def record_connection_failure(
        self,
        *,
        role: ConnectionRole | str,
        service: str | None,
        reason: BaseException | str,
        direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        dst: Endpoint | None = None,
        src: Endpoint | None = None,
        url: str | None = None,
        activity_id: NetworkActivityId | None = None,
        status_code: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Record one failed connection with its reason."""


@dataclass
class _OpenLink:
    """A link with an open success window."""

    window: CoalescingWindow
    url: str | None


class ConnectionTelemetryEmitter:
    """Turns connection outcomes into events on the forwarder transport.

    One instance per host application. Attached as the IEEE 2030.5 client's
    connection observer (``client.http.connection_observer = emitter``), the
    client calls :meth:`begin_request` / :meth:`record_success` /
    :meth:`record_failure` / :meth:`on_connect` / :meth:`flush` through that
    seam, and those events carry the ``northbound`` role. The host reports
    its other links through :meth:`record_connection` and
    :meth:`record_connection_failure`.

    Successes are coalesced per link into windows, unless the configuration
    turns coalescing off, in which case every success is its own event.
    Failures are always one event each.

    Events produced while the transport cannot deliver them -- the broker is
    unreachable, or the forwarder has not started -- are held, up to a bound,
    and sent once it can. A broker outage is itself a connection event, and
    its record would otherwise be lost to the outage it describes.

    Nothing here may raise into the caller. Telemetry that can break the
    connection it describes is worse than absent telemetry, so every entry
    point swallows its own errors and logs them.
    """

    def __init__(
        self,
        forwarder: ForwarderManager | None,
        config: ConnectionTelemetryConfig,
        *,
        product_version: str | None = None,
        product_name: str = PRODUCT_NAME,
        vendor_name: str = DEFAULT_VENDOR_NAME,
        max_held_events: int = DEFAULT_MAX_HELD_EVENTS,
    ) -> None:
        """Create an emitter.

        Args:
            forwarder: Where events go. ``None`` disables emission.
            config: Whether telemetry is on, its topic, and how successes are
                coalesced.
            product_version: Version of the producing application, for
                ``metadata.product``.
            product_name: Name of the producing application, for
                ``metadata.product``. A host application passes its own name.
            vendor_name: Publisher of the producing application.
            max_held_events: Most events held while the transport cannot
                deliver them.
        """
        self._forwarder = forwarder
        self._config = config
        self._metadata = {
            role: build_metadata(
                product_version, role=role, product_name=product_name, vendor_name=vendor_name
            )
            for role in ConnectionRole
        }
        #: Failures to *construct or hand off* an event since start -- a bug
        #: in classification, the window, or serialization, or a directly
        #: attached forwarder that raises. The first logs at WARNING, the
        #: rest at DEBUG. A broker outage is not counted here: events are held
        #: while the transport cannot deliver them, and broker-level delivery
        #: failure is visible in the transport's own statistics.
        self.emit_failures = 0
        #: Held events discarded because the hold was full.
        self.held_events_dropped = 0
        self._window_seconds = config.coalesce_window_seconds if config.coalesce_successes else 0.0
        self._links: dict[_LinkKey, _OpenLink] = {}
        self._held: deque[EventFrame] = deque()
        self._max_held = max(0, max_held_events)
        self._server_endpoint: Endpoint | None = None
        self._base_url: str | None = None

    @property
    def enabled(self) -> bool:
        """Whether events will actually be emitted."""
        return self._config.enabled and self._forwarder is not None

    @property
    def coalescing(self) -> bool:
        """Whether successes are collapsed into windows rather than sent one by one."""
        return self._window_seconds > 0

    @property
    def held_events(self) -> int:
        """Events waiting for the transport to become able to deliver them."""
        return len(self._held)

    def set_server(self, host: str | None, port: int | None, base_url: str | None = None) -> None:
        """Record the server this client targets, for events with no live socket.

        A connection that never established has no socket to report, but the
        address the client was trying to reach is exactly what the failure
        record needs to name.
        """
        self._base_url = _redact_url(base_url)
        self._server_endpoint = endpoint_for_host(host, port) if port else None

    # The IEEE 2030.5 client's observer seam.

    def begin_request(self) -> None:
        """Clear any socket left from an earlier request in this task.

        Scopes attribution to "a connection established while handling *this*
        request". Without it, a task that opens a connection and then makes a
        second request over the pool would attribute the first connection's
        local port to the second request -- right only by luck, and wrong as
        soon as the pool hands back a different connection.
        """
        _request_socket.set(None)

    def on_connect(self, pair: SocketPair) -> None:
        """Observe a newly established connection's socket.

        Reached through the client's connector, which is the only place the
        local port is available.
        """
        _request_socket.set(pair)

    def record_success(self, now_ms: int | None = None) -> None:
        """Record one successful IEEE 2030.5 exchange with the server."""
        if not self.enabled:
            return
        try:
            moment = now_ms if now_ms is not None else _now_ms()
            self._emit_expired(moment)
            src, dst = _endpoints(_request_socket.get(), None)
            self._record_window(_NORTHBOUND_KEY, src, dst, self._base_url, moment)
        except Exception:
            self._record_emit_failure("record a success")

    def record_failure(self, exc: BaseException) -> None:
        """Record one failed IEEE 2030.5 connection or exchange, with its reason.

        Never coalesced: each failure keeps its own event and its own reason.
        """
        if not self.enabled:
            return
        try:
            # The failure is also the clock's chance to close expired success
            # windows: those successes happened before this failure, and
            # holding them until the next success -- which may never come --
            # would leave them unreported until shutdown. Guarded on its own:
            # a problem emitting a window must not cost the failure record,
            # which is the more important of the two.
            try:
                self._emit_expired(_now_ms())
            except Exception:
                self._record_emit_failure("close an expired window")
            outcome = classify(exc)
            if outcome is None:
                return
            # The retained socket belongs to a connection this request
            # established. For an exchange failure (OPEN) or a teardown
            # (RESET) that connection is the subject of the record; for a
            # failure that never opened (FAIL, REFUSE) it is an earlier
            # attempt's socket, and attaching its local port would attribute
            # the failure to a connection that did not fail.
            socket_pair = (
                _request_socket.get()
                if outcome.activity_id in (NetworkActivityId.OPEN, NetworkActivityId.RESET)
                else None
            )
            self._emit(
                build_failure_event(
                    outcome,
                    metadata=self._metadata[ConnectionRole.NORTHBOUND],
                    socket_pair=socket_pair,
                    server_endpoint=self._server_endpoint,
                    url=self._base_url,
                )
            )
        except Exception:
            self._record_emit_failure("record a failure")

    # The host application's other links.

    def record_connection(
        self,
        *,
        role: ConnectionRole | str,
        service: str | None,
        direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        dst: Endpoint | None = None,
        src: Endpoint | None = None,
        url: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Record one successful connection on one of the host's links.

        Args:
            role: Which link the connection belongs to.
            service: Service label for ``dst_endpoint.svc_name``, for example
                ``"modbus"``.
            direction: ``OUTBOUND`` for a connection the host opened,
                ``INBOUND`` for one it accepted.
            dst: The responder: the device or broker for an outbound
                connection, the host's own listener for an inbound one.
                Required for the event to be reported.
            src: The initiator, when its address is known.
            url: URL the connection targeted, if any. Credentials and the
                query string are removed.
            now_ms: Time of the connection in epoch milliseconds; now when
                omitted.
        """
        if not self.enabled:
            return
        try:
            moment = now_ms if now_ms is not None else _now_ms()
            link_role = ConnectionRole(role)
            link_direction = ConnectionDirectionId(direction)
            self._emit_expired(moment)
            key = _link_key(link_role, link_direction, service, src, dst)
            self._record_window(key, src, dst, _redact_url(url), moment)
        except Exception:
            self._record_emit_failure("record a connection")

    def record_connection_failure(
        self,
        *,
        role: ConnectionRole | str,
        service: str | None,
        reason: BaseException | str,
        direction: ConnectionDirectionId = ConnectionDirectionId.OUTBOUND,
        dst: Endpoint | None = None,
        src: Endpoint | None = None,
        url: str | None = None,
        activity_id: NetworkActivityId | None = None,
        status_code: str | None = None,
        now_ms: int | None = None,
    ) -> None:
        """Record one failed connection on one of the host's links, with its reason.

        Never coalesced. The reason is reduced to a bounded line of text with
        credentials in URLs removed; see :func:`link_outcome`.

        Args:
            role: Which link the connection belongs to.
            service: Service label for ``dst_endpoint.svc_name``.
            reason: The exception the connection failed with, or reason text.
            direction: ``OUTBOUND`` or ``INBOUND``.
            dst: The responder. Required for the event to be reported.
            src: The initiator, when its address is known.
            url: URL the connection targeted, if any.
            activity_id: Overrides the classified activity; ``OPEN`` reports
                a connection that opened and whose exchange then failed.
            status_code: The peer's own code, when it gave one.
            now_ms: Time of the failure in epoch milliseconds; now when
                omitted.
        """
        if not self.enabled:
            return
        try:
            moment = now_ms if now_ms is not None else _now_ms()
            link_role = ConnectionRole(role)
            try:
                self._emit_expired(moment)
            except Exception:
                self._record_emit_failure("close an expired window")
            outcome = link_outcome(reason, activity_id=activity_id, status_code=status_code)
            self._emit(
                _failure_event(
                    outcome,
                    metadata=self._metadata[link_role],
                    src=src,
                    dst=dst,
                    url=_redact_url(url),
                    service=service,
                    direction=ConnectionDirectionId(direction),
                    time=moment,
                )
            )
        except Exception:
            self._record_emit_failure("record a connection failure")

    def flush_expired(self, now_ms: int | None = None) -> None:
        """Emit every success window whose length has elapsed, then send held events.

        Windows also close on any later activity through the emitter. A host
        calls this on a timer so a link that went quiet is still reported
        within about one window length, and so events held during a broker
        outage go out once it ends.
        """
        if not self.enabled:
            return
        try:
            self._emit_expired(now_ms if now_ms is not None else _now_ms())
        except Exception:
            self._record_emit_failure("close expired windows")
        self.release_held()

    def flush(self) -> None:
        """Emit every open success window.

        Called when the client or the host closes: successes accumulated since
        the last window closed are still attempts the connection log accounts
        for.
        """
        if not self.enabled:
            return
        try:
            for key in list(self._links):
                self._close_link(key)
        except Exception:
            self._record_emit_failure("flush")
        # With per-attempt logging there may be no open window to close, so
        # closing windows alone would not send events held during an outage.
        self.release_held()

    def release_held(self) -> None:
        """Send held events if the transport can deliver them now."""
        if self._forwarder is None or not self._held:
            return
        try:
            if not self._transport_ready():
                return
            while self._held:
                self._forwarder.queue_event(self._held.popleft())
        except Exception:
            self._record_emit_failure("send held events")

    # Internals.

    def _record_window(
        self,
        key: _LinkKey,
        src: Endpoint | None,
        dst: Endpoint | None,
        url: str | None,
        moment: int,
    ) -> None:
        """Add one success to its link's window, emitting the window when it closes."""
        link = self._links.get(key)
        if link is None:
            if len(self._links) >= MAX_OPEN_WINDOWS:
                # Make room by reporting the oldest window early.
                self._close_link(next(iter(self._links)))
            link = _OpenLink(CoalescingWindow(self._window_seconds), url)
            self._links[key] = link
        closed = link.window.record(moment, src, dst)
        if closed is not None:
            self._emit_window(key, closed, link.url)
            link.url = url
        if link.window.pending == 0:
            del self._links[key]

    def _emit_expired(self, now_ms: int) -> None:
        """Emit and forget every window whose length has elapsed."""
        for key, link in list(self._links.items()):
            closed = link.window.take_expired(now_ms)
            if closed is not None:
                del self._links[key]
                self._emit_window(key, closed, link.url)

    def _close_link(self, key: _LinkKey) -> None:
        """Emit the open window of one link and forget the link."""
        link = self._links.pop(key)
        closed = link.window.flush()
        if closed is not None:
            self._emit_window(key, closed, link.url)

    def _emit_window(self, key: _LinkKey, window: Window, url: str | None) -> None:
        """Build and send the event for one closed success window."""
        northbound = key == _NORTHBOUND_KEY
        self._emit(
            build_coalesced_success_event(
                window,
                metadata=self._metadata[key.role],
                server_endpoint=self._server_endpoint if northbound else None,
                url=self._base_url if northbound else url,
                service=key.service,
                direction=key.direction,
            )
        )

    def _transport_ready(self) -> bool:
        """Return whether an event queued now would reach a running forwarder.

        A forwarder that does not report this is assumed ready.
        """
        return bool(getattr(self._forwarder, "can_deliver_events", True))

    def _record_emit_failure(self, what: str) -> None:
        """Count an emission failure, and say so the first time.

        Swallowing is required -- telemetry must not break the connection it
        describes -- but swallowing silently means an operator relying on
        these records cannot tell a client with no failures from one whose
        telemetry stopped working.
        """
        self.emit_failures += 1
        if self.emit_failures == 1:
            logger.warning(
                "Connection telemetry failed to %s; further failures log at debug",
                what,
                exc_info=True,
            )
        else:
            logger.debug("Connection telemetry failed to %s", what, exc_info=True)

    def _emit(self, event: NetworkActivity | None) -> None:
        """Send one event, or hold it while the transport cannot deliver it."""
        if event is None or self._forwarder is None:
            return
        frame = EventFrame(
            payload=event.to_dict(),
            topic_suffix=self._config.topic_suffix,
            kind="connection-event",
        )
        if not self._transport_ready():
            self._hold(frame)
            return
        self.release_held()
        self._forwarder.queue_event(frame)

    def _hold(self, frame: EventFrame) -> None:
        """Keep one event until the transport can deliver it; drop the oldest at the bound."""
        if self._max_held == 0:
            self._count_held_drop()
            return
        if len(self._held) >= self._max_held:
            self._held.popleft()
            self._count_held_drop()
        self._held.append(frame)

    def _count_held_drop(self) -> None:
        """Count one held event lost to the bound, and warn the first time."""
        self.held_events_dropped += 1
        if self.held_events_dropped == 1:
            logger.warning(
                "Connection telemetry hold is full (%d events) while the transport is "
                "unavailable; dropping the oldest",
                self._max_held,
            )
