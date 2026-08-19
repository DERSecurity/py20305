"""Tests for the control mode translation layer (modes.py)."""

from __future__ import annotations

from py20305.connectors.modes import (
    translate_connect,
    translate_const_pf,
    translate_const_q,
    translate_controls,
    translate_default_controls,
    translate_delta_var,
    translate_delta_w,
    translate_exp_lim,
    translate_fixed_v,
    translate_fixed_w,
    translate_freq_watt,
    translate_grid_connect_permit,
    translate_imp_lim,
    translate_island_permit,
    translate_max_lim_pct_va_absorb,
    translate_max_lim_pct_va_inject,
    translate_max_lim_pct_var_absorb,
    translate_max_lim_pct_var_inject,
    translate_max_lim_pct_w_absorb,
    translate_max_lim_var_absorb,
    translate_max_lim_var_inject,
    translate_of,
    translate_ov,
    translate_p_lim,
    translate_p_lim_abs,
    translate_p_lim_inj,
    translate_pf,
    translate_pv,
    translate_qp,
    translate_qv,
    translate_target_v,
    translate_target_var,
    translate_target_w,
    translate_uf,
    translate_uv,
)
from py20305.models.sep.sep import (
    ActivePowerControlType,
    ActivePowerDeltaControlType,
    CurveData,
    DefaultDercontrol1,
    DercontrolBase,
    Dercurve1,
    DercurveLink,
    DercurveType,
    DerunitRefType,
    FixedVarControlType,
    FreqDroopType,
    MRidtype,
    PerCent,
    PerCentControlType,
    PowerFactorWithExcitationControlType,
    PowerOfTenMultiplierType,
    ReactivePowerControlType,
    ReactivePowerDeltaControlType,
    SignedPerCent,
    SignedPerCentControlType,
    TimeType,
    UnsignedActivePowerControlType,
    UnsignedFixedVarControlType,
    UnsignedReactivePowerControlType,
    VoltageRmscontrolType,
)


def _make_curve(
    href: str = "/curve/1",
    x_mult: int = 0,
    y_mult: int = 0,
    y_ref_type: int = 1,
    points: list[tuple[int, int]] | None = None,
    v_ref: int | None = None,
    open_loop_tms: int | None = None,
    auto_vref: bool | None = None,
    auto_vref_tms: int | None = None,
    ramp_dec_tms: int | None = None,
    ramp_inc_tms: int | None = None,
    ramp_pt1_tms: int | None = None,
) -> Dercurve1:
    if points is None:
        points = [(100, 50), (200, -50)]

    curve_data = [CurveData(xvalue=x, yvalue=y) for x, y in points]
    kwargs: dict = {
        "href": href,
        "m_rid": MRidtype(value=b"\x01" * 16),
        "description": "test",
        "creation_time": TimeType(value=1000),
        "curve_data": curve_data,
        "curve_type": DercurveType(value=11),  # opModVoltVar
        "x_multiplier": PowerOfTenMultiplierType(value=x_mult),
        "y_multiplier": PowerOfTenMultiplierType(value=y_mult),
        "y_ref_type": DerunitRefType(value=y_ref_type),
    }
    if open_loop_tms is not None:
        kwargs["open_loop_tms"] = open_loop_tms
    if v_ref is not None:
        kwargs["v_ref"] = PerCent(value=v_ref)
    if auto_vref is not None:
        kwargs["autonomous_vref_enable"] = auto_vref
    if auto_vref_tms is not None:
        kwargs["autonomous_vref_time_constant"] = auto_vref_tms
    if ramp_dec_tms is not None:
        kwargs["ramp_dec_tms"] = ramp_dec_tms
    if ramp_inc_tms is not None:
        kwargs["ramp_inc_tms"] = ramp_inc_tms
    if ramp_pt1_tms is not None:
        kwargs["ramp_pt1_tms"] = ramp_pt1_tms

    return Dercurve1(**kwargs)


def _link(href: str = "/curve/1") -> DercurveLink:
    return DercurveLink(href=href)


