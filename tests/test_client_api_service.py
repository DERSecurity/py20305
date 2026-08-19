"""Unit tests for ClientAPIService — the client-level API service layer."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305.api.service import ClientAPIService


def _make_mock_client() -> MagicMock:
    """Create a mock CsipClient for testing."""
    client = MagicMock()
    client.state.end_devices = {}
    client.state.der_programs = {}
    client.state.poll_rates = {}
    client.http.server_alive = True
    client.http.last_error = None
    client.http.host = "10.0.0.1"
    client.http.last_contact_epoch = None
    client.http.consecutive_failures = 0
    client.trigger_rediscovery = AsyncMock()
    client.poll_now = AsyncMock(return_value=0)
    client.http.reset_session = AsyncMock()
    client.http.update_ca_trust = MagicMock()
    client.http.update_client_cert = MagicMock()
    # No event processor by default
    del client._event_processor
    return client


@pytest.fixture
def mock_client() -> MagicMock:
    return _make_mock_client()


@pytest.fixture
def mock_telemetry() -> MagicMock:
    telemetry = MagicMock()
    telemetry.get_all_posted_log_events.return_value = []
    telemetry.find_device_with_log_events.return_value = None
    return telemetry


@pytest.fixture
def service(mock_client: MagicMock, mock_telemetry: MagicMock) -> ClientAPIService:
    return ClientAPIService(client=mock_client, telemetry=mock_telemetry)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestClientStatus:
    def test_status_basic(self, service: ClientAPIService) -> None:
        result = service.get_status()
        assert result["server_alive"] is True
        assert result["server_host"] == "10.0.0.1"
        assert result["status"] == "running"
        assert result["devices_discovered"] == 0
        assert result["programs_discovered"] == 0
        assert result["lfdi"] is None

    def test_status_has_no_mode(self, service: ClientAPIService) -> None:
        """Client-level status must not include 'mode' — that's aggregator-only."""
        result = service.get_status()
        assert "mode" not in result

    def test_status_with_device(self, mock_client: MagicMock) -> None:
        from dataclasses import dataclass

        @dataclass
        class MockEdev:
            lfdi: bytes

        mock_client.state.end_devices = {"/edev/1": MockEdev(lfdi=b"\xab\xcd")}
        service = ClientAPIService(client=mock_client)
        result = service.get_status()
        assert result["devices_discovered"] == 1
        assert result["lfdi"] == "abcd"

    def test_status_server_dead(self, mock_client: MagicMock) -> None:
        mock_client.http.server_alive = False
        mock_client.http.last_error = "Connection refused"
        service = ClientAPIService(client=mock_client)
        result = service.get_status()
        assert result["server_alive"] is False
        assert result["last_error"] == "Connection refused"

    def test_status_poll_rates(self, mock_client: MagicMock) -> None:
        mock_client.state.poll_rates = {"dcap": 900, "time": None}
        service = ClientAPIService(client=mock_client)
        result = service.get_status()
        assert result["poll_rates"] == {"dcap": 900}


# ---------------------------------------------------------------------------
# Devices
# ---------------------------------------------------------------------------


class TestClientDevices:
    def test_devices_empty(self, service: ClientAPIService) -> None:
        assert service.get_devices() == {"devices": []}

    def test_devices_all_enddevice(self, mock_client: MagicMock) -> None:
        """Client-level devices should all be EndDevice (no aggregator role)."""
        from dataclasses import dataclass

        @dataclass
        class MockDevice:
            is_aggregator: bool = False

        @dataclass
        class MockEdev:
            device: MockDevice
            href: str
            lfdi: bytes

        mock_client.state.end_devices = {
            "/edev/1": MockEdev(device=MockDevice(), href="/edev/1", lfdi=b"\x11\x22"),
        }
        service = ClientAPIService(client=mock_client)
        result = service.get_devices()
        assert len(result["devices"]) == 1
        assert result["devices"][0]["role"] == "EndDevice"
        assert result["devices"][0]["isAggregator"] is False


# ---------------------------------------------------------------------------
# Log Events
# ---------------------------------------------------------------------------


class TestClientLogEvents:
    def test_get_log_events_disabled(self, mock_client: MagicMock) -> None:
        service = ClientAPIService(client=mock_client, telemetry=None)
        result = service.get_log_events()
        assert result["enabled"] is False
        assert result["events"] == []

    def test_get_log_events_empty(self, service: ClientAPIService) -> None:
        result = service.get_log_events()
        assert result["enabled"] is True
        assert result["events"] == []


