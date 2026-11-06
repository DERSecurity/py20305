"""Control dispatch table: DERControlBase elements to connector methods.

Each IEEE 2030.5 control element has one connector method, ``update_<element>``,
named after the element in snake case (``opModVoltVar`` ->
``update_op_mod_volt_var``). The method receives one parameter dict:

- ``enabled``: True when the control sets the element, False when the element
  is no longer commanded and the device should return to its own default for
  that function.
- ``value``: the element's value as IEEE 2030.5 defines it, present when
  ``enabled`` is True. Keys are the IEEE 2030.5 element names. A curve link is
  resolved to the DERCurve it references (``curveType``, ``xMultiplier``,
  ``yMultiplier``, ``yRefType``, ``CurveData`` and the rest). A PerCent or
  SignedPerCent is the integer in hundredths of a percent. A power is
  ``{"value": ..., "multiplier": ...}``. A boolean is a bool.
- ``rampTms``: the DERControlBase ``rampTms``, in hundredths of a second, when
  the control sets it.

The table covers every DERControlBase element, the CSIP-AUS dynamic operating
envelope limits and the DefaultDERControl settings.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from xsdata_pydantic.bindings import XmlContext

from py20305.models.csipaus.elements import doe_limits
from py20305.models.sep.sep import (
    DefaultDercontrol1,
    DercontrolBase,
    Dercurve1,
)

logger = logging.getLogger(__name__)

#: Value kinds, which say how an element's value is read.
CURVE = "curve"
VALUE = "value"
DOE_LIMIT = "doe_limit"


@dataclass(frozen=True)
class ControlElement:
    """One IEEE 2030.5 control element and the connector method that receives it."""

    #: The IEEE 2030.5 element name, for example ``opModVoltVar``.
    element: str
    #: The attribute on the parsed model, or "" for a CSIP-AUS limit.
    field: str
    #: How the value is read: ``CURVE``, ``VALUE`` or ``DOE_LIMIT``.
    kind: str = VALUE

    @property
    def method(self) -> str:
        """Return the connector method name, ``update_<element in snake case>``."""
        return f"update_{snake_case(self.element)}"

    @property
    def control(self) -> str:
        """Return the control name recorded for a write: the method without ``update_``."""
        return snake_case(self.element)


def snake_case(element: str) -> str:
    """Return an IEEE 2030.5 element name in snake case.

    A run of capitals is one word: ``opModHVRTMustTrip`` ->
    ``op_mod_hvrt_must_trip``, ``opModFixedPFInjectW`` ->
    ``op_mod_fixed_pf_inject_w``.
    """
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", element)
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", spaced)
    return spaced.lower()


#: Every DERControlBase element, in the order the schema lists them.
CONTROL_ELEMENTS: tuple[ControlElement, ...] = (
    ControlElement("opModConnect", "op_mod_connect"),
    ControlElement("opModEnergize", "op_mod_energize"),
    ControlElement("opModDeltaVar", "op_mod_delta_var"),
    ControlElement("opModDeltaW", "op_mod_delta_w"),
    ControlElement("opModFixedPFAbsorbW", "op_mod_fixed_pfabsorb_w"),
    ControlElement("opModFixedPFInjectW", "op_mod_fixed_pfinject_w"),
    ControlElement("opModFixedV", "op_mod_fixed_v"),
    ControlElement("opModFixedVar", "op_mod_fixed_var"),
    ControlElement("opModFixedW", "op_mod_fixed_w"),
    ControlElement("opModFreqDroop", "op_mod_freq_droop"),
    ControlElement("opModFreqWatt", "op_mod_freq_watt", CURVE),
    ControlElement("opModGridConnectPermit", "op_mod_grid_connect_permit"),
    ControlElement("opModHFRTMayTrip", "op_mod_hfrtmay_trip", CURVE),
    ControlElement("opModHFRTMustTrip", "op_mod_hfrtmust_trip", CURVE),
    ControlElement("opModHVRTMayTrip", "op_mod_hvrtmay_trip", CURVE),
    ControlElement("opModHVRTMomentaryCessation", "op_mod_hvrtmomentary_cessation", CURVE),
    ControlElement("opModHVRTMustTrip", "op_mod_hvrtmust_trip", CURVE),
    ControlElement("opModIslandPermit", "op_mod_island_permit"),
    ControlElement("opModLFRTMayTrip", "op_mod_lfrtmay_trip", CURVE),
    ControlElement("opModLFRTMustTrip", "op_mod_lfrtmust_trip", CURVE),
    ControlElement("opModLVRTMayTrip", "op_mod_lvrtmay_trip", CURVE),
    ControlElement("opModLVRTMomentaryCessation", "op_mod_lvrtmomentary_cessation", CURVE),
    ControlElement("opModLVRTMustTrip", "op_mod_lvrtmust_trip", CURVE),
    ControlElement("opModMaxLimPctVAAbsorb", "op_mod_max_lim_pct_vaabsorb"),
    ControlElement("opModMaxLimPctVAInject", "op_mod_max_lim_pct_vainject"),
    ControlElement("opModMaxLimPctVarAbsorb", "op_mod_max_lim_pct_var_absorb"),
    ControlElement("opModMaxLimPctVarInject", "op_mod_max_lim_pct_var_inject"),
    ControlElement("opModMaxLimPctWAbsorb", "op_mod_max_lim_pct_wabsorb"),
    ControlElement("opModMaxLimVarAbsorb", "op_mod_max_lim_var_absorb"),
    ControlElement("opModMaxLimVarInject", "op_mod_max_lim_var_inject"),
    ControlElement("opModMaxLimW", "op_mod_max_lim_w"),
    ControlElement("opModMaxLimWAbsorb", "op_mod_max_lim_wabsorb"),
    ControlElement("opModMaxLimWInject", "op_mod_max_lim_winject"),
    ControlElement("opModTargetV", "op_mod_target_v"),
    ControlElement("opModTargetVar", "op_mod_target_var"),
    ControlElement("opModTargetW", "op_mod_target_w"),
    ControlElement("opModVoltVar", "op_mod_volt_var", CURVE),
    ControlElement("opModVoltWatt", "op_mod_volt_watt", CURVE),
    ControlElement("opModWattPF", "op_mod_watt_pf", CURVE),
    ControlElement("opModWattVar", "op_mod_watt_var", CURVE),
)

#: CSIP-AUS dynamic operating envelope limits, carried as DERControlBase extensions.
DOE_ELEMENTS: tuple[ControlElement, ...] = (
    ControlElement("opModExpLimW", "", DOE_LIMIT),
    ControlElement("opModImpLimW", "", DOE_LIMIT),
    ControlElement("opModGenLimW", "", DOE_LIMIT),
    ControlElement("opModLoadLimW", "", DOE_LIMIT),
)

#: DefaultDERControl settings outside DERControlBase.
DEFAULT_CONTROL_ELEMENTS: tuple[ControlElement, ...] = (
    ControlElement("setESDelay", "set_esdelay"),
    ControlElement("setESHighFreq", "set_eshigh_freq"),
    ControlElement("setESHighVolt", "set_eshigh_volt"),
    ControlElement("setESLowFreq", "set_eslow_freq"),
    ControlElement("setESLowVolt", "set_eslow_volt"),
    ControlElement("setESRampTms", "set_esramp_tms"),
    ControlElement("setESRandomDelay", "set_esrandom_delay"),
    ControlElement("setGradW", "set_grad_w"),
    ControlElement("setSoftGradW", "set_soft_grad_w"),
)

#: Every element with a connector method.
ALL_ELEMENTS: tuple[ControlElement, ...] = (
    CONTROL_ELEMENTS + DOE_ELEMENTS + DEFAULT_CONTROL_ELEMENTS
)

#: The connector method for each element name.
METHOD_BY_ELEMENT: dict[str, str] = {e.element: e.method for e in ALL_ELEMENTS}

#: The element name for each tracked mode key: a DERControlBase field name or a
#: CSIP-AUS limit element name, as the event processor tracks modes.
ELEMENT_BY_MODE: dict[str, ControlElement] = {
    **{e.field: e for e in CONTROL_ELEMENTS},
    **{e.element: e for e in DOE_ELEMENTS},
}

#: The parameters that withdraw an element.
DISABLED: dict[str, Any] = {"enabled": False}


# ---------------------------------------------------------------------------
# Element values
# ---------------------------------------------------------------------------

_CONTEXT = XmlContext()

#: Codegen fields that carry no IEEE 2030.5 content.
_SKIPPED_FIELDS = frozenset({"other_element", "any_attributes", "disabled"})


_XML_NAMES: dict[type, tuple[tuple[str, str], ...]] = {}


def _xml_names(model_type: type) -> tuple[tuple[str, str], ...]:
    """Return ``(attribute, element name)`` for each content field of a model type."""
    names = _XML_NAMES.get(model_type)
    if names is None:
        meta = _CONTEXT.build(model_type)
        names = tuple(
            (var.name, var.local_name)
            for var in meta.get_all_vars()
            if var.name not in _SKIPPED_FIELDS and not var.name.endswith("_r2_3")
        )
        _XML_NAMES[model_type] = names
    return names


def element_value(obj: Any) -> Any:
    """Return a parsed IEEE 2030.5 value as plain data keyed by element name.

    A model whose only content is ``value`` (a PerCent, an enumeration, a
    multiplier) becomes that value. Bytes become upper-case hex. ``None``
    fields are left out.
    """
    if obj is None or isinstance(obj, bool | int | float | str):
        return obj
    if isinstance(obj, bytes):
        return obj.hex().upper()
    if isinstance(obj, list | tuple):
        return [element_value(item) for item in obj]
    if not hasattr(type(obj), "model_fields"):
        return obj
    out: dict[str, Any] = {}
    for attribute, name in _xml_names(type(obj)):
        value = getattr(obj, attribute, None)
        if value is None or value == []:
            continue
        out[name] = element_value(value)
    if set(out) == {"value"}:
        return out["value"]
    return out


def _find_curve(href: str, curves: list[Dercurve1]) -> Dercurve1 | None:
    """Return the DERCurve with ``href`` from ``curves``, or None."""
    for curve in curves:
        if getattr(curve, "href", None) == href:
            return curve
    return None


def _is_disabled(field_value: Any) -> bool:
    """Return True when an element carries IEEE 2030.5-2023's ``disabled="true"``."""
    return field_value is not None and getattr(field_value, "disabled", False) is True


