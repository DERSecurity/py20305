"""Tests for ConnectorSunSpec.

Since pysunspec2 may not be available, these tests mock the SunSpec client
to verify Modbus register writes and adopt polling.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from py20305.connectors.base import (
    ConnectorTimeoutError,
    ConnectorValueError,
    ConnectorWriteError,
)
from py20305.connectors.modes import translate_const_q, translate_fixed_w
from py20305.models.sep.sep import (
    DercontrolBase,
    DerunitRefType,
    FixedVarControlType,
    SignedPerCent,
    SignedPerCentControlType,
)


def _make_mock_target():
    """Build a mock SunSpec client device."""
    target = MagicMock()
    target.scan = MagicMock()

    def make_model(mid):
        """Create a mock model with common attributes."""
        model = MagicMock()
        model.read = MagicMock()

        # Curve/control support
        crv = MagicMock()
        crv.ActPt = MagicMock()
        crv.DeptRef = MagicMock()
        crv.Pri = MagicMock()
        crv.VRef = MagicMock()
        crv.VRefAutoEna = MagicMock()
        crv.VRefAutoTms = MagicMock()
        crv.RspTms = MagicMock()
        crv.write = MagicMock()

        pts = [MagicMock() for _ in range(10)]
        for pt in pts:
            pt.V = MagicMock()
            pt.Var = MagicMock()
            pt.W = MagicMock()
        crv.Pt = pts
        model.Crv = {1: crv}

        # Control
        ctl = MagicMock()
        ctl.write = MagicMock()
        model.Ctl = {1: ctl}

        # Adopt
        model.AdptCrvReq = MagicMock()
        model.AdptCrvRslt = MagicMock()
        model.AdptCrvRslt.value = 1  # success
        model.AdptCtlReq = MagicMock()
        model.AdptCtlRslt = MagicMock()
        model.AdptCtlRslt.value = 1

        # Enable points
        model.Ena = MagicMock()
        model.Ena.cvalue = 1
        model.PFWInjEna = MagicMock()
        model.PFWInjEna.cvalue = 1
        model.PFWAbsEna = MagicMock()
        model.PFWAbsEna.cvalue = 1

        # P limit
        model.WMaxLimPct = MagicMock()

        # Const Q
        model.VarSetPct = MagicMock()
        model.VarSetMod = MagicMock()
        model.VarSetPri = MagicMock()

        # Const PF
        model.PFWInj = MagicMock()
        model.PFWAbs = MagicMock()

        # Telemetry (model 701)
        for attr in ["W", "Var", "Hz", "LLV", "PF", "VA", "A", "ConnSt", "InvSt", "St", "Alrm"]:
            setattr(model, attr, MagicMock(cvalue=42))
        # Default to ACType / per-line points unpopulated -- MagicMock
        # auto-creates attributes on access which would otherwise make
        # ``_fetch_monitoring_sync`` think a 3-phase device is attached
        # to every fixture. Tests that want per-line data override these
        # specific attributes inline.
        model.ACType = MagicMock(value=None)
        for line in (1, 2, 3):
            for prefix in ("WL", "VarL", "VL", "PFL", "VAL", "AL"):
                setattr(model, f"{prefix}{line}", MagicMock(cvalue=None))

        # Nameplate / settings (model 702) -- connector reads .value and scale factor points
        # Watt-rated points
        for attr in [
            "WMaxRtg",
            "WOvrExtRtg",
            "WUndExtRtg",
            "WChaRteMaxRtg",
            "WMax",
            "WMaxOvrExt",
            "WMaxUndExt",
            "WChaRteMax",
        ]:
            setattr(model, attr, MagicMock(value=5000, cvalue=5000))
        # VA-rated points
        for attr in ["VAMaxRtg", "VAChaRteMaxRtg", "VAMax", "VAChaRteMax"]:
            setattr(model, attr, MagicMock(value=6000, cvalue=6000))
        # Var-rated points
        for attr in ["VarMaxInjRtg", "VarMaxAbsRtg", "VarMaxInj", "VarMaxAbs"]:
            setattr(model, attr, MagicMock(value=3000, cvalue=3000))
        # Voltage-rated points
        for attr in ["VNomRtg", "VMaxRtg", "VMinRtg", "VNom", "VMax", "VMin"]:
            setattr(model, attr, MagicMock(value=240, cvalue=240))
        # Power factor points (displacement)
        for attr in ["WOvrExtRtgPF", "WUndExtRtgPF", "WOvrExtPF", "WUndExtPF"]:
            setattr(model, attr, MagicMock(value=950, cvalue=950))
        # Reactive susceptance
        model.ReactSusceptRtg = MagicMock(value=100, cvalue=100)
        # Direct integer points
        model.NorOpCatRtg = MagicMock(cvalue=1)
        model.AbnOpCatRtg = MagicMock(cvalue=2)
        model.CtrlModes = MagicMock(cvalue=0xFF)
        # Scale factor points
        model.W_SF = MagicMock(value=-1)
        model.VA_SF = MagicMock(value=-1)
        model.Var_SF = MagicMock(value=-1)
        model.V_SF = MagicMock(value=-1)
        model.PF_SF = MagicMock(value=-3)
        model.S_SF = MagicMock(value=-2)

        return model

    models = {}
    for mid in [701, 702, 704, 705, 706, 711, 712]:
        models[mid] = [make_model(mid)]
    target.models = models

    return target


@pytest.fixture
def sunspec_connector():
    """Create a ConnectorSunSpec with mocked pysunspec2."""
    mock_target = _make_mock_target()

    with patch.dict(
        "sys.modules",
        {
            "sunspec2": MagicMock(),
            "sunspec2.modbus": MagicMock(),
            "sunspec2.modbus.client": MagicMock(),
        },
    ):
        import sunspec2.modbus.client as mock_client

        mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

        from py20305.connectors.sunspec import ConnectorSunSpec

        connector = ConnectorSunSpec(host="127.0.0.1", port=8502)
        connector._target = mock_target
        return connector


class TestSunSpecFetch:
    @pytest.mark.asyncio
    async def test_fetch_monitoring(self, sunspec_connector):
        result = await sunspec_connector.fetch_monitoring()
        assert "W" in result
        assert result["W"] == 42

    @pytest.mark.asyncio
    async def test_fetch_monitoring_omits_actype_when_unpopulated(self, sunspec_connector):
        """ACType.value=None (the fixture default) must not surface ACType
        in the monitoring dict -- the MUP layer treats a missing key as
        "system readings only", and a None value would be even worse if
        it accidentally got pushed onto the wire."""
        result = await sunspec_connector.fetch_monitoring()
        assert "ACType" not in result

    @pytest.mark.asyncio
    async def test_fetch_monitoring_omits_per_line_when_unpopulated(self, sunspec_connector):
        """Per-line keys must NOT appear when the device hasn't populated
        the line points. A single-phase inverter that happens to expose
        the L2/L3 register block as None shouldn't trigger a per-line
        reading block."""
        result = await sunspec_connector.fetch_monitoring()
        for line in (1, 2, 3):
            for prefix in ("WL", "VarL", "VL", "PFL", "VAL", "AL"):
                assert f"{prefix}{line}" not in result

    @pytest.mark.asyncio
    async def test_fetch_monitoring_three_phase_emits_all_lines(self, sunspec_connector):
        """A three-phase device with populated L1/L2/L3 points emits
        ACType=2 and all eighteen per-line metric keys."""
        model = sunspec_connector._target.models[701][0]
        model.ACType = MagicMock(value=2)
        for line in (1, 2, 3):
            for prefix in ("WL", "VarL", "VL", "PFL", "VAL", "AL"):
                setattr(model, f"{prefix}{line}", MagicMock(cvalue=11 + line))
        result = await sunspec_connector.fetch_monitoring()
        assert result["ACType"] == 2
        for line in (1, 2, 3):
            for prefix in ("WL", "VarL", "VL", "PFL", "VAL", "AL"):
                key = f"{prefix}{line}"
                assert key in result, key
                assert result[key] == 11 + line

    @pytest.mark.asyncio
    async def test_fetch_monitoring_split_phase_omits_l3(self, sunspec_connector):
        """A split-phase device populates L1+L2 only; the L3 block must
        be dropped even though the SunSpec model defines those registers."""
        model = sunspec_connector._target.models[701][0]
        model.ACType = MagicMock(value=1)
        for line in (1, 2):
            for prefix in ("WL", "VarL", "VL", "PFL", "VAL", "AL"):
                setattr(model, f"{prefix}{line}", MagicMock(cvalue=20 + line))
        # L3 stays at the fixture default (cvalue=None).
        result = await sunspec_connector.fetch_monitoring()
        assert result["ACType"] == 1
        assert "WL1" in result
        assert "WL2" in result
        assert "WL3" not in result
        assert "VL3" not in result

    def test_lock_rebinds_when_event_loop_changes(self, sunspec_connector):
        """A connector reused across event loops must NOT raise the
        "Lock bound to a different event loop" RuntimeError. Regression
        for session-scoped fixtures + per-test loops in the integration
        suite -- the lock now rebinds lazily to the running loop.
        """
        import asyncio

        async def acquire_once():
            await sunspec_connector.fetch_monitoring()

        loop_a = asyncio.new_event_loop()
        try:
            loop_a.run_until_complete(acquire_once())
            lock_a = sunspec_connector._lock
            loop_bound_a = sunspec_connector._lock_loop
        finally:
            loop_a.close()

        loop_b = asyncio.new_event_loop()
        try:
            loop_b.run_until_complete(acquire_once())  # must not raise
            lock_b = sunspec_connector._lock
            loop_bound_b = sunspec_connector._lock_loop
        finally:
            loop_b.close()

        assert lock_a is not lock_b
        assert loop_bound_a is loop_a
        assert loop_bound_b is loop_b

    @pytest.mark.asyncio
    async def test_fetch_nameplate(self, sunspec_connector):
        result = await sunspec_connector.fetch_nameplate()
        assert "WMaxRtg" in result

    @pytest.mark.asyncio
    async def test_fetch_nameplate_returns_value_multiplier_dicts(self, sunspec_connector):
        """Nameplate fields should be {value, multiplier} dicts (not pre-scaled scalars)."""
        result = await sunspec_connector.fetch_nameplate()
        wmax = result["WMaxRtg"]
        assert isinstance(wmax, dict), f"Expected dict, got {type(wmax)}"
        assert "value" in wmax and "multiplier" in wmax
        assert isinstance(wmax["value"], int)
        assert isinstance(wmax["multiplier"], int)

    @pytest.mark.asyncio
    async def test_fetch_nameplate_pf_fields_use_displacement(self, sunspec_connector):
        """Power factor fields should use displacement key, not value."""
        result = await sunspec_connector.fetch_nameplate()
        pf = result["WOvrExtRtgPF"]
        assert isinstance(pf, dict)
        assert "displacement" in pf and "multiplier" in pf
        assert "value" not in pf

    @pytest.mark.asyncio
    async def test_fetch_nameplate_direct_int_fields(self, sunspec_connector):
        """Category and modes fields should be plain integers from .cvalue."""
        result = await sunspec_connector.fetch_nameplate()
        assert isinstance(result["NorOpCatRtg"], int)
        assert isinstance(result["AbnOpCatRtg"], int)
        assert isinstance(result["CtrlModes"], int)

    @pytest.mark.asyncio
    async def test_fetch_nameplate_doe_modes_export_only(self, sunspec_connector):
        """SunSpec 704 only enforces an export (inject) active-power limit, so it
        advertises doeModesSupported = export only (0x01). Only sent in CSIP-AUS
        mode (the DERCapability builder gates the element)."""
        result = await sunspec_connector.fetch_nameplate()
        assert result["DoeModesSupported"] == 0x01

    @pytest.mark.asyncio
    async def test_fetch_nameplate_wmax_none_raises(self, sunspec_connector):
        """WMaxRtg=None must raise ConnectorConnectionError (required field)."""
        from py20305.connectors.base import ConnectorConnectionError

        model = sunspec_connector._target.models[702][0]
        model.WMaxRtg.value = None
        with pytest.raises(ConnectorConnectionError, match="WMaxRtg"):
            await sunspec_connector.fetch_nameplate()

    @pytest.mark.asyncio
    async def test_fetch_nameplate_wsf_none_raises(self, sunspec_connector):
        """W_SF=None must raise ConnectorConnectionError (scale factor required)."""
        from py20305.connectors.base import ConnectorConnectionError

        model = sunspec_connector._target.models[702][0]
        model.W_SF.value = None
        with pytest.raises(ConnectorConnectionError, match="W_SF"):
            await sunspec_connector.fetch_nameplate()

    @pytest.mark.asyncio
    async def test_fetch_nameplate_optional_none_excluded(self, sunspec_connector):
        """Optional fields with None .value should be silently omitted."""
        model = sunspec_connector._target.models[702][0]
        model.WOvrExtRtgPF.value = None
        result = await sunspec_connector.fetch_nameplate()
        assert "WOvrExtRtgPF" not in result
        # Required and other optional fields should still be present
        assert "WMaxRtg" in result
        assert "VAMaxRtg" in result

    @pytest.mark.asyncio
    async def test_fetch_status(self, sunspec_connector):
        result = await sunspec_connector.fetch_status()
        assert "alarmStatus" in result

    @pytest.mark.asyncio
    async def test_fetch_configuration(self, sunspec_connector):
        result = await sunspec_connector.fetch_configuration()
        assert "WMax" in result

    @pytest.mark.asyncio
    async def test_fetch_configuration_doe_modes_export_only(self, sunspec_connector):
        """doeModesEnabled stays consistent with supported -- export only (0x01),
        since SunSpec 704 can't enforce more."""
        result = await sunspec_connector.fetch_configuration()
        assert result["DoeModesEnabled"] == 0x01

    @pytest.mark.asyncio
    async def test_fetch_configuration_returns_value_multiplier_dicts(self, sunspec_connector):
        """Configuration fields should be {value, multiplier} dicts."""
        result = await sunspec_connector.fetch_configuration()
        wmax = result["WMax"]
        assert isinstance(wmax, dict)
        assert "value" in wmax and "multiplier" in wmax

    @pytest.mark.asyncio
    async def test_fetch_configuration_none_value_excluded(self, sunspec_connector):
        """Fields with None .value should be omitted from configuration dict."""
        model = sunspec_connector._target.models[702][0]
        model.WMax.value = None
        model.WOvrExtPF.value = None
        result = await sunspec_connector.fetch_configuration()
        assert "WMax" not in result
        assert "WOvrExtPF" not in result
        assert "VAMax" in result


