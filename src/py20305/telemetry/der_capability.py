"""DERCapability model construction for IEEE 2030.5 nameplate rating reporting.

Builds Pydantic ``Dercapability`` models from connector nameplate data,
suitable for serialization via ``to_xml()`` and PUT to the server.
"""

from __future__ import annotations

from typing import Any

from py20305.models.csipaus.csipaus_ext import DoeModesSupported
from py20305.models.sep.sep import (
    Dercapability,
    DercontrolType,
    Dertype,
)
from py20305.telemetry.scale_factor import (
    to_active_power,
    to_amp_hour,
    to_apparent_power,
    to_power_factor,
    to_reactive_power,
    to_reactive_susceptance,
    to_voltage_rms,
    to_watt_hour,
)

# CSIP-AUS DOE control bitmap default: all four DOE limits -- opModExpLimW (bit
# 0) | opModImpLimW (bit 1) | opModGenLimW (bit 2) | opModLoadLimW (bit 3) =
# 0x0F. Used when the connector doesn't report which DOE modes it supports.
_DEFAULT_DOE_MODES_SUPPORTED = 0x0F


def build_der_capability(
    nameplate: dict[str, Any], *, der_type: int = 83, csip_aus_mode: bool = False
) -> Dercapability:
    """Build a ``Dercapability`` model from connector nameplate data.

    Args:
        nameplate: Dict from connector.fetch_nameplate() with keys like
            WMaxRtg, VAMaxRtg, VarMaxInjRtg, CtrlModes, etc.
        der_type: IEEE 2030.5 DER type code (default 83 = combined PV+storage).
        csip_aus_mode: When True, attach the CSIP-AUS ``doeModesSupported``
            extension (required by CSIP-AUS servers); plain 2030.5 servers omit
            it.

    Returns:
        Pydantic model ready for ``to_xml()`` serialization.

    Raises:
        ValueError: If WMaxRtg is missing (required by IEEE 2030.5 schema).
    """
    # Required field: rtgMaxW
    rtg_max_w = to_active_power(nameplate.get("WMaxRtg"))
    if rtg_max_w is None:
        msg = "WMaxRtg is required for DERCapability but was missing or None"
        raise ValueError(msg)

    # modesSupported: required, default to zero bitmap if absent
    ctrl_modes = nameplate.get("CtrlModes")
    if ctrl_modes is not None and isinstance(ctrl_modes, int):
        modes_supported = DercontrolType(value=ctrl_modes.to_bytes(4, "big"))
    else:
        modes_supported = DercontrolType(value=b"\x00\x00\x00\x00")

    # Category fields (plain int passthrough)
    nor_op_cat = nameplate.get("NorOpCatRtg")
    abn_op_cat = nameplate.get("AbnOpCatRtg")

    # CSIP-AUS extension: doeModesSupported (the DOE control bitmap, 4 bits).
    # Connector-provided via "DoeModesSupported"; defaults to all four DOE limits
    # when absent. Rides in the DERCapability wildcard slot and is only attached
    # in CSIP-AUS mode so plain 2030.5 servers are unaffected.
    other_element: list[object] = []
    if csip_aus_mode:
        doe = nameplate.get("DoeModesSupported")
        # Reject bool (a subclass of int) and mask to the 4 defined bits so
        # reserved bits are never advertised and a bad/negative connector value
        # can't overflow to_bytes and crash the capability cycle.
        doe_bits = (
            doe
            if isinstance(doe, int) and not isinstance(doe, bool)
            else _DEFAULT_DOE_MODES_SUPPORTED
        )
        other_element.append(DoeModesSupported(value=(doe_bits & 0x0F).to_bytes(1, "big")))

    return Dercapability(
        modes_supported=modes_supported,
        other_element=other_element,
        rtg_abnormal_category=abn_op_cat if isinstance(abn_op_cat, int) else None,
        rtg_max_ah=to_amp_hour(nameplate.get("AhMaxRtg")),
        rtg_max_charge_rate_va=to_apparent_power(nameplate.get("VAChaRteMaxRtg")),
        rtg_max_charge_rate_w=to_active_power(nameplate.get("WChaRteMaxRtg")),
        rtg_max_discharge_rate_va=to_apparent_power(nameplate.get("VADisChaRteMaxRtg")),
        rtg_max_discharge_rate_w=to_active_power(nameplate.get("WDisChaRteMaxRtg")),
        rtg_max_v=to_voltage_rms(nameplate.get("VMaxRtg")),
        rtg_max_va=to_apparent_power(nameplate.get("VAMaxRtg")),
        rtg_max_var=to_reactive_power(nameplate.get("VarMaxInjRtg")),
        rtg_max_var_neg=to_reactive_power(nameplate.get("VarMaxAbsRtg")),
        rtg_max_w=rtg_max_w,
        rtg_max_wh=to_watt_hour(nameplate.get("WhMaxRtg")),
        rtg_min_v=to_voltage_rms(nameplate.get("VMinRtg")),
        rtg_normal_category=nor_op_cat if isinstance(nor_op_cat, int) else None,
        rtg_over_excited_pf=to_power_factor(nameplate.get("WOvrExtRtgPF")),
        rtg_over_excited_w=to_active_power(nameplate.get("WOvrExtRtg")),
        rtg_reactive_susceptance=to_reactive_susceptance(nameplate.get("ReactSusceptRtg")),
        rtg_under_excited_pf=to_power_factor(nameplate.get("WUndExtRtgPF")),
        rtg_under_excited_w=to_active_power(nameplate.get("WUndExtRtg")),
        rtg_vnom=to_voltage_rms(nameplate.get("VNomRtg")),
        type_value=Dertype(value=der_type),
    )
