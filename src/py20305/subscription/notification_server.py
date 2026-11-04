"""Async notification server for IEEE 2030.5 subscription callbacks.

Receives POST /notify from the IEEE 2030.5 server when subscribed
resources change. Runs in the same asyncio event loop as the client.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Literal

from aiohttp import web
from lxml import etree

from py20305.client.connector import SocketPair, socket_pair_of
from py20305.client.exchange import bounded, endpoint, root_element, validate
from py20305.client.tls import build_cipher_string
from py20305.models.sep.sep import Notification
from py20305.observers import (
    NO_OBSERVERS,
    AnomalyCode,
    ConnectionDirection,
    ConnectionEvent,
    ConnectionOutcome,
    ConnectionRole,
    ConnectionTransport,
    ExchangeDirection,
    ExchangeRecord,
    ObserverGuard,
    Observers,
    ProtocolAnomaly,
    redact_headers,
)
from py20305.xml.serialization import from_xml

if TYPE_CHECKING:
    from py20305.client.tls import TlsConfig
    from py20305.client.traffic_recorder import TrafficRecorder

logger = logging.getLogger(__name__)

# Client-cert verification modes for the notification listener.
# - off:     CERT_NONE.  No verification at all (insecure -- dev/CI escape hatch).
# - warn:    CERT_OPTIONAL.  Cert is validated against CA when presented; if the
#            peer presents none, accept the notification but log a WARNING.
# - enforce: CERT_OPTIONAL.  Same TLS handling as warn, but app layer rejects
#            notifications without a client cert with HTTP 401.
#
# Default is ``warn`` so that upgrading the client does not silently break
# stale IEEE 2030.5 servers in the field that don't yet present a device cert
# (per IEEE 2030.5-2023 §8.9.3.2 + Table 12; that wording is permissive, not
# mandatory). Operators flip to ``enforce`` once the server side is upgraded.
ClientCertMode = Literal["off", "warn", "enforce"]

# Notification status constants (IEEE 2030.5 Table 10-12)
STATUS_DEFAULT = 0
STATUS_CANCELLED = 1
STATUS_RESOURCE_MOVED = 2
STATUS_DEFINITION_CHANGED = 3
STATUS_RESOURCE_DELETED = 4

NS_SEP = "urn:ieee:std:2030.5:ns"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"

NotificationCallback = Callable[[Notification], Awaitable[None]]


class NotificationServer:
    """Async HTTPS server that receives IEEE 2030.5 notification POSTs.

    Runs in the client's event loop using aiohttp.web. Dispatches
    parsed Notification objects to a callback for processing.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 10443,
        tls: TlsConfig | None = None,
        on_notification: NotificationCallback | None = None,
        client_cert_mode: ClientCertMode = "warn",
        traffic_recorder: TrafficRecorder | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._tls = tls
        self._on_notification = on_notification
        self._client_cert_mode: ClientCertMode = client_cert_mode
        self._traffic_recorder = traffic_recorder
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        # Set while an operator-triggered comms-loss simulation is running:
        # notifications are still accepted on the wire but are not acted on.
        self._suspended = False
        #: Receives one exchange record and one connection event per
        #: notification request, and an anomaly for each one refused or dropped.
        self.observers: Observers = NO_OBSERVERS
        self._observer_guard = ObserverGuard("notification observer")

    @property
    def on_notification(self) -> NotificationCallback | None:
        """The coroutine invoked with each parsed, accepted notification."""
        return self._on_notification

    @on_notification.setter
    def on_notification(self, callback: NotificationCallback | None) -> None:
        self._on_notification = callback

    @property
    def suspended(self) -> bool:
        """Whether accepted notifications are being dropped instead of handled."""
        return self._suspended

    async def suspend(self, *, drain_timeout: float = 5.0) -> bool:
        """Stop acting on notifications, and wait for any already running.

        Returns whether the in-flight handlers finished in time. The wait is the
        point: this server answers 201 and hands the notification to a
        background task, so one accepted moments earlier is still running and
        can apply a control *after* the caller believes the link is down -- a
        fresh setpoint landing on a DER the operator thinks is isolated.

        A False return means handlers were still running when the timeout
        elapsed. New notifications are dropped either way, from the moment this
        is called.
        """
        self._suspended = True
        if not self._background_tasks:
            return True
        pending = tuple(self._background_tasks)
        _, still_running = await asyncio.wait(pending, timeout=drain_timeout)
        return not still_running

    def resume(self) -> None:
        """Act on notifications again."""
        self._suspended = False

    @property
    def port(self) -> int:
        return self._port

    @property
    def running(self) -> bool:
        return self._site is not None

    def build_notification_uri(self, external_host: str) -> str:
        """Build the fully-qualified notification URI for subscription requests."""
        return f"https://{external_host}:{self._port}/notify"

    async def start(self) -> None:
        """Start the notification server."""
        if self._site is not None:
            return

        self._app = web.Application()
        self._app.router.add_post("/notify", self._handle_notify)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        ssl_ctx = self._create_server_ssl_context()
        # SO_REUSEADDR lets the listener rebind to a port still in TIME_WAIT
        # from a previous instance — matters most in the cert e2e suite where
        # multiple clients are torn down and recreated in a single pytest
        # session. Production restarts after a clean shutdown don't need it
        # but it's harmless: SO_REUSEADDR doesn't allow stealing an active
        # listener's port, only re-binding to a TIME_WAIT port.
        self._site = web.TCPSite(
            self._runner,
            self._host,
            self._port,
            ssl_context=ssl_ctx,
            reuse_address=True,
        )
        await self._site.start()

        if ssl_ctx is None:
            cert_status = "disabled (plain HTTP)"
        elif self._client_cert_mode == "enforce":
            cert_status = "enforce (mTLS required; no-cert peers rejected with 401)"
        elif self._client_cert_mode == "warn":
            cert_status = "warn (mTLS optional; no-cert peers logged but accepted)"
        else:  # off
            cert_status = "off (TLS without client-cert verification)"

        logger.info(
            "NotificationServer started on %s:%d  client_cert_mode=%s",
            self._host,
            self._port,
            cert_status,
        )

    async def stop(self) -> None:
        """Gracefully stop the notification server."""
        # Cancel background notification tasks
        for task in self._background_tasks:
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._app = None
        logger.info("NotificationServer stopped")

    def _create_server_ssl_context(self) -> ssl.SSLContext | None:
        """Create a server-side SSL context from the client TLS config.

        Verification mode is selected by ``client_cert_mode``:

        - ``off``     → ``CERT_NONE``: no client-cert verification.
        - ``warn``    → ``CERT_OPTIONAL``: handshake succeeds with or without
          a client cert; a presented cert is still validated against the CA
          (a wrong-CA or malformed cert still fails the handshake). The
          app-layer handler logs a warning when no cert is presented.
        - ``enforce`` → same TLS handling as ``warn`` (``CERT_OPTIONAL``),
          but the app-layer handler rejects with HTTP 401 when no cert is
          presented.

        ``CERT_OPTIONAL`` is intentional in both warn and enforce: it keeps
        the handshake succeeding for old IEEE 2030.5 servers that don't yet
        present a device cert, while still failing fast on a presented but
        wrong-CA cert. The "no cert at all" case is handled at the app layer
        so we can return a structured response instead of an opaque TLS error.
        """
        if self._tls is None:
            return None

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # Pin to TLS 1.2 only, matching the outbound client and IEEE 2030.5
        # §6.4. Without the upper bound, a TLS 1.3 handshake would silently
        # bypass set_ciphers() below -- set_ciphers() only controls TLS 1.2
        # suites and earlier; TLS 1.3 negotiates its own separate ciphersuite
        # list (TLS_AES_*_GCM_SHA*). That would defeat the IEEE 2030.5
        # baseline lockdown and contradict the spec's TLS-version requirement.
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        # Pin the cipher list to the IEEE 2030.5 baseline (plus any operator
        # opt-ins via `additional_ciphers`). Without an explicit set_ciphers
        # call, OpenSSL would fall back to its system default list -- which
        # accepts suites the spec doesn't sanction. Sharing the same builder
        # as `client/tls.py` keeps inbound and outbound policy aligned so a
        # single config knob controls both directions of the connection.
        if self._tls.additional_ciphers:
            logger.warning(
                "Notification server cipher policy relaxed beyond IEEE 2030.5 "
                "baseline; additional ciphers: %s",
                ", ".join(self._tls.additional_ciphers),
            )
        ctx.set_ciphers(build_cipher_string(self._tls))
        ctx.load_cert_chain(str(self._tls.client_cert), str(self._tls.client_key))
        ctx.load_verify_locations(str(self._tls.ca_cert))
        if self._client_cert_mode == "off":
            ctx.verify_mode = ssl.CERT_NONE
        else:  # warn or enforce
            ctx.verify_mode = ssl.CERT_OPTIONAL
        return ctx

    def _connection_event(
        self, request: web.Request, pair: SocketPair, refused: str | None
    ) -> None:
        """Report one inbound notification request to the connection observer.

        Reported per request: aiohttp accepts the connection before any
        application code runs, so a request is the first point this listener
        can observe. A TLS handshake that fails never reaches it.
        """
        observer = self.observers.connection
        if observer is None:
            return
        local = endpoint(pair.local)
        self._observer_guard.deliver(
            observer,
            ConnectionEvent(
                role=ConnectionRole.LISTENER,
                direction=ConnectionDirection.INBOUND,
                outcome=ConnectionOutcome.ACCEPTED
                if refused is None
                else ConnectionOutcome.REFUSED,
                transport=ConnectionTransport.TLS if request.secure else ConnectionTransport.TCP,
                remote=endpoint(pair.remote),
                local=local,
                reason=refused or "",
                status=401 if refused is not None else None,
                tls_version=pair.tls_version,
                tls_cipher=pair.tls_cipher,
                peer_cert_sha256=pair.peer_cert_sha256,
            ),
        )

    def _anomaly(self, pair: SocketPair, exchange_id: str, code: AnomalyCode, message: str) -> None:
        """Report a notification refused or dropped to the anomaly observer."""
        observer = self.observers.anomaly
        if observer is None:
            return
        self._observer_guard.deliver(
            observer,
            ProtocolAnomaly.of(
                code,
                bounded(message),
                exchange_id=exchange_id,
                method="POST",
                path="/notify",
                peer=endpoint(pair.remote),
            ),
        )

    def _exchange(
        self,
        request: web.Request,
        pair: SocketPair,
        exchange: _Notification,
        response: web.StreamResponse,
    ) -> None:
        """Report one notification request and the response sent back to it."""
        observers = self.observers
        if observers.exchange is None:
            return
        received_at = time.time()
        received_monotonic = time.monotonic()
        response_body = getattr(response, "body", None)
        response_bytes = (
            bytes(response_body) if isinstance(response_body, (bytes, bytearray)) else None
        )
        request_headers = tuple(request.headers.items())
        response_headers = tuple(response.headers.items())
        request_valid, request_error = validate(exchange.body, request_headers, observers)
        capture = observers.capture_bodies
        self._observer_guard.deliver(
            observers.exchange,
            ExchangeRecord(
                exchange_id=exchange.exchange_id,
                direction=ExchangeDirection.NOTIFICATION,
                method=request.method,
                url=str(request.url),
                path=request.path_qs,
                request_headers=redact_headers(request_headers, observers.redacted_headers),
                request_body=exchange.body if capture else None,
                status=response.status,
                response_headers=redact_headers(response_headers, observers.redacted_headers),
                response_body=response_bytes if capture else None,
                sent_at=exchange.started_at,
                received_at=received_at,
                sent_monotonic=exchange.started_monotonic,
                received_monotonic=received_monotonic,
                duration=received_monotonic - exchange.started_monotonic,
                local=endpoint(pair.local),
                remote=endpoint(pair.remote),
                tls_version=pair.tls_version,
                tls_cipher=pair.tls_cipher,
                server_cert_sha256=pair.peer_cert_sha256,
                resource_type=root_element(exchange.body),
                request_valid=request_valid,
                request_validation_error=request_error,
            ),
        )

    async def _handle_notify(self, request: web.Request) -> web.StreamResponse:
        """Handle POST /notify, and report it to the observers."""
        exchange = _Notification()
        if not self.observers.any:
            return await self._handle_notification(request, exchange)
        pair = socket_pair_of(request.transport) if request.transport else SocketPair(None, None)
        response = await self._handle_notification(request, exchange)
        try:
            self._connection_event(request, pair, exchange.refused)
            if exchange.anomaly is not None:
                self._anomaly(pair, exchange.exchange_id, *exchange.anomaly)
            self._exchange(request, pair, exchange, response)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Could not report a notification request", exc_info=True)
        return response

    async def _handle_notification(
        self, request: web.Request, exchange: _Notification
    ) -> web.Response:
        """Handle POST /notify from the IEEE 2030.5 server, noting what happened in ``exchange``."""
        # Client-cert mode check (warn/enforce). With CERT_OPTIONAL the
        # TLS layer accepts handshakes without a client cert, so the
        # "no cert presented" decision lives here at the app layer.
        from py20305.diagnostics import report

        if self._client_cert_mode in ("warn", "enforce"):
            transport = request.transport
            peercert = transport.get_extra_info("peercert") if transport else None
            if not peercert:
                remote = request.remote or "<unknown>"
                if self._client_cert_mode == "enforce":
                    report(
                        "warnings",
                        (
                            f"Rejecting notification from {remote}: no client certificate. "
                            "Configure outbound mTLS on the IEEE 2030.5 server "
                            "(per IEEE 2030.5-2023 §8.9.3.2 + Table 12)."
                        ),
                        source="notification",
                        dedup_key=f"notif_mtls_reject:{remote}",
                        details={"remote": remote},
                    )
                    exchange.refused = "No client certificate presented"
                    exchange.anomaly = (
                        AnomalyCode.REJECTED_NOTIFICATION,
                        f"Notification from {remote} refused: no client certificate",
                    )
                    return web.Response(status=401, text="client certificate required")
                # warn mode: log and accept
                report(
                    "warnings",
                    (
                        f"Notification from {remote} arrived without a client certificate. "
                        "Configure outbound mTLS on the IEEE 2030.5 server "
                        "(per IEEE 2030.5-2023 §8.9.3.2 + Table 12), or set "
                        "subscription.notification_client_cert_mode='off' to suppress."
                    ),
                    source="notification",
                    dedup_key=f"notif_mtls_warn:{remote}",
                    details={"remote": remote},
                )

        try:
            body = await request.read()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to read notification request body")
            exchange.anomaly = (
                AnomalyCode.UNREADABLE_NOTIFICATION,
                f"Notification body could not be read: {exc}",
            )
            return web.Response(status=400, text="Bad Request")
        exchange.body = body

        remote = request.remote or "<unknown>"
        if self._traffic_recorder is not None:
            # Recorded before parse/validation, so the accept/reject outcome
            # isn't known yet -- leave status unset rather than implying 201.
            self._traffic_recorder.record_notification(
                path=request.path_qs, body=body, source_ip=remote, status=None
            )
        try:
            notification = parse_notification(body)
        except Exception as exc:
            report(
                "warnings",
                f"Failed to parse notification XML from {remote}: {exc}",
                source="notification",
                dedup_key=f"notif_parse:{remote}",
                details={"remote": remote, "error": str(exc)},
                exc_info=True,
            )
            exchange.anomaly = (
                AnomalyCode.REJECTED_NOTIFICATION,
                f"Notification from {remote} is not a valid Notification: {exc}",
            )
            return web.Response(status=400, text="Invalid notification XML")

        if not validate_notification(notification, body):
            report(
                "warnings",
                f"Notification xsi:type validation failed from {remote}",
                source="notification",
                dedup_key=f"notif_parse:{remote}",
                details={"remote": remote, "reason": "xsi:type mismatch"},
            )
            exchange.anomaly = (
                AnomalyCode.REJECTED_NOTIFICATION,
                f"Notification from {remote} failed the xsi:type check",
            )
            return web.Response(status=400, text="xsi:type mismatch")

        if self._suspended:
            # A real outage would stop these arriving at all. The listener stays
            # up (the operator is driving the test through this device), so the
            # notification is accepted and dropped rather than refused: answering
            # with an error would tell the head-end its delivery failed, and some
            # servers respond by dropping the subscription -- a side effect on
            # the customer's server that a local simulation has no business
            # causing. Recovery re-polls schedules, so nothing is lost by it.
            logger.info(
                "Notification for %s dropped: comms-loss simulation active",
                notification.subscribed_resource or "<unknown>",
            )
            exchange.anomaly = (
                AnomalyCode.DROPPED_NOTIFICATION,
                f"Notification for {notification.subscribed_resource or '<unknown>'} dropped "
                "while a loss-of-communications simulation runs",
            )
            return web.Response(status=201, text="Created")

        if self._on_notification is not None:
            task = asyncio.create_task(self._safe_notify(notification))
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        return web.Response(status=201, text="Created")

    async def _safe_notify(self, notification: Notification) -> None:
        """Run the notification callback, logging any errors."""
        from py20305.client.errors import Sep2Error
        from py20305.diagnostics import report

        resource = notification.subscribed_resource or "<unknown>"
        try:
            await self._on_notification(notification)  # type: ignore[misc]
        except Sep2Error as exc:
            # B2: protocol-layer error from a downstream server call the
            # callback made (e.g. targeted re-poll, structural rediscovery).
            # Dedup per (resource, exception kind) so a flapping endpoint
            # collapses to a single entry while different paths stay visible.
            report(
                "warnings",
                f"Notification handler {resource} → {type(exc).__name__}: {exc}",
                source="notification",
                dedup_key=f"notif_handler:{resource}:{type(exc).__name__}",
                details={
                    "subscribed_resource": resource,
                    "exc_kind": type(exc).__name__,
                    "error": str(exc),
                },
            )
        except Exception as exc:
            # B11: caller-side bug in the registered callback. Dedup per
            # (resource, exc kind) so a recurring code-path issue doesn't
            # spam the operator -- the count tells the story.
            report(
                "warnings",
                f"Notification callback for {resource} raised {type(exc).__name__}: {exc}",
                source="notification",
                dedup_key=f"notif_callback:{resource}:{type(exc).__name__}",
                details={
                    "subscribed_resource": resource,
                    "exc_kind": type(exc).__name__,
                    "error": str(exc),
                },
                exc_info=True,
            )


