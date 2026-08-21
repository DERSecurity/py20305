"""Tests for the client-level router factory (create_client_router).

Verifies:
- Routes work when service_getter returns a ClientAPIService
- Routes degrade gracefully when service_getter returns None
- Aggregator-only endpoints are NOT registered on the client router
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from py20305.api.client_routes import create_client_router
from py20305.api.service import ClientAPIService
from py20305.client.timebase import ServerTimebase


def _make_test_app(service: ClientAPIService | None) -> FastAPI:
    """Build a minimal FastAPI app with the client router."""
    app = FastAPI()
    router = create_client_router(service_getter=lambda: service)
    app.include_router(router, prefix="/api/v1")
    return app


def _make_service() -> ClientAPIService:
    """Build a ClientAPIService with a fully mocked CsipClient."""
    client = MagicMock()
    client.state.end_devices = {}
    client.state.der_programs = {}
    client.state.poll_rates = {}
    client.state.tariff_profiles = {}
    client.http.server_alive = True
    client.http.last_error = None
    client.http.host = "10.0.0.1"
    client.http.last_contact_epoch = None
    client.http.consecutive_failures = 0
    client.trigger_rediscovery = AsyncMock()
    client.poll_now = AsyncMock(return_value=0)
    client.http.reset_session = AsyncMock()
    # The probe path awaits this; a bare MagicMock is not awaitable.
    client.http.get_raw = AsyncMock(return_value={"status_code": 0, "error": "not wired"})
    client.http.update_ca_trust = MagicMock()
    client.http.update_client_cert = MagicMock()
    del client._event_processor

    telemetry = MagicMock()
    telemetry.get_all_posted_log_events.return_value = []
    telemetry.find_device_with_log_events.return_value = None

    return ClientAPIService(client=client, telemetry=telemetry)


@pytest.fixture
def connected_client() -> TestClient:
    """Client router with a connected service."""
    return TestClient(_make_test_app(_make_service()))


@pytest.fixture
def disconnected_client() -> TestClient:
    """Client router with service_getter returning None."""
    return TestClient(_make_test_app(None))


# ---------------------------------------------------------------------------
# Connected service — verify response shapes
# ---------------------------------------------------------------------------


class TestConnectedRoutes:
    def test_status(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "running"
        assert data["server_alive"] is True
        assert data["server_host"] == "10.0.0.1"
        assert "mode" not in data

    def test_devices(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/devices")
        assert r.status_code == 200
        assert r.json() == {"devices": []}

    def test_programs(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/programs")
        assert r.status_code == 200
        assert r.json() == {"programs": []}

    def test_tariffs(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/tariffs")
        assert r.status_code == 200
        assert r.json() == {"profiles": []}

    def test_derc_controls(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/derc_controls")
        assert r.status_code == 200
        assert "derc_controls" in r.json()

    def test_events(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/events")
        assert r.status_code == 200
        for key in ("active", "scheduled", "completed", "cancelled", "superseded"):
            assert key in r.json()["events"]

    def test_responses(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/responses")
        assert r.status_code == 200
        assert r.json() == {"responses": {}}

    def test_logevents_get(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/logevents")
        assert r.status_code == 200
        data = r.json()
        assert "events" in data
        assert "enabled" in data

    def test_logevents_post(self, connected_client: TestClient) -> None:
        r = connected_client.post("/api/v1/logevents", json={})
        assert r.status_code == 200
        assert "status" in r.json()

    def test_httpmsg_get(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/httpmsg")
        assert r.status_code == 200
        data = r.json()
        assert "enabled" in data

    def test_httpmsg_post(self, connected_client: TestClient) -> None:
        r = connected_client.post("/api/v1/httpmsg", json={"enabled": 0})
        assert r.status_code == 200

    def test_certtype_get(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/certtype")
        assert r.status_code == 200
        assert "cert_type" in r.json()

    def test_certtype_post(self, connected_client: TestClient) -> None:
        r = connected_client.post("/api/v1/certtype")
        assert r.status_code == 200

    def test_rediscover(self, connected_client: TestClient) -> None:
        r = connected_client.post("/api/v1/rediscover")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_poll_now(self, connected_client: TestClient) -> None:
        r = connected_client.post("/api/v1/poll-now")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_tls_reset(self, connected_client: TestClient) -> None:
        r = connected_client.post("/api/v1/tls-reset")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_tls_ca(self, connected_client: TestClient) -> None:
        r = connected_client.post("/api/v1/tls-ca", json={"ca_cert": "/tmp/ca.pem"})
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["ca_cert"] == "/tmp/ca.pem"

    def test_diagnostics(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/diagnostics/messages")
        assert r.status_code == 200
        for key in ("errors", "warnings", "info"):
            assert key in r.json()

    def test_alarms(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/alarms")
        assert r.status_code == 200
        assert r.json() == []


# ---------------------------------------------------------------------------
# Disconnected service — graceful degradation
# ---------------------------------------------------------------------------


class TestDisconnectedRoutes:
    def test_status(self, disconnected_client: TestClient) -> None:
        r = disconnected_client.get("/api/v1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "not_connected"
        assert data["server_alive"] is False

    def test_devices(self, disconnected_client: TestClient) -> None:
        r = disconnected_client.get("/api/v1/devices")
        assert r.json() == {"devices": []}

    def test_tariffs(self, disconnected_client: TestClient) -> None:
        r = disconnected_client.get("/api/v1/tariffs")
        assert r.status_code == 200
        assert r.json() == {"profiles": []}

    def test_logevents_post(self, disconnected_client: TestClient) -> None:
        r = disconnected_client.post("/api/v1/logevents", json={})
        assert r.json()["status"] == "error"

    def test_rediscover(self, disconnected_client: TestClient) -> None:
        r = disconnected_client.post("/api/v1/rediscover")
        assert r.json()["status"] == "error"

    def test_tls_reset(self, disconnected_client: TestClient) -> None:
        r = disconnected_client.post("/api/v1/tls-reset")
        assert r.json()["status"] == "error"


# ---------------------------------------------------------------------------
# Aggregator-only endpoints must NOT exist on client router
# ---------------------------------------------------------------------------


class TestAggregatorRoutesAbsent:
    """Verify that aggregator-only endpoints are NOT on the client router."""

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/state",
            "/api/v1/config",
            "/api/v1/groups",
            "/api/v1/connectors",
            "/api/v1/device-connectors",
            # /subscriptions and /notifications are deliberately NOT here:
            # subscribe/notify is a client conformance feature, and the
            # certification suite drives a client DUT through both.
            "/api/v1/server-info",
        ],
    )
    def test_aggregator_endpoint_not_found(self, connected_client: TestClient, path: str) -> None:
        r = connected_client.get(path)
        # FastAPI returns 404 for unregistered routes
        assert r.status_code == 404, f"{path} should not be registered on client router"


# ---------------------------------------------------------------------------
# Subscribe/notify surface -- what the certification suite drives a DUT with
# ---------------------------------------------------------------------------


class TestSubscriptionRoutes:
    """The conformance suite's DutActions read these two endpoints."""

    def test_subscriptions_shape(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/subscriptions")
        assert r.status_code == 200
        body = r.json()
        assert "subscriptions" in body and isinstance(body["subscriptions"], list)

    def test_notifications_shape(self, connected_client: TestClient) -> None:
        r = connected_client.get("/api/v1/notifications")
        assert r.status_code == 200
        body = r.json()
        assert "notifications" in body and isinstance(body["notifications"], list)

    def test_subscription_entries_carry_the_fields_the_suite_reads(self) -> None:
        """Field names are the contract; the suite indexes them by name."""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from py20305.api.service import ClientAPIService

        sub = SimpleNamespace(
            subscription_uri="/edev/1/sub/1",
            subscribed_resource="/edev/1/fsa",
            notification_uri="https://dut:10443/notify",
            resource_type="FSAList",
            status="active",
            created_at=123.0,
        )
        notif = SimpleNamespace(
            subscribed_resource="/edev/1/fsa",
            status=0,
            subscription_uri="/edev/1/sub/1",
            new_resource_uri=None,
            created_at=124.0,
        )
        client = MagicMock()
        client.subscription_manager.active_subscriptions = [sub]
        client.subscription_manager.notifications = [notif]
        service = ClientAPIService(client)

        s = service.get_subscriptions()["subscriptions"][0]
        assert s == {
            "subscription_uri": "/edev/1/sub/1",
            "subscribed_resource": "/edev/1/fsa",
            "notification_uri": "https://dut:10443/notify",
            "resource_type": "FSAList",
            "status": "active",
            "created_at": 123.0,
        }
        n = service.get_notifications()["notifications"][0]
        assert n["new_resource_uri"] is None
        assert n["subscribed_resource"] == "/edev/1/fsa"

    def test_no_manager_means_empty_lists_not_errors(self) -> None:
        from unittest.mock import MagicMock

        from py20305.api.service import ClientAPIService

        client = MagicMock()
        client.subscription_manager = None
        service = ClientAPIService(client)
        assert service.get_subscriptions() == {"subscriptions": []}
        assert service.get_notifications() == {"notifications": []}


# ---------------------------------------------------------------------------
# HTTP-to-HTTPS redirect probe -- the ERR-001 instrumentation call
# ---------------------------------------------------------------------------


class TestHttpProbe:
    """ERR-001 asserts on these exact fields; their names are the contract."""

    @staticmethod
    def _service_with(responses):
        from unittest.mock import AsyncMock, MagicMock

        from py20305.api.service import ClientAPIService

        client = MagicMock()
        client.http.host = "server.example.com"
        client.http.get_raw = AsyncMock(side_effect=responses)
        service = ClientAPIService(client)

        # No event loop handoff in tests: await the coroutine inline.
        async def run_inline(coro):
            return await coro

        service._run_on_loop = run_inline
        return service, client

    @pytest.mark.asyncio
    async def test_redirect_followed_end_to_end(self):
        service, client = self._service_with([
            {"status_code": 301, "headers": {"Location": "https://server.example.com:8443/dcap"}},
            {
                "status_code": 200,
                "body": "<DeviceCapability><EndDeviceListLink/></DeviceCapability>" * 20,
                "content_type": "application/sep+xml",
            },
        ])
        result = await service.http_probe()

        assert result["http_response"]["status_code"] == 301
        assert result["http_response"]["location"].startswith("https://")
        assert result["redirect_followed"] is True
        assert result["https_response"]["status_code"] == 200
        assert "DeviceCapability" in result["https_response"]["body_excerpt"]
        assert len(result["https_response"]["body_excerpt"]) <= 500
        assert "sep+xml" in result["https_response"]["content_type"]
        # The HTTP leg went to the configured host and port 80, path /dcap.
        first_url = client.http.get_raw.await_args_list[0].args[0]
        assert first_url == "http://server.example.com:80/dcap"

    @pytest.mark.asyncio
    async def test_location_header_is_case_insensitive(self):
        service, _ = self._service_with([
            {"status_code": 302, "headers": {"location": "https://server.example.com:8443/dcap"}},
            {"status_code": 200, "body": "<DeviceCapability/>", "content_type": "xml"},
        ])
        result = await service.http_probe()
        assert result["redirect_followed"] is True

    @pytest.mark.asyncio
    async def test_no_redirect_is_reported_not_followed(self):
        """A server answering 200 on plain HTTP is the failure ERR-001 exists to catch."""
        service, _ = self._service_with([
            {"status_code": 200, "headers": {}, "body": "<DeviceCapability/>"},
        ])
        result = await service.http_probe()
        assert result["redirect_followed"] is False
        assert result["https_response"] is None
        assert result["http_response"]["status_code"] == 200

    @pytest.mark.asyncio
    async def test_transport_error_surfaces_as_error(self):
        service, _ = self._service_with([
            {"status_code": 0, "error": "connection refused"},
        ])
        result = await service.http_probe()
        assert "error" in result

    def test_route_registered(self, connected_client: TestClient) -> None:
        # 200 regardless of outcome: the endpoint reports what happened.
        r = connected_client.post("/api/v1/proxy/http-probe", json={"path": "/dcap"})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def _observed_time_client(*, observed: int = 1_030, at: float = 1_000.0) -> TestClient:
    """Client router whose service has seen one Time resource."""
    service = _make_service()
    tb = ServerTimebase(drift_warn_seconds=0)
    with patch("time.time", return_value=at):
        tb.observe(observed, quality=3, href="/tm")
    service._client.http.timebase = tb
    service._client.state.time = None
    return TestClient(_make_test_app(service))


class TestTimeRoute:
    def test_json_when_observed(self) -> None:
        client = _observed_time_client()
        with patch("time.time", return_value=1_000.0):
            r = client.get("/api/v1/time")
        assert r.status_code == 200
        data = r.json()
        assert data["source"] == "server"
        assert data["current_time"] == 1_030

    def test_text_format_is_a_bare_integer(self) -> None:
        """The whole point of the text variant: nothing to parse."""
        client = _observed_time_client()
        with patch("time.time", return_value=1_000.0):
            r = client.get("/api/v1/time?format=text")
        assert r.status_code == 200
        assert r.text == "1030"
        assert int(r.text) == 1_030

    def test_accept_header_selects_text(self) -> None:
        client = _observed_time_client()
        with patch("time.time", return_value=1_000.0):
            r = client.get("/api/v1/time", headers={"accept": "text/plain"})
        assert r.status_code == 200
        assert r.text == "1030"

    def test_unavailable_is_503_not_a_local_clock_reading(self) -> None:
        """The failure a consumer cannot detect for itself is being handed the
        unsynchronized local clock as though it were server time."""
        service = _make_service()
        service._client.http.timebase = ServerTimebase(drift_warn_seconds=0)
        service._client.state.time = None
        client = TestClient(_make_test_app(service))

        r = client.get("/api/v1/time")
        assert r.status_code == 503
        assert r.json()["source"] == "unavailable"
        assert r.json()["current_time"] is None

    def test_text_variant_emits_no_number_when_unavailable(self) -> None:
        """A consumer doing int(response.text) must not get a plausible number
        off the unsynchronized clock."""
        service = _make_service()
        service._client.http.timebase = ServerTimebase(drift_warn_seconds=0)
        service._client.state.time = None
        client = TestClient(_make_test_app(service))

        r = client.get("/api/v1/time?format=text")
        assert r.status_code == 503
        assert r.text == "unavailable"
        with pytest.raises(ValueError):
            int(r.text)

    def test_disconnected_is_503(self, disconnected_client: TestClient) -> None:
        r = disconnected_client.get("/api/v1/time")
        assert r.status_code == 503
        assert r.json()["source"] == "unavailable"

    def test_registered_in_openapi(self, connected_client: TestClient) -> None:
        schema = connected_client.get("/openapi.json").json()
        assert "/api/v1/time" in schema["paths"]
        assert "503" in schema["paths"]["/api/v1/time"]["get"]["responses"]

    def test_accept_header_is_case_insensitive(self) -> None:
        """Media types are case-insensitive (RFC 9110 8.3.1). A caller sending
        `Text/Plain` is asking for the text variant, and handing it JSON breaks
        exactly the constrained consumer the variant exists for."""
        client = _observed_time_client()
        with patch("time.time", return_value=1_000.0):
            r = client.get("/api/v1/time", headers={"accept": "Text/Plain"})
        assert r.status_code == 200
        assert r.text == "1030"
        assert int(r.text) == 1_030

    def test_a_clock_reading_is_never_cacheable(self) -> None:
        """A cached time is wrong in the one way the consumer cannot detect."""
        client = _observed_time_client()
        with patch("time.time", return_value=1_000.0):
            json_r = client.get("/api/v1/time")
            text_r = client.get("/api/v1/time?format=text")
        assert json_r.headers["cache-control"] == "no-store"
        assert text_r.headers["cache-control"] == "no-store"

    def test_unavailable_response_is_also_uncacheable(self) -> None:
        service = _make_service()
        service._client.http.timebase = ServerTimebase(drift_warn_seconds=0)
        service._client.state.time = None
        client = TestClient(_make_test_app(service))

        r = client.get("/api/v1/time")
        assert r.status_code == 503
        assert r.headers["cache-control"] == "no-store"

    def test_openapi_describes_both_media_types(self, connected_client: TestClient) -> None:
        """`response_model` cannot express a two-media-type route, so without an
        explicit `content` block the text variant is undiscoverable in the
        schema and the JSON body is untyped."""
        responses = connected_client.get("/openapi.json").json()["paths"]["/api/v1/time"]["get"][
            "responses"
        ]
        for code in ("200", "503"):
            content = responses[code]["content"]
            assert "text/plain" in content
            props = content["application/json"]["schema"]["properties"]
            assert "current_time" in props
            assert "dst_active" in props
