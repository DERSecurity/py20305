"""Tests for scale factor conversion utilities."""

from __future__ import annotations

from py20305.models.sep.sep import (
    ActivePower,
    AmpereHour,
    ApparentPower,
    PowerFactor,
    ReactivePower,
    ReactiveSusceptance,
    VoltageRms,
    WattHour,
)
from py20305.telemetry.scale_factor import (
    float_to_int,
    get_sf,
    to_active_power,
    to_amp_hour,
    to_apparent_power,
    to_power_factor,
    to_reactive_power,
    to_reactive_susceptance,
    to_voltage_rms,
    to_watt_hour,
)


class TestFloatToInt:
    """Tests for float_to_int."""

    def test_integer_value(self) -> None:
        assert float_to_int(100.0) == (100, 0)

    def test_integer_no_decimal(self) -> None:
        assert float_to_int(15000.0) == (15000, 0)

    def test_one_decimal(self) -> None:
        assert float_to_int(3.5) == (35, -1)

    def test_two_decimals(self) -> None:
        assert float_to_int(3.14) == (314, -2)

    def test_three_decimals(self) -> None:
        assert float_to_int(0.800) == (8, -1)

    def test_zero(self) -> None:
        assert float_to_int(0.0) == (0, 0)

    def test_negative_value(self) -> None:
        val, mult = float_to_int(-2.5)
        assert val == -25
        assert mult == -1

    def test_trailing_zeros_stripped(self) -> None:
        # 1.50 should be (15, -1) not (150, -2)
        assert float_to_int(1.50) == (15, -1)

    def test_fp_noise_handled(self) -> None:
        # 0.1 + 0.2 = 0.30000000000000004 in floating point
        result = float_to_int(round(0.1 + 0.2, 9))
        assert result == (3, -1)

    def test_large_value(self) -> None:
        assert float_to_int(15000.0) == (15000, 0)

    def test_small_decimal(self) -> None:
        assert float_to_int(0.001) == (1, -3)

    def test_int16_overflow_positive(self) -> None:
        """500 kW inverter: 500000 exceeds Int16, must scale up."""
        val, mult = float_to_int(500000.0)
        assert -32768 <= val <= 32767
        assert val == 5000
        assert mult == 2

    def test_int16_overflow_negative(self) -> None:
        val, mult = float_to_int(-500000.0)
        assert -32768 <= val <= 32767
        assert val == -5000
        assert mult == 2

    def test_int16_boundary_no_clamp(self) -> None:
        """32767 is exactly at the boundary -- no clamping needed."""
        assert float_to_int(32767.0) == (32767, 0)

    def test_int16_just_over_boundary(self) -> None:
        """32768 exceeds Int16 by one."""
        val, mult = float_to_int(32768.0)
        assert -32768 <= val <= 32767
        assert val == 3277
        assert mult == 1

    def test_int16_overflow_with_decimals(self) -> None:
        """Large value with decimals: 50000.5 -> initially (500005, -1)."""
        val, _mult = float_to_int(50000.5)
        assert -32768 <= val <= 32767


class TestGetSf:
    """Tests for get_sf."""

    def test_none_returns_none(self) -> None:
        assert get_sf(None) is None

    def test_string_returns_none(self) -> None:
        assert get_sf("abc") is None

    def test_list_returns_none(self) -> None:
        assert get_sf([1, 2, 3]) is None

    def test_integer(self) -> None:
        result = get_sf(15000)
        assert result == {"value": 15000, "multiplier": 0}

    def test_float(self) -> None:
        result = get_sf(0.800)
        assert result == {"value": 8, "multiplier": -1}

    def test_custom_value_name(self) -> None:
        result = get_sf(0.850, "displacement")
        assert result == {"displacement": 85, "multiplier": -2}

    def test_zero_value(self) -> None:
        result = get_sf(0)
        assert result == {"value": 0, "multiplier": 0}

    def test_negative_float(self) -> None:
        result = get_sf(-2.5)
        assert result == {"value": -25, "multiplier": -1}

    def test_dict_passthrough_valid(self) -> None:
        result = get_sf({"value": 100, "multiplier": -2})
        assert result == {"value": 100, "multiplier": -2}

    def test_dict_passthrough_with_custom_name(self) -> None:
        result = get_sf({"displacement": 850, "multiplier": -3}, "displacement")
        assert result == {"displacement": 850, "multiplier": -3}

    def test_dict_missing_multiplier(self) -> None:
        assert get_sf({"value": 100}) is None

    def test_dict_missing_value(self) -> None:
        assert get_sf({"multiplier": -2}) is None

    def test_dict_float_value_converted(self) -> None:
        result = get_sf({"value": 3.14, "multiplier": 0})
        assert result == {"value": 314, "multiplier": -2}

    def test_dict_non_int_multiplier(self) -> None:
        assert get_sf({"value": 100, "multiplier": 1.5}) is None

    def test_dict_multiplier_out_of_range_positive(self) -> None:
        """Multiplier 2805 from misaligned SunSpec registers is rejected."""
        assert get_sf({"value": 550, "multiplier": 2805}) is None

    def test_dict_multiplier_out_of_range_negative(self) -> None:
        assert get_sf({"value": 100, "multiplier": -200}) is None

    def test_dict_multiplier_at_boundary(self) -> None:
        """Multiplier at byte boundaries (-128, 127) is accepted."""
        assert get_sf({"value": 1, "multiplier": 127}) is not None
        assert get_sf({"value": 1, "multiplier": -128}) is not None

    def test_pf_value_from_connector(self) -> None:
        """Power factor 0.8 with displacement name."""
        result = get_sf(0.8, "displacement")
        assert result is not None
        assert result["displacement"] == 8
        assert result["multiplier"] == -1

    def test_nameplate_wmax(self) -> None:
        """Typical WMaxRtg integer value from PrintDemoConnector."""
        result = get_sf(15000)
        assert result == {"value": 15000, "multiplier": 0}

    def test_nameplate_var(self) -> None:
        """Typical VarMaxInjRtg value from PrintDemoConnector."""
        result = get_sf(4400)
        assert result == {"value": 4400, "multiplier": 0}

    def test_large_nameplate_clamped(self) -> None:
        """500 kW inverter nameplate must be clamped to Int16."""
        result = get_sf(500000)
        assert result is not None
        assert -32768 <= result["value"] <= 32767
        assert result == {"value": 5000, "multiplier": 2}

    def test_dict_int_overflow_clamped(self) -> None:
        """SunSpec connector returns {value: 50000, multiplier: 1} -- must clamp."""
        result = get_sf({"value": 50000, "multiplier": 1})
        assert result is not None
        assert -32768 <= result["value"] <= 32767
        assert result == {"value": 5000, "multiplier": 2}

    def test_dict_int_negative_overflow_clamped(self) -> None:
        result = get_sf({"value": -40000, "multiplier": 0})
        assert result is not None
        assert -32768 <= result["value"] <= 32767
        assert result == {"value": -4000, "multiplier": 1}

    def test_dict_int_at_boundary_no_clamp(self) -> None:
        """32767 fits in Int16 -- no clamping needed."""
        result = get_sf({"value": 32767, "multiplier": 0})
        assert result == {"value": 32767, "multiplier": 0}


