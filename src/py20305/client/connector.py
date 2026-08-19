"""aiohttp connector that enforces the IEEE 2030.5 PKI-profile chain audit at
TLS-connection establishment.

The stdlib ``ssl`` context (``create_ssl_context``) already runs mandatory
``CERT_REQUIRED`` verification on every connection -- trusted chain, validity,
basic/name constraints -- for every request method. What OpenSSL does *not*
enforce is the IEEE 2030.5 PKI-profile structure audit (EKU-criticality on CA
certs, NameConstraints criticality, no PolicyConstraints); that lives in
:func:`verify_ieee2030_5_chain`.

Running that audit here, right after the TLS handshake, gates *every* request on
the connection (GET/PUT/POST/DELETE) uniformly -- rather than deferring it to the
first GET, which left writes on a freshly established connection un-audited until
a GET happened to run.
"""

from __future__ import annotations

import _ssl
import logging
import ssl
from collections.abc import Callable
from dataclasses import dataclass

import aiohttp

from py20305.client.tls import CertChainError, verify_ieee2030_5_chain

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Address:
    """One end of a TCP connection."""

    ip: str
    port: int


@dataclass(frozen=True)
class SocketPair:
    """The two ends of an established TCP connection.

    Either side may be ``None`` when the transport does not expose it — an
    unknown address is reported as unknown rather than invented.
    """

    local: Address | None
    remote: Address | None


def _as_address(info: object) -> Address | None:
    """Convert a ``get_extra_info`` address tuple to an :class:`Address`.

    Handles IPv4 ``(host, port)`` and IPv6 ``(host, port, flowinfo, scopeid)``
    alike, and returns ``None`` for anything else — a Unix socket has no
    address of this shape, and a malformed value is not worth guessing at.
    """
    if not isinstance(info, tuple) or len(info) < 2:
        return None
    host, port = info[0], info[1]
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    return Address(ip=host, port=port)


def _verified_chain_der(ssl_obj: ssl.SSLObject) -> list[bytes] | None:
    """Return the peer's verified chain as leaf-first DER, or ``None`` to skip.

    ``get_verified_chain`` is Python 3.13+. On older interpreters there is nothing
    to audit here -- the connection has already passed the context's
    ``CERT_REQUIRED`` verification -- so we skip, matching the degrade-gracefully
    behaviour of the per-GET validation path (``Sep2Client._validate_chain``).
    """
    get_chain = getattr(ssl_obj, "get_verified_chain", None)
    if get_chain is None:
        return None
    chain: list[bytes] = []
    for cert in get_chain():
        # Python 3.13 may hand back DER bytes directly, or Certificate objects.
        chain.append(cert if isinstance(cert, bytes) else cert.public_bytes(_ssl.ENCODING_DER))
    return chain


class Ieee2030TCPConnector(aiohttp.TCPConnector):
    """TCPConnector that audits the negotiated peer chain against the IEEE 2030.5
    PKI profile at handshake time, aborting the connection if it violates the
    profile so no request of any method is ever sent over it.

    Also the one place the connection's own socket is observable. ``on_connect``
    receives the local and remote address of each TCP connection as it is
    established; see :meth:`_report_socket`.
    """

    def __init__(
        self,
        *args: object,
        on_connect: Callable[[SocketPair], None] | None = None,
        **kwargs: object,
    ) -> None:
        """Accept an optional observer for established sockets.

        Args:
            on_connect: Called with the local and remote address of each TCP
                connection once it is established and audited. Never called
                for a connection the audit rejects — nothing was usable.
        """
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._on_connect = on_connect
        self._observer_failed = False

    async def _wrap_create_connection(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        # ``super()`` performs the actual TLS handshake (and wraps handshake-time
        # cert/SSL errors itself). Once it returns, ssl_object is populated and the
        # verified chain is available for the IEEE profile audit.
        transport, protocol = await super()._wrap_create_connection(*args, **kwargs)
        try:
            self._audit_peer_chain(transport)
        except CertChainError as exc:
            logger.warning(
                "IEEE 2030.5 chain rejected at TLS handshake; aborting connection: %s", exc
            )
            transport.close()
            raise
        except Exception:
            # An unexpected audit failure must never leave a live, un-audited
            # connection in the pool.
            transport.close()
            raise
        self._report_socket(transport)
        return transport, protocol

    def _report_socket(self, transport: object) -> None:
        """Hand the established connection's addresses to the observer.

        The local port is only knowable here. A ``ClientResponse`` has already
        released its connection by the time a caller can inspect it, so
        ``response.connection`` is ``None`` and the local socket is
        unrecoverable from the response side. The port is a property of the
        connection rather than of a request in any case — a pooled connection
        carries many requests on one local port — so reporting it as the
        connection is established is both the only place it is available and
        the honest granularity for it.

        Never raises: connection telemetry must not be able to break the
        connection it is describing.
        """
        if self._on_connect is None:
            return
        try:
            sockname = transport.get_extra_info("sockname")  # type: ignore[attr-defined]
            peername = transport.get_extra_info("peername")  # type: ignore[attr-defined]
            self._on_connect(SocketPair(local=_as_address(sockname), remote=_as_address(peername)))
        except Exception:
            # Warn once: an observer that never fires is indistinguishable from
            # a client that never connects, and both look like silence.
            if not self._observer_failed:
                self._observer_failed = True
                logger.warning(
                    "Connection observer raised; ignoring further failures", exc_info=True
                )
            else:
                logger.debug("Connection observer raised; ignoring", exc_info=True)

    @staticmethod
    def _audit_peer_chain(transport: object) -> None:
        """Run the IEEE 2030.5 profile audit on ``transport``'s peer chain.

        A no-op when the transport isn't TLS or the chain can't be reached
        (Python < 3.13) -- basic ``CERT_REQUIRED`` verification already ran. Raises
        :class:`CertChainError` when the chain violates the IEEE 2030.5 profile.
        """
        ssl_obj = transport.get_extra_info("ssl_object")  # type: ignore[attr-defined]
        if ssl_obj is None:
            return
        chain = _verified_chain_der(ssl_obj)
        if chain is not None:
            verify_ieee2030_5_chain(chain)
