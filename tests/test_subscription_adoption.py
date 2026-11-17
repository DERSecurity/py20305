"""Tests for adopting subscriptions the server already holds for this client.

A client that takes over from another instance of itself shares its
certificate (one EndDevice, one SubscriptionList) and its notificationURI.
These tests run the real transport against a loopback server that holds
pre-existing subscriptions: ours, duplicates of ours, and another client's.
The SubscriptionList pages it serves are written out by hand.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from py20305.client.csip_client import CsipClient
from py20305.client.http import Sep2Client
from py20305.client.state import EndDeviceState
from py20305.subscription.manager import SubscriptionManager

NS = "urn:ieee:std:2030.5:ns"
OURS = "https://client.example.com:10443/notify"
OTHER = "https://other.example.com:10443/notify"
SUB_LIST = "/edev/1/sub"
FSA = "/edev/1/fsa"
DERC = "/derp/1/derc"
DDERC = "/derp/1/dderc"


class FakeSubscriptionServer:
    """A SubscriptionList on a loopback server: GET (paged), POST, PUT and DELETE.

    ``page_size`` caps every page below the client's requested limit, so a
    list of more than ``page_size`` entries is read across several GETs.
    """

    def __init__(
        self,
        subscriptions: list[tuple[str, str, str]],
        *,
        page_size: int = 2,
        list_status: int | None = None,
        list_body: bytes | None = None,
    ) -> None:
        # href -> (subscribedResource, notificationURI), in href order.
        self.subscriptions: dict[str, tuple[str, str]] = {
            href: (resource, uri) for href, resource, uri in subscriptions
        }
        self.page_size = page_size
        self.list_status = list_status
        self.list_body = list_body
        self.next_id = 100
        self.list_gets = 0
        self.posts: list[tuple[str, str]] = []
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def _page(self, start: int, limit: int) -> bytes:
        hrefs = sorted(self.subscriptions)
        page = hrefs[start : start + min(limit, self.page_size)]
        entries = "".join(
            f'<Subscription href="{href}">'
            f"<subscribedResource>{self.subscriptions[href][0]}</subscribedResource>"
            "<encoding>0</encoding><level>+S1</level><limit>1</limit>"
            f"<notificationURI>{self.subscriptions[href][1]}</notificationURI>"
            "</Subscription>"
            for href in page
        )
        return (
            f'<SubscriptionList xmlns="{NS}" href="{SUB_LIST}" '
            f'all="{len(hrefs)}" results="{len(page)}">{entries}</SubscriptionList>'
        ).encode()

    async def _get_list(self, request: web.Request) -> web.Response:
        self.list_gets += 1
        if self.list_status is not None:
            return web.Response(status=self.list_status)
        if self.list_body is not None:
            return web.Response(body=self.list_body, content_type="application/sep+xml")
        start = int(request.query.get("s", "0"))
        limit = int(request.query.get("l", "1"))
        return web.Response(body=self._page(start, limit), content_type="application/sep+xml")

    async def _post(self, request: web.Request) -> web.Response:
        root = ET.fromstring(await request.read())
        resource = root.findtext(f"{{{NS}}}subscribedResource") or ""
        uri = root.findtext(f"{{{NS}}}notificationURI") or ""
        self.posts.append((resource, uri))
        href = f"{SUB_LIST}/{self.next_id}"
        self.next_id += 1
        self.subscriptions[href] = (resource, uri)
        return web.Response(status=201, headers={"Location": href})

    async def _put(self, request: web.Request) -> web.Response:
        self.puts.append(request.path)
        return web.Response(status=204)

    async def _delete(self, request: web.Request) -> web.Response:
        self.deletes.append(request.path)
        if self.subscriptions.pop(request.path, None) is None:
            return web.Response(status=404)
        return web.Response(status=204)

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(SUB_LIST, self._get_list)
        app.router.add_post(SUB_LIST, self._post)
        app.router.add_put(SUB_LIST + "/{id}", self._put)
        app.router.add_delete(SUB_LIST + "/{id}", self._delete)
        return app


async def _serve(aiohttp_server, fake: FakeSubscriptionServer) -> str:
    server = await aiohttp_server(fake.app())
    return str(server.make_url("")).rstrip("/")


# Ours for the FSAList and DERControlList, a duplicate of ours for the FSAList,
# another client's for the FSAList, and ours for a resource the client no
# longer subscribes to. Five entries at two to a page: three GETs.
TAKEOVER_LIST = [
    ("/edev/1/sub/1", FSA, OURS),
    ("/edev/1/sub/2", DERC, OURS),
    ("/edev/1/sub/3", FSA, OURS),
    ("/edev/1/sub/4", FSA, OTHER),
    ("/edev/1/sub/5", "/derp/9/derc", OURS),
]
DESIRED = {(FSA, "FSAList"), (DERC, "DERControlList")}


class TestAdoptExisting:
    async def test_adopted_resources_are_not_subscribed_again(self, aiohttp_server):
        fake = FakeSubscriptionServer(TAKEOVER_LIST)
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            result = await mgr.adopt_existing(SUB_LIST, DESIRED)
            await mgr.subscribe(SUB_LIST, FSA, "FSAList")
            await mgr.subscribe(SUB_LIST, DERC, "DERControlList")

        assert result.listed is True
        assert result.adopted == 2
        assert result.duplicates == 1
        assert fake.list_gets == 3
        assert fake.posts == []
        assert {(s.subscription_uri, s.subscribed_resource) for s in mgr.active_subscriptions} == {
            ("/edev/1/sub/1", FSA),
            ("/edev/1/sub/2", DERC),
        }

    async def test_a_resource_with_no_subscription_of_ours_is_still_posted(self, aiohttp_server):
        fake = FakeSubscriptionServer(TAKEOVER_LIST)
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            await mgr.adopt_existing(SUB_LIST, DESIRED | {(DDERC, "DefaultDERControl")})
            await mgr.subscribe(SUB_LIST, DDERC, "DefaultDERControl")

        assert fake.posts == [(DDERC, OURS)]

    async def test_duplicates_are_left_in_place_unless_removal_is_asked_for(self, aiohttp_server):
        fake = FakeSubscriptionServer(TAKEOVER_LIST)
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            result = await mgr.adopt_existing(SUB_LIST, DESIRED)

        assert result.removed == 0
        assert fake.deletes == []
        assert len(fake.subscriptions) == 5

    async def test_remove_duplicates_deletes_only_our_extra_copies(self, aiohttp_server):
        fake = FakeSubscriptionServer(TAKEOVER_LIST)
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            result = await mgr.adopt_existing(SUB_LIST, DESIRED, remove_duplicates=True)

        assert result.removed == 1
        assert result.remove_errors == 0
        assert fake.deletes == ["/edev/1/sub/3"]
        # Another client's subscription and ours for an undesired resource remain.
        assert sorted(fake.subscriptions) == [
            "/edev/1/sub/1",
            "/edev/1/sub/2",
            "/edev/1/sub/4",
            "/edev/1/sub/5",
        ]

    async def test_a_duplicate_on_a_later_page_is_found(self, aiohttp_server):
        fake = FakeSubscriptionServer(
            [
                ("/edev/1/sub/1", FSA, OURS),
                ("/edev/1/sub/2", DERC, OURS),
                ("/edev/1/sub/3", FSA, OTHER),
                ("/edev/1/sub/4", DERC, OTHER),
                ("/edev/1/sub/5", FSA, OURS),
            ],
            page_size=1,
        )
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            result = await mgr.adopt_existing(SUB_LIST, DESIRED, remove_duplicates=True)

        assert fake.list_gets == 5
        assert result.adopted == 2
        assert fake.deletes == ["/edev/1/sub/5"]

    async def test_another_clients_subscription_is_never_adopted_or_removed(self, aiohttp_server):
        fake = FakeSubscriptionServer(
            [("/edev/1/sub/1", FSA, OTHER), ("/edev/1/sub/2", FSA, OTHER)]
        )
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            result = await mgr.adopt_existing(SUB_LIST, DESIRED, remove_duplicates=True)
            await mgr.subscribe(SUB_LIST, FSA, "FSAList")

        assert result.adopted == 0
        assert result.duplicates == 0
        assert fake.deletes == []
        assert fake.posts == [(FSA, OURS)]

    async def test_a_tracked_subscription_is_kept_and_other_copies_removed(self, aiohttp_server):
        fake = FakeSubscriptionServer([("/edev/1/sub/1", FSA, OURS), ("/edev/1/sub/3", FSA, OURS)])
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            mgr.restore_from_checkpoint(
                {
                    "subscriptions": [
                        {
                            "subscription_uri": "/edev/1/sub/3",
                            "subscribed_resource": FSA,
                            "notification_uri": OURS,
                            "resource_type": "FSAList",
                        }
                    ]
                }
            )
            result = await mgr.adopt_existing(SUB_LIST, DESIRED, remove_duplicates=True)

        assert result.adopted == 0
        assert fake.deletes == ["/edev/1/sub/1"]
        assert [s.subscription_uri for s in mgr.active_subscriptions] == ["/edev/1/sub/3"]

    @pytest.mark.parametrize("status", [400, 405, 500])
    async def test_a_server_that_does_not_list_subscriptions_falls_back_to_posting(
        self, aiohttp_server, status
    ):
        fake = FakeSubscriptionServer([("/edev/1/sub/1", FSA, OURS)], list_status=status)
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            result = await mgr.adopt_existing(SUB_LIST, DESIRED, remove_duplicates=True)
            await mgr.subscribe(SUB_LIST, FSA, "FSAList")

        assert result.listed is False
        assert result.adopted == 0
        assert fake.deletes == []
        assert fake.posts == [(FSA, OURS)]

    async def test_a_malformed_subscription_list_falls_back_to_posting(self, aiohttp_server):
        fake = FakeSubscriptionServer(
            [("/edev/1/sub/1", FSA, OURS)], list_body=b"<SubscriptionList"
        )
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS)
            result = await mgr.adopt_existing(SUB_LIST, DESIRED)
            await mgr.subscribe(SUB_LIST, FSA, "FSAList")

        assert result.listed is False
        assert fake.posts == [(FSA, OURS)]

    async def test_put_renewal_renews_an_adopted_subscription_at_once(self, aiohttp_server):
        fake = FakeSubscriptionServer([("/edev/1/sub/1", FSA, OURS)])
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS, renewal_method="put")
            await mgr.adopt_existing(SUB_LIST, DESIRED)

        assert fake.puts == ["/edev/1/sub/1"]
        assert fake.posts == []

    async def test_post_renewal_leaves_an_adopted_subscription_for_the_renewal_task(
        self, aiohttp_server
    ):
        # A re-POST to a server that does not apply the renewal rule would
        # create the duplicate that adoption avoids.
        fake = FakeSubscriptionServer([("/edev/1/sub/1", FSA, OURS)])
        base_url = await _serve(aiohttp_server, fake)

        async with Sep2Client(base_url) as http:
            mgr = SubscriptionManager(http, OURS, renewal_method="post")
            await mgr.adopt_existing(SUB_LIST, DESIRED)

        assert fake.posts == []
        assert fake.puts == []


class TestManagerSettings:
    def test_remove_duplicates_without_adoption_is_rejected(self):
        with pytest.raises(ValueError, match="remove_duplicates"):
            SubscriptionManager(AsyncMock(), OURS, remove_duplicates=True)

    def test_unsubscribe_on_close_is_on_by_default(self):
        assert SubscriptionManager(AsyncMock(), OURS).unsubscribe_on_close is True


def _edev_state() -> EndDeviceState:
    device = MagicMock()
    fsa_link = MagicMock()
    fsa_link.href = FSA
    device.function_set_assignments_list_link = fsa_link
    return EndDeviceState(
        device=device,
        href="/edev/1",
        lfdi=b"\xab\xcd",
        subscription_list_href=SUB_LIST,
        fsa_list_subscribable=True,
    )


async def _connect(base_url: str, mgr_kwargs: dict[str, bool]) -> CsipClient:
    client = CsipClient(base_url, reconcile_enabled=False)
    client.attach_subscriptions(SubscriptionManager(client.http, OURS, **mgr_kwargs))

    async def populate_state(_http, state, **_kw):
        state.end_devices["/edev/1"] = _edev_state()

    with patch("py20305.client.csip_client.discover", new_callable=AsyncMock) as mock_disc:
        mock_disc.side_effect = populate_state
        await client.connect()
    return client


class TestConnect:
    async def test_connect_with_adoption_creates_no_subscription_twice(self, aiohttp_server):
        fake = FakeSubscriptionServer(
            [
                ("/edev/1/sub/1", FSA, OURS),
                ("/edev/1/sub/2", FSA, OURS),
                ("/edev/1/sub/3", FSA, OTHER),
            ]
        )
        base_url = await _serve(aiohttp_server, fake)

        client = await _connect(base_url, {"adopt_on_connect": True, "remove_duplicates": True})
        try:
            assert fake.posts == []
            assert fake.deletes == ["/edev/1/sub/2"]
            assert sorted(fake.subscriptions) == ["/edev/1/sub/1", "/edev/1/sub/3"]
            mgr = client.subscription_manager
            assert mgr is not None
            assert [s.subscription_uri for s in mgr.active_subscriptions] == ["/edev/1/sub/1"]
        finally:
            await client.shutdown(subscription_cancel_timeout=0)

    async def test_connect_without_adoption_posts_a_new_subscription(self, aiohttp_server):
        fake = FakeSubscriptionServer([("/edev/1/sub/1", FSA, OURS)])
        base_url = await _serve(aiohttp_server, fake)

        client = await _connect(base_url, {})
        try:
            assert fake.list_gets == 0
            assert fake.posts == [(FSA, OURS)]
        finally:
            await client.shutdown(subscription_cancel_timeout=0)

    async def test_adopt_subscriptions_can_be_called_after_connect(self, aiohttp_server):
        fake = FakeSubscriptionServer([("/edev/1/sub/1", FSA, OURS)])
        base_url = await _serve(aiohttp_server, fake)

        client = await _connect(base_url, {})
        try:
            # connect() POSTed a second subscription; adoption finds both and
            # keeps the one this instance tracks.
            result = await client.adopt_subscriptions(remove_duplicates=True)
            assert result is not None
            assert result.duplicates == 1
            assert fake.deletes == ["/edev/1/sub/1"]
            assert sorted(fake.subscriptions) == ["/edev/1/sub/100"]
        finally:
            await client.shutdown(subscription_cancel_timeout=0)


class TestShutdown:
    async def test_shutdown_deletes_subscriptions_by_default(self, aiohttp_server):
        fake = FakeSubscriptionServer([])
        base_url = await _serve(aiohttp_server, fake)

        client = await _connect(base_url, {})
        await client.shutdown()

        assert fake.posts == [(FSA, OURS)]
        assert fake.deletes == ["/edev/1/sub/100"]
        assert fake.subscriptions == {}

    async def test_unsubscribe_on_close_off_leaves_subscriptions_on_the_server(
        self, aiohttp_server
    ):
        fake = FakeSubscriptionServer([("/edev/1/sub/1", FSA, OURS)])
        base_url = await _serve(aiohttp_server, fake)

        client = await _connect(base_url, {"adopt_on_connect": True, "unsubscribe_on_close": False})
        await client.shutdown()

        assert fake.deletes == []
        assert sorted(fake.subscriptions) == ["/edev/1/sub/1"]

    async def test_shutdown_unsubscribe_false_overrides_the_manager_default(self, aiohttp_server):
        fake = FakeSubscriptionServer([])
        base_url = await _serve(aiohttp_server, fake)

        client = await _connect(base_url, {})
        await client.shutdown(unsubscribe=False)

        assert fake.deletes == []
        assert sorted(fake.subscriptions) == ["/edev/1/sub/100"]

    async def test_shutdown_unsubscribe_true_overrides_the_manager_setting(self, aiohttp_server):
        fake = FakeSubscriptionServer([])
        base_url = await _serve(aiohttp_server, fake)

        client = await _connect(base_url, {"unsubscribe_on_close": False})
        await client.shutdown(unsubscribe=True)

        assert fake.deletes == ["/edev/1/sub/100"]
