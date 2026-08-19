"""Tests for SubscriptionManager."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from py20305.client.errors import Sep2ProtocolError
from py20305.subscription.manager import (
    ReconcileResult,
    StoredNotification,
    SubscriptionManager,
    SubscriptionState,
)


@pytest.fixture
def mock_client() -> AsyncMock:
    client = AsyncMock()
    client.post = AsyncMock(return_value="/edev/1/sub/1")
    client.delete = AsyncMock(return_value=200)
    return client


@pytest.fixture
def manager(mock_client: AsyncMock) -> SubscriptionManager:
    return SubscriptionManager(
        client=mock_client,
        notification_uri="https://agg.local:10443/notify",
    )


class TestSubscriptionState:
    def test_defaults(self):
        before = time.time()
        state = SubscriptionState(
            subscription_uri="/edev/1/sub/1",
            subscribed_resource="/edev/1/fsa",
            notification_uri="https://agg:10443/notify",
            resource_type="FSAList",
        )
        assert state.status == "active"
        assert state.created_at >= before

    def test_explicit_fields(self):
        state = SubscriptionState(
            subscription_uri="/edev/1/sub/1",
            subscribed_resource="/edev/1/fsa",
            notification_uri="https://agg:10443/notify",
            resource_type="FSAList",
            status="cancelled",
            created_at=100.0,
        )
        assert state.status == "cancelled"
        assert state.created_at == 100.0


class TestStoredNotification:
    def test_construction(self):
        n = StoredNotification(
            subscribed_resource="/edev/1/fsa",
            status=0,
            subscription_uri="/edev/1/sub/1",
            created_at=1000.0,
        )
        assert n.new_resource_uri is None

    def test_with_new_resource_uri(self):
        n = StoredNotification(
            subscribed_resource="/edev/1/fsa",
            status=2,
            subscription_uri="/edev/1/sub/1",
            created_at=1000.0,
            new_resource_uri="/edev/2/fsa",
        )
        assert n.new_resource_uri == "/edev/2/fsa"


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_success(self, manager: SubscriptionManager, mock_client: AsyncMock):
        result = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        assert result is not None
        assert result.subscription_uri == "/edev/1/sub/1"
        assert result.subscribed_resource == "/edev/1/fsa"
        assert result.resource_type == "FSAList"
        assert result.status == "active"

        # Verify POST was called with a Subscription model
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "/edev/1/sub"

    @pytest.mark.asyncio
    async def test_no_location_returns_none(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        mock_client.post.return_value = None
        result = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert result is None

    @pytest.mark.asyncio
    async def test_post_failure_returns_none(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        mock_client.post.side_effect = Sep2ProtocolError("409 Conflict", 409)
        result = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert result is None

    @pytest.mark.asyncio
    async def test_post_server_error_returns_none(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        mock_client.post.side_effect = Sep2ProtocolError("500 Internal", 500)
        result = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_truncation_of_subscribed_resource(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Verify subscribedResource is preserved full-length (fixes upstream 64-char bug)."""
        long_resource = "/edev/1/fsa/some/very/long/path/that/exceeds/sixty-four/characters/easily"
        result = await manager.subscribe("/edev/1/sub", long_resource, "FSAList")
        assert result is not None
        assert result.subscribed_resource == long_resource

        # Verify the Subscription model also has full-length resource
        posted_sub = mock_client.post.call_args[0][1]
        assert posted_sub.subscribed_resource == long_resource

    @pytest.mark.asyncio
    async def test_custom_encoding_and_level(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        await manager.subscribe(
            "/edev/1/sub", "/edev/1/fsa", "FSAList", encoding=1, level="+S2", limit=5
        )
        posted_sub = mock_client.post.call_args[0][1]
        assert posted_sub.encoding == 1
        assert posted_sub.level == "+S2"
        assert posted_sub.limit == 5

    @pytest.mark.asyncio
    async def test_protocol_error_logged_without_traceback(
        self,
        manager: SubscriptionManager,
        mock_client: AsyncMock,
        caplog: pytest.LogCaptureFixture,
    ):
        """A 400 (or any 4xx) from the server is an expected outcome and
        should not emit a traceback. Regression: the bare `except Exception`
        with exc_info=True was spamming logs with stack traces on e.g.
        server-side 400 responses to subscription attempts."""
        mock_client.post.side_effect = Sep2ProtocolError("400 Bad Request", 400)

        with caplog.at_level("WARNING"):
            result = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        assert result is None
        protocol_records = [
            r for r in caplog.records if "Server rejected subscription" in r.getMessage()
        ]
        assert len(protocol_records) == 1
        assert protocol_records[0].exc_info is None
        assert "HTTP 400" in protocol_records[0].getMessage()


class TestCancel:
    @pytest.mark.asyncio
    async def test_success(self, manager: SubscriptionManager, mock_client: AsyncMock):
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        result = await manager.cancel("/edev/1/sub/1")

        assert result is True
        mock_client.delete.assert_called_once_with("/edev/1/sub/1")
        assert manager.active_subscriptions == []

    @pytest.mark.asyncio
    async def test_removes_state(self, manager: SubscriptionManager, mock_client: AsyncMock):
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert len(manager.active_subscriptions) == 1
        await manager.cancel("/edev/1/sub/1")
        assert len(manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_failure_returns_false_and_removes_state(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.delete.side_effect = Sep2ProtocolError("500", 500)
        result = await manager.cancel("/edev/1/sub/1")
        assert result is False
        # State IS removed on failure to prevent stale entries
        assert len(manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_nonexistent_succeeds(self, manager: SubscriptionManager, mock_client: AsyncMock):
        result = await manager.cancel("/edev/1/sub/999")
        assert result is True


class TestCancelAll:
    @pytest.mark.asyncio
    async def test_cancels_all(self, manager: SubscriptionManager, mock_client: AsyncMock):
        mock_client.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/2"]
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await manager.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")

        await manager.cancel_all()
        assert manager.active_subscriptions == []
        assert mock_client.delete.call_count == 2

    @pytest.mark.asyncio
    async def test_tolerates_errors(self, manager: SubscriptionManager, mock_client: AsyncMock):
        mock_client.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/2"]
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await manager.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")

        # First delete fails, second succeeds
        mock_client.delete.side_effect = [
            Sep2ProtocolError("500", 500),
            200,
        ]
        await manager.cancel_all()
        # Both attempted
        assert mock_client.delete.call_count == 2
        # All state cleared regardless of DELETE failures
        assert len(manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_parallel(self, mock_client: AsyncMock):
        """DELETEs run concurrently, not sequentially."""
        import asyncio

        mgr = SubscriptionManager(
            client=mock_client,
            notification_uri="https://agg:10443/notify",
        )
        mock_client.post.side_effect = ["/sub/1", "/sub/2", "/sub/3"]
        await mgr.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await mgr.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")
        await mgr.subscribe("/edev/1/sub", "/edev/1/derc", "DERControlList")

        call_times: list[float] = []

        async def slow_delete(uri):
            call_times.append(asyncio.get_event_loop().time())
            await asyncio.sleep(0.05)
            return 200

        mock_client.delete = AsyncMock(side_effect=slow_delete)

        start = asyncio.get_event_loop().time()
        await mgr.cancel_all()
        elapsed = asyncio.get_event_loop().time() - start

        assert mock_client.delete.call_count == 3
        # If sequential, would take ~0.15s; parallel should be ~0.05s
        assert elapsed < 0.12
        assert len(mgr.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_clears_state_on_failure(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """_subscriptions is empty after cancel_all() even when DELETEs fail."""
        mock_client.post.side_effect = ["/sub/1", "/sub/2"]
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await manager.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")

        mock_client.delete.side_effect = Sep2ProtocolError("500", 500)
        await manager.cancel_all()
        assert len(manager._subscriptions) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_empty_is_noop(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """cancel_all() with no subscriptions does nothing."""
        await manager.cancel_all()
        mock_client.delete.assert_not_called()


class TestNotificationTracking:
    def test_record_notification(self, manager: SubscriptionManager):
        n = StoredNotification(
            subscribed_resource="/edev/1/fsa",
            status=0,
            subscription_uri="/edev/1/sub/1",
            created_at=1000.0,
        )
        manager.record_notification(n)
        assert len(manager.notifications) == 1
        assert manager.notifications[0] is n

    def test_bounded_deque_evicts_oldest(self):
        mgr = SubscriptionManager(
            client=AsyncMock(),
            notification_uri="https://agg:10443/notify",
            max_notifications=3,
        )
        for i in range(5):
            mgr.record_notification(
                StoredNotification(
                    subscribed_resource=f"/edev/{i}/fsa",
                    status=0,
                    subscription_uri=f"/edev/{i}/sub/1",
                    created_at=float(i),
                )
            )
        assert len(mgr.notifications) == 3
        # Oldest (0, 1) evicted, remaining are 2, 3, 4
        assert mgr.notifications[0].created_at == 2.0
        assert mgr.notifications[2].created_at == 4.0

    @pytest.mark.asyncio
    async def test_mark_cancelled(self, manager: SubscriptionManager, mock_client: AsyncMock):
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        manager.mark_cancelled("/edev/1/sub/1")
        assert manager.active_subscriptions == []

    def test_mark_cancelled_unknown_is_noop(self, manager: SubscriptionManager):
        manager.mark_cancelled("/unknown/sub/1")

    def test_remove_notifications_for(self, manager: SubscriptionManager):
        for resource in ["/edev/1/fsa", "/edev/2/fsa", "/edev/1/fsa"]:
            manager.record_notification(
                StoredNotification(
                    subscribed_resource=resource,
                    status=0,
                    subscription_uri="/sub/1",
                    created_at=time.time(),
                )
            )
        manager.remove_notifications_for("/edev/1/fsa")
        assert len(manager.notifications) == 1
        assert manager.notifications[0].subscribed_resource == "/edev/2/fsa"


class TestValidateRestoredSubscriptions:
    @pytest.mark.asyncio
    async def test_valid_subscriptions_kept(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Subscriptions that renew successfully are kept."""
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        # renew() will POST to the subscription URI, return location
        mock_client.post.return_value = "/edev/1/sub/1"

        valid, removed = await manager.validate_restored_subscriptions()
        assert valid == 1
        assert removed == 0
        assert len(manager.active_subscriptions) == 1

    @pytest.mark.asyncio
    async def test_stale_subscriptions_removed(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Subscriptions that fail renewal are removed."""
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        # Make renewal fail
        mock_client.post.side_effect = Exception("server gone")

        valid, removed = await manager.validate_restored_subscriptions()
        assert valid == 0
        assert removed == 1
        assert len(manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_mixed_valid_and_stale(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Mix of valid and stale subscriptions returns correct counts."""
        mock_client.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/2"]
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await manager.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")

        # First renewal succeeds, second fails
        mock_client.post.side_effect = ["/edev/1/sub/1", Exception("expired")]

        valid, removed = await manager.validate_restored_subscriptions()
        assert valid == 1
        assert removed == 1
        assert len(manager.active_subscriptions) == 1


class TestSubscribeDedup:
    @pytest.mark.asyncio
    async def test_existing_active_sub_returns_without_post(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """subscribe() returns existing active sub without POSTing again."""
        result1 = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert result1 is not None

        # Reset post mock to track new calls
        mock_client.post.reset_mock()

        result2 = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert result2 is result1
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancelled_sub_allows_new_post(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """subscribe() POSTs if existing sub is cancelled."""
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        manager.mark_cancelled("/edev/1/sub/1")

        mock_client.post.return_value = "/edev/1/sub/2"
        result = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert result is not None
        assert result.subscription_uri == "/edev/1/sub/2"


class TestSubscribedResourceTypes:
    @pytest.mark.asyncio
    async def test_returns_active_types(self, manager: SubscriptionManager, mock_client: AsyncMock):
        mock_client.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/2"]
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa/1/derp/1/derc", "DERControlList")

        assert manager.subscribed_resource_types() == {"FSAList", "DERControlList"}

    @pytest.mark.asyncio
    async def test_excludes_cancelled(self, manager: SubscriptionManager, mock_client: AsyncMock):
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        manager.mark_cancelled("/edev/1/sub/1")

        assert manager.subscribed_resource_types() == set()

    def test_empty_when_no_subscriptions(self, manager: SubscriptionManager):
        assert manager.subscribed_resource_types() == set()


class TestCheckpoint:
    @pytest.mark.asyncio
    async def test_roundtrip(self, manager: SubscriptionManager, mock_client: AsyncMock):
        mock_client.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/2"]
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        await manager.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")

        checkpoint = manager.to_checkpoint()

        new_manager = SubscriptionManager(
            client=AsyncMock(),
            notification_uri="https://agg:10443/notify",
        )
        new_manager.restore_from_checkpoint(checkpoint)

        assert len(new_manager.active_subscriptions) == 2
        uris = {s.subscription_uri for s in new_manager.active_subscriptions}
        assert uris == {"/edev/1/sub/1", "/edev/1/sub/2"}

    @pytest.mark.asyncio
    async def test_restore_preserves_fields(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        manager.mark_cancelled("/edev/1/sub/1")

        checkpoint = manager.to_checkpoint()
        new_manager = SubscriptionManager(
            client=AsyncMock(), notification_uri="https://agg:10443/notify"
        )
        new_manager.restore_from_checkpoint(checkpoint)

        # Cancelled status is preserved
        assert new_manager.active_subscriptions == []

    def test_restore_empty(self, manager: SubscriptionManager):
        manager.restore_from_checkpoint({})
        assert manager.active_subscriptions == []

    def test_restore_clears_existing(self, manager: SubscriptionManager):
        manager.restore_from_checkpoint(
            {
                "subscriptions": [
                    {
                        "subscription_uri": "/old/sub",
                        "subscribed_resource": "/old/res",
                        "notification_uri": "https://old/notify",
                        "resource_type": "FSAList",
                    }
                ]
            }
        )
        assert len(manager.active_subscriptions) == 1
        # Now restore empty to confirm it clears
        manager.restore_from_checkpoint({})
        assert manager.active_subscriptions == []


class TestUnmanagedSubscriptions:
    """Tests for 2018 compat mode: synthetic URI for missing Location header."""

    @pytest.fixture
    def compat_manager(self, mock_client: AsyncMock) -> SubscriptionManager:
        return SubscriptionManager(
            client=mock_client,
            notification_uri="https://agg:10443/notify",
            server_2018_compat=True,
        )

    @pytest.mark.asyncio
    async def test_no_location_2018_compat_creates_synthetic(
        self, compat_manager: SubscriptionManager, mock_client: AsyncMock
    ):
        mock_client.post.return_value = None
        result = await compat_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        assert result is not None
        assert result.unmanaged is True
        assert result.subscription_uri == "_synthetic:/edev/1/fsa"
        assert result.subscribed_resource == "/edev/1/fsa"

    @pytest.mark.asyncio
    async def test_no_location_non_compat_still_returns_none(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Non-compat mode is unchanged: no Location -> None."""
        mock_client.post.return_value = None
        result = await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        assert result is None

    @pytest.mark.asyncio
    async def test_cancel_unmanaged_skips_delete(
        self, compat_manager: SubscriptionManager, mock_client: AsyncMock
    ):
        mock_client.post.return_value = None
        await compat_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        mock_client.delete.reset_mock()
        result = await compat_manager.cancel("_synthetic:/edev/1/fsa")

        assert result is True
        mock_client.delete.assert_not_called()
        assert len(compat_manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_cancel_all_mixed_managed_unmanaged(
        self, compat_manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """cancel_all() DELETEs managed but skips unmanaged."""
        # First subscription: managed (has location)
        mock_client.post.return_value = "/edev/1/sub/1"
        await compat_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        # Second subscription: unmanaged (no location)
        mock_client.post.return_value = None
        await compat_manager.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")

        assert len(compat_manager.active_subscriptions) == 2

        mock_client.delete.reset_mock()
        await compat_manager.cancel_all()

        # DELETE called only for the managed subscription
        mock_client.delete.assert_called_once_with("/edev/1/sub/1")
        assert len(compat_manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_unmanaged_in_subscribed_resource_types(
        self, compat_manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Unmanaged subscriptions are included in subscribed_resource_types (poll suppression)."""
        mock_client.post.return_value = None
        await compat_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        assert "FSAList" in compat_manager.subscribed_resource_types()

    @pytest.mark.asyncio
    async def test_checkpoint_roundtrip_unmanaged(
        self, compat_manager: SubscriptionManager, mock_client: AsyncMock
    ):
        mock_client.post.return_value = None
        await compat_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        checkpoint = compat_manager.to_checkpoint()
        new_mgr = SubscriptionManager(
            client=AsyncMock(),
            notification_uri="https://agg:10443/notify",
            server_2018_compat=True,
        )
        new_mgr.restore_from_checkpoint(checkpoint)

        assert len(new_mgr.active_subscriptions) == 1
        restored = new_mgr.active_subscriptions[0]
        assert restored.unmanaged is True
        assert restored.subscription_uri == "_synthetic:/edev/1/fsa"

    @pytest.mark.asyncio
    async def test_renew_unmanaged_no_http(
        self, compat_manager: SubscriptionManager, mock_client: AsyncMock
    ):
        mock_client.post.return_value = None
        await compat_manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        mock_client.post.reset_mock()
        before = compat_manager.active_subscriptions[0].created_at

        import time

        time.sleep(0.01)
        result = await compat_manager.renew("_synthetic:/edev/1/fsa")

        assert result is True
        mock_client.post.assert_not_called()
        assert compat_manager.active_subscriptions[0].created_at > before


class TestReconcile:
    """Tests for diff-based subscription reconciliation."""

    @pytest.mark.asyncio
    async def test_all_kept_renews_each(self, manager: SubscriptionManager, mock_client: AsyncMock):
        """When desired == existing, each kept sub is renewed (1 POST per sub)."""
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.post.reset_mock()
        mock_client.delete.reset_mock()
        mock_client.post.return_value = "/edev/1/sub/1"  # same URI on renewal

        result = await manager.reconcile("/edev/1/sub", {("/edev/1/fsa", "FSAList")})

        assert result.kept == 1
        assert result.renewed == 1
        assert result.cancelled == 0
        assert result.created == 0
        mock_client.post.assert_called_once()
        mock_client.delete.assert_not_called()
        assert len(manager.active_subscriptions) == 1

    @pytest.mark.asyncio
    async def test_all_cancelled(self, manager: SubscriptionManager, mock_client: AsyncMock):
        """Empty desired set cancels all existing subscriptions."""
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")

        result = await manager.reconcile("/edev/1/sub", set())

        assert result.cancelled == 1
        assert result.kept == 0
        assert result.created == 0
        mock_client.delete.assert_called_once()
        assert len(manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_all_created(self, manager: SubscriptionManager, mock_client: AsyncMock):
        """No existing subs -- all desired are created."""
        mock_client.post.return_value = "/edev/1/sub/1"

        result = await manager.reconcile("/edev/1/sub", {("/edev/1/fsa", "FSAList")})

        assert result.created == 1
        assert result.kept == 0
        assert result.cancelled == 0
        assert len(manager.active_subscriptions) == 1

    @pytest.mark.asyncio
    async def test_reconcile_renew_kept_false_issues_no_repost(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Rediscovery path (renew_kept=False) keeps subs WITHOUT re-POSTing.

        Regression for the rediscovery<->renewal loop: re-POSTing kept subs on
        every rediscovery provokes the server to echo a current-state
        notification, which is classified structural and triggers another
        rediscovery -> infinite loop hammering the server. A no-op rediscovery
        must touch the wire zero times.
        """
        mock_client.post.return_value = "/edev/1/sub/1"
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.post.reset_mock()
        mock_client.delete.reset_mock()

        result = await manager.reconcile(
            "/edev/1/sub", {("/edev/1/fsa", "FSAList")}, renew_kept=False
        )

        assert result.kept == 1
        assert result.renewed == 0  # NOT renewed
        assert result.created == 0
        assert result.cancelled == 0
        mock_client.post.assert_not_called()  # zero re-POST -> no notification echo
        mock_client.delete.assert_not_called()
        assert len(manager.active_subscriptions) == 1

    @pytest.mark.asyncio
    async def test_reconcile_renew_kept_false_still_creates_and_cancels(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """renew_kept=False still reconciles membership: create new, cancel stale."""
        mock_client.post.return_value = "/edev/1/sub/1"
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.post.return_value = "/edev/1/sub/2"
        await manager.subscribe("/edev/1/sub", "/edev/1/derp/1/derc", "DERControlList")
        mock_client.post.reset_mock()
        mock_client.delete.reset_mock()
        mock_client.post.return_value = "/edev/1/sub/3"

        # Keep fsa, drop derc, add derp -- all while renew_kept=False
        result = await manager.reconcile(
            "/edev/1/sub",
            {("/edev/1/fsa", "FSAList"), ("/edev/1/derp", "DERProgramList")},
            renew_kept=False,
        )

        assert result.kept == 1  # fsa kept, not re-POSTed
        assert result.renewed == 0
        assert result.created == 1  # derp created
        assert result.cancelled == 1  # derc cancelled
        assert mock_client.post.call_count == 1  # only the create, not a renew
        active = {s.subscribed_resource for s in manager.active_subscriptions}
        assert active == {"/edev/1/fsa", "/edev/1/derp"}

    @pytest.mark.asyncio
    async def test_mixed_keep_cancel_create(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Mixed reconciliation: one kept (renewed), one cancelled, one created."""
        # Existing: fsa (keep) + derc (cancel)
        mock_client.post.return_value = "/edev/1/sub/1"
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.post.return_value = "/edev/1/sub/2"
        await manager.subscribe("/edev/1/sub", "/edev/1/derp/1/derc", "DERControlList")
        mock_client.post.reset_mock()
        mock_client.delete.reset_mock()
        # First POST = renewal of fsa (kept), second POST = create derp
        mock_client.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/3"]

        # Desired: fsa (keep) + derp (create), derc gone (cancel)
        result = await manager.reconcile(
            "/edev/1/sub",
            {("/edev/1/fsa", "FSAList"), ("/edev/1/derp", "DERProgramList")},
        )

        assert result.kept == 1
        assert result.renewed == 1
        assert result.cancelled == 1
        assert result.created == 1
        # fsa subscription retained (renewed), derc cancelled, derp created
        active_resources = {s.subscribed_resource for s in manager.active_subscriptions}
        assert active_resources == {"/edev/1/fsa", "/edev/1/derp"}
        # 1 POST for renewal + 1 POST for create
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_kept_renewal_failure_falls_back_to_create(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Kept sub whose renewal fails is cancelled locally and recreated."""
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.post.reset_mock()
        mock_client.delete.reset_mock()

        # First POST = renewal (fails), second POST = create (succeeds)
        mock_client.post.side_effect = [Exception("server reset"), "/edev/1/sub/99"]

        result = await manager.reconcile("/edev/1/sub", {("/edev/1/fsa", "FSAList")})

        assert result.kept == 0
        assert result.cancelled == 1  # stale sub cleaned up
        assert result.created == 1  # recreated
        assert result.renewed == 0
        assert mock_client.post.call_count == 2
        assert len(manager.active_subscriptions) == 1
        assert manager.active_subscriptions[0].subscription_uri == "/edev/1/sub/99"

    @pytest.mark.asyncio
    async def test_cancel_error_does_not_abort(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """A failed DELETE does not prevent other reconciliation actions."""
        mock_client.post.return_value = "/edev/1/sub/1"
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.delete.side_effect = Exception("server error")
        mock_client.post.reset_mock()
        mock_client.post.return_value = "/edev/1/sub/2"

        result = await manager.reconcile("/edev/1/sub", {("/edev/1/derp", "DERProgramList")})

        assert result.cancelled == 1
        assert result.cancel_errors == 1
        assert result.created == 1
        assert len(manager.active_subscriptions) == 1
        assert manager.active_subscriptions[0].subscribed_resource == "/edev/1/derp"

    @pytest.mark.asyncio
    async def test_create_error_does_not_abort(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """A failed POST records a create_error but doesn't abort."""
        mock_client.post.side_effect = Exception("server error")

        result = await manager.reconcile("/edev/1/sub", {("/edev/1/fsa", "FSAList")})

        assert result.create_errors == 1
        assert result.created == 0
        assert len(manager.active_subscriptions) == 0

    @pytest.mark.asyncio
    async def test_resource_type_change_triggers_resubscribe(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Same resource with different type is cancelled and recreated."""
        mock_client.post.return_value = "/edev/1/sub/1"
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        mock_client.post.reset_mock()
        mock_client.delete.reset_mock()
        mock_client.post.return_value = "/edev/1/sub/2"

        result = await manager.reconcile("/edev/1/sub", {("/edev/1/fsa", "EndDeviceList")})

        assert result.cancelled == 1
        assert result.created == 1
        assert result.kept == 0
        sub = manager.active_subscriptions[0]
        assert sub.resource_type == "EndDeviceList"
        assert sub.subscription_uri == "/edev/1/sub/2"

    @pytest.mark.asyncio
    async def test_cancelled_subs_cleaned_up(
        self, manager: SubscriptionManager, mock_client: AsyncMock
    ):
        """Subs with status='cancelled' are purged during reconciliation."""
        await manager.subscribe("/edev/1/sub", "/edev/1/fsa", "FSAList")
        manager.mark_cancelled("/edev/1/sub/1")
        mock_client.post.reset_mock()
        mock_client.post.return_value = "/edev/1/sub/2"

        result = await manager.reconcile("/edev/1/sub", {("/edev/1/fsa", "FSAList")})

        # Cancelled sub is cleaned up, new one created
        assert result.created == 1
        assert result.kept == 0
        assert len(manager.active_subscriptions) == 1
        assert manager.active_subscriptions[0].subscription_uri == "/edev/1/sub/2"

    @pytest.mark.asyncio
    async def test_reconcile_result_defaults(self):
        """ReconcileResult fields default to zero."""
        r = ReconcileResult()
        assert r.kept == r.cancelled == r.created == r.renewed == 0
        assert r.cancel_errors == r.create_errors == 0


class TestReconcileWithServer:
    """GET the server SubscriptionList and re-establish dropped subscriptions."""

    @staticmethod
    def _page(resource_to_href):
        """Build a SubscriptionList page from a {subscribed_resource: href} map."""
        from types import SimpleNamespace

        return SimpleNamespace(
            subscription=[
                SimpleNamespace(subscribed_resource=r, href=h) for r, h in resource_to_href.items()
            ]
        )

    def _mgr_with_subs(self, post_return="/sub/new", **subs):
        client = AsyncMock()
        client.post = AsyncMock(return_value=post_return)
        mgr = SubscriptionManager(client, "https://h:10443/notify")
        for uri, resource in subs.items():
            st = SubscriptionState(
                subscription_uri=f"/sub/{uri}",
                subscribed_resource=resource,
                resource_type="EndDeviceList",
                notification_uri=mgr._notification_uri,
            )
            mgr._subscriptions[st.subscription_uri] = st
        return client, mgr

    @pytest.mark.asyncio
    async def test_re_establishes_only_the_dropped_subscription(self):
        # Local: A (/edev) + B (/derp). Server has only A (at A's URI) -> B re-POSTed.
        client, mgr = self._mgr_with_subs(A="/api/v2/edev", B="/api/v2/derp")
        client.get_list = AsyncMock(return_value=[self._page({"/api/v2/edev": "/sub/A"})])
        n = await mgr.reconcile_with_server("/api/v2/edev/AGG/sub")
        assert n == 1
        assert client.post.call_count == 1  # exactly one re-subscribe (B)
        assert "/sub/B" not in mgr._subscriptions  # stale local entry dropped

    @pytest.mark.asyncio
    async def test_all_present_is_a_no_op(self):
        client, mgr = self._mgr_with_subs(A="/api/v2/edev")
        client.get_list = AsyncMock(return_value=[self._page({"/api/v2/edev": "/sub/A"})])
        n = await mgr.reconcile_with_server("/sub")
        assert n == 0
        client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_adopts_server_uri_when_local_uri_drifted(self):
        # Server holds the resource under a DIFFERENT URI than our local one
        # (dropped + recreated / renumbered). Adopt the server's URI so we don't
        # report a stale one that 404s on cancel/renew (ERR-002 regression).
        client, mgr = self._mgr_with_subs(A="/api/v2/edev")
        client.get_list = AsyncMock(return_value=[self._page({"/api/v2/edev": "/sub/A-NEW"})])
        n = await mgr.reconcile_with_server("/sub")
        assert n == 0  # nothing re-established
        client.post.assert_not_awaited()  # adopting a URI must not re-POST
        assert "/sub/A" not in mgr._subscriptions  # stale URI dropped
        assert "/sub/A-NEW" in mgr._subscriptions  # server URI adopted
        assert mgr._subscriptions["/sub/A-NEW"].subscribed_resource == "/api/v2/edev"
        assert [s.subscription_uri for s in mgr.active_subscriptions] == ["/sub/A-NEW"]

    @pytest.mark.asyncio
    async def test_present_with_missing_href_is_not_reposted(self):
        # A server entry with a resource but no href counts as "present" (prior
        # resource-presence semantics): no re-POST, and nothing to adopt.
        client, mgr = self._mgr_with_subs(A="/api/v2/edev")
        client.get_list = AsyncMock(return_value=[self._page({"/api/v2/edev": None})])
        n = await mgr.reconcile_with_server("/sub")
        assert n == 0
        client.post.assert_not_awaited()  # not treated as missing -> no re-POST
        assert "/sub/A" in mgr._subscriptions  # kept as-is (no href to adopt)

    @pytest.mark.asyncio
    async def test_reestablish_missing_false_empty_server_list_drops_nothing(self):
        # Safety guard: an empty (or unhelpful) server SubscriptionList must NOT
        # wipe local subscriptions -- we only drop against a *populated* list.
        client, mgr = self._mgr_with_subs(A="/api/v2/edev", B="/api/v2/derp")
        client.get_list = AsyncMock(return_value=[self._page({})])  # server returned nothing
        n = await mgr.reconcile_with_server("/sub", reestablish_missing=False)
        assert n == 0
        client.post.assert_not_awaited()
        assert "/sub/A" in mgr._subscriptions
        assert "/sub/B" in mgr._subscriptions
        assert len(mgr.active_subscriptions) == 2  # nothing dropped

    @pytest.mark.asyncio
    async def test_reestablish_missing_false_adopts_and_drops_but_never_reposts(self):
        # Rediscovery path: adopt a drifted URI, and drop a sub the server no
        # longer has -- but never re-POST (re-POSTing during rediscovery risks
        # the echo loop; _auto_subscribe re-creates the dropped one afterwards).
        client, mgr = self._mgr_with_subs(A="/api/v2/edev", B="/api/v2/derp")
        # Server: A present under a NEW URI (drift); B absent entirely (missing).
        client.get_list = AsyncMock(return_value=[self._page({"/api/v2/edev": "/sub/A-NEW"})])
        n = await mgr.reconcile_with_server("/sub", reestablish_missing=False)
        assert n == 0  # nothing re-established (no re-POST)
        client.post.assert_not_awaited()
        assert "/sub/A-NEW" in mgr._subscriptions  # A adopted
        assert "/sub/A" not in mgr._subscriptions  # A's stale URI gone
        assert "/sub/B" not in mgr._subscriptions  # B dropped (server no longer has it)
        assert [s.subscription_uri for s in mgr.active_subscriptions] == ["/sub/A-NEW"]

    @pytest.mark.asyncio
    async def test_get_failure_returns_zero_without_resubscribing(self):
        client, mgr = self._mgr_with_subs(A="/api/v2/edev")
        client.get_list = AsyncMock(side_effect=Sep2ProtocolError("boom", 500))
        n = await mgr.reconcile_with_server("/sub")
        assert n == 0
        client.post.assert_not_awaited()  # don't churn when we can't read the list

    @pytest.mark.asyncio
    async def test_non_protocol_get_error_is_a_logged_no_op(self):
        # A transport-level OSError (not a Sep2ProtocolError) must also be caught.
        client, mgr = self._mgr_with_subs(A="/api/v2/edev")
        client.get_list = AsyncMock(side_effect=OSError("connection refused"))
        n = await mgr.reconcile_with_server("/sub")
        assert n == 0
        client.post.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_restores_local_state_when_resubscribe_fails(self):
        # If the re-POST fails transiently, the dropped sub's local state must be
        # restored so the next reconcile retries -- otherwise the last sub could
        # vanish and stop the reconcile poll.
        client, mgr = self._mgr_with_subs(A="/api/v2/derp")
        client.get_list = AsyncMock(return_value=[self._page({})])  # server has none
        client.post = AsyncMock(side_effect=Sep2ProtocolError("rejected", 500))
        n = await mgr.reconcile_with_server("/sub")
        assert n == 0
        assert "/sub/A" in mgr._subscriptions  # restored
        assert len(mgr.active_subscriptions) == 1
