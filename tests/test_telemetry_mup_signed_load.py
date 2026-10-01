"""Tests for the SIGNED_LOAD_CONVENTION reading profile."""

from __future__ import annotations

from typing import Any

import pytest

from py20305.connectors.base import ReadingOverride
from py20305.telemetry.mup import (
    ReadingProfile,
    _create_mrid,
    create_meter_reading_list,
    create_mup,
    registration_slots,
)

LFDI = "1234567890abcdef1234567890abcdef12345678"
SIGNED = ReadingProfile.SIGNED_LOAD_CONVENTION
_SLOT_BY_MRID = {_create_mrid(LFDI, index=i).value: i for i in range(1, 31)}

SYSTEM_SLOTS = {"W": 1, "Var": 2, "Hz": 3, "V": 4, "PF": 5, "VA": 6, "A": 7}


def _three_phase(**extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "W": 3000.0,
        "Var": 300.0,
        "Hz": 60.0,
        "V": 240.0,
        "PF": 0.99,
        "VA": 3015.0,
        "A": 12.5,
        "ACType": 2,
    }
    for line in (1, 2, 3):
        data |= {
            f"WL{line}": 1000.0,
            f"VarL{line}": 100.0,
            f"VL{line}": 139.0,
            f"PFL{line}": 0.99,
            f"VAL{line}": 1005.0,
            f"AL{line}": 4.2,
        }
    return data | extra


def _reading_types(mup: Any) -> dict[int, Any]:
    return {_SLOT_BY_MRID[m.m_rid.value]: m.reading_type for m in mup.mirror_meter_reading}


def _readings(readings: Any) -> dict[int, Any]:
    return {_SLOT_BY_MRID[m.m_rid.value]: m.reading for m in readings.mirror_meter_reading}


def _values(monitoring: dict[str, Any], **kwargs: Any) -> dict[int, int]:
    built = create_meter_reading_list(LFDI, monitoring, timestamp=0, profile=SIGNED, **kwargs)
    return {slot: reading.value for slot, reading in _readings(built).items()}


class TestReadingTypes:
    def test_not_applicable_fields_on_every_reading(self) -> None:
        mup = create_mup(LFDI, _three_phase(WHAvail=5000, VL1L2=240.0), 300, profile=SIGNED)

        for reading_type in _reading_types(mup).values():
            assert reading_type.flow_direction.value == 0
            assert reading_type.commodity.value == 0
            assert reading_type.data_qualifier.value == 0
            assert reading_type.kind.value == 0
            assert reading_type.accumulation_behaviour.value == 12

    def test_system_voltage_and_frequency_carry_no_phase(self) -> None:
        types = _reading_types(create_mup(LFDI, _three_phase(), 300, profile=SIGNED))

        assert types[SYSTEM_SLOTS["V"]].phase is None
        assert types[SYSTEM_SLOTS["Hz"]].phase is None

    @pytest.mark.parametrize(("ac_type", "phase"), [(2, 224), (1, 132), (0, 128), (None, None)])
    def test_totals_phase_follows_ac_type(self, ac_type: int | None, phase: int | None) -> None:
        monitoring = _three_phase(WHAvail=5000)
        if ac_type is None:
            del monitoring["ACType"]
        else:
            monitoring["ACType"] = ac_type

        types = _reading_types(create_mup(LFDI, monitoring, 300, profile=SIGNED))

        for slot in (SYSTEM_SLOTS["W"], SYSTEM_SLOTS["Var"], SYSTEM_SLOTS["VA"], 7, 8):
            assert (types[slot].phase.value if types[slot].phase else None) == phase

    def test_per_line_phase_codes_are_kept(self) -> None:
        types = _reading_types(create_mup(LFDI, _three_phase(), 300, profile=SIGNED))

        assert types[10].phase.value == 128  # WL1
        assert types[12].phase.value == 129  # VL1

    def test_current_multiplier_is_zero(self) -> None:
        types = _reading_types(create_mup(LFDI, _three_phase(), 300, profile=SIGNED))

        assert types[SYSTEM_SLOTS["A"]].power_of_ten_multiplier.value == 0
        assert types[15].power_of_ten_multiplier.value == 0  # AL1

    def test_sign_does_not_change_the_reading_type(self) -> None:
        exporting = create_mup(LFDI, _three_phase(W=3000.0, Var=300.0), 300, profile=SIGNED)
        absorbing = create_mup(LFDI, _three_phase(W=-3000.0, Var=-300.0), 300, profile=SIGNED)

        assert _reading_types(exporting) == _reading_types(absorbing)

    def test_connector_override_takes_precedence(self) -> None:
        overrides = {"W": ReadingOverride(phase=128, kind=37)}

        types = _reading_types(create_mup(LFDI, _three_phase(), 300, overrides, profile=SIGNED))

        assert types[SYSTEM_SLOTS["W"]].phase.value == 128
        assert types[SYSTEM_SLOTS["W"]].kind.value == 37


