"""Tests for the SIGNED_AS_REPORTED reading profile."""

from __future__ import annotations

from typing import Any

from py20305.telemetry.mup import (
    DeviceRole,
    PowerDirection,
    ReadingProfile,
    _create_mrid,
    create_meter_reading_list,
    create_mup,
    registration_slots,
    undeclared_readings,
)
from py20305.xml.serialization import to_xml

LFDI = "1234567890abcdef1234567890abcdef12345678"
AS_REPORTED = ReadingProfile.SIGNED_AS_REPORTED
_SLOT_BY_MRID = {_create_mrid(LFDI, index=i).value: i for i in range(1, 100)}
#: SIGNED_AS_REPORTED's own slot for system W, outside every DEFAULT slot.
W_SLOT = 71


def _values(monitoring: dict[str, Any], **kwargs: Any) -> dict[int, int]:
    built = create_meter_reading_list(LFDI, monitoring, timestamp=0, profile=AS_REPORTED, **kwargs)
    return {_SLOT_BY_MRID[m.m_rid.value]: m.reading.value for m in built.mirror_meter_reading}


class TestReadingTypes:
    def test_no_reading_type_carries_a_flow_direction(self) -> None:
        mup = create_mup(LFDI, {"W": -5000.0, "Var": 100.0}, 300, profile=AS_REPORTED)

        assert mup.mirror_meter_reading
        for reading in mup.mirror_meter_reading:
            assert reading.reading_type.flow_direction is None
        assert b"flowDirection" not in to_xml(mup)

    def test_charging_at_registration_does_not_fix_a_direction(self) -> None:
        """The first cycle's sign must not reach the ReadingType."""
        charging = create_mup(LFDI, {"W": -5000.0}, 300, profile=AS_REPORTED)
        discharging = create_mup(LFDI, {"W": 5000.0}, 300, profile=AS_REPORTED)

        assert to_xml(charging) == to_xml(discharging)

    def test_energy_totals_are_not_registered(self) -> None:
        """WhFwd and WhRev differ only by flowDirection, which this profile omits."""
        mup = create_mup(LFDI, {"W": 1000.0, "WhFwd": 10, "WhRev": 5}, 300, profile=AS_REPORTED)

        slots = {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading}
        assert not slots & {61, 62}

    def test_power_direction_is_ignored(self) -> None:
        net = create_mup(
            LFDI, {"W": 1000.0}, 300, profile=AS_REPORTED, power_direction=PowerDirection.NET
        )
        default = create_mup(LFDI, {"W": 1000.0}, 300, profile=AS_REPORTED)

        assert to_xml(net) == to_xml(default)


class TestValues:
    def test_charging_posts_negative_watts(self) -> None:
        assert _values({"W": -5000.0})[W_SLOT] == -5000

    def test_discharging_posts_positive_watts(self) -> None:
        assert _values({"W": 5000.0})[W_SLOT] == 5000

    def test_sign_follows_each_cycle(self) -> None:
        assert _values({"W": 5000.0})[W_SLOT] == 5000
        assert _values({"W": -3000.0})[W_SLOT] == -3000

    def test_site_meter_is_posted_as_reported(self) -> None:
        """No role-based negation: the value is the connector's own."""
        assert _values({"W": -2000.0}, role=DeviceRole.SITE_METER)[W_SLOT] == -2000

    def test_only_supplied_readings_are_posted(self) -> None:
        assert set(_values({"W": -5000.0})) == {W_SLOT}

    def test_empty_monitoring_posts_no_reading(self) -> None:
        assert _values({}) == {}


