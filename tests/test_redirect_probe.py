"""Tests for the ERR-001 HTTP-to-HTTPS redirect probe.

Uses aiohttp.test_utils via pytest-aiohttp's ``aiohttp_server`` fixture
so the probe issues a real HTTP request against a real ``aiohttp.web``
app on a random port. ``run_redirect_probe`` accepts an explicit
``port`` argument so the test can point at the test server. The
HTTPS-followup leg remains mocked via a ``MagicMock`` Sep2Client --
that arm of the probe is about call sequencing and result-dict
assembly, not actual HTTPS behaviour, and the mTLS path is covered
end-to-end in ``tests/test_tls.py``.

For the connection-refused case (``aiohttp.ClientConnectionError``
historically simulated by aioresponses), the test binds a socket to
get an unused port and keeps it bound -- without calling ``listen()``
-- for the duration of the probe. The bound-but-not-listening socket
reserves the port against other binders while the kernel still rejects
incoming SYNs with RST, producing the real ECONNREFUSED the probe's
error path is designed to handle. Closing the socket before the probe
runs would leave a window for another process to bind the port and
turn the test into a non-refused (flaky) connection. See
``docs/planning/AIOHTTP_TEST_UTILS_MIGRATION.md`` D3 for the
rationale.
"""

import socket
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from py20305.client.redirect_probe import run_redirect_probe

HOST = "127.0.0.1"
HTTPS_LOCATION = "https://192.168.1.100:443/dcap"


@pytest.fixture
def mock_http_client():
    """Create a mock Sep2Client with get_raw()."""
    client = MagicMock()
    client.get_raw = AsyncMock()
    return client


async def _serve(aiohttp_server, app):
    """Boot ``app`` on a random port and return the bound ``TestServer``."""
    return await aiohttp_server(app)


class TestRedirectDetected:
    async def test_301_redirect(self, aiohttp_server, mock_http_client):
        """301 response is detected as a redirect."""

        async def handler(request):
            return web.Response(status=301, headers={"Location": HTTPS_LOCATION})

        app = web.Application()
        app.router.add_get("/dcap", handler)
        server = await _serve(aiohttp_server, app)
        mock_http_client.get_raw.return_value = {"status_code": 200}

        result = await run_redirect_probe(HOST, mock_http_client, port=server.port)

        assert result["is_redirect"] is True
        assert result["http_status"] == 301
        assert result["redirect_location"] == HTTPS_LOCATION
        assert result["status"] == "success"

    async def test_302_redirect(self, aiohttp_server, mock_http_client):
        """302 response is detected as a redirect."""

        async def handler(request):
            return web.Response(status=302, headers={"Location": HTTPS_LOCATION})

        app = web.Application()
        app.router.add_get("/dcap", handler)
        server = await _serve(aiohttp_server, app)
        mock_http_client.get_raw.return_value = {"status_code": 200}

        result = await run_redirect_probe(HOST, mock_http_client, port=server.port)

        assert result["is_redirect"] is True
        assert result["http_status"] == 302
        assert result["status"] == "success"


class TestRedirectFollowed:
    async def test_https_followup_success(self, aiohttp_server, mock_http_client):
        """HTTPS follow-up via mTLS client returns 200."""

        async def handler(request):
            return web.Response(status=301, headers={"Location": HTTPS_LOCATION})

        app = web.Application()
        app.router.add_get("/dcap", handler)
        server = await _serve(aiohttp_server, app)
        mock_http_client.get_raw.return_value = {"status_code": 200}

        result = await run_redirect_probe(HOST, mock_http_client, port=server.port)

        assert result["status"] == "success"
        assert result["https_status"] == 200
        mock_http_client.get_raw.assert_awaited_once_with(HTTPS_LOCATION)

    async def test_https_followup_error_status(self, aiohttp_server, mock_http_client):
        """HTTPS follow-up returns non-200 status."""

        async def handler(request):
            return web.Response(status=301, headers={"Location": HTTPS_LOCATION})

        app = web.Application()
        app.router.add_get("/dcap", handler)
        server = await _serve(aiohttp_server, app)
        mock_http_client.get_raw.return_value = {"status_code": 403, "error": "forbidden"}

        result = await run_redirect_probe(HOST, mock_http_client, port=server.port)

        assert result["status"] == "followup_error"
        assert result["https_status"] == 403
        assert result["https_error"] == "forbidden"

    async def test_https_followup_exception(self, aiohttp_server, mock_http_client):
        """HTTPS follow-up raises an exception."""

        async def handler(request):
            return web.Response(status=301, headers={"Location": HTTPS_LOCATION})

        app = web.Application()
        app.router.add_get("/dcap", handler)
        server = await _serve(aiohttp_server, app)
        mock_http_client.get_raw.side_effect = OSError("connection reset")

        result = await run_redirect_probe(HOST, mock_http_client, port=server.port)

        assert result["status"] == "followup_error"
        assert result["https_error"] == "connection reset"


class TestNoRedirect:
    async def test_200_no_redirect(self, aiohttp_server, mock_http_client):
        """Server returns 200 directly (no redirect)."""

        async def handler(request):
            return web.Response(status=200)

        app = web.Application()
        app.router.add_get("/dcap", handler)
        server = await _serve(aiohttp_server, app)

        result = await run_redirect_probe(HOST, mock_http_client, port=server.port)

        assert result["is_redirect"] is False
        assert result["http_status"] == 200
        assert result["status"] == "no_redirect"
        mock_http_client.get_raw.assert_not_awaited()


class TestConnectionError:
    async def test_server_unreachable(self, mock_http_client):
        """Connection error when the target port has no listener.

        Bind a socket to obtain an unused port, leave it bound (no
        ``listen()``) for the duration of the probe, then close. The
        bound-but-not-listening socket reserves the port -- preventing
        another process from racing in and accepting the connection --
        while still producing ECONNREFUSED for TCP connects, since
        without ``listen()`` the kernel rejects SYNs with RST. A more
        faithful exercise of the error path than mocking an exception
        object.
        """
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        try:
            result = await run_redirect_probe(HOST, mock_http_client, port=port)
        finally:
            sock.close()

        assert result["status"] == "error"
        assert result["is_redirect"] is False
        assert "http_error" in result
        mock_http_client.get_raw.assert_not_awaited()
