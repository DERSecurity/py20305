"""Lifecycle safety and outage attribution for the comms-loss simulation.

Two properties that only fail in the field, so they are asserted here rather
than left to inspection: the window must never be left open, and an outage must
be attributed to whatever actually caused it.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305.client.csip_client import CsipClient

_FAR_FUTURE = 1e12


def _client():
    return CsipClient("https://example.com", comms_loss_seconds=900)


class TestSimulationLifecycleSafety:
    """The window must never be left open, nor cleared by a stale timer."""

    @pytest.mark.parametrize("bad", [float("inf"), float("nan"), 0, -5])
    async def test_a_window_that_could_never_close_is_refused(self, bad):
        """An infinite sleep would defeat the only guarantee this makes."""
        client = _client()

        with pytest.raises(ValueError):
            await client.simulate_comm_loss(bad)

        # Refused before anything was suspended, so the client is untouched.
        assert client._http.comm_loss_simulation.active is False
        assert client._comm_loss_simulation_expiry is None

    async def test_a_failure_while_arming_rolls_back(self):
        """A half-isolated client with no timer is the outcome to avoid.

        Both gates close before their drains await, so a failure part-way
        through would otherwise leave traffic suppressed and notifications
        dropped with nothing scheduled to restore them.
        """
        notifications = AsyncMock()
        notifications.suspend = AsyncMock(return_value=True)
        notifications.resume = MagicMock()
        client = _client()
        client._notification_server = notifications

        async def failing_arm(**kwargs):
            client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
            raise RuntimeError("drain blew up")

        client._http.arm_comm_loss_simulation = failing_arm

        with pytest.raises(RuntimeError):
            await client.simulate_comm_loss(60)

        assert client._http.comm_loss_simulation.active is False
        notifications.resume.assert_called_once()
        assert client._comm_loss_simulation_expiry is None

    async def test_cancellation_while_arming_rolls_back(self):
        """The same guarantee under cancellation, not just exceptions."""
        notifications = AsyncMock()
        notifications.resume = MagicMock()
        client = _client()
        client._notification_server = notifications

        async def cancelled_suspend(**kwargs):
            raise asyncio.CancelledError

        notifications.suspend = cancelled_suspend

        with pytest.raises(asyncio.CancelledError):
            await client.simulate_comm_loss(60)

        assert client._http.comm_loss_simulation.active is False
        notifications.resume.assert_called_once()

    async def test_renewing_cancels_the_previous_timer_before_draining(self):
        """A previous window's timer must not clear the one replacing it.

        Renewed near its deadline, the old task would otherwise fire during the
        new activation's drains and disarm the gate just armed.
        """
        client = _client()
        await client.simulate_comm_loss(60)
        first = client._comm_loss_simulation_expiry

        await client.simulate_comm_loss(60)
        second = client._comm_loss_simulation_expiry

        try:
            assert first is not second
            # Let the loop process the cancellation it was asked for.
            await asyncio.sleep(0)
            assert first.cancelled() or first.done()
            assert client._http.comm_loss_simulation.active is True
        finally:
            client._cancel_comm_loss_simulation_expiry()

    async def test_expiry_honors_the_deadline_it_reported(self):
        """The timer starts after the drains, so it sleeps to the deadline.

        Sleeping the full requested duration instead would hold the window open
        past the ``expires`` value already handed to the caller, by however long
        draining took.
        """
        client = _client()

        async def slow_arm(*, expires_at, drain_timeout):
            await asyncio.sleep(0.15)
            client._http.comm_loss_simulation.arm(expires_at=expires_at)
            return True

        client._http.arm_comm_loss_simulation = slow_arm

        with patch.object(client, "clear_comm_loss_simulation", new_callable=AsyncMock) as clear:
            status = await client.simulate_comm_loss(0.2)
            # Most of the window went on the drain; only the remainder is left.
            assert status["expires"] - time.time() < 0.1
            await asyncio.sleep(0.12)

        clear.assert_awaited_once()

    async def test_shutdown_cancels_a_running_expiry(self):
        """An expiry outliving shutdown would reopen a closed HTTP session.

        It clears through recovery, which re-polls the server -- against a
        notification server and scheduler that have already stopped.
        """
        client = _client()
        await client.simulate_comm_loss(60)
        expiry = client._comm_loss_simulation_expiry
        assert expiry is not None

        await client.shutdown()

        assert client._comm_loss_simulation_expiry is None
        assert expiry.cancelled() or expiry.done()


class TestSimulationAttribution:
    """Which outages get marked simulated, and which must not."""

    @staticmethod
    async def _enter(client):
        with patch.object(client._event_processor, "enter_comms_loss", new_callable=AsyncMock):
            await client._enter_comms_loss(900)

    async def test_a_link_already_down_when_armed_stays_genuine(self):
        """Attribution is causal, not "a window was open at the time".

        An operator arming a simulation against an already-broken link would
        otherwise get that genuine outage filed as a test artifact -- exactly
        the misreading the marker exists to prevent.
        """
        client = _client()
        # The link fails for real first.
        client._http._record_contact(reachable=False)
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False)

        assert client._http.silence_is_simulated is False

        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)

        assert "simulated" not in report.call_args.kwargs["details"]

    async def test_a_silence_entirely_behind_the_gate_is_marked(self):
        client = _client()
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False)
        client._http._record_contact(reachable=False)

        assert client._http.silence_is_simulated is True

        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)

        assert report.call_args.kwargs["details"]["simulated"] is True

    async def test_contact_starts_a_fresh_attribution(self):
        """Reaching the server ends the run of silence.

        Without the reset, a simulation earlier in the session would colour
        every later outage.
        """
        client = _client()
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False)
        assert client._http.silence_is_simulated is True

        client._http.disarm_comm_loss_simulation()
        client._http._record_contact(reachable=True)
        assert client._http.silence_is_simulated is None

        client._http._record_contact(reachable=False)
        assert client._http.silence_is_simulated is False

    async def test_each_mode_gets_its_own_dedup_identity(self):
        """The store keeps the first entry's details for a repeated key.

        Sharing one key would let a simulated outage's marker stick to every
        genuine outage that followed it in the same session.
        """
        client = _client()

        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False)
        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)
        simulated_key = report.call_args.kwargs["dedup_key"]

        client._comms_loss.active = False
        client._http.disarm_comm_loss_simulation()
        client._http._record_contact(reachable=True)
        client._http._record_contact(reachable=False)
        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)
        genuine_key = report.call_args.kwargs["dedup_key"]

        assert simulated_key != genuine_key
