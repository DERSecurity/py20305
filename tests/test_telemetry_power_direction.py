"""Tests for PowerDirection: how W and var carry their direction under the DEFAULT profile."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from py20305.config import TelemetryConfig
from py20305.telemetry.mup import (
    PowerDirection,
    _create_mrid,
    create_meter_reading_list,
    create_mup,
    registration_slots,
)

LFDI = "1234567890abcdef1234567890abcdef12345678"
NET = PowerDirection.NET
SPLIT = PowerDirection.SPLIT
_SLOT_BY_MRID = {_create_mrid(LFDI, index=i).value: i for i in range(1, 60)}


def _single_phase(**values: Any) -> dict[str, Any]:
    return {"W": 1500.0, "Var": 200.0, "Hz": 60.0, "V": 240.0, "PF": 0.99, "VA": 1513.0} | values


def _three_phase(**values: Any) -> dict[str, Any]:
    data = _single_phase(ACType=2)
    for line in (1, 2, 3):
        data |= {f"WL{line}": 500.0, f"VarL{line}": 60.0, f"VL{line}": 139.0}
    return data | values


def _flow_directions(mup: Any) -> dict[int, int]:
    return {
        _SLOT_BY_MRID[m.m_rid.value]: m.reading_type.flow_direction.value
        for m in mup.mirror_meter_reading
    }


def _values(monitoring: dict[str, Any], direction: PowerDirection) -> dict[int, int]:
    built = create_meter_reading_list(LFDI, monitoring, timestamp=0, power_direction=direction)
    return {_SLOT_BY_MRID[m.m_rid.value]: m.reading.value for m in built.mirror_meter_reading}


class TestNet:
    def test_power_is_registered_once_as_net_in_its_own_slots(self):
        directions = _flow_directions(create_mup(LFDI, _single_phase(), 300, power_direction=NET))

        assert directions[31] == 4 and directions[32] == 4, "W and var are Net"
        assert 1 not in directions and 2 not in directions, "the DEFAULT W and var slots are unused"
        assert directions[3] == 1, "frequency is unchanged"

    def test_power_absorbed_is_posted_negative_and_delivered_positive(self):
        assert _values(_single_phase(W=-1500.0, Var=-200.0), NET)[31] == -1500
        assert _values(_single_phase(W=-1500.0, Var=-200.0), NET)[32] == -200
        assert _values(_single_phase(W=1500.0), NET)[31] == 1500

    def test_a_battery_that_changes_direction_after_registration_keeps_its_sign(self):
        registered = create_mup(LFDI, _single_phase(W=2000.0), 300, power_direction=NET)
        charging = _values(_single_phase(W=-2000.0), NET)

        assert _flow_directions(registered)[31] == 4
        assert charging[31] == -2000

    def test_per_line_power_is_net_in_its_own_slots(self):
        directions = _flow_directions(create_mup(LFDI, _three_phase(), 300, power_direction=NET))

        assert {slot: directions[slot] for slot in range(31, 39)} == dict.fromkeys(range(31, 39), 4)
        assert 10 not in directions and 11 not in directions, "the DEFAULT L1 W and var slots"
        assert 12 in directions, "L1 voltage keeps its DEFAULT slot"


class TestSplit:
    def test_each_quantity_is_registered_forward_and_reverse_in_its_own_slots(self):
        directions = _flow_directions(create_mup(LFDI, _single_phase(), 300, power_direction=SPLIT))

        assert (directions[41], directions[51]) == (1, 19), "W forward and reverse"
        assert (directions[42], directions[52]) == (1, 19), "var forward and reverse"
        assert 1 not in directions and 2 not in directions

    def test_only_the_active_direction_carries_a_value(self):
        charging = _values(_single_phase(W=-1500.0, Var=200.0), SPLIT)
        delivering = _values(_single_phase(W=1500.0, Var=-200.0), SPLIT)

        assert (charging[41], charging[51]) == (0, 1500)
        assert (charging[42], charging[52]) == (200, 0)
        assert (delivering[41], delivering[51]) == (1500, 0)
        assert (delivering[42], delivering[52]) == (0, 200)

    def test_per_line_power_is_split_in_its_own_slots(self):
        directions = _flow_directions(create_mup(LFDI, _three_phase(), 300, power_direction=SPLIT))

        assert (directions[45], directions[55]) == (1, 19), "L2 W forward and reverse"
        assert (directions[48], directions[58]) == (1, 19), "L3 var forward and reverse"


class TestSlots:
    @pytest.mark.parametrize("direction", [NET, SPLIT])
    def test_the_new_slots_overlap_no_default_slot(self, direction):
        default = registration_slots(_three_phase(), profile_default())
        directed = registration_slots(_three_phase(), profile_default(), power_direction=direction)

        new = directed - default
        assert new and min(new) > 30
        assert not new & set(range(0, 31))

    def test_net_and_split_slots_do_not_overlap(self):
        net = registration_slots(_three_phase(), profile_default(), power_direction=NET)
        split = registration_slots(_three_phase(), profile_default(), power_direction=SPLIT)

        assert not {s for s in net if s > 30} & {s for s in split if s > 30}

    def test_the_default_mode_registers_what_it_always_has(self):
        directions = _flow_directions(create_mup(LFDI, _single_phase(W=-1500.0), 300))

        assert directions[1] == 19, "set from the sign at registration"
        assert max(directions) <= 30


def profile_default() -> Any:
    from py20305.telemetry.mup import ReadingProfile

    return ReadingProfile.DEFAULT


class TestConfiguration:
    def test_the_default_mode_is_the_default(self):
        assert TelemetryConfig().power_direction == "default"

    @pytest.mark.parametrize("direction", ["net", "split"])
    def test_a_mode_is_accepted_with_the_default_profile(self, direction):
        assert TelemetryConfig(power_direction=direction).power_direction == direction

    def test_a_mode_with_the_signed_load_profile_is_refused(self):
        with pytest.raises(ValidationError, match="power_direction"):
            TelemetryConfig(reading_profile="signed_load_convention", power_direction="net")