class TestDeclaredReadings:
    """A connector that declares its readings gets a MUP of exactly those, from
    the first cycle, so the MUP never needs a re-POST."""

    @staticmethod
    def _slots(mup: Any) -> set[int]:
        return {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading}

    def test_declared_readings_are_registered_on_an_empty_first_cycle(self) -> None:
        mup = create_mup(LFDI, {}, 300, profile=AS_REPORTED, declared=frozenset({"W"}))

        assert self._slots(mup) == {W_SLOT}

    def test_only_declared_readings_are_registered(self) -> None:
        mup = create_mup(
            LFDI,
            {"W": 1000.0, "Var": 100.0, "Hz": 60.0},
            300,
            profile=AS_REPORTED,
            declared=frozenset({"W"}),
        )

        assert self._slots(mup) == {W_SLOT}

    def test_only_declared_readings_are_posted(self) -> None:
        values = _values({"W": -5000.0, "Var": 100.0}, declared=frozenset({"W"}))

        assert values == {W_SLOT: -5000}

    def test_a_declared_reading_without_a_value_is_not_posted(self) -> None:
        assert _values({}, declared=frozenset({"W"})) == {}

    def test_a_declared_per_line_reading_needs_no_ac_type(self) -> None:
        mup = create_mup(LFDI, {}, 300, profile=AS_REPORTED, declared=frozenset({"WL2"}))

        assert len(mup.mirror_meter_reading) == 1
        assert mup.mirror_meter_reading[0].description == "Real Power L2"

    def test_an_unknown_declared_key_is_ignored(self) -> None:
        mup = create_mup(
            LFDI, {}, 300, profile=AS_REPORTED, declared=frozenset({"W", "NotAReading"})
        )

        assert self._slots(mup) == {W_SLOT}

    def test_no_declaration_registers_the_standard_set(self) -> None:
        mup = create_mup(LFDI, {"W": 1000.0}, 300, profile=AS_REPORTED)

        assert self._slots(mup) == set(range(71, 78))

    def test_registration_slots_follow_the_declaration(self) -> None:
        slots = registration_slots({}, AS_REPORTED, declared=frozenset({"W"}))

        assert slots == frozenset({W_SLOT})

    def test_the_declaration_is_ignored_under_the_default_profile(self) -> None:
        mup = create_mup(LFDI, {"W": 1000.0}, 300, declared=frozenset({"W"}))

        assert len(mup.mirror_meter_reading) == 7

    def test_undeclared_readings_lists_returned_readings_left_out(self) -> None:
        returned = {"W": 1.0, "Var": 2.0, "ACType": 1, "W__quality": 0}

        assert undeclared_readings(returned, frozenset({"W"})) == frozenset({"Var"})


def _three_phase() -> dict[str, Any]:
    data: dict[str, Any] = {
        "W": 3000.0, "Var": 300.0, "Hz": 60.0, "V": 240.0, "PF": 0.99, "VA": 3015.0, "A": 12.5,
        "ACType": 2,
    }  # fmt: skip
    for line in (1, 2, 3):
        data |= {
            f"WL{line}": 1000.0, f"VarL{line}": 100.0, f"VL{line}": 139.0,
            f"PFL{line}": 0.99, f"VAL{line}": 1005.0, f"AL{line}": 4.2,
        }  # fmt: skip
    return data


class TestSlots:
    """A slot is never reused for a different ReadingType. Every DEFAULT
    ReadingType carries a flowDirection and this profile's carry none, so a
    device switched between the two keeps no mRID it registered under the other."""

    def test_no_slot_is_shared_with_the_default_profile(self) -> None:
        from py20305.telemetry.mup import registration_slots

        default = registration_slots(_three_phase(), ReadingProfile.DEFAULT)
        as_reported = registration_slots(_three_phase(), AS_REPORTED)

        assert len(as_reported) == len(default)  # the same readings, in other slots
        assert not default & as_reported

    def test_readings_post_to_the_registered_slots(self) -> None:
        mup = create_mup(LFDI, _three_phase(), 300, profile=AS_REPORTED)
        readings = create_meter_reading_list(LFDI, _three_phase(), timestamp=0, profile=AS_REPORTED)

        registered = {m.m_rid.value for m in mup.mirror_meter_reading}
        assert {m.m_rid.value for m in readings.mirror_meter_reading} == registered


class TestMultiplierOverride:
    """The value follows the multiplier the MUP registered."""

    def test_an_overridden_multiplier_rescales_the_value(self) -> None:
        from py20305.connectors.base import ReadingOverride

        overrides = {"A": ReadingOverride(multiplier=-2)}

        values = _values({"A": 12.7}, overrides=overrides)

        assert values[77] == 1270

    def test_an_overridden_power_multiplier_keeps_the_sign(self) -> None:
        from py20305.connectors.base import ReadingOverride

        overrides = {"W": ReadingOverride(multiplier=3)}

        assert _values({"W": -5400.0}, overrides=overrides)[W_SLOT] == -5

    def test_an_unchanged_multiplier_keeps_the_existing_scaling(self) -> None:
        from py20305.connectors.base import ReadingOverride

        overrides = {"A": ReadingOverride(multiplier=-1)}

        assert _values({"A": 12.75}, overrides=overrides)[77] == _values({"A": 12.75})[77]
