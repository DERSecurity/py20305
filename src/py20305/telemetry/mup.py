"""MirrorUsagePoint creation for IEEE 2030.5 telemetry.

Creates MUP resources with system-wide MirrorMeterReadings (7 specs:
W/Var/Hz/V/PF/VA/A) and, on multi-phase devices, an additional set of
per-line readings for each phase advertised by the inverter (W/Var/V/PF/
VA/A per line; Hz is grid-wide and stays system-only).
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

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
from py20305.telemetry.scaling import (
    FLOW_NOT_APPLICABLE,
    ScaledReading,
    scale_a,
    scale_hz,
    scale_pf,
    scale_v,
    scale_va,
    scale_var,
    scale_w,
)

if TYPE_CHECKING:
    from py20305.connectors.base import ReadingOverride

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

# SunSpec model 701 ACType enum -> count of distinct lines.
# (SunSpec uses 0/1/2 here, NOT IEEE 2030.5 phaseCode bits.)
_AC_TYPE_LINE_COUNT: dict[int, int] = {
    0: 1,  # SINGLE_PHASE  -- only system readings (line 1 == system)
    1: 2,  # SPLIT_PHASE   -- L1 + L2
    2: 3,  # THREE_PHASE   -- L1 + L2 + L3
}

# IEEE 2030.5 PhaseCode values per IEC 61968-9 Annex C / sep2 Annex E.
# Per-line readings use the line-to-neutral encoding for voltage (the
# neutral bit is set) and the phase-only encoding for everything else.
# This matches how production EMS and meter-data implementations tag
# their MirrorMeterReadings.
_PER_LINE_PHASE_CODES: dict[int, tuple[int, int]] = {
    # line: (V phase code, non-V phase code)
    1: (129, 128),  # phase A-N / phase A
    2: (65, 64),  # phase B-N / phase B
    3: (33, 32),  # phase C-N / phase C
}


@dataclass(frozen=True, slots=True)
class ReadingTypeSpec:
    """Specification for a MirrorMeterReading's ReadingType."""

    key: str  # Metric key in monitoring data (W, Var, Hz, etc.)
    description: str  # Human-readable description
    commodity: int
    data_qualifier: int
    kind: int
    uom: int
    multiplier: int
    phase: int | None = None
    scale_func: Callable[[float | None], ScaledReading] | None = None
    # AccumulationBehaviourType: 12 = Instantaneous. Correct for every reading
    # we publish (all are point-in-time samples). Accumulated quantities -- e.g.
    # energy (Wh) -- would instead use 9 (Summation); override per-spec if added.
    accumulation_behaviour: int = 12


# ReadingType specifications per IEEE 2030.5 and spec doc
# accumulationBehaviour: 12 = Instantaneous (set on every spec via the default)
# dataQualifier: 12 = Normal, 2 = Average
# kind: 37 = Power, 12 = Energy, 0 = None
# uom: 38=W, 63=Var, 33=Hz, 29=V, 65=PF, 61=VA, 5=A
READING_TYPE_SPECS: tuple[ReadingTypeSpec, ...] = (
    ReadingTypeSpec(
        key="W",
        description="Real Power",
        commodity=1,
        data_qualifier=12,
        kind=37,
        uom=38,
        multiplier=0,
        scale_func=scale_w,
    ),
    ReadingTypeSpec(
        key="Var",
        description="Reactive Power",
        commodity=1,
        data_qualifier=12,
        kind=37,
        uom=63,
        multiplier=0,
        scale_func=scale_var,
    ),
    ReadingTypeSpec(
        key="Hz",
        description="Frequency",
        commodity=1,
        data_qualifier=12,
        kind=37,
        uom=33,
        multiplier=-3,
        scale_func=scale_hz,
    ),
    ReadingTypeSpec(
        key="V",
        description="Voltage",
        commodity=1,
        data_qualifier=2,  # average, not instantaneous
        kind=37,
        uom=29,
        multiplier=-1,
        # No phase tag. SunSpec reads model 701 LLV (line-to-line average),
        # not LNV, so the historical phase=129 (Phase A-N) value was wrong
        # regardless of ACType -- a line-to-line measurement was being
        # advertised as if it were phase-A-to-neutral. The IEEE 2030.5
        # PhaseCode enum treats an absent attribute as 0 ("Not Applicable,
        # default if not specified"), which honestly describes an
        # aggregate-across-conductors measurement.
        scale_func=scale_v,
    ),
    ReadingTypeSpec(
        key="PF",
        description="Power Factor",
        commodity=1,
        data_qualifier=12,
        kind=0,  # kind=0 for PF
        uom=65,
        multiplier=-3,
        scale_func=scale_pf,
    ),
    ReadingTypeSpec(
        key="VA",
        description="Apparent Power",
        commodity=1,
        data_qualifier=12,
        kind=37,
        uom=61,
        multiplier=0,
        scale_func=scale_va,
    ),
    ReadingTypeSpec(
        key="A",
        description="Current",
        commodity=1,
        data_qualifier=12,
        kind=37,
        uom=5,
        multiplier=-1,
        scale_func=scale_a,
    ),
)