class TestSunSpecUpdate:
    @pytest.mark.asyncio
    async def test_update_qv(self, sunspec_connector):
        params = {
            "qv_mode_enable": 1,
            "qv_vref": 240,
            "qv_vref_auto_ena": 1,
            "qv_vref_olrt": 300,
            "qv_olrt": 500,
            "qv_curve_v_pts": [92, 98, 102, 108],
            "qv_curve_q_pts": [44, 0, 0, -44],
        }
        await sunspec_connector.update_qv(params)
        # Verify curve was written
        model = sunspec_connector._target.models[705][0]
        model.Crv[1].write.assert_called()

    @pytest.mark.asyncio
    async def test_update_pv(self, sunspec_connector):
        params = {
            "pv_mode_enable": 1,
            "pv_olrt": 100,
            "pv_curve_v_pts": [106, 110],
            "pv_curve_p_pts": [100, 0],
        }
        await sunspec_connector.update_pv(params)
        model = sunspec_connector._target.models[706][0]
        # Verify W (not Var) is used - bug fix
        crv = model.Crv[1]
        crv.Pt[0].W.cvalue.__eq__(100)

    @pytest.mark.asyncio
    async def test_update_p_lim(self, sunspec_connector):
        params = {"p_lim_mode_enable": 1, "p_lim_w": 50}
        await sunspec_connector.update_p_lim(params)
        model = sunspec_connector._target.models[704][0]
        model.write.assert_called()

    @pytest.mark.asyncio
    async def test_update_pf(self, sunspec_connector):
        params = {
            "pf_mode_enable": 1,
            "pf_dbof": 0.5,
            "pf_dbuf": 0.5,
            "pf_kof": 0.04,
            "pf_kuf": 0.04,
            "pf_olrt": 10,
            "pf_min": None,
        }
        await sunspec_connector.update_pf(params)

    @pytest.mark.asyncio
    async def test_update_pf_fractional_cvalues_not_truncated(self, sunspec_connector):
        """Fractional engineering values must reach pysunspec2 verbatim.

        Regression: int() casts in _update_pf_sync truncated sub-integer
        IEEE 2030.5 engineering values (pf_dbof=0.036, pf_kof=0.5) to zero.
        """
        params = {
            "pf_mode_enable": 1,
            "pf_dbof": 0.036,
            "pf_dbuf": 0.036,
            "pf_kof": 0.5,
            "pf_kuf": 0.5,
            "pf_olrt": 3.0,
            "pf_pmin": 25,
        }
        await sunspec_connector.update_pf(params)
        model = sunspec_connector._target.models[711][0]
        ctl = model.Ctl[1]
        assert ctl.DbOf.cvalue == 0.036
        assert ctl.DbUf.cvalue == 0.036
        assert ctl.KOf.cvalue == 0.5
        assert ctl.KUf.cvalue == 0.5
        assert ctl.RspTms.cvalue == 3.0
        assert ctl.PMin.cvalue == 25

    @pytest.mark.asyncio
    async def test_update_qv_fractional_curve_points_not_truncated(self, sunspec_connector):
        """Volt-Var curve V points (pu) must reach Crv[1].Pt[*] unrounded.

        Regression: int(v) in _update_qv_sync truncated pu V points
        (0.95 -> 0). An EMS-realistic Volt-Var curve with xMultiplier=-2
        produces float V points like 0.92, 0.98, 1.02, 1.08.
        """
        params = {
            "qv_mode_enable": 1,
            "qv_vref": 100.5,
            "qv_vref_auto_ena": 0,
            "qv_vref_olrt": 30.0,
            "qv_olrt": 3.5,
            "qv_curve_v_pts": [0.92, 0.98, 1.02, 1.08],
            "qv_curve_q_pts": [0.44, 0.0, 0.0, -0.44],
        }
        await sunspec_connector.update_qv(params)
        model = sunspec_connector._target.models[705][0]
        crv = model.Crv[1]
        assert crv.VRef.cvalue == 100.5
        assert crv.RspTms.cvalue == 3.5
        assert crv.VRefAutoTms.cvalue == 30.0
        for i, expected_v in enumerate([0.92, 0.98, 1.02, 1.08]):
            assert crv.Pt[i].V.cvalue == expected_v
        for i, expected_q in enumerate([0.44, 0.0, 0.0, -0.44]):
            assert crv.Pt[i].Var.cvalue == expected_q

    @pytest.mark.asyncio
    async def test_update_pv_fractional_curve_points_not_truncated(self, sunspec_connector):
        """Volt-Watt curve V points (pu) must reach Crv[1].Pt[*] unrounded."""
        params = {
            "pv_mode_enable": 1,
            "pv_olrt": 1.2,
            "pv_curve_v_pts": [1.06, 1.10],
            "pv_curve_p_pts": [1.0, 0.0],
        }
        await sunspec_connector.update_pv(params)
        model = sunspec_connector._target.models[706][0]
        crv = model.Crv[1]
        assert crv.RspTms.cvalue == 1.2
        assert crv.Pt[0].V.cvalue == 1.06
        assert crv.Pt[0].W.cvalue == 1.0
        assert crv.Pt[1].V.cvalue == 1.10
        assert crv.Pt[1].W.cvalue == 0.0

    @pytest.mark.asyncio
    async def test_update_qp_fractional_curve_points_not_truncated(self, sunspec_connector):
        """Watt-Var curve W/Var points (pu) must reach Crv[1].Pt[*] unrounded."""
        params = {
            "qp_mode_enable": 1,
            "qp_curve_p_pts": [0.0, 0.5, 1.0],
            "qp_curve_q_pts": [0.0, 0.3, 0.44],
        }
        await sunspec_connector.update_qp(params)
        model = sunspec_connector._target.models[712][0]
        crv = model.Crv[1]
        for i, (expected_p, expected_q) in enumerate(
            zip([0.0, 0.5, 1.0], [0.0, 0.3, 0.44], strict=True)
        ):
            assert crv.Pt[i].W.cvalue == expected_p
            assert crv.Pt[i].Var.cvalue == expected_q

    @pytest.mark.asyncio
    async def test_update_pv_curve_exceeds_device_npt_raises(self, sunspec_connector):
        """A volt-watt curve (model 706) with more points than the device's
        curve block holds must raise a clear ConnectorWriteError, not the
        opaque IndexError the per-point write loop would otherwise throw
        (device NPt=2, control sends 3)."""
        model = sunspec_connector._target.models[706][0]
        model.Crv[1].Pt = [MagicMock(), MagicMock()]  # device holds 2 points
        params = {
            "pv_mode_enable": 1,
            "pv_curve_v_pts": [1.02, 1.06, 1.10],
            "pv_curve_p_pts": [1.0, 0.5, 0.0],
        }
        with pytest.raises(ConnectorWriteError, match="holds only 2"):
            await sunspec_connector.update_pv(params)

    @pytest.mark.asyncio
    async def test_update_qv_curve_exceeds_device_npt_raises(self, sunspec_connector):
        """Volt-Var (model 705): same over-capacity guard."""
        model = sunspec_connector._target.models[705][0]
        model.Crv[1].Pt = [MagicMock(), MagicMock()]
        params = {
            "qv_mode_enable": 1,
            "qv_curve_v_pts": [0.92, 0.98, 1.08],
            "qv_curve_q_pts": [0.44, 0.0, -0.44],
        }
        with pytest.raises(ConnectorWriteError, match="holds only 2"):
            await sunspec_connector.update_qv(params)

    @pytest.mark.asyncio
    async def test_update_qp_curve_exceeds_device_npt_raises(self, sunspec_connector):
        """Watt-Var (model 712): same over-capacity guard."""
        model = sunspec_connector._target.models[712][0]
        model.Crv[1].Pt = [MagicMock(), MagicMock()]
        params = {
            "qp_mode_enable": 1,
            "qp_curve_p_pts": [0.0, 0.5, 1.0],
            "qp_curve_q_pts": [0.0, 0.3, 0.44],
        }
        with pytest.raises(ConnectorWriteError, match="holds only 2"):
            await sunspec_connector.update_qp(params)

    @pytest.mark.asyncio
    async def test_update_p_lim_fractional_pct_not_truncated(self, sunspec_connector):
        """opModMaxLimW yields a fractional percent (e.g. 62.5%) that must
        not be int()-cast to 62 before reaching WMaxLimPct.cvalue."""
        params = {"p_lim_mode_enable": 1, "p_lim_w": 62.5}
        await sunspec_connector.update_p_lim(params)
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 62.5

    @pytest.mark.asyncio
    async def test_update_const_q(self, sunspec_connector):
        # ref_type 2 = IEEE %setMaxVar -> SunSpec VarSetMod 1 (VAR_MAX_PCT).
        params = {"const_q_mode_enable": 1, "const_q_pct": 44, "ref_type": 2}
        await sunspec_connector.update_const_q(params)
        model = sunspec_connector._target.models[704][0]
        assert model.VarSetPct.cvalue == 44
        assert model.VarSetMod.value == 1

    @pytest.mark.asyncio
    async def test_update_const_q_unsupported_ref_type_not_applied(self, sunspec_connector, caplog):
        """An unmapped DERUnitRefType must not silently apply the percent
        against a default (wrong) base -- warn and skip the write instead."""
        import logging

        model = sunspec_connector._target.models[704][0]
        model.write.reset_mock()
        with caplog.at_level(logging.WARNING):
            # ref_type 5 (%setMaxChargeRateW) has no var-mode equivalent.
            await sunspec_connector.update_const_q(
                {"const_q_mode_enable": 1, "const_q_pct": 44, "ref_type": 5}
            )
        model.write.assert_not_called()
        assert any("DERUnitRefType" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_update_fixed_w_enabled(self, sunspec_connector):
        # WSet is a percent of WMax (50.0 == 50%), written straight to
        # WSetPct.cvalue -- no *100 (that drove 50% to 5000% on the wire).
        # WSetMod=0 is SunSpec 704's W_MAX_PCT (percent) mode.
        params = {"WSetEna": 1, "WSetMod": 0, "WSet": 50.0}
        await sunspec_connector.update_fixed_w(params)
        model = sunspec_connector._target.models[704][0]
        # ``WSetMod`` is an enum16; idiomatic pysunspec2 writes via
        # ``.value`` (not ``.cvalue``) for enums.
        assert model.WSetMod.value == 0
        assert model.WSetPct.cvalue == 50.0
        model.write.assert_called()

    @pytest.mark.asyncio
    async def test_update_const_q_fractional_pct_not_truncated(self, sunspec_connector):
        """``const_q_pct`` is always int today (per ``OpModFixedVar.value``
        being IEEE PerCent), but the same ``int()``-on-cvalue pattern that
        bit ``_update_pf_sync`` would re-introduce silent zero truncation
        if a fractional ever flows through. Locks the contract."""
        params = {"const_q_mode_enable": 1, "const_q_pct": 44.5, "ref_type": 3}
        await sunspec_connector.update_const_q(params)
        model = sunspec_connector._target.models[704][0]
        assert model.VarSetPct.cvalue == 44.5  # NOT 44

    @pytest.mark.asyncio
    async def test_update_fixed_w_disabled(self, sunspec_connector):
        params = {"WSetEna": 0}
        await sunspec_connector.update_fixed_w(params)
        model = sunspec_connector._target.models[704][0]
        # When disabled, write() should not be called after the enable point
        model.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_target_w_enabled(self, sunspec_connector):
        # opModTargetW carries absolute watts (scaled value * 10**multiplier
        # from the IEEE 2030.5 ActivePower struct). WSetMod=1 selects 704's
        # WATTS branch so the inverter reads the setpoint from WSet, not
        # WSetPct.
        params = {"mode_enable": 1, "watts": 2000}
        await sunspec_connector.update_target_w(params)
        model = sunspec_connector._target.models[704][0]
        assert model.WSetMod.value == 1
        assert model.WSet.cvalue == 2000
        model.write.assert_called()

    @pytest.mark.asyncio
    async def test_update_target_w_disabled(self, sunspec_connector):
        # mode_enable=0 only flips WSetEna (via _update_enable). The sync
        # worker must early-return before the WSetMod / WSet writes so a
        # stale setpoint from a prior call cannot leak through.
        params = {"mode_enable": 0}
        await sunspec_connector.update_target_w(params)
        model = sunspec_connector._target.models[704][0]
        model.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_target_w_missing_watts_does_not_enable(self, sunspec_connector):
        # mode_enable=1 with no watts key is a malformed payload. The sync
        # worker must skip the WHOLE update -- including the WSetEna write --
        # so the inverter is never left with WSetEna=1 sitting on top of a
        # stale WSet from a previous call. Asserts on both the model write
        # and the enable-point write that `_update_enable` would do.
        params = {"mode_enable": 1}
        await sunspec_connector.update_target_w(params)
        model = sunspec_connector._target.models[704][0]
        model.write.assert_not_called()
        # _update_enable writes via point.write(); when we skip it,
        # WSetEna.write must not be called either.
        model.WSetEna.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_target_w_fractional_watts_not_truncated(self, sunspec_connector):
        # The translator can emit a float (value * 10**multiplier with a
        # negative multiplier). int() casts elsewhere in this file have
        # caused silent truncation regressions before; lock the contract.
        params = {"mode_enable": 1, "watts": 1234.5}
        await sunspec_connector.update_target_w(params)
        model = sunspec_connector._target.models[704][0]
        assert model.WSet.cvalue == 1234.5  # NOT 1234

    @pytest.mark.asyncio
    async def test_update_const_pf_inject(self, sunspec_connector):
        params = {
            "inj": {"mode": True, "pf": 0.95, "excitation": False},
            "abs": {"mode": False},
        }
        await sunspec_connector.update_const_pf(params)


class TestSunSpecConstPFRange:
    """A power factor displacement above unity is invalid event data, not a
    value to be clamped. IEEE 2030.5 requires 0.0..1.0 inclusive (sep2_schema_2023.xsd
    opModFixedPFInjectW / setMinPFOverExcited). Model 704's PF point is a
    scaled uint16, so an out-of-range displacement encodes cleanly and the
    device silently discards or reverts it -- which the aggregator cannot
    see, because only the enable point is read back. Refuse the write so the
    event is reported as rejected instead of started."""

    @pytest.mark.asyncio
    async def test_inject_above_unity_refused(self, sunspec_connector):
        model = sunspec_connector._target.models[704][0]
        model.PFWInj.PF.cvalue = 0.9  # a prior, legitimate setpoint

        with pytest.raises(ConnectorValueError, match=r"1\.1"):
            await sunspec_connector.update_const_pf(
                {"inj": {"mode": True, "pf": 1.1, "excitation": False}, "abs": {"mode": False}}
            )

        # Nothing reached the device, and the previous setpoint is untouched.
        assert model.PFWInj.PF.cvalue == 0.9
        model.write.assert_not_called()
        # The guard runs before _update_enable, so the enable bit must not have
        # been raised for a write that never happens.
        model.PFWInjEna.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_absorb_above_unity_refused(self, sunspec_connector):
        model = sunspec_connector._target.models[704][0]
        model.PFWAbs.PF.cvalue = 0.9

        with pytest.raises(ConnectorValueError, match=r"1\.1"):
            await sunspec_connector.update_const_pf(
                {"inj": {"mode": False}, "abs": {"mode": True, "pf": 1.1, "excitation": True}}
            )

        assert model.PFWAbs.PF.cvalue == 0.9
        model.write.assert_not_called()
        model.PFWAbsEna.write.assert_not_called()

    @pytest.mark.parametrize("pf", [-0.5, -1.0, -1.5])
    @pytest.mark.asyncio
    async def test_negative_displacement_refused(self, sunspec_connector, pf):
        """Any negative displacement is refused, not just one below -1.

        The reactive direction rides on ``excitation``, not on the sign, so a
        negative is invalid input. -0.5 and -1.0 are the cases a [-1.0, 1.0] bound
        would have admitted: model 704's PF points are unsigned, so those reach
        the encoder and fail there -- after the mode-enable point was written,
        stranding the lever over a stale setpoint. That is the failure this guard
        exists to prevent, so it must catch them before any write.
        """
        model = sunspec_connector._target.models[704][0]
        model.PFWInj.PF.cvalue = 0.9  # a prior, legitimate setpoint

        with pytest.raises(ConnectorValueError):
            await sunspec_connector.update_const_pf(
                {"inj": {"mode": True, "pf": pf, "excitation": False}, "abs": {"mode": False}}
            )

        assert model.PFWInj.PF.cvalue == 0.9
        model.write.assert_not_called()
        model.PFWInjEna.write.assert_not_called()

    @pytest.mark.parametrize(
        "pf",
        [
            pytest.param(10**400, id="int-too-large-for-float"),
            pytest.param(float("nan"), id="nan"),
            pytest.param(float("inf"), id="inf"),
            pytest.param(True, id="bool"),
            pytest.param("0.9", id="string"),
        ],
    )
    @pytest.mark.asyncio
    async def test_unusable_displacement_refused(self, sunspec_connector, pf):
        """Every unusable value reports 253, not an untyped failure.

        The model layer types the displacement as a bare ``int``, so a
        non-conformant head-end can send something the profile's UInt16 could
        never hold. Unguarded, ``float(10**400)`` raises OverflowError, which is
        untyped and would be reported as 251 -- losing exactly the distinction
        this validation adds.
        """
        model = sunspec_connector._target.models[704][0]

        with pytest.raises(ConnectorValueError):
            await sunspec_connector.update_const_pf(
                {"inj": {"mode": True, "pf": pf, "excitation": False}, "abs": {"mode": False}}
            )

        model.write.assert_not_called()
        model.PFWInjEna.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_unity_is_accepted(self, sunspec_connector):
        # 1.0 is the inclusive bound, not an off-by-one rejection.
        model = sunspec_connector._target.models[704][0]
        await sunspec_connector.update_const_pf(
            {"inj": {"mode": True, "pf": 1.0, "excitation": False}, "abs": {"mode": False}}
        )
        assert model.PFWInj.PF.cvalue == 1.0
        model.write.assert_called()

    @pytest.mark.asyncio
    async def test_in_range_still_writes(self, sunspec_connector):
        model = sunspec_connector._target.models[704][0]
        await sunspec_connector.update_const_pf(
            {"inj": {"mode": True, "pf": 0.95, "excitation": False}, "abs": {"mode": False}}
        )
        assert model.PFWInj.PF.cvalue == 0.95
        model.write.assert_called()


class TestSunSpecPLimPercentRange:
    """opModMaxLimW is a PerCent (UInt16, hundredths, 0-10000), so the
    percent the translator derives is 0-100. A value outside that is invalid
    event data: nothing upstream bounds it (the generated model types it as a
    bare int and inbound XML is not schema-validated), and WMaxLimPct is an
    unsigned register -- a negative dies inside pysunspec2's encoder *after*
    the enable bit was raised, and a value above 100 encodes cleanly as a
    nonsense limit.

    Note the deliberate contrast with opModMaxLimWInject, which clamps a
    derived percent above 100 (see TestSunSpecPLimMerge): there the input is
    valid watts and only the conversion overflows the register, so reducing it
    still yields a limit. Here the input itself is outside the range the
    profile declares."""

    @pytest.mark.asyncio
    async def test_negative_percent_refused(self, sunspec_connector):
        model = sunspec_connector._target.models[704][0]

        with pytest.raises(ConnectorValueError, match="-5"):
            await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": -5})

        model.write.assert_not_called()
        model.WMaxLimPctEna.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_percent_above_100_refused(self, sunspec_connector):
        model = sunspec_connector._target.models[704][0]

        with pytest.raises(ConnectorValueError, match="200"):
            await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 200})

        model.write.assert_not_called()
        model.WMaxLimPctEna.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_bad_percent_does_not_disturb_an_active_limit(self, sunspec_connector):
        """A rejected value must not clobber a limit already in force."""
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 60})
        model = sunspec_connector._target.models[704][0]

        with pytest.raises(ConnectorValueError):
            await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 150})

        assert model.WMaxLimPct.cvalue == 60

    @pytest.mark.asyncio
    async def test_bounds_are_inclusive(self, sunspec_connector):
        model = sunspec_connector._target.models[704][0]
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 100})
        assert model.WMaxLimPct.cvalue == 100
        # 0% is a legitimate full curtailment, not a missing value.
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 0})
        assert model.WMaxLimPct.cvalue == 0

    @pytest.mark.asyncio
    async def test_disable_ignores_the_percent(self, sunspec_connector):
        """Clearing the lever carries no meaningful value; a stale or absent
        percent must not turn a teardown into a rejection."""
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 0, "p_lim_w": 999})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPctEna.cvalue == 0

    @pytest.mark.asyncio
    async def test_inject_still_clamps_rather_than_refusing(self, sunspec_connector):
        """Regression guard on the asymmetry: opModMaxLimWInject carries watts,
        so a value above the device's WMax is a valid limit whose derived
        percent overflows -- it clamps to 100 and is applied."""
        # Fixture WMax = 5000 W; 9000 W -> 180% -> clamped to 100%.
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 9000})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 100


