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
    async def test_fetch_monitoring_returns_null_dict(self):
        c = BaseConnector()
        result = await c.fetch_monitoring()
        assert isinstance(result, dict)
        assert set(result.keys()) == {"W", "Var", "Hz", "V", "PF", "VA", "A"}
        assert all(v is None for v in result.values())

    def test_reading_overrides_default_none(self):
        # Connectors opt in; the default declares no overrides.
        assert BaseConnector().reading_overrides() is None

    @pytest.mark.asyncio
    async def test_fetch_nameplate_returns_null_dict(self):
        c = BaseConnector()
        result = await c.fetch_nameplate()
        assert isinstance(result, dict)
        assert "WMaxRtg" in result
        assert "CtrlModes" in result

    @pytest.mark.asyncio
    async def test_fetch_configuration_returns_null_dict(self):
        c = BaseConnector()
        result = await c.fetch_configuration()
        assert isinstance(result, dict)
        assert "WMax" in result

    @pytest.mark.asyncio
    async def test_fetch_status_returns_dict(self):
        c = BaseConnector()
        result = await c.fetch_status()
        assert isinstance(result, dict)
        assert "alarmStatus" in result

    @pytest.mark.asyncio
    async def test_all_update_methods_return_none(self):
        c = BaseConnector()
        methods = [
            "update_qv",
            "update_pv",
            "update_qp",
            "update_p_lim",
            "update_p_lim_inj",
            "update_p_lim_abs",
            "update_pf",
            "update_const_q",
            "update_const_pf",
            "update_ov",
            "update_uv",
            "update_ov_mc",
            "update_uv_mc",
            "update_of",
            "update_uf",
            "update_fixed_w",
            "update_p_ramp",
            "update_es_permit_service",
            "update_exp_lim",
            "update_imp_lim",
            "update_gen_lim",
            "update_load_lim",
            "update_pricing_mode",
        ]
        for method_name in methods:
            method = getattr(c, method_name)
            result = await method({})
            assert result is None, f"{method_name} should return None"

    @pytest.mark.asyncio
    async def test_all_methods_are_async(self):
        import asyncio

        c = BaseConnector()
        for attr_name in dir(c):
            if attr_name.startswith("update_") or attr_name.startswith("fetch_"):
                method = getattr(c, attr_name)
                assert asyncio.iscoroutinefunction(method), f"{attr_name} should be async"
