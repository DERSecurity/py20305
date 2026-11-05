"""Tests for subscription renewal and deduplication."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from py20305.subscription.manager import SubscriptionManager, SubscriptionState


@pytest.fixture
def mgr() -> SubscriptionManager:
    client = AsyncMock()
    client.post = AsyncMock(return_value="/sub/1")
    return SubscriptionManager(client, "https://localhost:10443/notify")


class TestRenewAll:
    @pytest.mark.asyncio
    async def test_renew_all_posts_for_active(self, mgr: SubscriptionManager):
        # Create a subscription
        state = await mgr.subscribe("/edev/1/sub", "/edev/1/derc", "DERControlList")
        assert state is not None
        assert state.status == "active"

        count = await mgr.renew_all()
        assert count == 1

    @pytest.mark.asyncio
    async def test_renew_all_skips_cancelled(self, mgr: SubscriptionManager):
        state = await mgr.subscribe("/edev/1/sub", "/edev/1/derc", "DERControlList")
        assert state is not None
        mgr.mark_cancelled(state.subscription_uri)

        count = await mgr.renew_all()
        assert count == 0


class TestRenewSingle:
    @pytest.mark.asyncio
    async def test_renew_updates_created_at(self, mgr: SubscriptionManager):
        state = await mgr.subscribe("/edev/1/sub", "/edev/1/derc", "DERControlList")
        assert state is not None
        old_time = state.created_at

        # Small delay to ensure different timestamp
        await asyncio.sleep(0.01)
        result = await mgr.renew(state.subscription_uri)
        assert result is True
        assert state.created_at > old_time

    @pytest.mark.asyncio
    async def test_renew_unknown_returns_false(self, mgr: SubscriptionManager):
        result = await mgr.renew("/unknown/sub")
        assert result is False


class TestRenewSubscriptionLostCallback:
    """When a renewal returns 401/404, the server has forgotten
    our subscription (most commonly because it restarted). Fire the
    on_subscription_lost callback so the client can trigger rediscovery."""

    @pytest.mark.asyncio
    async def test_renew_404_invokes_on_subscription_lost(self):
        from py20305.client.errors import Sep2ProtocolError

        client = AsyncMock()
        client.post = AsyncMock(side_effect=Sep2ProtocolError("Not Found", 404))
        callback = AsyncMock()
        mgr = SubscriptionManager(
            client,
            "https://localhost:10443/notify",
            on_subscription_lost=callback,
        )
        # Pre-seed an active subscription state so renew() reaches the POST.
        state = SubscriptionState(
            subscription_uri="/edev/1/sub/2",
            subscribed_resource="/edev/1/derc",
            resource_type="DERControlList",
            notification_uri=mgr._notification_uri,
        )
        mgr._subscriptions[state.subscription_uri] = state

        result = await mgr.renew(state.subscription_uri)
        # Let the fire-and-forget task run.
        await asyncio.sleep(0)
        assert result is False
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_renew_401_invokes_on_subscription_lost(self):
        from py20305.client.errors import Sep2ProtocolError

        client = AsyncMock()
        client.post = AsyncMock(side_effect=Sep2ProtocolError("Unauthorized", 401))
        callback = AsyncMock()
        mgr = SubscriptionManager(
            client,
            "https://localhost:10443/notify",
            on_subscription_lost=callback,
        )
        state = SubscriptionState(
            subscription_uri="/edev/1/sub/2",
            subscribed_resource="/edev/1/derc",
            resource_type="DERControlList",
            notification_uri=mgr._notification_uri,
        )
        mgr._subscriptions[state.subscription_uri] = state

        result = await mgr.renew(state.subscription_uri)
        await asyncio.sleep(0)
        assert result is False
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_renew_500_does_not_invoke_callback(self):
        # 5xx is a transient server error, not "subscription forgotten" --
        # the next renewal tick will retry; don't churn rediscovery.
        from py20305.client.errors import Sep2ProtocolError

        client = AsyncMock()
        client.post = AsyncMock(side_effect=Sep2ProtocolError("Server Error", 500))
        callback = AsyncMock()
        mgr = SubscriptionManager(
            client,
            "https://localhost:10443/notify",
            on_subscription_lost=callback,
        )
        state = SubscriptionState(
            subscription_uri="/edev/1/sub/2",
            subscribed_resource="/edev/1/derc",
            resource_type="DERControlList",
            notification_uri=mgr._notification_uri,
        )
        mgr._subscriptions[state.subscription_uri] = state

        result = await mgr.renew(state.subscription_uri)
        await asyncio.sleep(0)
        assert result is False
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_renew_404_without_callback_does_not_crash(self):
        # Manager constructed without a callback (the legacy path) must
        # still handle 404 cleanly -- it just returns False without
        # triggering anything.
        from py20305.client.errors import Sep2ProtocolError

        client = AsyncMock()
        client.post = AsyncMock(side_effect=Sep2ProtocolError("Not Found", 404))
        mgr = SubscriptionManager(client, "https://localhost:10443/notify")
        state = SubscriptionState(
            subscription_uri="/edev/1/sub/2",
            subscribed_resource="/edev/1/derc",
            resource_type="DERControlList",
            notification_uri=mgr._notification_uri,
        )
        mgr._subscriptions[state.subscription_uri] = state

        result = await mgr.renew(state.subscription_uri)
        assert result is False


def _put_manager(
    put: AsyncMock, post: AsyncMock | None = None, callback: AsyncMock | None = None
) -> SubscriptionManager:
    client = AsyncMock()
    client.put = put
    client.post = post if post is not None else AsyncMock(return_value="/edev/1/sub/7")
    return SubscriptionManager(
        client,
        "https://localhost:10443/notify",
        renewal_method="put",
        on_subscription_lost=callback,
    )


async def _subscribed(mgr: SubscriptionManager, location: str) -> SubscriptionState:
    """Subscribe through ``/edev/1/sub``, with the server answering ``location``."""
    mgr._client.post = AsyncMock(return_value=location)  # type: ignore[method-assign]
    state = await mgr.subscribe("/edev/1/sub", "/edev/1/derp", "DERProgramList")
    assert state is not None
    return state


class TestRenewByPut:
    """Renewal by PUT to the subscription's own href (IEEE 2030.5 Annex A.2.7.2
    lists PUT on a Subscription as Mandatory for servers), falling back to a new
    subscription when the server no longer holds that href."""

    @pytest.mark.asyncio
    async def test_put_renews_at_the_subscription_href(self):
        mgr = _put_manager(put=AsyncMock(return_value=204))
        state = await _subscribed(mgr, "/edev/1/sub/2")
        renewal_post = AsyncMock(return_value="/edev/1/sub/9")
        mgr._client.post = renewal_post  # type: ignore[method-assign]
        old_time = state.created_at
        await asyncio.sleep(0.01)

        assert await mgr.renew("/edev/1/sub/2") is True

        mgr._client.put.assert_awaited_once()
        path, body = mgr._client.put.await_args.args
        assert path == "/edev/1/sub/2"
        assert body.subscribed_resource == "/edev/1/derp"
        assert body.notification_uri == "https://localhost:10443/notify"
        renewal_post.assert_not_awaited()
        assert state.created_at > old_time
        assert state.subscription_uri == "/edev/1/sub/2"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [404, 405])
    async def test_put_rejected_falls_back_to_post_on_the_subscribed_list(self, status: int):
        from py20305.client.errors import Sep2ProtocolError

        callback = AsyncMock()
        mgr = _put_manager(
            put=AsyncMock(side_effect=Sep2ProtocolError("rejected", status)), callback=callback
        )
        # The server placed the subscription outside the list it was POSTed to,
        # so the list cannot be derived from the subscription href.
        await _subscribed(mgr, "/moved/sub/2")
        fallback_post = AsyncMock(return_value="/edev/1/sub/9")
        mgr._client.post = fallback_post  # type: ignore[method-assign]

        assert await mgr.renew("/moved/sub/2") is True
        await asyncio.sleep(0)

        fallback_post.assert_awaited_once()
        assert fallback_post.await_args.args[0] == "/edev/1/sub"
        assert [s.subscription_uri for s in mgr.active_subscriptions] == ["/edev/1/sub/9"]
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_failed_fallback_post_reports_the_subscription_lost(self):
        from py20305.client.errors import Sep2ProtocolError

        callback = AsyncMock()
        mgr = _put_manager(
            put=AsyncMock(side_effect=Sep2ProtocolError("Not Found", 404)), callback=callback
        )
        await _subscribed(mgr, "/edev/1/sub/2")
        mgr._client.post = AsyncMock(  # type: ignore[method-assign]
            side_effect=Sep2ProtocolError("Not Found", 404)
        )

        assert await mgr.renew("/edev/1/sub/2") is False
        await asyncio.sleep(0)

        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_put_401_reports_the_subscription_lost_without_posting(self):
        from py20305.client.errors import Sep2ProtocolError

        callback = AsyncMock()
        mgr = _put_manager(
            put=AsyncMock(side_effect=Sep2ProtocolError("Unauthorized", 401)), callback=callback
        )
        await _subscribed(mgr, "/edev/1/sub/2")
        renewal_post = AsyncMock(return_value="/edev/1/sub/9")
        mgr._client.post = renewal_post  # type: ignore[method-assign]

        assert await mgr.renew("/edev/1/sub/2") is False
        await asyncio.sleep(0)

        renewal_post.assert_not_awaited()
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_put_500_neither_posts_nor_reports_lost(self):
        from py20305.client.errors import Sep2ProtocolError

        callback = AsyncMock()
        mgr = _put_manager(
            put=AsyncMock(side_effect=Sep2ProtocolError("Server Error", 500)), callback=callback
        )
        await _subscribed(mgr, "/edev/1/sub/2")
        renewal_post = AsyncMock(return_value="/edev/1/sub/9")
        mgr._client.post = renewal_post  # type: ignore[method-assign]

        assert await mgr.renew("/edev/1/sub/2") is False
        await asyncio.sleep(0)

        renewal_post.assert_not_awaited()
        callback.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_restored_subscription_falls_back_to_the_parent_list(self):
        """A checkpoint written before the list href was recorded still renews."""
        from py20305.client.errors import Sep2ProtocolError

        mgr = _put_manager(put=AsyncMock(side_effect=Sep2ProtocolError("Not Found", 404)))
        mgr.restore_from_checkpoint(
            {
                "subscriptions": [
                    {
                        "subscription_uri": "/edev/1/sub/2",
                        "subscribed_resource": "/edev/1/derp",
                        "notification_uri": "https://localhost:10443/notify",
                        "resource_type": "DERProgramList",
                    }
                ]
            }
        )

        assert await mgr.renew("/edev/1/sub/2") is True
        assert mgr._client.post.await_args.args[0] == "/edev/1/sub"

    @pytest.mark.asyncio
    async def test_checkpoint_keeps_the_subscribed_list(self):
        mgr = _put_manager(put=AsyncMock(return_value=204))
        await _subscribed(mgr, "/moved/sub/2")

        restored = _put_manager(put=AsyncMock(return_value=204))
        restored.restore_from_checkpoint(mgr.to_checkpoint())

        assert restored.active_subscriptions[0].sub_list_href == "/edev/1/sub"

    def test_unknown_renewal_method_is_rejected(self):
        with pytest.raises(ValueError, match="renewal_method"):
            SubscriptionManager(
                AsyncMock(),
                "https://localhost:10443/notify",
                renewal_method="patch",  # type: ignore[arg-type]
            )


class TestDuplicateNotification:
    def test_first_notification_not_duplicate(self, mgr: SubscriptionManager):
        assert not mgr.is_duplicate_notification("/derc/1")

    def test_second_within_window_is_duplicate(self, mgr: SubscriptionManager):
        mgr.is_duplicate_notification("/derc/1")
        assert mgr.is_duplicate_notification("/derc/1")

    def test_different_resources_not_duplicate(self, mgr: SubscriptionManager):
        mgr.is_duplicate_notification("/derc/1")
        assert not mgr.is_duplicate_notification("/derc/2")


class TestNotificationAncestryDedup:
    """Tests for hierarchical dedup after targeted fetch."""

    def test_ancestry_suppresses_program_scope_only(self, mgr: SubscriptionManager):
        """After targeted fetch of /edev/1/derp/3/derc, suppress /derp/3 and /derp.

        Higher ancestors (/edev/1, /edev) are independent EndDeviceList scope
        and must NOT be suppressed.
        """
        mgr.record_notification_ancestry("/edev/1/derp/3/derc")
        assert mgr.is_duplicate_notification("/edev/1/derp/3")
        assert mgr.is_duplicate_notification("/edev/1/derp")
        # /edev and /edev/1 are NOT suppressed -- they represent EndDeviceList
        # changes that fire alongside /derc bursts during mid-test structural
        # mutations (BASIC-003 remove_derp + add_fsa). Suppressing them swallows
        # legitimate structural-change rediscovery triggers.
        assert not mgr.is_duplicate_notification("/edev/1")
        assert not mgr.is_duplicate_notification("/edev")

    def test_ancestry_does_not_cross_fsa_boundary(self, mgr: SubscriptionManager):
        """FSA and EndDeviceList ancestors of a /derc path are independent events.

        When the server fires notifications during a structural mutation burst,
        /edev/1/fsa/N/derp/M/derc fires alongside /edev/1/fsa and /edev. The
        FSA/EndDevice notifications represent structural changes that require
        full rediscovery; they must NOT be suppressed by the targeted-fetch
        ancestry walk just because a /derc in the same FSA was just processed.
        """
        mgr.record_notification_ancestry("/edev/1/fsa/4/derp/1/derc")
        # Program-scope ancestors are suppressed (same-event ripples)
        assert mgr.is_duplicate_notification("/edev/1/fsa/4/derp/1")
        assert mgr.is_duplicate_notification("/edev/1/fsa/4/derp")
        # FSA and EndDevice ancestors are NOT suppressed
        assert not mgr.is_duplicate_notification("/edev/1/fsa/4")
        assert not mgr.is_duplicate_notification("/edev/1/fsa")
        assert not mgr.is_duplicate_notification("/edev/1")
        assert not mgr.is_duplicate_notification("/edev")

    def test_ancestry_does_not_suppress_siblings(self, mgr: SubscriptionManager):
        """Ancestry only suppresses parents, not sibling control lists."""
        mgr.record_notification_ancestry("/edev/1/derp/3/derc")
        assert not mgr.is_duplicate_notification("/edev/1/derp/4/derc")

    def test_ancestry_does_not_suppress_self(self, mgr: SubscriptionManager):
        """The resource itself is not recorded by ancestry (already in dedup from normal path)."""
        mgr.record_notification_ancestry("/edev/1/derp/3/derc")
        # The exact path is NOT recorded by ancestry -- that's the normal dedup's job
        assert not mgr.is_duplicate_notification("/edev/1/derp/3/derc")

    def test_no_ancestry_means_parent_not_suppressed(self, mgr: SubscriptionManager):
        """Without targeted fetch, parent notifications fire normally."""
        mgr.is_duplicate_notification("/edev/1/derp/3/derc")
        # Parent is a different path, not suppressed
        assert not mgr.is_duplicate_notification("/edev/1/derp")


class TestNotificationFiltering:
    def test_list_subscription_processes_all(self, mgr: SubscriptionManager):
        # Manually add a list subscription
        mgr._subscriptions["/sub/1"] = SubscriptionState(
            subscription_uri="/sub/1",
            subscribed_resource="/edev/1/derc",
            notification_uri="https://localhost/notify",
            resource_type="DERControlList",
            is_list_subscription=True,
        )
        assert mgr.should_process_notification("/sub/1", "/edev/1/derc/42")

    def test_non_list_rejects_different_resource(self, mgr: SubscriptionManager):
        mgr._subscriptions["/sub/1"] = SubscriptionState(
            subscription_uri="/sub/1",
            subscribed_resource="/edev/1/dderc",
            notification_uri="https://localhost/notify",
            resource_type="DefaultDERControl",
            is_list_subscription=False,
        )
        assert not mgr.should_process_notification("/sub/1", "/edev/1/derc/list")

    def test_non_list_accepts_same_resource(self, mgr: SubscriptionManager):
        mgr._subscriptions["/sub/1"] = SubscriptionState(
            subscription_uri="/sub/1",
            subscribed_resource="/edev/1/dderc",
            notification_uri="https://localhost/notify",
            resource_type="DefaultDERControl",
            is_list_subscription=False,
        )
        assert mgr.should_process_notification("/sub/1", "/edev/1/dderc")

    def test_unknown_subscription_processes(self, mgr: SubscriptionManager):
        assert mgr.should_process_notification("/unknown", "/any/resource")


class TestIsListSubscription:
    @pytest.mark.asyncio
    async def test_list_resource_detected(self, mgr: SubscriptionManager):
        state = await mgr.subscribe("/edev/1/sub", "/edev/1/derc", "DERControlList")
        assert state is not None
        assert state.is_list_subscription is True

    @pytest.mark.asyncio
    async def test_non_list_resource_detected(self, mgr: SubscriptionManager):
        state = await mgr.subscribe("/edev/1/sub", "/edev/1/dderc", "DefaultDERControl")
        assert state is not None
        assert state.is_list_subscription is False
