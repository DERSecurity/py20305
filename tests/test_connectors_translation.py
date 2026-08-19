"""Tests for ConnectorTranslation, translate_mup, and translate_to_sunspec."""

from __future__ import annotations

import pytest

from py20305.connectors.translation import (
    ConnectorTranslation,
    translate_mup,
    translate_to_sunspec,
)


@pytest.fixture
def tc():
    return ConnectorTranslation()


class TestTranslationQV:
    @pytest.mark.asyncio
    async def test_qv_points(self, tc):
        params = {
            "qv_mode_enable": 1,
            "qv_vref": 240,
            "qv_vref_auto_ena": 1,
            "qv_vref_olrt": 300,
            "qv_olrt": 500,
            "qv_curve_v_pts": [92, 98, 102, 108],
            "qv_curve_q_pts": [44, 0, 0, -44],
        }
        result = await tc.update_qv(params)
        assert result["DERVoltVar[0].Ena"] == 1
        assert result["DERVoltVar[0].Crv[1].VRef"] == 240
        assert result["DERVoltVar[0].Crv[1].ActPt"] == 4
        assert result["DERVoltVar[0].Crv[1].Pt[0].V"] == 92
        assert result["DERVoltVar[0].Crv[1].Pt[0].Var"] == 44


class TestTranslationPV:
    @pytest.mark.asyncio
    async def test_pv_uses_w_not_var(self, tc):
        params = {
            "pv_mode_enable": 1,
            "pv_olrt": 100,
            "pv_curve_v_pts": [106, 110],
            "pv_curve_p_pts": [100, 0],
        }
        result = await tc.update_pv(params)
        assert "DERVoltWatt[0].Crv[1].Pt[0].W" in result
        assert result["DERVoltWatt[0].Crv[1].Pt[0].W"] == 100


class TestTranslationQP:
    @pytest.mark.asyncio
    async def test_qp_points(self, tc):
        params = {
            "qp_mode_enable": 1,
            "qp_curve_p_pts": [20, 50],
            "qp_curve_q_pts": [44, 0],
        }
        result = await tc.update_qp(params)
        assert result["DERWattVar[0].Ena"] == 1
        assert result["DERWattVar[0].Crv[1].Pt[0].W"] == 20


class TestTranslationPLim:
    @pytest.mark.asyncio
    async def test_p_lim(self, tc):
        result = await tc.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 50})
        assert result["DERCtlAC[0].WMaxLimPctEna"] == 1
        assert result["DERCtlAC[0].WMaxLimPct"] == 50


class TestTranslationPF:
    @pytest.mark.asyncio
    async def test_pf(self, tc):
        result = await tc.update_pf(
            {
                "pf_mode_enable": 1,
                "pf_dbof": 0.5,
                "pf_dbuf": 0.5,
                "pf_kof": 0.04,
                "pf_kuf": 0.04,
                "pf_olrt": 10,
                "pf_pmin": 0,
            }
        )
        assert result["DERFreqDroop[0].Ena"] == 1
        assert result["DERFreqDroop[0].Ctl[1].DbOf"] == 0.5


class TestTranslationConstQ:
    @pytest.mark.asyncio
    async def test_enable(self, tc):
        result = await tc.update_const_q({"const_q_mode_enable": 1, "const_q_pct": 44})
        assert result["DERCtlAC[0].VarSetEna"] == 1
        assert result["DERCtlAC[0].VarSetPct"] == 44

    @pytest.mark.asyncio
    async def test_disable(self, tc):
        result = await tc.update_const_q({"const_q_mode_enable": 0})
        assert result["DERCtlAC[0].VarSetEna"] == 0


class TestTranslationConstPF:
    @pytest.mark.asyncio
    async def test_inject(self, tc):
        result = await tc.update_const_pf(
            {
                "inj": {"mode": 1, "pf": 0.95, "excitation": False},
                "abs": {"mode": 0},
            }
        )
        assert result["DERCtlAC[0].PFWInjEna"] == 1
        assert result["DERCtlAC[0].PFWInj.PF"] == 0.95
        assert result["DERCtlAC[0].PFWAbsEna"] == 0