class TestSunSpecPLimMerge:
    """opModMaxLimW{,Inject,Absorb} all map onto a single WMaxLimPct
    register; the connector tracks the three IEEE 2030.5 controls
    independently and applies the most restrictive inject-direction
    cap. Absorb is recorded for diagnostics but never reaches a
    register because SunSpec model 704 has no absorb-direction limit."""

    @pytest.mark.asyncio
    async def test_inject_alone_writes_register(self, sunspec_connector):
        # opModMaxLimWInject is absolute watts; the connector converts to a
        # percent of WMax (fixture WMax=5000), so 4000 W -> 80%.
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 80

    @pytest.mark.asyncio
    async def test_any_alone_writes_register(self, sunspec_connector):
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 75})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 75

    @pytest.mark.asyncio
    async def test_inject_then_any_writes_min(self, sunspec_connector):
        """opModMaxLimW arriving after opModMaxLimWInject must NOT
        clobber the inject value; the connector picks the more
        restrictive limit of the two."""
        # inj 4000 W / WMax 5000 -> 80%; any 60% is more restrictive.
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 60})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 60

    @pytest.mark.asyncio
    async def test_any_then_inject_writes_min(self, sunspec_connector):
        """Reverse ordering: opModMaxLimW first, then opModMaxLimWInject
        with a higher value -- the lower (any=60) wins."""
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 60})
        # inj 4000 W / WMax 5000 -> 80%; any 60% still wins.
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 60

    @pytest.mark.asyncio
    async def test_clearing_one_slot_keeps_the_other(self, sunspec_connector):
        """Clearing opModMaxLimWInject while opModMaxLimW remains active
        must keep the inject-direction cap on, at the remaining value."""
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 60})
        # Clear inj only -- the merge should fall back to any=60.
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 0})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 60
        # The WMaxLimPctEna readback chain ends with cvalue==1.
        assert model.WMaxLimPctEna.cvalue == 1

    @pytest.mark.asyncio
    async def test_clearing_all_inject_slots_disables_register(self, sunspec_connector):
        """When every inject-direction slot is cleared, the enable bit
        gets driven low instead of leaving a stale cap in place."""
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 60})
        await sunspec_connector.update_p_lim({"p_lim_mode_enable": 0})
        model = sunspec_connector._target.models[704][0]
        # The fixture's _update_enable readback sees the value we set.
        assert model.WMaxLimPctEna.cvalue == 0

    @pytest.mark.asyncio
    async def test_absorb_does_not_write_register(self, sunspec_connector, caplog):
        """opModMaxLimWAbsorb must not touch WMaxLimPct -- model 704
        has no absorb-direction limit. The connector logs once per
        device so the misconfiguration is operator-visible."""
        import logging

        model = sunspec_connector._target.models[704][0]
        # Pre-condition: nothing written yet.
        model.WMaxLimPct.cvalue = None  # type: ignore[assignment]
        model.write.reset_mock()
        # The warning is emitted by the sunspec_core._modbus logger; caplog
        # captures WARNING+ via root propagation, so we set the level and assert
        # on message content rather than scoping to a logger name.
        with caplog.at_level(logging.WARNING):
            await sunspec_connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 2500})
        assert model.WMaxLimPct.cvalue is None
        model.write.assert_not_called()
        assert any("opModMaxLimWAbsorb" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_absorb_warning_emitted_once_per_enable_cycle(self, sunspec_connector, caplog):
        """Two enables in a row produce one warning; clearing in
        between resets the suppression so the next enable warns again."""
        import logging

        with caplog.at_level(logging.WARNING):
            await sunspec_connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 2500})
            await sunspec_connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 2000})
        first_run = sum("opModMaxLimWAbsorb" in r.message for r in caplog.records)
        assert first_run == 1
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            # Clear, then re-enable -- the next enable should warn again.
            await sunspec_connector.update_p_lim_abs({"p_lim_mode_enable": 0})
            await sunspec_connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 1500})
        second_run = sum("opModMaxLimWAbsorb" in r.message for r in caplog.records)
        assert second_run == 1

    @pytest.mark.asyncio
    async def test_absorb_does_not_disturb_inject_cap(self, sunspec_connector):
        """A subsequent opModMaxLimWAbsorb update must not clear or
        change the inject-direction cap the operator already set."""
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
        model = sunspec_connector._target.models[704][0]
        model.write.reset_mock()
        await sunspec_connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 1250})
        model.write.assert_not_called()
        # Re-read the recorded value -- still 80% (inj 4000 W / WMax 5000).
        assert model.WMaxLimPct.cvalue == 80

    @pytest.mark.asyncio
    async def test_inject_watts_converted_to_pct_of_wmax(self, sunspec_connector):
        """opModMaxLimWInject (absolute watts) is written as a percent of the
        device's model-702 WMax, not the raw value. WMax=5000, so 2500 W -> 50%."""
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 2500})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 50

    @pytest.mark.asyncio
    async def test_inject_clamped_to_100_when_above_wmax(self, sunspec_connector, caplog):
        """A watt limit at or above WMax cannot exceed 100% -- it is clamped
        and a warning is logged."""
        import logging

        with caplog.at_level(logging.WARNING):
            await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 6000})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 100
        assert any("clamping to 100" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_inject_falls_back_to_wmaxrtg_when_wmax_unavailable(self, sunspec_connector):
        """When the settable WMax is unimplemented (None), the percent base
        falls back to the nameplate rating WMaxRtg. WMaxRtg=8000, so 2000 W
        -> 25% (not 40%, which 5000 would give)."""
        model_702 = sunspec_connector._target.models[702][0]
        model_702.WMax.cvalue = None
        model_702.WMaxRtg.cvalue = 8000
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 2000})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 25

    @pytest.mark.asyncio
    async def test_inject_skipped_when_neither_wmax_available(self, sunspec_connector, caplog):
        """If both WMax and WMaxRtg are unavailable (None), the watts cannot be
        turned into a percent: skip the write with a warning rather than
        fabricate a cap or divide by zero."""
        import logging

        model_702 = sunspec_connector._target.models[702][0]
        model_702.WMax.cvalue = None
        model_702.WMaxRtg.cvalue = None
        model = sunspec_connector._target.models[704][0]
        model.write.reset_mock()
        with caplog.at_level(logging.WARNING):
            await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
        model.write.assert_not_called()
        assert any("WMaxRtg" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_inject_multiplier_applied_upstream_is_watts(self, sunspec_connector):
        """The connector receives absolute watts -- the SunSpec multiplier is
        applied upstream in the translation layer, so the connector does not
        re-scale the value. 1000 W / WMax 5000 -> 20% (not 0.2% or 200%)."""
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 1000})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPct.cvalue == 20

    @pytest.mark.asyncio
    async def test_inject_enabled_without_value_clears_stale_cap(self, sunspec_connector, caplog):
        """An inject update that enables the mode but carries no watts value
        cannot form a cap. It must not leave a previously-applied inject cap
        in place: the slot is re-evaluated like the percent path, dropping the
        stale value and driving the enable bit low when nothing else is active."""
        import logging

        # Apply a real inject cap first (4000 W / WMax 5000 -> 80%).
        await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
        model = sunspec_connector._target.models[704][0]
        assert model.WMaxLimPctEna.cvalue == 1
        assert model.WMaxLimPct.cvalue == 80
        # An enabled-but-valueless inject update arrives; the stale cap must go.
        with caplog.at_level(logging.WARNING):
            await sunspec_connector.update_p_lim_inj({"p_lim_mode_enable": 1})
        assert model.WMaxLimPctEna.cvalue == 0
        assert any("carried no value" in r.message for r in caplog.records)


