"""Tests for XML serialization/deserialization."""

import pytest

from py20305.models.sep.sep import DeviceCapability, EndDeviceList, Registration, Time
from py20305.xml.serialization import (
    APPLICATION_SEP_XML,
    XmlParseError,
    from_xml,
    to_xml,
    validate_xml,
)
from tests.conftest import make_time


def test_application_sep_xml_constant():
    assert APPLICATION_SEP_XML == "application/sep+xml"


def test_time_roundtrip():
    """A Time model can be serialized to XML and deserialized back."""
    original = make_time(1234567890, 5)
    xml_bytes = to_xml(original)
    assert b"1234567890" in xml_bytes
    restored = from_xml(xml_bytes, Time)
    assert restored.current_time.value == original.current_time.value
    assert restored.quality == original.quality


def test_to_xml_returns_bytes():
    result = to_xml(make_time(100))
    assert isinstance(result, bytes)


def test_from_xml_accepts_string():
    original = make_time(42, 1)
    xml_bytes = to_xml(original)
    xml_str = xml_bytes.decode("utf-8")
    restored = from_xml(xml_str, Time)
    assert restored.current_time.value == 42


def test_xml_contains_namespace():
    xml_bytes = to_xml(make_time())
    assert b"urn:ieee:std:2030.5:ns" in xml_bytes


def test_validate_time_against_xsd():
    """Serialized Time passes XSD validation."""
    xml_bytes = to_xml(make_time(1000, 3))
    errors = validate_xml(xml_bytes)
    assert errors == []


def test_validate_device_capability_against_xsd():
    """Serialized DeviceCapability passes XSD validation."""
    dc = DeviceCapability()
    xml_bytes = to_xml(dc)
    errors = validate_xml(xml_bytes)
    assert errors == []


def test_validate_registration_against_xsd():
    """Serialized Registration passes XSD validation."""
    from py20305.models.sep.sep import Pintype, TimeType

    reg = Registration(
        date_time_registered=TimeType(value=1000),
        p_in=Pintype(value=12345),
    )
    xml_bytes = to_xml(reg)
    errors = validate_xml(xml_bytes)
    assert errors == []


def test_validate_end_device_list_against_xsd():
    """Serialized EndDeviceList passes XSD validation."""
    edl = EndDeviceList(**{"all": 0, "results": 0})
    xml_bytes = to_xml(edl)
    errors = validate_xml(xml_bytes)
    assert errors == []


def test_validate_invalid_xml_returns_errors():
    """Invalid XML returns a non-empty error list."""
    bad_xml = b'<Time xmlns="urn:ieee:std:2030.5:ns"><bogusElement>x</bogusElement></Time>'
    errors = validate_xml(bad_xml)
    assert len(errors) > 0


# --- CSIP-AUS namespace tests ---


def test_to_xml_excludes_csipaus_by_default():
    """Default to_xml() does NOT include csipaus namespace."""
    xml_bytes = to_xml(make_time())
    assert b"csipaus" not in xml_bytes
    assert b"https://csipaus.org/ns" not in xml_bytes


def test_to_xml_includes_csipaus_when_requested():
    """to_xml(include_csipaus=True) includes the CSIP-AUS namespace declaration."""
    xml_bytes = to_xml(make_time(), include_csipaus=True)
    assert b"csipaus" in xml_bytes
    assert b"https://csipaus.org/ns" in xml_bytes


# --- IEEE 2030.5-2018 compatibility tests ---


def test_to_xml_includes_schema_ver_by_default():
    """Models with schemaVer include it by default."""
    dc = DeviceCapability()
    xml_bytes = to_xml(dc)
    assert b'schemaVer="2.2"' in xml_bytes


def test_to_xml_2018_compat_omits_schema_ver():
    """server_2018_compat strips schemaVer from the root element."""
    dc = DeviceCapability()
    xml_bytes = to_xml(dc, server_2018_compat=True)
    assert b"schemaVer" not in xml_bytes


