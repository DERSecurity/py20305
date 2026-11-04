"""Tests for PrintDemoConnector."""

from __future__ import annotations

import pytest

from py20305.connectors.base import BaseConnector
from py20305.connectors.modes import ALL_ELEMENTS
from py20305.connectors.print_demo import PrintDemoConnector


class TestPrintDemoConnector:
    def test_is_base_connector(self):
        c = PrintDemoConnector()
        assert isinstance(c, BaseConnector)

    def test_connector_name(self):
        c = PrintDemoConnector()
        assert c.connector_name == "PrintDemoConnector"

    @pytest.mark.asyncio
    async def test_fetch_monitoring_returns_realistic_values(self):
        c = PrintDemoConnector()
        result = {reading.uom: reading.value for reading in await c.fetch_monitoring()}
        assert result[38] == 10000
        assert result[33] == 60
        assert result[65] == 0.98

    @pytest.mark.asyncio
    async def test_fetch_nameplate_returns_values(self):
        c = PrintDemoConnector()
        result = await c.fetch_nameplate()
        assert result["rtgMaxW"] == 15000
        assert result["rtgVNom"] == 480

    @pytest.mark.asyncio
    async def test_fetch_configuration_returns_values(self):
        c = PrintDemoConnector()
        result = await c.fetch_configuration()
        assert result["setMaxW"] == 10000

    @pytest.mark.asyncio
    async def test_fetch_status_returns_values(self):
        c = PrintDemoConnector()
        result = await c.fetch_status()
        assert result["alarmStatus"] == 1
        assert result["connectStatus"]["value"] == 1

    @pytest.mark.asyncio
    async def test_update_captures_params(self):
        c = PrintDemoConnector()
        params = {"enabled": True, "value": {"curveType": 11}}
        await c.update_op_mod_volt_var(params)
        assert c.last_control["update_op_mod_volt_var"] == params

    @pytest.mark.asyncio
    async def test_update_records_multiple_methods(self):
        c = PrintDemoConnector()
        await c.update_op_mod_volt_watt({"enabled": False})
        await c.update_op_mod_freq_droop({"enabled": False})
        assert "update_op_mod_volt_watt" in c.last_control
        assert "update_op_mod_freq_droop" in c.last_control

    @pytest.mark.asyncio
    async def test_all_update_methods_record(self):
        c = PrintDemoConnector()
        methods = [element.method for element in ALL_ELEMENTS]
        for name in methods:
            await getattr(c, name)({"test": True})
            assert name in c.last_control, f"{name} should be recorded"


class TestPrintDemoReadings:
    """The demo readings and their effect on the posted MUP and readings."""

    @pytest.mark.asyncio
    async def test_readings_shape_their_reading_types_and_quality(self):
        from py20305.telemetry.mup import create_meter_reading_list, create_mup

        lfdi = "1234567890abcdef1234567890abcdef12345678"
        readings = await PrintDemoConnector().fetch_monitoring()

        mup = create_mup(lfdi, readings, 300)
        by_desc = {mmr.description: mmr.reading_type for mmr in mup.mirror_meter_reading}
        assert by_desc["Real Power max"].data_qualifier.value == 8
        assert by_desc["Voltage L1"].phase.value == 129

        mrl = create_meter_reading_list(lfdi, readings)
        qf = {mmr.description: mmr.reading.quality_flags for mmr in mrl.mirror_meter_reading}
        assert qf["Real Power max"] == b"\x00\x20"
        assert qf["Frequency"] == b"\x00\x10"
        assert qf["Voltage L1"] == b"\x00\x01"

    def test_every_control_method_is_implemented(self):
        from py20305.connectors.dispatcher import ConnectorDispatcher

        c = PrintDemoConnector()
        for element in ALL_ELEMENTS:
            method, reason = ConnectorDispatcher._control_support(c, element.method)
            assert method is not None and reason is None, element.method
