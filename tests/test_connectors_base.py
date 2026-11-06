"""Tests for BaseConnector and error hierarchy."""

from __future__ import annotations

import pytest

from py20305.connectors.base import (
    BaseConnector,
    ConnectorConnectionError,
    ConnectorError,
    ConnectorTimeoutError,
    ConnectorWriteError,
)
from py20305.connectors.modes import ALL_ELEMENTS


class TestErrorHierarchy:
    def test_connector_error_is_exception(self):
        assert issubclass(ConnectorError, Exception)

    def test_connection_error_is_connector_error(self):
        assert issubclass(ConnectorConnectionError, ConnectorError)

    def test_timeout_error_is_connector_error(self):
        assert issubclass(ConnectorTimeoutError, ConnectorError)

    def test_write_error_is_connector_error(self):
        assert issubclass(ConnectorWriteError, ConnectorError)

    def test_errors_can_carry_message(self):
        err = ConnectorConnectionError("device unreachable")
        assert "device unreachable" in str(err)


class TestBaseConnector:
    def test_connector_name(self):
        c = BaseConnector()
        assert c.connector_name == "BaseConnector"

    @pytest.mark.asyncio
    async def test_fetch_monitoring_returns_no_readings(self):
        assert await BaseConnector().fetch_monitoring() == []

    @pytest.mark.asyncio
    async def test_fetch_nameplate_uses_der_capability_names(self):
        result = await BaseConnector().fetch_nameplate()
        assert "rtgMaxW" in result
        assert "modesSupported" in result
        assert all(value is None for value in result.values())

    @pytest.mark.asyncio
    async def test_fetch_configuration_uses_der_settings_names(self):
        result = await BaseConnector().fetch_configuration()
        assert "setMaxW" in result
        assert "modesEnabled" in result

    @pytest.mark.asyncio
    async def test_fetch_status_returns_dict(self):
        c = BaseConnector()
        result = await c.fetch_status()
        assert isinstance(result, dict)
        assert "alarmStatus" in result

    @pytest.mark.asyncio
    async def test_every_control_method_returns_none(self):
        c = BaseConnector()
        for element in ALL_ELEMENTS:
            result = await getattr(c, element.method)({"enabled": False})
            assert result is None, f"{element.method} should return None"

    @pytest.mark.asyncio
    async def test_all_methods_are_async(self):
        import asyncio

        c = BaseConnector()
        for attr_name in dir(c):
            if attr_name.startswith("update_") or attr_name.startswith("fetch_"):
                method = getattr(c, attr_name)
                assert asyncio.iscoroutinefunction(method), f"{attr_name} should be async"
