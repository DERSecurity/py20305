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
"""

from __future__ import annotations

import ipaddress
import logging
import ssl
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING
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
SERVICE_LABEL = "ieee2030.5"

# Passive capture pipelines commonly dedupe flows over 60 seconds; matching
# that keeps a coalesced record aligned with the capture beside it rather than
# inventing a second notion of "the same flow".
DEFAULT_COALESCE_WINDOW_SECONDS = 60.0

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
        socket: The connection observed when the window opened. Carried with
            the window rather than re-read at emission, because the success
            that closes a window belongs to a different request than the one
            that opened it.
    """

    count: int
    start_ms: int
    end_ms: int
    socket: SocketPair | None = None


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
        """Bound the reason text at the boundary, so no caller can bypass it."""
        object.__setattr__(self, "detail", _bounded(self.detail))


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
    """Collapses successful connections into one event per window.

    This client polls, so one event per success would put more volume on the
    collector's flow path than the passive capture beside it. A coalesced
    record still has to answer the question a connection log asks -- was there
    an attempt at time T -- so it carries the window's bounds and the attempt
    count, and every attempt in it shared one endpoint and service. It is not
    a sample.

    Only successes are coalesced. Failures never enter here.

    A window also remembers the socket that was live when it opened. The
    success that *closes* a window is a different request from the one that
    opened it, so reading the current socket at close time would report a
    connection that carried none of the successes being described.

    Not synchronized: every caller runs on the event loop, and none of the
    methods here await, so a window update cannot be interleaved with another.
    A lock would be synchronization against a concurrency this code does not
    have.
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
        self._socket: SocketPair | None = None

    @property
    def window_ms(self) -> int:
        """Window length in milliseconds."""
        return self._window_ms

    @property
    def pending(self) -> int:
        """Successes recorded but not yet emitted."""
        return self._count

    def record(self, now_ms: int, socket: SocketPair | None = None) -> Window | None:
        """Record one success, returning a window to emit when one closes.

        Args:
            now_ms: The time of the success, in epoch milliseconds.
            socket: The connection this success was carried on, if it opened
                one. Retained with the window it opens and reported when that
                window closes.

        Returns:
            The closed window when this success closed one, otherwise ``None``.
            The returned window never includes the success that closed it --
            that one opens the next window, so no attempt is counted twice or
            dropped -- and it carries the socket observed when it opened, not
            the one live at close time.
        """
        if self._window_ms == 0:
            return Window(1, now_ms, now_ms, socket)

        if self._start_ms is None:
            self._open(now_ms, socket)
            return None

        if now_ms - self._start_ms >= self._window_ms:
            closed = self._snapshot()
            self._open(now_ms, socket)
            return closed

        self._count += 1
        self._end_ms = now_ms
        # A window that opened without a socket adopts the first one seen
        # inside it: better a connection that carried some of these successes
        # than none at all.
        if self._socket is None:
            self._socket = socket
        return None

    def _open(self, now_ms: int, socket: SocketPair | None) -> None:
        """Start a new window at ``now_ms``, bound to ``socket``."""
        self._start_ms = now_ms
        self._end_ms = now_ms
        self._count = 1
        self._socket = socket

    def _snapshot(self) -> Window:
        """The current window as it stands, for emission."""
        assert self._start_ms is not None
        return Window(self._count, self._start_ms, self._end_ms or self._start_ms, self._socket)

    def take_expired(self, now_ms: int) -> Window | None:
        """Close the window if its configured length has elapsed.

        The window normally closes when the success *after* it arrives. A
        client whose successes stop -- it starts failing, or goes idle --
        would otherwise hold its last successes unreported until shutdown, so
        anything else passing through the emitter offers the clock a chance
        to close an expired window.

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
        self._socket = None
        return closed


def build_metadata(product_version: str | None = None) -> Metadata:
    """Build the OCSF ``metadata`` block identifying this client."""
    return Metadata(product=Product(name=PRODUCT_NAME, version=product_version))


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


def build_failure_event(
    outcome: Outcome,
    *,
    metadata: Metadata,
    socket_pair: SocketPair | None,
    server_endpoint: Endpoint | None,
    url: str | None = None,
) -> NetworkActivity | None:
    """Build one failure event. Failures are never coalesced.

    Returns ``None`` when there is no destination to report -- an event with
    neither endpoint says nothing about a connection, and the event class
    rejects it.
    """
    src, dst = _endpoints(socket_pair, server_endpoint)
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
            service=SERVICE_LABEL,
            url=url,
            connection_direction=ConnectionDirectionId.OUTBOUND,
        )

    return NetworkActivity.for_connection_failure(
        activity_id=outcome.activity_id,
        metadata=metadata,
        dst_endpoint=dst,
        src_endpoint=src,
        status_detail=outcome.detail,
        status_code=outcome.status_code,
        service=SERVICE_LABEL,
        url=url,
        connection_direction=ConnectionDirectionId.OUTBOUND,
    )


