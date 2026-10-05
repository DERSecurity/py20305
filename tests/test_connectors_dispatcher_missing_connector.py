"""A device with no connector is reported once, not once per control.

A server can list a device this process has no connector for: one registered
by another client under the same identity, or one whose connector is not
configured yet. Every control aimed at that device reaches the dispatcher,
which finds nothing to send it to. The condition is worth a warning, and it is
the same condition each time, so it is reported when first seen and again only
after the device has gained a connector and lost it.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, Mock

import pytest

from py20305 import diagnostics
from py20305.connectors.base import BaseConnector
from py20305.connectors.dispatcher import ConnectorDispatcher

MANAGED = "aa" * 20
UNMANAGED = "bb" * 20
ALSO_UNMANAGED = "cc" * 20


class _Registry:
    """A registry whose set of devices with connectors can change."""

    def __init__(self, *lfdis: str) -> None:
        self.lfdis = set(lfdis)

    def get_connector(self, lfdi: str) -> Mock | None:
        if lfdi not in self.lfdis:
            return None
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=BaseConnector())
        return proxy


def _dispatcher(registry: _Registry) -> ConnectorDispatcher:
    return ConnectorDispatcher(registry=registry, lfdi_resolver=lambda href: None)  # type: ignore[arg-type]


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "py20305.diagnostics" and record.levelno == logging.WARNING
    ]


def _stored() -> list[str]:
    store = diagnostics.get_store()
    assert store is not None
    return [entry["message"] for entry in store.snapshot()["warnings"]]


@pytest.fixture(autouse=True)
def _store() -> None:
    diagnostics.init_store(diagnostics.DiagnosticsStore())


async def test_missing_connector_is_logged_once_across_repeated_dispatches(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = _dispatcher(_Registry(MANAGED))

    with caplog.at_level(logging.WARNING, logger="py20305.diagnostics"):
        for _ in range(5):
            await dispatcher.clear_control_by_lfdi(UNMANAGED)

    assert _warnings(caplog) == [
        f"No connector found for LFDI {UNMANAGED}; controls for this device are not applied"
    ]
    assert len(_stored()) == 1


async def test_each_device_without_a_connector_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = _dispatcher(_Registry(MANAGED))

    with caplog.at_level(logging.WARNING, logger="py20305.diagnostics"):
        for lfdi in (UNMANAGED, ALSO_UNMANAGED, UNMANAGED, ALSO_UNMANAGED):
            await dispatcher.clear_control_by_lfdi(lfdi)

    reported = _warnings(caplog)
    assert len(reported) == 2
    assert UNMANAGED in reported[0]
    assert ALSO_UNMANAGED in reported[1]


async def test_a_device_with_a_connector_is_not_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = _dispatcher(_Registry(MANAGED))

    with caplog.at_level(logging.WARNING, logger="py20305.diagnostics"):
        await dispatcher.clear_control_by_lfdi(MANAGED)

    assert _warnings(caplog) == []
    assert _stored() == []


async def test_warning_clears_when_the_device_gains_a_connector() -> None:
    registry = _Registry(MANAGED)
    dispatcher = _dispatcher(registry)
    await dispatcher.clear_control_by_lfdi(UNMANAGED)
    assert len(_stored()) == 1

    registry.lfdis.add(UNMANAGED)
    await dispatcher.clear_control_by_lfdi(UNMANAGED)

    assert _stored() == []


async def test_a_device_that_loses_its_connector_again_is_reported_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = _Registry(MANAGED)
    dispatcher = _dispatcher(registry)

    with caplog.at_level(logging.WARNING, logger="py20305.diagnostics"):
        await dispatcher.clear_control_by_lfdi(UNMANAGED)
        registry.lfdis.add(UNMANAGED)
        await dispatcher.clear_control_by_lfdi(UNMANAGED)
        registry.lfdis.discard(UNMANAGED)
        await dispatcher.clear_control_by_lfdi(UNMANAGED)
        await dispatcher.clear_control_by_lfdi(UNMANAGED)

    assert len(_warnings(caplog)) == 2
    assert len(_stored()) == 1


async def test_reporting_is_per_dispatcher() -> None:
    """Two dispatchers do not share what they have reported."""
    first = _dispatcher(_Registry())
    second = _dispatcher(_Registry())

    await first.clear_control_by_lfdi(UNMANAGED)
    await second.clear_control_by_lfdi(UNMANAGED)

    assert first._missing_connector_reported == {UNMANAGED}
    assert second._missing_connector_reported == {UNMANAGED}
