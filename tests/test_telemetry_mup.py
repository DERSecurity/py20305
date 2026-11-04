"""Tests for MirrorUsagePoint creation from connector readings."""

from __future__ import annotations

from typing import Any

import pytest

from py20305.connectors.reading import Reading, reading_identity
from py20305.telemetry.mup import (
    DEFAULT_PEN,
    ROLE_FLAGS_DER,
    ROLE_FLAGS_SITE,
    DeviceRole,
    PowerDirection,
    ReadingProfile,
    _create_mrid,
    _hashed_mrid,
    compute_sfdi,
    create_meter_reading_list,
    create_mup,
    mup_mrid,
    plan_registration,
)
from tests.legacy_readings import readings_from

LFDI = "1234567890abcdef1234567890abcdef12345678"
SIGNED = ReadingProfile.SIGNED_LOAD_CONVENTION
_SLOT_BY_MRID = {_create_mrid(LFDI, index=i).value: i for i in range(0, 65)}


def _system(**values: float) -> dict[str, float]:
    return {
        "W": 1000.0,
        "Var": -200.0,
        "Hz": 60.0,
        "V": 240.0,
        "PF": 0.98,
        "VA": 1020.0,
        "A": 4.25,
    } | values


def _three_phase(**values: float) -> dict[str, float]:
    data = _system()
    for line in (1, 2, 3):
        data |= {
            f"WL{line}": 333.0,
            f"VarL{line}": -66.0,
            f"VL{line}": 139.0,
            f"PFL{line}": 0.98,
            f"VAL{line}": 340.0,
            f"AL{line}": 1.4,
        }
    return data | values


def _types(mup: Any) -> dict[bytes, Any]:
    return {m.m_rid.value: m.reading_type for m in mup.mirror_meter_reading}


def _values(reading_list: Any) -> dict[bytes, int]:
    return {m.m_rid.value: m.reading.value for m in reading_list.mirror_meter_reading}


def _slot_values(reading_list: Any) -> dict[int, int]:
    return {_SLOT_BY_MRID[mrid]: value for mrid, value in _values(reading_list).items()}


class TestSfdi:
    def test_check_digit_makes_the_digit_sum_a_multiple_of_ten(self) -> None:
        sfdi = compute_sfdi(LFDI)
        assert sum(int(d) for d in str(sfdi)) % 10 == 0

    def test_case_does_not_change_the_sfdi(self) -> None:
        assert compute_sfdi(LFDI) == compute_sfdi(LFDI.upper())


class TestMirrorUsagePoint:
    def test_identifies_the_device_and_its_role(self) -> None:
        mup = create_mup(LFDI, readings_from(_system()), 300)

        assert mup.m_rid.value == mup_mrid(LFDI)
        assert mup.device_lfdi == bytes.fromhex(LFDI)
        assert int.from_bytes(mup.role_flags.value, "big") == ROLE_FLAGS_DER
        assert mup.post_rate == 300

    def test_a_site_meter_has_the_site_role_flags(self) -> None:
        mup = create_mup(LFDI, readings_from(_system()), 300, role=DeviceRole.SITE_METER)
        assert int.from_bytes(mup.role_flags.value, "big") == ROLE_FLAGS_SITE

    def test_registers_only_the_readings_supplied(self) -> None:
        mup = create_mup(LFDI, [Reading(uom=38, value=1.0), Reading(uom=33, value=60.0)], 300)
        assert len(mup.mirror_meter_reading) == 2

    def test_device_lfdi_can_name_another_end_device(self) -> None:
        other = "ab" * 20
        mup = create_mup(LFDI, readings_from(_system()), 300, device_lfdi=other)
        assert mup.device_lfdi == bytes.fromhex(other)
        assert mup.m_rid.value == mup_mrid(LFDI)