class TestTranslateQV:
    def test_none_field_returns_none(self):
        base = DercontrolBase()
        assert translate_qv(base, []) is None

    def test_missing_curve_returns_disable(self):
        base = DercontrolBase(op_mod_volt_var=_link("/missing"))
        result = translate_qv(base, [])
        assert result is not None
        assert result[0] == "update_qv"
        assert result[1]["qv_mode_enable"] == 0

    def test_valid_curve(self):
        curve = _make_curve(
            v_ref=24000,
            x_mult=-2,
            y_mult=-1,
            points=[(9200, 440), (9800, 0), (10200, 0), (10800, -440)],
            open_loop_tms=500,
            auto_vref=True,
            auto_vref_tms=300,
        )
        base = DercontrolBase(op_mod_volt_var=_link("/curve/1"))
        method, params = translate_qv(base, [curve])

        assert method == "update_qv"
        assert params["qv_mode_enable"] == 1
        assert params["qv_vref"] == 24000 / 100  # PerCent → percent of nominal
        assert params["qv_vref_auto_ena"] == 1
        assert params["qv_vref_olrt"] == 300
        assert params["qv_olrt"] == 5.0  # 500 hundredths-of-seconds → 5.0 s
        assert len(params["qv_curve_v_pts"]) == 4
        assert params["qv_curve_v_pts"][0] == 9200 * (10**-2)
        assert params["qv_curve_q_pts"][0] == 440 * (10**-1)


class TestTranslatePV:
    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_pv(base, []) is None

    def test_valid_curve(self):
        curve = _make_curve(points=[(106, 100), (110, 0)], open_loop_tms=100)
        base = DercontrolBase(op_mod_volt_watt=_link())
        method, params = translate_pv(base, [curve])
        assert method == "update_pv"
        assert params["pv_mode_enable"] == 1
        assert len(params["pv_curve_v_pts"]) == 2


class TestTranslateQP:
    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_qp(base, []) is None

    def test_valid_curve(self):
        curve = _make_curve(points=[(20, 44), (50, 0), (80, 0), (100, -44)])
        base = DercontrolBase(op_mod_watt_var=_link())
        method, params = translate_qp(base, [curve])
        assert method == "update_qp"
        assert params["qp_mode_enable"] == 1
        assert len(params["qp_curve_p_pts"]) == 4


class TestTranslatePLim:
    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_p_lim(base, []) is None

    def test_correct_scaling(self):
        """Verify fix: (value / 100) not (value / 100 / 100)."""
        base = DercontrolBase(op_mod_max_lim_w=PerCentControlType(value=5000))
        method, params = translate_p_lim(base, [])
        assert method == "update_p_lim"
        assert params["p_lim_mode_enable"] == 1
        # 5000 / 100 = 50 (percent)
        assert params["p_lim_w"] == 50.0


class TestTranslatePLimInj:
    def test_emits_absolute_watts_with_multiplier(self):
        # opModMaxLimWInject is UnsignedActivePowerControlType: absolute watts
        # (value * 10^multiplier), NOT a percent. The translator emits the
        # watts unchanged under p_lim_watts; the connector converts to a
        # percent of WMax. See docs/planning/P_LIM_INJECT_SCALING_FIX.md.
        base = DercontrolBase(
            op_mod_max_lim_winject=UnsignedActivePowerControlType(
                value=500, multiplier=PowerOfTenMultiplierType(value=1)
            )
        )
        method, params = translate_p_lim_inj(base, [])
        assert method == "update_p_lim_inj"
        assert params["p_lim_mode_enable"] == 1
        # 500 * 10^1 = 5000 W (no /100 -- that was the watts-as-percent bug)
        assert params["p_lim_watts"] == 5000
        assert "p_lim_w" not in params


class TestTranslatePLimAbs:
    def test_emits_absolute_watts_with_multiplier(self):
        base = DercontrolBase(
            op_mod_max_lim_wabsorb=UnsignedActivePowerControlType(
                value=1000, multiplier=PowerOfTenMultiplierType(value=0)
            )
        )
        method, params = translate_p_lim_abs(base, [])
        assert method == "update_p_lim_abs"
        assert params["p_lim_mode_enable"] == 1
        # 1000 * 10^0 = 1000 W
        assert params["p_lim_watts"] == 1000
        assert "p_lim_w" not in params


class TestTranslatePF:
    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_pf(base, []) is None

    def test_scaling(self):
        base = DercontrolBase(
            op_mod_freq_droop=FreqDroopType(
                d_bof=500,
                d_buf=500,
                k_of=40,
                k_uf=40,
                open_loop_tms=1000,
            )
        )
        method, params = translate_pf(base, [])
        assert method == "update_pf"
        assert params["pf_dbof"] == 0.5
        assert params["pf_kof"] == 0.04
        assert params["pf_olrt"] == 10.0


