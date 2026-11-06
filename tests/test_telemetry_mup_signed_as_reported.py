"""Tests for the SIGNED_AS_REPORTED reading profile."""

from __future__ import annotations

from typing import Any

from py20305.connectors.reading import Reading, ReadingIdentity, reading_identity
from py20305.telemetry.mup import (
    _AS_REPORTED_SLOTS,
    _DEFAULT_SLOTS,
    _SIGNED_LOAD_SLOTS_SINGLE_PHASE,
    DeviceRole,
    PowerDirection,
    ReadingProfile,
    _create_mrid,
    _hashed_mrid,
    create_meter_reading_list,
    create_mup,
    plan_registration,
    undeclared_readings,
)
from py20305.xml.serialization import to_xml
from tests.legacy_readings import readings_from

LFDI = "1234567890abcdef1234567890abcdef12345678"
AS_REPORTED = ReadingProfile.SIGNED_AS_REPORTED
_SLOT_BY_MRID = {_create_mrid(LFDI, index=i).value: i for i in range(0, 100)}
#: SIGNED_AS_REPORTED's own slot for system W, outside every DEFAULT slot.
W_SLOT = 71
PF_SLOT = 75
PF_LINE_SLOTS = (83, 89, 95)


def _declare(*keys: str) -> frozenset[ReadingIdentity]:
    """Return the identities of the readings the monitoring ``keys`` stand for."""
    return frozenset(reading_identity(r) for r in readings_from(dict.fromkeys(keys, 1.0)))


def _values(monitoring: dict[str, Any], **kwargs: Any) -> dict[int, int]:
    fields = kwargs.pop("fields", {})
    built = create_meter_reading_list(
        LFDI, readings_from(monitoring, **fields), timestamp=0, profile=AS_REPORTED, **kwargs
    )
    return {_SLOT_BY_MRID[m.m_rid.value]: m.reading.value for m in built.mirror_meter_reading}


def _mup(monitoring: dict[str, Any], **kwargs: Any) -> Any:
    return create_mup(LFDI, readings_from(monitoring), 300, profile=AS_REPORTED, **kwargs)


class TestReadingTypes:
    def test_no_reading_type_carries_a_flow_direction(self) -> None:
        mup = _mup({"W": -5000.0, "Var": 100.0})

        assert mup.mirror_meter_reading
        for reading in mup.mirror_meter_reading:
            assert reading.reading_type.flow_direction is None
        assert b"flowDirection" not in to_xml(mup)

    def test_charging_at_registration_does_not_fix_a_direction(self) -> None:
        """The first cycle's sign must not reach the ReadingType."""
        assert to_xml(_mup({"W": -5000.0})) == to_xml(_mup({"W": 5000.0}))

    def test_a_stated_flow_direction_is_registered(self) -> None:
        reading = Reading(uom=72, value=10.0, kind=12, accumulation_behaviour=9, flow_direction=1)

        mup = create_mup(LFDI, [reading], 300, profile=AS_REPORTED)

        (registered,) = mup.mirror_meter_reading
        assert registered.reading_type.flow_direction.value == 1
        assert _SLOT_BY_MRID.get(registered.m_rid.value) is None

    def test_energy_totals_take_no_default_slot(self) -> None:
        mup = _mup({"W": 1000.0, "WhFwd": 10, "WhRev": 5})

        slots = {_SLOT_BY_MRID.get(m.m_rid.value) for m in mup.mirror_meter_reading}
        assert not slots & {61, 62}

    def test_power_direction_is_ignored(self) -> None:
        net = _mup({"W": 1000.0}, power_direction=PowerDirection.NET)

        assert to_xml(net) == to_xml(_mup({"W": 1000.0}))


