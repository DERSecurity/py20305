"""Tests for telemetry value scaling."""

from __future__ import annotations

import pytest

from py20305.telemetry.scaling import (
    FLOW_NORMAL,
    FLOW_REVERSE,
    ScaledReading,
    scale_a,
    scale_hz,
    scale_pf,
    scale_v,
    scale_va,
    scale_var,
    scale_w,
)


class TestScaleW:
    """Tests for real power (W) scaling."""

    def test_positive_value(self):
        result = scale_w(1000.0)
        assert result == ScaledReading(value=1000, flow_direction=FLOW_NORMAL)

    def test_negative_value_sets_reverse_flow(self):
        result = scale_w(-500.0)
        assert result == ScaledReading(value=500, flow_direction=FLOW_REVERSE)

    def test_zero_value(self):
        result = scale_w(0.0)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_none_returns_zero_normal(self):
        result = scale_w(None)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_truncates_decimal(self):
        result = scale_w(1234.7)
        assert result.value == 1234


class TestScaleVar:
    """Tests for reactive power (Var) scaling."""

    def test_positive_value(self):
        result = scale_var(800.0)
        assert result == ScaledReading(value=800, flow_direction=FLOW_NORMAL)

    def test_negative_value_sets_reverse_flow(self):
        result = scale_var(-300.0)
        assert result == ScaledReading(value=300, flow_direction=FLOW_REVERSE)

    def test_zero_value(self):
        result = scale_var(0.0)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_none_returns_zero_normal(self):
        result = scale_var(None)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)


class TestScaleHz:
    """Tests for frequency (Hz) scaling."""

    def test_applies_1000_multiplier(self):
        result = scale_hz(60.0)
        assert result.value == 60000

    def test_always_normal_flow_direction(self):
        result = scale_hz(60.0)
        assert result.flow_direction == FLOW_NORMAL

    def test_negative_is_abs_wrapped(self):
        result = scale_hz(-50.0)
        assert result.value == 50000
        assert result.flow_direction == FLOW_NORMAL

    def test_zero_value(self):
        result = scale_hz(0.0)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_none_returns_zero_normal(self):
        result = scale_hz(None)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_fractional_hz(self):
        result = scale_hz(59.95)
        assert result.value == 59950


class TestScaleV:
    """Tests for voltage (V) scaling."""

    def test_applies_10_multiplier(self):
        result = scale_v(240.0)
        assert result.value == 2400

    def test_always_abs_and_normal_flow(self):
        result = scale_v(-230.0)
        assert result.value == 2300
        assert result.flow_direction == FLOW_NORMAL

    def test_zero_value(self):
        result = scale_v(0.0)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_none_returns_zero_normal(self):
        result = scale_v(None)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_fractional_voltage(self):
        result = scale_v(240.5)
        assert result.value == 2405


class TestScalePF:
    """Tests for power factor (PF) scaling."""

    def test_applies_1000_multiplier(self):
        result = scale_pf(0.98)
        assert result.value == 980

    def test_positive_is_leading(self):
        result = scale_pf(0.95)
        assert result.flow_direction == FLOW_NORMAL

    def test_negative_sets_reverse_flow_lagging(self):
        result = scale_pf(-0.9)
        assert result.value == 900
        assert result.flow_direction == FLOW_REVERSE

    def test_zero_value(self):
        result = scale_pf(0.0)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_none_returns_zero_normal(self):
        result = scale_pf(None)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_unity_pf(self):
        result = scale_pf(1.0)
        assert result.value == 1000
        assert result.flow_direction == FLOW_NORMAL


class TestScaleVA:
    """Tests for apparent power (VA) scaling."""

    def test_positive_value(self):
        result = scale_va(1200.0)
        assert result == ScaledReading(value=1200, flow_direction=FLOW_NORMAL)

    def test_negative_value_sets_reverse_flow(self):
        result = scale_va(-600.0)
        assert result == ScaledReading(value=600, flow_direction=FLOW_REVERSE)

    def test_zero_value(self):
        result = scale_va(0.0)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_none_returns_zero_normal(self):
        result = scale_va(None)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)


class TestScaleA:
    """Tests for current (A) scaling."""

    def test_applies_10_multiplier(self):
        result = scale_a(10.5)
        assert result.value == 105

    def test_negative_sets_reverse_flow(self):
        result = scale_a(-5.0)
        assert result.value == 50
        assert result.flow_direction == FLOW_REVERSE

    def test_positive_normal_flow(self):
        result = scale_a(15.0)
        assert result.value == 150
        assert result.flow_direction == FLOW_NORMAL

    def test_zero_value(self):
        result = scale_a(0.0)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    def test_none_returns_zero_normal(self):
        result = scale_a(None)
        assert result == ScaledReading(value=0, flow_direction=FLOW_NORMAL)


class TestScaledReadingImmutability:
    """Test that ScaledReading is immutable."""

    def test_frozen(self):
        reading = ScaledReading(value=100, flow_direction=1)
        with pytest.raises(AttributeError):
            reading.value = 200  # type: ignore[misc]
