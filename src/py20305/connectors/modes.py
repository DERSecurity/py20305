"""Control mode translation: DercontrolBase fields -> connector param dicts.

Translates IEEE 2030.5 Pydantic model fields into the intermediate dict
format expected by BaseConnector.update_*() methods. Each translator
function extracts values from the typed model, applies scaling, and
returns a (method_name, params) pair.

Bug fixes from original:
- set_p_lim: uses (value * 10^multiplier) / 100 instead of double /100
- set_exp_lim: checks both namespaced and non-namespaced keys
- Curve-based modes: uses Pydantic model attributes instead of dict .get()
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from py20305.models.sep.sep import (
    DefaultDercontrol1,
    DercontrolBase,
    Dercurve1,
    DercurveLink,
)


def _find_curve(href: str, curves: list[Dercurve1]) -> Dercurve1 | None:
    """Find a DERCurve by its href in the provided curve list."""
    for curve in curves:
        if hasattr(curve, "href") and curve.href == href:
            return curve
    return None


def _scale_curve_points(
    curve: Dercurve1,
) -> tuple[list[float], list[float]]:
    """Extract and scale curve data points using x/y multipliers."""
    x_mult = int(curve.x_multiplier.value)
    y_mult = int(curve.y_multiplier.value)
    x_pts: list[float] = []
    y_pts: list[float] = []
    for pt in curve.curve_data:
        x_pts.append(pt.xvalue * (10**x_mult))
        y_pts.append(pt.yvalue * (10**y_mult))
    return x_pts, y_pts


def _get_y_ref_type(curve: Dercurve1) -> int:
    """Get the yRefType, converting from 1-based IEEE enum to 0-based."""
    return int(curve.y_ref_type.value) - 1


def _get_ramp_attrs(curve: Dercurve1) -> dict[str, int | None]:
    """Extract DERCurve ramp/filter attributes (IEEE B.23).

    Returns rampDecTms, rampIncTms, rampPT1Tms when present on the curve.
    """
    return {
        "ramp_dec_tms": curve.ramp_dec_tms,
        "ramp_inc_tms": curve.ramp_inc_tms,
        "ramp_pt1_tms": curve.ramp_pt1_tms,
    }


# ------------------------------------------------------------------
# Individual mode translators
# ------------------------------------------------------------------


def _is_mode_disabled(field: Any) -> bool:
    """Return True if *field* carries IEEE 2030.5-2023's ``disabled=true``.

    The 2023 schema added a ``disabled`` boolean attribute on every
    DERControl Mode element (DERCurveLink, FixedPF, etc.). Older parsed
    objects may not have the attribute -- treat absence as enabled (the
    spec default).
    """
    return field is not None and getattr(field, "disabled", False) is True


def translate_qv(
    base: DercontrolBase,
    curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModVoltVar to update_qv params."""
    link = base.op_mod_volt_var
    if link is None:
        return None
    if _is_mode_disabled(link):
        return ("update_qv", {"qv_mode_enable": 0})

    curve = _find_curve(link.href, curves)
    if curve is None:
        return ("update_qv", {"qv_mode_enable": 0})

    v_pts, q_pts = _scale_curve_points(curve)
    auto_vref = 1 if curve.autonomous_vref_enable is True else 0

    # vRef is PerCent (self-scaling: 10000 = 100.00%); the curve's xMultiplier
    # does not apply. /100 yields percent of nominal, matching the units of
    # v_pts after their xMultiplier scaling.
    vref_raw = curve.v_ref
    vref = vref_raw.value / 100 if vref_raw is not None else 0

    params: dict[str, Any] = {
        "qv_mode_enable": 1,
        "qv_vref": vref,
        "qv_pri": 1,
        "qv_olrt": curve.open_loop_tms / 100 if curve.open_loop_tms is not None else None,
        "qv_vref_auto_ena": auto_vref,
        "qv_vref_olrt": curve.autonomous_vref_time_constant,
        "qv_deptref": _get_y_ref_type(curve),
        "qv_curve_v_pts": v_pts,
        "qv_curve_q_pts": q_pts,
        **_get_ramp_attrs(curve),
    }
    return ("update_qv", params)


