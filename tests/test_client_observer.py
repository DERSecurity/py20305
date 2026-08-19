"""Tests for the connection-observer seam on Sep2Client and its connector.

The client reports its own connection outcomes because a passive capture
beside it cannot recover them from inside TLS. Two layers here: the funnel
tests drive real HTTP requests through Sep2Client (the aiohttp_server pattern
from test_client.py) and assert what a recording observer saw; the connector
tests exercise the socket-address plumbing directly, since the local port is
only knowable at connection establishment.
"""

from __future__ import annotations

import logging

import pytest
from aiohttp import web

from py20305.client.connector import (
    Address,
    Ieee2030TCPConnector,
    SocketPair,
    _as_address,
)
from py20305.client.errors import Sep2NoContentError, Sep2ProtocolError
from py20305.client.http import Sep2Client
from py20305.models.sep.sep import Time
from py20305.xml.serialization import to_xml
from tests.conftest import make_time


class RecordingObserver:
    """Keeps every callback in order, so tests can assert the exact sequence."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def begin_request(self) -> None:
        self.calls.append(("begin_request", None))

    def record_success(self) -> None:
        self.calls.append(("record_success", None))

    def record_failure(self, exc: BaseException) -> None:
        self.calls.append(("record_failure", exc))

    def on_connect(self, pair: SocketPair) -> None:
        self.calls.append(("on_connect", pair))

    def flush(self) -> None:
        self.calls.append(("flush", None))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


async def _serve(aiohttp_server, app):
    """Boot ``app`` on a random port and return its base URL as a string."""
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/")


def _time_app() -> web.Application:
    """An app serving a valid Time resource at /tm."""
    xml = to_xml(make_time(999, 3))

    async def handler(request):
        return web.Response(body=xml, content_type="application/sep+xml")

    app = web.Application()
    app.router.add_get("/tm", handler)
    return app


# -- Request-outcome reporting through the funnels ----------------------------


class TestRequestOutcomeReporting:
    async def test_a_successful_get_reports_begin_then_success(self, aiohttp_server):
        """The observer sees one begin/success pair per logical request."""
        base_url = await _serve(aiohttp_server, _time_app())
        observer = RecordingObserver()

        async with Sep2Client(base_url, connection_observer=observer) as client:
            await client.get("/tm", Time)

        assert observer.names()[:2] == ["begin_request", "record_success"]

    async def test_a_failing_get_reports_the_exception(self, aiohttp_server):
        """The failure record carries the real exception, not a summary of it."""

        async def handler(request):
            return web.Response(status=404, body=b"not found")

        app = web.Application()
        app.router.add_get("/missing", handler)
        base_url = await _serve(aiohttp_server, app)
        observer = RecordingObserver()

        async with Sep2Client(base_url, connection_observer=observer) as client:
            with pytest.raises(Sep2ProtocolError):
                await client.get("/missing", Time)

        assert observer.names()[:2] == ["begin_request", "record_failure"]
        recorded_exc = observer.calls[1][1]
        assert isinstance(recorded_exc, Sep2ProtocolError)

    async def test_a_204_counts_as_success_and_still_raises(self, aiohttp_server):
        """A 204 is a validated contact that signals itself by raising.

        Routing it through the failure branch would leave every empty optional
        resource unrecorded -- an under-count in a log whose whole purpose is
        to account for attempts.
        """

        async def handler(request):
            return web.Response(status=204)

        app = web.Application()
        app.router.add_get("/empty", handler)
        base_url = await _serve(aiohttp_server, app)
        observer = RecordingObserver()

        async with Sep2Client(base_url, connection_observer=observer) as client:
            with pytest.raises(Sep2NoContentError):
                await client.get("/empty", Time)

        assert observer.names()[:2] == ["begin_request", "record_success"]

    async def test_a_non_get_request_reports_through_the_same_seam(self, aiohttp_server):
        """POST funnels through _send_tracked; the observer must see it too."""

        async def handler(request):
            return web.Response(status=201, headers={"Location": "/mup/1"})

        app = web.Application()
        app.router.add_post("/mup", handler)
        base_url = await _serve(aiohttp_server, app)
        observer = RecordingObserver()

        async with Sep2Client(base_url, connection_observer=observer) as client:
            await client.post("/mup", make_time())

        assert observer.names()[:2] == ["begin_request", "record_success"]

    async def test_get_with_body_reports_through_the_same_seam(self, aiohttp_server):
        """The raw-body GET variant is its own funnel and must not be a gap."""
        base_url = await _serve(aiohttp_server, _time_app())
        observer = RecordingObserver()

        async with Sep2Client(base_url, connection_observer=observer) as client:
            result, body = await client.get_with_body("/tm", Time)

        assert result.current_time.value == 999
        assert observer.names()[:2] == ["begin_request", "record_success"]

    async def test_an_observer_attached_via_the_property_reports(self, aiohttp_server):
        """An embedder reads the config that decides on observation later than
        it builds the client, so attachment after construction must work."""
        base_url = await _serve(aiohttp_server, _time_app())
        observer = RecordingObserver()

        async with Sep2Client(base_url) as client:
            client.connection_observer = observer
            assert client.connection_observer is observer
            await client.get("/tm", Time)

        assert observer.names()[:2] == ["begin_request", "record_success"]

    async def test_without_an_observer_requests_behave_as_before(self, aiohttp_server):
        """No observer, no reporting -- and no change to the request path."""
        base_url = await _serve(aiohttp_server, _time_app())

        async with Sep2Client(base_url) as client:
            result = await client.get("/tm", Time)

        assert result.current_time.value == 999


# -- Lifecycle ----------------------------------------------------------------


class TestCloseFlushesTheObserver:
    async def test_close_flushes_before_the_session_closes(self, aiohttp_server):
        """Successes coalesced since the observer's last window are still
        attempts the connection log accounts for; they must go out before the
        transport underneath them is gone."""
        base_url = await _serve(aiohttp_server, _time_app())
        observer = RecordingObserver()
        session_open_at_flush: list[bool] = []

        client = Sep2Client(base_url, connection_observer=observer)

        def flush_recording_session_state() -> None:
            session_open_at_flush.append(client._session is not None and not client._session.closed)
            observer.calls.append(("flush", None))

        observer.flush = flush_recording_session_state  # type: ignore[method-assign]

        await client.get("/tm", Time)
        await client.close()

        assert observer.names()[-1] == "flush"
        assert session_open_at_flush == [True]

    async def test_close_without_an_observer_still_works(self, aiohttp_server):
        base_url = await _serve(aiohttp_server, _time_app())
        client = Sep2Client(base_url)
        await client.get("/tm", Time)
        await client.close()  # must not raise


# -- Socket forwarding --------------------------------------------------------


PAIR = SocketPair(
    local=Address(ip="192.0.2.10", port=54321),
    remote=Address(ip="192.0.2.20", port=443),
)


class TestDispatchConnect:
    def test_an_established_socket_reaches_the_observer(self):
        observer = RecordingObserver()
        client = Sep2Client("https://example.test", connection_observer=observer)

        client._dispatch_connect(PAIR)

        assert observer.calls == [("on_connect", PAIR)]

    def test_an_observer_attached_after_the_connector_exists_still_gets_sockets(self):
        """The connector binds the dispatch method once, at session creation;
        the observer lookup inside it happens per connection, so attaching an
        observer mid-session takes effect without a session reset."""
        client = Sep2Client("https://example.test")

        client._dispatch_connect(PAIR)  # no observer yet: must be a no-op

        observer = RecordingObserver()
        client.connection_observer = observer
        client._dispatch_connect(PAIR)

        assert observer.calls == [("on_connect", PAIR)]


# -- Address conversion -------------------------------------------------------


class TestAsAddress:
    def test_ipv4_tuple_converts(self):
        assert _as_address(("192.0.2.1", 502)) == Address(ip="192.0.2.1", port=502)

    def test_ipv6_four_tuple_converts(self):
        """getsockname on an IPv6 socket returns (host, port, flowinfo, scopeid)."""
        assert _as_address(("2001:db8::1", 443, 0, 0)) == Address(ip="2001:db8::1", port=443)

    def test_malformed_values_yield_none(self):
        """A Unix socket path or a junk value is not worth guessing at."""
        assert _as_address(None) is None
        assert _as_address("/var/run/socket") is None
        assert _as_address(("host-only",)) is None
        assert _as_address((502, "192.0.2.1")) is None


# -- Connector-side socket reporting ------------------------------------------


class _FakeTransport:
    """Just enough transport to answer get_extra_info for both socket ends."""

    def __init__(self, sockname: object, peername: object) -> None:
        self._info = {"sockname": sockname, "peername": peername}

    def get_extra_info(self, name: str) -> object:
        return self._info.get(name)


class TestReportSocket:
    async def test_both_ends_of_the_connection_are_reported(self):
        seen: list[SocketPair] = []
        connector = Ieee2030TCPConnector(on_connect=seen.append)
        try:
            connector._report_socket(_FakeTransport(("10.0.0.5", 54321), ("192.0.2.20", 443)))
        finally:
            await connector.close()

        assert seen == [
            SocketPair(
                local=Address(ip="10.0.0.5", port=54321),
                remote=Address(ip="192.0.2.20", port=443),
            )
        ]

    async def test_a_raising_observer_cannot_break_the_connection(self, caplog):
        """Connection telemetry must not fail the connection it describes.

        The first failure warns (an observer that never fires looks exactly
        like a client that never connects); repeats drop to debug so a
        persistently broken observer cannot flood the log.
        """

        def broken(_pair: SocketPair) -> None:
            raise RuntimeError("observer bug")

        connector = Ieee2030TCPConnector(on_connect=broken)
        transport = _FakeTransport(("10.0.0.5", 1), ("192.0.2.20", 2))
        try:
            with caplog.at_level(logging.DEBUG, logger="py20305.client.connector"):
                connector._report_socket(transport)  # must not raise
                connector._report_socket(transport)  # must not raise either
        finally:
            await connector.close()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
        assert len(warnings) == 1
        assert any("observer" in r.message.lower() for r in debugs)

    async def test_without_an_observer_reporting_is_a_no_op(self):
        connector = Ieee2030TCPConnector()
        try:
            connector._report_socket(_FakeTransport(("10.0.0.5", 1), ("192.0.2.20", 2)))
        finally:
            await connector.close()