@pytest.mark.asyncio
class TestClientTriggerLogEvent:
    async def test_trigger_no_telemetry(self, mock_client: MagicMock) -> None:
        service = ClientAPIService(client=mock_client, telemetry=None)
        result = await service.trigger_log_event()
        assert result["status"] == "error"

    async def test_trigger_no_eligible_device(
        self, service: ClientAPIService, mock_telemetry: MagicMock
    ) -> None:
        mock_telemetry.find_device_with_log_events.return_value = None
        result = await service.trigger_log_event()
        assert result["status"] == "error"

    async def test_trigger_fire_and_forget(
        self, service: ClientAPIService, mock_telemetry: MagicMock
    ) -> None:
        """Client-level trigger should return immediately with 'triggered'."""
        mock_telemetry.find_device_with_log_events.return_value = "abcd1234"
        mock_telemetry.post_log_event_burst = AsyncMock(return_value=5)
        result = await service.trigger_log_event(alarm_status=2)
        assert result["status"] == "triggered"
        assert result["alarm_status"] == 2


# ---------------------------------------------------------------------------
# HTTP Message Logging
# ---------------------------------------------------------------------------


class TestClientHttpMsg:
    def test_get_http_msg_default(self, service: ClientAPIService) -> None:
        result = service.get_http_msg()
        assert result["enabled"] is False
        assert result["last_updated"] is None

    def test_get_http_msg_custom_state(self, mock_client: MagicMock) -> None:
        state: dict[str, Any] = {"enabled": True, "last_updated": "x", "redirect_probe": {}}
        service = ClientAPIService(client=mock_client, http_msg_state=state)
        assert service.get_http_msg() is state


@pytest.mark.asyncio
class TestClientSetHttpMsg:
    async def test_disable(self, service: ClientAPIService) -> None:
        result = await service.set_http_msg(0)
        assert result["enabled"] is False
        assert result["last_updated"] is not None


# ---------------------------------------------------------------------------
# System Operations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestClientSystemOps:
    async def test_trigger_rediscovery(self, service: ClientAPIService) -> None:
        result = await service.trigger_rediscovery()
        assert result["status"] == "ok"
        assert "programs_discovered" in result
        assert "devices_discovered" in result

    async def test_poll_now(self, service: ClientAPIService) -> None:
        result = await service.poll_now()
        assert result["status"] == "ok"
        assert result["programs_polled"] == 0

    async def test_reset_tls_session(self, service: ClientAPIService) -> None:
        result = await service.reset_tls_session()
        assert result["status"] == "ok"

    async def test_update_ca_trust(self, service: ClientAPIService, mock_client: MagicMock) -> None:
        result = await service.update_ca_trust("/tmp/ca.pem")
        assert result["status"] == "ok"
        assert result["ca_cert"] == "/tmp/ca.pem"
        mock_client.http.update_ca_trust.assert_called_once_with("/tmp/ca.pem")
        mock_client.http.reset_session.assert_called()

    async def test_update_ca_trust_dispatched_to_loop(self, mock_client: MagicMock) -> None:
        """Both the sync mutation and async reset must run on the bridge loop."""
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)

        import concurrent.futures

        fut = concurrent.futures.Future()
        fut.set_result(None)

        with patch(
            "py20305.api.service.asyncio.run_coroutine_threadsafe",
            return_value=fut,
        ) as mock_rcts:
            service = ClientAPIService(client=mock_client, loop=mock_loop)
            await service.update_ca_trust("/tmp/ca.pem")
            # Both mutation and reset are bundled in a single dispatched coroutine
            mock_rcts.assert_called_once()
            assert mock_rcts.call_args[0][1] is mock_loop

    async def test_set_cert_type(self, service: ClientAPIService, mock_client: MagicMock) -> None:
        result = await service.set_cert_type("/tmp/cert.pem", "/tmp/key.pem")
        assert result["status"] == "ok"
        mock_client.http.update_client_cert.assert_called_once_with("/tmp/cert.pem", "/tmp/key.pem")
        mock_client.http.reset_session.assert_called()

    async def test_set_cert_type_dispatched_to_loop(self, mock_client: MagicMock) -> None:
        """Both the sync mutation and async reset must run on the bridge loop."""
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)

        import concurrent.futures

        fut = concurrent.futures.Future()
        fut.set_result(None)

        with patch(
            "py20305.api.service.asyncio.run_coroutine_threadsafe",
            return_value=fut,
        ) as mock_rcts:
            service = ClientAPIService(client=mock_client, loop=mock_loop)
            await service.set_cert_type("/tmp/cert.pem", "/tmp/key.pem")
            mock_rcts.assert_called_once()
            assert mock_rcts.call_args[0][1] is mock_loop


# ---------------------------------------------------------------------------
# Controls / Events / Responses (delegate to client._event_processor)
# ---------------------------------------------------------------------------