class TestSunSpecScanReadiness:
    """Verify the post-scan readiness check retries when WMaxRtg is None."""

    def test_scan_retries_when_nameplate_not_ready(self):
        """Scan succeeds but WMaxRtg is None on first read; retry succeeds."""
        mock_target = _make_mock_target()
        model702 = mock_target.models[702][0]

        # First read returns None, second returns a real value
        read_count = 0
        original_value = model702.WMaxRtg.value

        def side_effect():
            nonlocal read_count
            read_count += 1
            if read_count <= 1:
                model702.WMaxRtg.value = None
            else:
                model702.WMaxRtg.value = original_value

        model702.read.side_effect = side_effect

        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

            from py20305.connectors.sunspec import ConnectorSunSpec

            connector = ConnectorSunSpec(host="127.0.0.1", port=8502, scan_retry_delay=0.0)
            assert connector._target is mock_target

    def test_scan_raises_after_all_retries_fail(self):
        """All scan attempts fail → ConnectorConnectionError raised."""
        from py20305.connectors.base import ConnectorConnectionError

        mock_target = _make_mock_target()
        mock_target.scan.side_effect = Exception("connection refused")

        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

            from py20305.connectors.sunspec import ConnectorSunSpec

            with pytest.raises(ConnectorConnectionError, match="failed after"):
                ConnectorSunSpec(host="127.0.0.1", port=8502, scan_retry_delay=0.0)

    def test_readiness_check_verifies_model_702(self):
        """Scan succeeds but model 702 WMaxRtg stays None → retries and fails."""
        from py20305.connectors.base import ConnectorConnectionError

        mock_target = _make_mock_target()
        model702 = mock_target.models[702][0]
        model702.WMaxRtg.value = None

        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

            from py20305.connectors.sunspec import ConnectorSunSpec

            with pytest.raises(ConnectorConnectionError, match="WMaxRtg"):
                ConnectorSunSpec(host="127.0.0.1", port=8502, scan_retry_delay=0.0)

    def test_readiness_check_verifies_w_sf(self):
        """Scan succeeds but W_SF stays None → retries and fails."""
        from py20305.connectors.base import ConnectorConnectionError

        mock_target = _make_mock_target()
        model702 = mock_target.models[702][0]
        model702.W_SF.value = None

        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

            from py20305.connectors.sunspec import ConnectorSunSpec

            with pytest.raises(ConnectorConnectionError, match="W_SF"):
                ConnectorSunSpec(host="127.0.0.1", port=8502, scan_retry_delay=0.0)


