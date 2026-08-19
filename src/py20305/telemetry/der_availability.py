"""DERAvailability model construction for IEEE 2030.5 reserve status reporting.

Builds Pydantic ``Deravailability`` models from connector availability data,
suitable for serialization via ``to_xml()`` and PUT to the server.
"""

from __future__ import annotations

import time
from typing import Any

from py20305.models.sep.sep import (
    ActivePower,
    Deravailability,
    PerCent,
    PowerOfTenMultiplierType,
    ReactivePower,
    TimeType,
)


def build_der_availability(
    availability_data: dict[str, Any], *, default_time: int | None = None
) -> Deravailability:
    """Build a ``Deravailability`` model from connector data.

    Args:
        availability_data: Dict from connector.fetch_availability() with keys:
            - availabilityDuration: int (seconds, optional)
            - maxChargeDuration: int (seconds, optional)
            - readingTime: int (epoch seconds, defaults to now)
            - reserveChargePercent: int (percent * 100, optional)
            - reservePercent: int (percent * 100, optional)
            - statVarAvail: dict with value, multiplier (optional)
            - statWAvail: dict with value, multiplier (optional)

    Returns:
        Pydantic model ready for ``to_xml()`` serialization.
    """
    # Connector-supplied readingTime wins (explicit None check so an epoch of
    # 0 is preserved, matching build_der_status); otherwise the caller's
    # server-timebase default, falling back to the local clock.
    reading_time = availability_data.get("readingTime")
    if reading_time is None:
        reading_time = default_time if default_time is not None else int(time.time())

    reserve_charge = availability_data.get("reserveChargePercent")
    reserve_pct = availability_data.get("reservePercent")

    return Deravailability(
        availability_duration=availability_data.get("availabilityDuration"),
        max_charge_duration=availability_data.get("maxChargeDuration"),
        reading_time=TimeType(value=reading_time),
        reserve_charge_percent=PerCent(value=reserve_charge)
        if reserve_charge is not None
        else None,
        reserve_percent=PerCent(value=reserve_pct) if reserve_pct is not None else None,
        stat_var_avail=_to_reactive_power(availability_data.get("statVarAvail")),
        stat_wavail=_to_active_power(availability_data.get("statWAvail")),
    )


def _to_active_power(data: dict[str, Any] | None) -> ActivePower | None:
    if data is None:
        return None
    return ActivePower(
        multiplier=PowerOfTenMultiplierType(value=data.get("multiplier", 0)),
        value=data.get("value", 0),
    )


def _to_reactive_power(data: dict[str, Any] | None) -> ReactivePower | None:
    if data is None:
        return None
    return ReactivePower(
        multiplier=PowerOfTenMultiplierType(value=data.get("multiplier", 0)),
        value=data.get("value", 0),
    )
