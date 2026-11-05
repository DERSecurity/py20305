"""Tests for DERStatus Pydantic model builder."""

from __future__ import annotations

import time
from xml.etree.ElementTree import fromstring

import pytest

from py20305.models.sep.sep import (
    ConnectStatusType,
    ConnectStatusType2,
    Derstatus,
    InverterStatusType,
    ManufacturerStatusType,
    OperationalModeStatusType,
    StateOfChargeStatusType,
    StorageModeStatusType,
)
from py20305.server_profile import Edition, ServerProfile
from py20305.telemetry.der_status import build_der_status
from py20305.xml.serialization import from_xml, to_xml, validate_xml

NS = "urn:ieee:std:2030.5:ns"


class TestBuildDerStatus:
    """Tests for build_der_status model construction."""

    def test_basic_structure(self) -> None:
        model = build_der_status({"readingTime": 1000})
        assert isinstance(model, Derstatus)
        assert model.reading_time.value == 1000

    def test_reading_time_defaults_to_now(self) -> None:
        before = int(time.time())
        model = build_der_status({})
        after = int(time.time())
        assert before <= model.reading_time.value <= after

    def test_alarm_status_hex_encoding(self) -> None:
        model = build_der_status({"readingTime": 0, "alarmStatus": 255})
        assert model.alarm_status == b"\x00\x00\x00\xff"

    def test_alarm_status_zero_omitted(self) -> None:
        model = build_der_status({"readingTime": 0, "alarmStatus": 0})
        assert model.alarm_status is None

    def test_alarm_status_none_omitted(self) -> None:
        model = build_der_status({"readingTime": 0})
        assert model.alarm_status is None

    def test_alarm_status_zero_sent_when_flag_set(self) -> None:
        # always_send_alarm_status: an explicit all-zero bitmap instead of omitting.
        model = build_der_status(
            {"readingTime": 0, "alarmStatus": 0}, always_send_alarm_status=True
        )
        assert model.alarm_status == b"\x00\x00\x00\x00"

    def test_alarm_status_absent_sent_when_flag_set(self) -> None:
        model = build_der_status({"readingTime": 0}, always_send_alarm_status=True)
        assert model.alarm_status == b"\x00\x00\x00\x00"

    def test_alarm_status_nonzero_unaffected_by_flag(self) -> None:
        for flag in (False, True):
            model = build_der_status(
                {"readingTime": 0, "alarmStatus": 0x80}, always_send_alarm_status=flag
            )
            assert model.alarm_status == b"\x00\x00\x00\x80"

    def test_alarm_status_zero_in_xml_when_flag_set(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "alarmStatus": 0}, always_send_alarm_status=True
        )
        root = fromstring(to_xml(model).decode("utf-8"))
        assert root.findtext(f"{{{NS}}}alarmStatus") == "00000000"

    def test_connect_status_preferred(self) -> None:
        """connectStatus (ConnectStatusType2) is the preferred field."""
        model = build_der_status({"readingTime": 0, "connectStatus": {"dateTime": 100, "value": 3}})
        assert isinstance(model.connect_status, ConnectStatusType2)
        assert model.connect_status.date_time.value == 100
        assert model.connect_status.value == b"\x03"
        # When connectStatus is present, gen_connect_status should be None
        assert model.gen_connect_status is None

    def test_2023_server_gets_no_connect_status_when_only_a_2018_bitmap_is_supplied(self) -> None:
        model = build_der_status(
            {
                "readingTime": 0,
                "genConnectStatus": {"dateTime": 100, "value": 0b101},
                "storConnectStatus": {"dateTime": 100, "value": 0b101},
            }
        )
        assert model.connect_status is None
        assert model.gen_connect_status is None
        assert model.stor_connect_status is None

    def test_2018_server_gets_no_gen_connect_status_when_only_connect_status_is_supplied(
        self,
    ) -> None:
        model = build_der_status(
            {"readingTime": 0, "connectStatus": {"dateTime": 100, "value": 0b11}},
            profile=ServerProfile(base=Edition.E2018),
        )
        assert model.gen_connect_status is None
        assert model.stor_connect_status is None
        assert model.connect_status is None

    def test_2018_server_gets_gen_connect_status_and_no_connect_status(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "genConnectStatus": {"dateTime": 100, "value": 0b10111}},
            profile=ServerProfile(base=Edition.E2018),
        )
        assert model.connect_status is None
        assert isinstance(model.gen_connect_status, ConnectStatusType)
        assert model.gen_connect_status.value == bytes([0b10111])

    def test_2018_server_receives_the_2018_bitmap_when_both_are_supplied(self) -> None:
        """ConnectStatusType (2018) and ConnectStatusType2 (2023) define
        different bits above bit 0, so each edition gets its own bitmap."""
        data = {
            "readingTime": 0,
            "connectStatus": {"dateTime": 100, "value": 0b11},
            "genConnectStatus": {"dateTime": 100, "value": 0b10111},
        }
        model = build_der_status(data, profile=ServerProfile(base=Edition.E2018))
        assert model.connect_status is None
        assert model.gen_connect_status.value == bytes([0b10111])

    def test_2023_server_receives_the_2023_bitmap_when_both_are_supplied(self) -> None:
        data = {
            "readingTime": 0,
            "connectStatus": {"dateTime": 100, "value": 0b11},
            "genConnectStatus": {"dateTime": 100, "value": 0b10111},
        }
        model = build_der_status(data)
        assert model.connect_status.value == bytes([0b11])
        assert model.gen_connect_status is None

    def test_inverter_status(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "inverterStatus": {"dateTime": 200, "value": 3}}
        )
        assert isinstance(model.inverter_status, InverterStatusType)
        assert model.inverter_status.value == 3

    def test_local_control_mode_none_omitted(self) -> None:
        model = build_der_status({"readingTime": 0, "localControlModeStatus": None})
        assert model.local_control_mode_status is None

    def test_local_control_mode_none_value_omitted(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "localControlModeStatus": {"dateTime": 100, "value": None}}
        )
        assert model.local_control_mode_status is None

    def test_manufacturer_status(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "manufacturerStatus": {"dateTime": 300, "value": "1000"}}
        )
        assert isinstance(model.manufacturer_status, ManufacturerStatusType)
        assert model.manufacturer_status.value == "1000"

    def test_manufacturer_status_none_value_omitted(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "manufacturerStatus": {"dateTime": 100, "value": None}}
        )
        assert model.manufacturer_status is None

    def test_operational_mode_status(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "operationalModeStatus": {"dateTime": 400, "value": 1}}
        )
        assert isinstance(model.operational_mode_status, OperationalModeStatusType)
        assert model.operational_mode_status.value == 1

    def test_operational_mode_none_value_omitted(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "operationalModeStatus": {"dateTime": 100, "value": None}}
        )
        assert model.operational_mode_status is None

    def test_state_of_charge_status(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "stateOfChargeStatus": {"dateTime": 500, "value": 75}}
        )
        assert isinstance(model.state_of_charge_status, StateOfChargeStatusType)
        assert model.state_of_charge_status.value.value == 75

    def test_state_of_charge_none_value_omitted(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "stateOfChargeStatus": {"dateTime": 500, "value": None}}
        )
        assert model.state_of_charge_status is None

    def test_storage_mode_status(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "storageModeStatus": {"dateTime": 600, "value": 2}}
        )
        assert isinstance(model.storage_mode_status, StorageModeStatusType)
        assert model.storage_mode_status.value == 2

    def test_stor_connect_status_goes_to_a_2018_server(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "storConnectStatus": {"dateTime": 700, "value": 1}},
            profile=ServerProfile(base=Edition.E2018),
        )
        assert isinstance(model.stor_connect_status, ConnectStatusType)
        assert model.stor_connect_status.value == b""
        assert model.connect_status is None

    def test_2023_server_never_gets_stor_connect_status(self) -> None:
        model = build_der_status(
            {
                "readingTime": 0,
                "connectStatus": {"dateTime": 700, "value": 0b11},
                "storConnectStatus": {"dateTime": 700, "value": 0b101},
            }
        )
        assert model.stor_connect_status is None
        assert model.connect_status.value == bytes([0b11])

    def test_gen_connect_none_value_omitted(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "genConnectStatus": {"dateTime": 100, "value": None}}
        )
        assert model.gen_connect_status is None

    def test_inverter_status_none_value_omitted(self) -> None:
        model = build_der_status(
            {"readingTime": 0, "inverterStatus": {"dateTime": 100, "value": None}}
        )
        assert model.inverter_status is None

    def test_full_status_from_print_demo(self) -> None:
        now = int(time.time())
        status_data = {
            "alarmStatus": 0,
            "connectStatus": {"dateTime": now, "value": 1},
            "inverterStatus": {"dateTime": now, "value": 3},
            "localControlModeStatus": None,
            "manufacturerStatus": {"dateTime": now, "value": "1000"},
            "operationalModeStatus": {"dateTime": now, "value": 1},
            "readingTime": now,
        }
        model = build_der_status(status_data)

        assert model.reading_time.value == now
        assert model.alarm_status is None  # 0 treated as absent
        assert model.connect_status is not None
        assert model.gen_connect_status is None  # Not populated when connectStatus present
        assert model.inverter_status is not None
        assert model.local_control_mode_status is None
        assert model.manufacturer_status is not None
        assert model.operational_mode_status is not None


