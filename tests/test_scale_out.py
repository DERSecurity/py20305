"""Tests for running one client as several processes.

Each ``CsipClient`` stands for one process. The processes share a certificate,
so the loopback server sees one client: one EndDeviceList, one
SubscriptionList. The host splits the EndDevices between the processes with a
device filter, gives one of them the client's own-EndDevice work, and shares
one subscription store between them.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from py20305.client.csip_client import CsipClient
from py20305.client.errors import Sep2ConnectionError
from py20305.client.http import Sep2Client
from py20305.client.request_budget import RequestLimiter
from py20305.client.scope import DeviceSet
from py20305.models.sep.sep import DeviceCapability
from py20305.subscription.manager import SubscriptionManager, SubscriptionState
from py20305.subscription.notification_server import NotificationServer
from py20305.subscription.store import MemorySubscriptionStore
from py20305.telemetry.coordinator import DeviceRoleConfig, TelemetryCoordinator
from py20305.telemetry.mup import DeviceRole
from tests.scale_out_server import (
    NOTIFY,
    SUB_LIST,
    FakeTreeServer,
    lfdi_for,
    notification_xml,
)


async def _serve(aiohttp_server, fake: FakeTreeServer) -> str:
    server = await aiohttp_server(fake.app())
    return str(server.make_url("")).rstrip("/")


def _process(
    base_url: str,
    store: MemorySubscriptionStore,
    devices: list[int] | None,
    *,
    own: bool,
) -> CsipClient:
    """Build one process's client acting for EndDevices ``devices`` (None is all)."""
    device_filter = (
        DeviceSet(hrefs=[f"/edev/{i}" for i in devices]) if devices is not None else None
    )
    client = CsipClient(
        base_url,
        reconcile_enabled=False,
        device_filter=device_filter,
        own_end_device=own,
    )
    client.attach_subscriptions(SubscriptionManager(client.http, NOTIFY, store=store))
    return client


