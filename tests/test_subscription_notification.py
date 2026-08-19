"""Tests for NotificationServer and notification parsing."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from py20305 import diagnostics
from py20305.diagnostics import DiagnosticsStore
from py20305.subscription import notification_server as _ns
from py20305.subscription.notification_server import (
    STATUS_CANCELLED,
    STATUS_DEFAULT,
    STATUS_DEFINITION_CHANGED,
    STATUS_RESOURCE_DELETED,
    STATUS_RESOURCE_MOVED,
    NotificationServer,
    parse_notification,
    validate_notification,
)
from py20305.xml.serialization import XmlParseError

logger = _ns.logger

# -- Sample XML payloads --

NOTIFICATION_STATUS_0 = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <status>0</status>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""

NOTIFICATION_STATUS_1 = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <status>1</status>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""

NOTIFICATION_STATUS_2_MOVED = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <newResourceURI>https://server:8443/edev/2/fsa</newResourceURI>
  <status>2</status>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""

NOTIFICATION_STATUS_3 = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <status>3</status>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""

NOTIFICATION_STATUS_4 = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <status>4</status>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""

NOTIFICATION_WITH_RESOURCE = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns"
              xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <Resource xsi:type="Resource" href="/edev/1/fsa"/>
  <status>0</status>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""

NOTIFICATION_WITH_DATETIME = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <createdDateTime>1707840000</createdDateTime>
  <status>0</status>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""

INVALID_XML = b"<not valid xml"

NOTIFICATION_MISSING_STATUS = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<Notification xmlns="urn:ieee:std:2030.5:ns">
  <subscribedResource>/edev/1/fsa</subscribedResource>
  <subscriptionURI>https://server:8443/edev/1/sub/1</subscriptionURI>
</Notification>
"""


class TestStatusConstants:
    def test_values(self):
        assert STATUS_DEFAULT == 0
        assert STATUS_CANCELLED == 1
        assert STATUS_RESOURCE_MOVED == 2
        assert STATUS_DEFINITION_CHANGED == 3
        assert STATUS_RESOURCE_DELETED == 4


class TestParseNotification:
    def test_status_0(self):
        n = parse_notification(NOTIFICATION_STATUS_0)
        assert n.status == 0
        assert n.subscribed_resource == "/edev/1/fsa"
        assert n.subscription_uri == "https://server:8443/edev/1/sub/1"

    def test_status_1_cancelled(self):
        n = parse_notification(NOTIFICATION_STATUS_1)
        assert n.status == 1

    def test_status_2_moved(self):
        n = parse_notification(NOTIFICATION_STATUS_2_MOVED)
        assert n.status == 2
        assert n.new_resource_uri == "https://server:8443/edev/2/fsa"

    def test_status_3_definition_changed(self):
        n = parse_notification(NOTIFICATION_STATUS_3)
        assert n.status == 3

    def test_status_4_deleted(self):
        n = parse_notification(NOTIFICATION_STATUS_4)
        assert n.status == 4

    def test_with_created_datetime(self):
        n = parse_notification(NOTIFICATION_WITH_DATETIME)
        assert n.created_date_time is not None
        assert n.created_date_time.value == 1707840000

    def test_invalid_xml_raises(self):
        # parse_notification now wraps every parse-time failure -- raw lxml
        # errors, xsdata ParserError, pydantic ValidationError -- in
        # XmlParseError so callers see a typed, ValueError-derived
        # exception with snippet + length context.
        with pytest.raises(XmlParseError):
            parse_notification(INVALID_XML)

    def test_missing_required_fields_raises(self):
        with pytest.raises(XmlParseError):
            parse_notification(NOTIFICATION_MISSING_STATUS)


class TestValidateNotification:
    def test_no_resource_passes(self):
        n = parse_notification(NOTIFICATION_STATUS_0)
        assert validate_notification(n, NOTIFICATION_STATUS_0) is True

    def test_with_resource_xsi_type_passes(self):
        n = parse_notification(NOTIFICATION_WITH_RESOURCE)
        assert validate_notification(n, NOTIFICATION_WITH_RESOURCE) is True

    def test_invalid_xml_returns_false(self):
        # Create a notification manually, pass bad raw XML
        n = parse_notification(NOTIFICATION_STATUS_0)
        # Force resource to be non-None so validation tries to parse
        from py20305.models.sep.sep import Resource

        n.resource = Resource()
        assert validate_notification(n, INVALID_XML) is False


