"""Build DERControlType bitmask from active DercontrolBase fields.

IEEE 2030.5 Table 31 defines bit positions for modesResponded in DERControlResponse.
This module maps DercontrolBase field names to their corresponding bit positions.
"""

from __future__ import annotations

from py20305.models.csipaus.elements import DOE_LIMIT_NAMES, doe_limits
from py20305.models.sep.sep import DercontrolBase, DercontrolType

# Mapping from DercontrolBase field names to DERControlType bit positions.
# Per IEEE 2030.5-2023 Section 10.10, Table 27.
_FIELD_TO_BIT: dict[str, int] = {
    "op_mod_connect": 2,
    "op_mod_energize": 3,
    "op_mod_fixed_pfabsorb_w": 4,
    "op_mod_fixed_pfinject_w": 5,
    "op_mod_fixed_var": 6,
    "op_mod_fixed_w": 7,
    "op_mod_freq_droop": 8,
    "op_mod_freq_watt": 9,
    "op_mod_hfrtmay_trip": 10,
    "op_mod_hfrtmust_trip": 11,
    "op_mod_hvrtmay_trip": 12,
    "op_mod_hvrtmomentary_cessation": 13,
    "op_mod_hvrtmust_trip": 14,
    "op_mod_lfrtmay_trip": 15,
    "op_mod_lfrtmust_trip": 16,
    "op_mod_lvrtmay_trip": 17,
    "op_mod_lvrtmomentary_cessation": 18,
    "op_mod_lvrtmust_trip": 19,
    "op_mod_max_lim_w": 20,
    "op_mod_target_var": 21,
    "op_mod_target_w": 22,
    "op_mod_volt_var": 23,
    "op_mod_volt_watt": 24,
    "op_mod_watt_pf": 25,
    "op_mod_watt_var": 26,
    "op_mod_delta_var": 27,
    "op_mod_delta_w": 28,
    "op_mod_fixed_v": 29,
    "op_mod_grid_connect_permit": 30,
    "op_mod_island_permit": 31,
}


def build_modes_responded(base: DercontrolBase) -> DercontrolType:
    """Build a DERControlType bitmask from active fields in a DercontrolBase.

    A field is "active" if it is not None. The returned bitmask indicates
    which DER control modes were responded to.
    """
    bitmask = 0
    for field_name, bit_pos in _FIELD_TO_BIT.items():
        val = getattr(base, field_name, None)
        if val is not None:
            bitmask |= 1 << bit_pos

    return DercontrolType(value=bitmask.to_bytes(4, byteorder="big"))


#: CSIP-AUS envelope limits. They are extension elements, carried in
#: ``DercontrolBase.other_element`` rather than as fields, so they are tracked
#: by their element names, whether they parsed as typed models or not.
CSIPAUS_LIMIT_NAMES: frozenset[str] = DOE_LIMIT_NAMES


def _extension_mode_names(base: DercontrolBase) -> list[str]:
    return list(doe_limits(base))


def get_active_mode_names(base: DercontrolBase) -> frozenset[str]:
    """Return the set of active mode names from a DercontrolBase.

    Field names for the base modes (``op_mod_fixed_w``) and element names for
    the CSIP-AUS limits (``opModExpLimW``).
    """
    names: list[str] = []
    for field_name in _FIELD_TO_BIT:
        val = getattr(base, field_name, None)
        if val is not None:
            names.append(field_name)
    names.extend(_extension_mode_names(base))
    return frozenset(names)
