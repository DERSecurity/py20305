"""Tests for the CsipClient lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305.client.csip_client import _RESOURCE_TYPE_TO_POLL_KEY, CsipClient
from py20305.client.errors import (
    Sep2NoContentError,
    Sep2ProtocolError,
    Sep2RedirectError,
)
from py20305.client.state import (
    DerProgramState,
    DiscoveredState,
    EndDeviceState,
    TariffProfileState,
    TariffRateComponentState,
)
from py20305.subscription.manager import SubscriptionManager


@pytest.fixture
def client() -> CsipClient:
    return CsipClient("https://example.com")


@pytest.mark.asyncio
async def test_connect_calls_discover(client: CsipClient):
    with patch(
        "py20305.client.csip_client.discover", new_callable=AsyncMock
    ) as mock_disc:
        await client.connect()
        mock_disc.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_passes_custom_dcap_path():
    """Custom dcap_path is forwarded to discover()."""
    c = CsipClient("https://example.com", dcap_path="/custom/dcap")
    with patch(
        "py20305.client.csip_client.discover", new_callable=AsyncMock
    ) as mock_disc:
        await c.connect()
        mock_disc.assert_awaited_once()
        _, kwargs = mock_disc.call_args
        assert kwargs["dcap_path"] == "/custom/dcap"


@pytest.mark.asyncio
async def test_shutdown_sets_event(client: CsipClient):
    await client.shutdown()
    assert client._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_shutdown_closes_http(client: CsipClient):
    with patch.object(client._http, "close", new_callable=AsyncMock) as mock_close:
        await client.shutdown()
        mock_close.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_blocks_until_shutdown(client: CsipClient):
    """run() should block until shutdown is signaled."""
    with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
        await client.connect()

    done = False

    async def run_client() -> None:
        nonlocal done
        await client.run()
        done = True

    task = asyncio.create_task(run_client())
    await asyncio.sleep(0.05)
    assert not done

    await client.shutdown()
    await asyncio.sleep(0.05)
    assert done
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_404_triggers_rediscovery(client: CsipClient):
    """A 404 during polling should trigger rediscovery."""
    call_count = 0

    with patch(
        "py20305.client.csip_client.discover", new_callable=AsyncMock
    ) as mock_disc:
        # First call succeeds (connect), second call is rediscovery
        await client.connect()

        async def failing_poll() -> None:
            nonlocal call_count
            call_count += 1
            raise Sep2ProtocolError("Not found", 404)

        await client._poll_with_404_recovery(failing_poll)
        # discover called twice: connect + rediscovery
        assert mock_disc.await_count == 2


@pytest.mark.asyncio
async def test_poll_204_is_benign_no_crash(client: CsipClient):
    """A 204 No Content during a poll must not crash the loop or rediscover."""

    async def poll_204() -> None:
        raise Sep2NoContentError("GET /x returned 204 No Content")

    with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
        await client._poll_with_404_recovery(poll_204)  # must not raise
        mock_redisc.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_204_after_sustained_failure_triggers_rediscovery(client: CsipClient):
    """A 204 is a *successful* contact: after >= threshold failures it clears
    the streak and triggers rediscovery, exactly like a normal successful poll
    (otherwise a stale failure count would misreport later recovery)."""

    async def poll_204() -> None:
        raise Sep2NoContentError("GET /x returned 204 No Content")

    with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
        client._poll_failure_count = 3
        await client._poll_with_404_recovery(poll_204)
        mock_redisc.assert_awaited_once()
        assert client._poll_failure_count == 0


@pytest.mark.asyncio
async def test_poll_204_below_threshold_resubscribes(client: CsipClient):
    """A 204 after a brief failure streak clears it and re-checks subscriptions
    (no full rediscovery)."""

    async def poll_204() -> None:
        raise Sep2NoContentError("GET /x returned 204 No Content")

    with (
        patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc,
        patch.object(client, "_auto_subscribe", new_callable=AsyncMock) as mock_auto_sub,
    ):
        client._subscription_manager = MagicMock()
        client._poll_failure_count = 1
        await client._poll_with_404_recovery(poll_204)
        mock_redisc.assert_not_awaited()
        mock_auto_sub.assert_awaited_once()
        assert client._poll_failure_count == 0


@pytest.mark.asyncio
async def test_poll_persistent_redirect_triggers_rediscovery(client: CsipClient):
    """A persistent redirect during a poll triggers rediscovery (IEEE 5.5.2.7)."""

    async def poll_redirect() -> None:
        raise Sep2RedirectError("moved", location="/new")

    with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
        await client._poll_with_404_recovery(poll_redirect)
        mock_redisc.assert_awaited_once()


@pytest.mark.asyncio
async def test_recovery_after_sustained_failure_triggers_rediscovery(
    client: CsipClient, caplog: pytest.LogCaptureFixture
):
    """After >= _SERVER_RESTART_DETECTION_THRESHOLD consecutive
    poll failures, the next successful poll should trigger full rediscovery,
    not just re-subscribe. Without this, an upstream server restart
    leaves the aggregator with dead subscriptions and a half-wired state."""
    with (
        patch(
            "py20305.client.csip_client.discover",
            new_callable=AsyncMock,
        ) as mock_disc,
        patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc,
    ):
        await client.connect()
        # Simulate sustained failure: bump the counter directly to threshold
        client._poll_failure_count = 3

        async def succeeding_poll() -> None:
            pass

        with caplog.at_level("INFO"):
            await client._poll_with_404_recovery(succeeding_poll)

        mock_redisc.assert_awaited_once()
        assert client._poll_failure_count == 0
        assert any("treating as upstream restart" in r.getMessage() for r in caplog.records), (
            "expected restart-detection log line"
        )
        # discover() is patched separately from trigger_rediscovery, so only
        # the initial connect() call counts here -- rediscovery is mocked.
        assert mock_disc.await_count == 1


@pytest.mark.asyncio
async def test_recovery_below_threshold_only_resubscribes(client: CsipClient):
    """A single transient blip recovers via _auto_subscribe, NOT a full
    rediscovery. Keeps the cheap path cheap and avoids re-discovery
    flapping on every momentary network hiccup."""
    with (
        patch("py20305.client.csip_client.discover", new_callable=AsyncMock),
        patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc,
        patch.object(client, "_auto_subscribe", new_callable=AsyncMock) as mock_auto_sub,
    ):
        await client.connect()
        # Attach a subscription manager so the auto_subscribe branch runs.
        client._subscription_manager = MagicMock()
        client._poll_failure_count = 1

        async def succeeding_poll() -> None:
            pass

        await client._poll_with_404_recovery(succeeding_poll)
        mock_redisc.assert_not_awaited()
        mock_auto_sub.assert_awaited_once()
        assert client._poll_failure_count == 0


@pytest.mark.asyncio
async def test_non_404_error_propagates(client: CsipClient):
    async def failing_poll() -> None:
        raise Sep2ProtocolError("Server error", 500)

    with pytest.raises(Sep2ProtocolError, match="Server error"):
        await client._poll_with_404_recovery(failing_poll)


@pytest.mark.asyncio
async def test_context_manager():
    with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
        async with CsipClient("https://example.com") as client:
            await client.connect()
        assert client._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_run_processes_controls_immediately(client: CsipClient):
    """run() should call process_controls for discovered programs before polling."""
    with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
        await client.connect()

    client._state.der_programs["prog1"] = None  # type: ignore[assignment]
    client._state.der_programs["prog2"] = None  # type: ignore[assignment]

    with (
        patch.object(
            client._event_processor, "process_controls", new_callable=AsyncMock
        ) as mock_pc,
        patch.object(client, "_start_polls"),
    ):
        # Signal shutdown so run() doesn't block
        client._shutdown_event.set()
        await client.run()

        assert mock_pc.await_count == 2
        mock_pc.assert_any_await("prog1")
        mock_pc.assert_any_await("prog2")


@pytest.mark.asyncio
async def test_rediscovery_processes_controls(client: CsipClient):
    """trigger_rediscovery() should call process_controls for discovered programs."""
    with patch(
        "py20305.client.csip_client.discover", new_callable=AsyncMock
    ) as mock_disc:
        await client.connect()

        # On rediscovery, discover populates programs
        async def populate_state(*_args: object, **_kwargs: object) -> None:
            client._state.der_programs["/derp/1"] = None  # type: ignore[assignment]
            client._state.der_programs["/derp/2"] = None  # type: ignore[assignment]

        mock_disc.side_effect = populate_state

        # Mock process_controls on the existing (preserved) processor
        client._event_processor.process_controls = AsyncMock()  # type: ignore[method-assign]

        with (
            patch(
                "py20305.client.csip_client.refresh_function_set_assignments",
                new_callable=AsyncMock,
            ),
            patch(
                "py20305.client.csip_client.refresh_der_programs",
                new_callable=AsyncMock,
            ),
            patch(
                "py20305.client.csip_client.refresh_der_controls",
                new_callable=AsyncMock,
            ),
        ):
            await client.trigger_rediscovery()

        assert client._event_processor.process_controls.await_count == 2
        client._event_processor.process_controls.assert_any_await("/derp/1")
        client._event_processor.process_controls.assert_any_await("/derp/2")


@pytest.mark.asyncio
async def test_rediscovery_refreshes_controls_before_processing(client: CsipClient):
    """trigger_rediscovery() must refresh DER controls before processing them.

    Controls written to the server after discover() reads the DERControlList
    but before process_controls() runs would be missed without this refresh.
    """
    call_order: list[str] = []

    async def track_refresh(*_args: object, **_kwargs: object) -> None:
        call_order.append("refresh_der_controls")

    async def track_process(*_args: object, **_kwargs: object) -> None:
        call_order.append("process_controls")

    with patch(
        "py20305.client.csip_client.discover", new_callable=AsyncMock
    ) as mock_disc:
        await client.connect()

        async def populate_state(*_args: object, **_kwargs: object) -> None:
            client._state.der_programs["/derp/1"] = None  # type: ignore[assignment]

        mock_disc.side_effect = populate_state

        # Mock process_controls on the existing (preserved) processor
        client._event_processor.process_controls = AsyncMock(  # type: ignore[method-assign]
            side_effect=track_process
        )

        with (
            patch(
                "py20305.client.csip_client.refresh_function_set_assignments",
                new_callable=AsyncMock,
            ),
            patch(
                "py20305.client.csip_client.refresh_der_programs",
                new_callable=AsyncMock,
            ),
            patch(
                "py20305.client.csip_client.refresh_der_controls",
                side_effect=track_refresh,
            ),
        ):
            await client.trigger_rediscovery()

    assert call_order == ["refresh_der_controls", "process_controls"]


@pytest.mark.asyncio
async def test_rediscovery_refreshes_fsas_and_programs(client: CsipClient):
    """trigger_rediscovery() must refresh FSAs and programs after reconciliation.

    FSAs and DERPrograms added to the server after discover() but before the
    FSAList subscription is created would never be seen without this catch-up.
    """
    with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
        await client.connect()

        with (
            patch(
                "py20305.client.csip_client.refresh_function_set_assignments",
                new_callable=AsyncMock,
            ) as mock_rfa,
            patch(
                "py20305.client.csip_client.refresh_der_programs",
                new_callable=AsyncMock,
            ) as mock_rdp,
            patch(
                "py20305.client.csip_client.refresh_der_controls",
                new_callable=AsyncMock,
            ),
        ):
            await client.trigger_rediscovery()

    mock_rfa.assert_awaited_once()
    mock_rdp.assert_awaited_once()


@pytest.mark.asyncio
async def test_rediscovery_fsa_catch_up_order(client: CsipClient):
    """trigger_rediscovery() must refresh FSAs and programs before DER controls.

    The FSA/program catch-up must precede the DERControl refresh so that newly
    discovered programs are present when controls are re-fetched and processed.
    """
    call_order: list[str] = []

    async def track_fsa(*_args: object, **_kwargs: object) -> None:
        call_order.append("refresh_function_set_assignments")

    async def track_programs(*_args: object, **_kwargs: object) -> None:
        call_order.append("refresh_der_programs")

    async def track_controls(*_args: object, **_kwargs: object) -> None:
        call_order.append("refresh_der_controls")

    async def track_process(*_args: object, **_kwargs: object) -> None:
        call_order.append("process_controls")

    with patch(
        "py20305.client.csip_client.discover", new_callable=AsyncMock
    ) as mock_disc:
        await client.connect()

        async def populate_state(*_args: object, **_kwargs: object) -> None:
            client._state.der_programs["/derp/1"] = None  # type: ignore[assignment]

        mock_disc.side_effect = populate_state

        # Mock process_controls on the existing (preserved) processor
        client._event_processor.process_controls = AsyncMock(  # type: ignore[method-assign]
            side_effect=track_process
        )

        with (
            patch(
                "py20305.client.csip_client.refresh_function_set_assignments",
                side_effect=track_fsa,
            ),
            patch(
                "py20305.client.csip_client.refresh_der_programs",
                side_effect=track_programs,
            ),
            patch(
                "py20305.client.csip_client.refresh_der_controls",
                side_effect=track_controls,
            ),
        ):
            await client.trigger_rediscovery()

    assert call_order == [
        "refresh_function_set_assignments",
        "refresh_der_programs",
        "refresh_der_controls",
        "process_controls",
    ]


@pytest.mark.asyncio
async def test_rediscovery_reruns_on_pending_notification(client: CsipClient):
    """trigger_rediscovery() re-runs if _rediscovery_pending is set mid-run.

    Simulates a structural change notification arriving while rediscovery is
    already in progress: the pending flag causes the loop to run a second time
    so the notification is not silently dropped.
    """
    discover_count = 0

    async def discover_and_set_pending(*_args: object, **_kwargs: object) -> None:
        nonlocal discover_count
        discover_count += 1
        if discover_count == 1:
            # Simulate a notification arriving while discover() is awaited
            client._rediscovery_pending = True  # type: ignore[attr-defined]

    with (
        patch(
            "py20305.client.csip_client.discover",
            side_effect=discover_and_set_pending,
        ),
        patch(
            "py20305.client.csip_client.refresh_function_set_assignments",
            new_callable=AsyncMock,
        ),
        patch(
            "py20305.client.csip_client.refresh_der_programs",
            new_callable=AsyncMock,
        ),
        patch(
            "py20305.client.csip_client.refresh_der_controls",
            new_callable=AsyncMock,
        ),
    ):
        await client.trigger_rediscovery()

    assert discover_count == 2


@pytest.mark.asyncio
async def test_trigger_rediscovery_sets_pending_when_locked(client: CsipClient):
    """Calling trigger_rediscovery() while it's running sets _rediscovery_pending."""
    barrier: asyncio.Event = asyncio.Event()

    async def blocking_discover(*_args: object, **_kwargs: object) -> None:
        await barrier.wait()

    with (
        patch("py20305.client.csip_client.discover", side_effect=blocking_discover),
        patch(
            "py20305.client.csip_client.refresh_function_set_assignments",
            new_callable=AsyncMock,
        ),
        patch(
            "py20305.client.csip_client.refresh_der_programs",
            new_callable=AsyncMock,
        ),
        patch(
            "py20305.client.csip_client.refresh_der_controls",
            new_callable=AsyncMock,
        ),
    ):
        task = asyncio.create_task(client.trigger_rediscovery())
        await asyncio.sleep(0)  # let it start and acquire the lock

        assert client._rediscovery_lock.locked()  # type: ignore[attr-defined]
        assert not client._rediscovery_pending  # type: ignore[attr-defined]

        # Simulate a notification arriving while rediscovery is running
        await client.trigger_rediscovery()
        assert client._rediscovery_pending  # type: ignore[attr-defined]

        barrier.set()  # unblock both discover() calls (second run uses barrier already set)
        await task

    assert not client._rediscovery_pending  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_poll_now_calls_do_poll_derp(client: CsipClient):
    """poll_now() should call _do_poll_derp and return program count."""
    client._state.der_programs["prog1"] = None  # type: ignore[assignment]
    client._state.der_programs["prog2"] = None  # type: ignore[assignment]

    with patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_poll:
        result = await client.poll_now()
        mock_poll.assert_awaited_once()
        assert result == 2


