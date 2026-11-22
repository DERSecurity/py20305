"""Tests for sharing one subscription set between several subscription managers.

Each manager stands for one client process. The processes share a certificate,
so the server sees one EndDevice with one SubscriptionList, and a host gives
every process's manager the same store. ``CopyingStore`` stands for a store
backed by a database: it hands out copies, so a change a manager makes reaches
the other processes only if the manager writes it back.
"""

from __future__ import annotations

import copy
from unittest.mock import AsyncMock

import pytest

from py20305.client.errors import Sep2ProtocolError
from py20305.subscription.manager import SubscriptionManager, SubscriptionState
from py20305.subscription.store import (
    MemorySubscriptionStore,
    SubscriptionStore,
    subscription_from_record,
    subscription_record,
)

NOTIFY = "https://host.example.com:10443/notify"
SUB_LIST = "/edev/1/sub"


class CopyingStore:
    """A subscription store that returns and keeps copies, as a database would."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def get(self, subscription_uri: str) -> SubscriptionState | None:
        row = self.rows.get(subscription_uri)
        return subscription_from_record(copy.deepcopy(row)) if row is not None else None

    def put(self, state: SubscriptionState) -> None:
        self.rows[state.subscription_uri] = subscription_record(state)

    def remove(self, subscription_uri: str) -> None:
        self.rows.pop(subscription_uri, None)

    def values(self) -> list[SubscriptionState]:
        return [subscription_from_record(copy.deepcopy(row)) for row in self.rows.values()]


def _http() -> AsyncMock:
    http = AsyncMock()
    http.post = AsyncMock(return_value=None)
    http.put = AsyncMock(return_value=204)
    http.delete = AsyncMock(return_value=204)
    return http


@pytest.fixture(params=["memory", "copying"])
def store(request: pytest.FixtureRequest) -> SubscriptionStore:
    return MemorySubscriptionStore() if request.param == "memory" else CopyingStore()


class TestSharedSubscriptionSet:
    async def test_a_resource_subscribed_by_one_process_is_not_posted_by_another(
        self, store: SubscriptionStore
    ):
        http_a, http_b = _http(), _http()
        http_a.post.return_value = "/edev/1/sub/1"
        mgr_a = SubscriptionManager(http_a, NOTIFY, store=store)
        mgr_b = SubscriptionManager(http_b, NOTIFY, store=store)

        await mgr_a.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")
        state = await mgr_b.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")

        http_b.post.assert_not_awaited()
        assert state is not None
        assert state.subscription_uri == "/edev/1/sub/1"

    async def test_a_notification_is_classified_by_a_subscription_another_process_created(
        self, store: SubscriptionStore
    ):
        http_a = _http()
        http_a.post.return_value = "/edev/1/sub/1"
        mgr_a = SubscriptionManager(http_a, NOTIFY, store=store)
        mgr_b = SubscriptionManager(_http(), NOTIFY, store=store)

        await mgr_a.subscribe(SUB_LIST, "/derp/1/derc", "DERControlList")

        assert mgr_b.resource_type_for("/edev/1/sub/1") == "DERControlList"

    async def test_a_server_cancellation_recorded_by_one_process_is_seen_by_another(
        self, store: SubscriptionStore
    ):
        http_a = _http()
        http_a.post.return_value = "/edev/1/sub/1"
        mgr_a = SubscriptionManager(http_a, NOTIFY, store=store)
        mgr_b = SubscriptionManager(_http(), NOTIFY, store=store)
        await mgr_a.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")

        mgr_b.mark_cancelled("/edev/1/sub/1")

        assert mgr_a.active_subscriptions == []

    async def test_a_renewal_time_is_written_back_to_the_store(self, store: SubscriptionStore):
        http = _http()
        http.post.return_value = "/edev/1/sub/1"
        mgr = SubscriptionManager(http, NOTIFY, renewal_method="put", store=store)
        await mgr.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")
        stale = store.get("/edev/1/sub/1")
        assert stale is not None
        stale.created_at = 0.0
        store.put(stale)

        assert await mgr.renew("/edev/1/sub/1")

        renewed = store.get("/edev/1/sub/1")
        assert renewed is not None
        assert renewed.created_at > 0.0

    async def test_a_new_href_from_a_post_renewal_reaches_the_other_process(
        self, store: SubscriptionStore
    ):
        http_a = _http()
        http_a.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/7"]
        mgr_a = SubscriptionManager(http_a, NOTIFY, store=store)
        mgr_b = SubscriptionManager(_http(), NOTIFY, store=store)
        await mgr_a.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")

        assert await mgr_a.renew("/edev/1/sub/1")

        assert [s.subscription_uri for s in mgr_b.active_subscriptions] == ["/edev/1/sub/7"]

    async def test_a_put_renewal_that_creates_a_new_subscription_replaces_the_old_record(
        self, store: SubscriptionStore
    ):
        http = _http()
        http.post.side_effect = ["/edev/1/sub/1", "/edev/1/sub/8"]
        http.put.side_effect = Sep2ProtocolError("gone", 404)
        mgr = SubscriptionManager(http, NOTIFY, renewal_method="put", store=store)
        await mgr.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")

        assert await mgr.renew("/edev/1/sub/1")

        assert [s.subscription_uri for s in store.values()] == ["/edev/1/sub/8"]

    async def test_a_cancel_by_one_process_removes_the_record_for_all(
        self, store: SubscriptionStore
    ):
        http_a = _http()
        http_a.post.return_value = "/edev/1/sub/1"
        mgr_a = SubscriptionManager(http_a, NOTIFY, store=store)
        mgr_b = SubscriptionManager(_http(), NOTIFY, store=store)
        await mgr_a.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")

        await mgr_b.cancel("/edev/1/sub/1")

        assert store.values() == []
        assert mgr_a.active_subscriptions == []


class TestStoreFormat:
    def test_a_record_round_trips_through_the_checkpoint_format(self):
        state = SubscriptionState(
            subscription_uri="/edev/1/sub/1",
            subscribed_resource="/edev/1/fsa",
            notification_uri=NOTIFY,
            resource_type="FSAList",
            created_at=1234.5,
            is_list_subscription=True,
            sub_list_href=SUB_LIST,
        )

        assert subscription_from_record(subscription_record(state)) == state

    def test_a_checkpoint_restores_into_the_given_store(self):
        store = CopyingStore()
        mgr = SubscriptionManager(_http(), NOTIFY, store=store)

        mgr.restore_from_checkpoint(
            {
                "subscriptions": [
                    {
                        "subscription_uri": "/edev/1/sub/1",
                        "subscribed_resource": "/edev/1/fsa",
                        "notification_uri": NOTIFY,
                        "resource_type": "FSAList",
                    }
                ]
            }
        )

        assert list(store.rows) == ["/edev/1/sub/1"]

    def test_the_default_store_keeps_records_in_this_process(self):
        mgr = SubscriptionManager(_http(), NOTIFY)

        assert isinstance(mgr.store, MemorySubscriptionStore)
        assert isinstance(mgr.store, SubscriptionStore)