class TestTranslateConstQ:
    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_const_q(base, []) is None

    def test_with_values(self):
        # opModFixedVar.value is IEEE PerCent (hundredths): 4400 = 44%. The
        # translator emits the percent number the connector writes to
        # VarSetPct.cvalue, NOT the raw hundredths.
        base = DercontrolBase(
            op_mod_fixed_var=FixedVarControlType(
                ref_type=DerunitRefType(value=3),
                value=SignedPerCent(value=4400),
            )
        )
        method, params = translate_const_q(base, [])
        assert method == "update_const_q"
        assert params["const_q_mode_enable"] == 1
        assert params["ref_type"] == 3  # IEEE DERUnitRefType: %statVarAvail
        assert params["const_q_pct"] == 44.0  # 4400 hundredths-of-% -> 44%

    def test_percent_scaling_avoids_register_overflow(self):
        """Regression: opModFixedVar=5300 (53%) must yield const_q_pct=53.0, not
        5300 -- the raw value drove VarSetPct.cvalue to 5300% and overflowed the
        int16 register (saw 'VarSetPct 53000: h format' in the field)."""
        base = DercontrolBase(
            op_mod_fixed_var=FixedVarControlType(
                ref_type=DerunitRefType(value=2),
                value=SignedPerCent(value=5300),
            )
        )
        _method, params = translate_const_q(base, [])
        assert params["const_q_pct"] == 53.0

    def test_all_ieee_ref_types(self):
        """All IEEE DERUnitRefType values (0-8) should be passed through."""
        for ref_val in range(9):
            base = DercontrolBase(
                op_mod_fixed_var=FixedVarControlType(
                    ref_type=DerunitRefType(value=ref_val),
                    value=SignedPerCent(value=1000),
                )
            )
            _method, params = translate_const_q(base, [])
            assert params["ref_type"] == ref_val


class TestTranslateConstPF:
    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_const_pf(base, []) is None

    def test_inject_only(self):
        base = DercontrolBase(
            op_mod_fixed_pfinject_w=PowerFactorWithExcitationControlType(
                displacement=950,
                excitation=False,
                multiplier=PowerOfTenMultiplierType(value=-3),
            )
        )
        method, params = translate_const_pf(base, [])
        assert method == "update_const_pf"
        assert params["inj"]["mode"] == 1
        assert params["inj"]["pf"] == 0.95
        assert params["abs"]["mode"] == 0

    def test_absorb_only(self):
        base = DercontrolBase(
            op_mod_fixed_pfabsorb_w=PowerFactorWithExcitationControlType(
                displacement=900,
                excitation=True,
                multiplier=PowerOfTenMultiplierType(value=-3),
            )
        )
        _method, params = translate_const_pf(base, [])
        assert params["abs"]["mode"] == 1
        assert params["abs"]["pf"] == 0.9
        assert params["inj"]["mode"] == 0


class TestTranslateFixedW:
    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_fixed_w(base, []) is None

    def test_scaling(self):
        """The emitted key is ``WSet`` in both modes, by contract.

        ``WSetPct`` is the register SunSpec 704 reads under W_MAX_PCT, but this
        dict is a transport rather than a register map: ``WSetMod`` travels with
        the value so the consumer routes it. Moving the emitted key to match the
        register was tried and reverted -- it broke the three consumers that
        already read ``WSet`` as the percent carrier. This assertion is the
        contract, not an oversight.
        """
        base = DercontrolBase(op_mod_fixed_w=SignedPerCentControlType(value=5000))
        method, params = translate_fixed_w(base, [])
        assert method == "update_fixed_w"
        assert params["WSetEna"] == 1
        assert params["WSet"] == 50.0  # 5000 hundredths-of-% -> 50%
        assert params["WSetMod"] == 0  # SunSpec 704 W_MAX_PCT (percent mode)
        # No WSetPct: the mode says how to read WSet, so a second key would be
        # a second source of truth.
        assert "WSetPct" not in params


class TestTranslateRideThrough:
    def test_ov_with_curve(self):
        curve = _make_curve(points=[(1, 120), (10, 110)])
        base = DercontrolBase(op_mod_hvrtmust_trip=_link())
        result = translate_ov(base, [curve])
        assert result is not None
        assert result[0] == "update_ov"
        assert result[1]["ov_mode_enable"] == 1

    def test_uv_none(self):
        base = DercontrolBase()
        assert translate_uv(base, []) is None

    def test_of_with_curve(self):
        curve = _make_curve(points=[(1, 61000), (10, 60500)])
        base = DercontrolBase(op_mod_hfrtmust_trip=_link())
        result = translate_of(base, [curve])
        assert result is not None
        assert result[0] == "update_of"
        assert result[1]["of_mode_enable"] == 1

    def test_uf_none(self):
        base = DercontrolBase()
        assert translate_uf(base, []) is None