class TestReleaseSlots:
    def test_system_readings_keep_their_slots(self) -> None:
        mup = create_mup(LFDI, readings_from(_system()), 300)
        assert [_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading] == list(range(1, 8))

    def test_per_line_readings_keep_their_slots(self) -> None:
        mup = create_mup(LFDI, readings_from(_three_phase()), 300)
        slots = {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading}
        assert slots == set(range(1, 8)) | set(range(10, 28))

    def test_energy_totals_keep_their_slots(self) -> None:
        mup = create_mup(LFDI, readings_from({"WhFwd": 10.0, "WhRev": 2.0}), 300)
        assert {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading} == {61, 62}

    def test_signed_load_single_phase_totals_tagged_phase_a_stay_totals(self) -> None:
        readings = readings_from(_system(WHAvail=500.0), wiring="single")
        mup = create_mup(LFDI, readings, 300, profile=SIGNED)
        assert {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading} == set(range(1, 9))

    def test_phase_a_power_on_a_multi_phase_device_is_line_1(self) -> None:
        readings = readings_from(_three_phase(), wiring="three")
        mup = create_mup(LFDI, readings, 300, profile=SIGNED)
        slots = {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading}
        assert 10 in slots and 1 in slots


class TestHashedMrids:
    def test_a_reading_with_no_release_slot_gets_a_hashed_mrid(self) -> None:
        reading = Reading(uom=29, value=240.0, phase=224, data_qualifier=2)
        mup = create_mup(LFDI, [reading], 300)
        mrid = mup.mirror_meter_reading[0].m_rid.value

        assert mrid not in _SLOT_BY_MRID
        assert mrid.hex()[12] == "f"
        assert mrid == _hashed_mrid(LFDI, reading_identity(reading)).value
        assert mrid.hex().endswith(f"{DEFAULT_PEN:08x}")

    def test_hashed_mrids_never_match_a_slot_mrid(self) -> None:
        for slot in range(0, 100):
            assert _create_mrid(LFDI, index=slot).value.hex()[12] != "f"

    def test_a_different_reading_type_gets_a_different_mrid(self) -> None:
        average = Reading(uom=29, value=240.0, data_qualifier=2)
        maximum = Reading(uom=29, value=240.0, data_qualifier=8)
        mup = create_mup(LFDI, [average, maximum], 300)
        first, second = (m.m_rid.value for m in mup.mirror_meter_reading)
        assert first != second

    def test_a_multiplier_is_part_of_the_identity(self) -> None:
        default = create_mup(LFDI, [Reading(uom=5, value=4.0)], 300)
        hundredths = create_mup(LFDI, [Reading(uom=5, value=4.0, multiplier=-2)], 300)
        assert (
            default.mirror_meter_reading[0].m_rid.value
            != hundredths.mirror_meter_reading[0].m_rid.value
        )

    def test_hashed_mrids_are_stable_and_per_device(self) -> None:
        reading = Reading(uom=38, value=1.0, phase=224)
        first = create_mup(LFDI, [reading], 300).mirror_meter_reading[0].m_rid.value
        again = create_mup(LFDI.upper(), [reading], 300).mirror_meter_reading[0].m_rid.value
        other = create_mup("ab" * 20, [reading], 300).mirror_meter_reading[0].m_rid.value
        assert first == again != other

    def test_a_hashed_reading_is_described_by_its_reading_type(self) -> None:
        mup = create_mup(LFDI, [Reading(uom=29, value=240.0, phase=224, data_qualifier=2)], 300)
        assert mup.mirror_meter_reading[0].description == "Voltage ABC avg"


