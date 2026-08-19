"""Tests for DerResourceManager lifecycle and PUT operations."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305.telemetry.der_resource_manager import DerResourceManager


def _aresolver(connector):
    """Async resolver helper for DerResourceManager tests.

    Mirrors the current contract: the manager's ``connector_resolver`` is
    async (so first-touch construction can be offloaded to a worker thread
    via ``LazyConnectorProxy.aresolve`` in production). Tests still want to
    pass a plain mock; this wraps it in the async signature.

    Returns the same connector instance on every call. For the rare test
    that wants a fresh mock per cycle, write an explicit async closure
    rather than passing a factory here -- ``MagicMock`` instances are
    callable, so a generic factory-vs-instance branch can't tell them
    apart safely.
    """

    async def _resolve(_lfdi: str):
        return connector

    return _resolve


def _make_connector(
    nameplate: dict[str, Any] | None = None,
    configuration: dict[str, Any] | None = None,
    status: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock connector with async fetch methods."""
    connector = MagicMock()
    connector.fetch_nameplate = AsyncMock(return_value=nameplate or {"WMaxRtg": 15000})
    connector.fetch_configuration = AsyncMock(return_value=configuration or {"WMax": 10000})
    connector.fetch_status = AsyncMock(
        return_value=status
        or {
            "readingTime": 1000,
            "genConnectStatus": {"dateTime": 1000, "value": 1},
            "inverterStatus": {"dateTime": 1000, "value": 3},
            "operationalModeStatus": {"dateTime": 1000, "value": 1},
        }
    )
    return connector


class TestDerResourceManagerStart:
    """Tests for start_device scheduling."""

    @pytest.mark.asyncio
    async def test_start_device_with_all_hrefs(self) -> None:
        client = AsyncMock()
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device(
            "aabbccdd",
            capability_href="/cap",
            settings_href="/set",
            status_href="/stat",
            capability_poll_rate=300,
            settings_poll_rate=300,
            status_poll_rate=300,
        )

        assert "aabbccdd" in manager.active_devices
        await manager.shutdown()

    @pytest.mark.asyncio
    async def test_start_device_with_no_hrefs(self) -> None:
        client = AsyncMock()
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device(
            "aabbccdd",
            capability_href=None,
            settings_href=None,
            status_href=None,
        )

        assert "aabbccdd" in manager.active_devices

    @pytest.mark.asyncio
    async def test_lfdi_normalized_to_lower(self) -> None:
        client = AsyncMock()
        manager = DerResourceManager(client, _aresolver(_make_connector()))

        manager.start_device("AABBCCDD", None, None, None)

        assert "aabbccdd" in manager.active_devices