class TestTranslateCSIPAUS:
    def _make_csipaus_elem(self, meta_name: str, value: int, multiplier: int):
        """Create a fake CSIP-AUS element."""

        class FakeMeta:
            name = meta_name

        elem = type(
            "FakeCSIPAUS",
            (),
            {
                "Meta": FakeMeta,
                "value": value,
                "multiplier": PowerOfTenMultiplierType(value=multiplier),
            },
        )()
        return elem

    def test_exp_lim_none(self):
        base = DercontrolBase()
        assert translate_exp_lim(base, []) is None

    def test_exp_lim_from_other_element(self):
        elem = self._make_csipaus_elem("opModExpLimW", 5000, 0)
        base = DercontrolBase(other_element=[elem])
        result = translate_exp_lim(base, [])
        assert result is not None
        assert result[0] == "update_exp_lim"
        assert result[1]["exp_lim_w"] == 5000

    def test_imp_lim_from_other_element(self):
        elem = self._make_csipaus_elem("opModImpLimW", 3000, 1)
        base = DercontrolBase(other_element=[elem])
        result = translate_imp_lim(base, [])
        assert result is not None
        assert result[1]["imp_lim_w"] == 30000


class TestTranslateControls:
    def test_empty_base_returns_empty(self):
        base = DercontrolBase()
        assert translate_controls(base, []) == []

    def test_multiple_active_modes(self):
        curve = _make_curve()
        base = DercontrolBase(
            op_mod_volt_var=_link(),
            op_mod_max_lim_w=PerCentControlType(value=5000),
        )
        results = translate_controls(base, [curve])
        method_names = [r[0] for r in results]
        assert "update_qv" in method_names
        assert "update_p_lim" in method_names

    def test_disabled_mode_skipped(self):
        """WP3a: Modes with disabled=True should be skipped."""
        base = DercontrolBase(
            op_mod_max_lim_w=PerCentControlType(value=5000),
        )
        # Simulate disabled by setting the attribute on the field object
        base.op_mod_max_lim_w.disabled = True  # type: ignore[union-attr]
        results = translate_controls(base, [])
        method_names = [r[0] for r in results]
        assert "update_p_lim" not in method_names

    def test_mode_filter_restricts_translation(self):
        """WP3c: mode_filter should restrict which modes are translated."""
        curve = _make_curve()
        base = DercontrolBase(
            op_mod_volt_var=_link(),
            op_mod_max_lim_w=PerCentControlType(value=5000),
        )
        results = translate_controls(base, [curve], mode_filter=frozenset({"op_mod_volt_var"}))
        method_names = [r[0] for r in results]
        assert "update_qv" in method_names
        assert "update_p_lim" not in method_names