@pytest.mark.asyncio
async def test_state_property(client: CsipClient):
    assert isinstance(client.state, DiscoveredState)


# =========================================================================
# Notification routing tests
# =========================================================================


def _make_notification(subscribed_resource: str, status: int = 0) -> object:
    """Create a Notification-like object for testing."""
    from py20305.models.sep.sep import Notification

    return Notification(
        subscribed_resource=subscribed_resource,
        status=status,
        subscription_uri="/edev/1/sub/1",
    )


@pytest.fixture
def client_with_sub_mgr() -> CsipClient:
    """Client with a mocked SubscriptionManager."""
    client = CsipClient("https://example.com")
    mgr = MagicMock(spec=SubscriptionManager)
    mgr.is_duplicate_notification.return_value = False
    mgr.should_process_notification.return_value = True
    # Untracked subscription -> classification falls back to the path heuristic
    # (these tests exercise the path-based routing).
    mgr.resource_type_for.return_value = None
    client._subscription_manager = mgr
    return client


class TestIsStructuralResource:
    """Test _is_structural_resource path classification."""

    def test_edev_list(self, client: CsipClient):
        assert client._is_structural_resource("/edev") is True

    def test_edev_single(self, client: CsipClient):
        assert client._is_structural_resource("/edev/1") is True

    def test_fsa_list(self, client: CsipClient):
        assert client._is_structural_resource("/edev/1/fsa") is True

    def test_fsa_single(self, client: CsipClient):
        assert client._is_structural_resource("/edev/1/fsa/1") is True

    def test_derp_list_not_structural(self, client: CsipClient):
        assert client._is_structural_resource("/edev/1/fsa/1/derp") is False

    def test_derc_list_not_structural(self, client: CsipClient):
        assert client._is_structural_resource("/edev/1/fsa/1/derp/1/derc") is False

    def test_dderc_not_structural(self, client: CsipClient):
        assert client._is_structural_resource("/edev/1/fsa/1/derp/1/dderc") is False

    def test_group_derp_not_structural(self, client: CsipClient):
        assert client._is_structural_resource("/cert_e2e/derp") is False

    def test_group_derc(self, client: CsipClient):
        assert client._is_structural_resource("/cert_e2e/derp/1/derc") is False


