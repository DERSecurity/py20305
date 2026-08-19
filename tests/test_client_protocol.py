"""Tests for HTTP protocol compliance: 301/429 handling and resource version caching."""

from __future__ import annotations

import asyncio
import ssl
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import aiohttp
import pytest

from py20305 import diagnostics
from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2RateLimitError,
    Sep2RedirectError,
)
from py20305.client.http import (
    APPLICATION_SEP_XML,
    ResourceVersionCache,
    Sep2Client,
)
from py20305.client.retry import RetryPolicy, with_retry


class TestRedirectHandling:
    def test_301_raises_redirect_error(self):
        """IEEE 5.5.2.7: 301 should raise Sep2RedirectError with Location."""
        resp = MagicMock()
        resp.status = 301
        resp.headers = {"Location": "/new/path"}

        with pytest.raises(Sep2RedirectError) as exc_info:
            Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/old/path")
        assert exc_info.value.location == "/new/path"

    def test_301_missing_location(self):
        resp = MagicMock()
        resp.status = 301
        resp.headers = {}

        with pytest.raises(Sep2RedirectError) as exc_info:
            Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/old/path")
        assert exc_info.value.location == ""


class TestRateLimitHandling:
    def test_429_raises_rate_limit_error(self):
        """IEEE 5.5.2.17: 429 should raise Sep2RateLimitError."""
        resp = MagicMock()
        resp.status = 429
        resp.headers = {"Retry-After": "30"}

        with pytest.raises(Sep2RateLimitError) as exc_info:
            Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/resource")
        assert exc_info.value.retry_after == 30

    def test_429_no_retry_after(self):
        resp = MagicMock()
        resp.status = 429
        resp.headers = {}

        with pytest.raises(Sep2RateLimitError) as exc_info:
            Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/resource")
        assert exc_info.value.retry_after is None

    def test_429_non_numeric_retry_after(self):
        resp = MagicMock()
        resp.status = 429
        resp.headers = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}

        with pytest.raises(Sep2RateLimitError) as exc_info:
            Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/resource")
        assert exc_info.value.retry_after is None

    def test_200_does_not_raise(self):
        resp = MagicMock()
        resp.status = 200
        resp.headers = {}
        # Should not raise
        Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/resource")


class TestRateLimitRetry:
    @pytest.mark.asyncio
    async def test_429_retried_with_retry_after(self):
        """429 should be retried using Retry-After header."""
        call_count = 0

        async def flaky_op() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Sep2RateLimitError("rate limited", retry_after=1)
            return "ok"

        policy = RetryPolicy(max_transient=3, base_delay=0.01)
        result = await with_retry(policy, flaky_op)
        assert result == "ok"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_429_exhausts_retries(self):
        """429 should eventually give up after max_transient."""

        async def always_limited() -> str:
            raise Sep2RateLimitError("rate limited", retry_after=0)

        policy = RetryPolicy(max_transient=2, base_delay=0.01)
        with pytest.raises(Sep2RateLimitError):
            await with_retry(policy, always_limited)


class TestResourceVersionCache:
    """IEEE 4.8: mRID/version change detection."""

    def test_new_resource_is_changed(self):
        cache = ResourceVersionCache()

        @dataclass
        class FakeResource:
            m_rid: object
            version: int

        @dataclass
        class MRid:
            value: bytes

        r = FakeResource(m_rid=MRid(value=b"\x01" * 16), version=1)
        assert cache.is_changed("/path", r) is True

    def test_same_resource_unchanged(self):
        cache = ResourceVersionCache()

        @dataclass
        class MRid:
            value: bytes

        @dataclass
        class R:
            m_rid: object
            version: int

        r = R(m_rid=MRid(value=b"\x01" * 16), version=1)
        cache.is_changed("/path", r)
        assert cache.is_changed("/path", r) is False

    def test_version_change_detected(self):
        cache = ResourceVersionCache()

        @dataclass
        class MRid:
            value: bytes

        @dataclass
        class R:
            m_rid: object
            version: int

        r1 = R(m_rid=MRid(value=b"\x01" * 16), version=1)
        r2 = R(m_rid=MRid(value=b"\x01" * 16), version=2)
        cache.is_changed("/path", r1)
        assert cache.is_changed("/path", r2) is True

    def test_no_mrid_always_changed(self):
        cache = ResourceVersionCache()

        class NoMrid:
            pass

        assert cache.is_changed("/path", NoMrid()) is True
        assert cache.is_changed("/path", NoMrid()) is True

    def test_different_paths_independent(self):
        cache = ResourceVersionCache()

        @dataclass
        class MRid:
            value: bytes

        @dataclass
        class R:
            m_rid: object
            version: int

        r = R(m_rid=MRid(value=b"\x01" * 16), version=1)
        cache.is_changed("/path/a", r)
        assert cache.is_changed("/path/b", r) is True

    def test_clear(self):
        cache = ResourceVersionCache()

        @dataclass
        class MRid:
            value: bytes

        @dataclass
        class R:
            m_rid: object
            version: int

        r = R(m_rid=MRid(value=b"\x01" * 16), version=1)
        cache.is_changed("/path", r)
        cache.clear()
        assert cache.is_changed("/path", r) is True