class TestTranslateDefaultControls:
    def test_includes_set_grad_w(self):
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x01" * 16),
            dercontrol_base=DercontrolBase(),
            set_grad_w=500,
        )
        results = translate_default_controls(dderc, [])
        method_names = [r[0] for r in results]
        assert "update_p_ramp" in method_names
        ramp_params = next(p for m, p in results if m == "update_p_ramp")
        assert ramp_params["w_ramp"] == 500

    def test_no_set_grad_w(self):
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x01" * 16),
            dercontrol_base=DercontrolBase(),
        )
        results = translate_default_controls(dderc, [])
        method_names = [r[0] for r in results]
        assert "update_p_ramp" not in method_names

    def test_includes_set_soft_grad_w(self):
        """IEEE Gap 15: setSoftGradW should produce update_p_ramp with soft_grad_w."""
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x01" * 16),
            dercontrol_base=DercontrolBase(),
            set_soft_grad_w=300,
        )
        results = translate_default_controls(dderc, [])
        ramp_results = [(m, p) for m, p in results if m == "update_p_ramp"]
        assert len(ramp_results) == 1
        assert ramp_results[0][1]["soft_grad_w"] == 300

    def test_both_grad_w_and_soft_grad_w(self):
        """Both setGradW and setSoftGradW should produce separate ramp entries."""
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x01" * 16),
            dercontrol_base=DercontrolBase(),
            set_grad_w=500,
            set_soft_grad_w=300,
        )
        results = translate_default_controls(dderc, [])
        ramp_results = [(m, p) for m, p in results if m == "update_p_ramp"]
        assert len(ramp_results) == 2
        assert any(p.get("w_ramp") == 500 for _, p in ramp_results)
        assert any(p.get("soft_grad_w") == 300 for _, p in ramp_results)

    def test_includes_enter_service_params(self):
        """IEEE Gap 22: ES parameters from DefaultDercontrol passed through."""
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x01" * 16),
            dercontrol_base=DercontrolBase(),
            set_esdelay=500,
            set_eshigh_freq=6100,
            set_eslow_freq=5900,
            set_eshigh_volt=11000,
            set_eslow_volt=9000,
            set_esramp_tms=300,
            set_esrandom_delay=100,
        )
        results = translate_default_controls(dderc, [])
        es_results = [(m, p) for m, p in results if m == "update_es_permit_service"]
        assert len(es_results) == 1
        params = es_results[0][1]
        assert params["es_delay"] == 500
        assert params["es_high_freq"] == 6100
        assert params["es_low_freq"] == 5900
        assert params["es_high_volt"] == 11000
        assert params["es_low_volt"] == 9000
        assert params["es_ramp_tms"] == 300
        assert params["es_random_delay"] == 100

    def test_no_es_params_when_all_none(self):
        """No update_es_permit_service when no ES fields are set."""
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x01" * 16),
            dercontrol_base=DercontrolBase(),
        )
        results = translate_default_controls(dderc, [])
        assert not any(m == "update_es_permit_service" for m, _ in results)

    def test_partial_es_params(self):
        """Only set ES fields are included in params."""
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x01" * 16),
            dercontrol_base=DercontrolBase(),
            set_esdelay=500,
        )
        results = translate_default_controls(dderc, [])
        es_results = [(m, p) for m, p in results if m == "update_es_permit_service"]
        assert len(es_results) == 1
        assert es_results[0][1] == {"es_delay": 500}


class TestCurveRampAttributes:
    """IEEE Gap 12: DERCurve ramp attributes pass-through."""

    def test_qv_includes_ramp_attrs(self):
        curve = _make_curve(ramp_dec_tms=100, ramp_inc_tms=200, ramp_pt1_tms=300)
        base = DercontrolBase(op_mod_volt_var=_link())
        _, params = translate_qv(base, [curve])
        assert params["ramp_dec_tms"] == 100
        assert params["ramp_inc_tms"] == 200
        assert params["ramp_pt1_tms"] == 300

    def test_pv_includes_ramp_attrs(self):
        curve = _make_curve(ramp_dec_tms=50)
        base = DercontrolBase(op_mod_volt_watt=_link())
        _, params = translate_pv(base, [curve])
        assert params["ramp_dec_tms"] == 50
        assert params["ramp_inc_tms"] is None
        assert params["ramp_pt1_tms"] is None

    def test_qp_includes_ramp_attrs(self):
        curve = _make_curve(ramp_pt1_tms=400)
        base = DercontrolBase(op_mod_watt_var=_link())
        _, params = translate_qp(base, [curve])
        assert params["ramp_pt1_tms"] == 400

    def test_ride_through_includes_ramp_attrs(self):
        curve = _make_curve(ramp_dec_tms=10, ramp_inc_tms=20)
        base = DercontrolBase(op_mod_hvrtmust_trip=_link())
        _, params = translate_ov(base, [curve])
        assert params["ramp_dec_tms"] == 10
        assert params["ramp_inc_tms"] == 20

    def test_frequency_ride_through_includes_ramp_attrs(self):
        curve = _make_curve(ramp_dec_tms=15)
        base = DercontrolBase(op_mod_hfrtmust_trip=_link())
        _, params = translate_of(base, [curve])
        assert params["ramp_dec_tms"] == 15


