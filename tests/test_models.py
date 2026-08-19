"""Tests for IEEE 2030.5 model imports and basic validation."""

from py20305.models import (
    Dercontrol,
    DercontrolBase,
    Dercurve,
    Derprogram,
    DeviceCapability,
    EndDevice,
    EndDeviceList,
    FunctionSetAssignments,
    MirrorMeterReading,
    MirrorUsagePoint,
    Registration,
    Time,
)
from py20305.models.csipaus import (
    ConnectionPoint,
    DoecontrolType,
    OpModExpLimW,
    OpModImpLimW,
)
from py20305.models.sep.sep import TimeOffsetType, TimeType


def test_key_sep_types_importable():
    """All key SEP2 types can be imported."""
    assert DeviceCapability is not None
    assert EndDevice is not None
    assert EndDeviceList is not None
    assert Dercontrol is not None
    assert DercontrolBase is not None
    assert Dercurve is not None
    assert Derprogram is not None
    assert FunctionSetAssignments is not None
    assert MirrorMeterReading is not None
    assert MirrorUsagePoint is not None
    assert Registration is not None
    assert Time is not None


def test_csipaus_types_importable():
    """Australian extension types can be imported."""
    assert ConnectionPoint is not None
    assert DoecontrolType is not None
    assert OpModExpLimW is not None
    assert OpModImpLimW is not None


def test_time_instantiation():
    """Time model can be instantiated with required fields."""
    tt = TimeType(value=1000)
    zero = TimeOffsetType(value=0)
    t = Time(
        current_time=tt,
        dst_end_time=tt,
        dst_offset=zero,
        dst_start_time=tt,
        quality=0,
        tz_offset=zero,
    )
    assert t.current_time.value == 1000


def test_device_capability_instantiation():
    """DeviceCapability model can be instantiated."""
    dc = DeviceCapability()
    assert dc is not None