class TestToActivePower:
    """Tests for to_active_power factory."""

    def test_integer(self) -> None:
        result = to_active_power(15000)
        assert isinstance(result, ActivePower)
        assert result.value == 15000
        assert result.multiplier.value == 0

    def test_float(self) -> None:
        result = to_active_power(3.5)
        assert isinstance(result, ActivePower)
        assert result.value == 35
        assert result.multiplier.value == -1

    def test_dict(self) -> None:
        result = to_active_power({"value": 5000, "multiplier": -1})
        assert isinstance(result, ActivePower)
        assert result.value == 5000
        assert result.multiplier.value == -1

    def test_none_returns_none(self) -> None:
        assert to_active_power(None) is None

    def test_zero(self) -> None:
        result = to_active_power(0)
        assert isinstance(result, ActivePower)
        assert result.value == 0
        assert result.multiplier.value == 0

    def test_large_value_clamped(self) -> None:
        """500 kW rating must fit Int16 after clamping."""
        result = to_active_power(500000)
        assert isinstance(result, ActivePower)
        assert -32768 <= result.value <= 32767
        assert result.value == 5000
        assert result.multiplier.value == 2


class TestToWattHour:
    """Tests for to_watt_hour factory (rtgMaxWh energy capacity)."""

    def test_integer(self) -> None:
        result = to_watt_hour(30000)
        assert isinstance(result, WattHour)
        assert result.value == 30000
        assert result.multiplier.value == 0

    def test_zero(self) -> None:
        result = to_watt_hour(0)
        assert isinstance(result, WattHour)
        assert result.value == 0

    def test_none_returns_none(self) -> None:
        assert to_watt_hour(None) is None


class TestToAmpHour:
    """Tests for to_amp_hour factory (rtgMaxAh/setMaxAh energy capacity)."""

    def test_integer(self) -> None:
        result = to_amp_hour(120)
        assert isinstance(result, AmpereHour)
        assert result.value == 120
        assert result.multiplier.value == 0

    def test_zero(self) -> None:
        result = to_amp_hour(0)
        assert isinstance(result, AmpereHour)
        assert result.value == 0

    def test_none_returns_none(self) -> None:
        assert to_amp_hour(None) is None


class TestToApparentPower:
    """Tests for to_apparent_power factory."""

    def test_integer(self) -> None:
        result = to_apparent_power(7000)
        assert isinstance(result, ApparentPower)
        assert result.value == 7000

    def test_none_returns_none(self) -> None:
        assert to_apparent_power(None) is None


class TestToReactivePower:
    """Tests for to_reactive_power factory."""

    def test_integer(self) -> None:
        result = to_reactive_power(4400)
        assert isinstance(result, ReactivePower)
        assert result.value == 4400
        assert result.multiplier.value == 0

    def test_none_returns_none(self) -> None:
        assert to_reactive_power(None) is None


class TestToReactiveSusceptance:
    """Tests for to_reactive_susceptance factory."""

    def test_zero_is_valid(self) -> None:
        result = to_reactive_susceptance(0)
        assert isinstance(result, ReactiveSusceptance)
        assert result.value == 0
        assert result.multiplier.value == 0

    def test_none_returns_none(self) -> None:
        assert to_reactive_susceptance(None) is None


class TestToVoltageRms:
    """Tests for to_voltage_rms factory."""

    def test_integer(self) -> None:
        result = to_voltage_rms(240)
        assert isinstance(result, VoltageRms)
        assert result.value == 240

    def test_none_returns_none(self) -> None:
        assert to_voltage_rms(None) is None


class TestToPowerFactor:
    """Tests for to_power_factor factory."""

    def test_float(self) -> None:
        result = to_power_factor(0.85)
        assert isinstance(result, PowerFactor)
        assert result.displacement == 85
        assert result.multiplier.value == -2

    def test_dict_with_displacement(self) -> None:
        result = to_power_factor({"displacement": 850, "multiplier": -3})
        assert isinstance(result, PowerFactor)
        assert result.displacement == 850
        assert result.multiplier.value == -3

    def test_none_returns_none(self) -> None:
        assert to_power_factor(None) is None

    def test_zero_pf(self) -> None:
        result = to_power_factor(0)
        assert isinstance(result, PowerFactor)
        assert result.displacement == 0
