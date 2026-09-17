"""Tests for the operator-triggered comms-loss simulation gate.

The failure accounting is asserted per method family rather than in aggregate,
and that is the point of this file. Connectivity is recorded in three different
places depending on the method -- typed GET records inline in its own except
ladder, the raw probes convert transport errors to an in-band return value, and
only non-GET traffic funnels through ``_send_tracked``. A gate placed where the
failure escapes one of those paths still passes a test that only checks "some
request failed", which is exactly how two earlier designs for this feature went
wrong. Each family therefore gets its own assertion.
"""

import asyncio

import pytest
from aiohttp import web

from py20305.client.comm_loss_simulation import SIMULATED_FAILURE_MESSAGE, CommLossSimulation
from py20305.client.errors import Sep2ConnectionError, Sep2ProtocolError
from py20305.client.http import Sep2Client
from py20305.client.retry import RetryPolicy
from py20305.models.sep.sep import Time
from py20305.xml.serialization import to_xml
from tests.conftest import make_time

# Retries would otherwise turn every gated call into several seconds of backoff.
_FAST_RETRY = RetryPolicy(max_transient=1, max_tls=1, base_delay=0.0)

_FAR_FUTURE = 1e12


async def _serve(aiohttp_server, app):
    """Boot ``app`` on a random port and return its base URL as a string."""
    server = await aiohttp_server(app)
    return str(server.make_url("")).rstrip("/")


def _time_app(path="/tm", delay=0.0):
    """An app serving a valid Time resource, optionally slowly."""
    xml = to_xml(make_time(999, 3))

    async def handler(request):
        if delay:
            await asyncio.sleep(delay)
        return web.Response(body=xml, content_type="application/sep+xml")

    app = web.Application()
    app.router.add_get(path, handler)
    return app


async def _arm(client):
    """Arm the gate, asserting the drain completed."""
    drained = await client.arm_comm_loss_simulation(expires_at=_FAR_FUTURE, drain_timeout=5.0)
    assert drained is True


async def test_gated_get_alone_marks_server_unreachable(aiohttp_server):
    """A GET is enough on its own.

    GET records connectivity inline rather than through ``_send_tracked``, so a
    gate that only covered the non-GET path would leave a polling-only client
    reporting a healthy link forever and never enter comms-loss mode.
    """
    base_url = await _serve(aiohttp_server, _time_app())

    async with Sep2Client(base_url, retry=_FAST_RETRY) as client:
        await client.get("/tm", Time)
        assert client.server_alive is True
        contact_before = client.last_contact_epoch
        assert contact_before is not None

        await _arm(client)
        with pytest.raises(Sep2ConnectionError):
            await client.get("/tm", Time)

        assert client.server_alive is False
        assert client.last_contact_epoch == contact_before


async def test_gated_raw_probes_return_status_zero(aiohttp_server):
    """The raw probes keep their in-band contract instead of raising.

    ``get_raw`` and ``request_raw`` report transport failures to their caller as
    ``{"status_code": 0, ...}``. Raising at them instead would break every
    caller that expects a dict back.
    """
    base_url = await _serve(aiohttp_server, _time_app())

    async with Sep2Client(base_url, retry=_FAST_RETRY) as client:
        await _arm(client)

        raw = await client.get_raw(f"{base_url}/tm")
        assert raw["status_code"] == 0
        assert SIMULATED_FAILURE_MESSAGE in raw["error"]

        req = await client.request_raw("GET", f"{base_url}/tm")
        assert req["status_code"] == 0
        assert SIMULATED_FAILURE_MESSAGE in req["error"]

        assert client.server_alive is False


@pytest.mark.parametrize("method", ["post", "put", "delete"])
async def test_gated_non_get_records_unreachable(aiohttp_server, method):
    """Non-GET traffic surfaces as Sep2ConnectionError and is recorded.

    These funnel through ``_send_tracked``, the third of the three accounting
    paths, and telemetry PUT/POST is what keeps a subscription-suppressed client
    looking alive -- so it has to fail too, not just the polls.
    """
    app = web.Application()

    async def handler(request):
        return web.Response(status=204)

    app.router.add_route("*", "/res", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url, retry=_FAST_RETRY) as client:
        await _arm(client)

        with pytest.raises(Sep2ConnectionError):
            if method == "post":
                await client.post("/res", make_time())
            elif method == "put":
                await client.put("/res", make_time())
            else:
                await client.delete("/res")

        assert client.server_alive is False