class TestRouteDefaultNotification:
    """Test STATUS_DEFAULT notification routing."""

    @pytest.mark.asyncio
    async def test_derc_change_uses_targeted_fetch(self, client_with_sub_mgr: CsipClient):
        """DERControlList notification uses targeted fetch, not full re-poll."""
        client = client_with_sub_mgr
        with (
            patch.object(
                client, "_do_poll_derp_targeted", new_callable=AsyncMock, return_value=True
            ) as mock_targeted,
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_full,
        ):
            await client._route_default_notification("/edev/1/fsa/1/derp/1/derc")
            mock_targeted.assert_awaited_once_with("/edev/1/fsa/1/derp/1/derc")
            mock_full.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dderc_change_uses_targeted_fetch(self, client_with_sub_mgr: CsipClient):
        """DefaultDERControl notification uses targeted fetch."""
        client = client_with_sub_mgr
        with (
            patch.object(
                client, "_do_poll_derp_targeted", new_callable=AsyncMock, return_value=True
            ) as mock_targeted,
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_full,
        ):
            await client._route_default_notification("/edev/1/fsa/1/derp/1/dderc")
            mock_targeted.assert_awaited_once_with("/edev/1/fsa/1/derp/1/dderc")
            mock_full.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_targeted_fetch_falls_back_to_full_poll(self, client_with_sub_mgr: CsipClient):
        """When targeted fetch returns False, falls back to full re-poll."""
        client = client_with_sub_mgr
        with (
            patch.object(
                client, "_do_poll_derp_targeted", new_callable=AsyncMock, return_value=False
            ) as mock_targeted,
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_full,
        ):
            await client._route_default_notification("/edev/1/fsa/1/derp/1/derc")
            mock_targeted.assert_awaited_once()
            mock_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tariff_change_repolls_tariffs_not_derps(self, client_with_sub_mgr: CsipClient):
        """A TimeTariffIntervalList notification re-polls tariffs, not DER programs."""
        client = client_with_sub_mgr
        with (
            patch.object(client, "_do_poll_tariff", new_callable=AsyncMock) as mock_tariff,
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_derp,
        ):
            await client._route_default_notification("/tp/1/rc/1/tti", "TimeTariffIntervalList")
            mock_tariff.assert_awaited_once()
            mock_derp.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_derc_notification_does_targeted_fetch_and_no_sub_writes(
        self, client_with_sub_mgr: CsipClient
    ):
        """End-to-end through _handle_notification: a DERControl notification does
        its targeted DERControlList fetch and touches NO subscriptions (no renew,
        re-POST, reconcile, or cancel). Confirms the rediscovery-renew fix leaves
        the control path fully intact -- the two concerns are independent.
        """
        client = client_with_sub_mgr
        mgr = client._subscription_manager
        with patch.object(
            client, "_do_poll_derp_targeted", new_callable=AsyncMock, return_value=True
        ) as mock_targeted:
            await client._handle_notification(
                _make_notification("/edev/1/fsa/1/derp/1/derc", status=0)
            )

        # Control was fetched via the targeted GET...
        mock_targeted.assert_awaited_once_with("/edev/1/fsa/1/derp/1/derc")
        # ...and no subscription write of any kind happened.
        mgr.reconcile.assert_not_called()
        mgr.renew.assert_not_called()
        mgr.subscribe.assert_not_called()
        mgr.cancel_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_edev_change_triggers_rediscovery(self, client_with_sub_mgr: CsipClient):
        client = client_with_sub_mgr
        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
            await client._route_default_notification("/edev")
            mock_redisc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fsa_change_triggers_rediscovery(self, client_with_sub_mgr: CsipClient):
        client = client_with_sub_mgr
        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
            await client._route_default_notification("/edev/1/fsa")
            mock_redisc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_derp_change_uses_full_repoll(self, client_with_sub_mgr: CsipClient):
        """DERProgramList change uses full re-poll (not targeted)."""
        client = client_with_sub_mgr
        with (
            patch.object(client, "_do_poll_derp_targeted", new_callable=AsyncMock) as mock_targeted,
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_full,
        ):
            await client._route_default_notification("/edev/1/fsa/1/derp")
            mock_targeted.assert_not_awaited()
            mock_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resource_type_classifies_endevicelist_regardless_of_path(
        self, client_with_sub_mgr: CsipClient
    ):
        """A subscription tagged EndDeviceList routes to rediscovery no matter how
        the server names the URL (base-path prefix, or a non-`edev` name)."""
        client = client_with_sub_mgr
        for path in ("/api/v2/edev", "/some/other/devices"):
            with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc:
                await client._route_default_notification(path, "EndDeviceList")
                mock_redisc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_resource_type_classifies_control_regardless_of_path(
        self, client_with_sub_mgr: CsipClient
    ):
        """A subscription tagged DERControlList uses targeted fetch even when the
        path doesn't end in /derc."""
        client = client_with_sub_mgr
        with (
            patch.object(
                client, "_do_poll_derp_targeted", new_callable=AsyncMock, return_value=True
            ) as mock_targeted,
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_full,
        ):
            await client._route_default_notification("/api/v2/edev/1/programs/3", "DERControlList")
            mock_targeted.assert_awaited_once()
            mock_full.assert_not_awaited()

    def test_prefixed_edev_path_is_structural_in_fallback(self, client: CsipClient):
        """Even without a tracked subscription (resource_type None), the path
        heuristic recognizes a base-path-prefixed EndDeviceList."""
        assert client._is_structural("/api/v2/edev") is True
        assert client._is_structural("/api/v2/edev/5") is True
        assert client._is_structural("/api/v2/edev/5/fsa/1/derp") is False

    @pytest.mark.asyncio
    async def test_new_device_notification_triggers_rediscovery(self):
        """Interop regression: a notification on the prefixed EndDeviceList
        whose subscription is tagged EndDeviceList triggers rediscovery, so a
        newly provisioned EndDevice is discovered -- not a DERP re-poll."""
        client = CsipClient("https://example.com")
        mgr = MagicMock(spec=SubscriptionManager)
        mgr.is_duplicate_notification.return_value = False
        mgr.should_process_notification.return_value = True
        mgr.resource_type_for.return_value = "EndDeviceList"
        client._subscription_manager = mgr
        notification = _make_notification("/api/v2/edev", status=0)
        with (
            patch.object(client, "trigger_rediscovery", new_callable=AsyncMock) as mock_redisc,
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_repoll,
        ):
            await client._handle_notification(notification)
            mock_redisc.assert_awaited_once()
            mock_repoll.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parent_notification_suppressed_after_targeted_fetch(self):
        """After targeted fetch of /derc, a subsequent /derp notification is suppressed."""
        client = CsipClient("https://example.com")
        # Use a real SubscriptionManager so dedup cache actually works
        mgr = SubscriptionManager(
            client=MagicMock(),
            notification_uri="https://localhost/notify",
        )
        client._subscription_manager = mgr

        with (
            patch.object(
                client, "_do_poll_derp_targeted", new_callable=AsyncMock, return_value=True
            ),
            patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_full,
        ):
            # Notification 1: DERControlList → targeted fetch succeeds, records ancestry
            await client._route_default_notification("/edev/1/fsa/1/derp/3/derc")
            # Notification 2: DERProgramList → should be suppressed by ancestry dedup
            await client._handle_notification(_make_notification("/edev/1/fsa/1/derp", status=0))
            mock_full.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_parent_not_suppressed_without_targeted_fetch(
        self, client_with_sub_mgr: CsipClient
    ):
        """Without a prior targeted fetch, /derp notification triggers full re-poll."""
        client = client_with_sub_mgr
        with patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_full:
            await client._route_default_notification("/edev/1/fsa/1/derp")
            mock_full.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_structural_change_uses_callback(self):
        callback = AsyncMock()
        client = CsipClient("https://example.com", on_structural_change=callback)
        client._subscription_manager = MagicMock(spec=SubscriptionManager)
        await client._route_default_notification("/edev")
        callback.assert_awaited_once()


