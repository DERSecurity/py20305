"""The MUP and readings for every encoding release 0.16 had stay byte-identical.

The expected XML in ``fixtures/mup_default_golden.json`` was rendered by release
0.16, which read flat monitoring dicts. Each case here gives the readings that
carry the same ReadingTypes, so a server holding a 0.16 registration keeps
receiving readings under the mRIDs it knows. The fixture preserves 0.16's
behavior, including flowDirection taken from the sign of the value the MUP was
built with. Regenerate it only for an intended change to these encodings:

    python -m tests.test_telemetry_mup_default_golden
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from py20305.telemetry.mup import (
    DeviceRole,
    PowerDirection,
    ReadingProfile,
    create_meter_reading_list,
    create_mup,
)
from py20305.xml.serialization import to_xml
from tests.legacy_readings import readings_from

LFDI = "1234567890abcdef1234567890abcdef12345678"
FIXTURE = Path(__file__).parent / "fixtures" / "mup_default_golden.json"

_SYSTEM = {"W": -3500.4, "Var": 420.6, "Hz": 60.02, "V": 241.7, "PF": -0.97, "VA": 3530.0}
_LINES = {
    f"{key}L{line}": value + line
    for line in (1, 2, 3)
    for key, value in (
        ("W", -1166.0),
        ("Var", 140.0),
        ("V", 139.4),
        ("PF", -0.95),
        ("VA", 1176.0),
        ("A", 8.4),
    )
}
_SPLIT_LINES = {key: value for key, value in _LINES.items() if key.endswith(("L1", "L2"))}
_LINE_TO_LINE = {"VL1L2": 241.5, "VL2L3": 242.5, "VL3L1": 240.5}
_SIGNED = ReadingProfile.SIGNED_LOAD_CONVENTION

#: Each case: the monitoring dict release 0.16 read, the wiring it reported
#: (``ACType`` 0 single, 1 split, 2 three phase), and the encoding options.
CASES: dict[str, dict[str, Any]] = {
    "single_phase": {"monitoring": {**_SYSTEM, "A": 14.6}},
    "three_phase": {"monitoring": {**_SYSTEM, "A": 25.3, **_LINES}, "wiring": "three"},
    "split_phase": {"monitoring": {**_SYSTEM, "A": 12.7, **_SPLIT_LINES}, "wiring": "split"},
    "stale_with_quality": {
        "monitoring": {**_SYSTEM, "A": 12.7, "W__quality": 0x20},
        "stale": True,
    },
    "energy_totals": {
        "monitoring": {**_SYSTEM, "A": 14.6, "WhFwd": 125_000.0, "WhRev": 4_200.0},
    },
    "net": {
        "monitoring": {**_SYSTEM, "A": 25.3, **_LINES},
        "wiring": "three",
        "power_direction": PowerDirection.NET,
    },
    "split_direction": {
        "monitoring": {**_SYSTEM, "A": 25.3, **_LINES},
        "wiring": "three",
        "power_direction": PowerDirection.SPLIT,
    },
    "signed_load_single_phase": {
        "monitoring": {**_SYSTEM, "A": 14.6, "WHAvail": 9800},
        "wiring": "single",
        "profile": _SIGNED,
    },
    "signed_load_three_phase": {
        "monitoring": {**_SYSTEM, "A": 25.3, **_LINES, **_LINE_TO_LINE, "WHAvail": 9800},
        "wiring": "three",
        "profile": _SIGNED,
    },
    "signed_load_split_phase_site_meter": {
        "monitoring": {**_SYSTEM, "A": 12.7, **_SPLIT_LINES, "VL1L2": 240.0},
        "wiring": "split",
        "profile": _SIGNED,
        "role": DeviceRole.SITE_METER,
    },
}

_AC_TYPE = {"single": 0, "split": 1, "three": 2}


def render(case: dict[str, Any]) -> dict[str, str]:
    """Render a case's MUP and readings with the current implementation."""
    profile = case.get("profile", ReadingProfile.DEFAULT)
    wiring = case.get("wiring")
    readings = readings_from(case["monitoring"], wiring=wiring if profile is _SIGNED else None)
    options: dict[str, Any] = {
        "profile": profile,
        "power_direction": case.get("power_direction", PowerDirection.DEFAULT),
        "role": case.get("role", DeviceRole.DER),
    }
    mup = create_mup(LFDI, readings, 300, **options)
    reading_list = create_meter_reading_list(
        LFDI,
        readings,
        timestamp=1_760_000_000,
        post_rate=300,
        next_update_time=1_760_000_300,
        stale=case.get("stale", False),
        **options,
    )
    return {"mup": to_xml(mup).decode(), "readings": to_xml(reading_list).decode()}


@pytest.mark.parametrize("name", sorted(CASES))
def test_release_0_16_output_is_unchanged(name: str) -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert render(CASES[name]) == expected[name]


if __name__ == "__main__":
    rendered = {name: render(case) for name, case in sorted(CASES.items())}
    FIXTURE.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