# ---------------------------------------------------------------------------
# Sep2Client chain validation and session management
# ---------------------------------------------------------------------------


class TestSep2ClientSessionManagement:
    def test_server_alive_defaults_true(self) -> None:
        client = Sep2Client("https://localhost:8443")
        assert client.server_alive is True
        assert client.last_error is None

    @pytest.mark.asyncio
    async def test_reset_session_clears_connection_state(self) -> None:
        client = Sep2Client("https://localhost:8443")
        client._chain_validated = True
        client._server_alive = True
        client._last_error = "stale error"
        await client.reset_session()
        assert client._chain_validated is False
        assert client._server_alive is False
        assert client._last_error is None

    def test_update_ca_trust_without_tls_config_noop(self) -> None:
        client = Sep2Client("https://localhost:8443")
        client.update_ca_trust("/tmp/ca.pem")  # should not raise

    def test_update_ca_trust_rebuilds_ssl_context(self) -> None:
        from py20305.client.tls import TlsConfig

        tls = TlsConfig(
            client_cert=Path("/tmp/cert.pem"),
            client_key=Path("/tmp/key.pem"),
            ca_cert=Path("/tmp/ca.pem"),
        )
        with (
            patch.object(ssl.SSLContext, "load_cert_chain"),
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch.object(ssl.SSLContext, "set_ciphers"),
        ):
            client = Sep2Client("https://localhost:8443", tls=tls)
            old_ssl = client._ssl
            client.update_ca_trust("/tmp/new_ca.pem")
            assert client._ssl is not old_ssl
            assert client._chain_validated is False

    def test_validate_chain_skipped_when_no_ssl(self) -> None:
        client = Sep2Client("http://localhost:8080")
        resp = MagicMock()
        client._validate_chain(resp)
        assert client._chain_validated is False


# ---------------------------------------------------------------------------
# LFDI request header (utility-proxy compatibility)
# ---------------------------------------------------------------------------


