"""Integration tests for subscription system wiring."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305.client.csip_client import (
    _SERVER_RESTART_DETECTION_THRESHOLD,
    CsipClient,
)
from py20305.client.state import EndDeviceState
from py20305.models.sep.sep import Notification
from py20305.subscription.manager import SubscriptionManager
from py20305.subscription.notification_server import NotificationServer


@pytest.fixture
def mock_http() -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value="/edev/1/sub/1")
    client.delete = AsyncMock(return_value=200)
    client.close = AsyncMock()
    return client


@pytest.fixture
def sub_manager(mock_http: AsyncMock) -> SubscriptionManager:
    return SubscriptionManager(
        client=mock_http,
        notification_uri="https://agg:10443/notify",
    )


@pytest.fixture
def notification_server() -> NotificationServer:
    return NotificationServer(host="127.0.0.1", port=0, tls=None)


@pytest.fixture
def client(sub_manager: SubscriptionManager, notification_server: NotificationServer) -> CsipClient:
    c = CsipClient(
        "https://example.com",
        subscription_manager=sub_manager,
        notification_server=notification_server,
        # The periodic SubscriptionList reconcile is a separate mechanism with
        # its own tests, and leaving it armed makes this file's assertions
        # depend on task-scheduling order: rediscovery re-arms the poll, and
        # whether it gets a slot before rediscovery returns differs across
        # Python versions. When it does run, the mocked transport reports an
        # empty SubscriptionList, so it re-POSTs subscriptions that are in fact
        # present and any "did not re-POST" assertion here fails for a reason
        # that has nothing to do with what it is testing.
        reconcile_enabled=False,
    )
    return c


def _make_edev_state(
    *,
    sub_list_href: str | None = None,
    fsa_list_link_href: str | None = None,
    fsa_list_subscribable: bool = False,
    derp_list_subscribable: bool = False,
) -> EndDeviceState:
    """Create a minimal EndDeviceState with optional subscription/FSA links."""
    device = MagicMock()
    device.function_set_assignments_list_link = None
    device.subscription_list_link = None
    if fsa_list_link_href:
        fsa_link = MagicMock()
        fsa_link.href = fsa_list_link_href
        device.function_set_assignments_list_link = fsa_link
    if sub_list_href:
        sub_link = MagicMock()
        sub_link.href = sub_list_href
        device.subscription_list_link = sub_link

    state = EndDeviceState(
        device=device,
        href="/edev/1",
        lfdi=b"\xab\xcd",
        subscription_list_href=sub_list_href,
        fsa_list_subscribable=fsa_list_subscribable,
        derp_list_subscribable=derp_list_subscribable,
    )
    return state


class TestAutoSubscribe:
    @pytest.mark.asyncio
    async def test_auto_subscribe_after_connect(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """connect() should auto-subscribe when subscription_list_href is present."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        assert len(sub_manager.active_subscriptions) == 1
        assert sub_manager.active_subscriptions[0].subscribed_resource == "/edev/1/fsa"

    @pytest.mark.asyncio
    async def test_no_subscribe_without_sub_list_href(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """No subscriptions created when EndDevice lacks SubscriptionListLink."""
        edev_state = _make_edev_state(fsa_list_link_href="/edev/1/fsa")
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        assert len(sub_manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_no_subscribe_without_manager(self):
        """CsipClient without subscription_manager skips auto-subscribe."""
        c = CsipClient("https://example.com")
        with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
            await c.connect()
        # No error raised


class TestHandleNotification:
    @pytest.mark.asyncio
    async def test_status_0_triggers_poll_for_control_resource(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """Status 0 on a control resource should record notification and trigger poll."""
        notification = MagicMock(spec=Notification)
        notification.status = 0
        notification.subscribed_resource = "/edev/1/fsa/1/derp/1/derc"
        notification.subscription_uri = "/edev/1/sub/1"

        with patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_poll:
            await client._handle_notification(notification)
            mock_poll.assert_awaited_once()

        assert len(sub_manager.notifications) == 1
        assert sub_manager.notifications[0].subscribed_resource == "/edev/1/fsa/1/derp/1/derc"

    @pytest.mark.asyncio
    async def test_control_notification_records_ancestry_before_targeted_fetch(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """The DERControlList targeted fetch must record parent ancestry BEFORE it
        awaits.

        The server sends a sibling DERProgramList notification alongside the
        DERControlList one (its DERControlListLink.all count changed). Recording
        ancestry only after the fetch's awaits lets that sibling race ahead into
        a redundant full re-poll during the await window. Assert the parent path
        is already in the dedup cache by the time the fetch is awaited.
        """
        seen: dict[str, bool] = {}

        async def fake_targeted(resource: str) -> bool:
            seen["parent_recorded"] = "/FDA-SGA-TFA/derp" in sub_manager._dedup_cache
            return True

        notification = MagicMock(spec=Notification)
        notification.status = 0
        notification.subscribed_resource = "/FDA-SGA-TFA/derp/3/derc"
        notification.subscription_uri = "/edev/1/sub/1"

        with patch.object(client, "_do_poll_derp_targeted", side_effect=fake_targeted):
            await client._handle_notification(notification)

        assert seen["parent_recorded"] is True

    @pytest.mark.asyncio
    async def test_status_0_triggers_rediscovery_for_structural_resource(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """Status 0 on a structural resource (FSA) should trigger rediscovery."""
        notification = MagicMock(spec=Notification)
        notification.status = 0
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"

        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
            await client._handle_notification(notification)
            mock_redisc.assert_awaited_once()

        assert len(sub_manager.notifications) == 1
        assert sub_manager.notifications[0].subscribed_resource == "/edev/1/fsa"

    @pytest.mark.asyncio
    async def test_status_1_marks_cancelled(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Status 1 should mark subscription cancelled and clean notifications."""
        # Create a subscription first
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        notification = MagicMock(spec=Notification)
        notification.status = 1
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"

        await client._handle_notification(notification)
        assert sub_manager.active_subscriptions == []

    @pytest.mark.asyncio
    async def test_status_2_marks_cancelled_records_and_rediscovers(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Status 2 (resource moved, rule h): mark cancelled, record the new URI,
        and rediscover so we re-subscribe at the resource's new location."""
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        notification = MagicMock(spec=Notification)
        notification.status = 2
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"
        notification.new_resource_uri = "/edev/2/fsa"

        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
            await client._handle_notification(notification)
        assert sub_manager.active_subscriptions == []
        assert len(sub_manager.notifications) == 1
        assert sub_manager.notifications[0].new_resource_uri == "/edev/2/fsa"
        mock_redisc.assert_awaited_once()  # rediscovers to follow the move

    @pytest.mark.asyncio
    async def test_status_1_suppresses_resubscribe(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Status 1 (canceled, no info): mark cancelled AND suppress auto-resub so
        we honour the cancellation (poll-only) instead of re-subscribing."""
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        notification = MagicMock(spec=Notification)
        notification.status = 1
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"

        await client._handle_notification(notification)
        assert sub_manager.active_subscriptions == []
        assert sub_manager.resubscribe_suppressed("/edev/1/fsa") is True

    @pytest.mark.asyncio
    async def test_cancellation_after_change_is_not_deduped(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """A status=1 cancellation must survive dedup when a status=0 change for
        the same resource arrived within the dedup window.

        Conformance regression: the server sends a structural change
        (status=0) and then cancels the FSA subscription (status=1) for the
        same resource. Dedup keyed on resource alone swallowed the
        cancellation, so it was never honored -- the sub stayed active.
        """
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        change = MagicMock(spec=Notification)
        change.status = 0
        change.subscribed_resource = "/edev/1/fsa"
        change.subscription_uri = "/edev/1/sub/1"

        cancel = MagicMock(spec=Notification)
        cancel.status = 1
        cancel.subscribed_resource = "/edev/1/fsa"
        cancel.subscription_uri = "/edev/1/sub/1"

        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock):
            await client._handle_notification(change)  # status=0 populates dedup cache
            await client._handle_notification(cancel)  # status=1 must not be deduped

        assert sub_manager.active_subscriptions == []
        assert sub_manager.resubscribe_suppressed("/edev/1/fsa") is True

    @pytest.mark.asyncio
    async def test_status_3_marks_cancelled(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        notification = MagicMock(spec=Notification)
        notification.status = 3
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"

        await client._handle_notification(notification)
        assert sub_manager.active_subscriptions == []

    @pytest.mark.asyncio
    async def test_status_4_marks_cancelled(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        notification = MagicMock(spec=Notification)
        notification.status = 4
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"

        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock):
            await client._handle_notification(notification)
        assert sub_manager.active_subscriptions == []

    @pytest.mark.asyncio
    async def test_no_manager_is_noop(self):
        """CsipClient without manager ignores notifications."""
        c = CsipClient("https://example.com")
        notification = MagicMock(spec=Notification)
        notification.status = 0
        await c._handle_notification(notification)


class TestResubscribeSuppression:
    @pytest.mark.asyncio
    async def test_auto_subscribe_honours_then_clears_suppression(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Auto-subscribe skips a status=1-suppressed resource; clearing the
        suppression (as a reconnect does) re-enables it."""
        client._state.end_devices["/edev/1"] = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )

        with patch.object(sub_manager, "subscribe", new_callable=AsyncMock) as mock_sub:
            sub_manager.suppress_resubscribe("/edev/1/fsa")
            await client._auto_subscribe()
            assert "/edev/1/fsa" not in [c.args[1] for c in mock_sub.await_args_list]

            mock_sub.reset_mock()
            sub_manager.clear_resubscribe_suppression()
            await client._auto_subscribe()
            assert "/edev/1/fsa" in [c.args[1] for c in mock_sub.await_args_list]

    @pytest.mark.asyncio
    async def test_rediscovery_persists_resubscribe_suppression(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """A status=1 cancellation survives rediscovery: rediscovery
        is frequent and must not undo an honoured server cancellation."""
        sub_manager.suppress_resubscribe("/edev/1/fsa")
        assert sub_manager.resubscribe_suppressed("/edev/1/fsa") is True

        with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
            await client.trigger_rediscovery()

        assert sub_manager.resubscribe_suppressed("/edev/1/fsa") is True

    @pytest.mark.asyncio
    async def test_upstream_restart_clears_resubscribe_suppression(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """A genuine reconnect (upstream restart: sustained poll failures then
        recovery) clears suppression so every desired resource is re-attempted
        from a clean session."""
        sub_manager.suppress_resubscribe("/edev/1/fsa")
        client._poll_failure_count = _SERVER_RESTART_DETECTION_THRESHOLD

        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock):
            await client._note_successful_contact()

        assert sub_manager.resubscribe_suppressed("/edev/1/fsa") is False

    @pytest.mark.asyncio
    async def test_brief_blip_recovery_keeps_suppression(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """A brief blip (failures below the restart threshold) is not a reconnect:
        the server's subscription store is intact, so the honoured cancellation
        must survive."""
        sub_manager.suppress_resubscribe("/edev/1/fsa")
        client._poll_failure_count = 1  # below _SERVER_RESTART_DETECTION_THRESHOLD

        with patch.object(client, "_auto_subscribe", new_callable=AsyncMock):
            await client._note_successful_contact()

        assert sub_manager.resubscribe_suppressed("/edev/1/fsa") is True

    @pytest.mark.asyncio
    async def test_rediscovery_rearms_polls_when_discover_fails(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """A rediscovery whose discover() fails (server unreachable mid-reconnect)
        must still re-arm the poll scheduler.

        Rediscovery tears the scheduler down and rebuilds it empty before
        discover(). If discover() then raises and _start_polls() is skipped, the
        scheduler is left with no tasks -- crucially no connectivity heartbeat --
        so server_alive can never recover (telemetry keeps last_contact fresh but
        never chain-validates). The failure path must re-arm polls, and must not
        propagate the error out of trigger_rediscovery.
        """
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:
            mock_disc.side_effect = RuntimeError("server unreachable mid-rediscovery")
            with patch.object(client, "_start_polls") as mock_start_polls:
                await client.trigger_rediscovery()  # must not raise

        mock_start_polls.assert_called()


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_shutdown_cancels_subscriptions(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert len(sub_manager.active_subscriptions) == 1

        await client.shutdown()
        mock_http.delete.assert_called()

    @pytest.mark.asyncio
    async def test_shutdown_stops_notification_server(
        self, client: CsipClient, notification_server: NotificationServer
    ):
        await notification_server.start()
        assert notification_server.running

        await client.shutdown()
        assert not notification_server.running

    @pytest.mark.asyncio
    async def test_rediscovery_keeps_stable_subscriptions(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Rediscovery keeps subscriptions to unchanged resources WITHOUT re-POSTing.

        Regression for the rediscovery<->renewal loop: re-POSTing a kept
        subscription on every rediscovery makes some servers echo a
        current-state notification, which the aggregator classifies as a
        structural change and rediscovers again -- an infinite loop hammering
        the server. A rediscovery with unchanged subscription membership must
        issue zero subscription writes (no renewal POST, no DELETE).
        """
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        original_uri = sub_manager.active_subscriptions[0].subscription_uri

        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            mock_http.post.reset_mock()
            mock_http.delete.reset_mock()
            await client.trigger_rediscovery()

        # Subscription kept as-is -- no renewal POST, no DELETE
        assert len(sub_manager.active_subscriptions) == 1
        assert sub_manager.active_subscriptions[0].subscription_uri == original_uri
        mock_http.delete.assert_not_called()
        mock_http.post.assert_not_called()  # no re-POST -> no notification echo

    @pytest.mark.asyncio
    async def test_rediscovery_converges_when_server_echoes_subscription(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """End-to-end convergence: a server that echoes a structural notification
        on every subscription POST must NOT drive an unbounded rediscovery loop.

        This reproduces a failure seen in the field: before the fix, rediscovery re-POSTed
        kept subscriptions, the server echoed a current-state notification, that
        echo set the rediscovery-pending flag, and the loop re-ran forever. With
        the fix, rediscovery keeps subscriptions without re-POSTing, so no echo
        is provoked and it settles in a single pass.

        The asyncio.wait_for timeout means a regression FAILS fast here (the old
        behavior would spin until the timeout) instead of hanging the suite.
        """
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )

        discover_calls = 0

        async def populate_state(http, state, **_kw):
            nonlocal discover_calls
            discover_calls += 1
            state.end_devices["/edev/1"] = edev_state

        # Model the echoing server: any subscription (re)POST makes it echo a structural
        # notification, which (arriving mid-run) forces another rediscovery pass.
        async def echo_on_post(*_args, **_kwargs):
            client._rediscovery_pending = True
            return "/edev/1/sub/1"

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:
            mock_disc.side_effect = populate_state
            mock_http.post.reset_mock()
            mock_http.post.side_effect = echo_on_post
            await asyncio.wait_for(client.trigger_rediscovery(), timeout=5.0)

        # Converged: no re-POST -> echo never fired -> exactly one rediscovery pass
        mock_http.post.assert_not_called()
        assert discover_calls == 1

    @pytest.mark.asyncio
    async def test_rediscovery_loop_is_bounded_under_persistent_pending(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Safety net: if something keeps re-setting the rediscovery-pending flag
        every pass (e.g. a server echoing a structural notification on every write),
        the loop must stop after a bounded number of passes instead of spinning
        forever."""
        from py20305.client import csip_client as cc

        edev_state = _make_edev_state(fsa_list_link_href="/edev/1/fsa")
        discover_calls = 0

        async def populate_and_repend(http, state, **_kw):
            nonlocal discover_calls
            discover_calls += 1
            state.end_devices["/edev/1"] = edev_state
            # Simulate a structural echo arriving during every pass.
            client._rediscovery_pending = True

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:
            mock_disc.side_effect = populate_and_repend
            await asyncio.wait_for(client.trigger_rediscovery(), timeout=5.0)

        # Ran exactly the cap, then stopped -- not an unbounded loop.
        assert discover_calls == cc._MAX_REDISCOVERY_PASSES


class TestNotificationServerStart:
    @pytest.mark.asyncio
    async def test_connect_starts_notification_server(
        self, client: CsipClient, notification_server: NotificationServer
    ):
        """connect() should start the notification server before discovery."""
        with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
            await client.connect()
        assert notification_server.running

        await client.shutdown()


class TestRenewalLifecycle:
    @pytest.mark.asyncio
    async def test_renewal_task_created_after_connect(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """connect() should create a renewal task when active subscriptions exist."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        assert client._renewal_task is not None
        assert not client._renewal_task.done()

        await client.shutdown()

    @pytest.mark.asyncio
    async def test_no_renewal_task_without_subscriptions(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """connect() should not create a renewal task when no subscriptions exist."""
        edev_state = _make_edev_state(fsa_list_link_href="/edev/1/fsa")
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        assert client._renewal_task is None

    @pytest.mark.asyncio
    async def test_shutdown_cancels_renewal_task(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """shutdown() should cancel the renewal task."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        task = client._renewal_task
        assert task is not None

        await client.shutdown()
        assert task.done()

    @pytest.mark.asyncio
    async def test_rediscovery_restarts_renewal_task(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Rediscovery should cancel old renewal task and start a new one."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        old_task = client._renewal_task
        assert old_task is not None

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state2(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state2
            mock_http.post.return_value = "/edev/1/sub/2"
            await client.trigger_rediscovery()

        assert old_task.done()
        assert client._renewal_task is not None
        assert client._renewal_task is not old_task

        await client.shutdown()


class TestNotificationFiltering:
    @pytest.mark.asyncio
    async def test_duplicate_structural_notification_ignored(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """Second structural notification for same resource within dedup window is ignored.

        Dedup is only applied to structural paths (/edev, /fsa, /derp without /derc).
        /derc notifications intentionally bypass dedup because the targeted-fetch
        handler is cheap and burst notifications carry distinct state -- see
        test_control_notification_burst_bypasses_dedup for that contract.
        """
        notification = MagicMock(spec=Notification)
        notification.status = 0
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"

        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
            await client._handle_notification(notification)
            mock_redisc.assert_awaited_once()

            # Second notification for same resource should be deduped
            await client._handle_notification(notification)
            # Still only one call
            mock_redisc.assert_awaited_once()

        # Only one notification stored (second was dropped by dedup)
        assert len(sub_manager.notifications) == 1

    @pytest.mark.asyncio
    async def test_non_list_subscription_filters_list_notifications(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Non-list subscription should filter notifications for a different resource."""
        # Create a non-list subscription (e.g., DefaultDERControl)
        mock_http.post.return_value = "/edev/1/sub/99"
        await sub_manager.subscribe(
            "/edev/1/sub", "/edev/1/fsa/1/derp/1/dderc", "DefaultDERControl"
        )

        notification = MagicMock(spec=Notification)
        notification.status = 0
        # Notification comes for a different resource than what was subscribed
        notification.subscribed_resource = "/edev/1/fsa/1/derp/1/derc"
        notification.subscription_uri = "/edev/1/sub/99"

        with patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_poll:
            await client._handle_notification(notification)
            mock_poll.assert_not_awaited()

        assert len(sub_manager.notifications) == 0

    @pytest.mark.asyncio
    async def test_list_subscription_allows_notifications(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """List subscription should allow notifications for child resources."""
        # Create a list subscription
        mock_http.post.return_value = "/edev/1/sub/50"
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa/1/derp/1/derc", "DERControlList")

        notification = MagicMock(spec=Notification)
        notification.status = 0
        notification.subscribed_resource = "/edev/1/fsa/1/derp/1/derc/5"
        notification.subscription_uri = "/edev/1/sub/50"

        with patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_poll:
            await client._handle_notification(notification)
            mock_poll.assert_awaited_once()


class TestStartupValidation:
    @pytest.mark.asyncio
    async def test_restore_validate_subscribe_lifecycle(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Restored checkpoint -> validate removes stale -> auto_subscribe fills gaps."""
        # Simulate a restored checkpoint with a stale subscription
        sub_manager.restore_from_checkpoint(
            {
                "subscriptions": [
                    {
                        "subscription_uri": "/edev/1/sub/old",
                        "subscribed_resource": "/edev/1/fsa",
                        "notification_uri": "https://agg:10443/notify",
                        "resource_type": "FSAList",
                        "status": "active",
                        "created_at": 0.0,
                    }
                ]
            }
        )
        assert len(sub_manager.active_subscriptions) == 1

        # Make validation fail (stale sub), but new subscribe succeed
        mock_http.post.side_effect = [Exception("expired"), "/edev/1/sub/new"]

        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        # Stale sub removed, new sub created by auto_subscribe
        assert len(sub_manager.active_subscriptions) == 1
        assert sub_manager.active_subscriptions[0].subscription_uri == "/edev/1/sub/new"

        await client.shutdown()

    @pytest.mark.asyncio
    async def test_valid_restored_sub_not_duplicated(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Valid restored sub is kept; auto_subscribe dedup prevents double-POST."""
        sub_manager.restore_from_checkpoint(
            {
                "subscriptions": [
                    {
                        "subscription_uri": "/edev/1/sub/1",
                        "subscribed_resource": "/edev/1/fsa",
                        "notification_uri": "https://agg:10443/notify",
                        "resource_type": "FSAList",
                        "status": "active",
                        "created_at": 0.0,
                    }
                ]
            }
        )

        # Renewal succeeds
        mock_http.post.return_value = "/edev/1/sub/1"

        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        # Still just 1 subscription (dedup prevented double-POST)
        assert len(sub_manager.active_subscriptions) == 1
        assert sub_manager.active_subscriptions[0].subscription_uri == "/edev/1/sub/1"

        await client.shutdown()


class TestPollSuppression:
    @pytest.mark.asyncio
    async def test_connect_suppresses_polls_for_subscribed_resources(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """connect() should suppress poll keys covered by active subscriptions."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        # FSAList subscription -> suppresses "fsa" poll key
        assert "fsa" in client._scheduler.suppressed_keys

        await client.shutdown()

    @pytest.mark.asyncio
    async def test_cancellation_unsuppresses_polls(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Cancellation notification should unsuppress the poll key."""
        # Create an FSAList subscription
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        client._update_poll_suppression()
        assert "fsa" in client._scheduler.suppressed_keys

        # Simulate cancellation notification
        notification = MagicMock(spec=Notification)
        notification.status = 1
        notification.subscribed_resource = "/edev/1/fsa"
        notification.subscription_uri = "/edev/1/sub/1"

        await client._handle_notification(notification)

        assert "fsa" not in client._scheduler.suppressed_keys

    @pytest.mark.asyncio
    async def test_partial_fsa_cancellation_keeps_fsa_poll_active(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """One edev's FSA subscribed, another's cancelled: the fsa poll must stay
        active to observe the cancelled one, not be suppressed by the still-
        subscribed sibling."""
        client._state.end_devices["/edev/1"] = _make_edev_state(fsa_list_link_href="/edev/1/fsa")
        client._state.end_devices["/edev/2"] = _make_edev_state(fsa_list_link_href="/edev/2/fsa")
        # /edev/1/fsa actively subscribed; /edev/2/fsa is poll-only (cancelled).
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        client._update_poll_suppression()

        assert "fsa" not in client._scheduler.suppressed_keys

    @pytest.mark.asyncio
    async def test_all_fsa_subscribed_suppresses_fsa_poll(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """When every discovered FSA list is subscribed, the fsa poll is suppressed
        (nothing left to poll)."""
        client._state.end_devices["/edev/1"] = _make_edev_state(fsa_list_link_href="/edev/1/fsa")
        client._state.end_devices["/edev/2"] = _make_edev_state(fsa_list_link_href="/edev/2/fsa")
        # Distinct Locations so both subscriptions register (dict is keyed by URI).
        mock_http.post.side_effect = ["/edev/1/sub/1", "/edev/2/sub/1"]
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await sub_manager.subscribe("/edev/2/sub", "/edev/2/fsa", "FSAList")

        client._update_poll_suppression()

        assert "fsa" in client._scheduler.suppressed_keys

    @pytest.mark.asyncio
    async def test_fsa_poll_skips_subscribed_lists(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """_do_poll_fsa passes actively-subscribed FSA hrefs as skip_hrefs so the
        refresh doesn't re-fetch a subscribed resource (rule r)."""
        client._state.end_devices["/edev/1"] = _make_edev_state(fsa_list_link_href="/edev/1/fsa")
        client._state.end_devices["/edev/2"] = _make_edev_state(fsa_list_link_href="/edev/2/fsa")
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        with patch(
            "py20305.client.csip_client.refresh_function_set_assignments",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_refresh:
            await client._do_poll_fsa()

        skip = mock_refresh.await_args.kwargs["skip_hrefs"]
        assert "/edev/1/fsa" in skip
        assert "/edev/2/fsa" not in skip

    @pytest.mark.asyncio
    async def test_notification_fast_path_works_when_suppressed(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """Direct _do_poll_derp from notification still works when derp is suppressed."""
        client._scheduler.suppress("derp")

        notification = MagicMock(spec=Notification)
        notification.status = 0
        notification.subscribed_resource = "/edev/1/fsa/1/derp/1/derc"
        notification.subscription_uri = "/edev/1/sub/1"

        with patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_poll:
            await client._handle_notification(notification)
            mock_poll.assert_awaited_once()


class TestRecursiveChildSubscription:
    @pytest.mark.asyncio
    async def test_rediscovery_subscribes_new_program_controls(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Structural notification -> rediscovery -> auto_subscribe covers new children."""
        from py20305.client.state import DerProgramState

        # Step 1: connect with 1 program
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        derp_program = MagicMock()
        derp_program.dercontrol_list_link = MagicMock()
        derp_program.dercontrol_list_link.href = "/edev/1/fsa/1/derp/1/derc"
        derp_program.default_dercontrol_link = None

        derp_state = DerProgramState(program=derp_program, href="/edev/1/fsa/1/derp/1", primacy=0)
        derp_state.derc_list_subscribable = True
        derp_state.dderc_subscribable = False

        call_count = 0

        async def populate_state_phase1(http, state, **_kw):
            nonlocal call_count
            call_count += 1
            state.end_devices["/edev/1"] = edev_state
            state.der_programs["/edev/1/fsa/1/derp/1"] = derp_state

        # Use incrementing URIs for each subscribe POST
        sub_counter = 0

        def next_sub_uri(*args, **kwargs):
            nonlocal sub_counter
            sub_counter += 1
            return f"/edev/1/sub/{sub_counter}"

        mock_http.post.side_effect = next_sub_uri

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:
            mock_disc.side_effect = populate_state_phase1
            await client.connect()

        # Should have FSAList + DERControlList subscriptions
        initial_types = {s.resource_type for s in sub_manager.active_subscriptions}
        assert "FSAList" in initial_types
        assert "DERControlList" in initial_types
        initial_count = len(sub_manager.active_subscriptions)

        # Step 2: rediscovery discovers a second program
        derp_program2 = MagicMock()
        derp_program2.dercontrol_list_link = MagicMock()
        derp_program2.dercontrol_list_link.href = "/edev/1/fsa/1/derp/2/derc"
        derp_program2.default_dercontrol_link = None

        derp_state2 = DerProgramState(program=derp_program2, href="/edev/1/fsa/1/derp/2", primacy=0)
        derp_state2.derc_list_subscribable = True
        derp_state2.dderc_subscribable = False

        async def populate_state_phase2(http, state, **_kw):
            state.end_devices["/edev/1"] = edev_state
            state.der_programs["/edev/1/fsa/1/derp/1"] = derp_state
            state.der_programs["/edev/1/fsa/1/derp/2"] = derp_state2

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:
            mock_disc.side_effect = populate_state_phase2
            await client.trigger_rediscovery()

        # New program's DERControlList should now be subscribed
        derc_subs = [
            s for s in sub_manager.active_subscriptions if s.resource_type == "DERControlList"
        ]
        derc_resources = {s.subscribed_resource for s in derc_subs}
        assert "/edev/1/fsa/1/derp/2/derc" in derc_resources
        assert len(sub_manager.active_subscriptions) > initial_count

        await client.shutdown()


class TestSubscribableChecks:
    @pytest.mark.asyncio
    async def test_fsa_not_subscribed_when_not_subscribable(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """FSAList not subscribed when fsa_list_subscribable is False."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=False,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        assert len(sub_manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_fsa_subscribed_when_subscribable(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """FSAList subscribed when fsa_list_subscribable is True."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )
        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        fsa_subs = [s for s in sub_manager.active_subscriptions if s.resource_type == "FSAList"]
        assert len(fsa_subs) == 1

        await client.shutdown()

    @pytest.mark.asyncio
    async def test_derp_not_subscribed_when_not_subscribable(
        self, client: CsipClient, sub_manager: SubscriptionManager
    ):
        """DERProgramList not subscribed when derp_list_subscribable is False."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            derp_list_subscribable=False,
        )
        # Add an FSA with a derp list link
        fsa = MagicMock()
        fsa.derprogram_list_link = MagicMock()
        fsa.derprogram_list_link.href = "/edev/1/fsa/1/derp"
        edev_state.fsa_list = [fsa]

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        derp_subs = [
            s for s in sub_manager.active_subscriptions if s.resource_type == "DERProgramList"
        ]
        assert len(derp_subs) == 0

    @pytest.mark.asyncio
    async def test_derp_subscribed_when_subscribable(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """DERProgramList subscribed when derp_list_subscribable is True."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            derp_list_subscribable=True,
        )
        # Add an FSA with a derp list link
        fsa = MagicMock()
        fsa.derprogram_list_link = MagicMock()
        fsa.derprogram_list_link.href = "/edev/1/fsa/1/derp"
        edev_state.fsa_list = [fsa]

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            await client.connect()

        derp_subs = [
            s for s in sub_manager.active_subscriptions if s.resource_type == "DERProgramList"
        ]
        assert len(derp_subs) == 1

        await client.shutdown()


class TestRediscoveryReconciliation:
    """Tests for diff-based subscription reconciliation during rediscovery."""

    @pytest.mark.asyncio
    async def test_rediscovery_cancels_removed_subscriptions(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Subscriptions to resources that disappear after rediscovery are cancelled."""
        mock_http.post.return_value = "/edev/1/sub/1"
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        # Rediscovery returns no end devices (no desired subs)
        with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
            await client.trigger_rediscovery()

        assert len(sub_manager.active_subscriptions) == 0
        mock_http.delete.assert_called()

    @pytest.mark.asyncio
    async def test_rediscovery_creates_new_subscriptions(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """New resources after rediscovery get subscribed."""
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
        )

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            mock_http.post.return_value = "/edev/1/sub/1"
            await client.trigger_rediscovery()

        assert len(sub_manager.active_subscriptions) == 1
        assert sub_manager.active_subscriptions[0].resource_type == "FSAList"

    @pytest.mark.asyncio
    async def test_rediscovery_mixed_reconciliation(
        self, client: CsipClient, sub_manager: SubscriptionManager, mock_http: AsyncMock
    ):
        """Rediscovery with one kept (renewed) sub and one new sub."""
        mock_http.post.return_value = "/edev/1/sub/1"
        await sub_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        original_uri = sub_manager.active_subscriptions[0].subscription_uri

        # After rediscovery: same FSAList + new DERProgramList
        edev_state = _make_edev_state(
            sub_list_href="/edev/1/sub",
            fsa_list_link_href="/edev/1/fsa",
            fsa_list_subscribable=True,
            derp_list_subscribable=True,
        )
        fsa = MagicMock()
        fsa.derprogram_list_link = MagicMock()
        fsa.derprogram_list_link.href = "/edev/1/fsa/1/derp"
        edev_state.fsa_list = [fsa]

        with patch(
            "py20305.client.csip_client.discover", new_callable=AsyncMock
        ) as mock_disc:

            async def populate_state(http, state, **_kw):
                state.end_devices["/edev/1"] = edev_state

            mock_disc.side_effect = populate_state
            mock_http.post.reset_mock()
            mock_http.delete.reset_mock()
            # First POST = renewal of FSAList (returns same URI), second = create DERProgramList
            mock_http.post.side_effect = [original_uri, "/edev/1/sub/2"]
            await client.trigger_rediscovery()

        # FSAList kept (renewed, original URI), DERProgramList created
        assert len(sub_manager.active_subscriptions) == 2
        uris = {s.subscription_uri for s in sub_manager.active_subscriptions}
        assert original_uri in uris
        mock_http.delete.assert_not_called()
        # 1 POST for renewal + 1 POST for create
        assert mock_http.post.call_count == 2
