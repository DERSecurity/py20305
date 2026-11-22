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
from py20305.models.sep.sep import Subscription, SubscriptionList
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


def _record(
    uri: str, resource: str, rtype: str = "FSAList", created_at: float = 100.0
) -> SubscriptionState:
    return SubscriptionState(
        subscription_uri=uri,
        subscribed_resource=resource,
        notification_uri=NOTIFY,
        resource_type=rtype,
        created_at=created_at,
        sub_list_href=SUB_LIST,
    )


def _sub_list(*entries: tuple[str, ...]) -> list[SubscriptionList]:
    """Return one SubscriptionList page of ``(href, resource[, notificationURI])`` entries."""
    subs = [
        Subscription(
            href=entry[0],
            subscribed_resource=entry[1],
            encoding=0,
            level="+S1",
            limit=1,
            notification_uri=entry[2] if len(entry) > 2 else NOTIFY,
        )
        for entry in entries
    ]
    return [SubscriptionList(subscription=subs, all=len(subs), results=len(subs))]


class TestManagerThatDoesNotMaintainTheSet:
    async def test_it_sends_no_delete_or_renewal_and_removes_no_record(
        self, store: SubscriptionStore
    ):
        store.put(_record("/edev/1/sub/1", "/edev/1/fsa"))
        store.put(_record("/edev/1/sub/2", "/derp/1/derc", "DERControlList"))
        http = _http()
        http.get_list = AsyncMock(return_value=_sub_list())
        mgr = SubscriptionManager(http, NOTIFY, store=store, maintains_set=False)

        assert not await mgr.cancel("/edev/1/sub/1")
        await mgr.cancel_all()
        assert not await mgr.renew("/edev/1/sub/1")
        assert await mgr.renew_all() == 0
        assert await mgr.validate_restored_subscriptions() == (0, 0)
        await mgr.reconcile(SUB_LIST, {("/edev/1/fsa", "FSAList")})
        assert await mgr.reconcile_with_server(SUB_LIST) == 0

        http.delete.assert_not_awaited()
        http.put.assert_not_awaited()
        http.post.assert_not_awaited()
        http.get_list.assert_not_awaited()
        assert sorted(s.subscription_uri for s in store.values()) == [
            "/edev/1/sub/1",
            "/edev/1/sub/2",
        ]

    async def test_adoption_records_subscriptions_without_renewing_or_deleting(
        self, store: SubscriptionStore
    ):
        http = _http()
        http.get_list = AsyncMock(
            return_value=_sub_list(
                ("/edev/1/sub/4", "/edev/1/fsa"), ("/edev/1/sub/5", "/edev/1/fsa")
            )
        )
        mgr = SubscriptionManager(
            http,
            NOTIFY,
            store=store,
            renewal_method="put",
            adopt_on_connect=True,
            remove_duplicates=True,
            maintains_set=False,
        )

        result = await mgr.adopt_existing(
            SUB_LIST, {("/edev/1/fsa", "FSAList")}, remove_duplicates=True
        )

        assert result.adopted == 1
        assert result.removed == 0
        http.delete.assert_not_awaited()
        http.put.assert_not_awaited()
        assert [s.subscription_uri for s in store.values()] == ["/edev/1/sub/4"]


class TestOtherClientsSubscriptions:
    async def test_reconciliation_never_rekeys_to_another_clients_subscription(
        self, store: SubscriptionStore
    ):
        other = "https://other.example.com/notify"
        listed = _sub_list(
            ("/edev/1/sub/1", "/edev/1/fsa"), ("/edev/1/sub/9", "/edev/1/fsa", other)
        )
        http = _http()
        http.get_list = AsyncMock(return_value=listed)
        mgr = SubscriptionManager(http, NOTIFY, store=store)

        await mgr.adopt_existing(SUB_LIST, {("/edev/1/fsa", "FSAList")})
        await mgr.reconcile_with_server(SUB_LIST, reestablish_missing=False)
        await mgr.cancel_all()

        deleted = [call.args[0] for call in http.delete.await_args_list]
        assert deleted == ["/edev/1/sub/1"]


