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
)
from py20305.xml.serialization import to_xml

LFDI = "1234567890abcdef1234567890abcdef12345678"
AS_REPORTED = ReadingProfile.SIGNED_AS_REPORTED
_SLOT_BY_MRID = {_create_mrid(LFDI, index=i).value: i for i in range(1, 70)}
W_SLOT = 1


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