class TestValues:
    def test_charging_posts_negative_watts(self) -> None:
        assert _values({"W": -5000.0})[W_SLOT] == -5000

    def test_discharging_posts_positive_watts(self) -> None:
        assert _values({"W": 5000.0})[W_SLOT] == 5000

    def test_sign_follows_each_cycle(self) -> None:
        assert _values({"W": 5000.0})[W_SLOT] == 5000
        assert _values({"W": -3000.0})[W_SLOT] == -3000

    def test_power_factor_keeps_its_sign(self) -> None:
        """With no flowDirection to carry it, the sign must stay on the value."""
        assert _values({"PF": -0.9})[PF_SLOT] == -900
        assert _values({"PF": 0.9})[PF_SLOT] == 900

    def test_per_line_power_factor_keeps_its_sign(self) -> None:
        values = _values({"PFL1": -0.95, "PFL2": 0.9, "PFL3": -0.85})

        assert [values[slot] for slot in PF_LINE_SLOTS] == [-950, 900, -850]

    def test_current_and_voltage_post_as_magnitudes(self) -> None:
        values = _values({"A": -4.25, "V": 240.0})

        assert values[77] == 43
        assert values[74] == 2400

    def test_site_meter_is_posted_as_reported(self) -> None:
        """No role-based negation: the value is the connector's own."""
        assert _values({"W": -2000.0}, role=DeviceRole.SITE_METER)[W_SLOT] == -2000

    def test_only_supplied_readings_are_posted(self) -> None:
        assert set(_values({"W": -5000.0})) == {W_SLOT}

    def test_empty_monitoring_posts_no_reading(self) -> None:
        assert _values({}) == {}

    def test_an_interval_reading_covers_the_post_rate(self) -> None:
        built = create_meter_reading_list(
            LFDI, readings_from({"V": 240.0}), timestamp=0, profile=AS_REPORTED, post_rate=300
        )

        (reading,) = built.mirror_meter_reading
        assert reading.reading.time_period.duration == 300


class TestDeclaredReadings:
    """A connector that declares its readings gets a MUP of exactly those, from
    the first cycle, so the MUP never needs a re-POST."""

    @staticmethod
    def _slots(mup: Any) -> set[int]:
        return {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading}

    def test_declared_readings_are_registered_on_an_empty_first_cycle(self) -> None:
        mup = create_mup(LFDI, [], 300, profile=AS_REPORTED, declared=_declare("W"))

        assert self._slots(mup) == {W_SLOT}

    def test_only_declared_readings_are_registered(self) -> None:
        mup = _mup({"W": 1000.0, "Var": 100.0, "Hz": 60.0}, declared=_declare("W"))

        assert self._slots(mup) == {W_SLOT}

    def test_only_declared_readings_are_posted(self) -> None:
        values = _values({"W": -5000.0, "Var": 100.0}, declared=_declare("W"))

        assert values == {W_SLOT: -5000}

    def test_a_declared_reading_without_a_value_is_not_posted(self) -> None:
        assert _values({}, declared=_declare("W")) == {}

    def test_a_declared_per_line_reading_is_registered(self) -> None:
        mup = create_mup(LFDI, [], 300, profile=AS_REPORTED, declared=_declare("WL2"))

        (registered,) = mup.mirror_meter_reading
        assert registered.description == "Real Power L2"

    def test_a_declared_reading_with_no_slot_is_registered_under_a_hashed_mrid(self) -> None:
        identity = reading_identity(Reading(uom=72, value=0.0, kind=12, accumulation_behaviour=9))

        plan = plan_registration(LFDI, [], profile=AS_REPORTED, declared=frozenset({identity}))

        assert len(plan) == 1

    def test_no_declaration_registers_the_readings_supplied(self) -> None:
        mup = _mup({"W": 1000.0})

        assert self._slots(mup) == {W_SLOT}

    def test_the_declaration_is_ignored_under_the_default_profile(self) -> None:
        mup = create_mup(
            LFDI, readings_from({"W": 1000.0, "Var": 1.0}), 300, declared=_declare("W")
        )

        assert len(mup.mirror_meter_reading) == 2

    def test_undeclared_readings_lists_returned_readings_left_out(self) -> None:
        returned = readings_from({"W": 1.0, "Var": 2.0})

        assert undeclared_readings(returned, _declare("W")) == _declare("Var")


