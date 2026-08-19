"""Scale factor conversion for IEEE 2030.5 DER resources.

Converts floating-point values from connectors into integer + multiplier
pairs required by IEEE 2030.5 scale factor fields (e.g., rtgMaxW has
value + multiplier sub-elements).

Ported from reference: csip_utils.py:16-127.
"""

from __future__ import annotations

import logging
from typing import Any

from py20305.models.sep.sep import (
    ActivePower,
    AmpereHour,
    ApparentPower,
    PowerFactor,
    PowerOfTenMultiplierType,
    ReactivePower,
    ReactiveSusceptance,
    VoltageRms,
    WattHour,
)

logger = logging.getLogger(__name__)


def float_to_int(value: float) -> tuple[int, int]:
    """Convert a float to an integer with a power-of-10 multiplier.

    The multiplier is the negative count of decimal places needed.
    For example: 3.14 -> (314, -2), 100.0 -> (100, 0).

    Args:
        value: Numeric value to convert.

    Returns:
        Tuple of (integer_value, multiplier) where
        integer_value * 10^multiplier == value (approximately).
    """
    # Round to 9 decimal places to avoid floating-point noise
    value = round(value, 9)

    str_val = str(value)
    if "." in str_val:
        # Count decimal places
        decimal_part = str_val.split(".")[1]
        # Strip trailing zeros
        decimal_part = decimal_part.rstrip("0")
        if decimal_part:
            decimal_places = len(decimal_part)
            multiplier = -decimal_places
            integer_value = round(value * (10**decimal_places))
            return _clamp_int16(integer_value, multiplier)

    return _clamp_int16(int(value), 0)


def _clamp_int16(int_value: int, multiplier: int) -> tuple[int, int]:
    """Scale up the multiplier until int_value fits in Int16 (-32768..32767).

    IEEE 2030.5 value fields are xs:short. Large values (e.g. 500 kW
    nameplate ratings) overflow unless we trade precision for range.
    """
    while int_value > 32767 or int_value < -32768:
        int_value = round(int_value / 10)
        multiplier += 1
    return int_value, multiplier


def get_sf(
    value: Any,
    value_name: str = "value",
) -> dict[str, int] | None:
    """Convert a connector value to a scale factor dict for IEEE 2030.5.

    Handles several input types:
    - None/str/list: returns None (field omitted from XML)
    - int/float: converts via float_to_int()
    - dict: validates and passes through with float-to-int conversion

    Args:
        value: Raw value from connector (float, int, dict, or None).
        value_name: Key name for the value in the returned dict.
            Use "displacement" for power factor fields.

    Returns:
        Dict with {value_name: int, "multiplier": int} or None if
        the value should be omitted.
    """
    if value is None or isinstance(value, (str, list)):
        return None

    if isinstance(value, dict):
        return _convert_dict_sf(value, value_name)

    if isinstance(value, (int, float)):
        int_val, multiplier = float_to_int(float(value))
        return {value_name: int_val, "multiplier": multiplier}

    return None


def _convert_dict_sf(
    sf_data: dict[str, Any],
    value_name: str,
) -> dict[str, int] | None:
    """Validate and convert a pre-formatted scale factor dict.

    If the dict already has value + multiplier keys, convert any float
    values to integers and return. Otherwise return None.
    """
    # Check for required keys (using common key names)
    val = sf_data.get("value") if "value" in sf_data else sf_data.get(value_name)
    mult = sf_data.get("multiplier")

    if val is None or mult is None:
        return None

    if not isinstance(mult, int):
        return None

    # PowerOfTenMultiplierType is xs:byte (-128..127).  Values outside
    # this range indicate garbage data (e.g. misaligned SunSpec registers).
    if mult < -128 or mult > 127:
        logger.warning("Multiplier %d out of range (-128..127), omitting field", mult)
        return None

    if isinstance(val, float):
        val, mult = float_to_int(val)

    if not isinstance(val, int):
        return None

    # Ensure value fits in xs:short (-32768..32767) per IEEE 2030.5.
    # Connectors may pass raw register values that overflow Int16.
    val, mult = _clamp_int16(val, mult)

    return {value_name: val, "multiplier": mult}


# ---------------------------------------------------------------------------
# Pydantic model factories
# ---------------------------------------------------------------------------


def _multiplier(exp: int) -> PowerOfTenMultiplierType:
    return PowerOfTenMultiplierType(value=exp)


def to_active_power(value: Any) -> ActivePower | None:
    """Convert a connector value to an ``ActivePower`` model, or *None*."""
    sf = get_sf(value)
    if sf is None:
        return None
    return ActivePower(multiplier=_multiplier(sf["multiplier"]), value=sf["value"])


def to_apparent_power(value: Any) -> ApparentPower | None:
    """Convert a connector value to an ``ApparentPower`` model, or *None*."""
    sf = get_sf(value)
    if sf is None:
        return None
    return ApparentPower(multiplier=_multiplier(sf["multiplier"]), value=sf["value"])


def to_watt_hour(value: Any) -> WattHour | None:
    """Convert a connector value to a ``WattHour`` model, or *None*."""
    sf = get_sf(value)
    if sf is None:
        return None
    return WattHour(multiplier=_multiplier(sf["multiplier"]), value=sf["value"])


def to_amp_hour(value: Any) -> AmpereHour | None:
    """Convert a connector value to an ``AmpereHour`` model, or *None*."""
    sf = get_sf(value)
    if sf is None:
        return None
    return AmpereHour(multiplier=_multiplier(sf["multiplier"]), value=sf["value"])


def to_reactive_power(value: Any) -> ReactivePower | None:
    """Convert a connector value to a ``ReactivePower`` model, or *None*."""
    sf = get_sf(value)
    if sf is None:
        return None
    return ReactivePower(multiplier=_multiplier(sf["multiplier"]), value=sf["value"])


def to_reactive_susceptance(value: Any) -> ReactiveSusceptance | None:
    """Convert a connector value to a ``ReactiveSusceptance`` model, or *None*."""
    sf = get_sf(value)
    if sf is None:
        return None
    return ReactiveSusceptance(multiplier=_multiplier(sf["multiplier"]), value=sf["value"])


def to_voltage_rms(value: Any) -> VoltageRms | None:
    """Convert a connector value to a ``VoltageRms`` model, or *None*."""
    sf = get_sf(value)
    if sf is None:
        return None
    return VoltageRms(multiplier=_multiplier(sf["multiplier"]), value=sf["value"])


def to_power_factor(value: Any) -> PowerFactor | None:
    """Convert a connector value to a ``PowerFactor`` model, or *None*."""
    sf = get_sf(value, "displacement")
    if sf is None:
        return None
    return PowerFactor(displacement=sf["displacement"], multiplier=_multiplier(sf["multiplier"]))