def test_to_xml_2018_compat_preserves_other_attributes():
    """server_2018_compat only strips 2023-specific attributes."""
    dc = DeviceCapability()
    xml_bytes = to_xml(dc, server_2018_compat=True)
    assert b"pollRate" in xml_bytes
    assert b"schemaVer" not in xml_bytes


def test_to_xml_2018_compat_omits_subscribable():
    """server_2018_compat strips subscribable from MirrorUsagePoint."""
    from py20305.telemetry.mup import create_mup

    mup = create_mup(
        "a" * 40,
        {"W": 1000, "Var": 0, "Hz": 60, "V": 240, "PF": 1, "VA": 1000, "A": 4},
        300,
    )
    xml_bytes = to_xml(mup, server_2018_compat=True)
    assert b"subscribable" not in xml_bytes
    assert b"schemaVer" not in xml_bytes
    # Core content still present
    assert b"<MirrorUsagePoint" in xml_bytes
    assert b"<deviceLFDI>" in xml_bytes


def test_to_xml_includes_subscribable_by_default():
    """MirrorUsagePoint includes subscribable by default."""
    from py20305.telemetry.mup import create_mup

    mup = create_mup(
        "a" * 40,
        {"W": 1000, "Var": 0, "Hz": 60, "V": 240, "PF": 1, "VA": 1000, "A": 4},
        300,
    )
    xml_bytes = to_xml(mup)
    assert b'subscribable="0"' in xml_bytes


# -- XmlParseError --------------------------------------------------------------
#
# from_xml is on the network boundary: any byte string the upstream server
# returns lands here. Each of these cases used to surface as an xsdata
# ParserError traceback in the polling loop's logs. After the wrapping change
# they raise XmlParseError with a one-line operator-facing message so callers
# can log without exc_info=True.


def test_from_xml_empty_body_raises_xml_parse_error():
    """Empty 200 responses are the headline operational case: the server
    replied successfully but the body is zero bytes. xsdata's
    XMLSyntaxError 'no element found' should not reach polling code."""
    with pytest.raises(XmlParseError) as excinfo:
        from_xml(b"", Time)
    err = excinfo.value
    assert err.model_name == "Time"
    assert err.body_length == 0
    assert "empty body" in str(err)
    # Original xsdata error preserved for callers that want to introspect.
    assert excinfo.value.__cause__ is not None


def test_from_xml_garbage_bytes_raises_xml_parse_error():
    """Non-XML bytes (e.g. an HTML error page leaked through) produce a
    parse error rather than a generic ValueError."""
    with pytest.raises(XmlParseError) as excinfo:
        from_xml(b"<!doctype html><body>500 Internal Server Error</body>", Time)
    err = excinfo.value
    assert err.model_name == "Time"
    assert err.body_length > 0
    # Snippet of the body appears in the message for log diagnosis.
    assert "Internal Server Error" in str(err)


def test_from_xml_truncated_xml_raises_xml_parse_error():
    """Well-formed prefix but truncated mid-element."""
    truncated = b'<DeviceCapability xmlns="urn:ieee:std:2030.5:ns"><pollRate>30'
    with pytest.raises(XmlParseError):
        from_xml(truncated, DeviceCapability)


def test_xml_parse_error_is_value_error():
    """XmlParseError extends ValueError so callers expecting a generic
    'bad data' exception continue to work without typed handling."""
    with pytest.raises(ValueError):
        from_xml(b"", Time)


def test_from_xml_wrong_root_element_raises_xml_parse_error():
    """xsdata-pydantic silently produces a partial instance when the
    root element is not the expected one, which then fails pydantic
    field validation. The wrapper folds both stages into one typed
    error so the polling loop logs a single coherent diagnostic."""
    body = b'<Wrong xmlns="urn:ieee:std:2030.5:ns"/>'
    with pytest.raises(XmlParseError) as excinfo:
        from_xml(body, Time)
    err = excinfo.value
    assert err.model_name == "Time"
    assert err.body_length == len(body)