class TestSunSpecScanFailFast:
    """Permanent Modbus exceptions (codes 1-4) bail out of the scan retry
    loop after one attempt -- no back-off sleep, no second/third try.

    Root cause: with multiple devices returning Modbus
    exception 4 (server failure / model not present), the per-cycle
    3 x retry_delay back-off was saturating the asyncio event loop
    the API server runs on, queuing GET / behind 22 s of dead time.
    """

    @pytest.mark.parametrize(
        "msg",
        [
            # TCP read format (pysunspec2 modbus.py:682) -- the format
            # produced by reads against TCP Modbus servers.
            "Modbus exception 1: addr: 40000 count: 70",  # ILLEGAL FUNCTION
            "Modbus exception 2: addr: 40070 count: 123",  # ILLEGAL DATA ADDRESS
            "Modbus exception 3: addr: 40225 count: 52",  # ILLEGAL DATA VALUE
            "Modbus exception 4: addr: 41068 count: 9",  # SLAVE DEVICE FAILURE
            # RTU read format (modbus.py:313) -- no addr/count suffix.
            # Real-world surface for serial deployments (e.g. Wago PFC200).
            "Modbus exception 1",
            "Modbus exception 4",
            # Write format (modbus.py:412/472/784/837) -- colon BEFORE the
            # digit. Reads don't emit this today, but the classifier is
            # also reused by future write-fail-fast and the format is on
            # the same exception class.
            "Modbus exception: 2",
            "Modbus exception: 4",
        ],
    )
    def test_permanent_modbus_error_does_not_retry(self, msg: str):
        """Codes 1-4 raise after the first attempt -- no retry, no sleep."""
        from py20305.connectors.base import ConnectorConnectionError

        mock_target = _make_mock_target()
        mock_target.scan.side_effect = Exception(msg)

        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

            from py20305.connectors.sunspec import ConnectorSunSpec

            with pytest.raises(ConnectorConnectionError, match="failed after"):
                # retry_delay=10s would make a 3-attempt run take 20s
                # if we DID retry. Test will hang/time-out under regression.
                ConnectorSunSpec(host="127.0.0.1", port=8502, scan_retries=3, scan_retry_delay=10.0)

        # Permanent error path: exactly ONE scan attempt, no retries.
        assert mock_target.scan.call_count == 1, (
            f"Permanent Modbus error must fail-fast on first attempt, but scan() "
            f"was called {mock_target.scan.call_count} times"
        )

    def test_transient_error_still_retries(self):
        """Non-Modbus-protocol errors (e.g. connection refused) keep the
        existing 3-attempt back-off behavior."""
        from py20305.connectors.base import ConnectorConnectionError

        mock_target = _make_mock_target()
        mock_target.scan.side_effect = Exception("connection refused")

        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

            from py20305.connectors.sunspec import ConnectorSunSpec

            with pytest.raises(ConnectorConnectionError, match="failed after"):
                ConnectorSunSpec(host="127.0.0.1", port=8502, scan_retries=3, scan_retry_delay=0.0)

        # Transient error path: all 3 attempts consumed.
        assert mock_target.scan.call_count == 3, (
            f"Transient errors must retry up to scan_retries; got "
            f"{mock_target.scan.call_count} attempts"
        )

    @pytest.mark.parametrize(
        "msg",
        [
            "Modbus exception 5: ack",  # ACKNOWLEDGE
            "Modbus exception 6: slave busy",  # SLAVE DEVICE BUSY
            "Modbus exception 8: memory parity",  # MEMORY PARITY ERROR
            "Modbus exception 11: gateway target failed",  # GATEWAY TARGET FAILED
        ],
    )
    def test_modbus_codes_5_through_11_are_transient(self, msg: str):
        """Codes 5/6/8/11 (server busy, gateway issues, parity) are transient
        -- the existing 3-attempt back-off still applies."""
        from py20305.connectors.base import ConnectorConnectionError

        mock_target = _make_mock_target()
        mock_target.scan.side_effect = Exception(msg)

        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(return_value=mock_target)

            from py20305.connectors.sunspec import ConnectorSunSpec

            with pytest.raises(ConnectorConnectionError):
                ConnectorSunSpec(host="127.0.0.1", port=8502, scan_retries=3, scan_retry_delay=0.0)

        assert mock_target.scan.call_count == 3, (
            f"Transient Modbus exception {msg!r} must retry up to scan_retries; "
            f"got {mock_target.scan.call_count} attempts"
        )

    def test_helper_classifies_permanent_vs_transient(self):
        """``_is_permanent_modbus_error`` must classify exception codes
        per the MODBUS Application Protocol spec across every format
        pysunspec2 emits."""
        from py20305.connectors.sunspec import _is_permanent_modbus_error

        # Permanent (1-4) across all three pysunspec2 emit formats:
        # TCP read (with addr/count), RTU read (bare), write (colon-digit).
        for code in (1, 2, 3, 4):
            assert _is_permanent_modbus_error(
                Exception(f"Modbus exception {code}: addr: 40000 count: 1")
            ), f"code {code} must be classified permanent (TCP read format)"
            assert _is_permanent_modbus_error(Exception(f"Modbus exception {code}")), (
                f"code {code} must be classified permanent (RTU read format)"
            )
            assert _is_permanent_modbus_error(Exception(f"Modbus exception: {code}")), (
                f"code {code} must be classified permanent (write format)"
            )

        # Transient (5/6/8/10/11) across the same three formats.
        for code in (5, 6, 8, 10, 11):
            assert not _is_permanent_modbus_error(Exception(f"Modbus exception {code}: ...")), (
                f"code {code} must be classified transient (TCP read format)"
            )
            assert not _is_permanent_modbus_error(Exception(f"Modbus exception {code}")), (
                f"code {code} must be classified transient (RTU read format)"
            )
            assert not _is_permanent_modbus_error(Exception(f"Modbus exception: {code}")), (
                f"code {code} must be classified transient (write format)"
            )

        # Non-Modbus errors are NOT permanent (must retry like network issues)
        assert not _is_permanent_modbus_error(Exception("connection refused"))
        assert not _is_permanent_modbus_error(TimeoutError("read timed out"))
        assert not _is_permanent_modbus_error(OSError("broken pipe"))

    def test_pysunspec2_exception_formats_match_classifier(self):
        """Lock the contract against pysunspec2's actual emit strings.

        ``sunspec2.modbus.modbus.ModbusClientException`` is what every
        transport raises on protocol exceptions. As of pysunspec2 1.3.5
        it uses three distinct format strings depending on call site:

          * TCP read  (modbus.py:682):  "Modbus exception N: addr: A count: C"
          * RTU read  (modbus.py:313):  "Modbus exception N"
          * TCP/RTU write
            (modbus.py:412/472/784/837): "Modbus exception: N"

        If a future pysunspec2 release reformats any of these (or adds a
        fourth call site that takes a different shape), the classifier
        silently stops mitigating the stall on that path. Constructing
        the exception with each format directly verifies the regex
        survives the round-trip through the real exception class.
        """
        from sunspec2.modbus.modbus import ModbusClientException  # type: ignore[import-untyped]

        from py20305.connectors.sunspec import _is_permanent_modbus_error

        for code in (1, 2, 3, 4):
            tcp_read = ModbusClientException(f"Modbus exception {code}: addr: 40070 count: 123")
            rtu_read = ModbusClientException(f"Modbus exception {code}")
            write_path = ModbusClientException(f"Modbus exception: {code}")

            for fmt_name, exc in [
                ("TCP read", tcp_read),
                ("RTU read", rtu_read),
                ("write", write_path),
            ]:
                assert _is_permanent_modbus_error(exc), (
                    f"pysunspec2 {fmt_name} format for code {code} no longer "
                    f"matches _MODBUS_EXCEPTION_RE: {str(exc)!r}"
                )