class TestPollSuppression:
    """_update_poll_suppression must suppress the poll for every subscribed
    resource type -- including EndDeviceList, the regression this guards."""

    def _client(self, subscribed_types: set[str]) -> CsipClient:
        client = CsipClient("https://example.com")
        mgr = MagicMock()
        mgr.subscribed_resource_types.return_value = subscribed_types
        client._subscription_manager = mgr
        return client

    def test_enddevicelist_subscription_suppresses_edev_poll(self):
        client = self._client({"EndDeviceList"})
        client._update_poll_suppression()
        assert "edev" in client._scheduler.suppressed_keys

    def test_function_set_subscriptions_suppress_fsa_and_derp(self):
        client = self._client({"FSAList", "DERControlList"})
        client._update_poll_suppression()
        assert {"fsa", "derp"} <= client._scheduler.suppressed_keys
        assert "edev" not in client._scheduler.suppressed_keys

    def test_no_subscriptions_suppresses_nothing(self):
        client = self._client(set())
        client._update_poll_suppression()
        assert client._scheduler.suppressed_keys == set()

    def test_tariff_subscription_does_not_suppress_tariff_poll(self):
        # The tariff processor is poll-driven for active-interval transitions
        # (no event timers), so the poll must keep running even when subscribed.
        client = self._client({"TimeTariffIntervalList"})
        client._update_poll_suppression()
        assert "tariff" not in client._scheduler.suppressed_keys

    def test_map_values_are_real_poll_keys(self):
        # Every poll key in the map must be one the scheduler actually runs.
        valid_keys = {"dcap", "edev", "fsa", "derp", "time", "tariff"}
        assert set(_RESOURCE_TYPE_TO_POLL_KEY.values()) <= valid_keys

    def test_subscribable_list_types_all_mapped(self):
        # Drift guard: a subscribable+pollable list resource that isn't mapped
        # would be subscribed AND still polled (the EndDeviceList bug). Every
        # such type must have a suppression mapping.
        subscribable_pollable_lists = {
            "EndDeviceList",
            "FSAList",
            "DERProgramList",
            "DERControlList",
            "DefaultDERControl",
            "TariffProfileList",
            "TimeTariffIntervalList",
        }
        assert subscribable_pollable_lists <= set(_RESOURCE_TYPE_TO_POLL_KEY)


class TestIsControlResource:
    """Test _is_control_resource classification."""

    def test_derc_path(self) -> None:
        assert CsipClient._is_control_resource("/edev/1/fsa/1/derp/1/derc") is True

    def test_dderc_path(self) -> None:
        assert CsipClient._is_control_resource("/edev/1/fsa/1/derp/1/dderc") is True

    def test_derp_list_path(self) -> None:
        assert CsipClient._is_control_resource("/edev/1/fsa/1/derp") is False

    def test_edev_path(self) -> None:
        assert CsipClient._is_control_resource("/edev/1") is False

    def test_trailing_slash(self) -> None:
        assert CsipClient._is_control_resource("/edev/1/fsa/1/derp/1/derc/") is True


