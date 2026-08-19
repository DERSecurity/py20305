"""Value scaling and flow direction for telemetry readings.

Scaling transforms raw connector values into IEEE 2030.5 format with appropriate
multipliers and flow direction codes.
"""

from __future__ import annotations

from dataclasses import dataclass

# Flow direction codes per IEEE 2030.5
FLOW_NORMAL = 1
FLOW_REVERSE = 19


@dataclass(frozen=True, slots=True)
class ScaledReading:
    """A scaled reading value with flow direction."""

    value: int
    flow_direction: int


def scale_w(raw: float | None) -> ScaledReading:
    """Scale real power (W).

    No multiplier scaling. Flow direction is reverse if negative.
    """
    if raw is None:
        return ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    value = int(raw)
    if value < 0:
        return ScaledReading(value=abs(value), flow_direction=FLOW_REVERSE)
    return ScaledReading(value=value, flow_direction=FLOW_NORMAL)


def scale_var(raw: float | None) -> ScaledReading:
    """Scale reactive power (Var).

    No multiplier scaling. Flow direction is reverse if negative.
    """
    if raw is None:
        return ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    value = int(raw)
    if value < 0:
        return ScaledReading(value=abs(value), flow_direction=FLOW_REVERSE)
    return ScaledReading(value=value, flow_direction=FLOW_NORMAL)


def scale_hz(raw: float | None) -> ScaledReading:
    """Scale frequency (Hz).

    Multiplier: -3 (x1000). Always abs(), always normal flow direction.
    """
    if raw is None:
        return ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    value = abs(int(raw * 1000))
    return ScaledReading(value=value, flow_direction=FLOW_NORMAL)


def scale_v(raw: float | None) -> ScaledReading:
    """Scale voltage (V).

    Multiplier: -1 (x10). Always abs(), always normal flow direction.
    """
    if raw is None:
        return ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    value = abs(int(raw * 10))
    return ScaledReading(value=value, flow_direction=FLOW_NORMAL)


def scale_pf(raw: float | None) -> ScaledReading:
    """Scale power factor (PF).

    Multiplier: -3 (x1000). Flow direction is reverse if negative (lagging).
    """
    if raw is None:
        return ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    value = int(raw * 1000)
    if value < 0:
        return ScaledReading(value=abs(value), flow_direction=FLOW_REVERSE)
    return ScaledReading(value=value, flow_direction=FLOW_NORMAL)


def scale_va(raw: float | None) -> ScaledReading:
    """Scale apparent power (VA).

    No multiplier scaling. Flow direction is reverse if negative.
    """
    if raw is None:
        return ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    value = int(raw)
    if value < 0:
        return ScaledReading(value=abs(value), flow_direction=FLOW_REVERSE)
    return ScaledReading(value=value, flow_direction=FLOW_NORMAL)


def scale_a(raw: float | None) -> ScaledReading:
    """Scale current (A).

    Multiplier: -1 (x10). Flow direction is reverse if negative.
    """
    if raw is None:
        return ScaledReading(value=0, flow_direction=FLOW_NORMAL)

    value = int(raw * 10)
    if value < 0:
        return ScaledReading(value=abs(value), flow_direction=FLOW_REVERSE)
    return ScaledReading(value=value, flow_direction=FLOW_NORMAL)
