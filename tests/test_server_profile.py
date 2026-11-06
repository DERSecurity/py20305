"""Tests for the server schema profile: what is sent to a 2018, 2023 or blended server."""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from py20305.client.http import Sep2Client
from py20305.config import ServerConfig
from py20305.connectors.base import BaseConnector
from py20305.connectors.modes import modes_supported2
from py20305.connectors.reading import Reading
from py20305.events.response import ResponseCode, post_der_response, translate_code_for_revision
from py20305.models.sep import (
    DercontrolBase,
    DercontrolResponse,
    DercontrolType,
    DercontrolType2,
    MRidtype,
    Response,
    Subscription,
    TimeType,
)
from py20305.models.sep.sep import Dercontrol1, DeviceCapability
from py20305.models.sep.sep import DercontrolBase as ControlBase
from py20305.schema_editions import SCHEMA_DIR, borrowed_items, new_item, root_attributes
from py20305.server_profile import (
    EXTENSIONS,
    FEATURES,
    Edition,
    ServerProfile,
    base_from_schema_ver,
    declared_schema_ver,
)
from py20305.telemetry.der_availability import build_der_availability
from py20305.telemetry.der_capability import build_der_capability
from py20305.telemetry.der_settings import build_der_settings
from py20305.telemetry.der_status import build_der_status
from py20305.telemetry.log_events import create_log_event_xml
from py20305.telemetry.mup import create_meter_reading_list, create_mup
from py20305.xml.serialization import from_xml, to_xml, validate_xml

LFDI = "1234567890abcdef1234567890abcdef12345678"
NS = 'xmlns="urn:ieee:std:2030.5:ns"'
LIMITS = frozenset({"import_export_limits"})
INJECT_ABSORB = (1 << 7) | (1 << 8)

P2018 = ServerProfile(base=Edition.E2018)
P2018_LIMITS = ServerProfile(base=Edition.E2018, features=LIMITS)
P2018_AUS12 = ServerProfile(base=Edition.E2018, extensions=frozenset({"csip-aus-1.2"}))
P2018_AUS13 = ServerProfile(base=Edition.E2018, extensions=frozenset({"csip-aus-1.3"}))
P2023 = ServerProfile(base=Edition.E2023)
P2023_AUS = ServerProfile(base=Edition.E2023, extensions=frozenset({"csip-aus-1.3"}))

PROFILES = {
    "2018": P2018,
    "2018+limits": P2018_LIMITS,
    "2018+csip-aus-1.2": P2018_AUS12,
    "2018+csip-aus-1.3": P2018_AUS13,
    "2023": P2023,
    "2023+csip-aus": P2023_AUS,
}


def _aus_namespace(profile: ServerProfile) -> str | None:
    for name in sorted(profile.extensions or ()):
        return EXTENSIONS[name]
    return None


