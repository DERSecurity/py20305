"""LogEvent posting for IEEE 2030.5 alarm reporting.

Creates and POSTs LogEvent resources to the server's LogEventListLink,
which is discovered on EndDevice resources during the discovery chain.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# IEEE 2030.5 PEN for standard log events
IEEE_2030_5_PEN = 40732

# IEEE 2030.5 profile ID
PROFILE_IEEE_2030_5 = 2

# Function set: DER (11)
FUNCTION_SET_DER = 11

NS_SEP = "urn:ieee:std:2030.5:ns"

# DER alarm bit -> human name, per the IEEE 2030.5 DERStatus alarmStatus bitmap
# and CSIP Implementation Guide s4.6.3 Table 6 / s5.2.5.3 Table 14. Bits 11..31
# are reserved by IEEE and have no assigned LogEvent code.
DER_ALARM_NAMES: dict[int, str] = {
    0: "Over Current",
    1: "Over Voltage",
    2: "Under Voltage",
    3: "Over Frequency",
    4: "Under Frequency",
    5: "Voltage Imbalance",
    6: "Current Imbalance",
    7: "Local Emergency",
    8: "Remote Emergency",
    9: "Low Input Power",
    10: "Phase Rotation",
}

#: Highest alarm bit with an IEEE-assigned LogEvent code.
MAX_DER_ALARM_BIT = max(DER_ALARM_NAMES)

#: Mask of alarm bits that have an IEEE-assigned LogEvent code. Bits outside
#: this mask can never produce a LogEvent, so transition tracking syncs them
#: straight into its baseline instead of waiting on a POST that never happens.
MAPPED_ALARM_MASK = sum(1 << bit for bit in DER_ALARM_NAMES)


def alarm_log_event_code(bit: int, *, returned_to_normal: bool = False) -> int:
    """LogEvent code for a DER alarm bit, per CSIP s5.2.5.3 Table 14.

    Codes are assigned in pairs, ordered by alarm bit: the alarm itself gets
    ``2 * bit`` and its "return to normal" (RTN) counterpart ``2 * bit + 1``.
    So Over Current is 0/1, ... Local Emergency (bit 7) is 14/15, Remote
    Emergency (bit 8) is 16/17, up to Phase Rotation (bit 10) at 20/21.

    These are IEEE-2030.5-assigned codes: they are only meaningful alongside
    ``profileID=2`` (IEEE 2030.5), ``functionSet=11`` (DER), and the IEEE
    2030.5 ``logEventPEN`` -- see the XSD notes on ``logEventCode`` (codes are
    scoped to a profile + function set) and ``logEventPEN`` ("IEEE
    2030.5-assigned logEventCodes SHALL use the IEEE 2030.5 PEN").

    Raises ``ValueError`` for a bit outside the assigned range -- callers must
    filter reserved bits rather than invent a code for them.
    """
    if bit not in DER_ALARM_NAMES:
        raise ValueError(
            f"DER alarm bit {bit} has no IEEE 2030.5-assigned LogEvent code "
            f"(assigned bits are 0..{MAX_DER_ALARM_BIT})"
        )
    return 2 * bit + (1 if returned_to_normal else 0)


def alarm_bits_to_log_events(previous: int, current: int) -> list[tuple[int, int]]:
    """Diff two alarm bitmaps into the LogEvent codes to post.

    Returns ``(bit, code)`` pairs in ascending bit order: a bit that became set
    yields its alarm code, a bit that became clear yields its RTN code. An
    unchanged bitmap yields ``[]`` -- CSIP s4.6.3 wants alarms reported "as they
    occur", so a persisting alarm is not re-posted every cycle.

    Reserved bits (>= 11, which SunSpec does populate) are skipped: IEEE assigns
    them no code, so there is nothing compliant to send. Callers that want to
    surface them should inspect ``unmapped_alarm_bits``.
    """
    changed = previous ^ current
    events: list[tuple[int, int]] = []
    for bit in sorted(DER_ALARM_NAMES):
        if not changed & (1 << bit):
            continue
        rtn = not (current & (1 << bit))
        events.append((bit, alarm_log_event_code(bit, returned_to_normal=rtn)))
    return events


def unmapped_alarm_bits(alarm_status: int) -> list[int]:
    """Set bits in *alarm_status* that have no IEEE-assigned LogEvent code.

    SunSpec model 701 ``Alrm`` populates bits 11..16 (AC_UNDER_VOLT,
    BLOWN_STRING_FUSE, ...), which fall in the IEEE reserved range. They still
    reach the server via ``DERStatus.alarmStatus`` (the bitmap's proper home);
    they just can't be expressed as a LogEvent.
    """
    return [
        bit
        for bit in range(alarm_status.bit_length())
        if alarm_status & (1 << bit) and bit not in DER_ALARM_NAMES
    ]


def create_log_event_xml(
    log_event_code: int,
    log_event_id: int = 0,
    function_set: int = FUNCTION_SET_DER,
    profile_id: int = PROFILE_IEEE_2030_5,
    pen: int = IEEE_2030_5_PEN,
    details: str | None = None,
    *,
    server_2018_compat: bool = False,
    created_time: int | None = None,
) -> bytes:
    """Build a LogEvent XML body for POST to LogEventListLink.

    Args:
        log_event_code: IEEE 2030.5-assigned LogEvent code (NOT an alarm
            bitmap). For DER alarms use :func:`alarm_log_event_code` /
            :func:`alarm_bits_to_log_events` to derive it -- codes are scoped
            to ``profile_id`` + ``function_set`` per the XSD, so a raw bitmap
            here would claim a meaning IEEE never assigned.
        log_event_id: 16-bit event identifier.
        function_set: IEEE 2030.5 function set identifier (default: DER=11).
        profile_id: Profile identifier (default: IEEE 2030.5=2).
        pen: Private Enterprise Number (default: IEEE 2030.5 PEN).
        details: Optional human-readable detail string (max 32 chars).

    Returns:
        XML bytes for POST to the server.
    """
    # createdDateTime: caller passes server-timebase time; local-clock default.
    now = created_time if created_time is not None else int(time.time())

    schema_attr = "" if server_2018_compat else ' schemaVer="2.2"'
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<LogEvent xmlns="{NS_SEP}"{schema_attr}>',
        f"<createdDateTime>{now}</createdDateTime>",
    ]

    if details:
        # Truncate to 32 chars per spec
        lines.append(f"<details>{details[:32]}</details>")

    lines.extend(
        [
            f"<functionSet>{function_set}</functionSet>",
            f"<logEventCode>{log_event_code}</logEventCode>",
            f"<logEventID>{log_event_id}</logEventID>",
            f"<logEventPEN>{pen}</logEventPEN>",
            f"<profileID>{profile_id}</profileID>",
            "</LogEvent>",
        ]
    )

    return "".join(lines).encode("utf-8")


def extract_alarm_status(status_data: dict[str, Any]) -> int:
    """Extract alarm status from connector status data.

    Args:
        status_data: Dict from connector.fetch_status().

    Returns:
        Integer alarm status bitmap, or 0 if not available.
    """
    raw = status_data.get("alarmStatus", 0)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, bytes):
        return int.from_bytes(raw, byteorder="big")
    return 0