class TestHandleResourceDeleted:
    """Test STATUS_RESOURCE_DELETED handling."""

    @pytest.mark.asyncio
    async def test_edev_deletion_calls_callback(self):
        callback = AsyncMock()
        client = CsipClient("https://example.com", on_device_removed=callback)
        client._subscription_manager = MagicMock(spec=SubscriptionManager)
        await client._handle_resource_deleted("/edev/1")
        callback.assert_awaited_once_with("/edev/1")

    @pytest.mark.asyncio
    async def test_non_edev_deletion_triggers_rediscovery(self):
        structural_cb = AsyncMock()
        client = CsipClient("https://example.com", on_structural_change=structural_cb)
        client._subscription_manager = MagicMock(spec=SubscriptionManager)
        await client._handle_resource_deleted("/edev/1/fsa/1")
        structural_cb.assert_awaited_once()


class TestHandleNotificationIntegration:
    """Integration tests for the full _handle_notification flow."""

    @pytest.mark.asyncio
    async def test_status_default_derc_records_and_polls(self, client_with_sub_mgr: CsipClient):
        client = client_with_sub_mgr
        notification = _make_notification("/edev/1/fsa/1/derp/1/derc", status=0)
        with patch.object(client, "_do_poll_derp", new_callable=AsyncMock) as mock_poll:
            await client._handle_notification(notification)
            mock_poll.assert_awaited_once()
        client._subscription_manager.record_notification.assert_called_once()

    @pytest.mark.asyncio
    async def test_status_cancelled_marks_and_removes(self, client_with_sub_mgr: CsipClient):
        client = client_with_sub_mgr
        notification = _make_notification("/edev/1/fsa", status=1)
        await client._handle_notification(notification)
        client._subscription_manager.mark_cancelled.assert_called_once_with("/edev/1/sub/1")
        client._subscription_manager.remove_notifications_for.assert_called_once()

    @pytest.mark.asyncio
    async def test_status_resource_deleted_calls_handler(self):
        device_cb = AsyncMock()
        client = CsipClient("https://example.com", on_device_removed=device_cb)
        mgr = MagicMock(spec=SubscriptionManager)
        mgr.is_duplicate_notification.return_value = False
        mgr.should_process_notification.return_value = True
        client._subscription_manager = mgr
        notification = _make_notification("/edev/1", status=4)
        await client._handle_notification(notification)
        client._subscription_manager.mark_cancelled.assert_called_once()
        device_cb.assert_awaited_once_with("/edev/1")

    @pytest.mark.asyncio
    async def test_deduped_structural_sets_pending_during_rediscovery(
        self, client_with_sub_mgr: CsipClient
    ):
        """A deduped structural notification sets _rediscovery_pending if rediscovery is running.

        If the dedup window suppresses a structural notification while rediscovery holds
        the lock, the notification must not be completely lost — the pending flag ensures
        a follow-up rediscovery run picks up the change.
        """
        client = client_with_sub_mgr
        client._subscription_manager.is_duplicate_notification.return_value = True  # force dedup

        notification = _make_notification("/edev", status=0)

        async with client._rediscovery_lock:
            assert not client._rediscovery_pending  # type: ignore[attr-defined]
            await client._handle_notification(notification)
            assert client._rediscovery_pending  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_deduped_structural_no_pending_when_not_rediscovering(
        self, client_with_sub_mgr: CsipClient
    ):
        """A deduped structural notification does NOT set pending outside rediscovery.

        The pending flag is only meaningful while rediscovery holds the lock; outside
        that window, a deduplicated notification is a true duplicate and should be dropped.
        """
        client = client_with_sub_mgr
        client._subscription_manager.is_duplicate_notification.return_value = True

        notification = _make_notification("/edev", status=0)

        assert not client._rediscovery_lock.locked()
        await client._handle_notification(notification)
        assert not client._rediscovery_pending  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_control_notification_burst_bypasses_dedup(self, client_with_sub_mgr: CsipClient):
        """A burst of /derc notifications for the same path must each be processed.

        BASIC-015 reproducer: the server fires one /derc notification per
        cancellation during a 23-call cancel_derc burst (server-side mgmt
        history confirms this). The targeted-fetch handler is one cheap
        GET against a single URL; deduping it means we only see snapshots
        from the first / occasional notifications and the trailing
        cancellations are invisible. Each /derc notification must
        therefore trigger its own targeted fetch, regardless of dedup
        state for that path.
        """
        client = client_with_sub_mgr
        # Force worst-case dedup -- every notification looks like a duplicate.
        client._subscription_manager.is_duplicate_notification.return_value = True

        notification = _make_notification("/edev/1/fsa/2/derp/7/derc", status=0)

        with patch.object(
            client, "_do_poll_derp_targeted", new_callable=AsyncMock, return_value=True
        ) as mock_fetch:
            for _ in range(5):
                await client._handle_notification(notification)

        assert mock_fetch.await_count == 5, (
            f"Expected each /derc notification in a burst to trigger a "
            f"targeted fetch (got {mock_fetch.await_count}/5)"
        )

    @pytest.mark.asyncio
    async def test_control_notification_not_subject_to_dedup_during_rediscovery(
        self, client_with_sub_mgr: CsipClient
    ):
        """During rediscovery, /derc notifications still skip dedup but route_default
        early-returns (rediscovery's own refresh will catch the controls).

        Replaces test_deduped_control_notification_does_not_set_pending: now that
        /derc bypasses dedup entirely, "deduped control notification" is not a
        reachable state. Asserting the rediscovery interaction still holds:
        _rediscovery_pending must not be set by a control notification during
        rediscovery, because refresh_der_controls() in the active loop will
        pick up the change.
        """
        client = client_with_sub_mgr
        # Even with dedup return-value forced True, control paths don't consult it.
        client._subscription_manager.is_duplicate_notification.return_value = True

        notification = _make_notification("/edev/1/fsa/1/derp/1/derc", status=0)

        async with client._rediscovery_lock:
            await client._handle_notification(notification)
            assert not client._rediscovery_pending  # type: ignore[attr-defined]