class TestSunSpecGetModelFailFast:
    """``_get_model`` must skip the reconnect-and-retry on permanent Modbus
    errors -- same fail-fast contract as scan-time, applied to the per-cycle
    read path. Without this, a server that loses a model block at runtime
    (firmware update, register-map change) would burn a TCP reconnect on
    every fetch (narrow-scoped to avoid the stall)."""

    @pytest.mark.parametrize(
        "msg",
        [
            "Modbus exception 4: addr: 40000 count: 1",  # TCP read
            "Modbus exception 4",  # RTU read
            "Modbus exception: 4",  # write path
        ],
    )
    def test_get_model_skips_reconnect_on_permanent_error(self, sunspec_connector, msg: str):
        """Permanent Modbus error → no reconnect, no retry, raise immediately.

        Parametrized across the three pysunspec2 emit formats so an RTU
        deployment surfacing the bare ``"Modbus exception 4"`` form
        also fails fast.
        """
        from py20305.connectors.base import ConnectorConnectionError

        model = sunspec_connector._target.models[701][0]
        model.read = MagicMock(side_effect=Exception(msg))

        with pytest.raises(ConnectorConnectionError, match="Permanent Modbus error"):
            sunspec_connector._get_model(701)

        # Critical: no reconnect attempted on permanent errors. A reconnect
        # per cycle per misconfigured device is exactly the loop-saturating
        # behavior the mitigation is about.
        sunspec_connector._target.disconnect.assert_not_called()
        sunspec_connector._target.connect.assert_not_called()
        # Read called exactly once -- no second-attempt after reconnect.
        assert model.read.call_count == 1

    def test_get_model_still_reconnects_on_transient_error(self, sunspec_connector):
        """Non-permanent errors keep the existing reconnect+retry behavior
        (regression guard for ``test_get_model_reconnects_on_read_failure``)."""
        model = sunspec_connector._target.models[701][0]
        call_count = 0

        def read_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BrokenPipeError("Broken pipe")

        model.read = MagicMock(side_effect=read_side_effect)

        result = sunspec_connector._get_model(701)
        assert result is model
        assert call_count == 2
        sunspec_connector._target.disconnect.assert_called_once()
        sunspec_connector._target.connect.assert_called_once()


