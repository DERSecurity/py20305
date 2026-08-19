"""Tests for the schedule-notification relay.

Covers the BaseConnector transport hook + per-stream fan-out, and the
ConnectorDispatcher relay (connector resolution, instance de-dup, and
per-connector error isolation). Processor-level relay wiring is tested in
test_events_processor.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from py20305 import diagnostics
from py20305.connectors.base import BaseConnector, ScheduleNotification
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.diagnostics import DiagnosticsStore


def _notif(stream: str = "control", transition: str = "scheduled") -> ScheduleNotification:
    return ScheduleNotification(
        stream=stream,
        transition=transition,
        status="scheduled",
        current_status=0,
        mrid="ab" * 16,
        program_href="/derp/1",
        primacy=0,
        start=1,
        duration=2,
        end=3,
        affected_lfdis=["aa" * 20],
        randomization=(None, None),
        payload={},
    )


class _RecordingConnector(BaseConnector):
    def __init__(self) -> None:
        self.received: list[ScheduleNotification] = []

    async def on_schedule_notification(self, notification: ScheduleNotification) -> None:
        self.received.append(notification)


class _RaisingConnector(BaseConnector):
    async def on_schedule_notification(self, notification: ScheduleNotification) -> None:
        raise RuntimeError("boom")


def _registry_for(mapping: dict[str, BaseConnector]) -> Mock:
    """Fake ConnectorConfigRegistry: get_connector(lfdi) -> proxy.aresolve() -> connector."""
    registry = Mock()

    def get_connector(lfdi: str):
        connector = mapping.get(lfdi)
        if connector is None:
            return None
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=connector)
        return proxy

    registry.get_connector.side_effect = get_connector
    return registry


# -- BaseConnector transport hook -------------------------------------------


class TestScheduleNotificationHook:
    @pytest.mark.asyncio
    async def test_transport_dispatches_to_stream_method(self):
        seen: list[ScheduleNotification] = []

        class C(BaseConnector):
            async def notification_control(self, n: ScheduleNotification) -> None:
                seen.append(n)

        await C().on_schedule_notification(_notif(stream="control", transition="active"))
        assert len(seen) == 1
        assert seen[0].transition == "active"

    @pytest.mark.asyncio
    async def test_baseline_stream_routes_to_baseline_method(self):
        seen: list[str] = []

        class C(BaseConnector):
            async def notification_default_baseline(self, n: ScheduleNotification) -> None:
                seen.append(n.transition)

        await C().on_schedule_notification(
            _notif(stream="default_baseline", transition="default_added")
        )
        assert seen == ["default_added"]

    @pytest.mark.asyncio
    async def test_unknown_stream_is_noop(self):
        # No notification_<stream> method -> silently ignored, no error.
        await BaseConnector().on_schedule_notification(_notif(stream="nonexistent"))

    @pytest.mark.asyncio
    async def test_default_stream_methods_are_noop(self):
        c = BaseConnector()
        for stream in ("control", "default_baseline", "doe", "price", "drlc", "flow_reservation"):
            await c.on_schedule_notification(_notif(stream=stream))


# -- Dispatcher relay fan-out -----------------------------------------------


class TestRelayScheduleNotification:
    @pytest.mark.asyncio
    async def test_relays_to_each_connector(self):
        a, b = _RecordingConnector(), _RecordingConnector()
        dispatcher = ConnectorDispatcher(_registry_for({"a" * 40: a, "b" * 40: b}), lambda h: None)
        n = _notif()

        await dispatcher.relay_schedule_notification(["a" * 40, "b" * 40], n)

        assert a.received == [n]
        assert b.received == [n]

    @pytest.mark.asyncio
    async def test_relay_logs_delivery_at_debug(self, caplog):
        # The DEBUG relay line makes notification delivery observable for any
        # connector (e.g. the sunspec connector, which otherwise no-ops).
        a = _RecordingConnector()
        dispatcher = ConnectorDispatcher(_registry_for({"a" * 40: a}), lambda h: None)
        with caplog.at_level("DEBUG", logger="py20305.connectors.dispatcher"):
            await dispatcher.relay_schedule_notification(["a" * 40], _notif(transition="active"))
        assert any(
            "relay control/active" in r.message and "BaseConnector" in r.message
            for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_one_connector_many_lfdis_called_once(self):
        # A plugin that owns multiple LFDIs is called once (D8 de-dup).
        shared = _RecordingConnector()
        dispatcher = ConnectorDispatcher(
            _registry_for({"a" * 40: shared, "b" * 40: shared}), lambda h: None
        )

        await dispatcher.relay_schedule_notification(["a" * 40, "b" * 40], _notif())

        assert len(shared.received) == 1

    @pytest.mark.asyncio
    async def test_missing_connector_skipped(self):
        good = _RecordingConnector()
        dispatcher = ConnectorDispatcher(_registry_for({"b" * 40: good}), lambda h: None)

        await dispatcher.relay_schedule_notification(["a" * 40, "b" * 40], _notif())

        assert len(good.received) == 1

    @pytest.mark.asyncio
    async def test_connector_error_is_isolated(self, monkeypatch):
        store = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", store)
        bad, good = _RaisingConnector(), _RecordingConnector()
        dispatcher = ConnectorDispatcher(
            _registry_for({"a" * 40: bad, "b" * 40: good}), lambda h: None
        )

        # Must not raise, and the second connector must still be relayed to.
        await dispatcher.relay_schedule_notification(["a" * 40, "b" * 40], _notif())

        assert len(good.received) == 1
        warnings = store.snapshot()["warnings"]
        assert any("on_schedule_notification" in w["message"] for w in warnings)