class TestRegistration:
    def test_a_registered_reading_type_is_kept_when_the_sign_flips(self) -> None:
        registered = plan_registration(LFDI, [Reading(uom=38, value=-500.0)])
        plan = plan_registration(LFDI, [Reading(uom=38, value=500.0)], registered=registered)

        (entry,) = plan.values()
        assert entry.reading_type.flow_direction.value == 19

    def test_a_registered_reading_absent_this_cycle_stays_registered(self) -> None:
        registered = plan_registration(LFDI, readings_from(_system()))
        mup = create_mup(LFDI, [Reading(uom=38, value=1.0)], 300, registered=registered)
        assert len(mup.mirror_meter_reading) == 7

    def test_a_new_reading_is_added_to_the_registration(self) -> None:
        registered = plan_registration(LFDI, readings_from(_system()))
        extra = Reading(uom=29, value=240.0, phase=224, data_qualifier=2)
        plan = plan_registration(LFDI, [*readings_from(_system()), extra], registered=registered)
        assert len(plan) == 8 and plan.keys() > registered.keys()

    def test_readings_are_limited_to_the_registered_set(self) -> None:
        registered = plan_registration(LFDI, readings_from(_system()))
        extra = Reading(uom=29, value=240.0, phase=224, data_qualifier=2)
        reading_list = create_meter_reading_list(
            LFDI, [*readings_from(_system()), extra], timestamp=0, registered=registered
        )
        assert set(_values(reading_list)) == set(registered)


class TestValidation:
    @pytest.mark.parametrize(
        "bad",
        [
            Reading(uom=-1, value=1.0),
            Reading(uom=38, value=float("nan")),
            Reading(uom=38, value=float("inf")),
            Reading(uom=38, value=True),
            Reading(uom=38, value=1.0, multiplier=12),
            Reading(uom=38, value=1.0, quality_flags=0x1_0000),
            Reading(uom=38, value=1.0, phase=True),
            "W",
        ],
    )
    def test_an_invalid_reading_is_dropped(self, bad: Any) -> None:
        mup = create_mup(LFDI, [bad, Reading(uom=33, value=60.0)], 300)
        assert len(mup.mirror_meter_reading) == 1

    def test_a_second_reading_with_the_same_identity_is_dropped(self) -> None:
        readings = [Reading(uom=38, value=1.0), Reading(uom=38, value=2.0)]
        reading_list = create_meter_reading_list(LFDI, readings, timestamp=0)
        assert list(_values(reading_list).values()) == [1]


class TestDefaultValues:
    def test_values_are_scaled_and_truncated(self) -> None:
        values = _slot_values(create_meter_reading_list(LFDI, readings_from(_system()), 0))
        assert values == {1: 1000, 2: 200, 3: 60000, 4: 2400, 5: 980, 6: 1020, 7: 42}

    def test_an_interval_reading_covers_the_post_rate(self) -> None:
        reading_list = create_meter_reading_list(
            LFDI, [Reading(uom=29, value=240.0, data_qualifier=2)], 0, post_rate=60
        )
        assert reading_list.mirror_meter_reading[0].reading.time_period.duration == 60

    def test_quality_flags_default_to_valid(self) -> None:
        reading_list = create_meter_reading_list(LFDI, [Reading(uom=38, value=1.0)], 0)
        assert reading_list.mirror_meter_reading[0].reading.quality_flags == b"\x00\x01"

    def test_a_readings_quality_flags_are_posted(self) -> None:
        reading = Reading(uom=38, value=1.0, quality_flags=0x20)
        reading_list = create_meter_reading_list(LFDI, [reading], 0)
        assert reading_list.mirror_meter_reading[0].reading.quality_flags == b"\x00\x20"

    def test_stale_sets_questionable_and_clears_valid(self) -> None:
        reading = Reading(uom=38, value=1.0, quality_flags=0x21)
        reading_list = create_meter_reading_list(LFDI, [reading], 0, stale=True)
        assert reading_list.mirror_meter_reading[0].reading.quality_flags == b"\x00\x30"

    def test_a_reading_multiplier_scales_the_value(self) -> None:
        reading_list = create_meter_reading_list(
            LFDI, [Reading(uom=38, value=12_345.0, multiplier=3)], 0
        )
        assert reading_list.mirror_meter_reading[0].reading.value == 12

    def test_a_net_reading_from_the_connector_keeps_its_sign(self) -> None:
        reading = Reading(uom=38, value=-1500.0, flow_direction=4)
        mup = create_mup(LFDI, [reading], 300)
        reading_list = create_meter_reading_list(LFDI, [reading], 0)
        assert mup.mirror_meter_reading[0].reading_type.flow_direction.value == 4
        assert reading_list.mirror_meter_reading[0].reading.value == -1500


