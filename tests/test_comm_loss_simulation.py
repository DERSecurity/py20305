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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from py20305.client.comm_loss_simulation import SIMULATED_FAILURE_MESSAGE, CommLossSimulation
from py20305.client.csip_client import CsipClient
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


class TestSimulationCoordination:
    """CsipClient's coordination of the gate, notifications and recovery."""

    @staticmethod
    def _client(**kwargs):
        return CsipClient("https://example.com", comms_loss_seconds=900, **kwargs)

    @staticmethod
    def _in_comms_loss(client):
        """Put the client in loss-of-communications mode, ready to recover."""
        client._comms_loss.active = True
        client._own_lfdi = "a" * 40

    async def test_simulate_suspends_notifications_before_arming(self):
        """Inbound is shut first.

        Closing the outbound gate first would leave a window in which a
        notification could still arrive and apply a control, which is the very
        thing the operator is testing the absence of.
        """
        notifications = AsyncMock()
        notifications.suspend = AsyncMock(return_value=True)
        client = self._client()
        client._notification_server = notifications

        order = []
        notifications.suspend.side_effect = lambda **kw: order.append("suspend") or True
        original_arm = client._http.arm_comm_loss_simulation

        async def tracked_arm(**kwargs):
            order.append("arm")
            return await original_arm(**kwargs)

        client._http.arm_comm_loss_simulation = tracked_arm

        status = await client.simulate_comm_loss(60)
        try:
            assert order == ["suspend", "arm"]
            assert status["active"] is True
            assert status["phase"] == "active"
            assert client._http.comm_loss_simulation.active is True
        finally:
            client._cancel_comm_loss_simulation_expiry()

    async def test_simulate_reports_a_drain_that_did_not_finish(self):
        """An unclean isolation is reported rather than hidden."""
        notifications = AsyncMock()
        notifications.suspend = AsyncMock(return_value=False)
        client = self._client()
        client._notification_server = notifications

        status = await client.simulate_comm_loss(60)
        try:
            assert status["notifications_drained"] is False
            assert status["requests_drained"] is True
            # The window is open regardless.
            assert status["active"] is True
        finally:
            client._cancel_comm_loss_simulation_expiry()

    async def test_clear_disarms_resumes_and_recovers(self):
        notifications = AsyncMock()
        notifications.suspend = AsyncMock(return_value=True)
        notifications.resume = MagicMock()
        client = self._client()
        client._notification_server = notifications

        await client.simulate_comm_loss(60)
        self._in_comms_loss(client)

        with (
            patch.object(client, "_server_lists_end_device", new_callable=AsyncMock) as listed,
            patch.object(client, "register_end_device", new_callable=AsyncMock),
            patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as rediscover,
        ):
            listed.return_value = True
            rediscover.return_value = True
            status = await client.clear_comm_loss_simulation()

        assert client._http.comm_loss_simulation.active is False
        notifications.resume.assert_called_once()
        assert client._comms_loss.active is False
        assert status["phase"] == "recovered"

    async def test_concurrent_recovery_registers_at_most_once(self):
        """The reason recovery is serialized.

        Recovery checks for, and may re-POST, the client's own EndDevice before
        it reaches the rediscovery lock. Two overlapping runs would send a live
        head-end two registrations for the same device.
        """
        client = self._client()
        self._in_comms_loss(client)

        with (
            patch.object(client, "_server_lists_end_device", new_callable=AsyncMock) as listed,
            patch.object(client, "register_end_device", new_callable=AsyncMock) as register,
            patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as rediscover,
        ):
            # Server no longer lists us, so recovery reregisters -- the path
            # that must not run twice. The lookup yields to the event loop, as
            # a real round-trip would: without a suspension point here the two
            # calls never interleave and the test would pass with no lock at
            # all.
            async def slow_lookup(_lfdi):
                await asyncio.sleep(0.05)
                return False

            listed.side_effect = slow_lookup
            rediscover.return_value = True

            await asyncio.gather(
                client.recover_from_comms_loss_now(),
                client.recover_from_comms_loss_now(),
            )

        assert register.await_count == 1

    async def test_clear_reports_a_recovery_that_did_not_complete(self):
        """Staying in comms-loss mode is not 'recovered'.

        Rediscovery can fail against a live head-end, and the client then stays
        in loss-of-communications mode and retries on the next tick. An operator
        watching the UI has to be able to tell that apart from a clean restore.
        """
        client = self._client()
        self._in_comms_loss(client)
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)

        with (
            patch.object(client, "_server_lists_end_device", new_callable=AsyncMock) as listed,
            patch.object(client, "register_end_device", new_callable=AsyncMock),
            patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as rediscover,
        ):
            listed.return_value = True
            # Rediscovery did not complete, so _recover_from_comms_loss returns
            # early and leaves the mode active.
            rediscover.return_value = False
            status = await client.clear_comm_loss_simulation()

        assert client._comms_loss.active is True
        assert status["phase"] == "recovery_failed"
        assert "rediscovery" in status["detail"]

    async def test_window_expires_without_operator_action(self):
        """A forgotten simulation must not strand a live site."""
        client = self._client()

        with patch.object(client, "clear_comm_loss_simulation", new_callable=AsyncMock) as clear:
            await client.simulate_comm_loss(0.05)
            assert client._http.comm_loss_simulation.active is True
            await asyncio.sleep(0.15)

        clear.assert_awaited_once()

    async def test_clearing_cancels_a_pending_expiry(self):
        """An expiry left running would fire into the next window."""
        client = self._client()
        await client.simulate_comm_loss(60)
        assert client._comm_loss_simulation_expiry is not None

        with (
            patch.object(client, "_server_lists_end_device", new_callable=AsyncMock),
            patch.object(client, "trigger_rediscovery", new_callable=AsyncMock),
        ):
            await client.clear_comm_loss_simulation()

        assert client._comm_loss_simulation_expiry is None


class TestSimulationDiagnostics:
    """The simulated marker on comms-loss entry."""

    @staticmethod
    async def _enter(client):
        with patch.object(client._event_processor, "enter_comms_loss", new_callable=AsyncMock):
            await client._enter_comms_loss(900)

    async def test_entry_caused_by_a_simulation_is_marked(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)

        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)

        details = report.call_args.kwargs["details"]
        assert details["simulated"] is True
        assert "simulation" in report.call_args.args[1]

    async def test_a_genuine_outage_is_not_marked(self):
        """An unmarked record is the operator's proof the outage was real.

        The marker goes on records causally produced by the injected failure,
        not on everything raised while a window happens to be open -- otherwise
        a real fault occurring during a simulation would be relabelled a test
        artifact and hidden.
        """
        client = CsipClient("https://example.com", comms_loss_seconds=900)

        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)

        assert "simulated" not in report.call_args.kwargs["details"]
        assert "simulation" not in report.call_args.args[1]
