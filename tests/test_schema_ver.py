"""Every payload sent to a 2030.5-2023 server carries schemaVer="2.2"; a 2018 server gets none."""

from __future__ import annotations

from collections.abc import Callable
from xml.etree.ElementTree import fromstring

import pytest

from py20305.connectors.reading import Reading
from py20305.telemetry.der_availability import build_der_availability
from py20305.telemetry.der_capability import build_der_capability
from py20305.telemetry.der_settings import build_der_settings
from py20305.telemetry.der_status import build_der_status
from py20305.telemetry.log_events import create_log_event_xml
from py20305.telemetry.mup import create_meter_reading_list, create_mup
from py20305.xml.serialization import to_xml

LFDI = "1234567890abcdef1234567890abcdef12345678"
_READINGS = [Reading(value=1500.0, uom=38)]

_PAYLOADS: dict[str, Callable[[bool], bytes]] = {
    "DERStatus": lambda c: to_xml(build_der_status({"readingTime": 1}), server_2018_compat=c),
    "DERCapability": lambda c: to_xml(
        build_der_capability({"rtgMaxW": 5000}), server_2018_compat=c
    ),
    "DERSettings": lambda c: to_xml(build_der_settings({"setMaxW": 5000}), server_2018_compat=c),
    "DERAvailability": lambda c: to_xml(
        build_der_availability({"statWAvail": {"value": 1000}}, default_time=1),
        server_2018_compat=c,
    ),
    "MirrorUsagePoint": lambda c: to_xml(create_mup(LFDI, _READINGS, 300), server_2018_compat=c),
    "MirrorMeterReadingList": lambda c: to_xml(
        create_meter_reading_list(LFDI, _READINGS, 1000), server_2018_compat=c
    ),
    "LogEvent": lambda c: create_log_event_xml(1, server_2018_compat=c, created_time=1),
}


@pytest.mark.parametrize("name", sorted(_PAYLOADS))
def test_a_2023_payload_sets_schema_ver_2_2(name: str) -> None:
    assert fromstring(_PAYLOADS[name](False)).get("schemaVer") == "2.2"


@pytest.mark.parametrize("name", sorted(_PAYLOADS))
def test_a_2018_payload_has_no_schema_ver(name: str) -> None:
    assert fromstring(_PAYLOADS[name](True)).get("schemaVer") is None
