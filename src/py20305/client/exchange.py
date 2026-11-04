"""Build the observer records of the client's HTTP exchanges.

:class:`Sep2Client` opens one :class:`ExchangeScope` per attempt of each
request. The scope collects what the attempt sent and received and, when it
closes, hands the observers an :class:`~py20305.observers.ExchangeRecord`, a
:class:`~py20305.observers.ConnectionEvent` for a failed or lost connection,
and a :class:`~py20305.observers.ProtocolAnomaly` for a body the client could
not use or a status it did not expect. With no observer attached, the client
opens no scope.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import time
import uuid
from collections.abc import Iterable, Mapping
from types import TracebackType
from typing import Any

import aiohttp
from lxml import etree

from py20305.client.connector import Address, SocketPair, socket_pair_of
from py20305.client.errors import (
    Sep2NoContentError,
    Sep2PayloadError,
    Sep2ProtocolError,
    Sep2RateLimitError,
    Sep2RedirectError,
)
from py20305.client.tls import CertChainError
from py20305.observers import (
    AnomalyCode,
    ConnectionDirection,
    ConnectionEvent,
    ConnectionOutcome,
    ConnectionRole,
    ConnectionTransport,
    Endpoint,
    ErrorKind,
    ExchangeDirection,
    ExchangeRecord,
    ObserverGuard,
    Observers,
    ProtocolAnomaly,
    redact_headers,
)

logger = logging.getLogger(__name__)

_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)

#: Longest error text an exchange record or connection event carries.
MAX_ERROR_CHARS = 512


def endpoint(address: Address | None) -> Endpoint | None:
    """Return a connection end as an observer :class:`~py20305.observers.Endpoint`."""
    return Endpoint(address.ip, address.port) if address is not None else None


def classify_error(exc: BaseException) -> ErrorKind:
    """Return the kind of a failure that left an exchange without a response."""
    if isinstance(exc, (CertChainError, ssl.SSLError, aiohttp.ClientSSLError)):
        return ErrorKind.TLS
    if isinstance(exc, aiohttp.InvalidURL):
        return ErrorKind.INVALID_URL
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, aiohttp.ServerTimeoutError)):
        return ErrorKind.TIMEOUT
    if isinstance(exc, (aiohttp.ClientConnectorError, ConnectionRefusedError)):
        return ErrorKind.CONNECT
    if isinstance(
        exc,
        (
            aiohttp.ServerDisconnectedError,
            aiohttp.ClientPayloadError,
            aiohttp.ClientOSError,
            ConnectionResetError,
            ConnectionAbortedError,
        ),
    ):
        return ErrorKind.DISCONNECTED
    cause = exc.__cause__
    if cause is not None and cause is not exc:
        return classify_error(cause)
    return ErrorKind.OTHER


def _connection_outcome(exc: BaseException, kind: ErrorKind) -> ConnectionOutcome | None:
    """Return the connection outcome a failure reports, or None for none.

    A connection attempt that failed is ``FAILED``. A connection that closed
    under an exchange is ``LOST``. A read timeout says nothing about the
    connection, and is reported in the exchange record only.
    """
    if kind in (ErrorKind.CONNECT, ErrorKind.TLS):
        return ConnectionOutcome.FAILED
    if kind is ErrorKind.TIMEOUT and isinstance(exc, aiohttp.ConnectionTimeoutError):
        return ConnectionOutcome.FAILED
    if kind is ErrorKind.DISCONNECTED:
        return ConnectionOutcome.LOST
    return None


def bounded(text: str) -> str:
    """Return ``text`` cut to :data:`MAX_ERROR_CHARS`."""
    return text if len(text) <= MAX_ERROR_CHARS else text[:MAX_ERROR_CHARS]


def root_element(body: bytes | None) -> str | None:
    """Return the local name of the root element of an XML body, or None."""
    if not body:
        return None
    try:
        return etree.QName(etree.fromstring(body, parser=_SAFE_PARSER)).localname
    except (etree.XMLSyntaxError, ValueError):
        return None


def _is_xml(headers: Iterable[tuple[str, str]], body: bytes | None) -> bool:
    """Return whether a body is XML by its Content-Type, or by its first byte when untyped."""
    if not body:
        return False
    for name, value in headers:
        if name.lower() == "content-type":
            return "xml" in value.lower()
    return body.lstrip()[:1] == b"<"


def validate(
    body: bytes | None, headers: Iterable[tuple[str, str]], observers: Observers
) -> tuple[bool | None, str | None]:
    """Validate an XML body when the observers ask for it; ``(None, None)`` when not checked."""
    if not observers.validate or not _is_xml(headers, body):
        return None, None
    assert body is not None
    from py20305.xml.serialization import validate_xml_result

    try:
        valid, error = validate_xml_result(body, schema_dir=observers.schema_dir)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.debug("Schema validation could not run", exc_info=True)
        return None, None
    return valid, bounded(error) if error is not None else None


def response_transport(resp: aiohttp.ClientResponse) -> object | None:
    """Return the transport a response arrived on, while its connection is open."""
    connection = resp.connection
    if connection is not None and connection.transport is not None:
        return connection.transport
    protocol = getattr(resp, "_protocol", None)
    return getattr(protocol, "transport", None) if protocol is not None else None


class ExchangeScope:
    """Collect one attempt of one request, and report it when the scope closes.

    Use as a context manager around the attempt. Call :meth:`response` as soon
    as the response arrives, while its connection is still open, and
    :meth:`response_body` once its body is read. An exception that leaves the
    scope before a response arrived is the attempt's failure.
    """

    def __init__(
        self,
        *,
        observers: Observers,
        guard: ObserverGuard,
        method: str,
        url: str,
        path: str,
        request_headers: Mapping[str, str],
        request_body: bytes | None,
        attempt: int,
        server_host: str,
        server_port: int | None,
        tls: bool,
        client_lfdi: str | None,
        client_sfdi: int | None,
        device_lfdi: str | None,
        end_device_href: str | None,
        resource_hint: str | None = None,
        expected_statuses: Iterable[int] = (),
    ) -> None:
        self._observers = observers
        self._guard = guard
        self._method = method
        self._url = url
        self._path = path
        self._request_headers: tuple[tuple[str, str], ...] = tuple(request_headers.items())
        self._request_body = request_body
        self._attempt = attempt
        self._server_host = server_host
        self._server_port = server_port
        self._tls = tls
        self._client_lfdi = client_lfdi
        self._client_sfdi = client_sfdi
        self._device_lfdi = device_lfdi
        self._end_device_href = end_device_href
        self._resource_hint = resource_hint
        self._expected = frozenset(expected_statuses)
        self.exchange_id = uuid.uuid4().hex
        self._sent_at = time.time()
        self._sent_monotonic = time.monotonic()
        self._status: int | None = None
        self._response_headers: tuple[tuple[str, str], ...] = ()
        self._response_body: bytes | None = None
        self._received_at: float | None = None
        self._received_monotonic: float | None = None
        self._pair = SocketPair(local=None, remote=None)

    def __enter__(self) -> ExchangeScope:
        return self

    def response(self, resp: aiohttp.ClientResponse) -> None:
        """Note the response's status, headers, connection and arrival time."""
        self._received_at = time.time()
        self._received_monotonic = time.monotonic()
        self._status = int(resp.status)
        self._response_headers = tuple(resp.headers.items())
        sent = getattr(resp.request_info, "headers", None)
        if sent is not None:
            self._request_headers = tuple(sent.items())
        transport = response_transport(resp)
        if transport is not None:
            self._pair = socket_pair_of(transport)

    def response_body(self, body: bytes) -> None:
        """Note the response body exactly as received."""
        self._response_body = body

    def failed(self, exc: BaseException) -> None:
        """Report the attempt as failed with ``exc``, unless a response arrived."""
        self._report(exc)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self._report(exc)

    _reported = False
    _validation: tuple[tuple[bool | None, str | None], tuple[bool | None, str | None]] | None = None

    def _validated(
        self,
    ) -> tuple[tuple[bool | None, str | None], tuple[bool | None, str | None]]:
        """Return the request and response validation results, computed once."""
        if self._validation is None:
            request = (
                validate(self._request_body, self._request_headers, self._observers)
                if self._request_body
                else (None, None)
            )
            response = validate(self._response_body, self._response_headers, self._observers)
            self._validation = (request, response)
        return self._validation

    def _report(self, exc: BaseException | None) -> None:
        if self._reported:
            return
        self._reported = True
        try:
            self._deliver(exc)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Could not build the records of an exchange", exc_info=True)

    def _deliver(self, exc: BaseException | None) -> None:
        observers = self._observers
        failure = exc if self._status is None else None
        kind = classify_error(failure) if failure is not None else None
        if observers.exchange is not None:
            self._guard.deliver(observers.exchange, self._record(failure, kind))
        if observers.connection is not None and failure is not None and kind is not None:
            outcome = _connection_outcome(failure, kind)
            if outcome is not None:
                self._guard.deliver(
                    observers.connection, self._connection_event(outcome, failure, kind)
                )
        if observers.anomaly is not None:
            for anomaly in self._anomalies(exc):
                self._guard.deliver(observers.anomaly, anomaly)

    def _record(self, failure: BaseException | None, kind: ErrorKind | None) -> ExchangeRecord:
        observers = self._observers
        (request_valid, request_error), (response_valid, response_error) = self._validated()
        end = self._received_monotonic if self._received_monotonic is not None else time.monotonic()
        capture = observers.capture_bodies
        return ExchangeRecord(
            exchange_id=self.exchange_id,
            direction=ExchangeDirection.CLIENT_REQUEST,
            method=self._method,
            url=self._url,
            path=self._path,
            request_headers=redact_headers(self._request_headers, observers.redacted_headers),
            request_body=self._request_body if capture else None,
            status=self._status,
            response_headers=redact_headers(self._response_headers, observers.redacted_headers),
            response_body=self._response_body if capture else None,
            sent_at=self._sent_at,
            received_at=self._received_at,
            sent_monotonic=self._sent_monotonic,
            received_monotonic=self._received_monotonic,
            duration=end - self._sent_monotonic,
            attempt=self._attempt,
            error_kind=kind,
            error=bounded(str(failure) or type(failure).__name__) if failure is not None else None,
            local=endpoint(self._pair.local),
            remote=endpoint(self._pair.remote),
            server_host=self._server_host,
            tls_version=self._pair.tls_version,
            tls_cipher=self._pair.tls_cipher,
            server_cert_sha256=self._pair.peer_cert_sha256,
            client_lfdi=self._client_lfdi,
            client_sfdi=self._client_sfdi,
            device_lfdi=self._device_lfdi,
            end_device_href=self._end_device_href,
            resource_type=self._resource_type(),
            request_valid=request_valid,
            request_validation_error=request_error,
            response_valid=response_valid,
            response_validation_error=response_error,
        )

    def _resource_type(self) -> str | None:
        if self._method == "GET":
            return root_element(self._response_body) or self._resource_hint
        return root_element(self._request_body) or self._resource_hint

    def _connection_event(
        self, outcome: ConnectionOutcome, failure: BaseException, kind: ErrorKind
    ) -> ConnectionEvent:
        remote = endpoint(self._pair.remote)
        if remote is None and self._server_port is not None:
            remote = Endpoint(self._server_host, self._server_port)
        return ConnectionEvent(
            role=ConnectionRole.CLIENT,
            direction=ConnectionDirection.OUTBOUND,
            outcome=outcome,
            transport=ConnectionTransport.TLS if self._tls else ConnectionTransport.TCP,
            remote=remote,
            local=endpoint(self._pair.local),
            server_host=self._server_host,
            reason=bounded(str(failure) or type(failure).__name__),
            error_kind=kind,
        )

    def _anomalies(self, exc: BaseException | None) -> list[ProtocolAnomaly]:
        common: dict[str, Any] = {
            "exchange_id": self.exchange_id,
            "method": self._method,
            "path": self._path,
            "status": self._status,
            "peer": endpoint(self._pair.remote),
        }
        found: list[ProtocolAnomaly] = []
        if isinstance(exc, Sep2PayloadError):
            found.append(
                ProtocolAnomaly.of(AnomalyCode.UNPARSEABLE_BODY, bounded(str(exc)), **common)
            )
        elif self._status is not None and self._unexpected(exc):
            found.append(
                ProtocolAnomaly.of(
                    AnomalyCode.UNEXPECTED_STATUS,
                    f"{self._method} {self._path} returned {self._status}",
                    **common,
                )
            )
        if self._status is not None and 200 <= self._status < 300:
            valid, error = self._validated()[1]
            if valid is False:
                found.append(
                    ProtocolAnomaly.of(
                        AnomalyCode.INVALID_BODY, error or "schema validation failed", **common
                    )
                )
        return found

    def _unexpected(self, exc: BaseException | None) -> bool:
        """Return whether the response status is one the request did not expect.

        A redirect and a 429 are protocol signals the client acts on, and a GET
        answered 404 says the resource is absent, which a list read treats as
        empty. None of them is an anomaly.
        """
        if isinstance(exc, (Sep2RedirectError, Sep2RateLimitError, Sep2NoContentError)):
            return False
        if self._method == "GET" and self._status == 404:
            return False
        if self._expected:
            return self._status not in self._expected
        return isinstance(exc, Sep2ProtocolError)


class NullScope:
    """The scope of an attempt when no observer is attached. Every call does nothing."""

    exchange_id = ""

    def __enter__(self) -> NullScope:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def response(self, resp: aiohttp.ClientResponse) -> None:
        """Do nothing."""

    def response_body(self, body: bytes) -> None:
        """Do nothing."""

    def failed(self, exc: BaseException) -> None:
        """Do nothing."""
