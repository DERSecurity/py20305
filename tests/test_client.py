"""Tests for the async Sep2Client.

Uses aiohttp.test_utils via pytest-aiohttp's ``aiohttp_server`` fixture: a
small ``aiohttp.web.Application`` boots on a random port per test, and
Sep2Client points at its base URL. Real HTTP roundtrip exercises the
client's session, header serialisation, and status handling -- mocking
at the request layer would skip those paths entirely. See
``docs/planning/AIOHTTP_TEST_UTILS_MIGRATION.md`` for the patterns
chosen (server-fixture vs client-fixture, request inspection by
list-appending handler, HTTP-not-HTTPS in tests).
"""

import pytest
from aiohttp import web

from py20305.client.errors import (
    Sep2NoContentError,
    Sep2ProtocolError,
    Sep2RateLimitError,
    Sep2RedirectError,
)
from py20305.client.http import Sep2Client
from py20305.models.sep.sep import EndDeviceList, Time
from py20305.xml.serialization import to_xml
from tests.conftest import make_time


async def _serve(aiohttp_server, app):
    """Boot ``app`` on a random port and return its base URL as a string."""
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/")


async def test_get_success(aiohttp_server):
    t = make_time(999, 3)
    xml = to_xml(t)

    async def handler(request):
        return web.Response(body=xml, content_type="application/sep+xml")

    app = web.Application()
    app.router.add_get("/tm", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        result = await client.get("/tm", Time)
    assert result.current_time.value == 999


async def test_get_404_raises_protocol_error(aiohttp_server):
    async def handler(request):
        return web.Response(status=404, body=b"not found")

    app = web.Application()
    app.router.add_get("/missing", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        with pytest.raises(Sep2ProtocolError) as exc_info:
            await client.get("/missing", Time)
    assert exc_info.value.status_code == 404


async def test_post_returns_location(aiohttp_server):
    async def handler(request):
        return web.Response(status=201, headers={"Location": "/mup/1"})

    app = web.Application()
    app.router.add_post("/mup", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        loc = await client.post("/mup", make_time())
    assert loc == "/mup/1"


async def test_put_returns_status(aiohttp_server):
    async def handler(request):
        return web.Response(status=204)

    app = web.Application()
    app.router.add_put("/res/1", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        status = await client.put("/res/1", make_time())
    assert status == 204


async def test_delete_returns_status(aiohttp_server):
    async def handler(request):
        return web.Response(status=200)

    app = web.Application()
    app.router.add_delete("/res/1", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        status = await client.delete("/res/1")
    assert status == 200


async def test_post_error_status(aiohttp_server):
    async def handler(request):
        return web.Response(status=500, body=b"error")

    app = web.Application()
    app.router.add_post("/fail", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        with pytest.raises(Sep2ProtocolError) as exc_info:
            await client.post("/fail", make_time())
    assert exc_info.value.status_code == 500


async def test_context_manager():
    client = Sep2Client("http://example.test")
    async with client:
        assert client._session is None or not client._session.closed


# --- csip_aus_mode tests ---


def test_csip_aus_mode_defaults_to_false():
    client = Sep2Client("http://example.test")
    assert client.csip_aus_mode is False


def test_csip_aus_mode_setter():
    client = Sep2Client("http://example.test")
    client.csip_aus_mode = True
    assert client.csip_aus_mode is True


async def test_post_includes_csipaus_when_mode_on(aiohttp_server):
    """POST body includes csipaus namespace when csip_aus_mode is True."""
    sent_bodies: list[bytes] = []

    async def handler(request):
        sent_bodies.append(await request.read())
        return web.Response(status=201, headers={"Location": "/mup/1"})

    app = web.Application()
    app.router.add_post("/mup", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        client.csip_aus_mode = True
        await client.post("/mup", make_time())

    assert len(sent_bodies) == 1
    assert b"csipaus" in sent_bodies[0]
    assert b"https://csipaus.org/ns" in sent_bodies[0]


async def test_post_excludes_csipaus_when_mode_off(aiohttp_server):
    """POST body excludes csipaus namespace when csip_aus_mode is False (default)."""
    sent_bodies: list[bytes] = []

    async def handler(request):
        sent_bodies.append(await request.read())
        return web.Response(status=201, headers={"Location": "/mup/1"})

    app = web.Application()
    app.router.add_post("/mup", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        await client.post("/mup", make_time())

    assert len(sent_bodies) == 1
    assert b"csipaus" not in sent_bodies[0]


async def test_put_includes_csipaus_when_mode_on(aiohttp_server):
    """PUT body includes csipaus namespace when csip_aus_mode is True."""
    sent_bodies: list[bytes] = []

    async def handler(request):
        sent_bodies.append(await request.read())
        return web.Response(status=204)

    app = web.Application()
    app.router.add_put("/res/1", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        client.csip_aus_mode = True
        await client.put("/res/1", make_time())

    assert len(sent_bodies) == 1
    assert b"csipaus" in sent_bodies[0]


async def test_get_with_body_returns_parsed_and_raw(aiohttp_server):
    """get_with_body returns both parsed model and raw bytes."""
    t = make_time(999, 3)
    xml = to_xml(t)

    async def handler(request):
        return web.Response(body=xml, content_type="application/sep+xml")

    app = web.Application()
    app.router.add_get("/tm", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        result, raw_body = await client.get_with_body("/tm", Time)

    assert result.current_time.value == 999
    assert isinstance(raw_body, bytes)
    assert b"999" in raw_body


# --- get_list 404 handling tests ---


async def test_get_list_404_returns_empty_list(aiohttp_server):
    """get_list returns [] when the list endpoint returns 404 (no resources).

    Captures the request's query params so the s=0&l=50 paging contract is
    still under test -- the old aioresponses URL match included the query
    string, so a regression in query construction would have caused a
    miss-match; a handler that only matches the path would silently accept
    a client that stopped sending paging params.
    """
    queries: list[dict[str, str]] = []

    async def handler(request):
        queries.append(dict(request.query))
        return web.Response(status=404, body=b"not found")

    app = web.Application()
    app.router.add_get("/derc", handler)
    base_url = await _serve(aiohttp_server, app)

    from py20305.models.sep.sep import DercontrolList

    async with Sep2Client(base_url) as client:
        result = await client.get_list("/derc", DercontrolList)

    assert result == []
    assert queries == [{"s": "0", "l": "50"}]


async def test_get_list_non_404_error_propagates(aiohttp_server):
    """get_list propagates non-404 errors normally.

    Captures the request's query params for the same paging-contract
    reason as ``test_get_list_404_returns_empty_list``.
    """
    queries: list[dict[str, str]] = []

    async def handler(request):
        queries.append(dict(request.query))
        return web.Response(status=500, body=b"server error")

    app = web.Application()
    app.router.add_get("/derc", handler)
    base_url = await _serve(aiohttp_server, app)

    from py20305.models.sep.sep import DercontrolList

    async with Sep2Client(base_url) as client:
        with pytest.raises(Sep2ProtocolError) as exc_info:
            await client.get_list("/derc", DercontrolList)
    assert exc_info.value.status_code == 500
    assert queries == [{"s": "0", "l": "50"}]


async def test_delete_recorded_in_live_traffic(aiohttp_server):
    """A subscription cancel (DELETE) appears in Live Traffic with its status."""
    from py20305.client.traffic_recorder import TrafficRecorder

    async def handler(request):
        return web.Response(status=200)

    app = web.Application()
    app.router.add_delete("/edev/1/sub/5", handler)
    base_url = await _serve(aiohttp_server, app)

    rec = TrafficRecorder()
    async with Sep2Client(base_url, traffic_recorder=rec) as client:
        await client.delete("/edev/1/sub/5")

    deletes = [e for e in rec.get_snapshot()["entries"] if e["method"] == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0]["url"] == "/edev/1/sub/5"
    assert deletes[0]["status"] == 200


async def test_post_response_recorded_in_live_traffic(aiohttp_server):
    """A subscribe POST records both the outbound request body and the response status."""
    from py20305.client.traffic_recorder import TrafficRecorder

    async def handler(request):
        return web.Response(status=201, headers={"Location": "/edev/1/sub/6"})

    app = web.Application()
    app.router.add_post("/edev/1/sub", handler)
    base_url = await _serve(aiohttp_server, app)

    rec = TrafficRecorder()
    async with Sep2Client(base_url, traffic_recorder=rec) as client:
        await client.post("/edev/1/sub", make_time())

    entries = rec.get_snapshot()["entries"]
    posts = [(e["direction"], e["status"]) for e in entries if e["method"] == "POST"]
    assert ("response", 201) in posts  # server's result status captured
    assert ("request", None) in posts  # outbound request body captured


async def test_write_error_response_recorded_in_live_traffic(aiohttp_server):
    """A failed write records the server's error status + body in Live Traffic."""
    from py20305.client.traffic_recorder import TrafficRecorder

    async def handler(request):
        return web.Response(status=500, body=b"boom")

    app = web.Application()
    app.router.add_put("/res/1", handler)
    base_url = await _serve(aiohttp_server, app)

    rec = TrafficRecorder()
    async with Sep2Client(base_url, traffic_recorder=rec) as client:
        with pytest.raises(Sep2ProtocolError):
            await client.put("/res/1", make_time())

    errs = [e for e in rec.get_snapshot()["entries"] if e["method"] == "PUT" and e["status"] == 500]
    assert len(errs) == 1
    assert "boom" in errs[0]["body"]


async def test_get_list_204_treated_as_empty(aiohttp_server):
    """A 204 No Content on the first page of a list is treated as an empty list,
    so refresh callers replace cached state instead of keeping it stale."""

    async def handler(request):
        return web.Response(status=204)

    app = web.Application()
    app.router.add_get("/edev", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        items = await client.get_list("/edev", EndDeviceList)
    assert items == []


# --- GET response-code range (CSIP [GEN.037]) ---


async def test_get_204_raises_no_content_error(aiohttp_server):
    """204 No Content -> Sep2NoContentError (a typed signal, subclass of
    Sep2ProtocolError with status 204 for backward compatibility)."""

    async def handler(request):
        return web.Response(status=204)

    app = web.Application()
    app.router.add_get("/derp/1/dderc", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        with pytest.raises(Sep2NoContentError) as info:
            await client.get("/derp/1/dderc", Time)
    assert isinstance(info.value, Sep2ProtocolError)  # backward-compatible
    assert info.value.status_code == 204


async def test_get_204_records_validated_contact(aiohttp_server):
    """A 204 is a successful, validated contact: it validates the chain and
    revives server_alive instead of leaving it stale-false."""

    async def handler(request):
        return web.Response(status=204)

    app = web.Application()
    app.router.add_get("/derp/1/dderc", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        client._server_alive = False  # simulate a prior failed/unvalidated GET
        client._consecutive_failures = 2
        with pytest.raises(Sep2NoContentError):
            await client.get("/derp/1/dderc", Time)
        assert client.server_alive is True  # 204 marked the server validated/alive
        assert client._consecutive_failures == 0


async def test_get_with_body_204_records_validated_contact(aiohttp_server):
    """Same as get(): a 204 from get_with_body revives server_alive."""

    async def handler(request):
        return web.Response(status=204)

    app = web.Application()
    app.router.add_get("/derp/1/dderc", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        client._server_alive = False
        with pytest.raises(Sep2NoContentError):
            await client.get_with_body("/derp/1/dderc", Time)
        assert client.server_alive is True


def test_no_content_error_is_protocol_error_with_204():
    err = Sep2NoContentError("nope")
    assert isinstance(err, Sep2ProtocolError)
    assert err.status_code == 204


@pytest.mark.parametrize("code", [301, 302, 307, 308])
def test_redirect_codes_map_to_redirect_error(code):
    resp = type("R", (), {"status": code, "headers": {"Location": "/new"}})()
    with pytest.raises(Sep2RedirectError) as info:
        Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/old")
    assert info.value.location == "/new"
    # carries the actual redirect status so logs/diagnostics name the right code
    assert info.value.status_code == code


def test_429_maps_to_rate_limit_error():
    resp = type("R", (), {"status": 429, "headers": {"Retry-After": "12"}})()
    with pytest.raises(Sep2RateLimitError) as info:
        Sep2Client._check_redirect_or_rate_limit(resp, "GET", "/x")
    assert info.value.retry_after == 12


async def test_get_list_later_page_204_is_protocol_error_not_no_content(aiohttp_server):
    """A 204 mid-pagination (later page) is a data-integrity error -- surfaced as
    a plain Sep2ProtocolError, NOT a benign Sep2NoContentError."""
    edl_xml = to_xml(EndDeviceList(**{"all": 100, "results": 50}))

    async def handler(request):
        if request.query.get("s") == "0":
            return web.Response(body=edl_xml, content_type="application/sep+xml")
        return web.Response(status=204)  # page 2 -> 204

    app = web.Application()
    app.router.add_get("/edev", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url) as client:
        with pytest.raises(Sep2ProtocolError) as info:
            await client.get_list("/edev", EndDeviceList)
    assert not isinstance(info.value, Sep2NoContentError)  # surfaces, not benign
    assert info.value.status_code == 204
