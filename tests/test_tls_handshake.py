"""End-to-end TLS handshake tests for `additional_ciphers`.

Spins up a local aiohttp server with an RSA-2048 server cert (mirroring the
shape of utility test endpoints fronted by enterprise PKI that present an
RSA server cert rather than the IEEE 2030.5-mandated ECDSA one) and verifies
that py20305's client SSL context:

  - Cannot complete the handshake against an RSA peer with the IEEE 2030.5
    baseline cipher list alone (ECDSA-only).
  - Completes the handshake when the operator opts in via
    `additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",)`.

This exercises the real OpenSSL cipher negotiation, not a mocked
`set_ciphers()` -- catching anything our unit tests would miss.
"""

from __future__ import annotations

import datetime
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp
import pytest
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

from py20305.client.tls import TlsConfig, create_ssl_context


@dataclass
class TlsFixture:
    host: str
    port: int
    server_ca_path: Path  # what the client trusts (= the server's self-CA)
    client_cert_path: Path
    client_key_path: Path


def _self_signed_rsa_server(host: str, validity_days: int = 1) -> tuple[bytes, bytes]:
    """Self-signed RSA-2048 cert with SAN=DNS:host,IP:host. Acts as both CA + server cert."""
    now = datetime.datetime.now(datetime.UTC)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    san = x509.SubjectAlternativeName(
        [
            x509.DNSName(host),
            x509.IPAddress(__import__("ipaddress").ip_address(host)),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=validity_days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )


def _ecdsa_client_cert_signed_by(
    issuer_cert_pem: bytes,
    issuer_key_pem: bytes,
) -> tuple[bytes, bytes]:
    """ECDSA P-256 client cert signed by the supplied CA."""
    now = datetime.datetime.now(datetime.UTC)
    issuer_cert = x509.load_pem_x509_certificate(issuer_cert_pem)
    issuer_key = serialization.load_pem_private_key(issuer_key_pem, password=None)
    client_key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-client")]))
        .issuer_name(issuer_cert.subject)
        .public_key(client_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        client_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )


@pytest.fixture(scope="module")
def cert_dir() -> AsyncIterator[Path]:
    with TemporaryDirectory(prefix="csip-tls-handshake-") as d:
        yield Path(d)


@pytest.fixture(scope="module")
def cert_paths(cert_dir: Path) -> dict[str, Path]:
    """Generate the cert bundle once per module (key generation is the slow part)."""
    host = "127.0.0.1"
    server_cert_pem, server_key_pem = _self_signed_rsa_server(host)
    client_cert_pem, client_key_pem = _ecdsa_client_cert_signed_by(server_cert_pem, server_key_pem)
    paths = {
        "server_cert": cert_dir / "server.pem",
        "server_key": cert_dir / "server.key",
        "client_cert": cert_dir / "client.pem",
        "client_key": cert_dir / "client.key",
    }
    paths["server_cert"].write_bytes(server_cert_pem)
    paths["server_key"].write_bytes(server_key_pem)
    paths["client_cert"].write_bytes(client_cert_pem)
    paths["client_key"].write_bytes(client_key_pem)
    return paths


@pytest.fixture
async def rsa_tls_server(cert_paths: dict[str, Path]) -> AsyncIterator[TlsFixture]:
    """Run aiohttp on 127.0.0.1:<auto-port> with an RSA server cert + mTLS."""
    host = "127.0.0.1"

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    server_ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    # Server-side: only offer RSA suites (mirrors a non-2030.5-compliant peer).
    server_ctx.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384:@SECLEVEL=0")
    server_ctx.load_cert_chain(
        str(cert_paths["server_cert"]),
        str(cert_paths["server_key"]),
    )
    server_ctx.verify_mode = ssl.CERT_REQUIRED
    server_ctx.load_verify_locations(str(cert_paths["server_cert"]))  # client CA = server self-CA

    async def hello(_: web.Request) -> web.Response:
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/", hello)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, 0, ssl_context=server_ctx)
    await site.start()
    # Pull the OS-assigned port off the server's first socket
    sockets = site._server.sockets  # type: ignore[union-attr]
    assert sockets, "server did not bind a socket"
    port = sockets[0].getsockname()[1]

    try:
        yield TlsFixture(
            host=host,
            port=port,
            server_ca_path=cert_paths["server_cert"],
            client_cert_path=cert_paths["client_cert"],
            client_key_path=cert_paths["client_key"],
        )
    finally:
        await runner.cleanup()


def _client_tls_config(fixture: TlsFixture, additional_ciphers: tuple[str, ...] = ()) -> TlsConfig:
    return TlsConfig(
        client_cert=fixture.client_cert_path,
        client_key=fixture.client_key_path,
        ca_cert=fixture.server_ca_path,
        check_hostname=False,  # SAN matches but the test isn't about hostname checking
        additional_ciphers=additional_ciphers,
    )


class TestRsaPeerHandshake:
    async def test_baseline_cannot_handshake_with_rsa_peer(
        self, rsa_tls_server: TlsFixture
    ) -> None:
        """No cipher overlap: ECDSA-only client vs RSA-only server."""
        ctx = create_ssl_context(_client_tls_config(rsa_tls_server))
        url = f"https://{rsa_tls_server.host}:{rsa_tls_server.port}/"
        with pytest.raises(
            (aiohttp.ClientConnectorError, aiohttp.ClientConnectorSSLError, ssl.SSLError)
        ):
            async with aiohttp.ClientSession() as session:
                async with session.get(url, ssl=ctx) as resp:
                    await resp.read()

    async def test_additional_ciphers_unblocks_rsa_peer(self, rsa_tls_server: TlsFixture) -> None:
        """Opting into ECDHE-RSA-AES256-GCM-SHA384 lets the handshake complete."""
        ctx = create_ssl_context(
            _client_tls_config(
                rsa_tls_server,
                additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",),
            )
        )
        url = f"https://{rsa_tls_server.host}:{rsa_tls_server.port}/"
        async with aiohttp.ClientSession() as session, session.get(url, ssl=ctx) as resp:
            assert resp.status == 200
            assert await resp.text() == "ok"


# ---------------------------------------------------------------------------
# Inbound: NotificationServer accepting handshakes from an RSA-only peer.
# Mirrors the outbound test above. The aggregator's notification listener
# uses the same TlsConfig as the outbound client, so the cipher escape hatch
# has to apply symmetrically -- otherwise a utility that connects out to us
# fine still can't push notifications back through our server.
# ---------------------------------------------------------------------------


@dataclass
class NotifFixture:
    host: str
    port: int
    server_cert_path: Path  # acts as both server cert and client-trust CA
    server_key_path: Path


@pytest.fixture(scope="module")
def rsa_notif_cert_paths(cert_dir: Path) -> dict[str, Path]:
    """RSA self-signed cert reused as the notification server's identity."""
    host = "127.0.0.1"
    cert_pem, key_pem = _self_signed_rsa_server(host)
    paths = {
        "cert": cert_dir / "notif-rsa.pem",
        "key": cert_dir / "notif-rsa.key",
    }
    paths["cert"].write_bytes(cert_pem)
    paths["key"].write_bytes(key_pem)
    return paths


def _notif_tls_config(
    paths: dict[str, Path],
    additional_ciphers: tuple[str, ...] = (),
) -> TlsConfig:
    return TlsConfig(
        client_cert=paths["cert"],
        client_key=paths["key"],
        ca_cert=paths["cert"],  # self-signed, so the cert is its own CA
        check_hostname=False,
        additional_ciphers=additional_ciphers,
    )


@pytest.fixture
async def notif_server_factory(
    rsa_notif_cert_paths: dict[str, Path],
):
    """Yield a factory that boots a NotificationServer with given additional_ciphers."""
    from py20305.subscription.notification_server import NotificationServer

    started: list[NotificationServer] = []

    async def _factory(additional_ciphers: tuple[str, ...] = ()) -> NotifFixture:
        tls = _notif_tls_config(rsa_notif_cert_paths, additional_ciphers=additional_ciphers)
        # client_cert_mode='off' keeps the test focused on cipher negotiation:
        # we don't want a missing client cert to mask a cipher-mismatch error.
        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=tls,
            client_cert_mode="off",
        )
        await server.start()
        started.append(server)
        # Recover the OS-assigned port from the underlying TCPSite's server.
        site = server._site
        assert site is not None
        sockets = site._server.sockets  # type: ignore[union-attr]
        assert sockets, "notification server did not bind a socket"
        port = sockets[0].getsockname()[1]
        return NotifFixture(
            host="127.0.0.1",
            port=port,
            server_cert_path=rsa_notif_cert_paths["cert"],
            server_key_path=rsa_notif_cert_paths["key"],
        )

    try:
        yield _factory
    finally:
        for srv in started:
            await srv.stop()