class TestSharedReconciliation:
    async def test_disjoint_desired_sets_cancel_nothing_without_cancel_unwanted(
        self, store: SubscriptionStore
    ):
        http_a, http_b = _http(), _http()
        http_a.post.return_value = "/edev/1/sub/1"
        http_b.post.return_value = "/edev/1/sub/2"
        mgr_a = SubscriptionManager(http_a, NOTIFY, store=store)
        mgr_b = SubscriptionManager(http_b, NOTIFY, store=store)
        await mgr_a.subscribe(SUB_LIST, "/derp/1/derc", "DERControlList")
        await mgr_b.subscribe(SUB_LIST, "/derp/2/derc", "DERControlList")

        await mgr_b.reconcile(
            SUB_LIST, {("/derp/2/derc", "DERControlList")}, renew_kept=False, cancel_unwanted=False
        )
        await mgr_a.reconcile(
            SUB_LIST, {("/derp/1/derc", "DERControlList")}, renew_kept=False, cancel_unwanted=False
        )

        http_a.delete.assert_not_awaited()
        http_b.delete.assert_not_awaited()
        assert sorted(s.subscription_uri for s in store.values()) == [
            "/edev/1/sub/1",
            "/edev/1/sub/2",
        ]

    async def test_a_status_1_cancellation_stops_every_manager_from_subscribing_again(
        self, store: SubscriptionStore
    ):
        http_a, http_b = _http(), _http()
        http_a.post.return_value = "/edev/1/sub/1"
        mgr_a = SubscriptionManager(http_a, NOTIFY, store=store)
        mgr_b = SubscriptionManager(http_b, NOTIFY, store=store, maintains_set=False)
        await mgr_a.subscribe(SUB_LIST, "/edev/1/fsa", "FSAList")

        mgr_a.mark_cancelled("/edev/1/sub/1")
        mgr_a.suppress_resubscribe("/edev/1/fsa")
        await mgr_a.reconcile(SUB_LIST, {("/edev/1/fsa", "FSAList")}, renew_kept=False)
        await mgr_b.reconcile(SUB_LIST, {("/edev/1/fsa", "FSAList")}, renew_kept=False)

        assert mgr_b.resubscribe_suppressed("/edev/1/fsa")
        http_b.post.assert_not_awaited()
        assert http_a.post.await_count == 1

        mgr_a.clear_resubscribe_suppression()

        assert not mgr_b.resubscribe_suppressed("/edev/1/fsa")

    @pytest.mark.parametrize("method", ["post", "put"])
    async def test_a_cancellation_made_while_a_renewal_is_in_flight_is_kept(
        self, store: SubscriptionStore, method: str
    ):
        store.put(_record("/edev/1/sub/1", "/edev/1/fsa"))
        other = SubscriptionManager(_http(), NOTIFY, store=store)
        http = _http()

        async def cancelled_meanwhile(*_args: object) -> str:
            other.mark_cancelled("/edev/1/sub/1")
            return "/edev/1/sub/9"

        http.post.side_effect = cancelled_meanwhile
        http.put.side_effect = cancelled_meanwhile
        mgr = SubscriptionManager(http, NOTIFY, store=store, renewal_method=method)

        assert not await mgr.renew("/edev/1/sub/1")

        record = store.get("/edev/1/sub/1")
        assert record is not None
        assert record.status == "cancelled"
        assert store.get("/edev/1/sub/9") is None

    async def test_a_record_created_while_the_list_is_read_is_not_dropped(
        self, store: SubscriptionStore
    ):
        store.put(_record("/edev/1/sub/1", "/edev/1/fsa"))
        http = _http()

        async def subscribed_meanwhile(*_args: object) -> list[SubscriptionList]:
            store.put(_record("/edev/1/sub/2", "/derp/1/derc", "DERControlList"))
            return _sub_list(("/edev/1/sub/1", "/edev/1/fsa"))

        http.get_list = AsyncMock(side_effect=subscribed_meanwhile)
        mgr = SubscriptionManager(http, NOTIFY, store=store)

        await mgr.reconcile_with_server(SUB_LIST, reestablish_missing=False)

        assert store.get("/edev/1/sub/2") is not None

    async def test_a_record_for_a_resource_outside_the_given_resources_is_not_dropped(
        self, store: SubscriptionStore
    ):
        store.put(_record("/edev/1/sub/1", "/edev/1/fsa"))
        store.put(_record("/edev/1/sub/2", "/derp/2/derc", "DERControlList"))
        http = _http()
        http.get_list = AsyncMock(return_value=_sub_list(("/edev/1/sub/1", "/edev/1/fsa")))
        mgr = SubscriptionManager(http, NOTIFY, store=store)

        await mgr.reconcile_with_server(
            SUB_LIST, reestablish_missing=False, resources={"/edev/1/fsa"}
        )

        assert store.get("/edev/1/sub/2") is not None


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
