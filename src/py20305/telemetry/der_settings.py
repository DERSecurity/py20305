"""DERSettings model construction for IEEE 2030.5 configuration reporting.

Builds Pydantic ``Dersettings`` models from connector configuration data,
suitable for serialization via ``to_xml()`` and PUT to the server.
"""

from __future__ import annotations

import time
from typing import Any

from py20305.models.csipaus.csipaus_ext import DoeModesEnabled
from py20305.models.sep.sep import (
    DercontrolType,
    Dersettings,
    TimeType,
)
from py20305.telemetry.scale_factor import (
    to_active_power,
    to_amp_hour,
    to_apparent_power,
    to_power_factor,
    to_reactive_power,
    to_voltage_rms,
    to_watt_hour,
)

# CSIP-AUS DOE control bitmap default: opModExpLimW (bit 0, export limit) |
# opModImpLimW (bit 1, import limit) = 0x03. Used when the connector doesn't
# report which DOE modes are enabled.
_DEFAULT_DOE_MODES_ENABLED = 0x03


def build_der_settings(
    configuration: dict[str, Any],
    updated_time: int | None = None,
    *,
    csip_aus_mode: bool = False,
) -> Dersettings:
    """Build a ``Dersettings`` model from connector configuration data.

    Args:
        configuration: Dict from connector.fetch_configuration() with keys
            like WMax, VAMax, VarMaxInj, CtrlModes, GradW, etc.
        updated_time: Epoch seconds for updatedTime field. Defaults to now.
        csip_aus_mode: When True, attach the CSIP-AUS ``doeModesEnabled``
            extension (required by CSIP-AUS servers); plain 2030.5 servers omit
            it.

    Returns:
        Pydantic model ready for ``to_xml()`` serialization.

    Raises:
        ValueError: If WMax is missing (required by IEEE 2030.5 schema).

    Note:
        WMaxOvrExt and WMaxUndExt are deliberately omitted -- these keys
        do not exist in the IEEE 2030.5 DERSettings XSD (sep2_schema_2023.xsd
        lines 4211-4370) and were copy-paste errors in the reference
        implementation.
    """
    # Required field: setMaxW
    set_max_w = to_active_power(configuration.get("WMax"))
    if set_max_w is None:
        msg = "WMax is required for DERSettings but was missing or None"
        raise ValueError(msg)

    # modesEnabled: optional bitmap
    ctrl_modes = configuration.get("CtrlModes")
    modes_enabled = None
    if ctrl_modes is not None and isinstance(ctrl_modes, int):
        modes_enabled = DercontrolType(value=ctrl_modes.to_bytes(4, "big"))

    # setGradW: required UInt16, plain integer (not a scale factor type)
    grad_w = configuration.get("GradW", 0)
    set_grad_w = int(grad_w) if isinstance(grad_w, (int, float)) else 0

    # updatedTime: required
    ut = updated_time if updated_time is not None else int(time.time())

    # CSIP-AUS extension: doeModesEnabled (the enabled DOE control bitmap, 4
    # bits). Connector-provided via "DoeModesEnabled"; defaults to export +
    # import active-power limits when absent. Rides in the DERSettings wildcard
    # slot, only in CSIP-AUS mode.
    other_element: list[object] = []
    if csip_aus_mode:
        doe = configuration.get("DoeModesEnabled")
        # Reject bool (a subclass of int) and mask to the 4 defined bits so
        # reserved bits are never set and a bad/negative connector value can't
        # overflow to_bytes and crash the settings cycle.
        doe_bits = (
            doe
            if isinstance(doe, int) and not isinstance(doe, bool)
            else _DEFAULT_DOE_MODES_ENABLED
        )
        other_element.append(DoeModesEnabled(value=(doe_bits & 0x0F).to_bytes(1, "big")))

    return Dersettings(
        modes_enabled=modes_enabled,
        other_element=other_element,
        set_grad_w=set_grad_w,
        set_max_ah=to_amp_hour(configuration.get("AhMax")),
        set_max_charge_rate_va=to_apparent_power(configuration.get("VAChaRteMax")),
        set_max_charge_rate_w=to_active_power(configuration.get("WChaRteMax")),
        set_max_discharge_rate_va=to_apparent_power(configuration.get("VADisChaRteMax")),
        set_max_discharge_rate_w=to_active_power(configuration.get("WDisChaRteMax")),
        set_max_v=to_voltage_rms(configuration.get("VMax")),
        set_max_va=to_apparent_power(configuration.get("VAMax")),
        set_max_var=to_reactive_power(configuration.get("VarMaxInj")),
        set_max_var_neg=to_reactive_power(configuration.get("VarMaxAbs")),
        set_max_w=set_max_w,
        set_max_wh=to_watt_hour(configuration.get("WhMax")),
        set_min_pfover_excited=to_power_factor(configuration.get("WOvrExtPF")),
        set_min_pfunder_excited=to_power_factor(configuration.get("WUndExtPF")),
        set_min_v=to_voltage_rms(configuration.get("VMin")),
        set_vnom=to_voltage_rms(configuration.get("VNom")),
        updated_time=TimeType(value=ut),
    )