class TestTranslationFixedW:
    @pytest.mark.asyncio
    async def test_fixed_w(self, tc):
        # WSetMod 0 is W_MAX_PCT, under which SunSpec 704 reads WSetPct. The
        # translator emits the percent under the "WSet" key, so this consumer
        # has to route it to the point the mode selects rather than copy the
        # key -- a percent in the watts point is unread here and would be read
        # as watts if the mode were later switched.
        result = await tc.update_fixed_w({"WSetEna": 1, "WSetMod": 0, "WSet": 50})
        assert result["DERCtlAC[0].WSetEna"] == 1
        assert result["DERCtlAC[0].WSetPct"] == 50
        assert "DERCtlAC[0].WSet" not in result

    @pytest.mark.asyncio
    async def test_fixed_w_watts_mode(self, tc):
        result = await tc.update_fixed_w({"WSetEna": 1, "WSetMod": 1, "WSet": 4000})
        assert result["DERCtlAC[0].WSet"] == 4000
        assert "DERCtlAC[0].WSetPct" not in result

    @pytest.mark.asyncio
    async def test_fixed_w_accepts_either_key(self, tc):
        result = await tc.update_fixed_w({"WSetEna": 1, "WSetMod": 0, "WSetPct": 50})
        assert result["DERCtlAC[0].WSetPct"] == 50


class TestTranslationRideThrough:
    @pytest.mark.asyncio
    async def test_ov(self, tc):
        result = await tc.update_ov(
            {
                "ov_mode_enable": 1,
                "ov_curve_tms_points": [1, 10],
                "ov_curve_v_pts": [120, 110],
            }
        )
        assert result["DERTripHV[0].Ena"] == 1
        assert result["DERTripHV[0].Crv[1].MustTrip.Pt[0].V"] == 120

    @pytest.mark.asyncio
    async def test_ov_mc(self, tc):
        result = await tc.update_ov_mc(
            {
                "ov_mode_enable": 1,
                "ov_curve_tms_points": [1],
                "ov_curve_v_pts": [115],
            }
        )
        assert "DERTripHV[0].Crv[1].MomCess.Pt[0].V" in result

    @pytest.mark.asyncio
    async def test_of(self, tc):
        result = await tc.update_of(
            {
                "of_mode_enable": 1,
                "of_curve_tms_points": [1, 5],
                "of_curve_f_pts": [61000, 60500],
            }
        )
        assert result["DERTripHF[0].Crv[1].MustTrip.Pt[0].Hz"] == 61000

    @pytest.mark.asyncio
    async def test_uf(self, tc):
        result = await tc.update_uf(
            {
                "uf_mode_enable": 1,
                "uf_curve_tms_points": [1],
                "uf_curve_f_pts": [59500],
            }
        )
        assert result["DERTripLF[0].Crv[1].MustTrip.Pt[0].Hz"] == 59500


class TestTranslateMUP:
    def test_watts_conversion(self):
        readings = [{"uom": 38, "value": 1000, "multiplier": 0}]
        result = translate_mup(readings)
        assert result["DERMeasureAC[0].W"] == 1000

    def test_frequency_conversion(self):
        readings = [{"uom": 33, "value": 60, "multiplier": 0}]
        result = translate_mup(readings)
        # Hz stored as mHz: 60 * 10^(0 - (-3)) = 60000
        assert result["DERMeasureAC[0].Hz"] == 60000

    def test_voltage_conversion(self):
        readings = [{"uom": 29, "value": 240, "multiplier": 0}]
        result = translate_mup(readings)
        # V stored as decivolts: 240 * 10^(0-(-1)) = 2400
        assert result["DERMeasureAC[0].LNV"] == 2400

    def test_unknown_uom_skipped(self):
        readings = [{"uom": 999, "value": 100}]
        result = translate_mup(readings)
        assert result == {}

    def test_none_value_skipped(self):
        readings = [{"uom": 38, "value": None}]
        result = translate_mup(readings)
        assert result == {}

    def test_multiple_readings(self):
        readings = [
            {"uom": 38, "value": 1000, "multiplier": 0},
            {"uom": 63, "value": 500, "multiplier": 0},
        ]
        result = translate_mup(readings)
        assert result["DERMeasureAC[0].W"] == 1000
        assert result["DERMeasureAC[0].Var"] == 500

    def test_with_ieee_multiplier(self):
        readings = [{"uom": 38, "value": 5, "multiplier": 3}]
        result = translate_mup(readings)
        # 5 * 10^(3-0) = 5000
        assert result["DERMeasureAC[0].W"] == 5000