def _per_line_specs(line: int) -> tuple[ReadingTypeSpec, ...]:
    """Build the 6 per-line ReadingTypeSpecs for a given line number.

    Per-line monitoring keys follow SunSpec model 701 naming: ``WL{n}``,
    ``VarL{n}``, ``VL{n}``, ``PFL{n}``, ``VAL{n}``, ``AL{n}``. Voltage
    carries the line-to-neutral PhaseCode (neutral bit set); the rest
    carry the phase-only PhaseCode.
    """
    v_phase, other_phase = _PER_LINE_PHASE_CODES[line]
    return (
        ReadingTypeSpec(
            key=f"WL{line}",
            description=f"Real Power L{line}",
            commodity=1,
            data_qualifier=12,
            kind=37,
            uom=38,
            multiplier=0,
            phase=other_phase,
            scale_func=scale_w,
        ),
        ReadingTypeSpec(
            key=f"VarL{line}",
            description=f"Reactive Power L{line}",
            commodity=1,
            data_qualifier=12,
            kind=37,
            uom=63,
            multiplier=0,
            phase=other_phase,
            scale_func=scale_var,
        ),
        ReadingTypeSpec(
            key=f"VL{line}",
            description=f"Voltage L{line}",
            commodity=1,
            data_qualifier=2,
            kind=37,
            uom=29,
            multiplier=-1,
            phase=v_phase,
            scale_func=scale_v,
        ),
        ReadingTypeSpec(
            key=f"PFL{line}",
            description=f"Power Factor L{line}",
            commodity=1,
            data_qualifier=12,
            kind=0,
            uom=65,
            multiplier=-3,
            phase=other_phase,
            scale_func=scale_pf,
        ),
        ReadingTypeSpec(
            key=f"VAL{line}",
            description=f"Apparent Power L{line}",
            commodity=1,
            data_qualifier=12,
            kind=37,
            uom=61,
            multiplier=0,
            phase=other_phase,
            scale_func=scale_va,
        ),
        ReadingTypeSpec(
            key=f"AL{line}",
            description=f"Current L{line}",
            commodity=1,
            data_qualifier=12,
            kind=37,
            uom=5,
            multiplier=-1,
            phase=other_phase,
            scale_func=scale_a,
        ),
    )


# Per-line index allocation: each line gets 6 contiguous indices starting
# at 10. L1=10..15, L2=16..21, L3=22..27. Indices 0..7 stay system
# (MUP=0, W=1..A=7); 8 and 9 are reserved so an mRID generated for any
# new "system reading slot" added in the future also lands in the
# legacy single-digit encoding band.
_PER_LINE_INDEX_BASE = 10
_PER_LINE_SLOT_COUNT = 6


def _per_line_index(line: int, slot: int) -> int:
    return _PER_LINE_INDEX_BASE + (line - 1) * _PER_LINE_SLOT_COUNT + slot


class ReadingProfile(StrEnum):
    """How a device's MirrorMeterReadings are encoded.

    ``DEFAULT`` is the historical encoding. ``SIGNED_LOAD_CONVENTION`` carries
    the sign on every value (flowDirection 0) using the load convention --
    active power negative on export, reactive power negative when the DER
    injects -- with commodity, dataQualifier and kind left Not Applicable, and
    adds line-to-line voltage and State of Energy readings when the device
    supplies them.
    """

    DEFAULT = "default"
    SIGNED_LOAD_CONVENTION = "signed_load_convention"


