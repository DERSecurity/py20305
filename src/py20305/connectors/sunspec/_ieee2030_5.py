"""Convert between SunSpec points and the IEEE 2030.5 connector contract.

The Modbus core reads and writes SunSpec points. Everything this package hands
upward is IEEE 2030.5, so the conversion happens here, in both directions:

- Monitoring: each SunSpec measurement becomes a :class:`Reading` with its
  IEEE 2030.5 uom, phase, kind and data qualifier. The system voltage is the
  line-to-line average, so it takes the line-to-line PhaseCode of the wiring
  (AB for single- and split-phase, ABC for three-phase); the per-line
  voltages are line to neutral (AN, BN, CN). The system current is a total
  across the lines, phase A, AB or ABC by the wiring. A meter's average
  voltage may be line to neutral, so it carries no PhaseCode.
- Status: model 701 ``InvSt`` n is InverterStatusType n + 1; ``St`` OFF/ON is
  OperationalModeStatusType 1/2; ``ConnSt`` with ``InvSt`` gives the
  2030.5-2023 ``connectStatus`` bitmap (connected; energized while running or
  throttled) and the 2030.5-2018 ``genConnectStatus`` bitmap (connected;
  available while sleeping, starting, running or throttled; operating while
  running or throttled; fault while in fault); ``Alrm`` bits 8 to 11 become
  ``alarmStatus`` bits 3, 4, 1 and 2, and the other alarms are not carried.
- Ratings and settings: the model 702 points take their DERCapability and
  DERSettings element names. ``AMaxRtg`` with ``A_SF`` is ``rtgMaxA``.
- Controls: each IEEE 2030.5 element becomes the core's write, with its
  parameters in the units the core takes.

Sources: the IEEE 2030.5-2018 and 2030.5-2023 schemas and the SunSpec model
definitions.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from py20305.connectors.reading import Reading, is_valid_reading

# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

#: (uom, kind, data qualifier) of each system quantity.
_SYSTEM: dict[str, tuple[int, int, int]] = {
    "W": (38, 37, 12),
    "Var": (63, 37, 12),
    "Hz": (33, 37, 12),
    "V": (29, 37, 2),
    "PF": (65, 0, 12),
    "VA": (61, 37, 12),
    "A": (5, 37, 12),
}
_LINE_PHASE = {1: 128, 2: 64, 3: 32}
_LINE_NEUTRAL_PHASE = {1: 129, 2: 65, 3: 33}
_LINE_TO_LINE: dict[str, int] = {"VL1L2": 132, "VL2L3": 66, "VL3L1": 40}
_PER_LINE_KEY = re.compile(r"^(W|Var|V|PF|VA|A)L([1-3])$")
#: Lines with per-line readings, and line-to-line pairs, by SunSpec ``ACType``.
_AC_TYPE_LINES = {0: 1, 1: 2, 2: 3}
_AC_TYPE_LINE_TO_LINE = {0: 0, 1: 1, 2: 3}
#: PhaseCode of the line-to-line voltage average, by ``ACType``. A
#: single-phase line-to-line voltage is measured across L1 and L2, so AB.
_LINE_TO_LINE_PHASE: dict[int, int] = {0: 132, 1: 132, 2: 224}
#: PhaseCode of a total across the lines, by ``ACType``.
_TOTAL_PHASE: dict[int, int] = {0: 128, 1: 132, 2: 224}

#: The power totals the signed-load profile tags with the wiring's PhaseCode.
_POWER_TOTALS = frozenset({"W", "Var", "VA"})
#: Energy readings: uom 72 (Wh), summation, forward or reverse.
_ENERGY_FLOW = {"WhFwd": 1, "WhRev": 19}


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return int(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number: float = value
    return number


def line_to_line_phase(ac_type: Any) -> int | None:
    """Return the PhaseCode of the line-to-line voltage average for ``ACType``, or None."""
    code = _as_int(ac_type)
    return None if code is None else _LINE_TO_LINE_PHASE.get(code)


def total_phase(ac_type: Any) -> int | None:
    """Return the PhaseCode of a total across the lines for ``ACType``, or None."""
    code = _as_int(ac_type)
    return None if code is None else _TOTAL_PHASE.get(code)


def readings(
    values: Mapping[str, Any],
    *,
    line_to_line_voltage: bool,
    phased_totals: bool = False,
    wiring: int | None = None,
) -> list[Reading]:
    """Return the IEEE 2030.5 readings for the monitoring values the core read.

    ``line_to_line_voltage`` says whether the system voltage is the
    line-to-line average (an inverter's ``LLV``) rather than a meter's
    average voltage. ``phased_totals`` gives the real, reactive and apparent
    power totals and the state of energy the wiring's PhaseCode, as the
    signed-load reading profile registers them. Per-line readings are kept
    for the lines the wiring has. ``wiring`` is the ``ACType`` the device last
    stated, used when this read did not return one, so a read that loses it
    does not turn a three-phase device into a single-phase one. A value that is
    missing or not a number gives no reading.
    """
    ac_type = _as_int(values.get("ACType"))
    if ac_type is None:
        ac_type = _as_int(wiring)
    lines = _AC_TYPE_LINES.get(ac_type, 1) if ac_type is not None else 1
    pairs = set(list(_LINE_TO_LINE)[: _AC_TYPE_LINE_TO_LINE.get(ac_type, 0)]) if ac_type else set()
    voltage_phase = line_to_line_phase(ac_type) if line_to_line_voltage else None
    current_phase = total_phase(ac_type)

    out: list[Reading] = []
    for key, raw in values.items():
        value = _number(raw)
        if value is None:
            continue
        reading: Reading | None = None
        if key in _SYSTEM:
            uom, kind, dq = _SYSTEM[key]
            phase = 0
            if key == "V":
                phase = voltage_phase or 0
            elif key == "A" or (phased_totals and key in _POWER_TOTALS):
                phase = current_phase or 0
            reading = Reading(uom=uom, value=value, phase=phase, kind=kind, data_qualifier=dq)
        elif match := _PER_LINE_KEY.match(key):
            line = int(match.group(2))
            if lines < 2 or line > lines:
                continue
            uom, kind, dq = _SYSTEM[match.group(1)]
            phase = _LINE_NEUTRAL_PHASE[line] if match.group(1) == "V" else _LINE_PHASE[line]
            reading = Reading(uom=uom, value=value, phase=phase, kind=kind, data_qualifier=dq)
        elif key in _LINE_TO_LINE:
            if key not in pairs:
                continue
            reading = Reading(uom=29, value=value, phase=_LINE_TO_LINE[key])
        elif key == "WHAvail":
            phase = (current_phase or 0) if phased_totals else 0
            reading = Reading(uom=72, value=value, phase=phase, kind=0)
        elif key in _ENERGY_FLOW:
            reading = Reading(
                uom=72,
                value=value,
                kind=12,
                accumulation_behaviour=9,
                flow_direction=_ENERGY_FLOW[key],
            )
        if reading is not None and is_valid_reading(reading):
            out.append(reading)
    return out


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

_INV_ST_SLEEPING = 1
_INV_ST_STARTING = 2
_INV_ST_RUNNING = 3
_INV_ST_THROTTLED = 4
_INV_ST_FAULT = 6
#: ``InvSt`` OFF .. STANDBY (0 .. 7) is InverterStatusType ``InvSt`` + 1.
_INV_ST_COUNT = 8
#: ``St`` -> OperationalModeStatusType.
_OPERATIONAL_MODE_STATUS: dict[int, int] = {0: 1, 1: 2}
_CONN_ST_CONNECTED = 1
_CONNECTED = 1 << 0
_ENERGIZED = 1 << 1  # ConnectStatusType2, 2030.5-2023
_AVAILABLE = 1 << 1  # ConnectStatusType, 2030.5-2018
_OPERATING = 1 << 2
_FAULT = 1 << 4
_DELIVERING = frozenset({_INV_ST_RUNNING, _INV_ST_THROTTLED})
_READY = frozenset({_INV_ST_SLEEPING, _INV_ST_STARTING, _INV_ST_RUNNING, _INV_ST_THROTTLED})
#: ``Alrm`` bit -> DERStatus ``alarmStatus`` bit. The other alarms have no
#: ``alarmStatus`` bit and are not carried.
_ALARM_BITS: dict[int, int] = {
    8: 3,  # over frequency
    9: 4,  # under frequency
    10: 1,  # AC over-voltage
    11: 2,  # AC under-voltage
}


def inverter_status(inv_st: Any) -> int | None:
    """Return the InverterStatusType for an ``InvSt`` value, or None if it is undefined."""
    code = _as_int(inv_st)
    return code + 1 if code is not None and 0 <= code < _INV_ST_COUNT else None


def operational_mode_status(st: Any) -> int | None:
    """Return the OperationalModeStatusType for an ``St`` value, or None if it is undefined."""
    code = _as_int(st)
    return None if code is None else _OPERATIONAL_MODE_STATUS.get(code)


def connect_status(conn_st: Any, inv_st: Any) -> int | None:
    """Return the 2030.5-2023 ConnectStatusType2 bitmap, or None without ``ConnSt``."""
    connected = _as_int(conn_st)
    if connected is None:
        return None
    bits = _CONNECTED if connected == _CONN_ST_CONNECTED else 0
    return bits | (_ENERGIZED if _as_int(inv_st) in _DELIVERING else 0)


def gen_connect_status(conn_st: Any, inv_st: Any) -> int | None:
    """Return the 2030.5-2018 ConnectStatusType bitmap, or None without ``ConnSt``."""
    connected = _as_int(conn_st)
    if connected is None:
        return None
    state = _as_int(inv_st)
    bits = _CONNECTED if connected == _CONN_ST_CONNECTED else 0
    bits |= _AVAILABLE if state in _READY else 0
    bits |= _OPERATING if state in _DELIVERING else 0
    bits |= _FAULT if state == _INV_ST_FAULT else 0
    return bits


def alarm_status(bits: Any) -> int:
    """Return the ``alarmStatus`` bitmap for an ``Alrm`` bitfield; 0 when it is unreadable."""
    value = _as_int(bits)
    if value is None:
        return 0
    result = 0
    for sunspec_bit, der_bit in _ALARM_BITS.items():
        if value & (1 << sunspec_bit):
            result |= 1 << der_bit
    return result


def status(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return DERStatus values for the model 701 status the core read.

    ``raw`` carries the points as read: ``inverterStatus`` holds ``InvSt``,
    ``connectStatus`` ``ConnSt``, ``operationalModeStatus`` ``St`` (each as
    ``{dateTime, value}``) and ``alarmStatus`` ``Alrm``. A field whose point is
    unimplemented or undefined is left out.
    """
    if not raw:
        return {}

    def point(key: str) -> tuple[Any, Any]:
        entry = raw.get(key)
        if not isinstance(entry, Mapping):
            return None, None
        return entry.get("value"), entry.get("dateTime")

    inv_st, stamp = point("inverterStatus")
    conn_st, conn_stamp = point("connectStatus")
    st, st_stamp = point("operationalModeStatus")
    result: dict[str, Any] = {
        key: raw[key] for key in ("manufacturerStatus", "readingTime") if key in raw
    }
    result["alarmStatus"] = alarm_status(raw.get("alarmStatus"))
    values = {
        "connectStatus": (connect_status(conn_st, inv_st), conn_stamp),
        "genConnectStatus": (gen_connect_status(conn_st, inv_st), conn_stamp),
        "inverterStatus": (inverter_status(inv_st), stamp),
        "operationalModeStatus": (operational_mode_status(st), st_stamp),
    }
    for key, (value, when) in values.items():
        if value is None:
            continue
        when = when if when is not None else raw.get("readingTime")
        result[key] = {"value": value} if when is None else {"dateTime": when, "value": value}
    return result


# ---------------------------------------------------------------------------
# Ratings and settings
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

_NAMEPLATE_NAMES: dict[str, str] = {
    "WMaxRtg": "rtgMaxW",
    "VAMaxRtg": "rtgMaxVA",
    "VarMaxInjRtg": "rtgMaxVar",
    "VarMaxAbsRtg": "rtgMaxVarNeg",
    "WOvrExtRtg": "rtgOverExcitedW",
    "WOvrExtRtgPF": "rtgOverExcitedPF",
    "WUndExtRtg": "rtgUnderExcitedW",
    "WUndExtRtgPF": "rtgUnderExcitedPF",
    "WChaRteMaxRtg": "rtgMaxChargeRateW",
    "VAChaRteMaxRtg": "rtgMaxChargeRateVA",
    "WDisChaRteMaxRtg": "rtgMaxDischargeRateW",
    "VNomRtg": "rtgVNom",
    "VMaxRtg": "rtgMaxV",
    "VMinRtg": "rtgMinV",
    "ReactSusceptRtg": "rtgReactiveSusceptance",
    "NorOpCatRtg": "rtgNormalCategory",
    "AbnOpCatRtg": "rtgAbnormalCategory",
    "CtrlModes": "modesSupported",
    "DoeModesSupported": "doeModesSupported",
}

_SETTING_NAMES: dict[str, str] = {
    "WMax": "setMaxW",
    "VAMax": "setMaxVA",
    "VarMaxInj": "setMaxVar",
    "VarMaxAbs": "setMaxVarNeg",
    "WOvrExtPF": "setMinPFOverExcited",
    "WUndExtPF": "setMinPFUnderExcited",
    "WChaRteMax": "setMaxChargeRateW",
    "VAChaRteMax": "setMaxChargeRateVA",
    "WDisChaRteMax": "setMaxDischargeRateW",
    "VNom": "setVNom",
    "VMax": "setMaxV",
    "VMin": "setMinV",
    "CtrlModes": "modesEnabled",
    "DoeModesEnabled": "doeModesEnabled",
}


#: Elements IEEE 2030.5 defines as negative values. The SunSpec points carry
#: the absorbed reactive power as a magnitude.
_NEGATIVE_ELEMENTS = frozenset({"rtgMaxVarNeg", "setMaxVarNeg"})


def _negative(value: Any) -> Any:
    """Return ``value`` as a negative number, keeping the multiplier of a dict."""
    if isinstance(value, Mapping):
        magnitude = value.get("value")
        if isinstance(magnitude, (int, float)) and not isinstance(magnitude, bool):
            return {**value, "value": -abs(magnitude)}
        return dict(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return -abs(value)
    return value


def _renamed(values: Mapping[str, Any], names: Mapping[str, str]) -> dict[str, Any]:
    """Return the values with an element name, renamed; drop the rest."""
    return {
        names[key]: _negative(value) if names[key] in _NEGATIVE_ELEMENTS else value
        for key, value in values.items()
        if key in names
    }


def nameplate(values: Mapping[str, Any], model_702: Any = None) -> dict[str, Any]:
    """Return DERCapability values for the model 702 ratings the core read.

    ``model_702`` is the read model, for the current rating the core does not
    report.
    """
    out = _renamed(values, _NAMEPLATE_NAMES)
    rating = _as_int(getattr(getattr(model_702, "AMaxRtg", None), "value", None))
    multiplier = _as_int(getattr(getattr(model_702, "A_SF", None), "value", None))
    if rating is not None and multiplier is not None:
        out["rtgMaxA"] = {"value": rating, "multiplier": multiplier}
    return out


def settings(values: Mapping[str, Any]) -> dict[str, Any]:
    """Return DERSettings values for the model 702 settings the core read."""
    return _renamed(values, _SETTING_NAMES)


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

#: The core method that applies each IEEE 2030.5 element.
CORE_METHOD: dict[str, str] = {
    "opModVoltVar": "update_qv",
    "opModVoltWatt": "update_pv",
    "opModWattVar": "update_qp",
    "opModMaxLimW": "update_p_lim",
    "opModMaxLimWInject": "update_p_lim_inj",
    "opModMaxLimWAbsorb": "update_p_lim_abs",
    "opModFreqDroop": "update_pf",
    "opModFixedVar": "update_const_q",
    "opModFixedPFInjectW": "update_const_pf",
    "opModFixedPFAbsorbW": "update_const_pf",
    "opModFixedW": "update_fixed_w",
    "opModTargetW": "update_target_w",
    "opModGenLimW": "update_gen_lim",
    "opModLoadLimW": "update_load_lim",
    "opModConnect": "update_connect",
    "opModEnergize": "update_connect",
}

#: The core parameters that turn each function off.
DISABLE: dict[str, dict[str, Any]] = {
    "update_qv": {"qv_mode_enable": 0},
    "update_pv": {"pv_mode_enable": 0},
    "update_qp": {"qp_mode_enable": 0},
    "update_p_lim": {"p_lim_mode_enable": 0},
    "update_p_lim_inj": {"p_lim_mode_enable": 0},
    "update_p_lim_abs": {"p_lim_mode_enable": 0},
    "update_pf": {"pf_mode_enable": 0},
    "update_const_q": {"const_q_mode_enable": 0},
    "update_fixed_w": {"WSetEna": 0},
    "update_target_w": {"mode_enable": 0},
    "update_gen_lim": {"gen_lim_mode_enable": 0},
    "update_load_lim": {"load_lim_mode_enable": 0},
}

_CURVE_PREFIX = {"update_qv": "qv", "update_pv": "pv", "update_qp": "qp"}


def _scaled(value: Any) -> float:
    """Return a ``{value, multiplier}`` quantity as a number."""
    if isinstance(value, Mapping):
        return float(value.get("value", 0)) * float(10 ** int(value.get("multiplier", 0) or 0))
    return float(value)


def _curve_points(curve: Mapping[str, Any]) -> tuple[list[float], list[float]]:
    x_mult = int(curve.get("xMultiplier", 0) or 0)
    y_mult = int(curve.get("yMultiplier", 0) or 0)
    points = curve.get("CurveData") or []
    return (
        [p["xvalue"] * (10**x_mult) for p in points],
        [p["yvalue"] * (10**y_mult) for p in points],
    )


def _ramps(curve: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ramp_dec_tms": curve.get("rampDecTms"),
        "ramp_inc_tms": curve.get("rampIncTms"),
        "ramp_pt1_tms": curve.get("rampPT1Tms"),
    }


def _open_loop(curve: Mapping[str, Any]) -> float | None:
    tms = curve.get("openLoopTms")
    return tms / 100 if tms is not None else None


def _power_factor(value: Mapping[str, Any]) -> dict[str, Any]:
    mult = int(value.get("multiplier", 0) or 0)
    return {
        "pf": round(value["displacement"] * (10**mult), 2),
        "excitation": value.get("excitation"),
        "mode": 1,
    }


def _core_params(method: str, value: Any) -> dict[str, Any]:
    """Return the core's parameters for an element's IEEE 2030.5 value."""
    if method in _CURVE_PREFIX:
        prefix = _CURVE_PREFIX[method]
        x, y = _curve_points(value)
        params: dict[str, Any] = {f"{prefix}_mode_enable": 1, f"{prefix}_olrt": _open_loop(value)}
        dept_ref = int(value.get("yRefType", 1)) - 1
        if method == "update_qv":
            vref = value.get("vRef")
            params |= {
                "qv_vref": vref / 100 if vref is not None else 0,
                "qv_pri": 1,
                "qv_vref_auto_ena": 1 if value.get("autonomousVRefEnable") is True else 0,
                "qv_vref_olrt": value.get("autonomousVRefTimeConstant"),
                "qv_deptref": dept_ref,
                "qv_curve_v_pts": x,
                "qv_curve_q_pts": y,
            }
        elif method == "update_pv":
            params |= {"pv_curve_v_pts": x, "pv_curve_p_pts": y, "pv_deptref": dept_ref}
        else:
            params |= {"qp_curve_p_pts": x, "qp_curve_q_pts": y, "qp_deptref": dept_ref}
        return params | _ramps(value)
    if method == "update_p_lim":
        return {"p_lim_mode_enable": 1, "p_lim_w": value / 100}
    if method in ("update_p_lim_inj", "update_p_lim_abs"):
        return {"p_lim_mode_enable": 1, "p_lim_watts": _scaled(value)}
    if method == "update_gen_lim":
        return {"gen_lim_mode_enable": 1, "gen_lim_w": _scaled(value)}
    if method == "update_load_lim":
        return {"load_lim_mode_enable": 1, "load_lim_w": _scaled(value)}
    if method == "update_pf":
        return {
            "pf_mode_enable": 1,
            "pf_dbof": value["dBOF"] / 1000,
            "pf_dbuf": value["dBUF"] / 1000,
            "pf_kof": value["kOF"] / 1000,
            "pf_kuf": value["kUF"] / 1000,
            "pf_olrt": value["openLoopTms"] / 100,
            "pf_pmin": value.get("pMin"),
        }
    if method == "update_const_q":
        return {
            "const_q_mode_enable": 1,
            "ref_type": int(value.get("refType", 1)),
            "const_q": None,
            "const_q_pct": value.get("value", 0) / 100,
        }
    if method == "update_fixed_w":
        return {"WSetEna": 1, "WSetMod": 0, "WSet": value / 100}
    if method == "update_target_w":
        return {"mode_enable": 1, "watts": _scaled(value)}
    raise KeyError(method)


class Controls:
    """Turn each IEEE 2030.5 control element into the core's write for one device.

    Stateful where one write carries more than one element: the two fixed
    power factors share one write, and the connect state is
    ``opModConnect`` AND ``opModEnergize``. The write carries the latest
    value of each part. A control's ``rampTms`` is not applied.
    """

    def __init__(self) -> None:
        self._pf: dict[str, dict[str, Any]] = {"inj": {"mode": 0}, "abs": {"mode": 0}}
        self._connect: dict[str, bool] = {"opModConnect": True, "opModEnergize": True}
        self._ramp_noted = False

    def translate(self, element: str, params: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """Return the core method and parameters for one element's call.

        Raises:
            KeyError: the core applies no write for ``element``.
        """
        method = CORE_METHOD[element]
        enabled = bool(params.get("enabled"))
        value = params.get("value")
        if method == "update_const_pf":
            side = "inj" if element == "opModFixedPFInjectW" else "abs"
            self._pf[side] = _power_factor(value) if enabled and value else {"mode": 0}
            return method, {"inj": dict(self._pf["inj"]), "abs": dict(self._pf["abs"])}
        if method == "update_connect":
            self._connect[element] = bool(value) if enabled else True
            return method, {"connected": all(self._connect.values())}
        if not enabled:
            return method, dict(DISABLE[method])
        if params.get("rampTms") is not None and not self._ramp_noted:
            self._ramp_noted = True
            logger.info(
                "The SunSpec connector does not apply ramp times; rampTms %s is ignored "
                "and the device uses its own ramp",
                params["rampTms"],
            )
        return method, _core_params(method, value)


__all__ = [
    "CORE_METHOD",
    "DISABLE",
    "Controls",
    "alarm_status",
    "connect_status",
    "gen_connect_status",
    "inverter_status",
    "line_to_line_phase",
    "nameplate",
    "operational_mode_status",
    "readings",
    "settings",
    "status",
    "total_phase",
]