class TestDeviceFilter:
    async def test_discovery_walks_only_the_end_devices_the_filter_accepts(self, aiohttp_server):
        fake = FakeTreeServer(devices=3)
        base_url = await _serve(aiohttp_server, fake)
        client = CsipClient(base_url, device_filter=DeviceSet(hrefs=["/edev/1"]))
        try:
            await client.connect()

            assert list(client.state.end_devices) == ["/edev/1"]
            assert list(client.state.der_programs) == ["/derp/1"]
            assert fake.gets("/edev/0/fsa") == 0
            assert fake.gets("/edev/2/fsa") == 0
            assert fake.gets("/edev/1/fsa") == 1
        finally:
            await client.shutdown()

    async def test_a_device_set_accepts_an_end_device_by_lfdi(self, aiohttp_server):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        client = CsipClient(base_url, device_filter=DeviceSet(lfdis=[lfdi_for(1).upper()]))
        try:
            await client.connect()

            assert list(client.state.end_devices) == ["/edev/1"]
        finally:
            await client.shutdown()

    async def test_the_subscription_list_of_a_filtered_out_end_device_is_still_used(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        client = _process(base_url, MemorySubscriptionStore(), [1], own=False)
        try:
            await client.connect()

            assert client.state.subscription_list_href == SUB_LIST
            assert "/derp/1/derc" in fake.sub_posts
        finally:
            await client.shutdown()


class TestSharedSubscriptions:
    async def test_two_processes_subscribe_to_each_resource_once(self, aiohttp_server):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, [1], own=False)
        try:
            await a.connect()
            await b.connect()

            assert sorted(fake.sub_posts) == [
                "/derp/0/derc",
                "/derp/1/derc",
                "/edev",
                "/edev/0/fsa",
                "/edev/1/fsa",
            ]
        finally:
            await b.shutdown()
            await a.shutdown()

    async def test_a_filtered_rediscovery_cancels_no_subscription_of_another_process(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, [1], own=False)
        try:
            await a.connect()
            await b.connect()

            assert await b.trigger_rediscovery()

            assert fake.sub_deletes == []
            assert len(store.values()) == 5
        finally:
            await b.shutdown()
            await a.shutdown()

    async def test_only_the_process_with_own_end_device_work_renews_and_unsubscribes(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, [1], own=False)
        await a.connect()
        await b.connect()

        assert a._renewal_task is not None
        assert b._renewal_task is None

        await b.shutdown()
        assert fake.sub_deletes == []
        await a.shutdown()
        assert len(fake.sub_deletes) == 5

    async def test_a_process_without_own_end_device_work_sends_no_delete_when_asked_to(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, [1], own=False)
        try:
            await a.connect()
            await b.connect()

            await b.shutdown(unsubscribe=True)

            assert fake.sub_deletes == []
            assert len(store.values()) == 5
        finally:
            await a.shutdown(unsubscribe=False)

    async def test_a_process_without_own_end_device_work_or_a_filter_cancels_nothing(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, None, own=False)
        try:
            await a.connect()
            await b.connect()
            # A subscription another process needs that this one does not want.
            fake.subscriptions[f"{SUB_LIST}/99"] = "/derp/9/derc"
            store.put(
                SubscriptionState(
                    subscription_uri=f"{SUB_LIST}/99",
                    subscribed_resource="/derp/9/derc",
                    notification_uri=NOTIFY,
                    resource_type="DERControlList",
                    sub_list_href=SUB_LIST,
                )
            )

            assert await b.trigger_rediscovery()

            assert fake.sub_deletes == []
            assert store.get(f"{SUB_LIST}/99") is not None
        finally:
            await b.shutdown()
            await a.shutdown(unsubscribe=False)

    async def test_a_process_without_own_end_device_work_adopts_without_side_effects(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2)
        # Two subscriptions of ours for one resource, left by an earlier instance.
        fake.subscriptions = {f"{SUB_LIST}/50": "/edev", f"{SUB_LIST}/51": "/edev"}
        fake.next_sub = 52
        base_url = await _serve(aiohttp_server, fake)
        client = CsipClient(
            base_url,
            reconcile_enabled=False,
            device_filter=DeviceSet(hrefs=["/edev/1"]),
            own_end_device=False,
        )
        client.attach_subscriptions(
            SubscriptionManager(
                client.http,
                NOTIFY,
                store=MemorySubscriptionStore(),
                renewal_method="put",
                adopt_on_connect=True,
                remove_duplicates=True,
            )
        )
        try:
            await client.connect()

            assert fake.sub_deletes == []
            assert fake.sub_puts == []
            assert "/edev" not in fake.sub_posts
        finally:
            await client.shutdown()


class TestOwnEndDeviceWork:
    async def test_a_process_without_own_end_device_work_cannot_register_an_end_device(self):
        client = CsipClient("https://server.example.com", own_end_device=False)
        try:
            with pytest.raises(RuntimeError, match="own-EndDevice"):
                await client.register_end_device(lfdi=lfdi_for(5))
        finally:
            await client.shutdown()

    @pytest.mark.parametrize(("own", "checked"), [(True, 1), (False, 0)])
    async def test_comms_loss_recovery_reregisters_only_with_own_end_device_work(
        self, own: bool, checked: int
    ):
        client = CsipClient("https://server.example.com", own_end_device=own)
        client._own_lfdi = lfdi_for(0)
        client._server_lists_end_device = AsyncMock(return_value=True)  # type: ignore[method-assign]
        client.trigger_rediscovery = AsyncMock(return_value=True)  # type: ignore[method-assign]
        try:
            await client._recover_from_comms_loss()

            assert client._server_lists_end_device.await_count == checked
        finally:
            await client.shutdown()

    def test_telemetry_skips_the_own_lfdi_without_own_end_device_work(self):
        own, other = lfdi_for(0), lfdi_for(1)
        client = MagicMock()
        client.own_end_device = False
        client.http.client_lfdi = own
        coordinator = TelemetryCoordinator(
            client, lfdis=[own, other], connector_resolver=MagicMock()
        )
        started: list[str] = []
        coordinator._start_der_resources_for_device = (  # type: ignore[method-assign]
            lambda lfdi, _rate: started.append(lfdi)
        )
        coordinator._device_post_rate = lambda _lfdi: 300  # type: ignore[method-assign]

        coordinator.start_device_telemetry()

        assert started == [other]

    def test_telemetry_skips_a_site_meter_mirrored_under_the_own_end_device(self):
        own, meter = lfdi_for(0), lfdi_for(1)
        client = MagicMock()
        client.own_end_device = False
        client.http.client_lfdi = own
        coordinator = TelemetryCoordinator(
            client,
            lfdis=[meter],
            connector_resolver=MagicMock(),
            roles={meter: DeviceRoleConfig(role=DeviceRole.SITE_METER)},
        )
        coordinator._telemetry = MagicMock()
        coordinator._device_post_rate = lambda _lfdi: 300  # type: ignore[method-assign]

        coordinator.start_device_telemetry()

        coordinator._telemetry.start_metering.assert_not_called()