class TestComputeDesiredSubscriptions:
    """Tests for _compute_desired_subscriptions() pure function."""

    def test_empty_state_returns_empty(self):
        client = CsipClient("https://example.com")
        assert client._compute_desired_subscriptions() == set()

    def test_includes_edev_list(self):
        client = CsipClient("https://example.com")
        dcap = MagicMock()
        edev_link = MagicMock()
        edev_link.href = "/edev"
        dcap.end_device_list_link = edev_link
        client._state.dcap = dcap

        desired = client._compute_desired_subscriptions()
        assert ("/edev", "EndDeviceList") in desired

    def test_includes_subscribable_fsa_list(self):
        client = CsipClient("https://example.com")
        device = MagicMock()
        fsa_link = MagicMock()
        fsa_link.href = "/edev/1/fsa"
        device.function_set_assignments_list_link = fsa_link
        edev_state = EndDeviceState(
            device=device,
            href="/edev/1",
            lfdi=b"\xab\xcd",
            subscription_list_href="/edev/1/sub",
            fsa_list_subscribable=True,
        )
        client._state.end_devices["/edev/1"] = edev_state

        desired = client._compute_desired_subscriptions()
        assert ("/edev/1/fsa", "FSAList") in desired

    def test_excludes_non_subscribable_fsa(self):
        client = CsipClient("https://example.com")
        device = MagicMock()
        fsa_link = MagicMock()
        fsa_link.href = "/edev/1/fsa"
        device.function_set_assignments_list_link = fsa_link
        edev_state = EndDeviceState(
            device=device,
            href="/edev/1",
            lfdi=b"\xab\xcd",
            fsa_list_subscribable=False,
        )
        client._state.end_devices["/edev/1"] = edev_state

        desired = client._compute_desired_subscriptions()
        assert ("/edev/1/fsa", "FSAList") not in desired

    def test_includes_derc_and_dderc_per_program(self):
        client = CsipClient("https://example.com")
        program = MagicMock()
        derc_link = MagicMock()
        derc_link.href = "/edev/1/derp/1/derc"
        program.dercontrol_list_link = derc_link
        dderc_link = MagicMock()
        dderc_link.href = "/edev/1/derp/1/dderc"
        program.default_dercontrol_link = dderc_link
        derp_state = DerProgramState(
            program=program,
            href="/edev/1/derp/1",
            primacy=0,
            derc_list_subscribable=True,
            dderc_subscribable=True,
        )
        client._state.der_programs["/edev/1/derp/1"] = derp_state

        desired = client._compute_desired_subscriptions()
        assert ("/edev/1/derp/1/derc", "DERControlList") in desired
        assert ("/edev/1/derp/1/dderc", "DefaultDERControl") in desired

    def _client_with_tariff(self, *, subscribable: bool, href: str | None = "/tp/1/rc/1/tti"):
        client = CsipClient("https://example.com", pricing_enabled=True)
        rc = TariffRateComponentState(
            rate_component=MagicMock(),
            href="/tp/1/rc/1",
            tti_list_subscribable=subscribable,
            tti_list_href=href,
        )
        client._state.tariff_profiles["/tp/1"] = TariffProfileState(
            profile=MagicMock(), href="/tp/1", primacy=0, rate_components=[rc]
        )
        return client

    def test_includes_subscribable_tariff_interval_list(self):
        client = self._client_with_tariff(subscribable=True)
        desired = client._compute_desired_subscriptions()
        assert ("/tp/1/rc/1/tti", "TimeTariffIntervalList") in desired

    def test_excludes_non_subscribable_tariff_interval_list(self):
        client = self._client_with_tariff(subscribable=False)
        assert client._compute_desired_subscriptions() == set()

    def test_excludes_tariff_when_pricing_disabled(self):
        client = self._client_with_tariff(subscribable=True)
        client._state.pricing_enabled = False
        assert client._compute_desired_subscriptions() == set()

    def test_find_agg_sub_href(self):
        client = CsipClient("https://example.com")
        assert client._find_agg_sub_href() is None

        device = MagicMock()
        device.function_set_assignments_list_link = None
        edev_state = EndDeviceState(
            device=device,
            href="/edev/1",
            lfdi=b"\xab\xcd",
            subscription_list_href="/edev/1/sub",
        )
        client._state.end_devices["/edev/1"] = edev_state

        assert client._find_agg_sub_href() == "/edev/1/sub"


@pytest.mark.asyncio
async def test_auto_subscribe_warns_once_when_no_sub_link(caplog):
    """Missing SubscriptionListLink logs WARNING exactly once across repeated calls,
    then INFO when a link later appears (server-side fix recovered)."""
    client = CsipClient("https://example.com")
    client._subscription_manager = MagicMock()
    client._subscription_manager.subscribe = AsyncMock()

    import logging as _logging

    with caplog.at_level(_logging.INFO, logger="py20305.client.csip_client"):
        await client._auto_subscribe()
        await client._auto_subscribe()
        await client._auto_subscribe()

        warn_msgs = [
            r
            for r in caplog.records
            if r.levelno == _logging.WARNING and "Subscriptions disabled" in r.message
        ]
        assert len(warn_msgs) == 1, f"expected 1 warning, got {len(warn_msgs)}"
        assert client._no_sub_link_warned is True

        device = MagicMock()
        device.function_set_assignments_list_link = None
        client._state.end_devices["/edev/1"] = EndDeviceState(
            device=device,
            href="/edev/1",
            lfdi=b"\xab\xcd",
            subscription_list_href="/edev/1/sub",
        )
        with patch.object(client, "_compute_desired_subscriptions", return_value=set()):
            await client._auto_subscribe()

        info_msgs = [
            r
            for r in caplog.records
            if r.levelno == _logging.INFO and "Subscriptions now available" in r.message
        ]
        assert len(info_msgs) == 1
        assert client._no_sub_link_warned is False


class TestLateMupLinkOnDcapPoll:
    """A DCAP poll that surfaces a new MirrorUsagePointListLink triggers
    rediscovery, so telemetry is wired up without a restart even when no other
    structural change accompanies it."""

    @staticmethod
    def _dcap(href):
        from types import SimpleNamespace

        link = SimpleNamespace(href=href) if href is not None else None
        return SimpleNamespace(mirror_usage_point_list_link=link)

    @pytest.mark.asyncio
    async def test_adopts_link_that_appears(self, client: CsipClient):
        client._state.mup_list_href = None
        client._on_structural_change = AsyncMock()
        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=self._dcap("/mup")
        ):
            await client._do_poll_dcap()
        client._on_structural_change.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_trigger_when_link_already_known(self, client: CsipClient):
        client._state.mup_list_href = "/mup"
        client._on_structural_change = AsyncMock()
        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=self._dcap("/mup")
        ):
            await client._do_poll_dcap()
        client._on_structural_change.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_trigger_when_still_no_link(self, client: CsipClient):
        client._state.mup_list_href = None
        client._on_structural_change = AsyncMock()
        with patch.object(
            client._http, "get", new_callable=AsyncMock, return_value=self._dcap(None)
        ):
            await client._do_poll_dcap()
        client._on_structural_change.assert_not_awaited()


class TestSubReconcilePoll:
    """_poll_sub_reconcile drives the SubscriptionList reconcile."""

    @pytest.mark.asyncio
    async def test_calls_reconcile_when_subs_active(self, client: CsipClient):
        mgr = MagicMock()
        mgr.active_subscriptions = [object()]
        mgr.reconcile_with_server = AsyncMock()
        client._subscription_manager = mgr
        client._find_agg_sub_href = MagicMock(return_value="/api/v2/edev/AGG/sub")  # type: ignore[method-assign]
        await client._poll_sub_reconcile()
        mgr.reconcile_with_server.assert_awaited_once_with("/api/v2/edev/AGG/sub")

    @pytest.mark.asyncio
    async def test_noop_without_agg_sub_href(self, client: CsipClient):
        mgr = MagicMock()
        mgr.active_subscriptions = [object()]
        mgr.reconcile_with_server = AsyncMock()
        client._subscription_manager = mgr
        client._find_agg_sub_href = MagicMock(return_value=None)  # type: ignore[method-assign]
        await client._poll_sub_reconcile()
        mgr.reconcile_with_server.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_noop_without_active_subscriptions(self, client: CsipClient):
        mgr = MagicMock()
        mgr.active_subscriptions = []
        mgr.reconcile_with_server = AsyncMock()
        client._subscription_manager = mgr
        client._find_agg_sub_href = MagicMock(return_value="/sub")  # type: ignore[method-assign]
        await client._poll_sub_reconcile()
        mgr.reconcile_with_server.assert_not_awaited()


