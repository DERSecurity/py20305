"""IEEE 2030.5 XML sample fixtures for schema validation and forwarder tests.

Provides both valid and intentionally invalid XML for testing the XSD validator,
the MQTT adapter payload serialization, and the HTTP client forwarding pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Namespace
# ---------------------------------------------------------------------------
NS = 'xmlns="urn:ieee:std:2030.5:ns"'

# ---------------------------------------------------------------------------
# Valid XML samples (should pass XSD validation)
# ---------------------------------------------------------------------------

VALID_DEVICE_CAPABILITY = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DeviceCapability {NS} href="/dcap" pollRate="900">
  <TimeLink href="/tm"/>
  <EndDeviceListLink href="/edev" all="1"/>
  <MirrorUsagePointListLink href="/mup"/>
  <SelfDeviceLink href="/sdev"/>
</DeviceCapability>""".encode()

VALID_END_DEVICE_LIST = f"""\
<?xml version="1.0" encoding="utf-8"?>
<EndDeviceList {NS} href="/edev" all="1" results="1" pollRate="300">
  <EndDevice href="/edev/1" subscribable="0">
    <ConfigurationLink href="/cfg"/>
    <DERListLink href="/der" all="1"/>
    <lFDI>8A23EAFC6235ABD1BB7DD21FDDF3EF15B4B01179</lFDI>
    <sFDI>683475070343</sFDI>
    <changedTime>1773764585</changedTime>
  </EndDevice>
</EndDeviceList>""".encode()

VALID_DER_CONTROL_LIST = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERControlList {NS} href="/edev/1/fsa/1/derp/1/derc" subscribable="1" all="1" results="1">
  <DERControl href="/edev/1/fsa/1/derp/1/derc/1" subscribable="0"
    replyTo="/rsps" responseRequired="07">
    <mRID>0F5CFC7812035770</mRID>
    <version>3</version>
    <creationTime>1773764565</creationTime>
    <EventStatus>
      <currentStatus>0</currentStatus>
      <dateTime>1773764565</dateTime>
      <potentiallySuperseded>false</potentiallySuperseded>
    </EventStatus>
    <interval>
      <duration>3600</duration>
      <start>1773764585</start>
    </interval>
    <DERControlBase>
      <opModFixedPFInjectW>
        <displacement>950</displacement>
        <excitation>false</excitation>
        <multiplier>-3</multiplier>
      </opModFixedPFInjectW>
    </DERControlBase>
  </DERControl>
</DERControlList>""".encode()

VALID_DER_STATUS = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERStatus {NS} href="/der/1/ders">
  <genConnectStatus>
    <dateTime>1773764585</dateTime>
    <value>01</value>
  </genConnectStatus>
  <inverterStatus>
    <dateTime>1773764585</dateTime>
    <value>02</value>
  </inverterStatus>
  <readingTime>1773764585</readingTime>
</DERStatus>""".encode()

VALID_DER_CAPABILITY = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERCapability {NS} href="/der/1/dercap">
  <modesSupported>00000001</modesSupported>
  <rtgMaxW>
    <multiplier>0</multiplier>
    <value>10000</value>
  </rtgMaxW>
  <type>83</type>
</DERCapability>""".encode()

VALID_DER_SETTINGS = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERSettings {NS} href="/der/1/derg">
  <modesEnabled>00000001</modesEnabled>
  <setGradW>0</setGradW>
  <setMaxW>
    <multiplier>0</multiplier>
    <value>10000</value>
  </setMaxW>
  <updatedTime>1773764585</updatedTime>
</DERSettings>""".encode()

VALID_DER_AVAILABILITY = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERAvailability {NS} href="/der/1/dera">
  <availabilityDuration>0</availabilityDuration>
  <maxChargeDuration>0</maxChargeDuration>
  <readingTime>1773764585</readingTime>