class TestDerStatusXml:
    """XML serialization roundtrip and schema validation tests."""

    def test_xml_roundtrip(self) -> None:
        now = 1700000000
        model = build_der_status(
            {
                "readingTime": now,
                "alarmStatus": 1,
                "connectStatus": {"dateTime": now, "value": 1},
                "inverterStatus": {"dateTime": now, "value": 3},
                "operationalModeStatus": {"dateTime": now, "value": 1},
            }
        )
        xml = to_xml(model)
        parsed = from_xml(xml, Derstatus)

        assert parsed.reading_time.value == now
        assert parsed.alarm_status == b"\x00\x00\x00\x01"
        assert parsed.connect_status is not None
        assert parsed.inverter_status is not None
        assert parsed.inverter_status.value == 3

    def test_xml_omits_none_fields(self) -> None:
        model = build_der_status({"readingTime": 1700000000})
        xml = to_xml(model)
        root = fromstring(xml.decode("utf-8"))

        assert root.find(f"{{{NS}}}alarmStatus") is None
        assert root.find(f"{{{NS}}}genConnectStatus") is None
        assert root.findtext(f"{{{NS}}}readingTime") == "1700000000"

    def test_xml_has_namespace(self) -> None:
        model = build_der_status({"readingTime": 1700000000})
        xml = to_xml(model)
        assert NS.encode() in xml

    def test_xml_returns_bytes(self) -> None:
        model = build_der_status({"readingTime": 1700000000})
        assert isinstance(to_xml(model), bytes)

    def test_xml_schema_validation_minimal(self) -> None:
        model = build_der_status({"readingTime": 1700000000})
        xml = to_xml(model)
        errors = validate_xml(xml)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_xml_schema_validation_full(self) -> None:
        now = 1700000000
        model = build_der_status(
            {
                "readingTime": now,
                "alarmStatus": 255,
                "connectStatus": {"dateTime": now, "value": 1},
                "inverterStatus": {"dateTime": now, "value": 3},
                "manufacturerStatus": {"dateTime": now, "value": "1000"},
                "operationalModeStatus": {"dateTime": now, "value": 1},
                "stateOfChargeStatus": {"dateTime": now, "value": 75},
                "storageModeStatus": {"dateTime": now, "value": 2},
                "storConnectStatus": {"dateTime": now, "value": 1},
            }
        )
        xml = to_xml(model)
        errors = validate_xml(xml)
        assert errors == [], f"Schema validation errors: {errors}"