# SIGNED_LOAD_CONVENTION only. The slots sit outside every DEFAULT slot, so a
# device switched between profiles keeps all of its existing mRIDs.
_STATE_OF_ENERGY_SPEC: tuple[int, ReadingTypeSpec] = (
    8,
    ReadingTypeSpec(
        key="WHAvail",
        description="State of Energy",
        commodity=0,
        data_qualifier=0,
        kind=0,
        uom=72,
        multiplier=0,
    ),
)
_LINE_TO_LINE_SPECS: tuple[tuple[int, ReadingTypeSpec], ...] = tuple(
    (
        slot,
        ReadingTypeSpec(
            key=key,
            description=description,
            commodity=0,
            data_qualifier=0,
            kind=0,
            uom=29,
            multiplier=-1,
            phase=phase,
        ),
    )
    for slot, key, description, phase in (
        (28, "VL1L2", "Voltage L1-L2", 132),
        (29, "VL2L3", "Voltage L2-L3", 66),
        (30, "VL3L1", "Voltage L3-L1", 40),
    )
)
# Line-to-line readings a device can have, by SunSpec ACType: split-phase has
# only L1-L2.
_AC_TYPE_LINE_TO_LINE_COUNT: dict[int, int] = {0: 0, 1: 1, 2: 3}
# Phase code for the aggregate readings, by SunSpec ACType. IEEE 2030.5 has no
# S1-S2 code, and per-line readings already tag split-phase L2 as phase B, so
# split-phase totals are AB.
_AC_TYPE_TOTAL_PHASE: dict[int, int] = {0: 128, 1: 132, 2: 224}
#: Every slot a SIGNED_LOAD_CONVENTION MUP can register, so a re-POST can
#: rebuild any slot registered earlier whatever this cycle's data says.
_SPEC_BY_SLOT: dict[int, ReadingTypeSpec] = {
    **dict(enumerate(READING_TYPE_SPECS, start=1)),
    **{
        _per_line_index(line, slot): spec
        for line in (1, 2, 3)
        for slot, spec in enumerate(_per_line_specs(line))
    },
    **dict((_STATE_OF_ENERGY_SPEC, *_LINE_TO_LINE_SPECS)),
}
_TOTAL_KEYS = frozenset({"W", "Var", "VA", "A", "WHAvail"})
_PER_LINE_KEY = re.compile(r"^(W|Var|V|PF|VA|A)L[1-3]$")
_NEGATED_QUANTITIES = frozenset({"W", "Var"})


def _ac_type(monitoring_data: dict[str, Any]) -> int | None:
    ac_type = monitoring_data.get("ACType")
    if isinstance(ac_type, bool) or not isinstance(ac_type, int):
        return None
    return ac_type


def _quantity(key: str) -> str:
    """The system key a per-line key measures (``"VarL2"`` -> ``"Var"``)."""
    match = _PER_LINE_KEY.match(key)
    return match.group(1) if match else key


def _signed_load_spec(spec: ReadingTypeSpec, ac_type: int | None) -> ReadingTypeSpec:
    phase = _AC_TYPE_TOTAL_PHASE.get(ac_type) if ac_type is not None else None
    return replace(
        spec,
        commodity=0,
        data_qualifier=0,
        kind=0,
        phase=phase if spec.key in _TOTAL_KEYS else spec.phase,
        # Whole amperes is part of the profile's specification, not a precision
        # oversight: 2.5 A posts 3. A connector that needs tenths sets a
        # multiplier override, which the scaling honors.
        multiplier=0 if _quantity(spec.key) == "A" else spec.multiplier,
    )