class TestClientControls:
    def test_derc_controls_no_processor(self, service: ClientAPIService) -> None:
        result = service.get_derc_controls()
        assert result["derc_controls"]["dderc_dict"] == {}
        assert result["derc_controls"]["active_events"] == {}

    def test_events_no_processor(self, service: ClientAPIService) -> None:
        result = service.get_events()
        for key in ("active", "scheduled", "completed", "cancelled", "superseded"):
            assert key in result["events"]
            assert result["events"][key] == {}

    def test_responses_no_processor(self, service: ClientAPIService) -> None:
        result = service.get_responses()
        assert result == {"responses": {}}

    def test_programs_empty(self, service: ClientAPIService) -> None:
        result = service.get_der_programs()
        assert result == {"programs": []}

    def test_cert_type(self, service: ClientAPIService) -> None:
        result = service.get_cert_type()
        assert result["cert_type"] == "client"


# ---------------------------------------------------------------------------
# _run_on_loop dispatching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRunOnLoop:
    async def test_no_loop_awaits_directly(self, mock_client: MagicMock) -> None:
        """Without a loop, _run_on_loop should await the coroutine directly."""
        service = ClientAPIService(client=mock_client, loop=None)

        async def _dummy() -> str:
            return "direct"

        result = await service._run_on_loop(_dummy())
        assert result == "direct"

    async def test_with_loop_uses_run_coroutine_threadsafe(self, mock_client: MagicMock) -> None:
        """With a loop, _run_on_loop should dispatch via run_coroutine_threadsafe."""
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)

        async def _dummy() -> str:
            return "threadsafe"

        # Create a real concurrent.futures.Future that resolves immediately
        import concurrent.futures

        fut = concurrent.futures.Future()
        fut.set_result("threadsafe")

        with patch(
            "py20305.api.service.asyncio.run_coroutine_threadsafe",
            return_value=fut,
        ) as mock_rcts:
            service = ClientAPIService(client=mock_client, loop=mock_loop)
            result = await service._run_on_loop(_dummy())

            mock_rcts.assert_called_once()
            # First arg is the coroutine, second is the loop
            assert mock_rcts.call_args[0][1] is mock_loop
            assert result == "threadsafe"

    async def test_system_ops_dispatch_through_run_on_loop(self, mock_client: MagicMock) -> None:
        """Verify that system operations use _run_on_loop for dispatching."""
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)

        import concurrent.futures

        fut = concurrent.futures.Future()
        fut.set_result(None)
        mock_client.trigger_rediscovery = AsyncMock(return_value=None)

        with patch(
            "py20305.api.service.asyncio.run_coroutine_threadsafe",
            return_value=fut,
        ) as mock_rcts:
            service = ClientAPIService(client=mock_client, loop=mock_loop)
            await service.trigger_rediscovery()
            mock_rcts.assert_called_once()


# ---------------------------------------------------------------------------
# trigger_log_event branching (loop vs no-loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTriggerLogEventBranches:
    async def test_trigger_with_loop_uses_run_coroutine_threadsafe(
        self, mock_client: MagicMock, mock_telemetry: MagicMock
    ) -> None:
        """When loop is set, trigger_log_event dispatches burst via run_coroutine_threadsafe."""
        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mock_telemetry.find_device_with_log_events.return_value = "abcd1234abcd1234"
        mock_telemetry.post_log_event_burst = AsyncMock(return_value=5)

        with patch("py20305.api.service.asyncio.run_coroutine_threadsafe") as mock_rcts:
            service = ClientAPIService(client=mock_client, telemetry=mock_telemetry, loop=mock_loop)
            result = await service.trigger_log_event(alarm_status=3)

            assert result["status"] == "triggered"
            assert result["alarm_status"] == 3
            mock_rcts.assert_called_once()
            assert mock_rcts.call_args[0][1] is mock_loop

    async def test_trigger_without_loop_creates_task(
        self, mock_client: MagicMock, mock_telemetry: MagicMock
    ) -> None:
        """When no loop is set, trigger_log_event creates a background task."""
        mock_telemetry.find_device_with_log_events.return_value = "abcd1234abcd1234"
        mock_telemetry.post_log_event_burst = AsyncMock(return_value=5)

        service = ClientAPIService(client=mock_client, telemetry=mock_telemetry, loop=None)
        result = await service.trigger_log_event(alarm_status=1)

        assert result["status"] == "triggered"
        # A background task should have been created and tracked
        assert len(service._background_tasks) == 1
        # Let the task complete
        task = next(iter(service._background_tasks))
        await task
        # After completion, the done callback removes it
        assert len(service._background_tasks) == 0