def _payloads(profile: ServerProfile) -> dict[str, bytes]:
    """Every payload the client sends, built with every optional field present."""
    aus = _aus_namespace(profile)
    send: dict[str, Any] = {
        "profile": profile,
        "include_csipaus": aus is not None,
        "csipaus_namespace": aus or "https://csipaus.org/ns",
    }
    readings = [Reading(value=1500.0, uom=38)]
    capability = build_der_capability(
        {"rtgMaxW": 5000},
        csip_aus_mode=aus is not None,
        modes_supported2=INJECT_ABSORB,
        profile=profile,
    )
    settings = build_der_settings(
        {"setMaxW": 5000, "modesEnabled2": INJECT_ABSORB},
        csip_aus_mode=aus is not None,
        modes_supported2=INJECT_ABSORB,
        profile=profile,
    )
    status = build_der_status(
        {
            "readingTime": 1,
            "connectStatus": {"dateTime": 1, "value": 3},
            "genConnectStatus": {"dateTime": 1, "value": 5},
            "storConnectStatus": {"dateTime": 1, "value": 5},
        },
        profile=profile,
    )
    availability = build_der_availability({"statWAvail": {"value": 100}}, default_time=1)
    control_response = DercontrolResponse(
        end_device_lfdi=bytes.fromhex(LFDI),
        status=1,
        subject=MRidtype(value=bytes(16)),
        created_date_time=TimeType(value=1),
        modes_responded=DercontrolType(value=b"\x00\x00\x00\x80"),
        modes_responded2=DercontrolType2(value=INJECT_ABSORB.to_bytes(4, "big")),
    )
    response = Response(
        end_device_lfdi=bytes.fromhex(LFDI),
        status=1,
        subject=MRidtype(value=bytes(16)),
        created_date_time=TimeType(value=1),
    )
    subscription = Subscription(
        subscribed_resource="/edev/1/der/1/dera",
        encoding=0,
        level="+S1",
        limit=1,
        notification_uri="https://agg:10443/notify",
    )
    return {
        "DERCapability": to_xml(capability, **send),
        "DERSettings": to_xml(settings, **send),
        "DERStatus": to_xml(status, **send),
        "DERAvailability": to_xml(availability, **send),
        "MirrorUsagePoint": to_xml(create_mup(LFDI, readings, 300), **send),
        "MirrorMeterReadingList": to_xml(create_meter_reading_list(LFDI, readings, 1), **send),
        "DERControlResponse": to_xml(control_response, **send),
        "Response": to_xml(response, **send),
        "Subscription": to_xml(subscription, **send),
        "LogEvent": create_log_event_xml(1, profile=profile, created_time=1),
    }


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_outbound_payload_is_valid_and_inside_the_profile(name: str) -> None:
    profile = PROFILES[name]

    for resource, xml in _payloads(profile).items():
        assert validate_xml(xml) == [], f"{resource} under {name}"
        assert profile.audit(xml) == [], f"{resource} under {name}"


def test_a_2018_server_gets_payloads_the_2018_schema_accepts() -> None:
    """With every feature off, the real 2018 schema accepts every payload."""
    schema = SCHEMA_DIR / "sep2_schema_2018.xsd"

    for resource, xml in _payloads(P2018).items():
        assert validate_xml(xml, schema) == [], resource


def test_a_2018_server_gets_none_of_the_2023_elements() -> None:
    payloads = _payloads(P2018)

    assert b"connectStatus" not in payloads["DERStatus"].replace(b"genConnectStatus", b"")
    assert b"modesSupported2" not in payloads["DERCapability"]
    assert b"modesEnabled2" not in payloads["DERSettings"]
    assert b"modesResponded" not in payloads["DERControlResponse"]
    for xml in payloads.values():
        assert b"schemaVer" not in xml
    # subscribable is old on most resources and new on MirrorUsagePoint.
    assert b"subscribable" not in payloads["MirrorUsagePoint"]


def test_a_2018_server_with_import_export_limits_gets_the_additional_modes() -> None:
    payloads = _payloads(P2018_LIMITS)

    assert b"<modesSupported2>00000180</modesSupported2>" in payloads["DERCapability"]
    assert b"<modesEnabled2>00000180</modesEnabled2>" in payloads["DERSettings"]
    assert b"<modesResponded2>00000180</modesResponded2>" in payloads["DERControlResponse"]
    assert b"<modesResponded>" not in payloads["DERControlResponse"]
    assert b"genConnectStatus" in payloads["DERStatus"]
    assert b"<connectStatus>" not in payloads["DERStatus"]


def test_a_2023_server_gets_the_2023_elements_and_schema_ver() -> None:
    payloads = _payloads(P2023)

    assert b"<connectStatus>" in payloads["DERStatus"]
    assert b"ConnectStatus" not in payloads["DERStatus"].replace(b"connectStatus", b"")
    assert b"<modesSupported2>" in payloads["DERCapability"]
    assert b"<modesResponded>" in payloads["DERControlResponse"]
    assert b'schemaVer="2.2"' in payloads["MirrorUsagePoint"]


@pytest.mark.parametrize("resource", ["DERCapability", "DERSettings", "DERStatus", "LogEvent"])
def test_a_2023_base_with_a_borrowed_feature_changes_nothing(resource: str) -> None:
    plain = _payloads(P2023)[resource]
    borrowing = _payloads(ServerProfile(base=Edition.E2023, features=LIMITS))[resource]

    assert plain == borrowing


