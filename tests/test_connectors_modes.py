"""Tests for the control dispatch table (modes.py)."""

from __future__ import annotations

import pytest
from xsdata.formats.dataclass.models.generics import AnyElement

from py20305.connectors.base import BaseConnector
from py20305.connectors.modes import (
    ALL_ELEMENTS,
    CONTROL_ELEMENTS,
    DOE_ELEMENTS,
    METHOD_BY_ELEMENT,
    element_value,
    snake_case,
    translate_controls,
    translate_default_controls,
)
from py20305.models.sep.sep import (
    ActivePowerControlType,
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
    SignedPerCent,
    SignedPerCentControlType,
    TimeType,
    UnsignedActivePowerControlType,
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


def _link(href: str = "/curve/1", *, disabled: bool | None = None) -> DercurveLink:
    if disabled is None:
        return DercurveLink(href=href)
    return DercurveLink(href=href, disabled=disabled)


def _doe_limit(name: str, value: int, multiplier: int) -> object:
    """Return a stand-in for a typed CSIP-AUS limit element."""

    class Meta:
        pass

    Meta.name = name  # type: ignore[attr-defined]
    return type(
        "Limit",
        (),
        {"Meta": Meta, "value": value, "multiplier": PowerOfTenMultiplierType(value=multiplier)},
    )()


def _single(base: DercontrolBase, curves: list[Dercurve1] | None = None):
    (result,) = translate_controls(base, curves or [])
    return result


class TestMethodNames:
    @pytest.mark.parametrize(
        ("element", "method"),
        [
            ("opModVoltVar", "update_op_mod_volt_var"),
            ("opModHVRTMustTrip", "update_op_mod_hvrt_must_trip"),
            ("opModFixedPFInjectW", "update_op_mod_fixed_pf_inject_w"),
            ("opModMaxLimPctVAAbsorb", "update_op_mod_max_lim_pct_va_absorb"),
            ("opModMaxLimWInject", "update_op_mod_max_lim_w_inject"),
            ("opModWattPF", "update_op_mod_watt_pf"),
            ("opModExpLimW", "update_op_mod_exp_lim_w"),
            ("setESDelay", "update_set_es_delay"),
            ("setGradW", "update_set_grad_w"),
        ],
    )
    def test_method_is_the_element_name_in_snake_case(self, element: str, method: str) -> None:
        assert METHOD_BY_ELEMENT[element] == method

    def test_every_der_control_base_element_has_a_method(self) -> None:
        fields = {name for name in DercontrolBase.model_fields if name.startswith("op_mod_")}
        assert {e.field for e in CONTROL_ELEMENTS} == fields

    def test_every_method_is_declared_on_the_base_connector(self) -> None:
        for element in ALL_ELEMENTS:
            assert callable(getattr(BaseConnector, element.method))

    def test_method_names_are_unique(self) -> None:
        methods = [e.method for e in ALL_ELEMENTS]
        assert len(methods) == len(set(methods))


class TestValues:
    def test_a_curve_is_resolved_and_passed_in_its_own_terms(self) -> None:
        curve = _make_curve(
            x_mult=-2,
            y_mult=-1,
            y_ref_type=2,
            points=[(9200, 440), (10800, -440)],
            v_ref=10000,
            open_loop_tms=500,
            ramp_dec_tms=100,
        )
        method, params = _single(DercontrolBase(op_mod_volt_var=_link()), [curve])

        assert method == "update_op_mod_volt_var"
        assert params["enabled"] is True
        value = params["value"]
        assert value["xMultiplier"] == -2
        assert value["yMultiplier"] == -1
        assert value["yRefType"] == 2
        assert value["curveType"] == 11
        assert value["vRef"] == 10000
        assert value["openLoopTms"] == 500
        assert value["rampDecTms"] == 100
        assert value["CurveData"] == [
            {"xvalue": 9200, "yvalue": 440},
            {"xvalue": 10800, "yvalue": -440},
        ]

    def test_a_curve_the_client_does_not_hold_withdraws_the_element(self) -> None:
        method, params = _single(DercontrolBase(op_mod_volt_watt=_link("/missing")))
        assert (method, params) == ("update_op_mod_volt_watt", {"enabled": False})

    def test_a_percent_is_the_integer_in_hundredths(self) -> None:
        _, params = _single(DercontrolBase(op_mod_max_lim_w=PerCentControlType(value=8000)))
        assert params == {"enabled": True, "value": 8000}

    def test_a_signed_percent_keeps_its_sign(self) -> None:
        _, params = _single(DercontrolBase(op_mod_fixed_w=SignedPerCentControlType(value=-2500)))
        assert params["value"] == -2500

    def test_an_active_power_is_value_and_multiplier(self) -> None:
        field = UnsignedActivePowerControlType(
            value=5, multiplier=PowerOfTenMultiplierType(value=3)
        )
        method, params = _single(DercontrolBase(op_mod_max_lim_winject=field))
        assert method == "update_op_mod_max_lim_w_inject"
        assert params["value"] == {"multiplier": 3, "value": 5}

    def test_a_power_factor_carries_its_excitation(self) -> None:
        field = PowerFactorWithExcitationControlType(
            displacement=95, excitation=True, multiplier=PowerOfTenMultiplierType(value=-2)
        )
        _, params = _single(DercontrolBase(op_mod_fixed_pfinject_w=field))
        assert params["value"] == {"displacement": 95, "excitation": True, "multiplier": -2}

    def test_fixed_var_carries_its_reference(self) -> None:
        field = FixedVarControlType(
            ref_type=DerunitRefType(value=2), value=SignedPerCent(value=-300)
        )
        _, params = _single(DercontrolBase(op_mod_fixed_var=field))
        assert params["value"] == {"refType": 2, "value": -300}

    def test_freq_droop_uses_element_names(self) -> None:
        droop = FreqDroopType(d_bof=36, d_buf=36, k_of=50, k_uf=50, open_loop_tms=500)
        _, params = _single(DercontrolBase(op_mod_freq_droop=droop))
        assert params["value"] == {
            "dBOF": 36,
            "dBUF": 36,
            "kOF": 50,
            "kUF": 50,
            "openLoopTms": 500,
        }

    def test_connect_and_energize_arrive_separately(self) -> None:
        results = translate_controls(DercontrolBase(op_mod_connect=False, op_mod_energize=True), [])
        assert results == [
            ("update_op_mod_connect", {"enabled": True, "value": False}),
            ("update_op_mod_energize", {"enabled": True, "value": True}),
        ]

    def test_a_doe_limit_is_value_and_multiplier(self) -> None:
        base = DercontrolBase(other_element=[_doe_limit("opModExpLimW", 5, 3)])
        assert _single(base) == (
            "update_op_mod_exp_lim_w",
            {"enabled": True, "value": {"value": 5, "multiplier": 3}},
        )

    def test_a_doe_limit_the_parser_left_untyped_still_arrives(self) -> None:
        ns = "{https://csipaus.org/ns}"
        elem = AnyElement(
            qname=f"{ns}opModImpLimW",
            children=[
                AnyElement(qname=f"{ns}multiplier", text="3"),
                AnyElement(qname=f"{ns}value", text="2"),
            ],
        )
        assert _single(DercontrolBase(other_element=[elem]))[1]["value"] == {
            "value": 2,
            "multiplier": 3,
        }


class TestTranslateControls:
    def test_an_empty_control_sets_nothing(self) -> None:
        assert translate_controls(DercontrolBase(), []) == []

    def test_a_disabled_element_is_skipped(self) -> None:
        base = DercontrolBase(op_mod_volt_var=_link(disabled=True))
        assert translate_controls(base, [_make_curve()]) == []

    def test_ramp_tms_is_added_to_every_applied_element(self) -> None:
        base = DercontrolBase(
            op_mod_max_lim_w=PerCentControlType(value=5000),
            op_mod_target_w=ActivePowerControlType(
                value=1, multiplier=PowerOfTenMultiplierType(value=3)
            ),
            ramp_tms=1000,
        )
        results = translate_controls(base, [])
        assert [params["rampTms"] for _, params in results] == [1000, 1000]

    def test_ramp_tms_is_not_added_to_a_withdrawal(self) -> None:
        base = DercontrolBase(op_mod_volt_var=_link("/missing"), ramp_tms=1000)
        assert _single(base)[1] == {"enabled": False}


class TestTranslateDefaultControls:
    def test_includes_the_base_elements_and_each_setting(self) -> None:
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x02" * 16),
            dercontrol_base=DercontrolBase(op_mod_max_lim_w=PerCentControlType(value=5000)),
            set_grad_w=200,
            set_soft_grad_w=100,
            set_esdelay=300,
            set_eshigh_volt=10500,
        )
        results = dict(translate_default_controls(dderc, []))
        assert results["update_op_mod_max_lim_w"] == {"enabled": True, "value": 5000}
        assert results["update_set_grad_w"] == {"enabled": True, "value": 200}
        assert results["update_set_soft_grad_w"] == {"enabled": True, "value": 100}
        assert results["update_set_es_delay"] == {"enabled": True, "value": 300}
        assert results["update_set_es_high_volt"] == {"enabled": True, "value": 10500}
        assert "update_set_es_low_volt" not in results

    def test_unset_settings_are_left_out(self) -> None:
        dderc = DefaultDercontrol1(
            m_rid=MRidtype(value=b"\x02" * 16), dercontrol_base=DercontrolBase()
        )
        assert translate_default_controls(dderc, []) == []


class TestSnakeCase:
    def test_a_run_of_capitals_is_one_word(self) -> None:
        assert snake_case("opModLVRTMomentaryCessation") == "op_mod_lvrt_momentary_cessation"

    def test_element_value_of_none_is_none(self) -> None:
        assert element_value(None) is None


class TestWithdrawal:
    def test_a_clear_withdraws_every_element(self) -> None:
        from py20305.connectors.dispatcher import DISABLE_CALLS

        assert {method for method, _ in DISABLE_CALLS} == {
            e.method for e in CONTROL_ELEMENTS + DOE_ELEMENTS
        }
        assert all(params == {"enabled": False} for _, params in DISABLE_CALLS)

    def test_an_event_end_withdraws_all_but_connect_energize_and_the_permits(self) -> None:
        from py20305.connectors.dispatcher import FIELD_DISABLE_CALLS

        kept = {
            "op_mod_connect",
            "op_mod_energize",
            "op_mod_grid_connect_permit",
            "op_mod_island_permit",
        }
        assert set(FIELD_DISABLE_CALLS) == (
            {e.field for e in CONTROL_ELEMENTS} - kept | {e.element for e in DOE_ELEMENTS}
        )
