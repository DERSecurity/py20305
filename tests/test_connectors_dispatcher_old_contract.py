"""A connector that defines methods the client no longer calls is reported once."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from py20305.connectors.base import BaseConnector
from py20305.connectors.dispatcher import ConnectorDispatcher, unused_update_methods

LFDI = "aa" * 20


class _OldContractConnector(BaseConnector):
    async def update_qv(self, params: dict[str, Any]) -> None:
        return None

    async def update_p_lim(self, params: dict[str, Any]) -> None:
        return None


class _CurrentConnector(BaseConnector):
    async def update_op_mod_max_lim_w(self, params: dict[str, Any]) -> None:
        return None


class _Registry:
    def __init__(self, connector: BaseConnector) -> None:
        self.connector = connector

    def get_connector(self, lfdi: str) -> Mock | None:
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=self.connector)
        return proxy


def _dispatcher(connector: BaseConnector) -> ConnectorDispatcher:
    return ConnectorDispatcher(
        registry=_Registry(connector),  # type: ignore[arg-type]
        lfdi_resolver=lambda href: None,
    )


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "py20305.connectors.dispatcher" and r.levelno == logging.WARNING
    ]


def test_methods_outside_the_contract_are_listed() -> None:
    assert unused_update_methods(_OldContractConnector) == ["update_p_lim", "update_qv"]


def test_a_connector_that_follows_the_contract_has_no_unused_methods() -> None:
    assert unused_update_methods(_CurrentConnector) == []
    assert unused_update_methods(BaseConnector) == []


async def test_an_old_contract_connector_is_warned_about_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = _dispatcher(_OldContractConnector())

    with caplog.at_level(logging.WARNING, logger="py20305.connectors.dispatcher"):
        for _ in range(3):
            await dispatcher.clear_control_by_lfdi(LFDI)

    (message,) = _warnings(caplog)
    assert "_OldContractConnector" in message
    assert "update_p_lim" in message and "update_qv" in message
    assert "no longer calls" in message


async def test_a_connector_that_follows_the_contract_is_not_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    dispatcher = _dispatcher(_CurrentConnector())

    with caplog.at_level(logging.WARNING, logger="py20305.connectors.dispatcher"):
        await dispatcher.clear_control_by_lfdi(LFDI)

    assert _warnings(caplog) == []