class TestTranslateToSunspec:
    """Tests for the synchronous translate_to_sunspec() dispatch API."""

    def test_qv(self):
        params = {
            "qv_mode_enable": 1,
            "qv_vref": 240,
            "qv_vref_auto_ena": 1,
            "qv_vref_olrt": 300,
            "qv_olrt": 500,
            "qv_curve_v_pts": [92, 98, 102, 108],
            "qv_curve_q_pts": [44, 0, 0, -44],
        }
        result = translate_to_sunspec("update_qv", params)
        assert result is not None
        assert result["DERVoltVar[0].Ena"] == 1
        assert result["DERVoltVar[0].Crv[1].VRef"] == 240
        assert result["DERVoltVar[0].Crv[1].ActPt"] == 4
        assert result["DERVoltVar[0].Crv[1].Pt[0].V"] == 92
        assert result["DERVoltVar[0].Crv[1].Pt[3].Var"] == -44

    def test_pv(self):
        params = {
            "pv_mode_enable": 1,
            "pv_olrt": 100,
            "pv_curve_v_pts": [106, 110],
            "pv_curve_p_pts": [100, 0],
        }
        result = translate_to_sunspec("update_pv", params)
        assert result is not None
        assert result["DERVoltWatt[0].Ena"] == 1
        assert result["DERVoltWatt[0].Crv[1].Pt[0].W"] == 100

    def test_p_lim(self):
        result = translate_to_sunspec("update_p_lim", {"p_lim_mode_enable": 1, "p_lim_w": 50})
        assert result is not None
        assert result["DERCtlAC[0].WMaxLimPctEna"] == 1
        assert result["DERCtlAC[0].WMaxLimPct"] == 50

    def test_const_pf(self):
        result = translate_to_sunspec(
            "update_const_pf",
            {
                "inj": {"mode": 1, "pf": 0.95, "excitation": False},
                "abs": {"mode": 0},
            },
        )
        assert result is not None
        assert result["DERCtlAC[0].PFWInjEna"] == 1
        assert result["DERCtlAC[0].PFWInj.PF"] == 0.95
        assert result["DERCtlAC[0].PFWAbsEna"] == 0

    def test_pf_freq_droop(self):
        result = translate_to_sunspec(
            "update_pf",
            {
                "pf_mode_enable": 1,
                "pf_dbof": 0.5,
                "pf_dbuf": 0.5,
                "pf_kof": 0.04,
                "pf_kuf": 0.04,
                "pf_olrt": 10,
                "pf_pmin": 0,
            },
        )
        assert result is not None
        assert result["DERFreqDroop[0].Ena"] == 1
        assert result["DERFreqDroop[0].Ctl[1].DbOf"] == 0.5

    def test_unknown_method_returns_none(self):
        result = translate_to_sunspec("update_nonexistent", {"foo": 1})
        assert result is None

    def test_matches_async_output(self):
        """Sync dispatch produces identical output to async connector methods."""
        params = {"p_lim_mode_enable": 1, "p_lim_w": 75}
        sync_result = translate_to_sunspec("update_p_lim", params)

        import asyncio

        tc = ConnectorTranslation()
        # `asyncio.get_event_loop()` raises on Python 3.12+ when there's no
        # current loop (and any earlier-running test that called
        # `asyncio.run` will have left the slot empty). `asyncio.run`
        # handles its own loop lifecycle and is the modern API.
        async_result = asyncio.run(tc.update_p_lim(params))
        assert sync_result == async_result
