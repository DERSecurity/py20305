"""Build connector readings from the flat monitoring dicts older tests were written with.

Test support only. The keys are the names release 0.16 read from
``fetch_monitoring``; each becomes the :class:`Reading` that carries the same
ReadingType 0.16 registered for it, so a test ported from 0.16 checks the same
wire output.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from py20305.connectors.reading import Reading, reading_key

#: (uom, kind, data_qualifier) for each system quantity.
_SYSTEM: dict[str, tuple[int, int, int]] = {
    "W": (38, 37, 12),
    "Var": (63, 37, 12),
    "Hz": (33, 37, 12),
    "V": (29, 37, 2),
    "PF": (65, 0, 12),
    "VA": (61, 37, 12),
    "A": (5, 37, 12),
}
_LINE_PHASE = {1: 128, 2: 64, 3: 32}
_LINE_NEUTRAL_PHASE = {1: 129, 2: 65, 3: 33}
_LINE_TO_LINE_PHASE = {"VL1L2": 132, "VL2L3": 66, "VL3L1": 40}
#: Phase of the W, var, VA, A and state-of-energy totals by wiring, as 0.16
#: tagged them under the signed-load profile.
_TOTAL_PHASE = {"single": 128, "split": 132, "three": 224}
_PER_LINE = re.compile(r"^(W|Var|V|PF|VA|A)L([1-3])$")


class LegacyReadings(list[Reading]):
    """The readings for a monitoring dict, with the dict kept as ``source``."""

    source: dict[str, Any]


def readings_from(
    monitoring: dict[str, Any], *, wiring: str | None = None, **fields: dict[str, Any]
) -> LegacyReadings:
    """Return the readings for a flat monitoring dict.

    ``wiring`` ("single", "split", "three") tags the W, var, VA, A and
    state-of-energy totals with a phase, as the signed-load profile did.
    ``fields`` maps a key to Reading fields to set on it, for example
    ``W={"data_qualifier": 8}``. ``"<key>__quality"`` sets that reading's
    quality flags.
    """
    total_phase = _TOTAL_PHASE.get(wiring or "", 0)
    out = LegacyReadings()
    out.source = dict(monitoring)
    for key, value in monitoring.items():
        if value is None or key.endswith("__quality"):
            continue
        quality = monitoring.get(f"{key}__quality")
        extra = dict(fields.get(key, {}))
        if quality is not None:
            extra.setdefault("quality_flags", quality)
        if key in _SYSTEM:
            uom, kind, dq = _SYSTEM[key]
            phase = total_phase if key in {"W", "Var", "VA", "A"} else 0
            reading = Reading(uom=uom, value=value, phase=phase, kind=kind, data_qualifier=dq)
        elif match := _PER_LINE.match(key):
            uom, kind, dq = _SYSTEM[match.group(1)]
            line = int(match.group(2))
            phase = _LINE_NEUTRAL_PHASE[line] if match.group(1) == "V" else _LINE_PHASE[line]
            reading = Reading(uom=uom, value=value, phase=phase, kind=kind, data_qualifier=dq)
        elif key in _LINE_TO_LINE_PHASE:
            reading = Reading(uom=29, value=value, phase=_LINE_TO_LINE_PHASE[key])
        elif key == "WHAvail":
            reading = Reading(uom=72, value=value, phase=total_phase, kind=0)
        elif key in ("WhFwd", "WhRev"):
            reading = Reading(
                uom=72,
                value=value,
                kind=12,
                accumulation_behaviour=9,
                flow_direction=1 if key == "WhFwd" else 19,
            )
        else:
            continue
        if extra:
            reading = replace(reading, **extra)
        out.append(reading)
    return out


#: The store key of each system quantity's reading, for tests that look one up.
KEYS: dict[str, str] = {
    key: reading_key(readings_from({key: 1.0})[0])
    for key in ("W", "Var", "Hz", "V", "PF", "VA", "A")
}