class TestTranslateFreqWatt:
    """IEEE Gap 13: opModFreqWatt (curveType 0) curve-based frequency-watt."""

    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_freq_watt(base, []) is None

    def test_missing_curve_disables(self):
        base = DercontrolBase(op_mod_freq_watt=_link("/curve/missing"))
        method, params = translate_freq_watt(base, [])
        assert method == "update_freq_watt"
        assert params["fw_mode_enable"] == 0

    def test_with_curve(self):
        curve = _make_curve(
            points=[(59000, 100), (60000, 0), (61000, -100)],
            x_mult=-3,
            y_mult=0,
            open_loop_tms=500,
        )
        base = DercontrolBase(op_mod_freq_watt=_link())
        method, params = translate_freq_watt(base, [curve])
        assert method == "update_freq_watt"
        assert params["fw_mode_enable"] == 1
        assert len(params["fw_curve_f_pts"]) == 3
        assert len(params["fw_curve_p_pts"]) == 3
        assert params["fw_olrt"] == 5.0  # 500 hundredths-of-seconds → 5.0 s

    def test_includes_ramp_attrs(self):
        curve = _make_curve(ramp_dec_tms=50, ramp_inc_tms=100)
        base = DercontrolBase(op_mod_freq_watt=_link())
        _, params = translate_freq_watt(base, [curve])
        assert params["ramp_dec_tms"] == 50
        assert params["ramp_inc_tms"] == 100


# ------------------------------------------------------------------
# Gap 16: connect/energize
# ------------------------------------------------------------------


class TestTranslateConnect:
    def test_both_none_returns_none(self):
        base = DercontrolBase()
        assert translate_connect(base, []) is None

    def test_both_true(self):
        base = DercontrolBase(op_mod_connect=True, op_mod_energize=True)
        method, params = translate_connect(base, [])
        assert method == "update_connect"
        assert params["connected"] is True

    def test_connect_false_energize_true(self):
        base = DercontrolBase(op_mod_connect=False, op_mod_energize=True)
        _, params = translate_connect(base, [])
        assert params["connected"] is False

    def test_connect_true_energize_false(self):
        base = DercontrolBase(op_mod_connect=True, op_mod_energize=False)
        _, params = translate_connect(base, [])
        assert params["connected"] is False

    def test_connect_only_defaults_energize_true(self):
        """When only opModConnect is set, opModEnergize defaults to True."""
        base = DercontrolBase(op_mod_connect=True)
        _, params = translate_connect(base, [])
        assert params["connected"] is True

    def test_energize_only_defaults_connect_true(self):
        base = DercontrolBase(op_mod_energize=False)
        _, params = translate_connect(base, [])
        assert params["connected"] is False


# ------------------------------------------------------------------
# Gap 17: DERControlType2 modes
# ------------------------------------------------------------------


class TestTranslateMaxLimPctVA:
    def test_absorb_none(self):
        assert translate_max_lim_pct_va_absorb(DercontrolBase(), []) is None

    def test_absorb_scaling(self):
        base = DercontrolBase(op_mod_max_lim_pct_vaabsorb=PerCentControlType(value=8000))
        method, params = translate_max_lim_pct_va_absorb(base, [])
        assert method == "update_max_lim_pct_va_absorb"
        assert params["mode_enable"] == 1
        assert params["pct"] == 80.0  # 8000 / 100

    def test_inject_scaling(self):
        base = DercontrolBase(op_mod_max_lim_pct_vainject=PerCentControlType(value=5000))
        method, params = translate_max_lim_pct_va_inject(base, [])
        assert method == "update_max_lim_pct_va_inject"
        assert params["pct"] == 50.0


class TestTranslateMaxLimPctVar:
    def test_absorb_with_ref_type(self):
        base = DercontrolBase(
            op_mod_max_lim_pct_var_absorb=UnsignedFixedVarControlType(
                ref_type=DerunitRefType(value=3),
                value=PerCent(value=7500),
            )
        )
        method, params = translate_max_lim_pct_var_absorb(base, [])
        assert method == "update_max_lim_pct_var_absorb"
        assert params["pct"] == 7500
        assert params["ref_type"] == 3

    def test_inject_with_ref_type(self):
        base = DercontrolBase(
            op_mod_max_lim_pct_var_inject=UnsignedFixedVarControlType(
                ref_type=DerunitRefType(value=2),
                value=PerCent(value=6000),
            )
        )
        _, params = translate_max_lim_pct_var_inject(base, [])
        assert params["pct"] == 6000
        assert params["ref_type"] == 2


class TestTranslateMaxLimPctWAbsorb:
    def test_scaling(self):
        base = DercontrolBase(op_mod_max_lim_pct_wabsorb=PerCentControlType(value=9000))
        method, params = translate_max_lim_pct_w_absorb(base, [])
        assert method == "update_max_lim_pct_w_absorb"
        assert params["pct"] == 90.0