class TestProcessNotification:
    async def test_a_notification_for_another_processes_device_is_ignored(self, aiohttp_server):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, [1], own=False)
        try:
            await a.connect()
            await b.connect()
            sub = {s.subscribed_resource: s.subscription_uri for s in store.values()}
            before = fake.gets("/derp/0/derc")

            acted = await b.process_notification(
                notification_xml("/derp/0/derc", sub["/derp/0/derc"])
            )

            assert acted is False
            assert fake.gets("/derp/0/derc") == before
        finally:
            await b.shutdown()
            await a.shutdown()

    async def test_a_notification_for_this_processes_device_is_fetched(self, aiohttp_server):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, [1], own=False)
        try:
            await a.connect()
            await b.connect()
            sub = {s.subscribed_resource: s.subscription_uri for s in store.values()}
            before = fake.gets("/derp/1/derc")

            acted = await b.process_notification(
                notification_xml("/derp/1/derc", sub["/derp/1/derc"])
            )

            assert acted is True
            assert fake.gets("/derp/1/derc") == before + 1
        finally:
            await b.shutdown()
            await a.shutdown()

    async def test_a_failed_rediscovery_is_raised_and_the_notification_can_be_retried(
        self, aiohttp_server, monkeypatch: pytest.MonkeyPatch
    ):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        a = _process(base_url, store, [0], own=True)
        b = _process(base_url, store, [1], own=False)
        try:
            await a.connect()
            await b.connect()
            sub = {s.subscribed_resource: s.subscription_uri for s in store.values()}
            body = notification_xml("/edev/1/fsa", sub["/edev/1/fsa"])
            discover = AsyncMock(side_effect=Sep2ConnectionError("server unreachable"))
            monkeypatch.setattr("py20305.client.csip_client.discover", discover)

            with pytest.raises(Sep2ConnectionError):
                await b.process_notification(body)

            monkeypatch.undo()
            before = fake.gets("/edev/1/fsa")

            assert await b.process_notification(body) is True
            assert fake.gets("/edev/1/fsa") > before
        finally:
            await b.shutdown()
            await a.shutdown()

    async def test_a_deletion_of_another_processes_device_during_rediscovery_is_ignored(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        store = MemorySubscriptionStore()
        removed: list[str] = []

        async def on_removed(href: str) -> None:
            removed.append(href)

        b = CsipClient(
            base_url,
            reconcile_enabled=False,
            device_filter=DeviceSet(hrefs=["/edev/1"]),
            own_end_device=False,
            on_device_removed=on_removed,
        )
        b.attach_subscriptions(SubscriptionManager(b.http, NOTIFY, store=store))
        try:
            await b.connect()
            fake.hold = "/edev/1/fsa"
            rediscovery = asyncio.ensure_future(b.trigger_rediscovery())
            await fake.reached.wait()

            acted = await b.process_notification(
                notification_xml("/edev/0", f"{SUB_LIST}/1", status=4)
            )
            fake.release.set()
            await rediscovery

            assert acted is False
            assert removed == []
        finally:
            fake.release.set()
            await b.shutdown()

    async def test_an_invalid_body_is_rejected(self):
        client = CsipClient("https://server.example.com")
        client.attach_subscriptions(SubscriptionManager(client.http, NOTIFY))
        try:
            with pytest.raises(ValueError, match="Notification"):
                await client.process_notification(b"<not-a-notification/>")
        finally:
            await client.shutdown()


class TestNotificationForwarding:
    @staticmethod
    async def _listener(server: NotificationServer) -> TestClient:
        app = web.Application()
        app.router.add_post("/notify", server._handle_notify)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    async def test_a_forwarded_notification_is_answered_after_the_host_takes_it(self):
        taken: list[bytes] = []
        release = asyncio.Event()
        processed = AsyncMock()

        async def forward(body: bytes) -> None:
            await release.wait()
            taken.append(body)

        server = NotificationServer(
            host="127.0.0.1", port=0, tls=None, on_notification=processed, forward=forward
        )
        http = await self._listener(server)
        body = notification_xml("/derp/1/derc", "/edev/0/sub/1")
        try:
            post = asyncio.ensure_future(http.post("/notify", data=body))
            await asyncio.sleep(0.05)
            assert not post.done()

            release.set()
            resp = await post

            assert resp.status == 201
            assert taken == [body]
            processed.assert_not_awaited()
        finally:
            await http.close()

    async def test_a_notification_the_host_cannot_take_is_answered_with_an_error(self):
        async def forward(_body: bytes) -> None:
            raise RuntimeError("queue unavailable")

        server = NotificationServer(host="127.0.0.1", port=0, tls=None, forward=forward)
        http = await self._listener(server)
        try:
            resp = await http.post("/notify", data=notification_xml("/edev", "/edev/0/sub/1"))

            assert resp.status == 503
        finally:
            await http.close()


class CountingLimiter:
    """A request limiter that counts acquisitions and can hold requests back."""

    def __init__(self) -> None:
        self.acquired = 0
        self.open = asyncio.Event()
        self.open.set()

    async def acquire(self) -> None:
        await self.open.wait()
        self.acquired += 1


class TestRequestLimiter:
    async def test_every_request_to_the_server_acquires_the_limiter(self, aiohttp_server):
        fake = FakeTreeServer(devices=2)
        base_url = await _serve(aiohttp_server, fake)
        limiter = CountingLimiter()
        client = CsipClient(base_url, request_limiter=limiter)
        try:
            await client.connect()

            assert isinstance(limiter, RequestLimiter)
            assert limiter.acquired == len(fake.requests) > 0
        finally:
            await client.shutdown()

    async def test_a_request_waits_until_the_limiter_lets_it_go(self, aiohttp_server):
        fake = FakeTreeServer(devices=1)
        base_url = await _serve(aiohttp_server, fake)
        limiter = CountingLimiter()
        limiter.open.clear()

        async with Sep2Client(base_url, request_limiter=limiter) as http:
            get = asyncio.ensure_future(http.get("/dcap", DeviceCapability))
            await asyncio.sleep(0.05)
            assert fake.requests == []

            limiter.open.set()
            await get

        assert fake.requests == [("GET", "/dcap")]

    @pytest.mark.parametrize("error", [OSError("store unavailable"), TimeoutError("no slot")])
    async def test_a_limiter_failure_reaches_the_caller_unchanged(
        self, aiohttp_server, error: Exception
    ):
        fake = FakeTreeServer(devices=1)
        base_url = await _serve(aiohttp_server, fake)
        limiter = MagicMock()
        limiter.acquire = AsyncMock(side_effect=error)

        async with Sep2Client(base_url, request_limiter=limiter) as http:
            alive = http.server_alive
            with pytest.raises(type(error)) as raised:
                await http.get("/dcap", DeviceCapability)
            with pytest.raises(type(error)):
                await http.delete("/edev/0/sub/1")
            with pytest.raises(type(error)):
                await http.get_raw(f"{base_url}/dcap")

            assert raised.value is error
            assert limiter.acquire.await_count == 3
            assert http.server_alive is alive
            assert http.last_error is None

        assert fake.requests == []

    async def test_without_a_limiter_requests_go_straight_out(self, aiohttp_server):
        fake = FakeTreeServer(devices=1)
        base_url = await _serve(aiohttp_server, fake)
        client = CsipClient(base_url)
        try:
            await client.connect()

            assert client.http.request_limiter is None
            assert fake.gets("/dcap") == 1
        finally:
            await client.shutdown()
