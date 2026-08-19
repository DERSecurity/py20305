"""Async HTTP client for IEEE 2030.5 servers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlparse

import aiohttp

from py20305.client.connector import Ieee2030TCPConnector, SocketPair
from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2NoContentError,
    Sep2PayloadError,
    Sep2ProtocolError,
    Sep2RateLimitError,
    Sep2RedirectError,
    Sep2TlsError,
)
from py20305.client.observer import ConnectionObserver
from py20305.client.retry import RetryPolicy, with_retry
from py20305.client.timebase import ServerTimebase
from py20305.client.tls import (
    CertChainError,
    TlsConfig,
    create_ssl_context,
    verify_ieee2030_5_chain,
)
from py20305.xml.serialization import (
    APPLICATION_SEP_XML,
    XmlParseError,
    from_xml,
    to_xml,
)


def _parse_body(body: bytes, model_type: type[T], path: str) -> T:
    """Parse a response body, translating XmlParseError into Sep2PayloadError.

    Centralized so every GET path produces the same clean diagnostic when
    a server returns HTTP 200 with a malformed or empty body. The
    ``path`` is attached so log lines name the offending endpoint.
    """
    try:
        return from_xml(body, model_type)
    except XmlParseError as exc:
        raise Sep2PayloadError(
            f"GET {path}: {exc}",
            path=path,
            body_length=exc.body_length,
        ) from exc


if TYPE_CHECKING:
    from py20305.client.traffic_recorder import TrafficRecorder
    from py20305.forwarders import ForwarderManager, MessageDirection

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(sock_connect=5, sock_read=15)

# Methods the generic upstream proxy will send. An allowlist rather than a
# passthrough: the value reaches aiohttp's request line, and the debugger this
# backs is aimed at a live utility server.
_RAW_PROXY_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})

# Strong references for fire-and-forget background tasks (e.g. closing an
# orphaned aiohttp session after `update_client_cert`). Asyncio holds only
# weak refs to tasks via the loop, so without this the GC could collect a
# pending task mid-flight, prematurely cancelling the close.
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


class ResourceVersionCache:
    """Cache mRID/version per resource path to skip unchanged resources.

    IEEE 4.8: "Clients SHOULD only check the mRID and version of a resource,
    if applicable, to determine if the resource has been modified."
    """

    def __init__(self) -> None:
        self._versions: dict[str, tuple[bytes, int]] = {}

    def is_changed(self, path: str, resource: object) -> bool:
        """Check if a resource has changed since last seen.

        Returns True if the resource is new or has a different mRID/version.
        Always returns True for resources without mRID/version attributes.
        """
        mrid = getattr(resource, "m_rid", None)
        version = getattr(resource, "version", None)
        if mrid is None or version is None:
            return True

        mrid_val = mrid.value if hasattr(mrid, "value") else mrid
        version_val = version if isinstance(version, int) else 0

        prev = self._versions.get(path)
        current = (mrid_val, version_val)
        self._versions[path] = current

        if prev is None:
            return True
        return prev != current

    def clear(self) -> None:
        self._versions.clear()

    def __len__(self) -> int:
        return len(self._versions)


class Sep2Client:
    """Async client for IEEE 2030.5 REST API.

    Optionally integrates with ForwarderManager to capture HTTP exchanges
    for security auditing (e.g., a security monitoring system over MQTT).
    """

    def __init__(
        self,
        base_url: str,
        tls: TlsConfig | None = None,
        retry: RetryPolicy | None = None,
        timeout: aiohttp.ClientTimeout | None = None,
        forwarder: ForwarderManager | None = None,
        traffic_recorder: TrafficRecorder | None = None,
        server_2018_compat: bool = False,
        always_send_alarm_status: bool = False,
        request_headers: dict[str, str] | None = None,
        timebase: ServerTimebase | None = None,
        connection_observer: ConnectionObserver | None = None,
    ) -> None:
        """Initialize Sep2Client.

        Args:
            base_url: Base URL for the IEEE 2030.5 server
            tls: TLS configuration for mTLS
            retry: Retry policy for transient failures
            timeout: HTTP timeout settings
            forwarder: Optional ForwarderManager for message forwarding
            traffic_recorder: Optional TrafficRecorder for the Live Traffic view
            server_2018_compat: IEEE 2030.5-2018 compatibility mode
            always_send_alarm_status: Always emit <alarmStatus> in DERStatus PUTs
                (explicit all-zero bitmap when no alarms), instead of omitting it
            request_headers: Extra headers to send on every outbound request
                (e.g. a third-party API token); Accept/Content-Type always win
            timebase: Shared server timebase; carried here so free functions
                (discovery) and telemetry managers holding only this client can
                observe/read server time. Defaults to a fresh identity instance.
            connection_observer: Optional observer for this client's own
                connection outcomes and established sockets — what a passive
                capture beside the client cannot recover from inside TLS. May
                also be attached after construction via the
                ``connection_observer`` property. No observer, no reporting.
        """
        self._base_url = base_url.rstrip("/")
        self._retry = retry or RetryPolicy()
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._tls_config = tls
        self._ssl: ssl.SSLContext | bool = create_ssl_context(tls) if tls else False
        self._session: aiohttp.ClientSession | None = None
        self._forwarder = forwarder
        self._traffic_recorder = traffic_recorder
        self._csip_aus_mode = False
        self._timebase = timebase or ServerTimebase()
        self._server_2018_compat = server_2018_compat
        self._always_send_alarm_status = always_send_alarm_status
        # Operator-supplied extra headers sent on every outbound request (e.g. a
        # third-party API token). Protocol-critical Accept/Content-Type always win.
        self._request_headers: dict[str, str] = dict(request_headers or {})
        self._chain_validated = False
        self._server_alive = True
        self._last_error: str | None = None
        # Connectivity health (any-method, not just GET polls): epoch seconds of
        # the last reachable round-trip and the count of consecutive unreachable
        # attempts. A reachable result is any HTTP response (even a 4xx/5xx) or a
        # 200-with-bad-body; only transport failures (connect/timeout/TLS) count
        # as unreachable.
        self._last_contact_epoch: int | None = None
        # Epoch of the last reachable request over a chain-validated connection.
        # Since Ieee2030TCPConnector runs the IEEE chain audit at handshake, every
        # established connection is validated, so any reachable request -- GET or
        # telemetry PUT/POST -- refreshes this. Retained (mirrors
        # _last_contact_epoch now) for the connectivity probe and status surface.
        self._last_validated_epoch: int | None = None
        self._consecutive_failures = 0
        # Carried as a default header on every request (see `_default_headers`).
        # Off by default; operator opts in via `TlsSettings.send_lfdi_header`
        # for proxy-fronted deployments that strip the client cert before the
        # backend sees it. Header name defaults to `LFDI` but is configurable
        # per peer convention via `TlsSettings.lfdi_header_name`.
        self._send_lfdi_header: bool = tls.send_lfdi_header if tls is not None else False
        self._lfdi_header_name: str = tls.lfdi_header_name if tls is not None else "LFDI"

        self._schema_path: Path | None = None  # XSD path, set via set_schema_validator()

        # Parse server info for message forwarding
        parsed = urlparse(self._base_url)
        self._server_host = parsed.hostname or ""
        self._server_port = parsed.port or (443 if parsed.scheme == "https" else 80)

        # Compute client LFDI from TLS certificate for message attribution
        # *and* the LFDI request header (see _default_headers).
        self._client_lfdi: str | None = self._compute_client_lfdi(tls)

        # URL prefix → device LFDI mapping for forwarded messages.
        # Populated after discovery so each request can be attributed to
        # the correct device instead of using the client's cert LFDI.
        self._device_lfdi_by_prefix: dict[str, str] = {}

        self._connection_observer = connection_observer

    @property
    def connection_observer(self) -> ConnectionObserver | None:
        """Return the attached connection observer, if any."""
        return self._connection_observer

    @connection_observer.setter
    def connection_observer(self, value: ConnectionObserver | None) -> None:
        """Attach (or detach) the connection observer.

        Settable after construction because an embedder typically reads the
        configuration that decides on observation later than it builds the
        client. The connector's socket callback binds late, so connections
        established by an already-open session still reach a newly attached
        observer.
        """
        self._connection_observer = value

    def _dispatch_connect(self, pair: SocketPair) -> None:
        """Forward an established socket to the observer, if one is attached.

        Bound once into the connector at session creation; indirection rather
        than the observer's own method so attaching or replacing the observer
        mid-session takes effect without a session reset.
        """
        if self._connection_observer is not None:
            self._connection_observer.on_connect(pair)

    @property
    def forwarder(self) -> ForwarderManager | None:
        """Return the forwarder manager."""
        return self._forwarder

    @forwarder.setter
    def forwarder(self, value: ForwarderManager | None) -> None:
        """Set the forwarder manager."""
        self._forwarder = value

    @property
    def traffic_recorder(self) -> TrafficRecorder | None:
        """Return the Live Traffic recorder, if attached."""
        return self._traffic_recorder

    @traffic_recorder.setter
    def traffic_recorder(self, value: TrafficRecorder | None) -> None:
        self._traffic_recorder = value

    def update_device_lfdi_prefixes(self, prefix_map: dict[str, str]) -> None:
        """Update URL prefix → device LFDI mapping for message attribution.

        Called after discovery to map EndDevice and FSA path prefixes to
        their device LFDIs. This allows _forward_message to attribute
        each request to the correct device.

        Args:
            prefix_map: Maps URL prefixes (e.g., "/edev/2", "/FDA-SGA-TFA")
                       to hex LFDI strings.
        """
        self._device_lfdi_by_prefix = dict(prefix_map)
        logger.debug(
            "Updated device LFDI prefixes: %d entries",
            len(self._device_lfdi_by_prefix),
        )

    def _resolve_device_lfdi(self, uri: str) -> str | None:
        """Resolve a request URI to a device LFDI using prefix matching.

        Returns the LFDI of the device whose EndDevice or FSA path is a
        prefix of the given URI, or None if no match.
        """
        for prefix, lfdi in self._device_lfdi_by_prefix.items():
            if uri.startswith(prefix + "/") or uri == prefix:
                return lfdi
        return None

    def set_schema_validator(self, schema_dir: str) -> None:
        """Initialize the XSD schema validator for message validation.

        The entry point is ``sep2_schema_2023.xsd``.  A directory still holding
        the pre-0.14 ``sep.xsd`` name is accepted with a deprecation warning:
        that file was the IEEE 2030.5-2023 schema under a name that did not say
        so, and an operator directory carrying it should keep validating across
        the upgrade rather than fall silently to no validation at all.

        Args:
            schema_dir: Path to directory containing IEEE 2030.5 XSD files.
        """
        sep_xsd = Path(schema_dir) / "sep2_schema_2023.xsd"
        if sep_xsd.exists():
            self._schema_path = sep_xsd
            logger.info("XSD schema validator configured: %s", sep_xsd)
            return

        from py20305.diagnostics import report

        legacy_xsd = Path(schema_dir) / "sep.xsd"
        if legacy_xsd.exists():
            self._schema_path = legacy_xsd
            report(
                "warnings",
                f"Using deprecated XSD entry point {legacy_xsd}; "
                f"rename it to sep2_schema_2023.xsd to name the IEEE 2030.5 edition "
                f"the file actually contains",
                source="client",
                dedup_key="xsd_legacy_name",
                details={"path": str(legacy_xsd), "expected": str(sep_xsd)},
            )
            return

        report(
            "warnings",
            f"XSD schema not found at {sep_xsd}, validation disabled",
            source="client",
            dedup_key="xsd_missing",
            details={"path": str(sep_xsd)},
        )

    @property
    def csip_aus_mode(self) -> bool:
        """Whether CSIP-AUS namespace should be included in XML payloads."""
        return self._csip_aus_mode

    @csip_aus_mode.setter
    def csip_aus_mode(self, value: bool) -> None:
        self._csip_aus_mode = value

    @property
    def server_2018_compat(self) -> bool:
        """Whether IEEE 2030.5-2018 compatibility mode is enabled."""
        return self._server_2018_compat

    @property
    def always_send_alarm_status(self) -> bool:
        """Whether DERStatus PUTs always include an explicit <alarmStatus>."""
        return self._always_send_alarm_status

    @staticmethod
    def _compute_client_lfdi(tls: TlsConfig | None) -> str | None:
        """Compute the client's LFDI from the configured cert.

        Returns None on failure (cert missing, malformed, or no TLS at all);
        a WARNING is logged so the operator sees the regression. Used both
        at init time and when `update_client_cert` rotates the cert at
        runtime -- without the recompute on rotation, the LFDI header would
        keep the OLD cert's identity and break peers that authenticate via
        the header.
        """
        if tls is None:
            return None
        try:
            from py20305.security import compute_lfdi

            cert_pem = tls.client_cert.read_text()
            lfdi = compute_lfdi(cert_pem)
            logger.info("Client LFDI: %s", lfdi)
            return lfdi
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Could not compute client LFDI from cert: {e}",
                source="client",
                dedup_key="client_lfdi",
                details={"error": str(e)},
            )
            return None

    def _default_headers(self) -> dict[str, str]:
        """Default headers for every request.

        IEEE 2030.5 §6.11.7.2 says servers SHOULD derive the client's LFDI
        from the TLS cert, so an LFDI request header is non-spec and off by
        default. Operators enable it for utility deployments fronted by a
        TLS-terminating proxy that strips the client cert before the backend
        sees it:
        - `tls.send_lfdi_header = true` opts in.
        - `tls.lfdi_header_name` overrides the header name when a peer uses
          a different convention (e.g. `X-LFDI`). Defaults to `LFDI`.
        """
        # Operator-supplied headers form the base; the protocol-critical
        # Accept/Content-Type below always override them so a custom header can't
        # break content negotiation.
        headers: dict[str, str] = dict(self._request_headers)
        headers["Accept"] = APPLICATION_SEP_XML
        headers["Content-Type"] = APPLICATION_SEP_XML
        if self._client_lfdi is not None and self._send_lfdi_header:
            # Cert-bearer's LFDI -- identifies the *client* (the client's
            # cert) to the proxy, not the per-device LFDI implied by the
            # request URL. See `_resolve_device_lfdi` for that one. This is
            # the right semantic for proxy-strip-the-cert deployments where
            # the proxy needs to tell the backend WHO authenticated; per-
            # device routing happens in the URL path / request body.
            headers[self._lfdi_header_name] = self._client_lfdi
        return headers

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # When TLS is configured, use the connector that runs the IEEE 2030.5
            # PKI-profile chain audit at handshake time, so it gates every request
            # method uniformly (not just the first GET). Plain-HTTP sessions
            # (self._ssl is False) use aiohttp's default connector.
            connector = (
                Ieee2030TCPConnector(on_connect=self._dispatch_connect)
                if isinstance(self._ssl, ssl.SSLContext)
                else None
            )
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
                headers=self._default_headers(),
                connector=connector,
            )
        return self._session

    def _forward_message(
        self,
        direction: MessageDirection,
        message_type: str,
        content: Any,
        http_method: str,
        uri: str,
        status_code: int | None = None,
        metadata: dict[str, Any] | None = None,
        raw_xml: bytes | None = None,
    ) -> None:
        """Forward a message to the forwarder manager if configured.

        If a schema validator is configured and raw_xml is provided,
        the XML is validated against the IEEE 2030.5 XSD schema.
        Both valid and invalid messages are forwarded.
        """
        if self._forwarder is None or not self._forwarder.running:
            return

        # Validate against XSD if schema path and raw XML are available
        is_valid = True
        validation_error = None
        if raw_xml is not None and self._schema_path is not None:
            from py20305.xml.serialization import validate_xml_result

            is_valid, validation_error = validate_xml_result(raw_xml, self._schema_path)
            if not is_valid:
                logger.debug(
                    "XSD validation failed for %s %s: %s",
                    http_method,
                    uri,
                    validation_error,
                )

        from py20305.forwarders import MessageFrame

        # Inject device LFDI into metadata for message attribution.
        # Prefer the per-device LFDI resolved from the URL, fall back to
        # the client's own cert LFDI.
        effective_metadata = dict(metadata) if metadata else {}
        if "lfdi" not in effective_metadata:
            device_lfdi = self._resolve_device_lfdi(uri)
            if device_lfdi:
                effective_metadata["lfdi"] = device_lfdi
            elif self._client_lfdi:
                effective_metadata["lfdi"] = self._client_lfdi

        frame = MessageFrame(
            direction=direction,
            message_type=message_type,
            content=content,
            timestamp=datetime.now(UTC),
            is_valid=is_valid,
            validation_error=validation_error,
            http_method=http_method,
            uri=uri,
            status_code=status_code,
            server_host=self._server_host,
            server_port=self._server_port,
            metadata=effective_metadata,
        )
        try:
            self._forwarder.queue_message(frame)
        except Exception as e:
            logger.debug("Failed to forward message: %s", e)

    @property
    def host(self) -> str:
        """Server hostname extracted from the base URL."""
        return self._server_host

    @property
    def server_alive(self) -> bool:
        """Whether the server is a reachable, cert-chain-valid IEEE 2030.5 peer.

        Set True by any reachable response of any method, because the IEEE chain
        audit runs at the TLS handshake (``Ieee2030TCPConnector``) -- so a
        connection that carries a GET, PUT, or POST has already passed it. Any
        transport failure (connect/timeout/TLS handshake, including a rejected
        chain) sets it False. For a pure "did we recently reach the server"
        staleness check, prefer ``last_contact_epoch`` / ``consecutive_failures``.
        """
        return self._server_alive

    @property
    def last_error(self) -> str | None:
        """Description of the last connection error, if any."""
        return self._last_error

    @property
    def client_lfdi(self) -> str | None:
        """The client's own client LFDI (derived from its TLS cert), or None."""
        return self._client_lfdi

    @property
    def timebase(self) -> ServerTimebase:
        """Shared application-level server timebase (see client/timebase.py)."""
        return self._timebase

    @property
    def last_contact_epoch(self) -> int | None:
        """Epoch seconds of the last reachable round-trip, or ``None`` if the
        server has never been reached this session."""
        return self._last_contact_epoch

    @property
    def last_validated_epoch(self) -> int | None:
        """Epoch seconds of the last reachable request over a validated
        connection, or ``None`` if the server hasn't been reached this session.

        Because the IEEE chain audit runs at the TLS handshake, every established
        connection is validated, so this now tracks the same instants as
        ``last_contact_epoch``. Kept as the signal the connectivity probe gates
        on."""
        return self._last_validated_epoch

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive unreachable attempts since the last contact."""
        return self._consecutive_failures

    def _record_contact(self, *, reachable: bool) -> None:
        """Update connectivity health from a request outcome.

        ``server_alive`` means a reachable, IEEE-2030.5-cert-chain-valid peer, and
        both guarantees now hold for *any* established connection: basic
        ``CERT_REQUIRED`` verification and the IEEE PKI-profile audit
        (``Ieee2030TCPConnector``, run at the TLS handshake) gate every connection
        before a byte of any request -- GET, PUT, POST, DELETE -- is sent over it.
        So any reachable response (a success, or a 4xx/5xx/429/301 the server
        actually answered) asserts the peer is alive and valid; only a transport
        failure (connect/timeout/TLS handshake, including a rejected chain) flips
        it False. This is what lets telemetry PUT/POST keep ``server_alive`` fresh
        without a validating GET -- the method-specific distinction the chain audit
        used to require is gone now that the audit runs at handshake.
        """
        if not reachable:
            self._server_alive = False
            self._consecutive_failures += 1
            return
        self._last_contact_epoch = int(time.time())
        self._consecutive_failures = 0
        self._server_alive = True
        self._last_validated_epoch = self._last_contact_epoch

    async def _retry_observed(self, do_fn: Callable[[], Awaitable[T]]) -> T:
        """Run one logical request through retry, reporting its outcome.

        Every request method that signals its outcome by raising funnels
        through here, so the connection observer is applied in one place
        rather than repeated down each method's except ladder. The raw probe
        methods (``get_raw``, ``request_raw``) are deliberately outside: they
        are operator-driven diagnostics that report their outcome in-band to
        their caller, not protocol traffic to account for. With no observer
        attached this is exactly ``with_retry``.

        Reporting is per logical request, not per retry attempt: the retry
        wrapper collapses exhausted transport attempts into one
        ``Sep2ConnectionError``, so the individual attempts are not
        distinguishable here.
        """
        observer = self._connection_observer
        if observer is None:
            return await with_retry(self._retry, do_fn, peer=self._server_host)
        # Scope socket attribution to this request: a connection established
        # while handling it is this request's, and one left over from an
        # earlier request in the same task is not.
        observer.begin_request()
        try:
            result = await with_retry(self._retry, do_fn, peer=self._server_host)
        except Sep2NoContentError:
            # A 204 is a successful, validated contact that happens to signal
            # itself by raising. Routing it through the failure branch would
            # leave it classified as "not a connection outcome" and emit
            # nothing at all, so every empty optional resource would go
            # unrecorded — an under-count of attempts in a log whose whole
            # purpose is to account for them.
            observer.record_success()
            raise
        except BaseException as exc:
            observer.record_failure(exc)
            raise
        observer.record_success()
        return result

    async def _send_tracked(self, do_fn: Callable[[], Awaitable[T]]) -> T:
        """Run a non-GET request via retry, recording connectivity health once
        per logical request.

        A returned result or a ``Sep2ProtocolError`` (the server responded, even
        with an error status) counts as contact; a transport failure
        (TLS/connect/timeout, surfaced as ``ssl.SSLError`` or ``OSError``) counts
        as unreachable. GET keeps its own inline recording (cert-chain / payload
        nuances), so it does not route through here.
        """
        try:
            result = await self._retry_observed(do_fn)
        except (Sep2ConnectionError, Sep2TlsError) as exc:
            # ``with_retry`` wraps exhausted transport failures (connect/timeout,
            # TLS handshake) into these -> server unreachable.
            self._record_contact(reachable=False)
            self._last_error = str(exc)
            raise
        except (Sep2ProtocolError, Sep2PayloadError, Sep2RateLimitError, Sep2RedirectError):
            # The server answered (error status / unusable body / 429 / 301) over
            # a handshake-validated connection -- a reachable, valid peer.
            self._record_contact(reachable=True)
            self._last_error = None
            raise
        self._record_contact(reachable=True)
        self._last_error = None
        return result

    async def reset_session(self) -> None:
        """Close the HTTP session and reset connection state.

        Forces a new TLS handshake on the next request, re-running
        certificate chain validation. Resets connection state so that status
        reflects the outcome of the next connection attempt, not stale state.
        """
        await self.close()
        self._chain_validated = False
        self._server_alive = False
        self._last_error = None
        self._last_contact_epoch = None
        self._last_validated_epoch = None
        self._consecutive_failures = 0

    def update_ca_trust(self, ca_cert_path: str) -> None:
        """Rebuild SSL context with a new CA trust store.

        Args:
            ca_cert_path: Path to PEM CA bundle to trust.
        """
        if self._tls_config is None:
            return
        from dataclasses import replace
        from pathlib import Path

        new_config = replace(self._tls_config, ca_cert=Path(ca_cert_path))
        self._tls_config = new_config
        self._ssl = create_ssl_context(new_config)
        self._chain_validated = False

    def update_client_cert(self, cert_path: str, key_path: str) -> None:
        """Rebuild SSL context with a new client certificate and key.

        Used by COMM-004 PKI tests to swap client identity at runtime.
        Also recomputes `_client_lfdi` and invalidates the cached aiohttp
        session so the new cert's identity takes effect immediately --
        without that, the `LFDI:` request header (and forwarder message
        attribution) would keep the OLD cert's LFDI.

        Args:
            cert_path: Path to PEM client certificate.
            key_path: Path to PEM client private key.
        """
        if self._tls_config is None:
            return
        from dataclasses import replace
        from pathlib import Path

        new_config = replace(
            self._tls_config,
            client_cert=Path(cert_path),
            client_key=Path(key_path),
        )
        self._tls_config = new_config
        self._ssl = create_ssl_context(new_config)
        self._chain_validated = False
        # Recompute LFDI from the new cert and drop the cached session so
        # the next request rebuilds it with the new default headers.
        self._client_lfdi = self._compute_client_lfdi(new_config)
        old_session = self._session
        self._session = None
        if old_session is not None and not old_session.closed:
            self._close_session_best_effort(old_session)

    @staticmethod
    def _close_session_best_effort(session: aiohttp.ClientSession) -> None:
        """Close an orphaned aiohttp session without blocking the caller.

        ``update_client_cert`` is a sync method but ``ClientSession.close()``
        is async (aiohttp >= 3.9 -- the connector close is async too). If a
        loop is running, schedule the close as a background task; otherwise
        run the close coroutine to completion in a fresh event loop via
        ``asyncio.run``. Either path actually closes the connector and
        drains the pool, avoiding both the `Unclosed client session`
        warning and the un-awaited-coroutine warning.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop -- run the async close to completion so the
            # session and its connector are actually closed and the pool is
            # drained. Suppress so a transport edge-case can't escape.
            with contextlib.suppress(Exception):
                asyncio.run(session.close())
            return
        # Track the task in the module-level set so a strong reference
        # outlives this function -- otherwise GC could collect the pending
        # task mid-flight (asyncio holds only weak refs).
        task = loop.create_task(session.close())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)

    def _validate_chain(self, resp: aiohttp.ClientResponse) -> None:
        """Run IEEE 2030.5 chain validation on the peer's certificate chain.

        Only runs once per session (set _chain_validated = True on success).
        Accesses the SSL object from the underlying transport.
        """
        if self._chain_validated or not isinstance(self._ssl, ssl.SSLContext):
            return

        # Try resp.connection.transport (aiohttp < 3.13), then fall back to
        # resp._protocol.transport (aiohttp >= 3.13 where resp.connection
        # may return None after the connection is released to the pool).
        transport = None
        connection = resp.connection
        if connection is not None:
            transport = connection.transport
        if transport is None:
            protocol = getattr(resp, "_protocol", None)
            if protocol is not None:
                transport = getattr(protocol, "transport", None)
        if transport is None:
            logger.warning("Cannot access SSL transport for chain validation")
            return

        ssl_obj = transport.get_extra_info("ssl_object")
        if ssl_obj is None:
            return

        chain_fn = getattr(ssl_obj, "get_verified_chain", None)
        if chain_fn is None:
            # Python < 3.13: skip chain validation
            logger.debug("get_verified_chain not available (Python < 3.13), skipping")
            self._chain_validated = True
            return

        raw_chain = chain_fn()
        chain_der: list[bytes] = []
        for cert in raw_chain:
            if isinstance(cert, bytes):
                # Python 3.13+ may return DER bytes directly
                chain_der.append(cert)
            else:
                import _ssl

                chain_der.append(cert.public_bytes(_ssl.ENCODING_DER))
        try:
            verify_ieee2030_5_chain(chain_der)
        except CertChainError as exc:
            from py20305.diagnostics import report

            report(
                "errors",
                f"IEEE 2030.5 cert chain rejected for {self._server_host}: {exc}",
                source="tls",
                dedup_key=f"tls_chain:{self._server_host}",
                details={"host": self._server_host, "error": str(exc)},
            )
            raise
        self._chain_validated = True

    async def get_raw(self, url: str) -> dict[str, Any]:
        """GET a raw URL and return status, headers, and body text.

        Unlike ``get()``, this does not parse XML and accepts a full URL
        (the caller is responsible for providing it).
        """
        session = self._get_session()
        logger.debug("GET %s", url)
        try:
            async with session.get(url, ssl=self._ssl, allow_redirects=False) as resp:
                logger.debug("GET %s -> %s", url, resp.status)
                body = await resp.text()
                headers = dict(resp.headers.items())
                content_type = resp.headers.get("Content-Type", "")
                # The server responded over a handshake-validated connection, so
                # this on-demand proxy probe keeps /status fresh and consistent
                # with other requests (a reachable, valid peer).
                self._record_contact(reachable=True)
                self._last_error = None
                return {
                    "status_code": resp.status,
                    "content_type": content_type,
                    "headers": headers,
                    "body": body,
                }
        except (aiohttp.ClientError, OSError) as exc:
            self._record_contact(reachable=False)
            self._last_error = str(exc)
            return {"status_code": 0, "error": str(exc)}

    async def request_raw(
        self,
        method: str,
        url: str,
        body: str | None = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        """Send an arbitrary method to a URL and return status, headers, and body.

        Uses the client's authenticated session (client cert + the
        configured request headers, e.g. the server API token), so callers get
        the same TLS/auth context as the client's own traffic without
        re-plumbing credentials. Unlike the typed SEP methods this neither
        builds nor parses IEEE 2030.5 XML -- the caller supplies the body and
        content type and receives the raw response, and owns the full URL.

        ``body=None`` sends no body and sets no request-level Content-Type,
        which is what a GET or DELETE wants. Note the wire still carries the
        session's default ``Content-Type: application/sep+xml`` (see
        ``_default_headers``): aiohttp merges session defaults into every
        request, and omitting a key does not remove it. Harmless -- a
        Content-Type without a body is ignored -- and the same is already true
        of ``get_raw``. Removing it properly means dropping the session default
        and setting it on each typed write path instead, which touches every
        protocol write and is not worth doing here.

        Every method is recorded in Live Traffic: these are operator-triggered
        requests carrying the client's credentials, and a write that is
        invisible afterwards is the one worth seeing.
        """
        verb = method.upper()
        if verb not in _RAW_PROXY_METHODS:
            return {"status_code": 0, "error": f"Unsupported method: {method}"}
        # Reject header-injection attempts in the operator-supplied content type
        # before it reaches the request headers -- fail fast with a clear error
        # rather than a downstream aiohttp exception.
        if "\r" in content_type or "\n" in content_type:
            return {"status_code": 0, "error": "content_type must not contain CR/LF characters"}
        session = self._get_session()
        # Path for Live Traffic; this method takes a full URL, unlike the SEP
        # write paths that record a base-relative path. Keep the query string so
        # the recorded target isn't misleading.
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        data = body.encode("utf-8") if body is not None else None
        # Override the session default Accept (application/sep+xml): this is a
        # generic proxy for non-SEP endpoints (e.g. JSON registration), so don't
        # constrain content negotiation. Only keys set here override the session
        # defaults -- an omitted Content-Type falls through to the session's, it
        # is not removed (see the docstring).
        headers_out: dict[str, str] = {"Accept": "*/*"}
        if data is not None:
            headers_out["Content-Type"] = content_type
        logger.debug("%s %s", verb, url)
        # Record the outbound request body where there is one. Recorded straight
        # to the ring buffer since a generic proxy body isn't a SEP message to
        # fan out to the forwarders.
        if self._traffic_recorder is not None and data is not None:
            self._traffic_recorder.record_request(method=verb, url=path, body=data)
        try:
            async with session.request(
                verb,
                url,
                data=data,
                headers=headers_out,
                ssl=self._ssl,
                allow_redirects=False,
            ) as resp:
                logger.debug("%s %s -> %s", verb, url, resp.status)
                resp_body = await resp.text()
                resp_headers = dict(resp.headers.items())
                self._record_traffic_response(verb, path, status=resp.status, body=resp_body)
                self._record_contact(reachable=True)
                self._last_error = None
                return {
                    "status_code": resp.status,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "headers": resp_headers,
                    "body": resp_body,
                }
        except (aiohttp.ClientError, OSError) as exc:
            self._record_traffic_response(verb, path, status=None, error=str(exc))
            self._record_contact(reachable=False)
            self._last_error = str(exc)
            return {"status_code": 0, "error": str(exc)}

    async def post_raw(
        self, url: str, body: str, content_type: str = "application/json"
    ) -> dict[str, Any]:
        """POST a raw body to a URL and return status, headers, and body text.

        The POST-shaped form of :meth:`request_raw`, kept as its own name
        because the registration panel and its tests call it directly.
        """
        return await self.request_raw("POST", url, body, content_type)

    async def get(self, path: str, model_type: type[T]) -> T:
        """GET a single resource and deserialize to model_type."""

        async def _do_get() -> T:
            session = self._get_session()
            url = f"{self._base_url}{path}"
            logger.debug("GET %s", url)
            try:
                async with session.get(url, ssl=self._ssl, allow_redirects=False) as resp:
                    logger.debug("GET %s -> %s", url, resp.status)
                    self._check_redirect_or_rate_limit(resp, "GET", path)
                    if resp.status == 204:
                        # Success with no representation: a typed signal so callers
                        # treat an optional resource as absent/empty rather than
                        # crash (CSIP [GEN.037]); distinct from a real error. It's
                        # still a validated, successful contact, so validate the
                        # chain and record it (as the 200 path does) before
                        # signalling -- otherwise /status and the connectivity
                        # heartbeat wouldn't reflect recovery on a 204.
                        self._record_traffic_response("GET", path, status=204)
                        self._validate_chain(resp)
                        self._record_contact(reachable=True)
                        self._last_error = None
                        raise Sep2NoContentError(f"GET {path} returned 204 No Content")
                    if resp.status != 200:
                        text = await resp.text()
                        self._record_traffic_response("GET", path, status=resp.status, body=text)
                        raise Sep2ProtocolError(
                            f"GET {path} returned {resp.status}: {text}", resp.status
                        )

                    # IEEE 2030.5 chain validation (once per session)
                    self._validate_chain(resp)

                    body = await resp.read()
                    result = _parse_body(body, model_type, path)

                    # Forward successful response
                    self._forward_downstream(
                        result,
                        model_type.__name__,
                        "GET",
                        path,
                        resp.status,
                        raw_xml=body,
                    )
                    self._record_contact(reachable=True)
                    self._last_error = None
                    return result
            except Sep2PayloadError as exc:
                # Server replied 200 but body is unusable. Server is reachable
                # (this is a payload issue, not connectivity), so it counts as
                # contact; recorded as _last_error for the UI.
                self._record_contact(reachable=True)
                self._last_error = str(exc)
                raise
            except Sep2NoContentError:
                # A 204 is a successful, validated contact -- the 204 branch
                # above already validated the chain and recorded it. Propagate the
                # signal without the not-validated bookkeeping below (this clause
                # must precede the Sep2ProtocolError handler: it's a subclass).
                raise
            except (Sep2ProtocolError, Sep2RateLimitError, Sep2RedirectError):
                # Server answered (error status / 429 / 301) over a
                # handshake-validated connection -- a reachable, valid peer.
                self._record_contact(reachable=True)
                self._last_error = None
                raise
            except CertChainError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                await self.close()
                raise
            except ssl.SSLError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                raise
            except aiohttp.ClientError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                self._record_traffic_response("GET", path, error=str(exc))
                raise OSError(str(exc)) from exc
            except OSError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                self._record_traffic_response("GET", path, error=str(exc))
                raise

        return await self._retry_observed(_do_get)

    async def get_with_body(self, path: str, model_type: type[T]) -> tuple[T, bytes]:
        """GET a resource, returning both the parsed model and raw response body."""

        async def _do_get() -> tuple[T, bytes]:
            session = self._get_session()
            url = f"{self._base_url}{path}"
            logger.debug("GET %s", url)
            try:
                async with session.get(url, ssl=self._ssl, allow_redirects=False) as resp:
                    logger.debug("GET %s -> %s", url, resp.status)
                    self._check_redirect_or_rate_limit(resp, "GET", path)
                    if resp.status == 204:
                        # Success with no representation: a typed signal so callers
                        # treat an optional resource as absent/empty rather than
                        # crash (CSIP [GEN.037]); distinct from a real error. It's
                        # still a validated, successful contact, so validate the
                        # chain and record it (as the 200 path does) before
                        # signalling -- otherwise /status and the connectivity
                        # heartbeat wouldn't reflect recovery on a 204.
                        self._record_traffic_response("GET", path, status=204)
                        self._validate_chain(resp)
                        self._record_contact(reachable=True)
                        self._last_error = None
                        raise Sep2NoContentError(f"GET {path} returned 204 No Content")
                    if resp.status != 200:
                        text = await resp.text()
                        self._record_traffic_response("GET", path, status=resp.status, body=text)
                        raise Sep2ProtocolError(
                            f"GET {path} returned {resp.status}: {text}", resp.status
                        )
                    self._validate_chain(resp)
                    body = await resp.read()
                    result = _parse_body(body, model_type, path)
                    self._forward_downstream(
                        result,
                        model_type.__name__,
                        "GET",
                        path,
                        resp.status,
                        raw_xml=body,
                    )
                    self._record_contact(reachable=True)
                    self._last_error = None
                    return result, body
            except Sep2PayloadError as exc:
                self._record_contact(reachable=True)
                self._last_error = str(exc)
                raise
            except Sep2NoContentError:
                # A 204 is a successful, validated contact -- the 204 branch
                # above already validated the chain and recorded it. Propagate the
                # signal without the not-validated bookkeeping below (this clause
                # must precede the Sep2ProtocolError handler: it's a subclass).
                raise
            except (Sep2ProtocolError, Sep2RateLimitError, Sep2RedirectError):
                # Server answered (error status / 429 / 301) over a
                # handshake-validated connection -- a reachable, valid peer.
                self._record_contact(reachable=True)
                self._last_error = None
                raise
            except CertChainError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                await self.close()
                raise
            except ssl.SSLError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                raise
            except aiohttp.ClientError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                self._record_traffic_response("GET", path, error=str(exc))
                raise OSError(str(exc)) from exc
            except OSError as exc:
                self._record_contact(reachable=False)
                self._chain_validated = False
                self._last_error = str(exc)
                self._record_traffic_response("GET", path, error=str(exc))
                raise

        return await self._retry_observed(_do_get)

    async def get_list(self, path: str, model_type: type[T]) -> list[T]:
        """GET a list resource, fetching all pages.

        model_type should be the list container type (e.g. EndDeviceList).
        Returns all items across pages by following the `all` and `results` attributes.

        A 404 (Not Found) or 204 (No Content) on the first page is treated as
        "no resources exist" and returns an empty list immediately. This is
        expected for endpoints like DERControlList where the server returns 404
        (or 204) when no controls are configured yet — it is not a transient
        error. A non-first-page 404/204 is not normalized and surfaces as a
        protocol error (a pagination/data-integrity issue).
        """
        items: list[Any] = []
        start = 0
        limit = 50
        sep = "&" if "?" in path else "?"

        while True:
            page_path = f"{path}{sep}s={start}&l={limit}"
            try:
                page: Any = await self.get(page_path, model_type)
            except Sep2NoContentError as exc:
                # 204 No Content on the first page = empty list. On a later page
                # it's a pagination/data-integrity error (the server returned
                # items then "no content"); re-raise as a plain protocol error so
                # it surfaces rather than being treated as benign "no content".
                if start == 0:
                    logger.debug("GET %s returned 204 No Content, treating as empty list", path)
                    return []
                raise Sep2ProtocolError(
                    f"GET {path}: 204 No Content on page (start={start}) mid-pagination", 204
                ) from exc
            except Sep2ProtocolError as exc:
                # 404 (absent) on the first page also means "no items".
                if exc.status_code == 404 and start == 0:
                    logger.debug("GET %s returned 404, treating as empty list", path)
                    return []
                raise

            items.append(page)

            all_count = int(getattr(page, "all_", None) or getattr(page, "all", 0) or 0)
            results: int = getattr(page, "results", 0)
            start += results
            if start >= all_count or results == 0:
                break

        return items

    async def post(self, path: str, resource: object) -> str | None:
        """POST a resource. Returns the Location header if present."""

        async def _do_post() -> str | None:
            session = self._get_session()
            url = f"{self._base_url}{path}"
            body = to_xml(
                resource,
                include_csipaus=self._csip_aus_mode,
                server_2018_compat=self._server_2018_compat,
            )

            # Forward request (upstream)
            self._forward_upstream(
                resource,
                resource.__class__.__name__,
                "POST",
                path,
                raw_xml=body,
            )

            logger.debug("POST %s", url)
            try:
                async with session.post(url, data=body, ssl=self._ssl) as resp:
                    logger.debug("POST %s -> %s", url, resp.status)
                    await self._check_and_record_write("POST", path, resp, (200, 201, 204))
                    return str(resp.headers["Location"]) if "Location" in resp.headers else None
            except ssl.SSLError:
                raise
            except aiohttp.ClientError as exc:
                raise OSError(str(exc)) from exc

        return await self._send_tracked(_do_post)

    async def post_bytes(self, path: str, body: bytes) -> str | None:
        """POST pre-serialized XML bytes. Returns the Location header if present.

        Use this when custom XML serialization is needed (e.g., legacy compatibility).
        """

        async def _do_post() -> str | None:
            session = self._get_session()
            url = f"{self._base_url}{path}"

            # Forward request (upstream) - raw bytes
            self._forward_upstream(body, "RawXML", "POST", path, raw_xml=body)

            logger.debug("POST %s", url)
            try:
                async with session.post(url, data=body, ssl=self._ssl) as resp:
                    logger.debug("POST %s -> %s", url, resp.status)
                    await self._check_and_record_write("POST", path, resp, (200, 201, 204))
                    return str(resp.headers["Location"]) if "Location" in resp.headers else None
            except ssl.SSLError:
                raise
            except aiohttp.ClientError as exc:
                raise OSError(str(exc)) from exc

        return await self._send_tracked(_do_post)

    async def put_bytes(self, path: str, body: bytes) -> int:
        """PUT pre-serialized XML bytes. Returns the HTTP status code.

        Use this when custom XML serialization is needed (e.g., DERStatus).
        """

        async def _do_put() -> int:
            session = self._get_session()
            url = f"{self._base_url}{path}"

            # Forward request (upstream) - raw bytes
            self._forward_upstream(body, "RawXML", "PUT", path, raw_xml=body)

            logger.debug("PUT %s", url)
            try:
                async with session.put(url, data=body, ssl=self._ssl) as resp:
                    logger.debug("PUT %s -> %s", url, resp.status)
                    await self._check_and_record_write("PUT", path, resp, (200, 204))
                    return int(resp.status)
            except ssl.SSLError:
                raise
            except aiohttp.ClientError as exc:
                raise OSError(str(exc)) from exc

        return await self._send_tracked(_do_put)

    async def put(self, path: str, resource: object) -> int:
        """PUT a resource. Returns the HTTP status code."""

        async def _do_put() -> int:
            session = self._get_session()
            url = f"{self._base_url}{path}"
            body = to_xml(
                resource,
                include_csipaus=self._csip_aus_mode,
                server_2018_compat=self._server_2018_compat,
            )

            # Forward request (upstream)
            self._forward_upstream(resource, resource.__class__.__name__, "PUT", path, raw_xml=body)

            logger.debug("PUT %s", url)
            try:
                async with session.put(url, data=body, ssl=self._ssl) as resp:
                    logger.debug("PUT %s -> %s", url, resp.status)
                    await self._check_and_record_write("PUT", path, resp, (200, 204))
                    return int(resp.status)
            except ssl.SSLError:
                raise
            except aiohttp.ClientError as exc:
                raise OSError(str(exc)) from exc

        return await self._send_tracked(_do_put)

    async def delete(self, path: str) -> int:
        """DELETE a resource. Returns the HTTP status code."""

        async def _do_delete() -> int:
            session = self._get_session()
            url = f"{self._base_url}{path}"
            logger.debug("DELETE %s", url)
            try:
                async with session.delete(url, ssl=self._ssl) as resp:
                    logger.debug("DELETE %s -> %s", url, resp.status)
                    await self._check_and_record_write("DELETE", path, resp, (200, 204))
                    return int(resp.status)
            except ssl.SSLError:
                raise
            except aiohttp.ClientError as exc:
                raise OSError(str(exc)) from exc

        return await self._send_tracked(_do_delete)

    @staticmethod
    def _check_redirect_or_rate_limit(resp: aiohttp.ClientResponse, method: str, path: str) -> None:
        """Check for redirect (301/302/307/308) or 429 responses and raise.

        IEEE 5.5.2.7: On 301 Moved Permanently, clients SHOULD re-discover. We
        surface the other HTTP redirects (302/307/308) the same way so any
        redirect triggers re-discovery rather than a generic protocol error.
        IEEE 5.5.2.17: On 429 Too Many Requests, clients SHOULD back off.
        """
        if resp.status in (301, 302, 307, 308):
            location = resp.headers.get("Location", "")
            raise Sep2RedirectError(
                f"{method} {path} returned {resp.status} (redirect) -> {location}",
                location=location,
                status_code=resp.status,
            )
        if resp.status == 429:
            retry_after_raw = resp.headers.get("Retry-After")
            retry_after: int | None = None
            if retry_after_raw is not None:
                with contextlib.suppress(ValueError):
                    retry_after = int(retry_after_raw)
            raise Sep2RateLimitError(
                f"{method} {path} returned 429 Too Many Requests",
                retry_after=retry_after,
            )

    def _forward_upstream(
        self,
        content: Any,
        message_type: str,
        http_method: str,
        uri: str,
        raw_xml: bytes | None = None,
    ) -> None:
        """Forward an upstream (request) message."""
        from py20305.forwarders import MessageDirection

        if self._traffic_recorder is not None:
            self._traffic_recorder.record_request(
                method=http_method, url=uri, body=raw_xml if raw_xml is not None else content
            )
        self._forward_message(
            direction=MessageDirection.UPSTREAM,
            message_type=message_type,
            content=content,
            http_method=http_method,
            uri=uri,
            raw_xml=raw_xml,
        )

    def _forward_downstream(
        self,
        content: Any,
        message_type: str,
        http_method: str,
        uri: str,
        status_code: int,
        raw_xml: bytes | None = None,
    ) -> None:
        """Forward a downstream (response) message."""
        from py20305.forwarders import MessageDirection

        if self._traffic_recorder is not None:
            self._traffic_recorder.record_response(
                method=http_method,
                url=uri,
                status=status_code,
                body=raw_xml if raw_xml is not None else content,
            )
        self._forward_message(
            direction=MessageDirection.DOWNSTREAM,
            message_type=message_type,
            content=content,
            http_method=http_method,
            uri=uri,
            status_code=status_code,
            raw_xml=raw_xml,
        )

    def _record_traffic_response(
        self,
        method: str,
        path: str,
        *,
        status: int | None = None,
        body: object = None,
        error: str | None = None,
    ) -> None:
        """Record a response in Live Traffic.

        GET successes are captured via ``_forward_downstream``; this surfaces the
        rest: error responses (non-2xx, with the server's error body), GET
        transport failures (no response -> ``status=None`` + ``error``), and write
        (POST/PUT/DELETE) result statuses. Write *transport* failures (no
        response) are not recorded here -- they surface via connectivity health.
        """
        if self._traffic_recorder is not None:
            self._traffic_recorder.record_response(
                method=method, url=path, status=status, body=body, error=error
            )

    async def _check_and_record_write(
        self,
        method: str,
        path: str,
        resp: aiohttp.ClientResponse,
        ok_statuses: tuple[int, ...],
    ) -> None:
        """Validate a write (POST/PUT/DELETE) response, record it, and drain it.

        On a non-ok status, records the server's error body and raises
        ``Sep2ProtocolError``; on success, records the result status. Reads the
        body either way so the connection is drained. The matching request body
        for POST/PUT is captured separately via ``_forward_upstream``.
        """
        if resp.status not in ok_statuses:
            # Read raw bytes (not resp.text()) so a non-text/bad-charset error
            # body can't raise, and the recorder gets the byte-safe truncation
            # path; decode with errors="replace" only for the exception message.
            raw = await resp.read()
            self._record_traffic_response(method, path, status=resp.status, body=raw)
            text = raw.decode("utf-8", errors="replace")
            raise Sep2ProtocolError(f"{method} {path} returned {resp.status}: {text}", resp.status)
        await resp.read()
        self._record_traffic_response(method, path, status=resp.status)

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        # Flush the observer first: successes accumulated since its last
        # window closed are still attempts the connection log accounts for.
        if self._connection_observer is not None:
            self._connection_observer.flush()
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> Sep2Client:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        await self.close()