</DERAvailability>""".encode()

VALID_DER_PROGRAM_LIST = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERProgramList {NS} href="/edev/1/fsa/1/derp" all="1" results="1" pollRate="10">
  <DERProgram href="/edev/1/fsa/1/derp/1" subscribable="0">
    <mRID>A1B2C3D4E5F60000</mRID>
    <version>1</version>
    <DefaultDERControlLink href="/edev/1/fsa/1/derp/1/dderc"/>
    <DERControlListLink href="/edev/1/fsa/1/derp/1/derc" all="1"/>
    <primacy>25</primacy>
  </DERProgram>
</DERProgramList>""".encode()

VALID_TIME = f"""\
<?xml version="1.0" encoding="utf-8"?>
<Time {NS} href="/tm" pollRate="900">
  <currentTime>1773764585</currentTime>
  <dstEndTime>1773764585</dstEndTime>
  <dstOffset>0</dstOffset>
  <dstStartTime>1773764585</dstStartTime>
  <quality>5</quality>
  <tzOffset>0</tzOffset>
</Time>""".encode()

VALID_MIRROR_USAGE_POINT = f"""\
<?xml version="1.0" encoding="utf-8"?>
<MirrorUsagePoint {NS}>
  <mRID>683475070343119ABD1F1B150000D17E</mRID>
  <roleFlags>01</roleFlags>
  <serviceCategoryKind>0</serviceCategoryKind>
  <status>0</status>
  <deviceLFDI>8A23EAFC6235ABD1BB7DD21FDDF3EF15B4B01179</deviceLFDI>
  <MirrorMeterReading>
    <mRID>683475070343119ABD1F1B1500000001</mRID>
    <Reading>
      <value>5000</value>
    </Reading>
    <ReadingType>
      <commodity>1</commodity>
      <flowDirection>1</flowDirection>
      <kind>37</kind>
      <powerOfTenMultiplier>0</powerOfTenMultiplier>
      <uom>38</uom>
    </ReadingType>
  </MirrorMeterReading>
</MirrorUsagePoint>""".encode()

VALID_MIRROR_METER_READING_LIST = f"""\
<?xml version="1.0" encoding="utf-8"?>
<MirrorMeterReadingList {NS} all="1" results="1">
  <MirrorMeterReading>
    <mRID>683475070343119ABD1F1B1500000001</mRID>
    <Reading>
      <timePeriod>
        <duration>0</duration>
        <start>1773764585</start>
      </timePeriod>
      <value>5000</value>
    </Reading>
    <ReadingType>
      <commodity>1</commodity>
      <flowDirection>1</flowDirection>
      <kind>37</kind>
      <powerOfTenMultiplier>0</powerOfTenMultiplier>
      <uom>38</uom>
    </ReadingType>
  </MirrorMeterReading>
</MirrorMeterReadingList>""".encode()

VALID_FSA_LIST = f"""\
<?xml version="1.0" encoding="utf-8"?>
<FunctionSetAssignmentsList {NS} href="/edev/1/fsa" all="1" results="1">
  <FunctionSetAssignments href="/fsa/1" subscribable="0">
    <DERProgramListLink href="/edev/1/fsa/1/derp" all="1"/>
    <mRID>FFFFFFFFFFFFFF01</mRID>
  </FunctionSetAssignments>
</FunctionSetAssignmentsList>""".encode()

VALID_RESPONSE = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERControlResponse {NS}>
  <createdDateTime>1773764585</createdDateTime>
  <endDeviceLFDI>8A23EAFC6235ABD1BB7DD21FDDF3EF15B4B01179</endDeviceLFDI>
  <status>0</status>
  <subject>0F5CFC7812035770</subject>