class TestTranslateMaxLimVar:
    def test_absorb_with_multiplier(self):
        base = DercontrolBase(
            op_mod_max_lim_var_absorb=UnsignedReactivePowerControlType(
                value=500, multiplier=PowerOfTenMultiplierType(value=1)
            )
        )
        method, params = translate_max_lim_var_absorb(base, [])
        assert method == "update_max_lim_var_absorb"
        assert params["var"] == 5000  # 500 * 10^1

    def test_inject_with_multiplier(self):
        base = DercontrolBase(
            op_mod_max_lim_var_inject=UnsignedReactivePowerControlType(
                value=300, multiplier=PowerOfTenMultiplierType(value=0)
            )
        )
        _, params = translate_max_lim_var_inject(base, [])
        assert params["var"] == 300


class TestTranslateTargets:
    def test_target_v(self):
        base = DercontrolBase(
            op_mod_target_v=VoltageRmscontrolType(
                value=240, multiplier=PowerOfTenMultiplierType(value=0)
            )
        )
        method, params = translate_target_v(base, [])
        assert method == "update_target_v"
        assert params["voltage"] == 240

    def test_target_var(self):
        base = DercontrolBase(
            op_mod_target_var=ReactivePowerControlType(
                value=1000, multiplier=PowerOfTenMultiplierType(value=0)
            )
        )
        method, params = translate_target_var(base, [])
        assert method == "update_target_var"
        assert params["var"] == 1000

    def test_target_w(self):
        base = DercontrolBase(
            op_mod_target_w=ActivePowerControlType(
                value=5000, multiplier=PowerOfTenMultiplierType(value=0)
            )
        )
        method, params = translate_target_w(base, [])
        assert method == "update_target_w"
        assert params["watts"] == 5000

    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_target_v(base, []) is None
        assert translate_target_var(base, []) is None
        assert translate_target_w(base, []) is None


# ------------------------------------------------------------------
# Gap 18: delta modes
# ------------------------------------------------------------------


class TestTranslateDelta:
    def test_delta_w(self):
        base = DercontrolBase(
            op_mod_delta_w=ActivePowerDeltaControlType(
                value=1000, multiplier=PowerOfTenMultiplierType(value=0), bidirectional=True
            )
        )
        method, params = translate_delta_w(base, [])
        assert method == "update_delta_w"
        assert params["delta_w_mode_enable"] == 1
        assert params["delta_w"] == 1000
        assert params["bidirectional"] == 1

    def test_delta_var(self):
        base = DercontrolBase(
            op_mod_delta_var=ReactivePowerDeltaControlType(
                value=500, multiplier=PowerOfTenMultiplierType(value=1), bidirectional=False
            )
        )
        method, params = translate_delta_var(base, [])
        assert method == "update_delta_var"
        assert params["delta_var"] == 5000  # 500 * 10^1
        assert params["bidirectional"] == 0

    def test_none_returns_none(self):
        base = DercontrolBase()
        assert translate_delta_w(base, []) is None
        assert translate_delta_var(base, []) is None


# ------------------------------------------------------------------
# Gap 19: fixed voltage, permits
# ------------------------------------------------------------------


class TestTranslateFixedV:
    def test_scaling(self):
        base = DercontrolBase(op_mod_fixed_v=SignedPerCentControlType(value=10000))
        method, params = translate_fixed_v(base, [])
        assert method == "update_fixed_v"
        assert params["fixed_v_mode_enable"] == 1
        assert params["fixed_v_pct"] == 100.0  # 10000 / 100

    def test_none_returns_none(self):
        assert translate_fixed_v(DercontrolBase(), []) is None


class TestTranslatePermits:
    def test_grid_connect_permit_true(self):
        base = DercontrolBase(op_mod_grid_connect_permit=True)
        method, params = translate_grid_connect_permit(base, [])
        assert method == "update_grid_connect_permit"
        assert params["permit"] is True

    def test_grid_connect_permit_false(self):
        base = DercontrolBase(op_mod_grid_connect_permit=False)
        _, params = translate_grid_connect_permit(base, [])
        assert params["permit"] is False

    def test_grid_connect_none(self):
        assert translate_grid_connect_permit(DercontrolBase(), []) is None

    def test_island_permit_true(self):
        base = DercontrolBase(op_mod_island_permit=True)
        method, params = translate_island_permit(base, [])
        assert method == "update_island_permit"
        assert params["permit"] is True

    def test_island_permit_none(self):
        assert translate_island_permit(DercontrolBase(), []) is None


