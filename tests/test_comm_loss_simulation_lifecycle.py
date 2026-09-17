"""Lifecycle safety and outage attribution for the comms-loss simulation.

Two properties that only fail in the field, so they are asserted here rather
than left to inspection: the window must never be left open, and an outage must
be attributed to whatever actually caused it.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305.client.comm_loss_simulation import (
    SIMULATED_FAILURE_MESSAGE,
    SimulatedTransportError,
)
from py20305.client.csip_client import CsipClient

_FAR_FUTURE = 1e12


def _client():
    return CsipClient("https://example.com", comms_loss_seconds=900)


def _injected() -> SimulatedTransportError:
    """The failure the gate raises."""
    return SimulatedTransportError(SIMULATED_FAILURE_MESSAGE)


def _genuine() -> OSError:
    """A real transport failure, indistinguishable to every handler."""
    return OSError("connection refused")


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

        with patch.object(
            client, "_clear_comm_loss_simulation_locked", new_callable=AsyncMock
        ) as clear:
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
        client._http._record_contact(reachable=False, error=_genuine())
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False, error=_injected())

        assert client._http.silence_is_simulated is False

        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)

        assert "simulated" not in report.call_args.kwargs["details"]

    async def test_an_in_flight_request_failing_for_real_stays_genuine(self):
        """The gate did not cause every failure that happens while it is armed.

        A request already talking to the server when the window opened is
        allowed to finish, and can fail on its own. Reading the arm state would
        file that genuine failure as injected; the failure's own identity is
        what settles it.
        """
        client = _client()
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False, error=_genuine())

        assert client._http.silence_is_simulated is False

    async def test_a_wrapped_injected_failure_is_still_recognised(self):
        """Retry wraps transport failures, so the gate's error arrives as a cause."""
        from py20305.client.errors import Sep2ConnectionError

        wrapped = Sep2ConnectionError("giving up after retries")
        wrapped.__cause__ = _injected()

        client = _client()
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False, error=wrapped)

        assert client._http.silence_is_simulated is True

    async def test_a_silence_entirely_behind_the_gate_is_marked(self):
        client = _client()
        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False, error=_injected())
        client._http._record_contact(reachable=False, error=_injected())

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
        client._http._record_contact(reachable=False, error=_injected())
        assert client._http.silence_is_simulated is True

        client._http.disarm_comm_loss_simulation()
        client._http._record_contact(reachable=True)
        assert client._http.silence_is_simulated is None

        client._http._record_contact(reachable=False, error=_genuine())
        assert client._http.silence_is_simulated is False

    async def test_each_mode_gets_its_own_dedup_identity(self):
        """The store keeps the first entry's details for a repeated key.

        Sharing one key would let a simulated outage's marker stick to every
        genuine outage that followed it in the same session.
        """
        client = _client()

        client._http.comm_loss_simulation.arm(expires_at=_FAR_FUTURE)
        client._http._record_contact(reachable=False, error=_injected())
        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)
        simulated_key = report.call_args.kwargs["dedup_key"]

        client._comms_loss.active = False
        client._http.disarm_comm_loss_simulation()
        client._http._record_contact(reachable=True)
        client._http._record_contact(reachable=False, error=_genuine())
        with patch("py20305.diagnostics.report") as report:
            await self._enter(client)
        genuine_key = report.call_args.kwargs["dedup_key"]

        assert simulated_key != genuine_key


class TestExpiryHandOff:
    """The window between an expiry waking and its recovery finishing."""

    async def test_shutdown_waits_for_an_expiry_already_recovering(self):
        """The timer keeps its handle while it clears, so shutdown can wait.

        Clearing the handle on the way into recovery would make
        ``_stop_comm_loss_simulation_expiry`` find nothing to wait on and return
        at once -- leaving recovery talking to the head-end, and reopening the
        HTTP session shutdown had just closed. The sleeping timer is the easy
        case; this is the one that hides.
        """
        client = _client()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def blocking_recovery():
            entered.set()
            await release.wait()

        client._recover_after_simulation = blocking_recovery

        await client.simulate_comm_loss(0.05)
        expiry = client._comm_loss_simulation_expiry
        await asyncio.wait_for(entered.wait(), 2)

        # Still the handle shutdown will wait on, even mid-recovery.
        assert client._comm_loss_simulation_expiry is expiry

        await client._stop_comm_loss_simulation_expiry()

        # Recovery is definitively over, not still running in the background.
        assert expiry.done()
        release.set()

    async def test_a_stale_timer_cannot_clear_a_newer_window(self):
        """Generations, not just cancellation.

        A timer that has already woken and is waiting on the lifecycle lock
        cannot be cancelled out of existence by the next activation. Without a
        generation check it would acquire the lock afterwards and disarm a
        window it never owned.
        """
        client = _client()
        released = asyncio.Event()

        async def blocking_recovery():
            await released.wait()

        client._recover_after_simulation = blocking_recovery

        # Window one expires and parks inside recovery, holding the lock.
        await client.simulate_comm_loss(0.05)
        first = client._comm_loss_simulation_expiry
        await asyncio.sleep(0.12)

        # Window two starts once the first releases the lock.
        client._recover_after_simulation = AsyncMock()
        released.set()
        await first
        await client.simulate_comm_loss(60)

        try:
            assert client._http.comm_loss_simulation.active is True
            assert client._comm_loss_generation == 2
        finally:
            client._cancel_comm_loss_simulation_expiry()


class TestRollbackPhase:
    """A simulation that never started must not report an end-of-window phase."""

    async def test_rollback_returns_the_phase_to_inactive(self):
        """`recovering` is a phase this client never earned.

        Nothing would advance it afterwards either: only a clear marks
        `recovered`, and an operator has no reason to clear a simulation that
        failed to start.
        """
        notifications = AsyncMock()
        notifications.resume = MagicMock()
        client = _client()
        client._notification_server = notifications

        async def cancelled_suspend(**kwargs):
            raise RuntimeError("suspend failed")

        notifications.suspend = cancelled_suspend
        assert client.comm_loss_simulation_status()["phase"] == "inactive"

        with pytest.raises(RuntimeError):
            await client.simulate_comm_loss(60)

        status = client.comm_loss_simulation_status()
        assert status["phase"] == "inactive"
        assert status["active"] is False
        assert status["started"] is None


class TestClearWithoutCommsLossMode:
    """Clearing still has to catch up on what the window dropped."""

    async def test_clear_rediscovers_when_the_detector_never_fired(self):
        """Notifications were dropped for the whole window.

        A short window, or a client with comms-loss detection disabled, never
        enters the mode -- but a schedule or control change delivered only by
        notification during it is still missing, so the clear path has to
        re-poll regardless.
        """
        client = _client()
        assert client._comms_loss.active is False

        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as rediscover:
            rediscover.return_value = True
            await client.simulate_comm_loss(60)
            status = await client.clear_comm_loss_simulation()

        rediscover.assert_awaited_once()
        assert status["phase"] == "recovered"
