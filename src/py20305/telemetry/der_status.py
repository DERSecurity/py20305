"""DERStatus model construction for IEEE 2030.5 inverter status reporting.

Builds Pydantic ``Derstatus`` models from connector status data,
suitable for serialization via ``to_xml()`` and PUT to the server.

Every value is an IEEE 2030.5 value, and each is checked against the
type its field carries before it is sent (IEEE 2030.5-2018 and 2030.5-2023
schemas, DERStatus and its status types):

- inverterStatus (InverterStatusType) 0 to 10, operationalModeStatus
  (OperationalModeStatusType) 0 to 3, localControlModeStatus 0 to 1,
  storageModeStatus 0 to 2, stateOfChargeStatus 0 to 10000 hundredths of a
  percent.
- connectStatus (ConnectStatusType2, 2030.5-2023) is a bitmap of bits 0 and 1
  (connected, energized). genConnectStatus and storConnectStatus
  (ConnectStatusType, 2030.5-2018) are bitmaps of bits 0 to 4 (connected,
  available, operating, test, fault).
- alarmStatus is a bitmap of bits 0 to 10; bits 11 to 31 are reserved.

A value outside its type, or a bitmap with a reserved bit set, is left out
of the DERStatus and logged, rather than sent as a code the server would
take as fact. Converting another protocol's status codes to these values is
the caller's job.
"""

from __future__ import annotations

import logging
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
from py20305.server_profile import ServerProfile

logger = logging.getLogger(__name__)

#: Highest defined value of each enumerated status field.
_ENUM_MAX: dict[str, int] = {
    "inverterStatus": 10,
    "operationalModeStatus": 3,
    "localControlModeStatus": 1,
    "storageModeStatus": 2,
    "stateOfChargeStatus": 10000,
}

#: ConnectStatusType2 (2030.5-2023): bit 0 connected, bit 1 energized.
CONNECT_STATUS_2023_BITS = 0b11
#: ConnectStatusType (2030.5-2018): bits 0 to 4.
CONNECT_STATUS_2018_BITS = 0b11111
#: DERStatus alarmStatus: bits 0 to 10.
ALARM_STATUS_BITS = (1 << 11) - 1


def build_der_status(
    status_data: dict[str, Any],
    *,
    profile: ServerProfile | None = None,
    always_send_alarm_status: bool = False,
    default_time: int | None = None,
) -> Derstatus:
    """Build a ``Derstatus`` model from connector data.

    Args:
        status_data: Dict from connector.fetch_status() with keys:
            - readingTime: int (epoch seconds, defaults to now)
            - alarmStatus: int (bitmap of bits 0 to 10, optional; 0 omitted
              unless ``always_send_alarm_status`` is set)
            - connectStatus: {dateTime, value} (ConnectStatusType2 bitmap,
              IEEE 2030.5-2023)
            - genConnectStatus: {dateTime, value} (ConnectStatusType bitmap,
              IEEE 2030.5-2018)
            - inverterStatus: {dateTime, value} (optional)
            - localControlModeStatus: {dateTime, value} (optional)
            - manufacturerStatus: {dateTime, value: str} (optional)
            - operationalModeStatus: {dateTime, value} (optional)
            - stateOfChargeStatus: {dateTime, value} (optional)
            - storageModeStatus: {dateTime, value} (optional)
            - storConnectStatus: {dateTime, value} (optional)
        profile: The server's schema profile. A 2018 base sends
            genConnectStatus/storConnectStatus (ConnectStatusType) and not
            connectStatus (ConnectStatusType2); a 2023 base the reverse. None
            builds for 2023.
        always_send_alarm_status: When True, include alarmStatus even when no
            alarms are active (all-zero bitmap) instead of omitting it. For
            servers that treat an absent alarmStatus as "no change" rather than
            "cleared". Edition-independent.

    Each server edition receives the connect bitmap it defines, exactly as the
    connector supplied it, and the bitmaps are never converted into each other.
    A 2023 server receives connectStatus only, and only if the connector
    supplied it; it never receives genConnectStatus or storConnectStatus. A
    2018 server receives genConnectStatus and storConnectStatus only as the
    connector supplied them, and never receives connectStatus.

    Returns:
        Pydantic model ready for ``to_xml()`` serialization.
    """
    # Connector-supplied timestamps win; otherwise the caller's server-
    # timebase default, falling back to the local clock.
    now_default = default_time if default_time is not None else int(time.time())
    reading_time = status_data.get("readingTime", now_default)

    alarm = status_data.get("alarmStatus")
    alarm_int = alarm if isinstance(alarm, int) and not isinstance(alarm, bool) else None
    alarm_bytes: bytes | None = None
    if alarm_int is not None and not _fits_bitmap("alarmStatus", alarm_int, ALARM_STATUS_BITS):
        # Left out entirely: an explicit zero would clear the server's alarms
        # on the strength of a value that was rejected.
        pass
    elif alarm_int is not None and alarm_int != 0:
        alarm_bytes = alarm_int.to_bytes(4, "big")
    elif always_send_alarm_status:
        # Emit an explicit all-zero bitmap rather than omitting the (optional)
        # field, so a server that reads an absent alarmStatus as "no change"
        # (not "cleared") still sees alarms clear. Opt-in via config.
        alarm_bytes = (0).to_bytes(4, "big")

    connect_2023 = _bitmap("connectStatus", status_data.get("connectStatus"))
    connect_2018 = _bitmap("genConnectStatus", status_data.get("genConnectStatus"))
    common: dict[str, Any] = {
        "reading_time": TimeType(value=reading_time),
        "alarm_status": alarm_bytes,
        "inverter_status": _enum(InverterStatusType, "inverterStatus", status_data, now_default),
        "local_control_mode_status": _enum(
            LocalControlModeStatusType, "localControlModeStatus", status_data, now_default
        ),
        "manufacturer_status": _to_manufacturer_status(
            status_data.get("manufacturerStatus"), now_default
        ),
        "operational_mode_status": _enum(
            OperationalModeStatusType, "operationalModeStatus", status_data, now_default
        ),
        "state_of_charge_status": _to_state_of_charge(status_data, now_default),
        "storage_mode_status": _enum(
            StorageModeStatusType, "storageModeStatus", status_data, now_default
        ),
    }

    if profile is not None and profile.is_2018:
        # IEEE 2030.5-2018: only genConnectStatus/storConnectStatus exist, and
        # they are sent as the connector supplied them. connectStatus
        # (ConnectStatusType2) is never sent.
        return Derstatus(
            connect_status=None,
            gen_connect_status=_to_connect_status(connect_2018, now_default),
            stor_connect_status=_to_connect_status(
                _bitmap("storConnectStatus", status_data.get("storConnectStatus"), 2018),
                now_default,
            ),
            **common,
        )

    # IEEE 2030.5-2023: connectStatus (ConnectStatusType2) is sent as the
    # connector supplied it. genConnectStatus and storConnectStatus SHALL NOT
    # be included.
    return Derstatus(
        connect_status=_to_connect_status2(connect_2023, now_default),
        gen_connect_status=None,
        stor_connect_status=None,
        **common,
    )


