"""MirrorUsagePoint creation for IEEE 2030.5 telemetry.

A connector returns a list of :class:`~py20305.connectors.reading.Reading`.
Each reading becomes a MirrorMeterReading whose mRID is derived from the
reading's identity (its ReadingType fields, see
:func:`~py20305.connectors.reading.reading_identity`), so a reading whose
ReadingType differs from another's always has a different mRID. IEEE
2030.5-2023 §10.11.3 Rule n forbids changing a MirrorMeterReading's ReadingType
once created, and this keeps that true without special cases.

Readings that release 0.16 registered keep the mRID it gave them. A fixed table
maps each of those identities to its 0.16 slot, and the slot gives the mRID:

- 0: the MirrorUsagePoint itself.
- 1-7: system W, var, Hz, V (average), PF, VA, A.
- 8: state of energy (SIGNED_LOAD_CONVENTION only).
- 10-27: per-line W, var, V (average, line to neutral), PF, VA, A, six slots
  per line.
- 28-30: line-to-line voltage (SIGNED_LOAD_CONVENTION only).
- 31-38: W and var with flowDirection 4 (``PowerDirection.NET``).
- 41-48 and 51-58: W and var with flowDirection 1 and 19
  (``PowerDirection.SPLIT``).
- 61 and 62: energy delivered forward and in reverse, DEFAULT profile only.

Every other identity gets an mRID hashed from the identity, in a layout that
cannot collide with the slot layout.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from py20305.connectors.reading import (
    DEFAULT_MULTIPLIER,
    Reading,
    ReadingIdentity,
    is_valid_reading,
    reading_identity,
)
from py20305.models.sep import (
    AccumulationBehaviourType,
    CommodityType,
    DataQualifierType,
    DateTimeInterval,
    FlowDirectionType,
    KindType,
    MirrorMeterReading1,
    MirrorMeterReadingList,
    MirrorUsagePoint,
    MRidtype,
    PhaseCode,
    PowerOfTenMultiplierType,
    Reading1,
    ReadingType1,
    RoleFlagsType,
    ServiceKind,
    TimeType,
    UomType,
    VersionType,
)
from py20305.telemetry.scaling import FLOW_NET, FLOW_NORMAL, FLOW_NOT_APPLICABLE, FLOW_REVERSE

logger = logging.getLogger(__name__)

# Default Private Enterprise Number (DER Security Corp)
DEFAULT_PEN = 53630

# Service category kind: electricity = 0
SERVICE_CATEGORY_ELECTRICITY = 0

# RoleFlagsType for a DER MirrorUsagePoint. CSIP-AUS requires 0x49 for DER
# measurements: isMirror (bit 0 -- SHALL be set for mirrored data, BASE.046),
# isDER (bit 3), and isSubmeter (bit 6 -- a DER meters behind the site meter,
# BASE.052). isDER alone (0x08) leaves a conformance tool unable to classify the
# metering location ("Site type is unknown").
ROLE_FLAGS_DER = 0x49  # isMirror | isDER | isSubmeter

# RoleFlagsType for the meter at the premises point of delivery: isMirror (bit 0)
# and isPremisesAggregationPoint (bit 1). isSubmeter (bit 6) stays clear, since
# IEEE 2030.5 sets it only on a usage point that is not a premises aggregation
# point. CSIP-AUS v1.1a (January 2023) Annex A lists 0x03 for the site
# (connection point) real and reactive power.
ROLE_FLAGS_SITE = 0x03  # isMirror | isPremisesAggregationPoint

# RoleFlagsType bit 4, isRevenueQuality (IEEE 2030.5-2023 RoleFlagsType).
ROLE_FLAG_REVENUE_QUALITY = 0x10


class DeviceRole(StrEnum):
    """What a configured device measures, which sets its MUP roleFlags.

    - ``DER``: a DER. Its readings are mirrored with roleFlags 0x49.
    - ``SITE_METER``: the meter at the premises point of delivery, mirrored
      with roleFlags 0x03. Positive real power is import, from the grid to the
      premises, which IEEE 2030.5 FlowDirectionType names Forward.
    - ``DER_METER``: a meter on one DER's circuit, mirrored with roleFlags
      0x49 like the DER itself. Positive real power is the DER's output.

    Reactive power takes the same orientation as real power. The role also
    sets how ``SIGNED_LOAD_CONVENTION`` signs W and var (see
    :class:`ReadingProfile`). A meter takes no controls and has no DER
    resources.
    """

    DER = "der"
    SITE_METER = "site_meter"
    DER_METER = "der_meter"


def role_flags_for(role: DeviceRole, revenue_quality: bool = False) -> int:
    """Return the MUP roleFlags for a device role, with isRevenueQuality when set."""
    flags = ROLE_FLAGS_SITE if role is DeviceRole.SITE_METER else ROLE_FLAGS_DER
    return (flags | ROLE_FLAG_REVENUE_QUALITY) if revenue_quality else flags


_ROLE_DESCRIPTION: dict[DeviceRole, str] = {
    DeviceRole.DER: "DER",
    DeviceRole.SITE_METER: "Site meter",
    DeviceRole.DER_METER: "DER meter",
}

# Status: on = 1
STATUS_ON = 1

# IEEE 2030.5 dataQualifier for an instantaneous (point-in-time) sample. Interval
# qualifiers (2=Average, 8=Maximum, 9=Minimum) cover a window, so their readings
# carry a non-zero timePeriod duration; an instantaneous reading is a point.
_DATA_QUALIFIER_INSTANTANEOUS = 12

# Reading qualityFlags (HexBinary16): bit 0 = valid (data that passed all
# required validation checks or has been verified). Our readings are live
# measurements from the connector, so each carries the Valid bit.
QUALITY_FLAGS_VALID = 1

# Bit 4 = questionable ("data that has failed one or more checks",
# sep2_schema_2023.xsd ReadingBase). An age past what any consumer asked for
# is such a check.
QUALITY_FLAGS_QUESTIONABLE = 1 << 4


class ReadingProfile(StrEnum):
    """How a device's MirrorMeterReadings are encoded.

    ``DEFAULT`` posts each reading's ReadingType as the connector states it,
    with commodity 1 (electricity, secondary metered). A W, var, VA, A or PF
    reading that sets no flowDirection takes it from the value's sign when the
    reading is first registered and posts magnitudes.

    ``SIGNED_LOAD_CONVENTION`` carries the sign on every value (flowDirection
    0) using the load convention -- active power negative on export, reactive
    power negative on injection -- with commodity, dataQualifier and kind left
    Not Applicable, and current in whole amperes unless the reading sets a
    multiplier.

    How W and var are signed depends on the device's :class:`DeviceRole`. A
    DER and a DER meter report them positive for the DER's output, which the
    load convention counts as export, so both are negated. A site meter
    reports them positive for import, from the grid to the premises, which is
    already the load convention, so both are posted as reported.
    """

    DEFAULT = "default"
    SIGNED_LOAD_CONVENTION = "signed_load_convention"


class PowerDirection(StrEnum):
    """How real and reactive power carry their direction under the DEFAULT profile.

    IEEE 2030.5 forbids changing a MeterReading's ReadingType once created,
    and its flow directions other than Net carry a positive magnitude. So:

    - ``DEFAULT`` sets each reading's flowDirection once, from the value's sign
      when the reading is registered (1 Forward or 19 Reverse), and posts
      magnitudes. A value that changes sign later, a battery that registered
      while discharging and then charges, is posted under the first direction.
    - ``NET`` posts flowDirection 4 (Net, forward minus reverse) with the
      sign on the value: positive while the DER delivers, negative while it
      absorbs.
    - ``SPLIT`` posts two readings per quantity, flowDirection 1 (Forward)
      and 19 (Reverse), each a magnitude, with the inactive one 0.

    It applies to W and var readings that set no flowDirection. ``NET`` and
    ``SPLIT`` readings have mRIDs of their own, so a device switched between
    modes never posts a value under a ReadingType registered for another mode.
    """

    DEFAULT = "default"
    NET = "net"
    SPLIT = "split"


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

_UOM_W = 38
_UOM_VAR = 63
_UOM_VA = 61
_UOM_V = 29
_UOM_A = 5
_UOM_HZ = 33
_UOM_PF = 65
_UOM_WH = 72

#: Units whose value is signed in the connector contract. Under DEFAULT a
#: reading of one of these that sets no flowDirection takes it from the sign.
_SIGNED_UOMS = frozenset({_UOM_W, _UOM_VAR, _UOM_VA, _UOM_A, _UOM_PF})
#: Units that PowerDirection applies to.
_DIRECTED_UOMS = frozenset({_UOM_W, _UOM_VAR})

#: SIGNED_LOAD_CONVENTION posts current in whole amperes by default.
_SIGNED_LOAD_MULTIPLIER: dict[int, int] = {**DEFAULT_MULTIPLIER, _UOM_A: 0}

_UOM_NAME: dict[int, str] = {
    _UOM_W: "Real Power",
    _UOM_VAR: "Reactive Power",
    _UOM_HZ: "Frequency",
    _UOM_V: "Voltage",
    _UOM_PF: "Power Factor",
    _UOM_VA: "Apparent Power",
    _UOM_A: "Current",
    _UOM_WH: "Real Energy",
}

_PHASE_NAME: dict[int, str] = {
    128: "A",
    64: "B",
    32: "C",
    129: "AN",
    65: "BN",
    33: "CN",
    132: "AB",
    66: "BC",
    40: "CA",
    224: "ABC",
    225: "ABCN",
    136: "S1",
    16: "S1N",
    8: "S2N",
    24: "S12N",
    134: "S12",
}

_DATA_QUALIFIER_NAME: dict[int, str] = {2: "avg", 8: "max", 9: "min"}

_DIRECTION_SUFFIX: dict[int, str] = {
    FLOW_NET: "(net)",
    FLOW_NORMAL: "(forward)",
    FLOW_REVERSE: "(reverse)",
}

#: The line a per-line phase code belongs to; anything else is a total.
_LINE_OF_PHASE: dict[int, int] = {128: 1, 129: 1, 64: 2, 65: 2, 32: 3, 33: 3}

#: IEEE 2030.5 descriptions are String32.
_DESCRIPTION_MAX = 32


# ---------------------------------------------------------------------------
# The 0.16 slot table
# ---------------------------------------------------------------------------


def _identity(
    uom: int,
    *,
    phase: int = 0,
    kind: int = 37,
    dq: int = 12,
    acc: int = 12,
    flow: int | None = None,
    multiplier: int = 0,
) -> ReadingIdentity:
    return ReadingIdentity(uom, phase, kind, dq, acc, flow, multiplier)


def _system_entries(total_phases: Iterable[int], amp_multiplier: int) -> list[tuple]:
    """Return ``(identity, slot, description)`` for the seven system readings.

    ``total_phases`` are the phase codes a W, var, VA or A total was registered
    with: 0 under DEFAULT, and also the wiring-derived 128, 132 and 224 under
    SIGNED_LOAD_CONVENTION.
    """
    entries: list[tuple] = []
    for phase in total_phases:
        entries += [
            (_identity(_UOM_W, phase=phase), 1, "Real Power"),
            (_identity(_UOM_VAR, phase=phase), 2, "Reactive Power"),
            (_identity(_UOM_VA, phase=phase), 6, "Apparent Power"),
            (_identity(_UOM_A, phase=phase, multiplier=amp_multiplier), 7, "Current"),
        ]
    entries += [
        (_identity(_UOM_HZ, multiplier=-3), 3, "Frequency"),
        (_identity(_UOM_V, dq=2, multiplier=-1), 4, "Voltage"),
        (_identity(_UOM_PF, kind=0, multiplier=-3), 5, "Power Factor"),
    ]
    return entries


def _per_line_entries(amp_multiplier: int) -> list[tuple]:
    """Return ``(identity, slot, description)`` for the per-line readings, slots 10-27."""
    entries: list[tuple] = []
    for line, (phase, v_phase) in enumerate(((128, 129), (64, 65), (32, 33)), start=1):
        base = 10 + (line - 1) * 6
        entries += [
            (_identity(_UOM_W, phase=phase), base, f"Real Power L{line}"),
            (_identity(_UOM_VAR, phase=phase), base + 1, f"Reactive Power L{line}"),
            (_identity(_UOM_V, phase=v_phase, dq=2, multiplier=-1), base + 2, f"Voltage L{line}"),
            (
                _identity(_UOM_PF, phase=phase, kind=0, multiplier=-3),
                base + 3,
                f"Power Factor L{line}",
            ),
            (_identity(_UOM_VA, phase=phase), base + 4, f"Apparent Power L{line}"),
            (
                _identity(_UOM_A, phase=phase, multiplier=amp_multiplier),
                base + 5,
                f"Current L{line}",
            ),
        ]
    return entries


def _table(entries: Iterable[tuple]) -> dict[ReadingIdentity, tuple[int, str]]:
    return {identity: (slot, description) for identity, slot, description in entries}


#: DEFAULT profile: identity -> (0.16 slot, description).
_DEFAULT_SLOTS: dict[ReadingIdentity, tuple[int, str]] = _table(
    [
        *_system_entries((0,), -1),
        *_per_line_entries(-1),
        (
            _identity(_UOM_WH, kind=12, acc=9, flow=FLOW_NORMAL),
            61,
            "Real Energy (forward)",
        ),
        (
            _identity(_UOM_WH, kind=12, acc=9, flow=FLOW_REVERSE),
            62,
            "Real Energy (reverse)",
        ),
    ]
)


def _signed_load_table(total_phases: tuple[int, ...]) -> dict[ReadingIdentity, tuple[int, str]]:
    return _table(
        [
            *_per_line_entries(0),
            *_system_entries(total_phases, 0),
            *(
                (_identity(_UOM_WH, phase=phase, kind=0), 8, "State of Energy")
                for phase in total_phases
            ),
            (_identity(_UOM_V, phase=132, multiplier=-1), 28, "Voltage L1-L2"),
            (_identity(_UOM_V, phase=66, multiplier=-1), 29, "Voltage L2-L3"),
            (_identity(_UOM_V, phase=40, multiplier=-1), 30, "Voltage L3-L1"),
        ]
    )


#: SIGNED_LOAD_CONVENTION: identity -> (0.16 slot, description). Release 0.16
#: tagged the W, var, VA, A and state-of-energy totals with a phase code taken
#: from the device's wiring: 128 on a single-phase device, 132 split phase,
#: 224 three phase. Phase 128 also marks line 1 of a multi-phase device, so a
#: device that reports no line 2 or line 3 reading takes the single-phase table.
_SIGNED_LOAD_SLOTS: dict[ReadingIdentity, tuple[int, str]] = _signed_load_table((0, 132, 224))
_SIGNED_LOAD_SLOTS_SINGLE_PHASE: dict[ReadingIdentity, tuple[int, str]] = _signed_load_table(
    (0, 128, 132, 224)
)

#: Phase codes of lines 2 and 3.
_LINE_2_3_PHASES = frozenset({64, 65, 32, 33})
#: Phase codes a multi-phase device tags its totals with (split phase, three phase).
_MULTI_PHASE_TOTAL_PHASES = frozenset({132, 224})
#: The quantities the signed-load profile tags with the wiring's phase code.
_TOTAL_UOMS = frozenset({_UOM_W, _UOM_VAR, _UOM_VA, _UOM_A, _UOM_WH})
#: Slots only the multi-phase table gives a reading: the real power of lines 1, 2 and 3.
_MULTI_PHASE_ONLY_SLOTS = (10, 16, 22)

#: First slot of each fixed flowDirection's W and var readings, and the
#: offset of each W or var slot from it.
_DIRECTED_SLOT_BASE: dict[int, int] = {FLOW_NET: 31, FLOW_NORMAL: 41, FLOW_REVERSE: 51}
_DIRECTED_OFFSET: dict[int, int] = {1: 0, 2: 1, 10: 2, 11: 3, 16: 4, 17: 5, 22: 6, 23: 7}


# ---------------------------------------------------------------------------
# mRIDs
# ---------------------------------------------------------------------------


def compute_sfdi(lfdi: str) -> int:
    """Compute SFDI from LFDI using Luhn-like check digit.

    Args:
        lfdi: 40-character hex string (LFDI)

    Returns:
        SFDI as integer with check digit appended
    """
    # Take first 9 hex characters and convert to decimal
    sfdi_base = lfdi[:9]
    sfdi_str = str(int(sfdi_base, 16))

    # Compute Luhn-like check digit
    digit_sum = sum(int(d) for d in sfdi_str)
    check_digit = (10 - (digit_sum % 10)) % 10

    return int(sfdi_str + str(check_digit))


def _create_mrid(lfdi: str, index: int = 0, pen: int = DEFAULT_PEN) -> MRidtype:
    """Return the mRID of slot ``index`` for ``lfdi``, stable across calls.

    IEEE 2030.5-2023 §10.11.3 Rule h matches readings POSTs to existing
    MeterReadings by mRID, and Rule h.3 answers 400 to an unknown mRID that
    carries no ReadingType, so the same logical reading must always produce the
    same mRID.

    Two encodings share the 32-hex-char (128-bit) layout, SFDI first as twelve
    decimal digits:

    * Slots 0..9: one slot digit followed by 11 hex chars of LFDI.
    * Slots 10+: two slot digits followed by 10 hex chars of LFDI.
    """
    lfdi_norm = lfdi.lower()
    sfdi = compute_sfdi(lfdi_norm)
    if index < 10:
        salt_hex = lfdi_norm[:11]
        mrid_hex = f"{sfdi:012d}{index:01d}{salt_hex}{pen:08x}"
    else:
        salt_hex = lfdi_norm[:10]
        mrid_hex = f"{sfdi:012d}{index:02d}{salt_hex}{pen:08x}"
    return MRidtype(value=bytes.fromhex(mrid_hex))


#: Marks a hashed mRID. The slot layouts hold a decimal slot digit at this
#: position, so a hex letter there cannot match any slot mRID.
_HASHED_MARKER = "f"


def _hashed_mrid(lfdi: str, identity: ReadingIdentity, pen: int = DEFAULT_PEN) -> MRidtype:
    """Return the mRID of a reading with no 0.16 slot.

    Layout: SFDI (12 decimal digits), the marker, 11 hex chars of a SHA-256
    over the LFDI and the identity, and the PEN.
    """
    lfdi_norm = lfdi.lower()
    sfdi = compute_sfdi(lfdi_norm)
    text = "|".join([lfdi_norm, *("" if v is None else str(v) for v in identity)])
    digest = hashlib.sha256(text.encode("ascii")).hexdigest()[:11]
    return MRidtype(value=bytes.fromhex(f"{sfdi:012d}{_HASHED_MARKER}{digest}{pen:08x}"))


def mup_mrid(lfdi: str, pen: int = DEFAULT_PEN) -> bytes:
    """Return the mRID of the MirrorUsagePoint :func:`create_mup` builds for ``lfdi``."""
    return _create_mrid(lfdi.lower(), index=0, pen=pen).value


# ---------------------------------------------------------------------------
# Readings to MirrorMeterReadings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegisteredReading:
    """A MirrorMeterReading as registered in a MUP: its mRID, description and ReadingType.

    Kept by the caller after a successful MUP POST and passed back, so a re-POST
    repeats each ReadingType exactly as the server already holds it.
    """

    mrid: bytes
    description: str
    reading_type: ReadingType1
    #: Position in the MUP: 0.16 slot readings first, in slot order.
    order: tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class _Posted:
    """One MirrorMeterReading built from a reading this cycle."""

    mrid: bytes
    description: str
    order: tuple[int, int, int]
    reading: Reading
    #: Effective ReadingType fields.
    uom: int
    phase: int
    kind: int
    data_qualifier: int
    accumulation_behaviour: int
    multiplier: int
    commodity: int
    #: Fixed flowDirection, or None to take it from the sign at registration.
    flow_direction: int | None
    #: The line the reading measures, 1 to 3, or 0 for a total.
    line: int


def _description(identity: ReadingIdentity, flow: int | None) -> str:
    """Return a description for a reading with no 0.16 slot."""
    if identity.uom == _UOM_WH and identity.kind == 0:
        name = "State of Energy"
    else:
        name = _UOM_NAME.get(identity.uom, f"uom {identity.uom}")
    parts = [name]
    if identity.phase:
        parts.append(_PHASE_NAME.get(identity.phase, f"phase {identity.phase}"))
    if identity.data_qualifier in _DATA_QUALIFIER_NAME:
        parts.append(_DATA_QUALIFIER_NAME[identity.data_qualifier])
    if flow in _DIRECTION_SUFFIX:
        parts.append(_DIRECTION_SUFFIX[flow])
    return " ".join(parts)[:_DESCRIPTION_MAX]


def valid_readings(readings: Iterable[object]) -> list[Reading]:
    """Return the readings the library can post, each identity once.

    A connector is third-party code. An entry that is not a valid
    :class:`Reading` is logged and dropped, and so is a second reading with the
    identity of an earlier one, which would otherwise post two values under
    one mRID.
    """
    out: list[Reading] = []
    seen: set[ReadingIdentity] = set()
    for reading in readings:
        if not is_valid_reading(reading):
            logger.warning("Ignoring invalid reading %r", reading)
            continue
        assert isinstance(reading, Reading)
        identity = reading_identity(reading)
        if identity in seen:
            logger.warning("Ignoring a second reading with identity %s", identity)
            continue
        seen.add(identity)
        out.append(reading)
    return out


def _legacy_slot(
    legacy: tuple[int, str] | None, flow: int | None, own_flow: int | None
) -> int | None:
    """Return the 0.16 slot of a reading, or of its PowerDirection variant."""
    if legacy is None:
        return None
    if flow == own_flow:
        return legacy[0]
    offset = _DIRECTED_OFFSET.get(legacy[0])
    if offset is None or flow is None:
        return None
    return _DIRECTED_SLOT_BASE[flow] + offset


def _line_of(reading: Reading, legacy: tuple[int, str] | None) -> int:
    """Return the line a reading measures, 1 to 3, or 0 for a total.

    A reading in a 0.16 slot takes the line of its slot, so a single-phase
    total tagged phase A stays a total.
    """
    if legacy is not None:
        slot = legacy[0]
        return (slot - 10) // 6 + 1 if 10 <= slot <= 27 else 0
    return _LINE_OF_PHASE.get(reading.phase, 0)


def _is_multi_phase(
    lfdi: str,
    readings: Sequence[Reading],
    registered: Mapping[bytes, RegisteredReading] | None,
) -> bool:
    """Return whether the device is wired for more than one phase.

    Phase 128 is line 1 of a multi-phase device and the total of a single-phase
    one, so which it is has to come from something that does not change when a
    value fails to read. A device is multi-phase once its MirrorUsagePoint has
    registered a reading only a multi-phase device has: line 2 or 3, line 1's
    real power, or a total tagged with a split- or three-phase code. A poll
    that shows any of those says the same. Only a device that has shown none
    is single phase, so a poll that loses lines 2 and 3 never turns a
    three-phase device into a single-phase one.
    """
    for reading in readings:
        if reading.phase in _LINE_2_3_PHASES:
            return True
        if reading.phase in _MULTI_PHASE_TOTAL_PHASES and reading.uom in _TOTAL_UOMS:
            return True
    if not registered:
        return False
    if any(_create_mrid(lfdi, index=slot).value in registered for slot in _MULTI_PHASE_ONLY_SLOTS):
        return True
    for entry in registered.values():
        phase = entry.reading_type.phase.value if entry.reading_type.phase is not None else 0
        if phase in _LINE_2_3_PHASES:
            return True
        uom = entry.reading_type.uom.value if entry.reading_type.uom is not None else 0
        if phase in _MULTI_PHASE_TOTAL_PHASES and uom in _TOTAL_UOMS:
            return True
    return False


def _posted_readings(
    lfdi: str,
    readings: Sequence[Reading],
    profile: ReadingProfile,
    power_direction: PowerDirection,
    registered: Mapping[bytes, RegisteredReading] | None = None,
) -> list[_Posted]:
    """Return the MirrorMeterReadings ``readings`` post, in MUP order.

    ``registered`` is what the MirrorUsagePoint has registered; under
    SIGNED_LOAD_CONVENTION it, with the readings, says whether the device is
    single or multi-phase (see :func:`_is_multi_phase`).
    """
    signed_load = profile is ReadingProfile.SIGNED_LOAD_CONVENTION
    valid = valid_readings(readings)
    table = _DEFAULT_SLOTS
    if signed_load:
        multi_phase = _is_multi_phase(lfdi, valid, registered)
        table = _SIGNED_LOAD_SLOTS if multi_phase else _SIGNED_LOAD_SLOTS_SINGLE_PHASE
    defaults = _SIGNED_LOAD_MULTIPLIER if signed_load else DEFAULT_MULTIPLIER
    directions: tuple[int, ...] = ()
    if not signed_load and power_direction is PowerDirection.NET:
        directions = (FLOW_NET,)
    elif not signed_load and power_direction is PowerDirection.SPLIT:
        directions = (FLOW_NORMAL, FLOW_REVERSE)

    out: list[_Posted] = []
    seen: set[bytes] = set()
    for position, reading in enumerate(valid):
        identity = reading_identity(reading, defaults)
        legacy = table.get(identity)
        variants: list[tuple[int | None, ReadingIdentity]]
        if directions and reading.uom in _DIRECTED_UOMS and reading.flow_direction is None:
            variants = [
                (direction, identity._replace(flow_direction=direction)) for direction in directions
            ]
        else:
            variants = [(reading.flow_direction, identity)]
        for rank, (flow, variant) in enumerate(variants):
            slot = _legacy_slot(legacy, flow, identity.flow_direction)
            if slot is not None and legacy is not None:
                mrid = _create_mrid(lfdi, index=slot).value
                description = legacy[1]
                if flow != identity.flow_direction and flow is not None:
                    description = f"{description} {_DIRECTION_SUFFIX[flow]}"
                order = (0, legacy[0], rank)
            else:
                mrid = _hashed_mrid(lfdi, variant).value
                description = _description(variant, flow)
                order = (1, position, rank)
            if mrid in seen:
                logger.warning(
                    "Reading %s for %s has the same mRID as one already posted this cycle; "
                    "its value %s is not posted",
                    description,
                    lfdi[:8],
                    reading.value,
                )
                continue
            seen.add(mrid)
            out.append(
                _Posted(
                    mrid=mrid,
                    description=description,
                    order=order,
                    reading=reading,
                    uom=reading.uom,
                    phase=reading.phase,
                    kind=0 if signed_load else reading.kind,
                    data_qualifier=0 if signed_load else reading.data_qualifier,
                    accumulation_behaviour=reading.accumulation_behaviour,
                    multiplier=identity.multiplier,
                    commodity=0 if signed_load else 1,
                    flow_direction=(
                        flow if flow is not None else (FLOW_NOT_APPLICABLE if signed_load else None)
                    ),
                    line=_line_of(reading, legacy),
                )
            )
    out.sort(key=lambda p: p.order)
    return out


def _scale_truncated(raw: float, multiplier: int) -> int:
    """``raw x 10^-multiplier`` truncated toward zero, as release 0.16 scaled DEFAULT values."""
    if multiplier <= 0:
        return int(raw * 10**-multiplier)
    return int(raw / 10**multiplier)


def _scale_half_away(raw: float, multiplier: int) -> int:
    """``raw x 10^-multiplier`` rounded half away from zero, in decimal.

    Scaling the float directly misrounds decimal ties: 1.005 is stored as
    1.00499..., so x100 rounds to 100. ``str`` gives the shortest decimal that
    round-trips, which is the value the device reported.
    """
    scaled = Decimal(str(raw)).scaleb(-multiplier)
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))


def _registration_flow(posted: _Posted) -> int:
    """Return the flowDirection a reading registers with under DEFAULT."""
    if posted.flow_direction is not None:
        return posted.flow_direction
    if posted.uom in _SIGNED_UOMS and _scale_truncated(posted.reading.value, posted.multiplier) < 0:
        return FLOW_REVERSE
    return FLOW_NORMAL


def _reading_type(posted: _Posted, flow_direction: int) -> ReadingType1:
    return ReadingType1(
        accumulation_behaviour=AccumulationBehaviourType(value=posted.accumulation_behaviour),
        commodity=CommodityType(value=posted.commodity),
        data_qualifier=DataQualifierType(value=posted.data_qualifier),
        flow_direction=FlowDirectionType(value=flow_direction),
        kind=KindType(value=posted.kind),
        power_of_ten_multiplier=PowerOfTenMultiplierType(value=posted.multiplier),
        uom=UomType(value=posted.uom),
        phase=PhaseCode(value=posted.phase) if posted.phase else None,
    )


def plan_registration(
    lfdi: str,
    readings: Sequence[Reading],
    *,
    profile: ReadingProfile = ReadingProfile.DEFAULT,
    power_direction: PowerDirection = PowerDirection.DEFAULT,
    registered: Mapping[bytes, RegisteredReading] | None = None,
) -> dict[bytes, RegisteredReading]:
    """Return the MirrorMeterReadings a MUP built now registers, keyed by mRID.

    Every reading in ``registered`` is kept with its ReadingType unchanged, so a
    re-POST never drops a reading that is absent this cycle and never changes a
    ReadingType the server holds (Rule a.4 writes the new MUP over the old one,
    and Rule n forbids the change). A new reading is added with the ReadingType
    it has now.
    """
    plan: dict[bytes, RegisteredReading] = dict(registered or {})
    for posted in _posted_readings(lfdi.lower(), readings, profile, power_direction, registered):
        if posted.mrid in plan:
            continue
        flow = (
            posted.flow_direction
            if profile is ReadingProfile.SIGNED_LOAD_CONVENTION
            else _registration_flow(posted)
        )
        assert flow is not None
        plan[posted.mrid] = RegisteredReading(
            mrid=posted.mrid,
            description=posted.description,
            reading_type=_reading_type(posted, flow),
            order=posted.order,
        )
    return dict(sorted(plan.items(), key=lambda item: item[1].order))


def create_mup(
    lfdi: str,
    readings: Sequence[Reading],
    post_rate: int,
    *,
    profile: ReadingProfile = ReadingProfile.DEFAULT,
    registered: Mapping[bytes, RegisteredReading] | None = None,
    power_direction: PowerDirection = PowerDirection.DEFAULT,
    role: DeviceRole = DeviceRole.DER,
    revenue_quality: bool = False,
    device_lfdi: str | None = None,
) -> MirrorUsagePoint:
    """Create a MirrorUsagePoint registering a MirrorMeterReading per reading.

    Args:
        lfdi: Device LFDI. Every mRID is derived from it, so two devices
              mirrored under one ``device_lfdi`` never share an mRID.
        readings: The connector's readings this cycle.
        post_rate: Posting rate in seconds.
        profile: How the readings are encoded (see :class:`ReadingProfile`).
        registered: What an earlier POST of this MUP registered (see
              :func:`plan_registration`). Those readings are kept, with their
              ReadingTypes unchanged, even when absent from ``readings``.
        power_direction: How W and var carry their direction under the
              DEFAULT profile (see :class:`PowerDirection`). Ignored under
              SIGNED_LOAD_CONVENTION, which signs every value.
        role: What the device measures; sets roleFlags (see
              :class:`DeviceRole`).
        revenue_quality: Set isRevenueQuality in roleFlags.
        device_lfdi: LFDI of the EndDevice the MUP is posted under, put in
              deviceLFDI. Defaults to ``lfdi``.
    """
    lfdi_norm = lfdi.lower()
    plan = plan_registration(
        lfdi_norm,
        readings,
        profile=profile,
        power_direction=power_direction,
        registered=registered,
    )
    meter_readings = [
        MirrorMeterReading1(
            m_rid=MRidtype(value=entry.mrid),
            description=entry.description,
            reading_type=entry.reading_type,
        )
        for entry in plan.values()
    ]
    return MirrorUsagePoint(
        m_rid=_create_mrid(lfdi_norm, index=0),
        description=f"{_ROLE_DESCRIPTION[role]} {lfdi_norm[:8]}",
        version=VersionType(value=1),
        role_flags=RoleFlagsType(value=role_flags_for(role, revenue_quality).to_bytes(2, "big")),
        service_category_kind=ServiceKind(value=SERVICE_CATEGORY_ELECTRICITY),
        status=STATUS_ON,
        device_lfdi=bytes.fromhex((device_lfdi or lfdi_norm).lower()),
        mirror_meter_reading=meter_readings,
        post_rate=post_rate,
    )


def _quality_flags(reading: Reading, *, stale: bool) -> bytes:
    """Return qualityFlags (HexBinary16) for a reading.

    The connector's flags, or ``valid`` when it gave none. ``stale`` is OR-ed on
    top rather than replacing them: a connector reporting ``valid`` asserts its
    read succeeded, and the client knowing the sample is older than anyone
    asked for is a different claim. Staleness sets *questionable*, clears
    *valid*, and leaves every other connector bit intact.
    """
    resolved = QUALITY_FLAGS_VALID if reading.quality_flags is None else reading.quality_flags
    if stale:
        resolved = (resolved | QUALITY_FLAGS_QUESTIONABLE) & ~QUALITY_FLAGS_VALID
    return resolved.to_bytes(2, "big")


def _default_value(posted: _Posted) -> int:
    """Return a DEFAULT-profile value: a magnitude, or signed under Net."""
    scaled = _scale_truncated(posted.reading.value, posted.multiplier)
    flow = posted.flow_direction
    if flow == FLOW_NET:
        return scaled
    if flow == FLOW_NORMAL and posted.reading.flow_direction is None:
        return max(scaled, 0)
    if flow == FLOW_REVERSE and posted.reading.flow_direction is None:
        return max(-scaled, 0)
    return abs(scaled)


def _signed_load_value(posted: _Posted, var_by_line: Mapping[int, int], role: DeviceRole) -> int:
    """Return a SIGNED_LOAD_CONVENTION value scaled from the effective multiplier.

    W and var are signed by ``role``. A DER and a DER meter report them
    positive for the DER's output (generation and reactive injection), so both
    are negated. A site meter reports them positive for import, which is
    already the load convention, so both are kept. PF takes the sign of the var
    posted on the same line -- negative on reactive injection -- and stays an
    unsigned magnitude when that var is missing. A direction-specific reading
    and everything else is a magnitude.
    """
    scaled = _scale_half_away(posted.reading.value, posted.multiplier)
    if posted.reading.flow_direction is not None:
        return abs(scaled)
    if posted.uom in _DIRECTED_UOMS:
        return scaled if role is DeviceRole.SITE_METER else -scaled
    if posted.uom == _UOM_PF:
        var = var_by_line.get(posted.line)
        return -abs(scaled) if var is not None and var < 0 else abs(scaled)
    return abs(scaled)


def create_meter_reading_list(
    lfdi: str,
    readings: Sequence[Reading],
    timestamp: int | None = None,
    *,
    post_rate: int = 300,
    next_update_time: int | None = None,
    stale: bool = False,
    profile: ReadingProfile = ReadingProfile.DEFAULT,
    registered: Mapping[bytes, RegisteredReading] | None = None,
    power_direction: PowerDirection = PowerDirection.DEFAULT,
    role: DeviceRole = DeviceRole.DER,
) -> MirrorMeterReadingList:
    """Create a MirrorMeterReadingList with this cycle's readings.

    Used when POSTing readings to an existing MUP. Only the readings the
    connector supplied are posted, so an unmeasured quantity never shows up as
    a misleading ``0``. Posting a subset of the registered MirrorMeterReadings
    is valid per IEEE 2030.5 §10.11.3, and each mRID is fixed by the reading's
    identity, so a quantity that resumes reporting matches its registered
    ReadingType (Rule h.1). The ReadingType itself is not included (Rule n).

    ``timestamp`` is the instant the values were *acquired*. It becomes
    ``timePeriod.start`` and ``lastUpdateTime``. ``next_update_time`` is when
    this MirrorMeterReading will next be *posted*, so a server can detect a
    stalled feed without waiting out the 72-hour MUP timeout; ``None`` when the
    caller cannot say.

    ``stale`` marks the sample older than its consumers asked for, or from an
    unreachable device (see :func:`_quality_flags`).

    ``registered``, when given, limits the list to readings the MUP has
    registered, so a reading whose registration has not succeeded yet is held
    back rather than met with Rule h.3. ``profile``, ``power_direction`` and
    ``role`` must be the ones the MUP was created with.

    An interval reading (Average, Maximum, Minimum) covers a window that
    CSIP-AUS requires to match the MUP postRate, so its duration is
    ``post_rate``; an instantaneous reading is a point in time, duration 0.
    Under SIGNED_LOAD_CONVENTION every reading is an instantaneous sample.
    """
    if timestamp is None:
        timestamp = int(time.time())

    lfdi_norm = lfdi.lower()
    signed_load = profile is ReadingProfile.SIGNED_LOAD_CONVENTION
    posted_list = _posted_readings(lfdi_norm, readings, profile, power_direction, registered)

    var_by_line: dict[int, int] = {}
    if signed_load:
        for posted in posted_list:
            if posted.uom == _UOM_VAR and posted.reading.flow_direction is None:
                var_by_line.setdefault(posted.line, _signed_load_value(posted, {}, role))

    meter_readings: list[MirrorMeterReading1] = []
    for posted in posted_list:
        if registered is not None and posted.mrid not in registered:
            continue
        if signed_load:
            value = _signed_load_value(posted, var_by_line, role)
            duration = 0
        else:
            value = _default_value(posted)
            duration = 0 if posted.data_qualifier == _DATA_QUALIFIER_INSTANTANEOUS else post_rate
        reading = Reading1(
            quality_flags=_quality_flags(posted.reading, stale=stale),
            time_period=DateTimeInterval(duration=duration, start=TimeType(value=timestamp)),
            value=value,
        )
        meter_readings.append(
            MirrorMeterReading1(
                m_rid=MRidtype(value=posted.mrid),
                description=posted.description,
                reading=reading,
                # IEEE 2030.5 Annex B.17.1. lastUpdateTime is when the data
                # behind this reading was refreshed, nextUpdateTime is when the
                # mirror will next be written.
                last_update_time=TimeType(value=timestamp),
                next_update_time=(
                    None if next_update_time is None else TimeType(value=next_update_time)
                ),
            )
        )

    return MirrorMeterReadingList(
        mirror_meter_reading=meter_readings,
        all=len(meter_readings),
        results=len(meter_readings),
    )


__all__ = [
    "DEFAULT_PEN",
    "DeviceRole",
    "PowerDirection",
    "ReadingProfile",
    "RegisteredReading",
    "compute_sfdi",
    "create_meter_reading_list",
    "create_mup",
    "mup_mrid",
    "plan_registration",
    "role_flags_for",
    "valid_readings",
]