def build_coalesced_success_event(
    window: Window,
    *,
    metadata: Metadata,
    server_endpoint: Endpoint | None,
    url: str | None = None,
) -> NetworkActivity | None:
    """Build one event standing for a window of successful connections."""
    count, start_ms, end_ms = window.count, window.start_ms, window.end_ms
    src, dst = _endpoints(window.socket, server_endpoint)
    if dst is None:
        logger.debug("No destination endpoint for connection successes; not reporting")
        return None
    if count == 1:
        return NetworkActivity.for_connection_success(
            metadata=metadata,
            dst_endpoint=dst,
            src_endpoint=src,
            service=SERVICE_LABEL,
            url=url,
            time=end_ms,
            connection_direction=ConnectionDirectionId.OUTBOUND,
        )
    return NetworkActivity.for_coalesced_successes(
        metadata=metadata,
        dst_endpoint=dst,
        src_endpoint=src,
        count=count,
        start_time=start_ms,
        end_time=end_ms,
        service=SERVICE_LABEL,
        url=url,
        connection_direction=ConnectionDirectionId.OUTBOUND,
    )


class ConnectionTelemetryEmitter:
    """Turns request-path outcomes into events on the forwarder transport.

    One instance per client, attached as the client's connection observer
    (``client.http.connection_observer = emitter``). The client calls
    :meth:`begin_request` / :meth:`record_success` / :meth:`record_failure` /
    :meth:`on_connect` / :meth:`flush` through that seam; this decides what
    becomes an event and hands it to the forwarder manager.

    Nothing here may raise into the request path. Telemetry that can break
    the connection it describes is worse than absent telemetry, so every
    entry point swallows its own errors and logs them.
    """

    def __init__(
        self,
        forwarder: ForwarderManager | None,
        config: ConnectionTelemetryConfig,
        *,
        product_version: str | None = None,
    ) -> None:
        """Create an emitter.

        Args:
            forwarder: Where events go. ``None`` disables emission.
            config: Whether telemetry is on, its topic, and its window.
            product_version: Client version, for ``metadata.product``.
        """
        self._forwarder = forwarder
        self._config = config
        self._metadata = build_metadata(product_version)
        #: Failures to *construct or hand off* an event since start -- a bug
        #: in classification, the window, or serialization, or a directly
        #: attached forwarder that raises. The first logs at WARNING, the
        #: rest at DEBUG. A broker outage is not counted here: the forwarder
        #: manager absorbs its forwarders' errors by design, and broker-level
        #: delivery failure is visible in the transport's own statistics and
        #: its failed-forwarder retry loop instead.
        self.emit_failures = 0
        self._window = CoalescingWindow(config.coalesce_window_seconds)
        self._server_endpoint: Endpoint | None = None
        self._base_url: str | None = None

    @property
    def enabled(self) -> bool:
        """Whether events will actually be emitted."""
        return self._config.enabled and self._forwarder is not None

    def set_server(self, host: str | None, port: int | None, base_url: str | None = None) -> None:
        """Record the server this client targets, for events with no live socket.

        A connection that never established has no socket to report, but the
        address the client was trying to reach is exactly what the failure
        record needs to name.
        """
        self._base_url = _redact_url(base_url)
        if host and port:
            # OCSF types the endpoint's `ip` as an IP address; a configured
            # server URL usually names a host instead, and a DNS name in an
            # `ip` field is a schema violation a consumer may reject.
            try:
                ipaddress.ip_address(host)
            except ValueError:
                self._server_endpoint = Endpoint(hostname=host, port=port)
            else:
                self._server_endpoint = Endpoint(ip=host, port=port)
        else:
            self._server_endpoint = None

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
        """Record one successful exchange, emitting a window when one closes."""
        if not self.enabled:
            return
        try:
            moment = now_ms if now_ms is not None else _now_ms()
            closed = self._window.record(moment, _request_socket.get())
            if closed is not None:
                self._emit(
                    build_coalesced_success_event(
                        closed,
                        metadata=self._metadata,
                        server_endpoint=self._server_endpoint,
                        url=self._base_url,
                    )
                )
        except Exception:
            self._record_emit_failure("record a success")

    def record_failure(self, exc: BaseException) -> None:
        """Record one failed connection or exchange, with its reason.

        Never coalesced: each failure keeps its own event and its own reason.
        """
        if not self.enabled:
            return
        try:
            # The failure is also the clock's chance to close an expired
            # success window: those successes happened before this failure,
            # and holding them until the next success -- which may never
            # come -- would leave them unreported until shutdown. Guarded on
            # its own: a problem emitting the window must not cost the
            # failure record, which is the more important of the two.
            try:
                expired = self._window.take_expired(_now_ms())
                if expired is not None:
                    self._emit(
                        build_coalesced_success_event(
                            expired,
                            metadata=self._metadata,
                            server_endpoint=self._server_endpoint,
                            url=self._base_url,
                        )
                    )
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
                    metadata=self._metadata,
                    socket_pair=socket_pair,
                    server_endpoint=self._server_endpoint,
                    url=self._base_url,
                )
            )
        except Exception:
            self._record_emit_failure("record a failure")

    def flush(self) -> None:
        """Emit the open success window, if any.

        Called when the client closes: successes accumulated since the last
        window closed are still attempts the connection log accounts for.
        """
        if not self.enabled:
            return
        try:
            closed = self._window.flush()
            if closed is not None:
                self._emit(
                    build_coalesced_success_event(
                        closed,
                        metadata=self._metadata,
                        server_endpoint=self._server_endpoint,
                        url=self._base_url,
                    )
                )
        except Exception:
            self._record_emit_failure("flush")

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
        """Hand one event to the forwarder transport."""
        if event is None or self._forwarder is None:
            return
        self._forwarder.queue_event(
            EventFrame(
                payload=event.to_dict(),
                topic_suffix=self._config.topic_suffix,
                kind="connection-event",
            )
        )
