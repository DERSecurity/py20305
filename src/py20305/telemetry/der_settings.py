"""DERSettings model construction for IEEE 2030.5 configuration reporting.

Builds Pydantic ``Dersettings`` models from connector configuration data,
suitable for serialization via ``to_xml()`` and PUT to the server.
"""

from __future__ import annotations

import time
from typing import Any

from py20305.models.csipaus.csipaus_ext import DoeModesEnabled
from py20305.models.sep.sep import (
    DercontrolType2,
    Dersettings,
    TimeType,
)
from py20305.server_profile import ServerProfile
from py20305.telemetry.der_capability import control_type_from
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

#: DERControlType2 defines bits 0 to 9; the rest are reserved.
_ALL_MODES2 = 0x3FF


def build_der_settings(
    configuration: dict[str, Any],
    updated_time: int | None = None,
    *,
    csip_aus_mode: bool = False,
    modes_supported2: int | None = None,
    profile: ServerProfile | None = None,
) -> Dersettings:
    """Build a ``Dersettings`` model from connector configuration data.

    Args:
        configuration: Dict from connector.fetch_configuration(), keyed by
            DERSettings element name (setMaxW, setMaxVA, modesEnabled, ...).
        updated_time: Epoch seconds for updatedTime field. Defaults to now.
        csip_aus_mode: When True, attach the CSIP-AUS ``doeModesEnabled``
            extension (required by CSIP-AUS servers); plain 2030.5 servers omit
            it.
        modes_supported2: The DERControlType2 bits the connector supports.
            ``modesEnabled2`` is limited to them, since a mode the device cannot
            apply is not enabled on it.
        profile: The server's schema profile. It limits ``modesEnabled2`` to the
            bits the profile permits.

    Returns:
        Pydantic model ready for ``to_xml()`` serialization.

    Raises:
        ValueError: If setMaxW is missing (required by IEEE 2030.5 schema).
    """
    # Required field: setMaxW
    set_max_w = to_active_power(configuration.get("setMaxW"))
    if set_max_w is None:
        msg = "setMaxW is required for DERSettings but was missing or None"
        raise ValueError(msg)

    # modesEnabled: optional bitmap
    modes_enabled = control_type_from("modesEnabled", configuration.get("modesEnabled"))

    # modesEnabled2: the additional modes (2023) the connector reports enabled.
    # Only the connector can say which are enabled, so nothing is sent without
    # its value; it is limited to what the device supports and the server
    # profile permits. A reported 0 is sent when some mode is available, so the
    # server learns that modes it saw enabled are now disabled. It is left out
    # only when no additional mode is available.
    modes_enabled2 = None
    enabled2 = configuration.get("modesEnabled2")
    if isinstance(enabled2, int) and not isinstance(enabled2, bool):
        available = _ALL_MODES2 if modes_supported2 is None else modes_supported2 & _ALL_MODES2
        if profile is not None:
            available = profile.mask_modes2(available)
        if available:
            modes_enabled2 = DercontrolType2(value=(enabled2 & available).to_bytes(4, "big"))

    # setGradW: required UInt16, plain integer (not a scale factor type)
    grad_w = configuration.get("setGradW", 0)
    set_grad_w = int(grad_w) if isinstance(grad_w, (int, float)) else 0

    # updatedTime: required
    ut = updated_time if updated_time is not None else int(time.time())

    # CSIP-AUS extension: doeModesEnabled (the enabled DOE control bitmap, 4
    # bits). Connector-provided via "doeModesEnabled"; defaults to export +
    # import active-power limits when absent. Rides in the DERSettings wildcard
    # slot, only in CSIP-AUS mode.
    other_element: list[object] = []
    if csip_aus_mode:
        doe = configuration.get("doeModesEnabled")
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
        modes_enabled2=modes_enabled2,
        other_element=other_element,
        set_grad_w=set_grad_w,
        set_max_ah=to_amp_hour(configuration.get("setMaxAh")),
        set_max_charge_rate_va=to_apparent_power(configuration.get("setMaxChargeRateVA")),
        set_max_charge_rate_w=to_active_power(configuration.get("setMaxChargeRateW")),
        set_max_discharge_rate_va=to_apparent_power(configuration.get("setMaxDischargeRateVA")),
        set_max_discharge_rate_w=to_active_power(configuration.get("setMaxDischargeRateW")),
        set_max_v=to_voltage_rms(configuration.get("setMaxV")),
        set_max_va=to_apparent_power(configuration.get("setMaxVA")),
        set_max_var=to_reactive_power(configuration.get("setMaxVar")),
        set_max_var_neg=to_reactive_power(configuration.get("setMaxVarNeg")),
        set_max_w=set_max_w,
        set_max_wh=to_watt_hour(configuration.get("setMaxWh")),
        set_min_pfover_excited=to_power_factor(configuration.get("setMinPFOverExcited")),
        set_min_pfunder_excited=to_power_factor(configuration.get("setMinPFUnderExcited")),
        set_min_v=to_voltage_rms(configuration.get("setMinV")),
        set_vnom=to_voltage_rms(configuration.get("setVNom")),
        updated_time=TimeType(value=ut),
    )
