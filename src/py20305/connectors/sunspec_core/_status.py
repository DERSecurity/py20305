"""Convert SunSpec model 701 status points to IEEE 2030.5 DERStatus values.

The two standards encode the same states differently, so no point is copied
across unconverted:

- ``InvSt`` (SunSpec model 701) and InverterStatusType (IEEE 2030.5) list the
  same eight states in the same order, but 2030.5 starts at 1 and keeps 0 for
  "not applicable". ``InvSt`` n is InverterStatusType n + 1.
- ``St`` is OFF or ON. OperationalModeStatusType is 0 not applicable, 1 off,
  2 operational mode, 3 test mode.
- ``ConnSt`` is an enumeration, DISCONNECTED or CONNECTED. The DERStatus
  connect fields are bitmaps, and the two editions define different bits:
  ConnectStatusType2 (2030.5-2023 ``connectStatus``) has bit 0 connected and
  bit 1 energized; ConnectStatusType (2030.5-2018 ``genConnectStatus``) has
  bit 0 connected, 1 available, 2 operating, 3 test and 4 fault.
- ``Alrm`` is a bitfield whose bits do not line up with the DERStatus
  ``alarmStatus`` bits. Only the alarms that 2030.5 also names are carried.

Sources: the IEEE 2030.5-2018 and 2030.5-2023 schemas in
``py20305/schemas/`` (the type documentation of each field), and the SunSpec
model 701 definition.

The bits other than "connected" are derived from ``InvSt``, because model 701
has no point for them. Each choice is the narrowest one the state supports:

- Energized (2023) and operating (2018): RUNNING or THROTTLED, the states in
  which the inverter delivers power.
- Available (2018): SLEEPING, STARTING, RUNNING or THROTTLED, the states in
  which the inverter produces power or will once its source allows it.
  OFF, SHUTTING_DOWN, FAULT and STANDBY are not available.
- Fault (2018): FAULT.

This module depends on nothing else in this package, like the rest of the
SunSpec core.
"""

from __future__ import annotations

from typing import Any

#: SunSpec model 701 ``InvSt`` values.
INV_ST_OFF = 0
INV_ST_SLEEPING = 1
INV_ST_STARTING = 2
INV_ST_RUNNING = 3
INV_ST_THROTTLED = 4
INV_ST_SHUTTING_DOWN = 5
INV_ST_FAULT = 6
INV_ST_STANDBY = 7

#: SunSpec model 701 ``InvSt`` -> IEEE 2030.5 InverterStatusType.
_INVERTER_STATUS: dict[int, int] = {
    INV_ST_OFF: 1,  # off
    INV_ST_SLEEPING: 2,  # sleeping
    INV_ST_STARTING: 3,  # starting up, or on but not producing power
    INV_ST_RUNNING: 4,  # running (2018: tracking MPPT power point)
    INV_ST_THROTTLED: 5,  # forced power reduction or derating
    INV_ST_SHUTTING_DOWN: 6,  # shutting down
    INV_ST_FAULT: 7,  # one or more faults exist
    INV_ST_STANDBY: 8,  # standby
}

#: SunSpec model 701 ``St`` -> IEEE 2030.5 OperationalModeStatusType.
_OPERATIONAL_MODE_STATUS: dict[int, int] = {
    0: 1,  # OFF -> off
    1: 2,  # ON -> operational mode
}

#: SunSpec model 701 ``ConnSt`` value for CONNECTED.
_CONN_ST_CONNECTED = 1

#: Connect status bits, both editions.
CONNECTED = 1 << 0
#: ConnectStatusType2 (IEEE 2030.5-2023).
ENERGIZED = 1 << 1
#: ConnectStatusType (IEEE 2030.5-2018).
AVAILABLE = 1 << 1
OPERATING = 1 << 2
FAULT = 1 << 4

_DELIVERING = frozenset({INV_ST_RUNNING, INV_ST_THROTTLED})
_AVAILABLE = frozenset({INV_ST_SLEEPING, INV_ST_STARTING, INV_ST_RUNNING, INV_ST_THROTTLED})

#: SunSpec model 701 ``Alrm`` bit -> IEEE 2030.5 DERStatus ``alarmStatus`` bit.
#: Alarms with no 2030.5 counterpart (ground fault, DC over-voltage,
#: disconnects, cabinet open, manual shutdown, temperature, blown fuse, memory
#: loss, hardware test, manufacturer alarm) are not carried.
_ALARM_BITS: dict[int, int] = {
    8: 3,  # OVER_FREQUENCY -> DER_FAULT_OVER_FREQUENCY
    9: 4,  # UNDER_FREQUENCY -> DER_FAULT_UNDER_FREQUENCY
    10: 1,  # AC_OVER_VOLT -> DER_FAULT_OVER_VOLTAGE
    11: 2,  # AC_UNDER_VOLT -> DER_FAULT_UNDER_VOLTAGE
}


def _as_int(value: Any) -> int | None:
    """Return ``value`` as an int, or None when it is missing or not an integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def inverter_status(inv_st: Any) -> int | None:
    """Return the InverterStatusType for an ``InvSt`` value, or None if it is undefined."""
    code = _as_int(inv_st)
    return None if code is None else _INVERTER_STATUS.get(code)


def operational_mode_status(st: Any) -> int | None:
    """Return the OperationalModeStatusType for an ``St`` value, or None if it is undefined."""
    code = _as_int(st)
    return None if code is None else _OPERATIONAL_MODE_STATUS.get(code)


def connect_status(conn_st: Any, inv_st: Any) -> int | None:
    """Return the IEEE 2030.5-2023 ConnectStatusType2 bitmap, or None without ``ConnSt``."""
    connected = _as_int(conn_st)
    if connected is None:
        return None
    bits = CONNECTED if connected == _CONN_ST_CONNECTED else 0
    if _as_int(inv_st) in _DELIVERING:
        bits |= ENERGIZED
    return bits


def gen_connect_status(conn_st: Any, inv_st: Any) -> int | None:
    """Return the IEEE 2030.5-2018 ConnectStatusType bitmap, or None without ``ConnSt``."""
    connected = _as_int(conn_st)
    if connected is None:
        return None
    bits = CONNECTED if connected == _CONN_ST_CONNECTED else 0
    state = _as_int(inv_st)
    if state in _AVAILABLE:
        bits |= AVAILABLE
    if state in _DELIVERING:
        bits |= OPERATING
    if state == INV_ST_FAULT:
        bits |= FAULT
    return bits


def alarm_status(alrm: Any) -> int:
    """Return the DERStatus ``alarmStatus`` bitmap for an ``Alrm`` bitfield.

    A missing or unreadable ``Alrm`` reports no alarms.
    """
    bits = _as_int(alrm)
    if bits is None:
        return 0
    result = 0
    for sunspec_bit, der_bit in _ALARM_BITS.items():
        if bits & (1 << sunspec_bit):
            result |= 1 << der_bit
    return result
