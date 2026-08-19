"""Tests for PrintDemoConnector."""

from __future__ import annotations

import pytest

from py20305.connectors.base import BaseConnector
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
        result = await c.fetch_monitoring()
        assert result["W"] == 10000
        assert result["Hz"] == 60
        assert result["PF"] == 0.98

    @pytest.mark.asyncio
    async def test_fetch_nameplate_returns_values(self):
        c = PrintDemoConnector()
        result = await c.fetch_nameplate()
        assert result["WMaxRtg"] == 15000
        assert result["VNomRtg"] == 480

    @pytest.mark.asyncio
    async def test_fetch_configuration_returns_values(self):
        c = PrintDemoConnector()
        result = await c.fetch_configuration()
        assert result["WMax"] == 10000

    @pytest.mark.asyncio
    async def test_fetch_status_returns_values(self):
        c = PrintDemoConnector()
        result = await c.fetch_status()
        assert result["alarmStatus"] == 1
        assert result["connectStatus"]["value"] == 1

    @pytest.mark.asyncio
    async def test_update_captures_params(self):
        c = PrintDemoConnector()
        params = {"qv_mode_enable": 1, "qv_vref": 240}
        await c.update_qv(params)
        assert c.last_control["update_qv"] == params

    @pytest.mark.asyncio
    async def test_update_records_multiple_methods(self):
        c = PrintDemoConnector()
        await c.update_pv({"pv_mode_enable": 1})
        await c.update_pf({"pf_mode_enable": 1})
        assert "update_pv" in c.last_control
        assert "update_pf" in c.last_control

    @pytest.mark.asyncio
    async def test_all_update_methods_record(self):
        c = PrintDemoConnector()
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
        for name in methods:
            await getattr(c, name)({"test": True})
            assert name in c.last_control, f"{name} should be recorded"


class TestPrintDemoReadingOverrides:
    """The demo reading_overrides() and its effect on the posted MUP/readings."""

    @pytest.mark.asyncio
    async def test_overrides_shape_their_readings(self):
        from py20305.telemetry.mup import create_meter_reading_list, create_mup

        c = PrintDemoConnector()
        mon = await c.fetch_monitoring()
        ov = c.reading_overrides()

        # ReadingType metadata: W overridden to Maximum (8); others default.
        mup = create_mup("1234567890abcdef1234567890abcdef12345678", mon, 300, overrides=ov)
        by_desc = {mmr.description: mmr.reading_type for mmr in mup.mirror_meter_reading}
        assert by_desc["Real Power"].data_qualifier.value == 8  # overridden
        assert by_desc["Voltage"].data_qualifier.value == 2  # untouched default

        # qualityFlags: W=derived (0x20), Hz=questionable (0x10), rest=valid (0x01).
        mrl = create_meter_reading_list(
            "1234567890abcdef1234567890abcdef12345678", mon, overrides=ov
        )
        qf = {mmr.description: mmr.reading.quality_flags for mmr in mrl.mirror_meter_reading}
        assert qf["Real Power"] == b"\x00\x20"
        assert qf["Frequency"] == b"\x00\x10"
        assert qf["Voltage"] == b"\x00\x01"