# -- IEEE 2030.5-2023 disabled attribute --------------------------------------


class TestDisabledAttributeHonored:
    """The 2023 schema added a ``disabled`` boolean attribute on every
    DERControl Mode element. When set, the translator must emit the
    mode-disabled tuple (``<mode>_mode_enable: 0``) regardless of whether
    the curve is resolvable. Curves still need to be present so the
    translator can short-circuit before curve resolution kicks in."""

    def test_qv_disabled_returns_disable_tuple(self):
        link = DercurveLink(href="/curve/1", disabled=True)
        base = DercontrolBase(op_mod_volt_var=link)
        # Even with a valid curve in the list, disabled wins.
        curve = _make_curve(points=[(100, 50), (110, 0)], open_loop_tms=0)
        method, params = translate_qv(base, [curve])
        assert method == "update_qv"
        assert params == {"qv_mode_enable": 0}

    def test_qv_disabled_false_still_translates_normally(self):
        link = DercurveLink(href="/curve/1", disabled=False)
        base = DercontrolBase(op_mod_volt_var=link)
        curve = _make_curve(points=[(100, 50), (110, 0)], open_loop_tms=0)
        _method, params = translate_qv(base, [curve])
        assert params["qv_mode_enable"] == 1

    def test_pv_disabled(self):
        link = DercurveLink(href="/curve/1", disabled=True)
        base = DercontrolBase(op_mod_volt_watt=link)
        curve = _make_curve(points=[(106, 100), (110, 0)], open_loop_tms=0)
        _method, params = translate_pv(base, [curve])
        assert params == {"pv_mode_enable": 0}

    def test_qp_disabled(self):
        link = DercurveLink(href="/curve/1", disabled=True)
        base = DercontrolBase(op_mod_watt_var=link)
        curve = _make_curve(points=[(0, 0), (50, 25)], open_loop_tms=0)
        _method, params = translate_qp(base, [curve])
        assert params == {"qp_mode_enable": 0}

    def test_freq_watt_disabled(self):
        link = DercurveLink(href="/curve/1", disabled=True)
        base = DercontrolBase(op_mod_freq_watt=link)
        curve = _make_curve(points=[(60, 100), (61, 0)], open_loop_tms=0)
        _method, params = translate_freq_watt(base, [curve])
        assert params == {"fw_mode_enable": 0}

    def test_disabled_when_curve_also_missing(self):
        """If both disabled and curve-missing apply, disable still wins (and
        the disable tuple comes from the disabled check, not the
        curve-not-found fallback -- both produce the same shape, though)."""
        link = DercurveLink(href="/missing/curve", disabled=True)
        base = DercontrolBase(op_mod_volt_var=link)
        _method, params = translate_qv(base, [])
        assert params == {"qv_mode_enable": 0}

    def test_const_pf_inj_disabled(self):
        from py20305.models.sep.sep import (
            PowerFactorWithExcitationControlType,
            PowerOfTenMultiplierType,
        )

        inj = PowerFactorWithExcitationControlType(
            displacement=950,
            multiplier=PowerOfTenMultiplierType(value=-3),
            excitation=False,
            disabled=True,
        )
        base = DercontrolBase(op_mod_fixed_pfinject_w=inj)
        method, params = translate_const_pf(base, [])
        assert method == "update_const_pf"
        # disabled inj = mode 0 in the inj sub-dict.
        assert params["inj"] == {"mode": 0}
        # No abs field at all -> abs sub-dict reports mode 0 too.
        assert params["abs"] == {"mode": 0}

    def test_const_pf_inj_enabled_abs_disabled(self):
        from py20305.models.sep.sep import (
            PowerFactorWithExcitationControlType,
            PowerOfTenMultiplierType,
        )

        inj = PowerFactorWithExcitationControlType(
            displacement=950,
            multiplier=PowerOfTenMultiplierType(value=-3),
            excitation=False,
            disabled=False,
        )
        abs_ = PowerFactorWithExcitationControlType(
            displacement=900,
            multiplier=PowerOfTenMultiplierType(value=-3),
            excitation=True,
            disabled=True,
        )
        base = DercontrolBase(op_mod_fixed_pfinject_w=inj, op_mod_fixed_pfabsorb_w=abs_)
        _method, params = translate_const_pf(base, [])
        # Inj passes through normally (disabled=False), abs disabled.
        assert params["inj"]["mode"] == 1
        assert params["inj"]["pf"] == 0.95
        assert params["abs"] == {"mode": 0}