def _signed_load_specs_for(
    monitoring_data: dict[str, Any], registered: frozenset[int]
) -> list[tuple[int, ReadingTypeSpec]]:
    """``(mrid_index, spec)`` for a SIGNED_LOAD_CONVENTION device.

    The DEFAULT set, plus each optional reading the device supplies this cycle,
    plus every slot registered earlier. Keeping registered slots means a MUP
    re-POST never drops a reading that is merely absent this cycle -- an
    optional reading, or a per-line block whose ACType is missing -- since
    Rule a.4 writes the new MUP over the old one.
    """
    ac_type = _ac_type(monitoring_data)
    line_to_line = _AC_TYPE_LINE_TO_LINE_COUNT.get(ac_type, 0) if ac_type is not None else 0
    candidates = [
        (_STATE_OF_ENERGY_SPEC, True),
        *((entry, n < line_to_line) for n, entry in enumerate(_LINE_TO_LINE_SPECS)),
    ]
    by_slot = dict(_all_specs_for(monitoring_data))
    by_slot |= {
        slot: spec
        for (slot, spec), allowed in candidates
        if allowed and monitoring_data.get(spec.key) is not None
    }
    by_slot |= {slot: _SPEC_BY_SLOT[slot] for slot in registered if slot in _SPEC_BY_SLOT}
    return [(index, _signed_load_spec(spec, ac_type)) for index, spec in sorted(by_slot.items())]


def registration_slots(
    monitoring_data: dict[str, Any],
    profile: ReadingProfile,
    registered: frozenset[int] = frozenset(),
) -> frozenset[int]:
    """The mRID slots a MUP built now would register.

    Under SIGNED_LOAD_CONVENTION the set grows when an optional reading first
    appears, which is the signal to re-POST the MUP so the new mRID arrives
    with its ReadingType (Rule h.2) instead of meeting Rule h.3.
    """
    if profile is ReadingProfile.SIGNED_LOAD_CONVENTION:
        specs = _signed_load_specs_for(monitoring_data, registered)
    else:
        specs = _all_specs_for(monitoring_data)
    return frozenset(index for index, _ in specs)


def _scale_half_away(raw: float, multiplier: int) -> int:
    """``raw x 10^-multiplier`` rounded half away from zero, in decimal.

    Scaling the float directly misrounds decimal ties: 1.005 is stored as
    1.00499..., so x100 rounds to 100. ``str`` gives the shortest decimal that
    round-trips, which is the value the device reported.
    """
    scaled = Decimal(str(raw)).scaleb(-multiplier)
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))


def _signed_load_value(
    spec: ReadingTypeSpec, monitoring_data: dict[str, Any], posted: dict[str, int]
) -> int:
    """Scale a SIGNED_LOAD_CONVENTION value from the effective multiplier.

    Connectors report W and var DER-referenced (positive for generation and
    for injection), so both are negated. PF takes the sign of the var posted
    with it -- negative when the DER injects -- and stays an unsigned
    magnitude when that var is missing. Everything else is a magnitude.
    """
    scaled = _scale_half_away(monitoring_data[spec.key], spec.multiplier)
    quantity = _quantity(spec.key)
    if quantity in _NEGATED_QUANTITIES:
        return -scaled
    if quantity == "PF":
        var = posted.get("Var" + spec.key[2:])
        return -abs(scaled) if var is not None and var < 0 else abs(scaled)
    return abs(scaled)


def _line_count_for(monitoring_data: dict[str, Any]) -> int:
    """How many distinct AC lines does the device advertise?

    Reads ``ACType`` from monitoring data (SunSpec model 701 enum). Falls
    back to 1 (i.e. no per-line readings appended) if the connector
    didn't supply ``ACType`` -- the historical behaviour. Any value
    outside the known 0/1/2 range is logged via the same fallback so
    we don't crash on a forward-compatible device firmware.
    """
    ac_type = monitoring_data.get("ACType")
    if not isinstance(ac_type, int):
        return 1
    return _AC_TYPE_LINE_COUNT.get(ac_type, 1)