class TestNotificationServer:
    @pytest.fixture
    def callback(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    async def server_and_client(self, callback: AsyncMock):
        """Create a NotificationServer with an aiohttp test client."""
        server = NotificationServer(
            host="127.0.0.1",
            port=0,  # Let OS assign port
            tls=None,
            on_notification=callback,
        )
        # Build the app manually for testing (no TLS needed)
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)

        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        yield server, client, callback

        await client.close()

    @pytest.mark.asyncio
    async def test_valid_notification_returns_201(self, server_and_client):
        _, client, callback = server_and_client
        resp = await client.post(
            "/notify",
            data=NOTIFICATION_STATUS_0,
            headers={"Content-Type": "application/sep+xml"},
        )
        assert resp.status == 201
        await asyncio.sleep(0)  # let background task run
        callback.assert_called_once()
        notification = callback.call_args[0][0]
        assert notification.status == 0
        assert notification.subscribed_resource == "/edev/1/fsa"

    @pytest.mark.asyncio
    async def test_invalid_xml_returns_400(self, server_and_client):
        _, client, callback = server_and_client
        resp = await client.post(
            "/notify",
            data=INVALID_XML,
            headers={"Content-Type": "application/sep+xml"},
        )
        assert resp.status == 400
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_error_still_returns_201(self, server_and_client):
        _, client, callback = server_and_client
        callback.side_effect = RuntimeError("callback exploded")
        resp = await client.post(
            "/notify",
            data=NOTIFICATION_STATUS_0,
            headers={"Content-Type": "application/sep+xml"},
        )
        assert resp.status == 201
        await asyncio.sleep(0)  # let background task run

    @pytest.mark.asyncio
    async def test_no_callback_still_returns_201(self):
        """Server without a callback should still accept notifications."""
        server = NotificationServer(host="127.0.0.1", port=0, tls=None, on_notification=None)
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)

        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        try:
            resp = await client.post(
                "/notify",
                data=NOTIFICATION_STATUS_0,
                headers={"Content-Type": "application/sep+xml"},
            )
            assert resp.status == 201
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_status_1_notification(self, server_and_client):
        _, client, callback = server_and_client
        resp = await client.post("/notify", data=NOTIFICATION_STATUS_1)
        assert resp.status == 201
        await asyncio.sleep(0)  # let background task run
        notification = callback.call_args[0][0]
        assert notification.status == 1

    @pytest.mark.asyncio
    async def test_status_2_moved_notification(self, server_and_client):
        _, client, callback = server_and_client
        resp = await client.post("/notify", data=NOTIFICATION_STATUS_2_MOVED)
        assert resp.status == 201
        await asyncio.sleep(0)  # let background task run
        notification = callback.call_args[0][0]
        assert notification.status == 2
        assert notification.new_resource_uri == "https://server:8443/edev/2/fsa"

    @pytest.mark.asyncio
    async def test_notification_with_resource(self, server_and_client):
        _, client, _callback = server_and_client
        resp = await client.post("/notify", data=NOTIFICATION_WITH_RESOURCE)
        assert resp.status == 201


class TestNotificationServerLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        server = NotificationServer(host="127.0.0.1", port=0, tls=None)
        assert server.running is False

        await server.start()
        assert server.running is True

        # Starting again is a no-op
        await server.start()
        assert server.running is True

        await server.stop()
        assert server.running is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        server = NotificationServer(host="127.0.0.1", port=0, tls=None)
        await server.stop()  # Should not raise

    def test_build_notification_uri(self):
        server = NotificationServer(port=10443)
        uri = server.build_notification_uri("192.168.1.100")
        assert uri == "https://192.168.1.100:10443/notify"

    def test_build_notification_uri_custom_port(self):
        server = NotificationServer(port=9999)
        uri = server.build_notification_uri("agg.local")
        assert uri == "https://agg.local:9999/notify"

    @pytest.mark.asyncio
    async def test_listener_uses_reuse_address(self):
        """TCPSite must set SO_REUSEADDR so the listener can rebind to a port
        still in TIME_WAIT from a previous instance.

        Regression test -- cert e2e suites tear down and recreate
        aggregators in a single pytest session; without SO_REUSEADDR the
        second start fails when the prior listener's port hasn't fully
        released.
        """
        server = NotificationServer(host="127.0.0.1", port=0, tls=None)
        await server.start()
        try:
            assert server._site is not None
            assert server._site._reuse_address is True
        finally:
            await server.stop()