def _dt(data: dict[str, Any], default_time: int) -> TimeType:
    return TimeType(value=data.get("dateTime", default_time))


def _stamped_int(data: Any) -> int | None:
    """Return the integer value of a ``{dateTime, value}`` entry, or None."""
    if not isinstance(data, dict):
        return None
    value = data.get("value")
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("DERStatus value %r is not an integer; left out", value)
        return None


def _fits_bitmap(name: str, value: int, defined: int) -> bool:
    """Return True when ``value`` sets only bits in ``defined``; log otherwise."""
    if value < 0 or value & ~defined:
        logger.warning(
            "DERStatus %s 0x%X sets bits IEEE 2030.5 does not define; left out", name, value
        )
        return False
    return True


def _bitmap(name: str, data: Any, edition: int | None = None) -> tuple[int, Any] | None:
    """Return ``(value, entry)`` for a connect bitmap within its edition's bits, or None."""
    value = _stamped_int(data)
    if value is None:
        return None
    if edition is None:
        edition = 2023 if name == "connectStatus" else 2018
    defined = CONNECT_STATUS_2023_BITS if edition == 2023 else CONNECT_STATUS_2018_BITS
    if not _fits_bitmap(name, value, defined):
        return None
    return value, data


def _to_connect_status(
    bitmap: tuple[int, Any] | None, default_time: int
) -> ConnectStatusType | None:
    if bitmap is None:
        return None
    value, data = bitmap
    return ConnectStatusType(date_time=_dt(data, default_time), value=value.to_bytes(1, "big"))


def _to_connect_status2(
    bitmap: tuple[int, Any] | None, default_time: int
) -> ConnectStatusType2 | None:
    """Build ConnectStatusType2 (IEEE 2030.5-2023 replacement for genConnectStatus)."""
    if bitmap is None:
        return None
    value, data = bitmap
    return ConnectStatusType2(date_time=_dt(data, default_time), value=value.to_bytes(1, "big"))


def _enum(model: type, name: str, status_data: dict[str, Any], default_time: int) -> Any:
    """Build an enumerated status field, or None when absent or out of range."""
    data = status_data.get(name)
    value = _stamped_int(data)
    if value is None or not isinstance(data, dict):
        return None
    if not 0 <= value <= _ENUM_MAX[name]:
        logger.warning(
            "DERStatus %s %d is not an IEEE 2030.5 value (0 to %d); left out",
            name,
            value,
            _ENUM_MAX[name],
        )
        return None
    return model(date_time=_dt(data, default_time), value=value)


def _to_manufacturer_status(
    data: dict[str, Any] | None, default_time: int
) -> ManufacturerStatusType | None:
    if data is None or data.get("value") is None:
        return None
    return ManufacturerStatusType(date_time=_dt(data, default_time), value=str(data["value"]))


def _to_state_of_charge(
    status_data: dict[str, Any], default_time: int
) -> StateOfChargeStatusType | None:
    data = status_data.get("stateOfChargeStatus")
    value = _stamped_int(data)
    if value is None or not isinstance(data, dict):
        return None
    if not 0 <= value <= _ENUM_MAX["stateOfChargeStatus"]:
        logger.warning(
            "DERStatus stateOfChargeStatus %d is outside 0 to 10000 hundredths of a percent;"
            " left out",
            value,
        )
        return None
    return StateOfChargeStatusType(date_time=_dt(data, default_time), value=PerCent(value=value))
