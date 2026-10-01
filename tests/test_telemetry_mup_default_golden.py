"""The DEFAULT reading profile's MUP and readings stay byte-identical.

The expected XML in ``fixtures/mup_default_golden.json`` was rendered by the
release before reading profiles existed. It deliberately preserves that
release's behavior, including flowDirection taken from the sign of the value
the MUP was built with. Regenerate it only for an intended change to the
DEFAULT encoding:

    python -m tests.test_telemetry_mup_default_golden
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from py20305.connectors.base import ReadingOverride
from py20305.telemetry.mup import create_meter_reading_list, create_mup
from py20305.xml.serialization import to_xml

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
# Keys only SIGNED_LOAD_CONVENTION reads; DEFAULT output must ignore them.
_PROFILE_ONLY = {"VL1L2": 241.5, "VL2L3": 242.5, "VL3L1": 240.5, "WHAvail": 9800}

CASES: dict[str, dict[str, Any]] = {
    "single_phase": {"monitoring": {**_SYSTEM, "A": 14.6}},
    "three_phase": {"monitoring": {**_SYSTEM, "A": 25.3, "ACType": 2, **_LINES, **_PROFILE_ONLY}},
    "split_phase_partial": {
        "monitoring": {**_SYSTEM, "A": 12.7, "ACType": 1, "WL1": 900.0, "VL1L2": 240.0},
    },
    "overrides_and_stale": {
        "monitoring": {**_SYSTEM, "A": 12.7, "W__quality": 0x20},
        "overrides": {
            "W": ReadingOverride(data_qualifier=8, quality_flags=0x02),
            "A": ReadingOverride(multiplier=-2),
            "V": ReadingOverride(phase=129),
        },
        "stale": True,
    },
}


def render(case: dict[str, Any]) -> dict[str, str]:
    monitoring = case["monitoring"]
    overrides = case.get("overrides")
    mup = create_mup(LFDI, monitoring, 300, overrides)
    readings = create_meter_reading_list(
        LFDI,
        monitoring,
        timestamp=1_760_000_000,
        overrides=overrides,
        post_rate=300,
        next_update_time=1_760_000_300,
        stale=case.get("stale", False),
    )
    return {"mup": to_xml(mup).decode(), "readings": to_xml(readings).decode()}


@pytest.mark.parametrize("name", sorted(CASES))
def test_default_profile_output_is_unchanged(name: str) -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert render(CASES[name]) == expected[name]


if __name__ == "__main__":
    rendered = {name: render(case) for name, case in sorted(CASES.items())}
    FIXTURE.write_text(json.dumps(rendered, indent=2) + "\n", encoding="utf-8")