class TestPerParentCheck:
    """An element is 2023-only inside one type and old inside another."""

    def test_usage_point_link_is_new_inside_a_mirror_usage_point(self) -> None:
        xml = (
            f"<MirrorUsagePoint {NS}><mRID>00</mRID><deviceLFDI>{LFDI}</deviceLFDI>"
            '<UsagePointLink href="/upt/1"/></MirrorUsagePoint>'
        ).encode()

        assert P2018.audit(xml) == [new_item("MirrorUsagePoint", "UsagePointLink")]
        assert P2023.audit(xml) == []

    def test_usage_point_link_is_not_new_inside_a_usage_point_list_entry(self) -> None:
        assert new_item("UsagePoint", "UsagePointLink") is None

    def test_updated_time_is_new_inside_a_default_der_control(self) -> None:
        xml = (
            f"<DefaultDERControl {NS}><mRID>00</mRID><updatedTime>1</updatedTime>"
            "</DefaultDERControl>"
        ).encode()

        assert P2018.audit(xml) == [new_item("DefaultDERControl", "updatedTime")]

    def test_updated_time_is_not_new_inside_der_settings(self) -> None:
        assert new_item("DERSettings", "updatedTime") is None

    def test_the_disabled_attribute_is_caught(self) -> None:
        xml = (
            f"<DefaultDERControl {NS}><mRID>00</mRID><DERControlBase>"
            '<opModMaxLimW disabled="true">100</opModMaxLimW></DERControlBase></DefaultDERControl>'
        ).encode()

        items = P2018.audit(xml)

        assert any(item.name == "@disabled" for item in items), items

    def test_schema_ver_and_subscribable_are_caught_on_the_root(self) -> None:
        xml = (
            f'<MirrorUsagePoint {NS} subscribable="0" schemaVer="2.2">'
            "<mRID>00</mRID></MirrorUsagePoint>"
        )

        items = P2018.audit(xml.encode())

        assert new_item("MirrorUsagePoint", "@subscribable") in items
        assert any(item.name == "@schemaVer" for item in items)

    def test_a_borrowed_feature_permits_only_its_own_elements(self) -> None:
        xml = (
            f"<DERCapability {NS}><modesSupported>00000000</modesSupported>"
            "<modesSupported2>00000180</modesSupported2><type>83</type></DERCapability>"
        ).encode()

        assert P2018.audit(xml) == [new_item("DERCapability", "modesSupported2")]
        assert P2018_LIMITS.audit(xml) == []

    def test_the_tables_come_from_the_schemas(self) -> None:
        """An item is new exactly when the 2023 schema has it and the 2018 schema lacks it."""
        assert new_item("DERStatus", "connectStatus") is not None
        assert new_item("DERStatus", "inverterStatus") is None
        assert new_item("DERStatus", "noSuchElement") is None