class TestDerResourceManagerCapabilityCycle:
    """Tests for capability PUT cycle."""

    @pytest.mark.asyncio
    async def test_capability_puts_xml(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        connector = _make_connector(nameplate={"WMaxRtg": 15000})
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", "/cap", None, None)
        await manager._capability_cycle("ab")

        client.put_bytes.assert_called_once()
        path, body = client.put_bytes.call_args[0]
        assert path == "/cap"
        assert b"<DERCapability" in body
        assert b"<rtgMaxW>" in body

    @pytest.mark.asyncio
    async def test_capability_error_does_not_propagate(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(side_effect=RuntimeError("network"))
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", "/cap", None, None)
        # Should not raise
        await manager._capability_cycle("ab")

    @pytest.mark.asyncio
    async def test_capability_no_href_noop(self) -> None:
        client = AsyncMock()
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", None, None, None)
        await manager._capability_cycle("ab")

        client.put_bytes.assert_not_called()


class TestDerResourceManagerSettingsCycle:
    """Tests for settings PUT cycle."""

    @pytest.mark.asyncio
    async def test_settings_puts_xml(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        connector = _make_connector(configuration={"WMax": 10000})
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", None, "/set", None)
        await manager._settings_cycle("ab")

        client.put_bytes.assert_called_once()
        path, body = client.put_bytes.call_args[0]
        assert path == "/set"
        assert b"<DERSettings" in body
        assert b"<setMaxW>" in body

    @pytest.mark.asyncio
    async def test_settings_error_does_not_propagate(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(side_effect=RuntimeError("network"))
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", None, "/set", None)
        await manager._settings_cycle("ab")

    @pytest.mark.asyncio
    async def test_settings_missing_wmax_skips_put_with_actionable_warning(self) -> None:
        # Regression: a connector whose fetch_configuration omits the
        # required WMax (e.g. one based on the pre-fix print_demo example) must
        # not crash the cycle with a full traceback every tick. The cycle should
        # skip the PUT and emit one actionable, traceback-free warning.
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        connector = _make_connector(configuration={"VAMax": 5000})  # no WMax
        manager = DerResourceManager(client, _aresolver(connector))
        manager.start_device("ab", None, "/set", None)

        with patch("py20305.diagnostics.report") as mock_report:
            await manager._settings_cycle("ab")

        client.put_bytes.assert_not_called()
        mock_report.assert_called_once()
        args, kwargs = mock_report.call_args
        assert args[0] == "warnings"
        assert "WMax" in args[1]
        # A known connector-contract problem, not a crash: no traceback, and a
        # distinct dedup key from the generic-exception path.
        assert not kwargs.get("exc_info", False)
        assert kwargs.get("dedup_key", "").endswith("settings_config")


class TestDerResourceManagerStatusCycle:
    """Tests for status PUT cycle."""

    @pytest.mark.asyncio
    async def test_status_puts_xml(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", None, None, "/stat")
        await manager._status_cycle("ab")

        client.put_bytes.assert_called_once()
        path, body = client.put_bytes.call_args[0]
        assert path == "/stat"
        assert b"<DERStatus" in body

    @pytest.mark.asyncio
    async def test_status_error_does_not_propagate(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(side_effect=RuntimeError("network"))
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", None, None, "/stat")
        await manager._status_cycle("ab")


class TestDerResourceManagerChangeDetection:
    """Tests for change detection on DERSettings (DERCapability always PUTs)."""

    @pytest.mark.asyncio
    async def test_capability_always_puts_every_cycle(self) -> None:
        """DERCapability always PUTs, even when data is unchanged."""
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        connector = _make_connector(nameplate={"WMaxRtg": 15000})
        manager = DerResourceManager(client, _aresolver(connector))

        # Register device without scheduling (avoids background task interference)
        manager.start_device("ab", None, None, None)
        manager._devices["ab"].capability_href = "/cap"

        await manager._capability_cycle("ab")
        await manager._capability_cycle("ab")

        assert client.put_bytes.call_count == 2

    @pytest.mark.asyncio
    async def test_settings_skips_put_when_unchanged(self) -> None:
        """Second settings cycle with same data skips the PUT."""
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        config = {"WMax": 10000, "VAMax": 20000}
        connector = _make_connector(configuration=config)
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", None, "/set", None)
        await manager._settings_cycle("ab")
        await manager._settings_cycle("ab")

        assert client.put_bytes.call_count == 1

    @pytest.mark.asyncio
    async def test_settings_puts_when_changed(self) -> None:
        """Settings PUT fires again when connector data changes."""
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        connector = _make_connector(configuration={"WMax": 10000})
        manager = DerResourceManager(client, _aresolver(connector))

        manager.start_device("ab", None, "/set", None)
        await manager._settings_cycle("ab")

        connector.fetch_configuration = AsyncMock(return_value={"WMax": 8000})
        await manager._settings_cycle("ab")

        assert client.put_bytes.call_count == 2

    @pytest.mark.asyncio
    async def test_status_always_puts(self) -> None:
        """DERStatus has no change detection -- always PUTs."""
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        status = {
            "readingTime": 1000,
            "genConnectStatus": {"dateTime": 1000, "value": 1},
        }
        connector = _make_connector(status=status)
        manager = DerResourceManager(client, _aresolver(connector))

        # Register device without scheduling (avoids background task interference)
        manager.start_device("ab", None, None, None)
        manager._devices["ab"].status_href = "/stat"

        await manager._status_cycle("ab")
        await manager._status_cycle("ab")

        assert client.put_bytes.call_count == 2


class TestDerResourceManagerPerResourceRates:
    """Tests for per-resource poll rates."""

    @pytest.mark.asyncio
    async def test_different_rates_per_resource(self) -> None:
        """Each resource type uses its own poll rate."""
        client = AsyncMock()
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        with patch.object(manager._scheduler, "schedule") as mock_schedule:
            manager.start_device(
                "ab",
                "/cap",
                "/set",
                "/stat",
                capability_poll_rate=86400,
                settings_poll_rate=60,
                status_poll_rate=120,
            )

            assert mock_schedule.call_count == 3
            # Extract (key, interval) from each call
            calls = {args[0]: args[1] for args, _ in (c for c in mock_schedule.call_args_list)}
            assert calls["dercap_ab"] == 86400
            assert calls["derset_ab"] == 60
            assert calls["derstat_ab"] == 120

    @pytest.mark.asyncio
    async def test_default_rates(self) -> None:
        """Default rates: 86400 for cap, 60 for settings, 300 for status."""
        client = AsyncMock()
        connector = _make_connector()
        manager = DerResourceManager(client, _aresolver(connector))

        with patch.object(manager._scheduler, "schedule") as mock_schedule:
            manager.start_device("ab", "/cap", "/set", "/stat")

            calls = {args[0]: args[1] for args, _ in (c for c in mock_schedule.call_args_list)}
            assert calls["dercap_ab"] == 86400
            assert calls["derset_ab"] == 60
            assert calls["derstat_ab"] == 300


class TestDerResourceManagerShutdown:
    """Tests for shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_devices(self) -> None:
        client = AsyncMock()
        manager = DerResourceManager(client, _aresolver(_make_connector()))

        manager.start_device("ab", "/cap", "/set", "/stat")
        assert len(manager.active_devices) == 1

        with patch.object(manager._scheduler, "cancel_all", new_callable=AsyncMock):
            await manager.shutdown()

        assert len(manager.active_devices) == 0

    @pytest.mark.asyncio
    async def test_shutdown_cancels_scheduler(self) -> None:
        client = AsyncMock()
        manager = DerResourceManager(client, _aresolver(_make_connector()))

        manager.start_device("ab", "/cap", None, None)

        with patch.object(manager._scheduler, "cancel_all", new_callable=AsyncMock) as mock_cancel:
            await manager.shutdown()
            mock_cancel.assert_called_once()


class _RecordingForwarder:
    """Stands in for the forwarder manager, keeping what was queued."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def queue_event(self, event: Any) -> None:
        self.events.append(event)


def _make_telemetry() -> tuple[Any, _RecordingForwarder]:
    """A live DeviceTelemetryEmitter backed by a recording forwarder."""
    from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
    from py20305.forwarders.config import DeviceTelemetryConfig

    fw = _RecordingForwarder()
    emitter = DeviceTelemetryEmitter(fw, DeviceTelemetryConfig(enabled=True), client_id="site-a")
    return emitter, fw


def _points_of(event: Any) -> dict[str, Any]:
    import json

    return json.loads(event.payload["payload"]["data"])["points"]


class TestReadsAreReported:
    """The manager's own capability/settings/status reads are southbound
    traffic no measurement source covers; without this seam a monitoring
    system never sees them."""

    @pytest.mark.asyncio
    async def test_capability_cycle_reports_the_nameplate_read(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        emitter, fw = _make_telemetry()
        manager = DerResourceManager(
            client,
            _aresolver(_make_connector(nameplate={"WMaxRtg": 15000})),
            device_telemetry=emitter,
        )

        manager.start_device("ab", "/cap", None, None)
        await manager._capability_cycle("ab")

        assert len(fw.events) == 1
        assert fw.events[0].payload["direction"] == "upstream"
        assert _points_of(fw.events[0]) == {"WMaxRtg": 15000}

    @pytest.mark.asyncio
    async def test_settings_cycle_reports_the_configuration_read(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        emitter, fw = _make_telemetry()
        manager = DerResourceManager(
            client,
            _aresolver(_make_connector(configuration={"WMax": 10000})),
            device_telemetry=emitter,
        )

        manager.start_device("ab", None, "/set", None)
        await manager._settings_cycle("ab")

        assert len(fw.events) == 1
        assert _points_of(fw.events[0]) == {"WMax": 10000}

    @pytest.mark.asyncio
    async def test_status_cycle_reports_the_status_read(self) -> None:
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        emitter, fw = _make_telemetry()
        manager = DerResourceManager(
            client, _aresolver(_make_connector()), device_telemetry=emitter
        )

        manager.start_device("ab", None, None, "/stat")
        await manager._status_cycle("ab")

        assert len(fw.events) == 1
        assert "genConnectStatus" in _points_of(fw.events[0])

    @pytest.mark.asyncio
    async def test_without_the_kwarg_cycles_run_unchanged(self) -> None:
        """No emitter, no reporting -- and no change to the PUT path."""
        client = AsyncMock()
        client.put_bytes = AsyncMock(return_value=200)
        manager = DerResourceManager(client, _aresolver(_make_connector()))

        manager.start_device("ab", "/cap", None, None)
        await manager._capability_cycle("ab")

        client.put_bytes.assert_called_once()