</DERControlResponse>""".encode()

# All valid XML samples — every resource type is tested
VALID_SAMPLES: dict[str, bytes] = {
    "DeviceCapability": VALID_DEVICE_CAPABILITY,
    "EndDeviceList": VALID_END_DEVICE_LIST,
    "DERControlList": VALID_DER_CONTROL_LIST,
    "DERStatus": VALID_DER_STATUS,
    "DERCapability": VALID_DER_CAPABILITY,
    "DERSettings": VALID_DER_SETTINGS,
    "DERAvailability": VALID_DER_AVAILABILITY,
    "DERProgramList": VALID_DER_PROGRAM_LIST,
    "Time": VALID_TIME,
    "MirrorUsagePoint": VALID_MIRROR_USAGE_POINT,
    "MirrorMeterReadingList": VALID_MIRROR_METER_READING_LIST,
    "FunctionSetAssignmentsList": VALID_FSA_LIST,
    "DERControlResponse": VALID_RESPONSE,
}

# ---------------------------------------------------------------------------
# Invalid XML samples (should FAIL XSD validation)
# ---------------------------------------------------------------------------

INVALID_UNKNOWN_ELEMENT = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERControlList {NS} all="1" results="1">
  <BOGUS>unexpected element</BOGUS>
</DERControlList>""".encode()

INVALID_MISSING_REQUIRED = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERControl {NS} href="/test" replyTo="/rsps" responseRequired="07">
  <mRID>0F5CFC7812035770</mRID>
  <creationTime>1773764565</creationTime>
  <EventStatus>
    <currentStatus>0</currentStatus>
    <dateTime>1773764565</dateTime>
    <potentiallySuperseded>false</potentiallySuperseded>
  </EventStatus>
</DERControl>""".encode()

INVALID_BAD_NAMESPACE = b"""\
<?xml version="1.0" encoding="utf-8"?>
<DERControlList xmlns="urn:wrong:namespace" all="1" results="1">
</DERControlList>"""

INVALID_MALFORMED_XML = b"""\
<?xml version="1.0" encoding="utf-8"?>
<DERControlList xmlns="urn:ieee:std:2030.5:ns" all="1" results="1">
  <unclosed_tag>
</DERControlList>"""

INVALID_WRONG_ROOT = f"""\
<?xml version="1.0" encoding="utf-8"?>
<NotAReal2030Point5Element {NS}>
  <stuff>123</stuff>
</NotAReal2030Point5Element>""".encode()

INVALID_BAD_ATTRIBUTE_TYPE = f"""\
<?xml version="1.0" encoding="utf-8"?>
<DERControlList {NS} all="not_a_number" results="1">
</DERControlList>""".encode()

INVALID_SAMPLES: dict[str, bytes] = {
    "unknown_element": INVALID_UNKNOWN_ELEMENT,
    "missing_required": INVALID_MISSING_REQUIRED,
    "bad_namespace": INVALID_BAD_NAMESPACE,
    "malformed_xml": INVALID_MALFORMED_XML,
    "wrong_root": INVALID_WRONG_ROOT,
    "bad_attribute_type": INVALID_BAD_ATTRIBUTE_TYPE,
}

# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=list(VALID_SAMPLES.keys()), ids=list(VALID_SAMPLES.keys()))  # type: ignore[misc]
def valid_xml_sample(request: pytest.FixtureRequest) -> tuple[str, bytes]:
    """Parametrized fixture yielding (resource_type, xml_bytes) for each valid sample."""
    return request.param, VALID_SAMPLES[request.param]


@pytest.fixture(params=list(INVALID_SAMPLES.keys()), ids=list(INVALID_SAMPLES.keys()))  # type: ignore[misc]
def invalid_xml_sample(request: pytest.FixtureRequest) -> tuple[str, bytes]:
    """Parametrized fixture yielding (label, xml_bytes) for each invalid sample."""
    return request.param, INVALID_SAMPLES[request.param]


@pytest.fixture  # type: ignore[misc]
def captured_xml_dir() -> Path | None:
    """Return path to captured XML directory if it exists (from capture_messages.py)."""
    path = Path(__file__).parent / "xml"
    if path.exists() and any(path.glob("*.xml")):
        return path
    return None


@pytest.fixture  # type: ignore[misc]
def captured_xml_files(captured_xml_dir: Path | None) -> list[Path]:
    """Return list of captured XML files (empty list if none exist).

    Consuming tests should assert ``len(captured_xml_files) > 0`` if they
    require captured data, rather than having this fixture silently skip.
    """
    if captured_xml_dir is None:
        return []
    return sorted(captured_xml_dir.glob("*.xml"))