class TestSunSpecReconnect:
    """Verify automatic reconnection on broken pipe / connection errors."""

    def test_get_model_reconnects_on_read_failure(self, sunspec_connector):
        """First read raises (broken pipe), reconnect succeeds, retry read works."""
        model = sunspec_connector._target.models[701][0]
        call_count = 0

        def read_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BrokenPipeError("Broken pipe")

        model.read = MagicMock(side_effect=read_side_effect)

        result = sunspec_connector._get_model(701)
        assert result is model
        assert call_count == 2
        sunspec_connector._target.disconnect.assert_called_once()
        sunspec_connector._target.connect.assert_called_once()

    def test_get_model_raises_after_reconnect_failure(self, sunspec_connector):
        """Read fails, reconnect fails → ConnectorConnectionError raised."""
        from py20305.connectors.base import ConnectorConnectionError

        model = sunspec_connector._target.models[701][0]
        model.read = MagicMock(side_effect=BrokenPipeError("Broken pipe"))
        sunspec_connector._target.connect.side_effect = Exception("Connection refused")

        with pytest.raises(ConnectorConnectionError, match="reconnection failed"):
            sunspec_connector._get_model(701)

    def test_get_model_raises_after_retry_read_failure(self, sunspec_connector):
        """Read fails, reconnect succeeds, retry read also fails → raises."""
        from py20305.connectors.base import ConnectorConnectionError

        model = sunspec_connector._target.models[701][0]
        model.read = MagicMock(side_effect=BrokenPipeError("Broken pipe"))

        with pytest.raises(ConnectorConnectionError, match="after reconnect"):
            sunspec_connector._get_model(701)