def test_default_time_applies_to_reading_time_and_substatus():
    model = build_der_status({"connectStatus": {"value": 1}}, default_time=777)
    assert model.reading_time.value == 777
    assert model.connect_status is not None
    assert model.connect_status.date_time.value == 777


def test_connector_timestamps_win_over_default_time():
    model = build_der_status(
        {"readingTime": 555, "connectStatus": {"value": 1, "dateTime": 556}},
        default_time=777,
    )
    assert model.reading_time.value == 555
    assert model.connect_status is not None
    assert model.connect_status.date_time.value == 556


class TestIeee2030_5Values:
    """Values outside their IEEE 2030.5 type are left out, not sent.

    Ranges from the IEEE 2030.5 schemas: InverterStatusType 0 to 10,
    OperationalModeStatusType 0 to 3, LocalControlModeStatusType 0 to 1,
    StorageModeStatusType 0 to 2, stateOfChargeStatus 0 to 10000;
    ConnectStatusType2 bits 0 to 1, ConnectStatusType bits 0 to 4, alarmStatus
    bits 0 to 10.
    """

    @staticmethod
    def _stamped(value: int) -> dict[str, int]:
        return {"dateTime": 100, "value": value}

    @pytest.mark.parametrize(
        ("field", "attr", "highest"),
        [
            ("inverterStatus", "inverter_status", 10),
            ("operationalModeStatus", "operational_mode_status", 3),
            ("localControlModeStatus", "local_control_mode_status", 1),
            ("storageModeStatus", "storage_mode_status", 2),
        ],
    )
    def test_enumerations_accept_their_range_and_leave_out_the_rest(
        self, field: str, attr: str, highest: int
    ) -> None:
        for value in (0, highest):
            model = build_der_status({"readingTime": 0, field: self._stamped(value)})
            assert getattr(model, attr).value == value
        for value in (highest + 1, -1):
            model = build_der_status({"readingTime": 0, field: self._stamped(value)})
            assert getattr(model, attr) is None

    def test_state_of_charge_is_hundredths_of_a_percent(self) -> None:
        model = build_der_status({"readingTime": 0, "stateOfChargeStatus": self._stamped(10000)})
        assert model.state_of_charge_status.value.value == 10000
        model = build_der_status({"readingTime": 0, "stateOfChargeStatus": self._stamped(10001)})
        assert model.state_of_charge_status is None

    def test_connect_bitmaps_with_reserved_bits_are_left_out(self) -> None:
        model = build_der_status({"readingTime": 0, "connectStatus": self._stamped(0b100)})
        assert model.connect_status is None
        model = build_der_status(
            {"readingTime": 0, "genConnectStatus": self._stamped(0b100000)},
            profile=ServerProfile(base=Edition.E2018),
        )
        assert model.gen_connect_status is None
        model = build_der_status(
            {"readingTime": 0, "genConnectStatus": self._stamped(0b10111)},
            profile=ServerProfile(base=Edition.E2018),
        )
        assert model.gen_connect_status.value == bytes([0b10111])

    def test_alarm_status_with_reserved_bits_is_left_out(self) -> None:
        model = build_der_status({"readingTime": 0, "alarmStatus": 1 << 11})
        assert model.alarm_status is None
        # A rejected bitmap must not become an explicit clear.
        model = build_der_status(
            {"readingTime": 0, "alarmStatus": (1 << 11) | 1}, always_send_alarm_status=True
        )
        assert model.alarm_status is None
        model = build_der_status({"readingTime": 0, "alarmStatus": (1 << 10) | 1})
        assert model.alarm_status == ((1 << 10) | 1).to_bytes(4, "big")

    def test_2023_payload_has_no_deprecated_connect_fields_and_validates(self) -> None:
        now = 1700000000
        model = build_der_status(
            {
                "readingTime": now,
                "connectStatus": self._stamped(0b11),
                "genConnectStatus": self._stamped(0b00101),
                "storConnectStatus": self._stamped(0b00001),
            }
        )
        xml = to_xml(model)
        root = fromstring(xml.decode("utf-8"))
        assert root.find(f"{{{NS}}}genConnectStatus") is None
        assert root.find(f"{{{NS}}}storConnectStatus") is None
        assert root.find(f"{{{NS}}}connectStatus") is not None
        assert validate_xml(xml) == []
        assert root.get("schemaVer") == "2.2"

    def test_2018_payload_has_the_2018_fields_and_no_connect_status(self) -> None:
        now = 1700000000
        model = build_der_status(
            {
                "readingTime": now,
                "connectStatus": self._stamped(0b11),
                "genConnectStatus": self._stamped(0b00101),
                "storConnectStatus": self._stamped(0b00001),
            },
            profile=ServerProfile(base=Edition.E2018),
        )
        root = fromstring(to_xml(model, profile=ServerProfile(base=Edition.E2018)).decode("utf-8"))
        assert root.find(f"{{{NS}}}genConnectStatus") is not None
        assert root.find(f"{{{NS}}}storConnectStatus") is not None
        assert root.find(f"{{{NS}}}connectStatus") is None
        assert root.get("schemaVer") is None

    def test_sunspec_status_gives_each_edition_its_own_bitmap(self) -> None:
        from py20305.connectors.sunspec._ieee2030_5 import status

        raw = {
            "inverterStatus": {"dateTime": 100, "value": 4},
            "connectStatus": {"dateTime": 100, "value": 1},
        }
        supplied = status(raw)

        new = build_der_status(supplied)
        old = build_der_status(supplied, profile=ServerProfile(base=Edition.E2018))

        assert new.connect_status is not None
        assert new.gen_connect_status is None
        assert old.gen_connect_status is not None
        assert old.connect_status is None
