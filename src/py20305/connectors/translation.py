"""Pure SunSpec point-path translation connector.

Translates intermediate control parameters to SunSpec Modbus point paths.
No I/O, no external dependencies. Returns dicts like:
    {'DERVoltVar[0].Ena': 1, 'DERVoltVar[0].Crv[1].Pt[0].V': 240}

Also includes translate_mup() for MirrorUsagePoint -> SunSpec point mapping.

Synchronous API
---------------
All translation logic is pure computation. The ``translate_to_sunspec()``
function provides synchronous access for callers that cannot use ``await``
(e.g. a threaded host application). It accepts the same
``(method_name, params)`` tuples returned by ``translate_controls()``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from py20305.connectors.base import BaseConnector, ConnectorPayload

# IEEE 2030.5 UOM codes to SunSpec DERMeasureAC point mapping
# Format: {uom_code: (sunspec_point, sunspec_multiplier_offset)}
UOM_TO_SUNSPEC: dict[int, tuple[str, int]] = {
    38: ("DERMeasureAC[0].W", 0),  # Watts
    63: ("DERMeasureAC[0].Var", 0),  # Vars
    33: ("DERMeasureAC[0].Hz", -3),  # Frequency (Hz, stored as mHz)
    29: ("DERMeasureAC[0].LNV", -1),  # Voltage (V, stored as decivolts)
    65: ("DERMeasureAC[0].PF", -3),  # Power Factor (stored as per-thousand)
    61: ("DERMeasureAC[0].VA", 0),  # Apparent Power (VA)
    5: ("DERMeasureAC[0].A", -1),  # Current (A, stored as deciamps)
}


def _curve_points(
    prefix: str,
    x_key: str,
    y_key: str,
    x_pts: list[Any],
    y_pts: list[Any],
) -> dict[str, Any]:
    """Build SunSpec point dict for curve data."""
    points: dict[str, Any] = {}
    num_pts = max(len(x_pts), len(y_pts))
    if num_pts > 0:
        points[f"{prefix}.ActPt"] = num_pts
    for i in range(len(x_pts)):
        points[f"{prefix}.Pt[{i}].{x_key}"] = x_pts[i]
        if i < len(y_pts):
            points[f"{prefix}.Pt[{i}].{y_key}"] = y_pts[i]
    return points


# ------------------------------------------------------------------
# Private sync translation functions (pure computation)
# ------------------------------------------------------------------


def _translate_qv(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {
        "DERVoltVar[0].Ena": params.get("qv_mode_enable"),
        "DERVoltVar[0].Crv[1].VRef": params.get("qv_vref"),
        "DERVoltVar[0].Crv[1].VRefAutoEna": params.get("qv_vref_auto_ena"),
        "DERVoltVar[0].Crv[1].VRefAutoTms": params.get("qv_vref_olrt"),
        "DERVoltVar[0].Crv[1].RspTms": params.get("qv_olrt"),
    }
    v_pts = params.get("qv_curve_v_pts", [])
    q_pts = params.get("qv_curve_q_pts", [])
    points.update(_curve_points("DERVoltVar[0].Crv[1]", "V", "Var", v_pts, q_pts))
    return points


def _translate_pv(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {
        "DERVoltWatt[0].Ena": params.get("pv_mode_enable"),
        "DERVoltWatt[0].Crv[1].RspTms": params.get("pv_olrt"),
    }
    v_pts = params.get("pv_curve_v_pts", [])
    p_pts = params.get("pv_curve_p_pts", [])
    points.update(_curve_points("DERVoltWatt[0].Crv[1]", "V", "W", v_pts, p_pts))
    return points


def _translate_qp(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {
        "DERWattVar[0].Ena": params.get("qp_mode_enable"),
    }
    p_pts = params.get("qp_curve_p_pts", [])
    q_pts = params.get("qp_curve_q_pts", [])
    points.update(_curve_points("DERWattVar[0].Crv[1]", "W", "Var", p_pts, q_pts))
    return points


def _translate_p_lim(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "DERCtlAC[0].WMaxLimPctEna": params.get("p_lim_mode_enable"),
        "DERCtlAC[0].WMaxLimPct": params.get("p_lim_w"),
    }


def _translate_pf(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "DERFreqDroop[0].Ena": params.get("pf_mode_enable"),
        "DERFreqDroop[0].Ctl[1].DbOf": params.get("pf_dbof"),
        "DERFreqDroop[0].Ctl[1].DbUf": params.get("pf_dbuf"),
        "DERFreqDroop[0].Ctl[1].KOf": params.get("pf_kof"),
        "DERFreqDroop[0].Ctl[1].KUf": params.get("pf_kuf"),
        "DERFreqDroop[0].Ctl[1].RspTms": params.get("pf_olrt"),
        "DERFreqDroop[0].Ctl[1].PMin": params.get("pf_pmin"),
    }


def _translate_const_q(params: dict[str, Any]) -> dict[str, Any]:
    ena = params.get("const_q_mode_enable")
    if ena is False or ena == 0:
        return {"DERCtlAC[0].VarSetEna": 0}

    points: dict[str, Any] = {"DERCtlAC[0].VarSetEna": 1 if ena else 0}
    const_q = params.get("const_q")
    const_q_pct = params.get("const_q_pct")
    if const_q is not None:
        points["DERCtlAC[0].VarSet"] = const_q
    if const_q_pct is not None:
        points["DERCtlAC[0].VarSetPct"] = const_q_pct
    return points


def _translate_const_pf(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {}

    inj = params.get("inj", {})
    if inj.get("mode"):
        points["DERCtlAC[0].PFWInjEna"] = 1
        points["DERCtlAC[0].PFWInj.PF"] = inj.get("pf")
        points["DERCtlAC[0].PFWInj.Ext"] = inj.get("excitation")
    else:
        points["DERCtlAC[0].PFWInjEna"] = 0

    abs_ = params.get("abs", {})
    if abs_.get("mode"):
        points["DERCtlAC[0].PFWAbsEna"] = 1
        points["DERCtlAC[0].PFWAbs.PF"] = abs_.get("pf")
        points["DERCtlAC[0].PFWAbs.Ext"] = abs_.get("excitation")
    else:
        points["DERCtlAC[0].PFWAbsEna"] = 0

    return points


def _translate_fixed_w(params: dict[str, Any]) -> dict[str, Any]:
    """Map opModFixedW onto the point ``WSetMod`` selects.

    ``WSet`` and ``WSetPct`` are mode-gated registers: SunSpec 704 reads
    ``WSetPct`` under ``W_MAX_PCT`` (0) and ``WSet`` under ``WATTS`` (1). The
    params dict is a transport that carries ``WSetMod`` alongside the value, so
    either key may carry it -- but this function maps keys onto *point paths*,
    so it has to route rather than copy.

    Previously it copied ``params["WSet"]`` to ``DERCtlAC[0].WSet``
    unconditionally. ``translate_fixed_w`` emits a percent there while
    selecting mode 0, so a percent was written to the watts point and the
    percent point the mode actually reads was left unset -- the setpoint never
    applied.
    """
    points: dict[str, Any] = {
        "DERCtlAC[0].WSetEna": params.get("WSetEna"),
        "DERCtlAC[0].WSetMod": params.get("WSetMod"),
    }
    mode = params.get("WSetMod")
    value = params.get("WSetPct")
    if value is None:
        value = params.get("WSet")
    if mode == 0:
        points["DERCtlAC[0].WSetPct"] = value
    elif mode == 1:
        points["DERCtlAC[0].WSet"] = value
    else:
        # Unknown or absent mode: no basis to route on, so preserve the prior
        # pass-through rather than guessing which point was meant.
        points["DERCtlAC[0].WSet"] = params.get("WSet")
        if params.get("WSetPct") is not None:
            points["DERCtlAC[0].WSetPct"] = params["WSetPct"]
    return points


def _translate_ov(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {"DERTripHV[0].Ena": params.get("ov_mode_enable")}
    tms = params.get("ov_curve_tms_points", [])
    v = params.get("ov_curve_v_pts", [])
    points.update(_curve_points("DERTripHV[0].Crv[1].MustTrip", "Tms", "V", tms, v))
    return points


def _translate_uv(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {"DERTripLV[0].Ena": params.get("uv_mode_enable")}
    tms = params.get("uv_curve_tms_points", [])
    v = params.get("uv_curve_v_pts", [])
    points.update(_curve_points("DERTripLV[0].Crv[1].MustTrip", "Tms", "V", tms, v))
    return points


def _translate_ov_mc(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {"DERTripHV[0].Ena": params.get("ov_mode_enable")}
    tms = params.get("ov_curve_tms_points", [])
    v = params.get("ov_curve_v_pts", [])
    points.update(_curve_points("DERTripHV[0].Crv[1].MomCess", "Tms", "V", tms, v))
    return points


def _translate_uv_mc(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {"DERTripLV[0].Ena": params.get("uv_mode_enable")}
    tms = params.get("uv_curve_tms_points", [])
    v = params.get("uv_curve_v_pts", [])
    points.update(_curve_points("DERTripLV[0].Crv[1].MomCess", "Tms", "V", tms, v))
    return points


def _translate_of(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {"DERTripHF[0].Ena": params.get("of_mode_enable")}
    tms = params.get("of_curve_tms_points", [])
    f = params.get("of_curve_f_pts", [])
    points.update(_curve_points("DERTripHF[0].Crv[1].MustTrip", "Tms", "Hz", tms, f))
    return points


def _translate_uf(params: dict[str, Any]) -> dict[str, Any]:
    points: dict[str, Any] = {"DERTripLF[0].Ena": params.get("uf_mode_enable")}
    tms = params.get("uf_curve_tms_points", [])
    f = params.get("uf_curve_f_pts", [])
    points.update(_curve_points("DERTripLF[0].Crv[1].MustTrip", "Tms", "Hz", tms, f))
    return points


# ------------------------------------------------------------------
# Sync dispatch table
# ------------------------------------------------------------------

_SyncTranslator = Callable[[dict[str, Any]], dict[str, Any]]

_SYNC_TRANSLATORS: dict[str, _SyncTranslator] = {
    "update_qv": _translate_qv,
    "update_pv": _translate_pv,
    "update_qp": _translate_qp,
    "update_p_lim": _translate_p_lim,
    "update_pf": _translate_pf,
    "update_const_q": _translate_const_q,
    "update_const_pf": _translate_const_pf,
    "update_fixed_w": _translate_fixed_w,
    "update_ov": _translate_ov,
    "update_uv": _translate_uv,
    "update_ov_mc": _translate_ov_mc,
    "update_uv_mc": _translate_uv_mc,
    "update_of": _translate_of,
    "update_uf": _translate_uf,
}


def translate_to_sunspec(method_name: str, params: dict[str, Any]) -> ConnectorPayload:
    """Synchronously translate control params to SunSpec point paths.

    Accepts the same ``(method_name, params)`` tuples returned by
    ``translate_controls()`` and ``translate_default_controls()``.

    Returns a dict of SunSpec point paths, or ``None`` if the method
    is not a recognized translation method (e.g. methods only defined
    on BaseConnector with no translation logic).
    """
    translator = _SYNC_TRANSLATORS.get(method_name)
    if translator is None:
        return None
    return translator(params)


class ConnectorTranslation(BaseConnector):
    """Translates intermediate parameters to SunSpec Modbus point paths."""

    connector_name: str = "TranslationConnector"

    # ------------------------------------------------------------------
    # Curve-based modes
    # ------------------------------------------------------------------

    async def update_qv(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_qv(params)

    async def update_pv(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_pv(params)

    async def update_qp(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_qp(params)

    # ------------------------------------------------------------------
    # Power limits
    # ------------------------------------------------------------------

    async def update_p_lim(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_p_lim(params)

    # ------------------------------------------------------------------
    # Frequency droop
    # ------------------------------------------------------------------

    async def update_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_pf(params)

    # ------------------------------------------------------------------
    # Constant reactive power
    # ------------------------------------------------------------------

    async def update_const_q(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_const_q(params)

    # ------------------------------------------------------------------
    # Constant power factor
    # ------------------------------------------------------------------

    async def update_const_pf(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_const_pf(params)

    # ------------------------------------------------------------------
    # Fixed active power
    # ------------------------------------------------------------------

    async def update_fixed_w(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_fixed_w(params)

    # ------------------------------------------------------------------
    # Voltage ride-through
    # ------------------------------------------------------------------

    async def update_ov(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_ov(params)

    async def update_uv(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_uv(params)

    async def update_ov_mc(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_ov_mc(params)

    async def update_uv_mc(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_uv_mc(params)

    # ------------------------------------------------------------------
    # Frequency ride-through
    # ------------------------------------------------------------------

    async def update_of(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_of(params)

    async def update_uf(self, params: dict[str, Any]) -> ConnectorPayload:
        return _translate_uf(params)


def translate_mup(mup_readings: list[dict[str, Any]]) -> dict[str, Any]:
    """Translate MirrorUsagePoint meter readings to SunSpec points.

    Args:
        mup_readings: List of reading dicts, each with:
            - uom: IEEE 2030.5 unit of measure code
            - value: The reading value
            - multiplier: Power-of-ten multiplier (default 0)

    Returns:
        Dict of SunSpec point paths to converted values.
    """
    points: dict[str, Any] = {}
    for reading in mup_readings:
        uom = reading.get("uom")
        if uom is None or uom not in UOM_TO_SUNSPEC:
            continue

        value = reading.get("value")
        if value is None:
            continue

        ieee_mult = reading.get("multiplier", 0) or 0
        sunspec_point, sunspec_mult = UOM_TO_SUNSPEC[uom]

        try:
            converted = int(value * (10 ** (ieee_mult - sunspec_mult)))
            points[sunspec_point] = converted
        except (TypeError, ValueError):
            continue

    return points