class TestInboundIsLiberal:
    """A 2018-base server may send a 2023 element: it is read and logged, not rejected."""

    BODY = (
        f"<DERCapability {NS}><modesSupported>00000000</modesSupported>"
        "<modesSupported2>00000180</modesSupported2><rtgMaxW><multiplier>0</multiplier>"
        "<value>5000</value></rtgMaxW><type>83</type></DERCapability>"
    ).encode()

    def test_the_element_is_read(self) -> None:
        from py20305.models.sep import Dercapability

        parsed = from_xml(self.BODY, Dercapability)

        assert parsed.modes_supported2 is not None

    def test_an_element_no_feature_covers_is_logged_once(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Sep2Client("https://server.example", profile=P2018)
        client.observe_device_capability(b"<DeviceCapability " + NS.encode() + b"/>")

        with caplog.at_level(logging.INFO, logger="py20305.client.http"):
            client._note_inbound(self.BODY)
            client._note_inbound(self.BODY)

        lines = [r.getMessage() for r in caplog.records if "modesSupported2" in r.getMessage()]
        assert len(lines) == 1
        assert "not enabled" in lines[0]

    def test_an_enabled_feature_the_server_uses_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Sep2Client("https://server.example", profile=P2018_LIMITS)

        with caplog.at_level(logging.INFO, logger="py20305.client.http"):
            client.observe_device_capability(b"<DeviceCapability " + NS.encode() + b"/>")
            client._note_inbound(self.BODY)

        assert any("feature import_export_limits" in r.getMessage() for r in caplog.records)

    def test_a_2023_base_logs_nothing(self, caplog: pytest.LogCaptureFixture) -> None:
        client = Sep2Client("https://server.example", profile=P2023)

        with caplog.at_level(logging.INFO, logger="py20305.client.http"):
            client._note_inbound(self.BODY)

        assert not [r for r in caplog.records if "modesSupported2" in r.getMessage()]


class TestDetection:
    @pytest.mark.parametrize(
        ("schema_ver", "expected"),
        [("2.2", Edition.E2023), ("2.3", Edition.E2023), ("3.0", Edition.E2023)]
        + [
            ("2.1", Edition.E2018),
            ("2.0", Edition.E2018),
            (None, Edition.E2018),
            ("x", Edition.E2018),
        ],
    )
    def test_the_base_follows_the_schema_version(
        self, schema_ver: str | None, expected: Edition
    ) -> None:
        assert base_from_schema_ver(schema_ver) is expected

    def test_the_schema_ver_is_read_from_the_root_element(self) -> None:
        assert declared_schema_ver(f'<DeviceCapability {NS} schemaVer="2.2"/>'.encode()) == "2.2"
        assert declared_schema_ver(f"<DeviceCapability {NS}/>".encode()) is None
        assert declared_schema_ver(b"not xml") is None

    def test_auto_resolves_from_the_device_capability_and_logs_the_evidence(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = Sep2Client("https://server.example")

        with caplog.at_level(logging.INFO, logger="py20305.client.http"):
            client.observe_device_capability(f'<DeviceCapability {NS} schemaVer="2.2"/>'.encode())

        assert client.profile.edition is Edition.E2023
        assert any('schemaVer="2.2"' in r.getMessage() for r in caplog.records)

    def test_a_missing_schema_ver_resolves_to_2018(self) -> None:
        client = Sep2Client("https://server.example")

        client.observe_device_capability(f"<DeviceCapability {NS}/>".encode())

        assert client.profile.is_2018
        assert client.profile.evidence == "DeviceCapability has no schemaVer"

    def test_a_configured_base_is_never_overridden(self) -> None:
        client = Sep2Client("https://server.example", profile=P2018)

        client.observe_device_capability(f'<DeviceCapability {NS} schemaVer="2.2"/>'.encode())

        assert client.profile.is_2018

    def test_borrowed_features_are_never_detected(self) -> None:
        client = Sep2Client("https://server.example")

        client.observe_device_capability(f'<DeviceCapability {NS} schemaVer="2.2"/>'.encode())
        client.observe_device_capability(f"<DeviceCapability {NS}/>".encode())

        assert client.profile.features == frozenset()


class TestConfig:
    def test_the_defaults_detect_the_base_and_borrow_nothing(self) -> None:
        profile = ServerConfig(url="https://server.example").profile()

        assert profile.base is None
        assert profile.features == frozenset()
        assert profile.extensions is None
        assert profile.missing_location_fallback is False

    def test_a_schema_block_is_read(self) -> None:
        config = ServerConfig.model_validate(
            {
                "url": "https://server.example",
                "schema": {
                    "base": "2018",
                    "features": ["import_export_limits"],
                    "extensions": ["csip-aus-1.3"],
                },
            }
        )

        profile = config.profile()

        assert profile.base is Edition.E2018
        assert profile.features == LIMITS
        assert profile.extensions == frozenset({"csip-aus-1.3"})

    def test_an_unknown_feature_is_refused_with_the_known_ones(self) -> None:
        with pytest.raises(ValidationError, match="import_export_limits"):
            ServerConfig.model_validate(
                {"url": "https://server.example", "schema": {"features": ["nope"]}}
            )

    def test_an_unknown_extension_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="csip-aus-1.3"):
            ServerConfig.model_validate(
                {"url": "https://server.example", "schema": {"extensions": ["nope"]}}
            )

    def test_the_deprecated_flag_true_is_base_2018_with_the_location_fallback(self) -> None:
        with pytest.warns(DeprecationWarning, match="server.schema.base: 2018"):
            config = ServerConfig(url="https://server.example", server_2018_compat=True)

        profile = config.profile()

        assert profile.base is Edition.E2018
        assert profile.missing_location_fallback is True
        assert profile.features == frozenset()

    def test_the_deprecated_flag_false_is_base_2023_without_the_fallback(self) -> None:
        with pytest.warns(DeprecationWarning):
            config = ServerConfig(url="https://server.example", server_2018_compat=False)

        profile = config.profile()

        assert profile.base is Edition.E2023
        assert profile.missing_location_fallback is False

    def test_an_explicit_base_wins_over_the_deprecated_flag(self) -> None:
        with pytest.warns(DeprecationWarning):
            config = ServerConfig.model_validate(
                {
                    "url": "https://server.example",
                    "server_2018_compat": True,
                    "schema": {"base": "2023"},
                }
            )

        profile = config.profile()

        assert profile.base is Edition.E2023
        assert profile.missing_location_fallback is True

    def test_the_location_fallback_is_a_separate_option(self) -> None:
        config = ServerConfig(url="https://server.example", missing_location_fallback=True)

        assert config.profile().missing_location_fallback is True
        assert config.profile().base is None

    def test_the_flag_is_not_warned_about_when_unset(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            ServerConfig(url="https://server.example")


class TestSupportedModes:
    """Features permit bits; the connector decides which are set."""

    class _Inject(BaseConnector):
        async def update_op_mod_max_lim_w_inject(self, params: dict[str, Any]) -> None:
            return None

    class _Both(BaseConnector):
        async def update_op_mod_max_lim_w_absorb(self, params: dict[str, Any]) -> None:
            return None

        async def update_op_mod_max_lim_w_inject(self, params: dict[str, Any]) -> None:
            return None

    class _BothButCannotHonor(_Both):
        def supported_modes2(self) -> int | None:
            return 0

    def test_a_connector_without_the_methods_supports_no_additional_mode(self) -> None:
        assert modes_supported2(BaseConnector()) == 0

    def test_the_bits_follow_the_methods_the_connector_implements(self) -> None:
        assert modes_supported2(self._Inject()) == 1 << 8
        assert modes_supported2(self._Both()) == INJECT_ABSORB

    def test_a_connector_can_state_that_it_cannot_honor_a_method_it_implements(self) -> None:
        assert modes_supported2(self._BothButCannotHonor()) == 0

    def test_a_connector_without_inject_or_absorb_never_advertises_bits_7_and_8(self) -> None:
        bits = modes_supported2(BaseConnector())

        capability = build_der_capability(
            {"rtgMaxW": 5000}, modes_supported2=bits, profile=P2018_LIMITS
        )
        enabled = build_der_settings(
            {"setMaxW": 5000, "modesEnabled2": INJECT_ABSORB},
            modes_supported2=bits,
            profile=P2018_LIMITS,
        )

        assert capability.modes_supported2 is None
        assert enabled.modes_enabled2 is None

    def test_the_profile_masks_what_the_connector_supports(self) -> None:
        both = modes_supported2(self._Both())
        pct = both | (1 << 4)

        capability = build_der_capability(
            {"rtgMaxW": 5000}, modes_supported2=pct, profile=P2018_LIMITS
        )

        assert capability.modes_supported2.value == INJECT_ABSORB.to_bytes(4, "big")

    def test_a_2018_base_without_the_feature_sends_no_additional_modes(self) -> None:
        capability = build_der_capability(
            {"rtgMaxW": 5000}, modes_supported2=INJECT_ABSORB, profile=P2018
        )

        assert capability.modes_supported2 is None

    def test_modes_enabled2_is_limited_to_what_the_device_supports(self) -> None:
        settings = build_der_settings(
            {"setMaxW": 5000, "modesEnabled2": INJECT_ABSORB},
            modes_supported2=1 << 8,
            profile=P2018_LIMITS,
        )

        assert settings.modes_enabled2.value == (1 << 8).to_bytes(4, "big")

    def test_modes_enabled2_is_not_invented(self) -> None:
        settings = build_der_settings(
            {"setMaxW": 5000}, modes_supported2=INJECT_ABSORB, profile=P2018_LIMITS
        )

        assert settings.modes_enabled2 is None


class _Http:
    def __init__(self, profile: ServerProfile) -> None:
        self.profile = profile
        self.posted: list[Any] = []

    async def post(self, path: str, resource: Any) -> str | None:
        self.posted.append(resource)
        return None


class _Tracker:
    async def reserve(self, *args: Any) -> bool:
        return True

    def mark_sent(self, *args: Any) -> None:
        return None

    def notify(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def release(self, *args: Any, **kwargs: Any) -> None:
        return None


def _derc() -> Dercontrol1:
    base = ControlBase.model_construct(
        op_mod_max_lim_wabsorb=object(),
        op_mod_max_lim_winject=object(),
    )
    return Dercontrol1.model_construct(
        m_rid=MRidtype(value=bytes(16)),
        reply_to="/rsp",
        response_required=b"\x03",
        dercontrol_base=base,
    )


class TestResponses:
    async def _post(self, profile: ServerProfile, supported: int | None) -> DercontrolResponse:
        http = _Http(profile)
        tracker = _Tracker()
        await post_der_response(
            http,  # type: ignore[arg-type]
            _derc(),
            ResponseCode.ACKNOWLEDGED,
            bytes.fromhex(LFDI),
            tracker,  # type: ignore[arg-type]
            now_ts=1,
            supported_modes2=supported,
        )
        assert http.posted, "no response was posted"
        return http.posted[0]

    async def test_a_2023_server_gets_both_mode_fields(self) -> None:
        response = await self._post(P2023, INJECT_ABSORB)

        assert response.modes_responded is not None
        assert response.modes_responded2.value == INJECT_ABSORB.to_bytes(4, "big")

    async def test_a_2018_server_gets_neither_without_the_feature(self) -> None:
        response = await self._post(P2018, INJECT_ABSORB)

        assert response.modes_responded is None
        assert response.modes_responded2 is None

    async def test_a_2018_server_with_the_feature_gets_only_the_additional_modes(self) -> None:
        response = await self._post(P2018_LIMITS, INJECT_ABSORB)

        assert response.modes_responded is None
        assert response.modes_responded2.value == INJECT_ABSORB.to_bytes(4, "big")

    async def test_only_the_modes_the_device_supports_are_reported(self) -> None:
        response = await self._post(P2018_LIMITS, 1 << 8)

        assert response.modes_responded2.value == (1 << 8).to_bytes(4, "big")

    async def test_nothing_is_reported_for_a_device_whose_support_is_unknown(self) -> None:
        response = await self._post(P2018_LIMITS, None)

        assert response.modes_responded2 is None


class TestResponseCodes:
    def test_2018_table_27_maps_not_supported_to_252(self) -> None:
        assert translate_code_for_revision(ResponseCode.NOT_SUPPORTED, profile=P2018) == (
            ResponseCode.NOT_APPLICABLE
        )

    def test_2023_table_31_keeps_251(self) -> None:
        assert translate_code_for_revision(ResponseCode.NOT_SUPPORTED, profile=P2023) == (
            ResponseCode.NOT_SUPPORTED
        )

    def test_a_feature_does_not_change_the_table(self) -> None:
        assert P2018_LIMITS.wire_response_code(251) == 252


class TestProfileQuestions:
    def test_connect_status_fields_follow_the_edition(self) -> None:
        assert P2018.connect_status_fields == ("genConnectStatus", "storConnectStatus")
        assert P2023.connect_status_fields == ("connectStatus",)

    def test_multi_der_and_schema_ver_follow_the_edition(self) -> None:
        assert P2018.allows_multi_der and not P2023.allows_multi_der
        assert P2018.schema_ver is None and P2023.schema_ver == "2.2"

    def test_an_unresolved_profile_acts_as_2018(self) -> None:
        assert ServerProfile().edition is Edition.E2018
        assert ServerProfile().schema_ver is None
        assert not ServerProfile().resolved

    def test_an_unresolved_profile_sends_the_payloads_auto_sends_without_evidence(
        self,
    ) -> None:
        no_evidence = ServerProfile().resolve_from_device_capability(
            f"<DeviceCapability {NS}/>".encode()
        )

        assert no_evidence.base is Edition.E2018
        assert _payloads(ServerProfile()) == _payloads(no_evidence)

    def test_the_feature_registry_names_what_each_feature_borrows(self) -> None:
        feature = FEATURES["import_export_limits"]

        assert feature.schema is Edition.E2023
        assert feature.mode_bits2 == {7, 8}
        for item in feature.elements:
            assert new_item(item.parent, item.name) == item

    def test_borrowed_items_of_a_clean_2018_payload_are_empty(self) -> None:
        assert borrowed_items(_payloads(P2018)["DERStatus"]) == []


def test_the_schema_files_are_the_bundled_ones() -> None:
    assert Path(SCHEMA_DIR / "sep2_schema_2018.xsd").is_file()
    assert Path(SCHEMA_DIR / "sep2_schema_2023.xsd").is_file()


def test_a_dercontrol_base_still_parses_the_borrowed_limits() -> None:
    xml = (
        f"<DERControlBase {NS}><opModMaxLimWAbsorb><multiplier>0</multiplier><value>100</value>"
        "</opModMaxLimWAbsorb></DERControlBase>"
    ).encode()

    parsed = from_xml(xml, DercontrolBase)

    assert parsed.op_mod_max_lim_wabsorb is not None


class TestRootAttributes:
    """The 2023 schema declares schemaVer on each global element, not on a type."""

    def test_the_2023_schema_declares_only_schema_ver_on_its_global_elements(self) -> None:
        assert root_attributes(2023) == {"schemaVer"}

    def test_the_2018_schema_declares_no_attributes_on_its_global_elements(self) -> None:
        assert root_attributes(2018) == frozenset()

    def test_the_type_lookup_does_not_report_schema_ver(self) -> None:
        assert new_item("DeviceCapability", "@schemaVer") is None

    def test_a_2018_base_leaves_schema_ver_off_a_device_capability(self) -> None:
        xml = to_xml(DeviceCapability(href="/dcap", schema_ver="2.2"), profile=P2018)

        assert b"schemaVer" not in xml


class TestConstructorAlias:
    """``server_2018_compat`` stays on the public constructors for one release line."""

    def test_sep2_client_true_is_base_2018_with_the_location_fallback(self) -> None:
        with pytest.warns(DeprecationWarning, match="server_2018_compat is deprecated"):
            client = Sep2Client("https://server.example", server_2018_compat=True)

        assert client.profile.base is Edition.E2018
        assert client.profile.missing_location_fallback is True
        assert client.profile.features == frozenset()

    def test_sep2_client_false_is_base_2023(self) -> None:
        with pytest.warns(DeprecationWarning):
            client = Sep2Client("https://server.example", server_2018_compat=False)

        assert client.profile.base is Edition.E2023
        assert client.profile.missing_location_fallback is False

    def test_csip_client_maps_the_same_way(self) -> None:
        from py20305.client.csip_client import CsipClient

        with pytest.warns(DeprecationWarning):
            client = CsipClient("https://server.example", server_2018_compat=True)

        assert client.http.profile.base is Edition.E2018
        assert client.http.profile.missing_location_fallback is True

    @pytest.mark.parametrize("flag", [True, False])
    def test_passing_both_is_an_error(self, flag: bool) -> None:
        from py20305.client.csip_client import CsipClient

        with pytest.raises(TypeError, match="not both"):
            Sep2Client("https://server.example", profile=P2018, server_2018_compat=flag)
        with pytest.raises(TypeError, match="not both"):
            CsipClient("https://server.example", profile=P2018, server_2018_compat=flag)

    def test_neither_warns_nor_changes_the_default(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            client = Sep2Client("https://server.example")

        assert client.profile.base is None

    def test_subscription_manager_true_enables_the_location_fallback(self) -> None:
        from unittest.mock import AsyncMock

        from py20305.subscription.manager import SubscriptionManager

        with pytest.warns(DeprecationWarning):
            manager = SubscriptionManager(
                AsyncMock(), "https://agg:10443/notify", server_2018_compat=True
            )

        assert manager._missing_location_fallback is True

    def test_subscription_manager_false_leaves_it_off(self) -> None:
        from unittest.mock import AsyncMock

        from py20305.subscription.manager import SubscriptionManager

        with pytest.warns(DeprecationWarning):
            manager = SubscriptionManager(
                AsyncMock(), "https://agg:10443/notify", server_2018_compat=False
            )

        assert manager._missing_location_fallback is False


class TestRestrictMasksTheBitmaps:
    """A prebuilt model is held to the profile's DERControlType2 bits too."""

    def test_a_capability_built_without_the_profile_loses_the_bits_it_does_not_permit(
        self,
    ) -> None:
        bits = (1 << 4) | INJECT_ABSORB
        capability = build_der_capability({"rtgMaxW": 5000}, modes_supported2=bits)
        assert capability.modes_supported2.value == bits.to_bytes(4, "big")

        restricted = P2018_LIMITS.restrict(capability)

        assert restricted.modes_supported2.value == INJECT_ABSORB.to_bytes(4, "big")

    def test_the_serialized_capability_carries_only_the_permitted_bits(self) -> None:
        bits = (1 << 4) | INJECT_ABSORB
        capability = build_der_capability({"rtgMaxW": 5000}, modes_supported2=bits)

        xml = to_xml(capability, profile=P2018_LIMITS)

        assert b"<modesSupported2>00000180</modesSupported2>" in xml

    def test_a_2023_base_keeps_every_bit(self) -> None:
        bits = (1 << 4) | INJECT_ABSORB
        capability = build_der_capability({"rtgMaxW": 5000}, modes_supported2=bits)

        assert P2023.restrict(capability).modes_supported2.value == bits.to_bytes(4, "big")


class TestModesEnabled2Zero:
    def test_a_reported_zero_is_sent_when_a_mode_is_available(self) -> None:
        settings = build_der_settings(
            {"setMaxW": 5000, "modesEnabled2": 0},
            modes_supported2=INJECT_ABSORB,
            profile=P2018_LIMITS,
        )

        assert settings.modes_enabled2.value == (0).to_bytes(4, "big")

    def test_a_reported_zero_is_left_out_when_no_mode_is_available(self) -> None:
        settings = build_der_settings(
            {"setMaxW": 5000, "modesEnabled2": 0},
            modes_supported2=0,
            profile=P2018_LIMITS,
        )

        assert settings.modes_enabled2 is None

    def test_a_reported_zero_is_left_out_when_the_profile_permits_no_mode(self) -> None:
        settings = build_der_settings(
            {"setMaxW": 5000, "modesEnabled2": 0},
            modes_supported2=INJECT_ABSORB,
            profile=P2018,
        )

        assert settings.modes_enabled2 is None


class TestSupportedModesOnTheResponsePath:
    """Reading the supported modes for a response never constructs a connector."""

    class _Registry:
        def __init__(self, proxy: Any) -> None:
            self.proxy = proxy

        def get_connector(self, lfdi: str) -> Any:
            return self.proxy

    class _Both(BaseConnector):
        async def update_op_mod_max_lim_w_absorb(self, params: dict[str, Any]) -> None:
            return None

        async def update_op_mod_max_lim_w_inject(self, params: dict[str, Any]) -> None:
            return None

    def _dispatcher(self, proxy: Any) -> Any:
        from py20305.connectors.dispatcher import ConnectorDispatcher

        return ConnectorDispatcher(
            registry=self._Registry(proxy),  # type: ignore[arg-type]
            lfdi_resolver=lambda href: None,
        )

    async def test_an_unconstructed_connector_is_not_constructed(self) -> None:
        from py20305.connectors.registry import LazyConnectorProxy

        built: list[str] = []

        def factory() -> BaseConnector:
            built.append("built")
            return self._Both()

        proxy = LazyConnectorProxy("device", factory)

        assert await self._dispatcher(proxy).supported_modes2(LFDI) is None
        assert built == []

    async def test_a_constructed_connector_reports_its_modes(self) -> None:
        from py20305.connectors.registry import LazyConnectorProxy

        proxy = LazyConnectorProxy("device", self._Both)
        proxy.resolve()

        assert await self._dispatcher(proxy).supported_modes2(LFDI) == INJECT_ABSORB

    async def test_the_lookup_is_skipped_when_the_profile_omits_modes_responded2(self) -> None:
        from py20305.events.processor import EventProcessor

        asked: list[str] = []

        class _Dispatcher:
            async def supported_modes2(self, lfdi: str) -> int:
                asked.append(lfdi)
                return INJECT_ABSORB

        processor = object.__new__(EventProcessor)
        processor._http = _Http(P2018)
        processor._dispatcher = _Dispatcher()

        assert await processor._supported_modes2(bytes.fromhex(LFDI)) is None
        assert asked == []

        processor._http = _Http(P2018_LIMITS)
        assert await processor._supported_modes2(bytes.fromhex(LFDI)) == INJECT_ABSORB