def _three_phase() -> dict[str, Any]:
    data: dict[str, Any] = {
        "W": 3000.0, "Var": 300.0, "Hz": 60.0, "V": 240.0, "PF": 0.99, "VA": 3015.0, "A": 12.5,
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
        readings = readings_from(_three_phase())
        default = {m.m_rid.value for m in create_mup(LFDI, readings, 300).mirror_meter_reading}
        as_reported = {
            m.m_rid.value
            for m in create_mup(LFDI, readings, 300, profile=AS_REPORTED).mirror_meter_reading
        }

        assert len(as_reported) == len(default)  # the same readings, in other slots
        assert not default & as_reported

    def test_the_slots_are_the_default_slots_plus_70(self) -> None:
        slots = sorted(slot for slot, _ in _AS_REPORTED_SLOTS.values())

        assert slots == [*range(71, 78), *range(80, 98)]
        for identity, (slot, description) in _AS_REPORTED_SLOTS.items():
            assert _DEFAULT_SLOTS[identity] == (slot - 70, description)

    def test_the_slots_collide_with_no_other_slot_layout(self) -> None:
        """Slots 0 to 62 are the largest in use under any profile or direction."""
        used = {slot for slot, _ in _AS_REPORTED_SLOTS.values()}
        others = {slot for slot, _ in _DEFAULT_SLOTS.values()}
        others |= {slot for slot, _ in _SIGNED_LOAD_SLOTS_SINGLE_PHASE.values()}
        others |= {0, *range(31, 39), *range(41, 49), *range(51, 59)}

        assert not used & others
        assert max(others) < 71

    def test_a_hashed_mrid_never_equals_a_slot_mrid(self) -> None:
        """A slot mRID has a decimal digit where a hashed one has the marker."""
        identities = [
            reading_identity(Reading(uom=uom, value=0.0, phase=phase))
            for uom in (38, 63, 72, 29)
            for phase in (0, 128, 224)
        ]
        slot_mrids = {_create_mrid(LFDI, index=i).value for i in range(0, 100)}

        for identity in identities:
            for salt in ("default", "signed_load_convention", "signed_as_reported"):
                assert _hashed_mrid(LFDI, identity, salt=salt).value not in slot_mrids
                assert _hashed_mrid(LFDI, identity, salt=salt).value.hex()[12] == "f"

    def test_a_hashed_mrid_differs_between_the_default_and_as_reported_profiles(self) -> None:
        reading = Reading(uom=72, value=1.0, phase=224, kind=12)

        default = {m.m_rid.value for m in create_mup(LFDI, [reading], 300).mirror_meter_reading}
        as_reported = {
            m.m_rid.value
            for m in create_mup(LFDI, [reading], 300, profile=AS_REPORTED).mirror_meter_reading
        }

        assert default and as_reported and not default & as_reported

    def test_readings_post_to_the_registered_slots(self) -> None:
        readings = readings_from(_three_phase())
        mup = create_mup(LFDI, readings, 300, profile=AS_REPORTED)
        posted = create_meter_reading_list(LFDI, readings, timestamp=0, profile=AS_REPORTED)

        registered = {m.m_rid.value for m in mup.mirror_meter_reading}
        assert {m.m_rid.value for m in posted.mirror_meter_reading} == registered


class TestMultiplierOverride:
    """The value follows the multiplier the MUP registered. A reading with its
    own multiplier is a different identity, so it has its own mRID."""

    @staticmethod
    def _only_value(monitoring: dict[str, Any], **fields: Any) -> int:
        built = create_meter_reading_list(
            LFDI, readings_from(monitoring, **fields), timestamp=0, profile=AS_REPORTED
        )
        (posted,) = built.mirror_meter_reading
        return posted.reading.value

    def test_an_overridden_multiplier_rescales_the_value(self) -> None:
        assert self._only_value({"A": 12.7}, A={"multiplier": -2}) == 1270

    def test_an_overridden_power_multiplier_keeps_the_sign(self) -> None:
        assert self._only_value({"W": -5400.0}, W={"multiplier": 3}) == -5

    def test_an_overridden_power_factor_multiplier_keeps_the_sign(self) -> None:
        assert self._only_value({"PF": -0.87}, PF={"multiplier": -2}) == -87

    def test_an_unchanged_multiplier_keeps_the_slot(self) -> None:
        values = _values({"A": 12.75}, fields={"A": {"multiplier": -1}})

        assert values == _values({"A": 12.75})