class TestSubReconcileRate:
    """_sub_poll_rate follows the server SubscriptionList pollRate, overridable."""

    def test_follows_server_pollrate(self, client: CsipClient):
        client._reconcile_interval_seconds = 0  # auto
        client._state.poll_rates["sub"] = 300  # server-advertised
        assert client._sub_poll_rate() == 300

    def test_defaults_to_900_when_server_silent(self, client: CsipClient):
        client._reconcile_interval_seconds = 0
        client._state.poll_rates.pop("sub", None)
        assert client._sub_poll_rate() == 900

    def test_override_takes_precedence(self, client: CsipClient):
        client._reconcile_interval_seconds = 30  # fixed override
        client._state.poll_rates["sub"] = 300
        assert client._sub_poll_rate() == 30

    def test_constructor_default_follows_server(self):
        """The constructor default (0) follows the server pollRate rather than
        forcing a fixed 900s override, matching SubscriptionConfig's default."""
        c = CsipClient("https://example.com")
        assert c._reconcile_interval_seconds == 0
        c._state.poll_rates["sub"] = 120
        assert c._sub_poll_rate() == 120

    def test_override_is_clamped_to_safe_floor(self, client: CsipClient):
        client._reconcile_interval_seconds = 5  # below the 10s clamp
        assert client._sub_poll_rate() == 10


class TestRenewalInterval:
    """The configured renewal interval flows into start_renewal_task."""

    @pytest.mark.asyncio
    async def test_connect_passes_renewal_interval(self):
        mgr = MagicMock()
        mgr.active_subscriptions = [object()]
        mgr.validate_restored_subscriptions = AsyncMock(return_value=(1, 0))
        mgr.start_renewal_task = AsyncMock()
        client = CsipClient(
            "https://example.com",
            subscription_manager=mgr,
            renewal_interval_seconds=300,
        )
        with (
            patch("py20305.client.csip_client.discover", new_callable=AsyncMock),
            patch.object(client, "_auto_subscribe", new_callable=AsyncMock),
            patch.object(client, "_update_poll_suppression"),
        ):
            await client.connect()
        mgr.start_renewal_task.assert_called_once()
        assert mgr.start_renewal_task.call_args.kwargs.get("interval_seconds") == 300
        if client._renewal_task:
            client._renewal_task.cancel()