class TestSunSpecAdoptTimeout:
    def test_adopt_curve_timeout(self, sunspec_connector):
        model = sunspec_connector._target.models[705][0]
        model.AdptCrvRslt.value = 0  # stays in-progress

        with pytest.raises(ConnectorTimeoutError, match="timed out"):
            sunspec_connector._adopt_curve(model, timeout=1)

    def test_adopt_curve_failure(self, sunspec_connector):
        model = sunspec_connector._target.models[705][0]
        model.AdptCrvRslt.value = 2  # failure

        with pytest.raises(ConnectorWriteError, match="failed"):
            sunspec_connector._adopt_curve(model, timeout=1)

    def test_adopt_curve_success(self, sunspec_connector):
        model = sunspec_connector._target.models[705][0]
        model.AdptCrvRslt.value = 1
        assert sunspec_connector._adopt_curve(model) is True


class TestSunSpecTransport:
    """Verify transport selection dispatches to the correct pysunspec2 client."""

    def _make_connector(self, mock_client: MagicMock, **kwargs: object):
        """Import and instantiate ConnectorSunSpec inside the mock context."""
        from py20305.connectors.sunspec import ConnectorSunSpec

        return ConnectorSunSpec(**kwargs, scan_retry_delay=0.0)  # type: ignore[arg-type]

    @pytest.fixture(autouse=True)
    def _sunspec_modules(self):
        """Patch sunspec2 modules and expose mock client classes."""
        self.mock_target = _make_mock_target()
        with patch.dict(
            "sys.modules",
            {
                "sunspec2": MagicMock(),
                "sunspec2.modbus": MagicMock(),
                "sunspec2.modbus.client": MagicMock(),
            },
        ):
            import sunspec2.modbus.client as mock_client

            mock_client.SunSpecModbusClientDeviceTCP = MagicMock(
                return_value=self.mock_target,
            )
            mock_client.SunSpecModbusClientDeviceRTU = MagicMock(
                return_value=self.mock_target,
            )
            self.mock_client = mock_client
            yield

    def test_tcp_is_default(self):
        """No transport arg defaults to TCP client (backward compat)."""
        self._make_connector(self.mock_client, host="10.0.0.1", port=502)
        self.mock_client.SunSpecModbusClientDeviceTCP.assert_called_once_with(
            slave_id=1,
            ipaddr="10.0.0.1",
            ipport=502,
            timeout=5,
        )
        self.mock_client.SunSpecModbusClientDeviceRTU.assert_not_called()

    def test_tcp_explicit(self):
        """Explicit transport='tcp' uses TCP client."""
        self._make_connector(
            self.mock_client,
            transport="tcp",
            host="10.0.0.1",
            port=502,
        )
        self.mock_client.SunSpecModbusClientDeviceTCP.assert_called_once_with(
            slave_id=1,
            ipaddr="10.0.0.1",
            ipport=502,
            timeout=5,
        )

    def test_rtu_transport(self):
        """RTU transport uses SunSpecModbusClientDeviceRTU with mapped params."""
        self._make_connector(
            self.mock_client,
            transport="rtu",
            serial_port="/dev/ttyUSB0",
            baudrate=19200,
            parity="E",
            unit_id=3,
        )
        self.mock_client.SunSpecModbusClientDeviceRTU.assert_called_once_with(
            slave_id=3,
            name="/dev/ttyUSB0",
            baudrate=19200,
            parity="E",
            timeout=5,
        )
        self.mock_client.SunSpecModbusClientDeviceTCP.assert_not_called()

    def test_tcp_tls_transport(self):
        """TCP/TLS transport uses TCP client with tls=True and cert paths."""
        self._make_connector(
            self.mock_client,
            transport="tcp+tls",
            host="inverter.local",
            port=8502,
            ca_path="/certs/ca.pem",
            cert_path="/certs/cert.pem",
            key_path="/certs/key.pem",
        )
        self.mock_client.SunSpecModbusClientDeviceTCP.assert_called_once_with(
            slave_id=1,
            ipaddr="inverter.local",
            ipport=8502,
            timeout=5,
            tls=True,
            cafile="/certs/ca.pem",
            certfile="/certs/cert.pem",
            keyfile="/certs/key.pem",
            insecure_skip_tls_verify=False,
        )

    def test_tcp_tls_insecure(self):
        """insecure=True maps to insecure_skip_tls_verify=True."""
        self._make_connector(
            self.mock_client,
            transport="tcp+tls",
            host="inverter.local",
            port=8502,
            insecure=True,
        )
        call_kwargs = self.mock_client.SunSpecModbusClientDeviceTCP.call_args[1]
        assert call_kwargs["tls"] is True
        assert call_kwargs["insecure_skip_tls_verify"] is True

    def test_invalid_transport_raises(self):
        """Unknown transport value raises ValueError."""
        with pytest.raises(ValueError, match="Unknown transport 'udp'"):
            self._make_connector(self.mock_client, transport="udp")

    def test_rtu_ignores_host_port(self):
        """RTU transport with host/port still uses RTU client, not TCP."""
        self._make_connector(
            self.mock_client,
            transport="rtu",
            host="10.0.0.1",
            port=502,
            serial_port="/dev/ttyS0",
        )
        self.mock_client.SunSpecModbusClientDeviceRTU.assert_called_once()
        self.mock_client.SunSpecModbusClientDeviceTCP.assert_not_called()


class TestConstQEndToEnd:
    """End-to-end (translator -> connector) coverage for opModFixedVar.

    The watts/percent and percent-scaling bugs shipped because the translator
    and connector were only tested in isolation, with mismatched unit
    conventions that cancelled out per-layer. This drives the real chain:
    a 2030.5 opModFixedVar -> translate_const_q -> update_const_q -> register.
    """

    @pytest.mark.asyncio
    async def test_opmodfixedvar_5300_writes_53_percent(self, sunspec_connector):
        # opModFixedVar.value is IEEE PerCent (hundredths): 5300 = 53%.
        base = DercontrolBase(
            op_mod_fixed_var=FixedVarControlType(
                ref_type=DerunitRefType(value=2),
                value=SignedPerCent(value=5300),
            )
        )
        _method, params = translate_const_q(base, [])
        await sunspec_connector.update_const_q(params)
        model = sunspec_connector._target.models[704][0]
        # 53%, not 5300% (the latter overflowed the int16 VarSetPct register).
        assert model.VarSetPct.cvalue == 53.0


class TestFixedWEndToEnd:
    """End-to-end (translator -> connector) coverage for opModFixedW.

    Same percent-scale class as const_q: the translator emits a percent and the
    connector must write it straight to WSetPct.cvalue. The previous *100 drove
    a 50% setpoint to 5000% on the wire."""

    @pytest.mark.asyncio
    async def test_opmodfixedw_5000_writes_50_percent(self, sunspec_connector):
        # opModFixedW.value is IEEE SignedPerCent (hundredths): 5000 = 50%.
        base = DercontrolBase(op_mod_fixed_w=SignedPerCentControlType(value=5000))
        _method, params = translate_fixed_w(base, [])
        await sunspec_connector.update_fixed_w(params)
        model = sunspec_connector._target.models[704][0]
        assert model.WSetPct.cvalue == 50.0  # 50%, not 5000%
        assert model.WSetMod.value == 0  # W_MAX_PCT (percent of WMax)