def translate_pv(
    base: DercontrolBase,
    curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModVoltWatt to update_pv params."""
    link = base.op_mod_volt_watt
    if link is None:
        return None
    if _is_mode_disabled(link):
        return ("update_pv", {"pv_mode_enable": 0})

    curve = _find_curve(link.href, curves)
    if curve is None:
        return ("update_pv", {"pv_mode_enable": 0})

    v_pts, p_pts = _scale_curve_points(curve)
    params: dict[str, Any] = {
        "pv_mode_enable": 1,
        "pv_olrt": curve.open_loop_tms / 100 if curve.open_loop_tms is not None else None,
        "pv_curve_v_pts": v_pts,
        "pv_curve_p_pts": p_pts,
        "pv_deptref": _get_y_ref_type(curve),
        **_get_ramp_attrs(curve),
    }
    return ("update_pv", params)


def translate_qp(
    base: DercontrolBase,
    curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModWattVar to update_qp params."""
    link = base.op_mod_watt_var
    if link is None:
        return None
    if _is_mode_disabled(link):
        return ("update_qp", {"qp_mode_enable": 0})

    curve = _find_curve(link.href, curves)
    if curve is None:
        return ("update_qp", {"qp_mode_enable": 0})

    p_pts, q_pts = _scale_curve_points(curve)
    params: dict[str, Any] = {
        "qp_mode_enable": 1,
        "qp_olrt": curve.open_loop_tms / 100 if curve.open_loop_tms is not None else None,
        "qp_curve_p_pts": p_pts,
        "qp_curve_q_pts": q_pts,
        "qp_deptref": _get_y_ref_type(curve),
        **_get_ramp_attrs(curve),
    }
    return ("update_qp", params)


def translate_p_lim(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModMaxLimW to update_p_lim params.

    FIX: Original used (value / 100) / 100 producing values 100x too small.
    opModMaxLimW is a PerCent (hundredths of percent), so we divide by 100
    to get percent of setMaxW.
    """
    field = base.op_mod_max_lim_w
    if field is None:
        return None

    p_lim_pct = field.value / 100
    return ("update_p_lim", {"p_lim_mode_enable": 1, "p_lim_w": p_lim_pct})


def translate_p_lim_inj(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModMaxLimWInject to update_p_lim_inj params.

    opModMaxLimWInject is UnsignedActivePowerControlType -- an absolute
    active-power limit in *watts* (``value * 10^multiplier``), NOT a percent
    (unlike opModMaxLimW). Emit the watts unchanged under ``p_lim_watts``; the
    connector converts to a percent of the device's WMax before writing the
    percent-only SunSpec WMaxLimPct register.
    """
    field = base.op_mod_max_lim_winject
    if field is None:
        return None

    multiplier = field.multiplier.value if field.multiplier is not None else 0
    p_lim_watts = field.value * (10 ** int(multiplier))
    return ("update_p_lim_inj", {"p_lim_mode_enable": 1, "p_lim_watts": p_lim_watts})


def translate_p_lim_abs(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModMaxLimWAbsorb to update_p_lim_abs params.

    Like opModMaxLimWInject, this is UnsignedActivePowerControlType (absolute
    watts, ``value * 10^multiplier``), not a percent. Emit the watts under
    ``p_lim_watts``; the connector records it for diagnostics (SunSpec model
    704 has no absorb-direction active-power limit, so it is never written).
    """
    field = base.op_mod_max_lim_wabsorb
    if field is None:
        return None

    multiplier = field.multiplier.value if field.multiplier is not None else 0
    p_lim_watts = field.value * (10 ** int(multiplier))
    return ("update_p_lim_abs", {"p_lim_mode_enable": 1, "p_lim_watts": p_lim_watts})


def translate_pf(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModFreqDroop to update_pf params."""
    droop = base.op_mod_freq_droop
    if droop is None:
        return None

    params: dict[str, Any] = {
        "pf_mode_enable": 1,
        "pf_dbof": droop.d_bof / 1000,
        "pf_dbuf": droop.d_buf / 1000,
        "pf_kof": droop.k_of / 1000,
        "pf_kuf": droop.k_uf / 1000,
        "pf_olrt": droop.open_loop_tms / 100,
        "pf_pmin": droop.p_min,
    }
    return ("update_pf", params)


def translate_freq_watt(
    base: DercontrolBase,
    curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModFreqWatt (curveType 0) to update_freq_watt params.

    IEEE B.23: Curve-based Frequency-Watt mode. X-axis is frequency (Hz),
    Y-axis is active power output (%setMaxW or similar per yRefType).
    """
    link = base.op_mod_freq_watt
    if link is None:
        return None
    if _is_mode_disabled(link):
        return ("update_freq_watt", {"fw_mode_enable": 0})

    curve = _find_curve(link.href, curves)
    if curve is None:
        return ("update_freq_watt", {"fw_mode_enable": 0})

    f_pts, p_pts = _scale_curve_points(curve)
    params: dict[str, Any] = {
        "fw_mode_enable": 1,
        "fw_curve_f_pts": f_pts,
        "fw_curve_p_pts": p_pts,
        "fw_olrt": curve.open_loop_tms / 100 if curve.open_loop_tms is not None else None,
        **_get_ramp_attrs(curve),
    }
    return ("update_freq_watt", params)


def translate_const_q(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModFixedVar to update_const_q params."""
    fv = base.op_mod_fixed_var
    if fv is None:
        return None

    ref_type = int(fv.ref_type.value) if fv.ref_type is not None else 1
    # opModFixedVar.value is IEEE PerCent (hundredths of a percent, 10000 =
    # 100%), so divide by 100 to get the percent the connector writes to
    # VarSetPct.cvalue -- mirroring translate_p_lim / translate_fixed_v. Without
    # this, e.g. 5300 (=53%) reached VarSetPct.cvalue as 5300%, overflowing the
    # int16 register.
    raw_pct = fv.value.value if fv.value is not None else 0
    q_value = raw_pct / 100

    params: dict[str, Any] = {
        "const_q_mode_enable": 1,
        "ref_type": ref_type,
        "const_q": None,
        "const_q_pct": q_value,
    }
    return ("update_const_q", params)


def translate_const_pf(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModFixedPFInjectW / opModFixedPFAbsorbW to update_const_pf."""
    inj_field = base.op_mod_fixed_pfinject_w
    abs_field = base.op_mod_fixed_pfabsorb_w

    if inj_field is None and abs_field is None:
        return None

    inj: dict[str, Any]
    if inj_field is not None and not _is_mode_disabled(inj_field):
        mult = int(inj_field.multiplier.value) if inj_field.multiplier is not None else 0
        pf_inj = round(inj_field.displacement * (10**mult), 2)
        inj = {"pf": pf_inj, "excitation": inj_field.excitation, "mode": 1}
    else:
        inj = {"mode": 0}

    abs_: dict[str, Any]
    if abs_field is not None and not _is_mode_disabled(abs_field):
        mult = int(abs_field.multiplier.value) if abs_field.multiplier is not None else 0
        pf_abs = round(abs_field.displacement * (10**mult), 2)
        abs_ = {"pf": pf_abs, "excitation": abs_field.excitation, "mode": 1}
    else:
        abs_ = {"mode": 0}

    return ("update_const_pf", {"inj": inj, "abs": abs_})


def translate_fixed_w(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModFixedW to update_fixed_w params.

    opModFixedW is SignedPerCent (hundredths of a percent of setMaxW), so
    ``WSet`` carries the percent (field.value / 100, e.g. 5000 -> 50.0) and
    ``WSetMod`` selects SunSpec 704's ``W_MAX_PCT`` mode (enum value 0).

    **The key is not the register.** SunSpec 704 reads ``WSetPct`` under
    ``W_MAX_PCT`` and ``WSet`` under ``WATTS``, but this dict is a transport,
    not a register map: it carries ``WSetMod`` alongside the value, so a
    consumer always knows which mode it is in and routes the value to the right
    point itself. Consumers here have standardized on ``WSet`` as the carrier
    for both modes, and moving the emitted key to match the register would break
    that contract for no gain -- it was tried and reverted.

    An earlier version of this docstring said the device "reads the percent from
    WSetPct" while the code emitted ``WSet``, which read as a defect in this
    function and was reported as one. The defect was real but downstream: a
    consumer copied ``WSet`` to the watts point without consulting ``WSetMod``.
    Write consumers that route on the mode.
    """
    field = base.op_mod_fixed_w
    if field is None:
        return None

    return ("update_fixed_w", {"WSetEna": 1, "WSetMod": 0, "WSet": field.value / 100})


# ------------------------------------------------------------------
# Gap 16: connect/energize
# ------------------------------------------------------------------


def translate_connect(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModConnect and opModEnergize with AND logic.

    IEEE 10.10.4.2.2: When both are present, values are logically ANDed
    to determine the connection state.
    """
    connect = base.op_mod_connect
    energize = base.op_mod_energize
    if connect is None and energize is None:
        return None

    # AND logic: both must be True for connected state
    connect_val = connect if connect is not None else True
    energize_val = energize if energize is not None else True
    connected = connect_val and energize_val

    return ("update_connect", {"connected": connected})


# ------------------------------------------------------------------
# Gap 17: DERControlType2 modes
# ------------------------------------------------------------------


def _translate_pct_control(
    field: object | None,
    method: str,
    enable_key: str,
    value_key: str,
) -> tuple[str, dict[str, Any]] | None:
    """Generic translator for PerCentControlType fields."""
    if field is None:
        return None
    value = getattr(field, "value", 0)
    return (method, {enable_key: 1, value_key: value / 100})


def _translate_unsigned_fixed_var_control(
    field: object | None,
    method: str,
    enable_key: str,
    value_key: str,
) -> tuple[str, dict[str, Any]] | None:
    """Generic translator for UnsignedFixedVarControlType fields."""
    if field is None:
        return None
    ref_type = int(getattr(getattr(field, "ref_type", None), "value", 1))
    raw_value = getattr(field, "value", 0)
    # UnsignedFixedVar.value is a PerCent model; unwrap to int
    value = getattr(raw_value, "value", raw_value)
    return (method, {enable_key: 1, value_key: value, "ref_type": ref_type})


def _translate_power_control(
    field: object | None,
    method: str,
    enable_key: str,
    value_key: str,
) -> tuple[str, dict[str, Any]] | None:
    """Generic translator for power control types with value+multiplier."""
    if field is None:
        return None
    value = getattr(field, "value", 0)
    mult_obj = getattr(field, "multiplier", None)
    multiplier = int(getattr(mult_obj, "value", 0)) if mult_obj is not None else 0
    scaled = value * (10**multiplier)
    return (method, {enable_key: 1, value_key: scaled})


def translate_max_lim_pct_va_absorb(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_pct_control(
        base.op_mod_max_lim_pct_vaabsorb, "update_max_lim_pct_va_absorb", "mode_enable", "pct"
    )


def translate_max_lim_pct_va_inject(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_pct_control(
        base.op_mod_max_lim_pct_vainject, "update_max_lim_pct_va_inject", "mode_enable", "pct"
    )


def translate_max_lim_pct_var_absorb(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_unsigned_fixed_var_control(
        base.op_mod_max_lim_pct_var_absorb,
        "update_max_lim_pct_var_absorb",
        "mode_enable",
        "pct",
    )


def translate_max_lim_pct_var_inject(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_unsigned_fixed_var_control(
        base.op_mod_max_lim_pct_var_inject,
        "update_max_lim_pct_var_inject",
        "mode_enable",
        "pct",
    )


def translate_max_lim_pct_w_absorb(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_pct_control(
        base.op_mod_max_lim_pct_wabsorb, "update_max_lim_pct_w_absorb", "mode_enable", "pct"
    )


def translate_max_lim_var_absorb(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_power_control(
        base.op_mod_max_lim_var_absorb, "update_max_lim_var_absorb", "mode_enable", "var"
    )


def translate_max_lim_var_inject(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_power_control(
        base.op_mod_max_lim_var_inject, "update_max_lim_var_inject", "mode_enable", "var"
    )


def translate_target_v(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_power_control(
        base.op_mod_target_v, "update_target_v", "mode_enable", "voltage"
    )


def translate_target_var(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_power_control(
        base.op_mod_target_var, "update_target_var", "mode_enable", "var"
    )


def translate_target_w(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_power_control(base.op_mod_target_w, "update_target_w", "mode_enable", "watts")


# ------------------------------------------------------------------
# Gap 18: delta modes
# ------------------------------------------------------------------


def translate_delta_w(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModDeltaW to update_delta_w params."""
    field = base.op_mod_delta_w
    if field is None:
        return None

    multiplier = int(field.multiplier.value) if field.multiplier is not None else 0
    delta = field.value * (10**multiplier)

    return (
        "update_delta_w",
        {
            "delta_w_mode_enable": 1,
            "delta_w": delta,
            "bidirectional": field.bidirectional,
        },
    )


def translate_delta_var(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModDeltaVar to update_delta_var params."""
    field = base.op_mod_delta_var
    if field is None:
        return None

    multiplier = int(field.multiplier.value) if field.multiplier is not None else 0
    delta = field.value * (10**multiplier)

    return (
        "update_delta_var",
        {
            "delta_var_mode_enable": 1,
            "delta_var": delta,
            "bidirectional": field.bidirectional,
        },
    )


# ------------------------------------------------------------------
# Gap 19: fixed voltage, permits
# ------------------------------------------------------------------


def translate_fixed_v(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModFixedV (%setVNom in hundredths) to update_fixed_v."""
    field = base.op_mod_fixed_v
    if field is None:
        return None
    return ("update_fixed_v", {"fixed_v_mode_enable": 1, "fixed_v_pct": field.value / 100})


def translate_grid_connect_permit(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModGridConnectPermit boolean."""
    if base.op_mod_grid_connect_permit is None:
        return None
    return ("update_grid_connect_permit", {"permit": base.op_mod_grid_connect_permit})


def translate_island_permit(
    base: DercontrolBase,
    _curves: list[Dercurve1],
) -> tuple[str, dict[str, Any]] | None:
    """Translate opModIslandPermit boolean."""
    if base.op_mod_island_permit is None:
        return None
    return ("update_island_permit", {"permit": base.op_mod_island_permit})


def _translate_voltage_ride_through(
    link: DercurveLink | None,
    curves: list[Dercurve1],
    method_name: str,
    enable_key: str,
    tms_key: str,
    val_key: str,
) -> tuple[str, dict[str, Any]] | None:
    """Generic translator for voltage ride-through modes (OV/UV/MC)."""
    if link is None:
        return None
    if _is_mode_disabled(link):
        return (method_name, {enable_key: 0})

    curve = _find_curve(link.href, curves)
    if curve is None:
        return (method_name, {enable_key: 0})

    t_pts, v_pts = _scale_curve_points(curve)
    return (method_name, {enable_key: 1, tms_key: t_pts, val_key: v_pts, **_get_ramp_attrs(curve)})


def _translate_frequency_ride_through(
    link: DercurveLink | None,
    curves: list[Dercurve1],
    method_name: str,
    enable_key: str,
    tms_key: str,
    val_key: str,
) -> tuple[str, dict[str, Any]] | None:
    """Generic translator for frequency ride-through modes (OF/UF)."""
    if link is None:
        return None
    if _is_mode_disabled(link):
        return (method_name, {enable_key: 0})

    curve = _find_curve(link.href, curves)
    if curve is None:
        return (method_name, {enable_key: 0})

    t_pts, f_pts = _scale_curve_points(curve)
    return (method_name, {enable_key: 1, tms_key: t_pts, val_key: f_pts, **_get_ramp_attrs(curve)})


def translate_ov(
    base: DercontrolBase, curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_voltage_ride_through(
        base.op_mod_hvrtmust_trip,
        curves,
        "update_ov",
        "ov_mode_enable",
        "ov_curve_tms_points",
        "ov_curve_v_pts",
    )


def translate_uv(
    base: DercontrolBase, curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_voltage_ride_through(
        base.op_mod_lvrtmust_trip,
        curves,
        "update_uv",
        "uv_mode_enable",
        "uv_curve_tms_points",
        "uv_curve_v_pts",
    )


def translate_ov_mc(
    base: DercontrolBase, curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_voltage_ride_through(
        base.op_mod_hvrtmomentary_cessation,
        curves,
        "update_ov_mc",
        "ov_mode_enable",
        "ov_curve_tms_points",
        "ov_curve_v_pts",
    )


def translate_uv_mc(
    base: DercontrolBase, curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_voltage_ride_through(
        base.op_mod_lvrtmomentary_cessation,
        curves,
        "update_uv_mc",
        "uv_mode_enable",
        "uv_curve_tms_points",
        "uv_curve_v_pts",
    )


def translate_of(
    base: DercontrolBase, curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_frequency_ride_through(
        base.op_mod_hfrtmust_trip,
        curves,
        "update_of",
        "of_mode_enable",
        "of_curve_tms_points",
        "of_curve_f_pts",
    )


def translate_uf(
    base: DercontrolBase, curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_frequency_ride_through(
        base.op_mod_lfrtmust_trip,
        curves,
        "update_uf",
        "uf_mode_enable",
        "uf_curve_tms_points",
        "uf_curve_f_pts",
    )


def _translate_csipaus_power_limit(
    base: DercontrolBase,
    method_name: str,
    enable_key: str,
    value_key: str,
) -> tuple[str, dict[str, Any]] | None:
    """Translate CSIP-AUS power limit from other_element list.

    FIX: Checks both namespaced and non-namespaced elements consistently,
    unlike the original which only checked namespaced for exp_lim.
    """
    # CSIP-AUS fields arrive via DercontrolBase.other_element as parsed
    # Pydantic extension objects (OpModExpLimW, OpModImpLimW, etc.)
    # They have .value and .multiplier attributes (inheriting from ActivePower).
    xml_name_map = {
        "update_exp_lim": "opModExpLimW",
        "update_imp_lim": "opModImpLimW",
        "update_gen_lim": "opModGenLimW",
        "update_load_lim": "opModLoadLimW",
    }
    target_name = xml_name_map.get(method_name)
    if target_name is None:
        return None

    for elem in base.other_element:
        meta_name = None
        if hasattr(elem, "Meta") and hasattr(elem.Meta, "name"):
            meta_name = elem.Meta.name
        elif hasattr(elem, "__class__") and hasattr(elem.__class__, "Meta"):
            meta = elem.__class__.Meta
            meta_name = getattr(meta, "name", None)

        if meta_name == target_name and hasattr(elem, "value") and hasattr(elem, "multiplier"):
            value = elem.value
            multiplier = elem.multiplier
            if hasattr(multiplier, "value"):
                multiplier = multiplier.value
            w = value * (10 ** int(multiplier))
            return (method_name, {enable_key: 1, value_key: w})

    return None


def translate_exp_lim(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_csipaus_power_limit(
        base, "update_exp_lim", "exp_lim_mode_enable", "exp_lim_w"
    )


def translate_imp_lim(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_csipaus_power_limit(
        base, "update_imp_lim", "imp_lim_mode_enable", "imp_lim_w"
    )


def translate_gen_lim(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_csipaus_power_limit(
        base, "update_gen_lim", "gen_lim_mode_enable", "gen_lim_w"
    )


def translate_load_lim(
    base: DercontrolBase, _curves: list[Dercurve1]
) -> tuple[str, dict[str, Any]] | None:
    return _translate_csipaus_power_limit(
        base, "update_load_lim", "load_lim_mode_enable", "load_lim_w"
    )


# ------------------------------------------------------------------
# Translator registry
# ------------------------------------------------------------------

TranslatorFunc = Callable[
    [DercontrolBase, list[Dercurve1]],
    tuple[str, dict[str, Any]] | None,
]


@dataclass(frozen=True)
class ModeTranslation:
    """Maps a DercontrolBase field to its translator function."""

    name: str
    translator: TranslatorFunc
    field_name: str = ""


MODE_TRANSLATIONS: list[ModeTranslation] = [
    ModeTranslation("volt_var", translate_qv, "op_mod_volt_var"),
    ModeTranslation("volt_watt", translate_pv, "op_mod_volt_watt"),
    ModeTranslation("watt_var", translate_qp, "op_mod_watt_var"),
    ModeTranslation("max_lim_w", translate_p_lim, "op_mod_max_lim_w"),
    ModeTranslation("max_lim_w_inject", translate_p_lim_inj, "op_mod_max_lim_winject"),
    ModeTranslation("max_lim_w_absorb", translate_p_lim_abs, "op_mod_max_lim_wabsorb"),
    ModeTranslation("freq_droop", translate_pf, "op_mod_freq_droop"),
    ModeTranslation("freq_watt", translate_freq_watt, "op_mod_freq_watt"),
    ModeTranslation("fixed_var", translate_const_q, "op_mod_fixed_var"),
    ModeTranslation("fixed_pf", translate_const_pf, "op_mod_fixed_pfabsorb_w"),
    ModeTranslation("fixed_w", translate_fixed_w, "op_mod_fixed_w"),
    ModeTranslation("hvrt_must_trip", translate_ov, "op_mod_hvrtmust_trip"),
    ModeTranslation("lvrt_must_trip", translate_uv, "op_mod_lvrtmust_trip"),
    ModeTranslation("hvrt_momentary_cessation", translate_ov_mc, "op_mod_hvrtmomentary_cessation"),
    ModeTranslation("lvrt_momentary_cessation", translate_uv_mc, "op_mod_lvrtmomentary_cessation"),
    ModeTranslation("hfrt_must_trip", translate_of, "op_mod_hfrtmust_trip"),
    ModeTranslation("lfrt_must_trip", translate_uf, "op_mod_lfrtmust_trip"),
    ModeTranslation("connect", translate_connect, "op_mod_connect"),
    ModeTranslation(
        "max_lim_pct_va_absorb", translate_max_lim_pct_va_absorb, "op_mod_max_lim_pct_vaabsorb"
    ),
    ModeTranslation(
        "max_lim_pct_va_inject", translate_max_lim_pct_va_inject, "op_mod_max_lim_pct_vainject"
    ),
    ModeTranslation(
        "max_lim_pct_var_absorb", translate_max_lim_pct_var_absorb, "op_mod_max_lim_pct_var_absorb"
    ),
    ModeTranslation(
        "max_lim_pct_var_inject", translate_max_lim_pct_var_inject, "op_mod_max_lim_pct_var_inject"
    ),
    ModeTranslation(
        "max_lim_pct_w_absorb", translate_max_lim_pct_w_absorb, "op_mod_max_lim_pct_wabsorb"
    ),
    ModeTranslation(
        "max_lim_var_absorb", translate_max_lim_var_absorb, "op_mod_max_lim_var_absorb"
    ),
    ModeTranslation(
        "max_lim_var_inject", translate_max_lim_var_inject, "op_mod_max_lim_var_inject"
    ),
    ModeTranslation("target_v", translate_target_v, "op_mod_target_v"),
    ModeTranslation("target_var", translate_target_var, "op_mod_target_var"),
    ModeTranslation("target_w", translate_target_w, "op_mod_target_w"),
    ModeTranslation("delta_w", translate_delta_w, "op_mod_delta_w"),
    ModeTranslation("delta_var", translate_delta_var, "op_mod_delta_var"),
    ModeTranslation("fixed_v", translate_fixed_v, "op_mod_fixed_v"),
    ModeTranslation(
        "grid_connect_permit", translate_grid_connect_permit, "op_mod_grid_connect_permit"
    ),
    ModeTranslation("island_permit", translate_island_permit, "op_mod_island_permit"),
    ModeTranslation("exp_lim", translate_exp_lim),
    ModeTranslation("imp_lim", translate_imp_lim),
    ModeTranslation("gen_lim", translate_gen_lim),
    ModeTranslation("load_lim", translate_load_lim),
]


def _is_disabled(base: DercontrolBase, field_name: str) -> bool:
    """Check if a control mode field has its disabled attribute set."""
    if not field_name:
        return False
    field_obj = getattr(base, field_name, None)
    if field_obj is None:
        return False
    return bool(getattr(field_obj, "disabled", False))


def translate_controls(
    base: DercontrolBase,
    curves: list[Dercurve1],
    *,
    mode_filter: frozenset[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Translate all active control modes in a DercontrolBase.

    Returns a list of (method_name, params) tuples for each active mode.
    Inactive modes (where the field is None) are skipped. Modes with
    ``disabled=True`` on the control type object are also skipped.

    If ``mode_filter`` is provided, only modes whose ``field_name`` is in
    the filter set are translated.
    """
    results: list[tuple[str, dict[str, Any]]] = []
    for mt in MODE_TRANSLATIONS:
        if mode_filter is not None and mt.field_name and mt.field_name not in mode_filter:
            continue
        if _is_disabled(base, mt.field_name):
            continue
        result = mt.translator(base, curves)
        if result is not None:
            results.append(result)
    return results


def translate_default_controls(
    dderc: DefaultDercontrol1,
    curves: list[Dercurve1],
    *,
    mode_filter: frozenset[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Translate DefaultDercontrol -- same as regular plus ramp and ES params.

    DefaultDercontrol has the same DercontrolBase plus additional fields
    like setGradW for power ramp rate and enter-service parameters.
    """
    results = translate_controls(dderc.dercontrol_base, curves, mode_filter=mode_filter)

    if dderc.set_grad_w is not None:
        results.append(("update_p_ramp", {"p_ramp_mode_enable": 1, "w_ramp": dderc.set_grad_w}))

    if dderc.set_soft_grad_w is not None:
        results.append(
            ("update_p_ramp", {"p_ramp_mode_enable": 1, "soft_grad_w": dderc.set_soft_grad_w})
        )

    # IEEE B.23 / Annex E Table E.12: Enter-service parameters
    es_params: dict[str, Any] = {}
    _ES_FIELDS = (
        ("set_esdelay", "es_delay"),
        ("set_eshigh_freq", "es_high_freq"),
        ("set_eslow_freq", "es_low_freq"),
        ("set_eshigh_volt", "es_high_volt"),
        ("set_eslow_volt", "es_low_volt"),
        ("set_esramp_tms", "es_ramp_tms"),
        ("set_esrandom_delay", "es_random_delay"),
    )
    for model_field, param_key in _ES_FIELDS:
        value = getattr(dderc, model_field, None)
        if value is not None:
            es_params[param_key] = value

    if es_params:
        results.append(("update_es_permit_service", es_params))

    return results