class TestCommsLossProbe:
    """Time-based loss-of-communications detection and recovery on CsipClient."""

    @staticmethod
    def _scheduled_keys(client: CsipClient) -> list[str]:
        keys: list[str] = []
        client._scheduler.schedule = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda key, *a, **k: keys.append(key)
        )
        client._start_polls()
        return keys

    def test_probe_scheduled_when_enabled(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        assert "comms_loss" in self._scheduled_keys(client)

    def test_probe_not_scheduled_when_disabled(self):
        client = CsipClient("https://example.com", comms_loss_seconds=0)
        assert "comms_loss" not in self._scheduled_keys(client)

    @pytest.mark.asyncio
    async def test_no_entry_before_first_contact(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._http._last_contact_epoch = None
        with patch.object(client, "_enter_comms_loss", new_callable=AsyncMock) as enter:
            await client._comms_loss_probe()
        enter.assert_not_awaited()
        assert client._comms_loss.active is False

    @pytest.mark.asyncio
    async def test_enters_after_threshold(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._http._last_contact_epoch = int(time.time()) - 1000
        with patch.object(
            client._event_processor, "enter_comms_loss", new_callable=AsyncMock
        ) as ep:
            await client._comms_loss_probe()
        assert client._comms_loss.active is True
        ep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_entry_below_threshold(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._http._last_contact_epoch = int(time.time()) - 100
        await client._comms_loss_probe()
        assert client._comms_loss.active is False

    @pytest.mark.asyncio
    async def test_reentry_is_idempotent(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.active = True
        with patch.object(
            client._event_processor, "enter_comms_loss", new_callable=AsyncMock
        ) as ep:
            await client._enter_comms_loss(1000)
        ep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recovers_on_fresh_contact(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.active = True
        client._comms_loss.resume_after_epoch = 12345
        client._own_lfdi = "ab" * 20
        client._http._last_contact_epoch = int(time.time())
        with (
            patch.object(
                client, "_server_lists_end_device", new_callable=AsyncMock, return_value=False
            ),
            patch.object(client, "register_end_device", new_callable=AsyncMock) as reg,
            patch.object(
                client, "trigger_rediscovery", new_callable=AsyncMock, return_value=True
            ) as redisc,
        ):
            await client._comms_loss_probe()
        reg.assert_awaited_once_with(lfdi="ab" * 20, check_duplicate=False)
        redisc.assert_awaited_once()
        assert client._comms_loss.active is False
        # Boundary 12345 is long past -> cleared with the mode.
        assert client._comms_loss.resume_after_epoch is None

    @pytest.mark.asyncio
    async def test_recovery_skips_reregister_when_edev_still_listed(self):
        """Servers that retain the EndDevice through an outage must not get a
        duplicate POST (IEEE 2030.5 s8.5.3); only removed EndDevices (e.g. by
        certain utility head-ends) are re-registered."""
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.active = True
        client._own_lfdi = "ab" * 20
        with (
            patch.object(
                client, "_server_lists_end_device", new_callable=AsyncMock, return_value=True
            ) as listed,
            patch.object(client, "register_end_device", new_callable=AsyncMock) as reg,
            patch.object(
                client, "trigger_rediscovery", new_callable=AsyncMock, return_value=True
            ) as redisc,
        ):
            await client._recover_from_comms_loss()
        listed.assert_awaited_once_with("ab" * 20)
        reg.assert_not_awaited()
        redisc.assert_awaited_once()  # recovery otherwise proceeds normally
        assert client._comms_loss.active is False

    @pytest.mark.asyncio
    async def test_server_lists_end_device(self):
        client = CsipClient("https://example.com")
        lfdi = "ab" * 20
        page = MagicMock()
        page.end_device = [MagicMock(l_fdi=bytes.fromhex(lfdi))]
        client._http.get_list = AsyncMock(return_value=[page])  # type: ignore[method-assign]
        assert await client._server_lists_end_device(lfdi, edev_list_href="/edev") is True

        page.end_device = [MagicMock(l_fdi=b"\x01" * 20)]
        assert await client._server_lists_end_device(lfdi, edev_list_href="/edev") is False

        # No EndDeviceList href known and none supplied -> ValueError
        with pytest.raises(ValueError, match="EndDeviceList"):
            await client._server_lists_end_device(lfdi)

    @pytest.mark.asyncio
    async def test_recovery_retains_future_boundary(self):
        """A boundary still in the future survives recovery: routine polls must
        keep skipping opted-out-window events until the window has passed."""
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.active = True
        boundary = int(time.time()) + 3600
        client._comms_loss.resume_after_epoch = boundary
        with patch.object(client, "trigger_rediscovery", new_callable=AsyncMock, return_value=True):
            await client._recover_from_comms_loss()
        assert client._comms_loss.active is False
        assert client._comms_loss.resume_after_epoch == boundary

    @pytest.mark.asyncio
    async def test_probe_clears_boundary_once_passed(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.resume_after_epoch = int(time.time()) - 10  # already passed
        client._http._last_contact_epoch = int(time.time())
        await client._comms_loss_probe()
        assert client._comms_loss.resume_after_epoch is None

    def test_eval_seconds_must_be_positive_when_enabled(self):
        with pytest.raises(ValueError, match="comms_loss_eval_seconds"):
            CsipClient("https://example.com", comms_loss_seconds=900, comms_loss_eval_seconds=0)
        # Disabled detection tolerates any cadence (probe never scheduled).
        CsipClient("https://example.com", comms_loss_seconds=0, comms_loss_eval_seconds=0)

    @pytest.mark.asyncio
    async def test_recovery_stays_in_mode_when_rediscovery_fails(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.active = True
        client._comms_loss.resume_after_epoch = 999
        client._own_lfdi = "ab" * 20
        with (
            patch.object(
                client, "_server_lists_end_device", new_callable=AsyncMock, return_value=False
            ),
            patch.object(client, "register_end_device", new_callable=AsyncMock),
            patch.object(
                client, "trigger_rediscovery", new_callable=AsyncMock, return_value=False
            ) as redisc,
        ):
            await client._recover_from_comms_loss()
        redisc.assert_awaited_once()
        # Rediscovery didn't complete -> stay gated so scheduled events remain
        # opted out; the boundary is preserved for the next-tick retry.
        assert client._comms_loss.active is True
        assert client._comms_loss.resume_after_epoch == 999

    @pytest.mark.asyncio
    async def test_recovery_skips_reregister_without_own_lfdi(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.active = True
        client._own_lfdi = None
        with (
            patch.object(client, "register_end_device", new_callable=AsyncMock) as reg,
            patch.object(
                client, "trigger_rediscovery", new_callable=AsyncMock, return_value=True
            ) as redisc,
        ):
            await client._recover_from_comms_loss()
        reg.assert_not_awaited()
        redisc.assert_awaited_once()  # still re-polls schedules
        assert client._comms_loss.active is False

    @pytest.mark.asyncio
    async def test_recovery_survives_reregister_failure(self):
        client = CsipClient("https://example.com", comms_loss_seconds=900)
        client._comms_loss.active = True
        client._own_lfdi = "cd" * 20
        with (
            patch.object(
                client, "_server_lists_end_device", new_callable=AsyncMock, return_value=False
            ),
            patch.object(
                client,
                "register_end_device",
                new_callable=AsyncMock,
                side_effect=Sep2ProtocolError("boom", 500),
            ),
            patch.object(
                client, "trigger_rediscovery", new_callable=AsyncMock, return_value=True
            ) as redisc,
        ):
            await client._recover_from_comms_loss()
        redisc.assert_awaited_once()  # reregister failure doesn't block recovery
        assert client._comms_loss.active is False

    @pytest.mark.asyncio
    async def test_connect_captures_own_lfdi(self):
        client = CsipClient("https://example.com")
        client._http._client_lfdi = "ef" * 20
        with patch("py20305.client.csip_client.discover", new_callable=AsyncMock):
            await client.connect()
        assert client._own_lfdi == "ef" * 20


class TestServerTimebasePlumbing:
    """Timebase creation, observation points, and comms-loss boundary adoption."""

    @pytest.mark.asyncio
    async def test_do_poll_time_observes(self):
        client = CsipClient("https://example.com", time_drift_warn_seconds=0)
        client._state.time_href = "/tm"
        t = MagicMock()
        t.current_time.value = int(time.time()) + 120
        t.quality = 3
        client._http.get = AsyncMock(return_value=t)  # type: ignore[method-assign]

        await client._do_poll_time()

        assert client.timebase.offset() == pytest.approx(120, abs=2)

    @pytest.mark.asyncio
    async def test_connectivity_probe_observes(self):
        client = CsipClient("https://example.com", time_drift_warn_seconds=0)
        client._state.time_href = "/tm"
        t = MagicMock()
        t.current_time.value = int(time.time()) - 60
        t.quality = 4
        client._http.get = AsyncMock(return_value=t)  # type: ignore[method-assign]

        await client._connectivity_probe()

        assert client.timebase.offset() == pytest.approx(-60, abs=2)

    @pytest.mark.asyncio
    async def test_comms_loss_boundary_honors_server_time(self):
        """A boundary in the local-clock future but already past per server
        time is cleared by the probe tidy-up."""
        client = CsipClient(
            "https://example.com", comms_loss_seconds=900, time_drift_warn_seconds=0
        )
        client._timebase.observe(int(time.time()) + 1000)
        client._comms_loss.resume_after_epoch = int(time.time()) + 500
        client._http._last_contact_epoch = int(time.time())

        await client._comms_loss_probe()

        assert client._comms_loss.resume_after_epoch is None

    def test_use_server_time_off_forces_local_clock(self):
        client = CsipClient("https://example.com", use_server_time=False, time_drift_warn_seconds=0)
        client._timebase.observe(int(time.time()) + 500)
        assert client.timebase.offset() == 0.0

    def test_timebase_shared_with_http_and_processor(self):
        client = CsipClient("https://example.com")
        assert client._http.timebase is client._timebase
        assert client._event_processor._timebase is client._timebase


@pytest.mark.asyncio
async def test_boundary_not_cleared_within_its_final_second(monkeypatch: pytest.MonkeyPatch):
    """Integer epoch comparison: a fractional now() inside the boundary's final
    second must not clear the resume-after gate early."""
    client = CsipClient("https://example.com", comms_loss_seconds=900, time_drift_warn_seconds=0)
    client._comms_loss.resume_after_epoch = 1000

    monkeypatch.setattr("time.time", lambda: 1000.5)  # int() == 1000, not past
    await client._comms_loss_probe()
    assert client._comms_loss.resume_after_epoch == 1000  # retained

    monkeypatch.setattr("time.time", lambda: 1001.5)  # int() == 1001 > 1000
    await client._comms_loss_probe()
    assert client._comms_loss.resume_after_epoch is None  # cleared


class TestPricingWiring:
    """Increment 4: the pricing.enabled flag drives tariff discovery/poll."""

    def test_flag_threads_to_state(self):
        enabled = CsipClient("https://example.com", pricing_enabled=True)
        assert enabled._state.pricing_enabled is True
        assert CsipClient("https://example.com")._state.pricing_enabled is False

    def test_tariff_poll_scheduled_only_when_enabled(self):
        for enabled in (True, False):
            client = CsipClient("https://example.com", pricing_enabled=enabled)
            client._state.poll_rates = {"dcap": 60, "edev": 60, "fsa": 60, "derp": 60, "time": 60}
            scheduled: list[str] = []
            client._scheduler.schedule = lambda key, rate, fn, s=scheduled: s.append(key)
            client._start_polls()
            assert ("tariff" in scheduled) is enabled

    @pytest.mark.asyncio
    async def test_do_poll_tariff_refreshes_then_processes(self, monkeypatch):
        client = CsipClient("https://example.com", pricing_enabled=True)
        order: list[str] = []

        async def fake_refresh(http, state):
            order.append("refresh")

        async def fake_process():
            order.append("process")

        monkeypatch.setattr("py20305.client.csip_client.refresh_tariffs", fake_refresh)
        client._tariff_processor.process_tariffs = fake_process
        await client._do_poll_tariff()
        assert order == ["refresh", "process"]