def _element_params(
    element: ControlElement, base: DercontrolBase, curves: list[Dercurve1]
) -> dict[str, Any] | None:
    """Return the parameters for one element of ``base``, or None when it is absent."""
    if element.kind == DOE_LIMIT:
        pair = doe_limits(base).get(element.element)
        if pair is None:
            return None
        value, multiplier = pair
        return {"enabled": True, "value": {"value": value, "multiplier": multiplier}}

    field_value = getattr(base, element.field, None)
    if field_value is None:
        return None
    if _is_disabled(field_value):
        # IEEE 2030.5-2023: a disabled mode "follows the rules and guidelines as
        # if the DERControl Mode were not disabled". It takes part in event
        # precedence and withdraws the mode.
        return dict(DISABLED)
    if element.kind == CURVE:
        curve = _find_curve(field_value.href, curves)
        if curve is None:
            # The control names a curve the client does not hold. Applying
            # nothing would leave the device on whatever curve it had, so
            # the mode is withdrawn instead.
            logger.warning("No DERCurve at %s for %s", field_value.href, element.element)
            return dict(DISABLED)
        return {"enabled": True, "value": element_value(curve)}
    return {"enabled": True, "value": element_value(field_value)}


def translate_controls(
    base: DercontrolBase,
    curves: list[Dercurve1],
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(method, params)`` for every element ``base`` sets.

    An absent element is skipped. An element marked ``disabled="true"`` is
    withdrawn (``{"enabled": False}``), as is a curve element whose curve the
    client does not hold. ``rampTms``, when set, is added to every call that
    applies an element.
    """
    results: list[tuple[str, dict[str, Any]]] = []
    for element in CONTROL_ELEMENTS + DOE_ELEMENTS:
        params = _element_params(element, base, curves)
        if params is None:
            continue
        if base.ramp_tms is not None and params.get("enabled"):
            params["rampTms"] = base.ramp_tms
        results.append((element.method, params))
    return results


def translate_default_controls(
    dderc: DefaultDercontrol1,
    curves: list[Dercurve1],
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(method, params)`` for a DefaultDERControl.

    The DERControlBase elements as :func:`translate_controls` returns them,
    then each DefaultDERControl setting it carries (``setGradW``,
    ``setSoftGradW`` and the enter-service settings).
    """
    results = translate_controls(dderc.dercontrol_base, curves)
    for element in DEFAULT_CONTROL_ELEMENTS:
        value = getattr(dderc, element.field, None)
        if value is not None:
            results.append((element.method, {"enabled": True, "value": element_value(value)}))
    return results


__all__ = [
    "ALL_ELEMENTS",
    "CONTROL_ELEMENTS",
    "DEFAULT_CONTROL_ELEMENTS",
    "DISABLED",
    "DOE_ELEMENTS",
    "ELEMENT_BY_MODE",
    "METHOD_BY_ELEMENT",
    "ControlElement",
    "element_value",
    "snake_case",
    "translate_controls",
    "translate_default_controls",
]