async def test_disarm_restores_traffic_on_the_next_request(aiohttp_server):
    """Clearing the gate lets the link recover the ordinary way.

    Disarming does not assert connectivity itself: ``server_alive`` comes back
    because a request actually reached the server, not because the simulation
    said so.
    """
    base_url = await _serve(aiohttp_server, _time_app())

    async with Sep2Client(base_url, retry=_FAST_RETRY) as client:
        await _arm(client)
        with pytest.raises(Sep2ConnectionError):
            await client.get("/tm", Time)
        assert client.server_alive is False

        client.disarm_comm_loss_simulation()

        result = await client.get("/tm", Time)
        assert result.current_time.value == 999
        assert client.server_alive is True
        assert client.last_contact_epoch is not None


async def test_activation_waits_for_an_in_flight_request(aiohttp_server):
    """Arming drains requests already talking to the server.

    Without the wait, a request already awaiting aiohttp completes after the
    window opens and refreshes ``last_contact_epoch``, pushing back the moment
    the detector would fire while the operator is being told the link is down.
    """
    base_url = await _serve(aiohttp_server, _time_app(delay=0.3))

    async with Sep2Client(base_url, retry=_FAST_RETRY) as client:
        pending = asyncio.create_task(client.get("/tm", Time))
        while client.comm_loss_simulation.in_flight == 0:
            await asyncio.sleep(0.01)

        await _arm(client)

        # The drain returned only once the in-flight request had finished.
        assert client.comm_loss_simulation.in_flight == 0
        assert pending.done()
        assert (await pending).current_time.value == 999

        # And nothing new gets out afterwards.
        contact_after_drain = client.last_contact_epoch
        with pytest.raises(Sep2ConnectionError):
            await client.get("/tm", Time)
        assert client.last_contact_epoch == contact_after_drain


async def test_activation_reports_a_drain_that_did_not_finish(aiohttp_server):
    """A drain that times out says so rather than claiming a clean window."""
    base_url = await _serve(aiohttp_server, _time_app(delay=0.5))

    async with Sep2Client(base_url, retry=_FAST_RETRY) as client:
        pending = asyncio.create_task(client.get("/tm", Time))
        while client.comm_loss_simulation.in_flight == 0:
            await asyncio.sleep(0.01)

        drained = await client.arm_comm_loss_simulation(
            expires_at=_FAR_FUTURE, drain_timeout=0.01
        )
        assert drained is False
        # Armed regardless: the gate closes first, then the wait happens.
        assert client.comm_loss_simulation.active is True

        await pending


async def test_status_reports_the_phase_lifecycle():
    """Phase walks active -> recovering -> recovered.

    Clearing the gate is not the same event as leaving comms-loss mode, so
    ``recovering`` has to be representable; a schema of active/started/expires
    alone cannot express it.
    """
    sim = CommLossSimulation()
    assert sim.status()["phase"] == "inactive"

    sim.arm(expires_at=_FAR_FUTURE)
    status = sim.status()
    assert status["active"] is True
    assert status["phase"] == "active"
    assert status["expires"] == _FAR_FUTURE
    assert status["started"] is not None

    sim.disarm()
    status = sim.status()
    assert status["active"] is False
    assert status["phase"] == "recovering"
    assert status["expires"] is None

    sim.note_recovered()
    assert sim.status()["phase"] == "recovered"


async def test_status_keeps_the_reason_when_recovery_fails():
    """A failed recovery is distinguishable from a completed one, with detail."""
    sim = CommLossSimulation()
    sim.arm(expires_at=_FAR_FUTURE)
    sim.disarm()
    sim.note_recovery_failed("rediscovery did not complete")

    status = sim.status()
    assert status["phase"] == "recovery_failed"
    assert status["detail"] == "rediscovery did not complete"


async def test_tracking_releases_when_the_request_itself_fails(aiohttp_server):
    """A failed request must not leave the in-flight count stuck.

    The real context manager raises out of ``__aenter__``, which never reaches
    ``__aexit__`` -- so releasing only there would leave the count above zero
    and hang the next activation's drain.
    """

    async def handler(request):
        raise web.HTTPInternalServerError

    app = web.Application()
    app.router.add_get("/boom", handler)
    base_url = await _serve(aiohttp_server, app)

    async with Sep2Client(base_url, retry=_FAST_RETRY) as client:
        with pytest.raises(Sep2ProtocolError):
            await client.get("/boom", Time)
        assert client.comm_loss_simulation.in_flight == 0

        # The drain therefore still completes promptly.
        drained = await client.arm_comm_loss_simulation(expires_at=_FAR_FUTURE, drain_timeout=1.0)
        assert drained is True