def _rsa_only_client_ctx(ca_path: Path) -> ssl.SSLContext:
    """TLS client context that offers only ECDHE-RSA-AES256-GCM-SHA384.

    Models a utility IEEE 2030.5 server pushing notifications back into the
    aggregator from an enterprise-PKI deployment whose stack is RSA-only.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    ctx.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384:@SECLEVEL=0")
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(str(ca_path))
    return ctx


class TestRsaPeerHandshakeNotificationServer:
    async def test_baseline_rejects_rsa_only_peer(self, notif_server_factory) -> None:
        """Notification server's IEEE 2030.5 baseline has no overlap with RSA-only peer."""
        fixture = await notif_server_factory()
        ctx = _rsa_only_client_ctx(fixture.server_cert_path)
        url = f"https://{fixture.host}:{fixture.port}/notify"
        with pytest.raises(
            (aiohttp.ClientConnectorError, aiohttp.ClientConnectorSSLError, ssl.SSLError)
        ):
            async with (
                aiohttp.ClientSession() as session,
                session.post(url, data=b"", ssl=ctx) as resp,
            ):
                await resp.read()

    async def test_additional_ciphers_unblocks_rsa_only_peer(self, notif_server_factory) -> None:
        """`additional_ciphers` extends the inbound listener the same way it does outbound."""
        fixture = await notif_server_factory(additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",))
        ctx = _rsa_only_client_ctx(fixture.server_cert_path)
        url = f"https://{fixture.host}:{fixture.port}/notify"
        # Handshake should now complete. The POST body is empty, so the
        # handler will return 400 ("Invalid notification XML") -- which is
        # fine: the assertion is that we reached the app layer at all
        # (i.e., TLS negotiation succeeded), not that the body parsed.
        async with (
            aiohttp.ClientSession() as session,
            session.post(url, data=b"", ssl=ctx) as resp,
        ):
            assert resp.status in (400, 201), (
                f"Unexpected status {resp.status} -- expected app-layer response "
                f"after successful TLS handshake"
            )

    async def test_tls_1_3_only_client_is_refused(self, notif_server_factory) -> None:
        """A TLS-1.3-only client must fail to handshake against the listener.

        Regression test for a gap in cipher configuration: `set_ciphers()`
        controls TLS 1.2 suites and earlier, but does NOT control TLS 1.3
        ciphersuites. Without a `maximum_version` pin on the server context,
        a TLS-1.3-capable peer would silently bypass the IEEE 2030.5 cipher
        baseline. The listener now caps at TLS 1.2, so this handshake must
        be rejected by version mismatch.
        """
        fixture = await notif_server_factory()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.maximum_version = ssl.TLSVersion.TLSv1_3
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
        ctx.load_verify_locations(str(fixture.server_cert_path))
        url = f"https://{fixture.host}:{fixture.port}/notify"
        with pytest.raises(
            (aiohttp.ClientConnectorError, aiohttp.ClientConnectorSSLError, ssl.SSLError)
        ):
            async with (
                aiohttp.ClientSession() as session,
                session.post(url, data=b"", ssl=ctx) as resp,
            ):
                await resp.read()
