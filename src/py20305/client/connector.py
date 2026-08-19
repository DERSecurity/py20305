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

import aiohttp

from py20305.client.tls import CertChainError, verify_ieee2030_5_chain

logger = logging.getLogger(__name__)


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
    """

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
        return transport, protocol

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
