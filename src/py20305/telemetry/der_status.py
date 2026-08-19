"""DERStatus model construction for IEEE 2030.5 inverter status reporting.

Builds Pydantic ``Derstatus`` models from connector status data,
suitable for serialization via ``to_xml()`` and PUT to the server.
"""

from __future__ import annotations

import time
from typing import Any

from py20305.models.sep.sep import (
    ConnectStatusType,
    ConnectStatusType2,
    Derstatus,
    InverterStatusType,
    LocalControlModeStatusType,
    ManufacturerStatusType,
    OperationalModeStatusType,
    PerCent,
    StateOfChargeStatusType,
    StorageModeStatusType,
    TimeType,
)


def build_der_status(
    status_data: dict[str, Any],
    *,
    server_2018_compat: bool = False,
    always_send_alarm_status: bool = False,
    default_time: int | None = None,
) -> Derstatus:
    """Build a ``Derstatus`` model from connector data.

    Args:
        status_data: Dict from connector.fetch_status() with keys:
            - readingTime: int (epoch seconds, defaults to now)
            - alarmStatus: int (32-bit bitmap, optional; 0 omitted unless
              ``always_send_alarm_status`` is set)
            - connectStatus: {dateTime, value} (preferred, ConnectStatusType2)
            - genConnectStatus: {dateTime, value} (deprecated fallback)
            - inverterStatus: {dateTime, value} (optional)
            - localControlModeStatus: {dateTime, value} (optional)
            - manufacturerStatus: {dateTime, value: str} (optional)
            - operationalModeStatus: {dateTime, value} (optional)
            - stateOfChargeStatus: {dateTime, value} (optional)
            - storageModeStatus: {dateTime, value} (optional)
            - storConnectStatus: {dateTime, value} (optional)
        server_2018_compat: When True, emit genConnectStatus/storConnectStatus
            (ConnectStatusType) instead of connectStatus (ConnectStatusType2)
            for IEEE 2030.5-2018 server compatibility.
        always_send_alarm_status: When True, include alarmStatus even when no
            alarms are active (all-zero bitmap) instead of omitting it. For
            servers that treat an absent alarmStatus as "no change" rather than
            "cleared". Edition-independent.

    Returns:
        Pydantic model ready for ``to_xml()`` serialization.
    """
    # Connector-supplied timestamps win; otherwise the caller's server-
    # timebase default, falling back to the local clock.
    now_default = default_time if default_time is not None else int(time.time())
    reading_time = status_data.get("readingTime", now_default)

    alarm = status_data.get("alarmStatus")
    alarm_int = alarm if isinstance(alarm, int) else None
    alarm_bytes: bytes | None = None
    if alarm_int is not None and alarm_int != 0:
        alarm_bytes = alarm_int.to_bytes(4, "big")
    elif always_send_alarm_status:
        # Emit an explicit all-zero bitmap rather than omitting the (optional)
        # field, so a server that reads an absent alarmStatus as "no change"
        # (not "cleared") still sees alarms clear. Opt-in via config.
        alarm_bytes = (0).to_bytes(4, "big")

    connect_data = status_data.get("connectStatus")
    gen_connect_data = status_data.get("genConnectStatus")
    # Resolve the best available connect data for either field
    best_connect = connect_data or gen_connect_data

    if server_2018_compat:
        # IEEE 2030.5-2018: only genConnectStatus/storConnectStatus exist;
        # connectStatus (ConnectStatusType2) must not be emitted.
        return Derstatus(
            reading_time=TimeType(value=reading_time),
            alarm_status=alarm_bytes,
            connect_status=None,
            gen_connect_status=_to_connect_status(best_connect, now_default),
            inverter_status=_to_inverter_status(status_data.get("inverterStatus"), now_default),
            local_control_mode_status=_to_local_control_mode(
                status_data.get("localControlModeStatus"), now_default
            ),
            manufacturer_status=_to_manufacturer_status(
                status_data.get("manufacturerStatus"), now_default
            ),
            operational_mode_status=_to_operational_mode(
                status_data.get("operationalModeStatus"), now_default
            ),
            state_of_charge_status=_to_state_of_charge(
                status_data.get("stateOfChargeStatus"), now_default
            ),
            storage_mode_status=_to_storage_mode(status_data.get("storageModeStatus"), now_default),
            stor_connect_status=_to_connect_status(
                status_data.get("storConnectStatus"), now_default
            ),
        )

    # IEEE 2030.5-2023: connectStatus (ConnectStatusType2) replaces deprecated
    # genConnectStatus. Populate connectStatus when available; fall back to
    # genConnectStatus for backward compatibility with older connectors.
    return Derstatus(
        reading_time=TimeType(value=reading_time),
        alarm_status=alarm_bytes,
        connect_status=_to_connect_status2(best_connect, now_default),
        gen_connect_status=(
            _to_connect_status(gen_connect_data, now_default) if connect_data is None else None
        ),
        inverter_status=_to_inverter_status(status_data.get("inverterStatus"), now_default),
        local_control_mode_status=_to_local_control_mode(
            status_data.get("localControlModeStatus"), now_default
        ),
        manufacturer_status=_to_manufacturer_status(
            status_data.get("manufacturerStatus"), now_default
        ),
        operational_mode_status=_to_operational_mode(
            status_data.get("operationalModeStatus"), now_default
        ),
        state_of_charge_status=_to_state_of_charge(
            status_data.get("stateOfChargeStatus"), now_default
        ),
        storage_mode_status=_to_storage_mode(status_data.get("storageModeStatus"), now_default),
        stor_connect_status=_to_connect_status(status_data.get("storConnectStatus"), now_default),
    )


def _dt(data: dict[str, Any], default_time: int) -> TimeType:
    return TimeType(value=data.get("dateTime", default_time))


def _to_connect_status(data: dict[str, Any] | None, default_time: int) -> ConnectStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return ConnectStatusType(
        date_time=_dt(data, default_time), value=int(data["value"]).to_bytes(1, "big")
    )


def _to_connect_status2(
    data: dict[str, Any] | None, default_time: int
) -> ConnectStatusType2 | None:
    """Build ConnectStatusType2 (IEEE 2030.5-2023 replacement for genConnectStatus)."""
    if data is None or data.get("value") is None:
        return None
    return ConnectStatusType2(
        date_time=_dt(data, default_time), value=int(data["value"]).to_bytes(1, "big")
    )


def _to_inverter_status(
    data: dict[str, Any] | None, default_time: int
) -> InverterStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return InverterStatusType(date_time=_dt(data, default_time), value=int(data["value"]))


def _to_local_control_mode(
    data: dict[str, Any] | None, default_time: int
) -> LocalControlModeStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return LocalControlModeStatusType(date_time=_dt(data, default_time), value=int(data["value"]))


def _to_manufacturer_status(
    data: dict[str, Any] | None, default_time: int
) -> ManufacturerStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return ManufacturerStatusType(date_time=_dt(data, default_time), value=str(data["value"]))


def _to_operational_mode(
    data: dict[str, Any] | None, default_time: int
) -> OperationalModeStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return OperationalModeStatusType(date_time=_dt(data, default_time), value=int(data["value"]))


def _to_state_of_charge(
    data: dict[str, Any] | None, default_time: int
) -> StateOfChargeStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return StateOfChargeStatusType(
        date_time=_dt(data, default_time), value=PerCent(value=int(data["value"]))
    )


def _to_storage_mode(
    data: dict[str, Any] | None, default_time: int
) -> StorageModeStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return StorageModeStatusType(date_time=_dt(data, default_time), value=int(data["value"]))