def _generate_test_client_cert_pem() -> str:
    """Self-signed P-256 client cert PEM for LFDI computation tests."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Client")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode("utf-8")


@pytest.fixture(scope="module")
def lfdi_test_client_cert_pem() -> str:
    """Generate the self-signed P-256 client cert once per module.

    Reused across all TestLfdiRequestHeader cases so cert/key generation
    (~50ms) only happens once instead of per-test.
    """
    return _generate_test_client_cert_pem()


class TestLfdiRequestHeader:
    """Sep2Client can inject an `LFDI:` request header when an operator
    opts in via `tls.send_lfdi_header = true`. Off by default (IEEE 2030.5
    §6.11.7.2 says servers SHOULD derive LFDI from the client cert);
    enabled for utility deployments fronted by a TLS-terminating proxy
    that strips the cert before the backend sees it.

    Helper default mirrors the prod default (off); tests that exercise
    the on-path opt in explicitly with `send_lfdi_header=True`.
    """

    def _client_with_cert(
        self,
        tmp_path: Path,
        cert_pem: str,
        *,
        send_lfdi_header: bool = False,
        lfdi_header_name: str = "LFDI",
    ):
        from py20305.client.tls import TlsConfig

        cert_file = tmp_path / "client.pem"
        cert_file.write_text(cert_pem)
        # The other paths don't need to exist -- create_ssl_context is the only
        # consumer and we're patching its three load_* calls.
        tls = TlsConfig(
            client_cert=cert_file,
            client_key=tmp_path / "client.key",
            ca_cert=tmp_path / "ca.pem",
            send_lfdi_header=send_lfdi_header,
            lfdi_header_name=lfdi_header_name,
        )
        with (
            patch.object(ssl.SSLContext, "load_cert_chain"),
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch.object(ssl.SSLContext, "set_ciphers"),
        ):
            return Sep2Client("https://localhost:8443", tls=tls)

    def test_lfdi_header_present_when_cert_configured_and_opted_in(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=True)
        headers = client._default_headers()
        assert "LFDI" in headers
        assert headers["LFDI"] == client._client_lfdi
        # 40-char lowercase hex per IEEE 2030.5 §6.11.7.2
        assert len(headers["LFDI"]) == 40
        assert headers["LFDI"] == headers["LFDI"].lower()
        assert all(c in "0123456789abcdef" for c in headers["LFDI"])

    def test_lfdi_header_absent_by_default_when_cert_configured(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """Prod default: even with a valid client cert, the LFDI header is
        NOT sent unless the operator opts in. IEEE 2030.5 §6.11.7.2 expects
        servers to derive LFDI from the cert."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem)
        # LFDI is still computed (forwarder attribution still wants it)
        # but it must NOT be on the wire.
        assert client._client_lfdi is not None
        assert "LFDI" not in client._default_headers()

    def test_lfdi_header_absent_when_no_tls(self):
        client = Sep2Client("https://localhost:8443")
        headers = client._default_headers()
        assert "LFDI" not in headers

    def test_lfdi_header_omitted_when_lfdi_computation_fails(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str, caplog
    ):
        """Realistic failure: cert is loadable for SSL (so create_ssl_context
        succeeds), but LFDI derivation raises -- e.g., compute_lfdi fails
        on a malformed PEM that OpenSSL still accepts. We patch
        `compute_lfdi` directly so the test reflects the actual failure
        mode rather than relying on the `load_cert_chain` patches to mask
        a missing file (which would also fail real `create_ssl_context`)."""
        from py20305.client.tls import TlsConfig

        cert_file = tmp_path / "client.pem"
        cert_file.write_text(lfdi_test_client_cert_pem)
        # Opt in explicitly so this test proves header-absent is caused by
        # the LFDI computation failure, not by the default-off flag.
        tls = TlsConfig(
            client_cert=cert_file,
            client_key=tmp_path / "client.key",
            ca_cert=tmp_path / "ca.pem",
            send_lfdi_header=True,
        )
        with (
            patch.object(ssl.SSLContext, "load_cert_chain"),
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch.object(ssl.SSLContext, "set_ciphers"),
            patch(
                "py20305.security.compute_lfdi",
                side_effect=ValueError("malformed PEM"),
            ),
            caplog.at_level("WARNING"),
        ):
            client = Sep2Client("https://localhost:8443", tls=tls)

        assert client._client_lfdi is None
        assert "LFDI" not in client._default_headers()
        assert any("Could not compute client LFDI" in r.message for r in caplog.records)

    def test_default_headers_still_include_xml_content_type(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """Adding LFDI must not displace the existing Accept/Content-Type headers."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem)
        headers = client._default_headers()
        assert headers["Accept"].startswith("application/sep+xml")
        assert headers["Content-Type"].startswith("application/sep+xml")

    @pytest.mark.asyncio
    async def test_session_has_lfdi_in_default_headers(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """The aiohttp session built by `_get_session` must carry LFDI so it
        applies to every request made through the session, not just the
        first one we happen to inspect."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=True)
        session = client._get_session()
        try:
            assert session.headers.get("LFDI") == client._client_lfdi
        finally:
            await session.close()

    def test_lfdi_header_omitted_when_send_lfdi_header_explicit_false(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """Explicit `tls.send_lfdi_header = false` produces no header. Same
        observable behavior as the default; this test guards against a
        future default flip silently changing the explicit-False case."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=False)
        # LFDI is still computed (forwarder attribution still wants it) but
        # it must NOT be on the wire.
        assert client._client_lfdi is not None
        assert "LFDI" not in client._default_headers()

    @pytest.mark.asyncio
    async def test_session_omits_lfdi_when_explicit_false(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """End-to-end: explicit False propagates to the aiohttp session."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=False)
        session = client._get_session()
        try:
            assert "LFDI" not in session.headers
        finally:
            await session.close()

    @pytest.mark.asyncio
    async def test_lfdi_header_survives_session_reset(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """`reset_session()` rebuilds the aiohttp session; the new session
        must still carry LFDI. Regression for: a connection error that
        triggers reset shouldn't silently drop the header for subsequent
        requests."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=True)
        original = client._get_session()
        original_lfdi = original.headers.get("LFDI")
        await client.reset_session()
        new_session = client._get_session()
        try:
            assert new_session is not original
            assert new_session.headers.get("LFDI") == original_lfdi
            assert original_lfdi is not None
        finally:
            await new_session.close()

    @pytest.mark.asyncio
    async def test_explicit_false_survives_session_reset(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """Reset must not silently flip an explicitly-disabled header back
        on. Mirrors `test_lfdi_header_survives_session_reset` for the
        explicit-False path."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=False)
        await client.reset_session()
        new_session = client._get_session()
        try:
            assert "LFDI" not in new_session.headers
        finally:
            await new_session.close()

    def test_disabled_header_does_not_disable_forwarder_attribution(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """`send_lfdi_header=False` is about the *wire* header, not LFDI
        computation. The forwarder still relies on `_client_lfdi` for
        message attribution -- it must remain populated even when the
        header is off (which, post-flip, is the default)."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=False)
        assert client._client_lfdi is not None
        assert len(client._client_lfdi) == 40

    @pytest.mark.asyncio
    async def test_update_client_cert_recomputes_lfdi_and_invalidates_session(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """Regression: `update_client_cert()` (used by COMM-004 PKI tests)
        rebuilds the SSL context but used to leave `_client_lfdi` and the
        cached aiohttp session pointing at the OLD cert -- so the LFDI
        request header would carry the wrong identity until restart."""
        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem, send_lfdi_header=True)
        original_lfdi = client._client_lfdi
        original_session = client._get_session()
        assert original_session.headers.get("LFDI") == original_lfdi

        # Generate a *different* cert and rotate to it.
        new_cert_pem = _generate_test_client_cert_pem()
        new_cert_file = tmp_path / "client_v2.pem"
        new_cert_file.write_text(new_cert_pem)
        with (
            patch.object(ssl.SSLContext, "load_cert_chain"),
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch.object(ssl.SSLContext, "set_ciphers"),
        ):
            client.update_client_cert(str(new_cert_file), str(tmp_path / "client_v2.key"))

        # Yield once so the close-task scheduled by update_client_cert runs
        await asyncio.sleep(0)

        # _client_lfdi tracked the rotation
        assert client._client_lfdi is not None
        assert client._client_lfdi != original_lfdi

        # The cached session was dropped so the next _get_session
        # rebuilds with the new LFDI in the default headers
        new_session = client._get_session()
        try:
            assert new_session is not original_session
            assert new_session.headers.get("LFDI") == client._client_lfdi
            # The orphaned session is closed (best-effort cleanup) -- no
            # leaked connection pool, no `Unclosed client session` warning.
            assert original_session.closed
        finally:
            await new_session.close()

    @pytest.mark.asyncio
    async def test_update_client_cert_closes_orphaned_session(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """`update_client_cert` must schedule a close of the orphaned session
        on the running event loop -- otherwise long-running processes that
        rotate certs repeatedly leak connection pools and emit
        `Unclosed client session` warnings on GC."""
        import asyncio as _asyncio

        client = self._client_with_cert(tmp_path, lfdi_test_client_cert_pem)
        old_session = client._get_session()
        assert not old_session.closed

        new_cert_file = tmp_path / "client_v2.pem"
        new_cert_file.write_text(_generate_test_client_cert_pem())
        with (
            patch.object(ssl.SSLContext, "load_cert_chain"),
            patch.object(ssl.SSLContext, "load_verify_locations"),
            patch.object(ssl.SSLContext, "set_ciphers"),
        ):
            client.update_client_cert(str(new_cert_file), str(tmp_path / "client_v2.key"))

        # Close was scheduled as a task; let it run.
        await _asyncio.sleep(0)
        assert old_session.closed

    def test_close_session_best_effort_no_running_loop(self):
        """The no-running-loop branch must actually `await` the close.

        Earlier revisions called `connector.close()` sync, which in
        aiohttp >= 3.9 returns an un-awaited coroutine and leaves the
        pool open. Regression for that bug.

        Uses AsyncMock instead of a real `ClientSession` so the test
        doesn't perturb asyncio module state for tests that follow it
        (a real `asyncio.run` here closes the event loop, which can
        break neighbour tests that still use the deprecated
        `asyncio.get_event_loop()` API).
        """
        from unittest.mock import AsyncMock

        session = AsyncMock(spec=aiohttp.ClientSession)
        session.closed = False

        Sep2Client._close_session_best_effort(session)

        # close() was actually awaited (would NOT be true if the helper
        # had returned an un-awaited coroutine like the old buggy code).
        session.close.assert_awaited_once()

    def test_lfdi_header_uses_custom_name(self, tmp_path: Path, lfdi_test_client_cert_pem: str):
        """Override `lfdi_header_name` to send under a different name (e.g.
        a peer that expects `X-LFDI` instead of `LFDI`)."""
        client = self._client_with_cert(
            tmp_path,
            lfdi_test_client_cert_pem,
            send_lfdi_header=True,
            lfdi_header_name="X-LFDI",
        )
        headers = client._default_headers()
        assert "X-LFDI" in headers
        assert "LFDI" not in headers  # default name must NOT also be sent
        assert headers["X-LFDI"] == client._client_lfdi

    @pytest.mark.asyncio
    async def test_session_carries_custom_lfdi_header_name(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """Live aiohttp session reflects the custom name end-to-end."""
        client = self._client_with_cert(
            tmp_path,
            lfdi_test_client_cert_pem,
            send_lfdi_header=True,
            lfdi_header_name="X-LFDI",
        )
        session = client._get_session()
        try:
            assert session.headers.get("X-LFDI") == client._client_lfdi
            assert "LFDI" not in session.headers
        finally:
            await session.close()

    def test_custom_header_name_respects_disabled_flag(
        self, tmp_path: Path, lfdi_test_client_cert_pem: str
    ):
        """`send_lfdi_header=False` wins over the custom name -- no header
        of any name is sent. Locks in the AND-gating in `_default_headers`."""
        client = self._client_with_cert(
            tmp_path,
            lfdi_test_client_cert_pem,
            send_lfdi_header=False,
            lfdi_header_name="X-LFDI",
        )
        headers = client._default_headers()
        assert "X-LFDI" not in headers
        assert "LFDI" not in headers

    def test_no_cert_and_default_flag_combined(self):
        """Degenerate case: no TLS at all + send_lfdi_header default=False.
        Header is absent because both there's no LFDI to send AND the flag
        is off by default. Locks in the AND-gating in `_default_headers`."""
        client = Sep2Client("https://localhost:8443")
        assert client._client_lfdi is None
        assert client._send_lfdi_header is False  # default when tls=None
        assert "LFDI" not in client._default_headers()

    def test_validate_chain_skipped_when_already_validated(self) -> None:
        client = Sep2Client("https://localhost:8443")
        client._ssl = MagicMock(spec=ssl.SSLContext)
        client._chain_validated = True
        resp = MagicMock()
        client._validate_chain(resp)  # should not re-validate

    def test_validate_chain_skipped_when_no_transport(self) -> None:
        client = Sep2Client("https://localhost:8443")
        client._ssl = MagicMock(spec=ssl.SSLContext)
        resp = MagicMock()
        resp.connection = None
        resp._protocol = None
        client._validate_chain(resp)
        assert client._chain_validated is False

    def test_validate_chain_fallback_to_protocol_transport(self) -> None:
        """When resp.connection is None (aiohttp >= 3.13), fall back to _protocol."""
        client = Sep2Client("https://localhost:8443")
        client._ssl = MagicMock(spec=ssl.SSLContext)
        ssl_obj = MagicMock(spec=[])  # no get_verified_chain
        resp = MagicMock()
        resp.connection = None
        resp._protocol.transport.get_extra_info.return_value = ssl_obj
        client._validate_chain(resp)
        # Reached chain validation via _protocol fallback; no get_verified_chain
        # means Python < 3.13 path — marked validated to skip future attempts
        assert client._chain_validated is True

    def test_validate_chain_skipped_when_no_ssl_object(self) -> None:
        client = Sep2Client("https://localhost:8443")
        client._ssl = MagicMock(spec=ssl.SSLContext)
        resp = MagicMock()
        resp.connection.transport.get_extra_info.return_value = None
        client._validate_chain(resp)
        assert client._chain_validated is False

    def test_validate_chain_skipped_when_no_get_verified_chain(self) -> None:
        """Python < 3.13: ssl_obj has no get_verified_chain."""
        client = Sep2Client("https://localhost:8443")
        client._ssl = MagicMock(spec=ssl.SSLContext)
        ssl_obj = MagicMock(spec=[])  # no get_verified_chain attr
        resp = MagicMock()
        resp.connection.transport.get_extra_info.return_value = ssl_obj
        client._validate_chain(resp)
        assert client._chain_validated is True  # marked validated to skip future attempts


class TestCustomRequestHeaders:
    """Sep2Client injects operator-supplied request_headers on every request,
    without letting them override the protocol Accept/Content-Type."""

    def test_custom_headers_present(self):
        client = Sep2Client(
            "https://localhost:8443",
            request_headers={"X-Api-Token": "abcd1234", "X-Env": "prod"},
        )
        headers = client._default_headers()
        assert headers["X-Api-Token"] == "abcd1234"
        assert headers["X-Env"] == "prod"

    def test_custom_headers_cannot_override_content_negotiation(self):
        client = Sep2Client(
            "https://localhost:8443",
            request_headers={"Accept": "text/plain", "Content-Type": "text/plain"},
        )
        headers = client._default_headers()
        assert headers["Accept"] == APPLICATION_SEP_XML
        assert headers["Content-Type"] == APPLICATION_SEP_XML

    def test_no_custom_headers_by_default(self):
        client = Sep2Client("https://localhost:8443")
        assert set(client._default_headers()) == {"Accept", "Content-Type"}


class TestChainValidationResetOnError:
    """_chain_validated must reset on connection errors so reconnects re-validate."""

    def _make_client(self) -> Sep2Client:
        client = Sep2Client("https://localhost:8443")
        client._chain_validated = True
        client._server_alive = True
        client._retry = RetryPolicy(max_transient=1, max_tls=1, base_delay=0.0)
        return client

    @pytest.mark.asyncio
    async def test_ssl_error_resets_chain_validated(self) -> None:
        from py20305.client.errors import Sep2TlsError

        client = self._make_client()
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = MagicMock(side_effect=ssl.SSLError("handshake failed"))
        mock_ctx.__aexit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_ctx

        with (
            patch.object(client, "_get_session", return_value=mock_session),
            pytest.raises(Sep2TlsError),
        ):
            await client.get("/test", MagicMock)
        assert client._chain_validated is False

    @pytest.mark.asyncio
    async def test_os_error_resets_chain_validated(self) -> None:
        client = self._make_client()
        mock_session = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = MagicMock(side_effect=ConnectionRefusedError("refused"))
        mock_ctx.__aexit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_ctx

        with (
            patch.object(client, "_get_session", return_value=mock_session),
            pytest.raises(Sep2ConnectionError),
        ):
            await client.get("/test", MagicMock)
        assert client._chain_validated is False


class TestDeviceLfdiResolution:
    """Tests for URL prefix → device LFDI resolution."""

    def test_resolve_edev_prefix(self) -> None:
        client = Sep2Client("https://example.com")
        client.update_device_lfdi_prefixes(
            {
                "/edev/2": "aa00000000000000000000000000000000000001",
            }
        )
        assert (
            client._resolve_device_lfdi("/edev/2/der/1/dera")
            == "aa00000000000000000000000000000000000001"
        )

    def test_resolve_fsa_prefix(self) -> None:
        client = Sep2Client("https://example.com")
        client.update_device_lfdi_prefixes(
            {
                "/FDA-SGA-TFA": "bb00000000000000000000000000000000000002",
            }
        )
        assert (
            client._resolve_device_lfdi("/FDA-SGA-TFA/derp/3/derc")
            == "bb00000000000000000000000000000000000002"
        )

    def test_resolve_exact_match(self) -> None:
        client = Sep2Client("https://example.com")
        client.update_device_lfdi_prefixes(
            {
                "/edev/2": "aa00000000000000000000000000000000000001",
            }
        )
        assert client._resolve_device_lfdi("/edev/2") == "aa00000000000000000000000000000000000001"

    def test_no_match_returns_none(self) -> None:
        client = Sep2Client("https://example.com")
        client.update_device_lfdi_prefixes(
            {
                "/edev/2": "aa00000000000000000000000000000000000001",
            }
        )
        assert client._resolve_device_lfdi("/dcap") is None

    def test_no_partial_prefix_match(self) -> None:
        """'/edev/2' should not match '/edev/22/...'."""
        client = Sep2Client("https://example.com")
        client.update_device_lfdi_prefixes(
            {
                "/edev/2": "aa00000000000000000000000000000000000001",
            }
        )
        assert client._resolve_device_lfdi("/edev/22/der/1") is None

    def test_multiple_devices(self) -> None:
        client = Sep2Client("https://example.com")
        client.update_device_lfdi_prefixes(
            {
                "/edev/2": "aa00000000000000000000000000000000000001",
                "/edev/3": "bb00000000000000000000000000000000000002",
                "/SPA1": "bb00000000000000000000000000000000000002",
            }
        )
        assert (
            client._resolve_device_lfdi("/edev/2/der/1")
            == "aa00000000000000000000000000000000000001"
        )
        assert (
            client._resolve_device_lfdi("/edev/3/der/1")
            == "bb00000000000000000000000000000000000002"
        )
        assert (
            client._resolve_device_lfdi("/SPA1/derp/1")
            == "bb00000000000000000000000000000000000002"
        )

    def test_empty_prefix_map(self) -> None:
        client = Sep2Client("https://example.com")
        assert client._resolve_device_lfdi("/edev/2/der/1") is None


class TestSchemaValidatorEntryPoint:
    """``set_schema_validator`` resolving the XSD entry point in ``schema_dir``.

    The entry point is edition-named (``sep2_schema_2023.xsd``).  A deployment
    directory still holding the old ``sep.xsd`` name must keep validating --
    that file was the 2023 schema all along -- rather than silently dropping to
    no validation on upgrade.
    """

    def test_edition_named_entry_point_is_used(self, tmp_path: Path) -> None:
        (tmp_path / "sep2_schema_2023.xsd").write_bytes(b"<xs:schema/>")
        client = Sep2Client("https://example.com")
        client.set_schema_validator(str(tmp_path))
        assert client._schema_path == tmp_path / "sep2_schema_2023.xsd"

    def test_legacy_name_is_accepted_with_a_warning(self, tmp_path: Path) -> None:
        (tmp_path / "sep.xsd").write_bytes(b"<xs:schema/>")
        store = diagnostics.init_store()
        client = Sep2Client("https://example.com")
        client.set_schema_validator(str(tmp_path))

        assert client._schema_path == tmp_path / "sep.xsd"
        warnings = store.snapshot()["warnings"]
        assert len(warnings) == 1, warnings
        assert "deprecated" in warnings[0]["message"]
        assert warnings[0]["details"]["expected"].endswith("sep2_schema_2023.xsd")

    def test_edition_named_entry_point_wins_over_legacy(self, tmp_path: Path) -> None:
        (tmp_path / "sep.xsd").write_bytes(b"<xs:schema/>")
        (tmp_path / "sep2_schema_2023.xsd").write_bytes(b"<xs:schema/>")
        store = diagnostics.init_store()
        client = Sep2Client("https://example.com")
        client.set_schema_validator(str(tmp_path))

        assert client._schema_path == tmp_path / "sep2_schema_2023.xsd"
        assert not store.snapshot()["warnings"]

    def test_neither_name_present_disables_validation(self, tmp_path: Path) -> None:
        store = diagnostics.init_store()
        client = Sep2Client("https://example.com")
        client.set_schema_validator(str(tmp_path))

        assert client._schema_path is None
        warnings = store.snapshot()["warnings"]
        assert len(warnings) == 1, warnings
        assert "validation disabled" in warnings[0]["message"]