def _all_specs_for(monitoring_data: dict[str, Any]) -> list[tuple[int, ReadingTypeSpec]]:
    """Return the ordered list of ``(mrid_index, spec)`` for this device.

    System readings are always present (indices 1..7). Per-line readings
    are appended for every line the device advertises: SPLIT_PHASE emits
    L1+L2, THREE_PHASE emits L1+L2+L3. A SINGLE_PHASE (or ACType-less)
    device emits none -- there the system readings genuinely *are* L1, so a
    second copy would just bloat the MUP.

    L1 is NOT folded into the system readings on a multi-phase device: the
    system readings are aggregates, not phase-A values. ``W``/``Var``/``VA``/
    ``A`` are totals across phases, and ``V`` is SunSpec 701 ``LLV`` -- a
    line-to-line *average* carrying no PhaseCode. A consumer keying on
    PhaseCode 129 (Phase AN, i.e. L1-N voltage) therefore never sees an L1
    reading unless it is emitted explicitly.
    """
    result: list[tuple[int, ReadingTypeSpec]] = []
    for i, spec in enumerate(READING_TYPE_SPECS, start=1):
        result.append((i, spec))

    line_count = _line_count_for(monitoring_data)
    # Multi-phase: emit L1..N. Single-phase (line_count == 1): emit nothing,
    # since range(2, 2) is empty and the system readings already are L1.
    first_line = 1 if line_count >= 2 else 2
    for line in range(first_line, line_count + 1):
        for slot, spec in enumerate(_per_line_specs(line)):
            result.append((_per_line_index(line, slot), spec))
    return result


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
    """Create an IEEE 2030.5 mRID stable across calls for the same
    ``(lfdi, index, pen)`` triple.

    IEEE 2030.5-2023 §10.11.3 Rule h matches readings POSTs to existing
    MeterReadings by mRID:

    * h.1: matching mRID -> overwrite the existing MeterReading.
    * h.2: non-matching mRID + has ReadingType -> create new MeterReading.
    * h.3: non-matching mRID + no ReadingType -> 400 Bad Request.

    Rule n further says ReadingType SHOULD NOT be included in subsequent
    POSTs. So a readings POST that re-generates mRIDs from the wall clock
    looks like "all-new mRIDs, no ReadingType" to the server -- which is
    exactly the Rule h.3 case, and a strict server (Envoy, third-party
    parsers) will return 400. This version derives the mRID solely from
    ``(lfdi, index, pen)`` so the same logical reading always produces
    the same mRID.

    Two encodings share the 32-hex-char (128-bit) layout:

    * Indices 0..9 (MUP + the seven system readings): single-digit slot
      followed by 11 hex chars of LFDI. This is the legacy encoding;
      preserved so MUPs already posted to a server under the original
      7-reading scheme keep matching after the per-line readings ship.
    * Indices 10+ (per-line readings): two-digit slot followed by 10
      hex chars of LFDI. One fewer salt char buys the extra index digit
      without changing the total length.
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


# ReadingOverride fields that map onto ReadingTypeSpec attributes. All are
# non-negative int enum codes EXCEPT ``multiplier`` (powerOfTenMultiplier), which
# is signed (IEEE 2030.5: -9..9); invalid values are ignored (default kept).
_OVERRIDABLE_SPEC_FIELDS = (
    "accumulation_behaviour",
    "data_qualifier",
    "kind",
    "commodity",
    "multiplier",
    "uom",
    "phase",
)


def _effective_spec(spec: ReadingTypeSpec, override: ReadingOverride | None) -> ReadingTypeSpec:
    """Merge a connector's per-field ReadingType overrides onto the base spec.

    Each overridden field must be an int -- non-negative for the enum codes, and
    in ``-9..9`` for ``multiplier`` (powerOfTenMultiplier is signed). An invalid
    value is logged and skipped so the default stays in place (one bad field can't
    break the MUP).
    """
    if override is None:
        return spec
    # dict[str, Any] (not int): dataclasses.replace's typed kwargs reject a
    # **dict[str, int] spread because some fields (scale_func) aren't ints.
    changes: dict[str, Any] = {}
    for field_name in _OVERRIDABLE_SPEC_FIELDS:
        # getattr default guards against a non-ReadingOverride value. Both
        # acquisition paths filter these per value through
        # ``readings.typed_overrides``, so this is a belt-and-braces check
        # rather than the only one -- it still covers callers that build an
        # override map and call the builders directly.
        value = getattr(override, field_name, None)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            logger.warning(
                "Ignoring invalid %s override %r for reading %s (expected int)",
                field_name,
                value,
                spec.key,
            )
            continue
        # powerOfTenMultiplier is signed (-9..9); the rest are non-negative codes.
        in_range = (-9 <= value <= 9) if field_name == "multiplier" else (value >= 0)
        if not in_range:
            logger.warning(
                "Ignoring out-of-range %s override %r for reading %s",
                field_name,
                value,
                spec.key,
            )
            continue
        changes[field_name] = value
    return replace(spec, **changes) if changes else spec


def _resolve_quality_flags(
    override: ReadingOverride | None, per_cycle: object = None, *, stale: bool = False
) -> bytes:
    """qualityFlags (HexBinary16) for a reading.

    Precedence among *connector-supplied* opinions: a per-cycle value attached
    to this sample (``"<key>__quality"`` in ``fetch_monitoring``) > the
    connector's static per-key ``quality_flags`` override > the default
    ``valid`` bit. Each candidate must be a 16-bit int; an invalid one is logged
    and the next candidate is tried. The ``getattr`` default and the ``object``
    annotations guard against non-int / non-ReadingOverride values.

    ``stale`` is *not* part of that chain. A connector reporting ``valid``
    is asserting its read succeeded; the client knowing the sample is older
    than anyone asked for is a different claim, and both can be true. Letting
    first-wins precedence apply would post ``valid`` on a reading known to be
    stale, so staleness is OR-ed on top instead: set *questionable*, clear
    *valid*, leave every other connector-supplied bit intact.
    """
    candidates: tuple[object, object] = (per_cycle, getattr(override, "quality_flags", None))
    resolved = QUALITY_FLAGS_VALID
    for candidate in candidates:
        if candidate is None:
            continue
        if (
            not isinstance(candidate, bool)
            and isinstance(candidate, int)
            and 0 <= candidate <= 0xFFFF
        ):
            resolved = candidate
            break
        logger.warning("Ignoring invalid qualityFlags %r (expected 16-bit int)", candidate)

    if stale:
        resolved = (resolved | QUALITY_FLAGS_QUESTIONABLE) & ~QUALITY_FLAGS_VALID
    return resolved.to_bytes(2, "big")


def _create_reading_type(spec: ReadingTypeSpec, flow_direction: int) -> ReadingType1:
    """Create a ReadingType from a spec with given flow direction."""
    return ReadingType1(
        accumulation_behaviour=AccumulationBehaviourType(value=spec.accumulation_behaviour),
        commodity=CommodityType(value=spec.commodity),
        data_qualifier=DataQualifierType(value=spec.data_qualifier),
        flow_direction=FlowDirectionType(value=flow_direction),
        kind=KindType(value=spec.kind),
        power_of_ten_multiplier=PowerOfTenMultiplierType(value=spec.multiplier),
        uom=UomType(value=spec.uom),
        phase=PhaseCode(value=spec.phase) if spec.phase is not None else None,
    )


def _scaled_for(spec: ReadingTypeSpec, monitoring_data: dict[str, Any]) -> ScaledReading:
    """Pull the raw value for ``spec.key`` and run it through the spec's
    scaling function. ``scale_func`` is non-None for every spec we
    publish; the assertion documents the invariant and lets mypy narrow
    the type.
    """
    raw_value = monitoring_data.get(spec.key)
    assert spec.scale_func is not None, f"ReadingTypeSpec {spec.key} missing scale_func"
    return spec.scale_func(raw_value)


def create_mup(
    lfdi: str,
    monitoring_data: dict[str, Any],
    post_rate: int,
    overrides: dict[str, ReadingOverride] | None = None,
    *,
    profile: ReadingProfile = ReadingProfile.DEFAULT,
    registered: frozenset[int] = frozenset(),
) -> MirrorUsagePoint:
    """Create a MirrorUsagePoint with system + per-line MirrorMeterReadings.

    Args:
        lfdi: Device LFDI (will be normalized to lowercase internally,
              stored as uppercase in deviceLFDI per spec)
        monitoring_data: Raw monitoring values from connector.fetch_monitoring().
              May include ``ACType`` (SunSpec 701 enum) and per-line
              keys (``WL1``, ``VL2`` etc.); on a multi-phase device per-line
              readings are appended for every line advertised, **including
              L1** (single-phase emits none -- see ``_all_specs_for``).
        post_rate: Posting rate in seconds
        overrides: Optional per-key ReadingType overrides from the connector
              (see ``BaseConnector.reading_overrides``); fields left unset keep
              the standard ReadingType metadata.
        profile: How the readings are encoded (see :class:`ReadingProfile`).
        registered: Slots registered by an earlier POST of this MUP. Under
              SIGNED_LOAD_CONVENTION their optional readings are kept even
              when absent from ``monitoring_data``.

    Returns:
        MirrorUsagePoint with system + per-line meter readings.
    """
    lfdi_norm = lfdi.lower()

    # Create MUP mRID (index 0)
    mup_mrid = _create_mrid(lfdi_norm, index=0)

    signed_load = profile is ReadingProfile.SIGNED_LOAD_CONVENTION
    specs = (
        _signed_load_specs_for(monitoring_data, registered)
        if signed_load
        else _all_specs_for(monitoring_data)
    )
    meter_readings: list[MirrorMeterReading1] = []
    for mrid_index, spec in specs:
        override = overrides.get(spec.key) if overrides else None
        flow_direction = (
            FLOW_NOT_APPLICABLE
            if signed_load
            else _scaled_for(spec, monitoring_data).flow_direction
        )
        reading_type = _create_reading_type(_effective_spec(spec, override), flow_direction)
        mmr_mrid = _create_mrid(lfdi_norm, index=mrid_index)
        mmr = MirrorMeterReading1(
            m_rid=mmr_mrid,
            description=spec.description,
            reading_type=reading_type,
        )
        meter_readings.append(mmr)

    # deviceLFDI is stored as uppercase hex bytes per spec
    device_lfdi_bytes = bytes.fromhex(lfdi_norm)

    return MirrorUsagePoint(
        m_rid=mup_mrid,
        description=f"DER {lfdi_norm[:8]}",
        version=VersionType(value=1),
        role_flags=RoleFlagsType(value=ROLE_FLAGS_DER.to_bytes(2, "big")),
        service_category_kind=ServiceKind(value=SERVICE_CATEGORY_ELECTRICITY),
        status=STATUS_ON,
        device_lfdi=device_lfdi_bytes,
        mirror_meter_reading=meter_readings,
        post_rate=post_rate,
    )


def create_meter_reading_list(
    lfdi: str,
    monitoring_data: dict[str, Any],
    timestamp: int | None = None,
    overrides: dict[str, ReadingOverride] | None = None,
    *,
    post_rate: int = 300,
    next_update_time: int | None = None,
    stale: bool = False,
    profile: ReadingProfile = ReadingProfile.DEFAULT,
    registered: frozenset[int] | None = None,
) -> MirrorMeterReadingList:
    """Create a MirrorMeterReadingList with current readings.

    This is used when POSTing updated readings to an existing MUP. We post a
    reading only for quantities the connector actually supplied this cycle: a
    spec whose key is absent or ``None`` in ``monitoring_data`` is omitted
    rather than zero-filled, so an un-measured quantity doesn't show up as a
    misleading ``0`` (e.g. ``0 V``) on the server.

    ``overrides`` (see ``BaseConnector.reading_overrides``) supplies a per-key
    ``qualityFlags`` default; unset keys use the standard ``valid`` flag.

    ``timestamp`` is the instant the values were *acquired*, which since the
    poll planner is no longer the instant they are posted. It becomes
    ``timePeriod.start`` and ``lastUpdateTime``; per METER.001 the interval runs
    forward from ``start``, so the existing duration semantics are unchanged and
    only the clock supplying ``start`` differs.

    ``next_update_time`` is when this MirrorMeterReading will next be *posted*,
    not when the device will next be read, letting a server detect a stalled
    feed without waiting out the 72-hour MUP timeout. Post cadence rather than
    acquisition cadence because the two differ once the poll planner owns
    acquisition, and only the former is reliably knowable: a device in backoff
    has no predictable next read. ``None`` when the caller cannot say.

    ``stale`` marks the sample older than its consumers asked for, or from an
    unreachable device. It ORs the *questionable* bit onto whatever quality the
    connector reported rather than replacing it, so a connector saying ``valid``
    cannot mask staleness the client knows about.

    This is a *subset* of what ``create_mup`` registered -- ``create_mup`` always
    registers the full ReadingType set for the device, so every omitted reading's
    ReadingType is still registered. Posting a subset of registered
    MirrorMeterReadings is valid per IEEE 2030.5 §10.11.3 (MirrorMeterReadings are
    optional in the MUP and updates), and mRIDs are deterministic from
    ``(lfdi, slot_index, pen)`` so a quantity that resumes reporting later matches
    its registered ReadingType (Rule h.1).

    Under ``SIGNED_LOAD_CONVENTION`` every reading is an instantaneous sample, so
    its duration is 0 whatever the dataQualifier, and its value is scaled from the
    effective multiplier (see ``_signed_load_value``). ``registered``, when given,
    limits it to slots the MUP has registered, so a reading whose registration
    has not succeeded yet is held back rather than met with Rule h.3.
    """
    if timestamp is None:
        timestamp = int(time.time())

    lfdi_norm = lfdi.lower()

    signed_load = profile is ReadingProfile.SIGNED_LOAD_CONVENTION
    specs = (
        _signed_load_specs_for(monitoring_data, frozenset())
        if signed_load
        else _all_specs_for(monitoring_data)
    )
    posted: dict[str, int] = {}
    meter_readings: list[MirrorMeterReading1] = []
    for mrid_index, spec in specs:
        if monitoring_data.get(spec.key) is None:
            continue
        if signed_load and registered is not None and mrid_index not in registered:
            continue
        mmr_mrid = _create_mrid(lfdi_norm, index=mrid_index)
        override = overrides.get(spec.key) if overrides else None
        # Optional per-cycle quality the connector attached to this sample.
        per_cycle_quality = monitoring_data.get(f"{spec.key}__quality")

        if signed_load:
            # Var precedes PF in slot order, system and per-line, so the sign
            # PF takes from it is already in `posted`.
            value = _signed_load_value(_effective_spec(spec, override), monitoring_data, posted)
            posted[spec.key] = value
            duration = 0
        else:
            value = _scaled_for(spec, monitoring_data).value
            # timePeriod duration: an interval reading (Average/Maximum/Minimum)
            # covers a window, and CSIP-AUS requires that window to match the MUP
            # postRate, so its duration is post_rate; an instantaneous reading is a
            # point in time -> duration 0. Use the effective dataQualifier (a
            # connector override can change it, e.g. print_demo reports W as
            # Maximum). getattr guards against a buggy in-process connector
            # returning a non-ReadingOverride value (the manager only checks the
            # top-level map).
            override_dq = (
                getattr(override, "data_qualifier", None) if override is not None else None
            )
            eff_dq = override_dq if override_dq is not None else spec.data_qualifier
            duration = 0 if eff_dq == _DATA_QUALIFIER_INSTANTANEOUS else post_rate
        reading = Reading1(
            quality_flags=_resolve_quality_flags(override, per_cycle_quality, stale=stale),
            time_period=DateTimeInterval(
                duration=duration,
                start=TimeType(value=timestamp),
            ),
            value=value,
        )

        # IEEE 10.11.3 rule n): ReadingType SHALL NOT be included in
        # subsequent POSTs -- only the initial MUP POST includes it.
        mmr = MirrorMeterReading1(
            m_rid=mmr_mrid,
            description=spec.description,
            reading=reading,
            # IEEE 2030.5 Annex B.17.1. Deliberately asymmetric, because the
            # two answer different questions: lastUpdateTime is when the data
            # behind this reading was refreshed (which the server cannot
            # otherwise derive), nextUpdateTime is when the mirror will next be
            # written (which is what makes a stalled feed detectable). Neither
            # is governed by a requirement in the conformance matrix; both are
            # optional and were previously unset, tracked as an IEEE Gap in
            # docs/spec/07-telemetry.md.
            last_update_time=TimeType(value=timestamp),
            next_update_time=(
                None if next_update_time is None else TimeType(value=next_update_time)
            ),
        )
        meter_readings.append(mmr)

    return MirrorMeterReadingList(
        mirror_meter_reading=meter_readings,
        all=len(meter_readings),
        results=len(meter_readings),
    )