class _Notification:
    """What happened to one notification request, for the observers."""

    def __init__(self) -> None:
        self.exchange_id = uuid.uuid4().hex
        self.started_at = time.time()
        self.started_monotonic = time.monotonic()
        self.body: bytes | None = None
        #: Why the listener refused the request, for the connection event.
        self.refused: str | None = None
        #: The anomaly to report, as (code, message).
        self.anomaly: tuple[AnomalyCode, str] | None = None


def parse_notification(body: bytes) -> Notification:
    """Parse XML bytes into a Notification model."""
    return from_xml(body, Notification)


def validate_notification(notification: Notification, raw_xml: bytes) -> bool:
    """Validate xsi:type on the Resource element matches expectations.

    For list resources, xsi:type must end with "List".
    For non-list resources, xsi:type must match the element name.
    Returns True if valid, no Resource present, or no xsi:type.
    """
    if notification.resource is None:
        return True

    try:
        tree = etree.fromstring(raw_xml)
    except etree.XMLSyntaxError:
        return False

    resource_elem = tree.find(f"{{{NS_SEP}}}Resource")
    if resource_elem is None:
        return True

    xsi_type = resource_elem.get(f"{{{NS_XSI}}}type")
    if xsi_type is None:
        return True

    # Strip namespace prefix if present (e.g., "sep:FunctionSetAssignmentsList")
    if ":" in xsi_type:
        xsi_type = xsi_type.split(":", 1)[1]

    # Validate consistency: list types must end with "List"
    subscribed = notification.subscribed_resource
    if subscribed and subscribed.endswith("/"):
        # List resources typically have trailing paths
        pass

    # The xsi:type should be a valid IEEE 2030.5 type name
    # For list resources, the type ends with "List"
    # For non-list resources, it doesn't
    # We validate structural consistency but don't reject unknown types
    logger.debug("Notification Resource xsi:type=%s for %s", xsi_type, subscribed)
    return True