class TestFireAndForgetNotification:
    @pytest.mark.asyncio
    async def test_notify_returns_201_before_callback_completes(self):
        """201 is returned immediately; callback runs in background."""
        started = asyncio.Event()
        gate = asyncio.Event()

        async def slow_callback(notification):
            started.set()
            await gate.wait()

        server = NotificationServer(
            host="127.0.0.1", port=0, tls=None, on_notification=slow_callback
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        try:
            resp = await client.post("/notify", data=NOTIFICATION_STATUS_0)
            assert resp.status == 201

            # Callback should have started but not finished
            await asyncio.sleep(0)
            assert started.is_set()

            # Unblock callback
            gate.set()
            await asyncio.sleep(0)
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_stop_cancels_background_tasks(self):
        """stop() cancels in-flight background notification tasks."""
        gate = asyncio.Event()

        async def slow_callback(notification):
            await gate.wait()

        server = NotificationServer(
            host="127.0.0.1", port=0, tls=None, on_notification=slow_callback
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        try:
            resp = await client.post("/notify", data=NOTIFICATION_STATUS_0)
            assert resp.status == 201
            await asyncio.sleep(0)  # let task start

            assert len(server._background_tasks) == 1
            await server.stop()
            assert len(server._background_tasks) == 0
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# Tests: TLS context (IEEE 2030.5-2023 §6.4 + Table 12 mTLS for notifications)
# ---------------------------------------------------------------------------


class TestNotificationServerTls:
    """The notification listener must require client certs from the IEEE 2030.5
    server pushing notifications, matching the rest of the protocol's mTLS.
    """

    def _make_tls_config(self, tmp_path):
        """Generate a self-signed cert + write a CA bundle, return a TlsConfig."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        from py20305.client.tls import TlsConfig

        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "test")])
        import datetime

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.UTC))
            .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256())
        )

        cert_path = tmp_path / "cert.pem"
        key_path = tmp_path / "key.pem"
        ca_path = tmp_path / "ca.pem"

        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ca_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

        return TlsConfig(
            client_cert=cert_path,
            client_key=key_path,
            ca_cert=ca_path,
        )

    def test_ssl_context_default_mode_is_warn(self, tmp_path):
        """Default ``client_cert_mode='warn'`` uses CERT_OPTIONAL.

        ``warn`` accepts handshakes with or without a client cert; the
        app layer logs a warning when none was presented. This is the
        default for backward compatibility with old IEEE 2030.5 servers
        that don't yet present a device cert on notification POSTs.
        """
        import ssl

        tls = self._make_tls_config(tmp_path)
        server = NotificationServer(host="127.0.0.1", port=10443, tls=tls)
        ctx = server._create_server_ssl_context()

        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_OPTIONAL
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2
        # TLS 1.3 must be capped, not just floored. set_ciphers() does not
        # control TLS 1.3 ciphersuites -- without a maximum_version pin a
        # TLS 1.3 handshake would silently bypass the IEEE 2030.5 cipher
        # baseline.
        assert ctx.maximum_version == ssl.TLSVersion.TLSv1_2

    def test_ssl_context_off_mode_uses_cert_none(self, tmp_path):
        """``client_cert_mode='off'`` disables verification entirely."""
        import ssl

        tls = self._make_tls_config(tmp_path)
        server = NotificationServer(host="127.0.0.1", port=10443, tls=tls, client_cert_mode="off")
        ctx = server._create_server_ssl_context()

        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_ssl_context_enforce_mode_uses_cert_optional(self, tmp_path):
        """``client_cert_mode='enforce'`` uses CERT_OPTIONAL at TLS layer.

        The app layer rejects no-cert requests with HTTP 401; we deliberately
        do NOT use CERT_REQUIRED so that a presented-but-wrong-CA cert still
        fails loudly at TLS while a no-cert client gets a structured 401
        response instead of an opaque connection-reset error.
        """
        import ssl

        tls = self._make_tls_config(tmp_path)
        server = NotificationServer(
            host="127.0.0.1", port=10443, tls=tls, client_cert_mode="enforce"
        )
        ctx = server._create_server_ssl_context()

        assert ctx is not None
        assert ctx.verify_mode == ssl.CERT_OPTIONAL

    def test_ssl_context_loads_ca_into_trust_store(self, tmp_path):
        """``load_verify_locations`` was actually called with our CA bundle.

        ``verify_mode == CERT_REQUIRED`` alone doesn't prove the trust store
        was populated -- a future refactor that drops ``load_verify_locations``
        would leave verify_mode set but accept no clients.
        ``cert_store_stats`` reports the loaded trust store size, which is
        non-zero only when a CA file has been loaded. Unlike
        ``get_ca_certs()`` this works for certs that don't carry the
        BasicConstraints CA extension (our self-signed test cert doesn't).
        """
        tls = self._make_tls_config(tmp_path)
        server = NotificationServer(host="127.0.0.1", port=10443, tls=tls)
        ctx = server._create_server_ssl_context()
        assert ctx is not None

        stats = ctx.cert_store_stats()
        assert stats.get("x509", 0) >= 1, (
            f"Trust store empty: {stats} -- load_verify_locations was not called"
        )

    def test_ssl_context_construction_fails_with_missing_cert_files(self, tmp_path):
        """Building the context with non-existent cert paths must raise.

        Python's ssl module has no public introspection of the loaded
        server cert chain, so we prove ``load_cert_chain`` is being called
        by showing that pointing it at a missing file fails fast. If the
        call were silently dropped, this test would pass without raising.
        """
        from py20305.client.tls import TlsConfig

        bogus = TlsConfig(
            client_cert=tmp_path / "does-not-exist.pem",
            client_key=tmp_path / "does-not-exist.key",
            ca_cert=tmp_path / "does-not-exist-ca.pem",
        )
        server = NotificationServer(host="127.0.0.1", port=10443, tls=bogus)
        with pytest.raises((FileNotFoundError, OSError)):
            server._create_server_ssl_context()

    def test_no_tls_returns_no_context(self):
        """Without a TlsConfig, no SSL context is created (plain HTTP fallback)."""
        server = NotificationServer(host="127.0.0.1", port=10443, tls=None)
        ctx = server._create_server_ssl_context()
        assert ctx is None

    def test_ssl_context_pins_ieee_2030_5_baseline_ciphers(self, tmp_path):
        """Server cipher list defaults to the IEEE 2030.5 ECDSA baseline.

        Without an explicit ``set_ciphers`` call OpenSSL would fall back to
        its system default list, which accepts suites the spec doesn't
        sanction. Pinning the baseline keeps inbound TLS aligned with the
        outbound client (`client/tls.py`) so a single config knob controls
        both directions.
        """
        import ssl
        from unittest.mock import patch

        tls = self._make_tls_config(tmp_path)
        server = NotificationServer(host="127.0.0.1", port=10443, tls=tls)
        with patch.object(ssl.SSLContext, "set_ciphers") as mock_set:
            server._create_server_ssl_context()
        mock_set.assert_called_once()
        cipher_string = mock_set.call_args.args[0]
        assert "ECDHE-ECDSA-AES256-GCM-SHA384" in cipher_string
        assert "ECDHE-ECDSA-AES128-CCM8" in cipher_string
        assert "ECDHE-RSA" not in cipher_string

    def test_ssl_context_appends_additional_ciphers(self, tmp_path):
        """``additional_ciphers`` extends the baseline on the notification listener.

        Mirrors the outbound client's escape hatch: operators paired with a
        utility test peer fronted by enterprise PKI need RSA suites to
        complete the handshake in either direction.
        """
        import dataclasses
        import ssl
        from unittest.mock import patch

        base = self._make_tls_config(tmp_path)
        tls = dataclasses.replace(base, additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",))
        server = NotificationServer(host="127.0.0.1", port=10443, tls=tls)
        with patch.object(ssl.SSLContext, "set_ciphers") as mock_set:
            server._create_server_ssl_context()
        cipher_string = mock_set.call_args.args[0]
        assert cipher_string.endswith("ECDHE-RSA-AES256-GCM-SHA384")
        assert "ECDHE-ECDSA-AES256-GCM-SHA384" in cipher_string

    def test_ssl_context_logs_warning_when_additional_ciphers_set(self, tmp_path, caplog):
        """Relaxing the cipher policy must leave a visible startup-log trail."""
        import dataclasses
        import logging as stdlib_logging
        import ssl
        from unittest.mock import patch

        base = self._make_tls_config(tmp_path)
        tls = dataclasses.replace(base, additional_ciphers=("ECDHE-RSA-AES256-GCM-SHA384",))
        server = NotificationServer(host="127.0.0.1", port=10443, tls=tls)
        with (
            patch.object(ssl.SSLContext, "set_ciphers"),
            caplog.at_level(stdlib_logging.WARNING, logger=logger.name),
        ):
            server._create_server_ssl_context()
        assert any(
            "cipher policy relaxed" in r.message and "ECDHE-RSA-AES256-GCM-SHA384" in r.message
            for r in caplog.records
        ), f"Expected cipher-policy warning, got: {[r.message for r in caplog.records]}"

    # ----- app-layer client-cert mode behavior -----

    @pytest.mark.asyncio
    async def test_warn_mode_accepts_no_client_cert_with_warning(self, caplog):
        """warn mode: no peer cert → 201 + WARNING log naming the peer."""
        import logging as stdlib_logging

        callback = AsyncMock()
        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=callback,
            client_cert_mode="warn",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        try:
            with caplog.at_level(stdlib_logging.WARNING, logger=logger.name):
                resp = await client.post(
                    "/notify",
                    data=NOTIFICATION_STATUS_0,
                    headers={"Content-Type": "application/sep+xml"},
                )
            assert resp.status == 201
            # Behavior is the test (201 returned despite no cert); the
            # message-content check just confirms the operator got a
            # readable warn-mode signal in the diagnostics stream.
            assert any("without a client certificate" in r.message for r in caplog.records), (
                f"Expected warn-mode notice, got: {[r.message for r in caplog.records]}"
            )
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_enforce_mode_rejects_no_client_cert(self, caplog):
        """enforce mode: no peer cert → 401 + WARNING."""
        import logging as stdlib_logging

        callback = AsyncMock()
        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=callback,
            client_cert_mode="enforce",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        try:
            with caplog.at_level(stdlib_logging.WARNING, logger=logger.name):
                resp = await client.post(
                    "/notify",
                    data=NOTIFICATION_STATUS_0,
                    headers={"Content-Type": "application/sep+xml"},
                )
            assert resp.status == 401
            callback.assert_not_called()
            assert any("Rejecting notification" in r.message for r in caplog.records), (
                f"Expected reject log, got: {[r.message for r in caplog.records]}"
            )
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_off_mode_accepts_no_client_cert_silently(self, caplog):
        """off mode: no peer cert → 201, no cert-related warning."""
        import logging as stdlib_logging

        callback = AsyncMock()
        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=callback,
            client_cert_mode="off",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        try:
            with caplog.at_level(stdlib_logging.WARNING, logger=logger.name):
                resp = await client.post(
                    "/notify",
                    data=NOTIFICATION_STATUS_0,
                    headers={"Content-Type": "application/sep+xml"},
                )
            assert resp.status == 201
            assert not any("client certificate" in r.message for r in caplog.records), (
                f"off mode should be silent, got: {[r.message for r in caplog.records]}"
            )
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_warn_mode_accepts_with_peercert_no_warning(self, caplog):
        """warn mode with a peer cert presented → 201, no cert warning.

        Patches request.transport.get_extra_info to simulate a TLS handshake
        that produced a peer cert, even though the test transport is plain
        HTTP.
        """
        import logging as stdlib_logging
        from unittest.mock import patch

        callback = AsyncMock()
        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=callback,
            client_cert_mode="warn",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        test_server = TestServer(app)
        client = TestClient(test_server)
        await client.start_server()

        fake_peercert = {"subject": (((b"CN", "test"),),)}
        fake_transport = _FakeTransport(fake_peercert)
        try:
            with (
                patch.object(
                    web.Request,
                    "transport",
                    new_callable=lambda: property(lambda self: fake_transport),
                ),
                caplog.at_level(stdlib_logging.WARNING, logger=logger.name),
            ):
                resp = await client.post(
                    "/notify",
                    data=NOTIFICATION_STATUS_0,
                    headers={"Content-Type": "application/sep+xml"},
                )
            assert resp.status == 201
            assert not any("client certificate" in r.message for r in caplog.records), (
                f"presented cert should not log, got: {[r.message for r in caplog.records]}"
            )
        finally:
            await client.close()


class _FakeTransport:
    """Minimal stand-in for asyncio.Transport for tests that need to fake peercert."""

    def __init__(self, peercert):
        self._peercert = peercert

    def get_extra_info(self, name, default=None):
        if name == "peercert":
            return self._peercert
        return default


class TestNotificationDiagnostics:
    """U6 / U6b / U12: notification-server callsites surface in the UI."""

    @pytest.mark.asyncio
    async def test_enforce_no_cert_emits_diagnostic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=AsyncMock(),
            client_cert_mode="enforce",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.post("/notify", data=NOTIFICATION_STATUS_0)
            assert resp.status == 401
        finally:
            await client.close()

        warnings = fresh.snapshot()["warnings"]
        assert any(
            "Rejecting notification" in w["message"] and w["source"] == "notification"
            for w in warnings
        ), warnings

    @pytest.mark.asyncio
    async def test_warn_no_cert_emits_diagnostic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=AsyncMock(),
            client_cert_mode="warn",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.post("/notify", data=NOTIFICATION_STATUS_0)
            assert resp.status == 201
        finally:
            await client.close()

        warnings = fresh.snapshot()["warnings"]
        assert any(
            "without a client certificate" in w["message"] and w["source"] == "notification"
            for w in warnings
        ), warnings

    @pytest.mark.asyncio
    async def test_parse_failure_emits_diagnostic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=AsyncMock(),
            client_cert_mode="off",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            resp = await client.post("/notify", data=b"not valid xml at all")
            assert resp.status == 400
        finally:
            await client.close()

        warnings = fresh.snapshot()["warnings"]
        assert any(
            "Failed to parse notification" in w["message"] and w["source"] == "notification"
            for w in warnings
        ), warnings

    @pytest.mark.asyncio
    async def test_callback_exception_emits_deduped_diagnostic(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """B11: callback raising the same exception across N notifications collapses
        to a single warning with `count` reflecting the volume.
        """
        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)

        async def boom(_notification) -> None:
            raise RuntimeError("kaboom")

        server = NotificationServer(
            host="127.0.0.1",
            port=0,
            tls=None,
            on_notification=boom,
            client_cert_mode="off",
        )
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            for _ in range(3):
                resp = await client.post("/notify", data=NOTIFICATION_STATUS_0)
                assert resp.status == 201
            # Let the spawned _safe_notify tasks run.
            await asyncio.sleep(0.05)
        finally:
            await client.close()

        warnings = fresh.snapshot()["warnings"]
        callback_warnings = [
            w
            for w in warnings
            if w.get("details", {}).get("exc_kind") == "RuntimeError" and "callback" in w["message"]
        ]
        # All emissions collapsed to a single deduped entry.
        assert len(callback_warnings) == 1
        # And dedup hits incremented count beyond 1.
        assert callback_warnings[0].get("count", 1) >= 2
