"""The reading a connector returns from ``fetch_monitoring``.

A :class:`Reading` is one measured value with the IEEE 2030.5 ReadingType that
describes it. The library posts each reading as a MirrorMeterReading and derives
its mRID from the reading's identity (see :func:`reading_identity`), so a reading
whose ReadingType differs from another's always gets a different mRID
(IEEE 2030.5-2023 §10.11.3 Rule n).

The module imports nothing from the rest of the package, so a connector can
import it without loading the client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

#: UomType codes the library knows a default powerOfTenMultiplier and a name for.
UOM_AMPERE = 5
UOM_VOLT = 29
UOM_HERTZ = 33
UOM_WATT = 38
UOM_VA = 61
UOM_VAR = 63
UOM_COS_THETA = 65
UOM_WATT_HOUR = 72

#: KindType codes.
KIND_NOT_APPLICABLE = 0
KIND_ENERGY = 12
KIND_POWER = 37

#: DataQualifierType codes.
DATA_QUALIFIER_AVERAGE = 2
DATA_QUALIFIER_MAXIMUM = 8
DATA_QUALIFIER_MINIMUM = 9
DATA_QUALIFIER_NORMAL = 12

#: AccumulationBehaviourType codes.
ACCUMULATION_SUMMATION = 9
ACCUMULATION_INSTANTANEOUS = 12

#: FlowDirectionType codes.
FLOW_FORWARD = 1
FLOW_NET = 4
FLOW_REVERSE = 19


@dataclass(frozen=True, slots=True)
class Reading:
    """One measured value and the IEEE 2030.5 ReadingType that describes it.

    ``value`` is in engineering units (W, var, VA, V, A, Hz, Wh, or a power
    factor between -1 and 1). Real and reactive power are signed in the frame of
    the device's role:

    - A DER and a DER meter report positive real power for power the DER
      delivers and negative for power it absorbs; positive reactive power for
      reactive power it injects (over-excited) and negative for reactive power
      it absorbs (under-excited).
    - A site meter reports positive real and reactive power for import, from the
      area EPS to the premises, which IEEE 2030.5 FlowDirectionType calls
      Forward.

    A power factor carries the sign of its reactive power. Every other quantity
    is posted as a magnitude.

    The remaining fields are IEEE 2030.5 ReadingType codes:

    - ``uom``: UomType, for example 38 W, 63 var, 61 VA, 29 V, 5 A, 33 Hz,
      65 cos(theta), 72 Wh.
    - ``phase``: PhaseCode, for example 0 not applicable, 128 A, 129 AN,
      132 AB, 224 ABC.
    - ``kind``: KindType, 37 power, 12 energy, 0 not applicable.
    - ``data_qualifier``: DataQualifierType, 12 normal, 2 average, 8 maximum,
      9 minimum.
    - ``accumulation_behaviour``: 12 instantaneous, 9 summation.
    - ``flow_direction``: set only when the reading is direction-specific, such
      as an energy total delivered forward (1) or in reverse (19). None lets the
      reading profile choose (see ``py20305.telemetry.mup``).
    - ``multiplier``: the powerOfTenMultiplier to post with. None takes the
      library default for the unit.
    - ``quality_flags``: this sample's qualityFlags (16 bits). None posts
      ``valid``.
    """

    uom: int
    value: float
    phase: int = 0
    kind: int = KIND_POWER
    data_qualifier: int = DATA_QUALIFIER_NORMAL
    accumulation_behaviour: int = ACCUMULATION_INSTANTANEOUS
    flow_direction: int | None = None
    multiplier: int | None = None
    quality_flags: int | None = None


class ReadingIdentity(NamedTuple):
    """The ReadingType fields that identify a reading.

    Two readings with the same identity are the same measurement. ``multiplier``
    is the effective powerOfTenMultiplier, after the library default is applied,
    because it is part of the ReadingType too.
    """

    uom: int
    phase: int
    kind: int
    data_qualifier: int
    accumulation_behaviour: int
    flow_direction: int | None
    multiplier: int


#: The powerOfTenMultiplier posted when a reading sets none.
DEFAULT_MULTIPLIER: dict[int, int] = {
    UOM_HERTZ: -3,
    UOM_VOLT: -1,
    UOM_COS_THETA: -3,
    UOM_AMPERE: -1,
}


def effective_multiplier(reading: Reading, defaults: dict[int, int] | None = None) -> int:
    """Return the powerOfTenMultiplier a reading posts with."""
    if reading.multiplier is not None:
        return reading.multiplier
    table = DEFAULT_MULTIPLIER if defaults is None else defaults
    return table.get(reading.uom, 0)


def reading_identity(reading: Reading, defaults: dict[int, int] | None = None) -> ReadingIdentity:
    """Return the identity of ``reading`` with its effective multiplier."""
    return ReadingIdentity(
        reading.uom,
        reading.phase,
        reading.kind,
        reading.data_qualifier,
        reading.accumulation_behaviour,
        reading.flow_direction,
        effective_multiplier(reading, defaults),
    )


def reading_key(reading: Reading) -> str:
    """Return a stable text key for a reading's identity, for keying stores and logs.

    The multiplier is left out: a store keys a measurement, and the multiplier
    only says how the value is posted.
    """
    flow = "" if reading.flow_direction is None else f":flow{reading.flow_direction}"
    return (
        f"uom{reading.uom}:phase{reading.phase}:kind{reading.kind}"
        f":dq{reading.data_qualifier}:acc{reading.accumulation_behaviour}{flow}"
    )


def is_valid_reading(reading: object) -> bool:
    """Return True when ``reading`` is a :class:`Reading` the library can post.

    The codes must be non-negative ints, the multiplier in -9..9, the quality
    flags 16 bits, and the value a finite number. A bool is not accepted where
    an int or a number is expected.
    """
    if not isinstance(reading, Reading):
        return False
    codes = (
        reading.uom,
        reading.phase,
        reading.kind,
        reading.data_qualifier,
        reading.accumulation_behaviour,
    )
    if any(isinstance(c, bool) or not isinstance(c, int) or c < 0 for c in codes):
        return False
    flow = reading.flow_direction
    if flow is not None and (isinstance(flow, bool) or not isinstance(flow, int) or flow < 0):
        return False
    mult = reading.multiplier
    if mult is not None and (isinstance(mult, bool) or not isinstance(mult, int)):
        return False
    if mult is not None and not -9 <= mult <= 9:
        return False
    flags = reading.quality_flags
    if flags is not None and (
        isinstance(flags, bool) or not isinstance(flags, int) or not 0 <= flags <= 0xFFFF
    ):
        return False
    value = reading.value
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


__all__ = [
    "DEFAULT_MULTIPLIER",
    "Reading",
    "ReadingIdentity",
    "effective_multiplier",
    "is_valid_reading",
    "reading_identity",
    "reading_key",
]