class TestSignedLoad:
    def test_every_reading_type_leaves_commodity_qualifier_and_kind_not_applicable(
        self,
    ) -> None:
        mup = create_mup(LFDI, readings_from(_three_phase(), wiring="three"), 300, profile=SIGNED)
        for reading_type in _types(mup).values():
            assert reading_type.commodity.value == 0
            assert reading_type.data_qualifier.value == 0
            assert reading_type.kind.value == 0
            assert reading_type.flow_direction.value == 0

    def test_a_der_posts_its_output_negative(self) -> None:
        values = _slot_values(
            create_meter_reading_list(LFDI, readings_from(_system()), 0, profile=SIGNED)
        )
        assert values[1] == -1000

    def test_a_site_meter_posts_import_positive(self) -> None:
        values = _slot_values(
            create_meter_reading_list(
                LFDI, readings_from(_system()), 0, profile=SIGNED, role=DeviceRole.SITE_METER
            )
        )
        assert values[1] == 1000

    def test_pf_takes_the_sign_of_the_var_on_its_line(self) -> None:
        readings = readings_from(_three_phase(VarL2=50.0), wiring="three")
        values = _slot_values(create_meter_reading_list(LFDI, readings, 0, profile=SIGNED))
        # A DER's var is negated: absorbing (-66) posts +66, injecting (+50) posts -50.
        assert values[5] > 0 and values[13] > 0
        assert values[19] < 0

    def test_current_is_whole_amperes_rounded_half_away_from_zero(self) -> None:
        values = _slot_values(
            create_meter_reading_list(LFDI, readings_from(_system(A=2.5)), 0, profile=SIGNED)
        )
        assert values[7] == 3

    def test_every_reading_is_an_instant(self) -> None:
        reading_list = create_meter_reading_list(
            LFDI, [Reading(uom=29, value=240.0, data_qualifier=2)], 0, profile=SIGNED
        )
        assert reading_list.mirror_meter_reading[0].reading.time_period.duration == 0


class TestPowerDirection:
    def test_net_posts_power_under_its_own_slots_with_the_sign(self) -> None:
        readings = readings_from(_system(W=-1500.0))
        mup = create_mup(LFDI, readings, 300, power_direction=PowerDirection.NET)
        values = _slot_values(
            create_meter_reading_list(LFDI, readings, 0, power_direction=PowerDirection.NET)
        )
        slots = {_SLOT_BY_MRID[m.m_rid.value] for m in mup.mirror_meter_reading}
        assert {31, 32} <= slots and not {1, 2} & slots
        assert values[31] == -1500

    def test_split_posts_the_active_direction_and_zero(self) -> None:
        values = _slot_values(
            create_meter_reading_list(
                LFDI, readings_from(_system(W=-1500.0)), 0, power_direction=PowerDirection.SPLIT
            )
        )
        assert (values[41], values[51]) == (0, 1500)

    def test_a_phased_total_gets_hashed_mrids_per_direction(self) -> None:
        reading = Reading(uom=38, value=10.0, phase=224)
        mup = create_mup(LFDI, [reading], 300, power_direction=PowerDirection.SPLIT)
        flows = [m.reading_type.flow_direction.value for m in mup.mirror_meter_reading]
        mrids = {m.m_rid.value for m in mup.mirror_meter_reading}
        assert flows == [1, 19] and len(mrids) == 2