class TestValues:
    def test_active_power_uses_load_convention(self) -> None:
        assert _values(_three_phase(W=5000.0))[SYSTEM_SLOTS["W"]] == -5000
        assert _values(_three_phase(W=-3000.0))[SYSTEM_SLOTS["W"]] == 3000
        assert _values(_three_phase(WL1=1200.0))[10] == -1200

    def test_current_rounds_to_nearest_ampere(self) -> None:
        values = _values(_three_phase(A=12.7, AL1=4.4))

        assert values[SYSTEM_SLOTS["A"]] == 13
        assert values[15] == 4

    def test_current_is_always_positive(self) -> None:
        assert _values(_three_phase(A=-12.5))[SYSTEM_SLOTS["A"]] == 13

    def test_frequency_rounding_avoids_float_truncation(self) -> None:
        assert _values(_three_phase(Hz=64.02))[SYSTEM_SLOTS["Hz"]] == 64020

    def test_injecting_reactive_power_is_negative_with_negative_pf(self) -> None:
        values = _values(_three_phase(Var=500.0, PF=0.95))

        assert values[SYSTEM_SLOTS["Var"]] == -500
        assert values[SYSTEM_SLOTS["PF"]] == -950

    def test_absorbing_reactive_power_is_positive_with_positive_pf(self) -> None:
        # SunSpec's PF sign follows W; the profile ignores it.
        values = _values(_three_phase(Var=-500.0, PF=-0.95))

        assert values[SYSTEM_SLOTS["Var"]] == 500
        assert values[SYSTEM_SLOTS["PF"]] == 950

    def test_zero_var_gives_positive_pf(self) -> None:
        values = _values(_three_phase(Var=0.0, PF=-1.0))

        assert values[SYSTEM_SLOTS["PF"]] == 1000

    def test_per_line_pf_follows_that_lines_var(self) -> None:
        values = _values(_three_phase(VarL1=200.0, VarL2=-200.0, PFL1=0.9, PFL2=0.9))

        assert values[13] == -900  # PFL1
        assert values[19] == 900  # PFL2

    def test_pf_without_var_is_the_unsigned_magnitude(self) -> None:
        monitoring = _three_phase(PF=-0.95)
        del monitoring["Var"]

        values = _values(monitoring)

        assert SYSTEM_SLOTS["Var"] not in values
        assert values[SYSTEM_SLOTS["PF"]] == 950

    def test_multiplier_override_rescales_the_value(self) -> None:
        overrides = {"A": ReadingOverride(multiplier=-2)}

        assert _values(_three_phase(A=12.7), overrides=overrides)[SYSTEM_SLOTS["A"]] == 1270

    def test_every_reading_has_zero_duration(self) -> None:
        built = create_meter_reading_list(
            LFDI, _three_phase(WHAvail=5000), timestamp=0, post_rate=300, profile=SIGNED
        )

        for reading in _readings(built).values():
            assert reading.time_period.duration == 0

    def test_data_qualifier_override_keeps_zero_duration(self) -> None:
        overrides = {"W": ReadingOverride(data_qualifier=8)}

        built = create_meter_reading_list(
            LFDI, _three_phase(), timestamp=0, overrides=overrides, profile=SIGNED
        )

        assert _readings(built)[SYSTEM_SLOTS["W"]].time_period.duration == 0


class TestOptionalReadings:
    def test_three_phase_line_to_line_voltages(self) -> None:
        monitoring = _three_phase(VL1L2=240.4, VL2L3=241.0, VL3L1=239.6)

        types = _reading_types(create_mup(LFDI, monitoring, 300, profile=SIGNED))
        values = _values(monitoring)

        assert {slot: types[slot].phase.value for slot in (28, 29, 30)} == {
            28: 132,
            29: 66,
            30: 40,
        }
        assert types[28].uom.value == 29
        assert types[28].power_of_ten_multiplier.value == -1
        assert (values[28], values[29], values[30]) == (2404, 2410, 2396)

    def test_split_phase_has_only_l1_l2(self) -> None:
        monitoring = _three_phase(ACType=1, VL1L2=240.0, VL2L3=241.0, VL3L1=239.0)

        slots = registration_slots(monitoring, SIGNED)

        assert 28 in slots
        assert not {29, 30} & slots

    def test_single_phase_has_no_line_to_line_voltages(self) -> None:
        monitoring = _three_phase(ACType=0, VL1L2=240.0)

        assert 28 not in registration_slots(monitoring, SIGNED)

    def test_unpopulated_line_to_line_voltage_is_not_registered(self) -> None:
        monitoring = _three_phase(VL1L2=240.0, VL2L3=None)

        slots = registration_slots(monitoring, SIGNED)

        assert 28 in slots
        assert 29 not in slots

    def test_state_of_energy(self) -> None:
        monitoring = _three_phase(WHAvail=9876.4)

        types = _reading_types(create_mup(LFDI, monitoring, 300, profile=SIGNED))

        assert types[8].uom.value == 72
        assert types[8].power_of_ten_multiplier.value == 0
        assert _values(monitoring)[8] == 9876

    def test_no_state_of_energy_without_storage(self) -> None:
        assert 8 not in registration_slots(_three_phase(), SIGNED)

    def test_default_profile_ignores_optional_readings(self) -> None:
        monitoring = _three_phase(WHAvail=5000, VL1L2=240.0)

        slots = registration_slots(monitoring, ReadingProfile.DEFAULT)

        assert not {8, 28} & slots

    def test_registered_reading_survives_a_cycle_without_it(self) -> None:
        registered = registration_slots(_three_phase(WHAvail=5000), SIGNED)

        mup = create_mup(LFDI, _three_phase(), 300, profile=SIGNED, registered=registered)

        assert 8 in _reading_types(mup)
        assert registration_slots(_three_phase(), SIGNED, registered) == registered

    def test_registration_grows_when_a_reading_appears(self) -> None:
        registered = registration_slots(_three_phase(), SIGNED)

        grown = registration_slots(_three_phase(WHAvail=5000), SIGNED, registered)

        assert grown == registered | {8}

    def test_existing_mrids_are_kept_across_profiles(self) -> None:
        default = registration_slots(_three_phase(), ReadingProfile.DEFAULT)

        assert default <= registration_slots(_three_phase(WHAvail=1), SIGNED)
