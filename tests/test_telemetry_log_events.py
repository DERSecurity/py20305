"""Tests for LogEvent creation and XML serialization."""

from __future__ import annotations

import pytest

from py20305.telemetry.log_events import (
    DER_ALARM_NAMES,
    FUNCTION_SET_DER,
    IEEE_2030_5_PEN,
    PROFILE_IEEE_2030_5,
    alarm_bits_to_log_events,
    alarm_log_event_code,
    create_log_event_xml,
    extract_alarm_status,
    unmapped_alarm_bits,
)


class TestCreateLogEventXml:
    """Tests for create_log_event_xml."""

    def test_basic_structure(self) -> None:
        xml = create_log_event_xml(log_event_code=0)
        text = xml.decode("utf-8")
        assert "<LogEvent" in text
        assert "</LogEvent>" in text
        assert "urn:ieee:std:2030.5:ns" in text

    def test_schema_ver_present(self) -> None:
        xml = create_log_event_xml(log_event_code=0)
        text = xml.decode("utf-8")
        assert 'schemaVer="2.2"' in text

    def test_required_fields_present(self) -> None:
        xml = create_log_event_xml(log_event_code=42, log_event_id=7)
        text = xml.decode("utf-8")
        assert "<createdDateTime>" in text
        assert f"<functionSet>{FUNCTION_SET_DER}</functionSet>" in text
        assert "<logEventCode>" in text
        assert "<logEventID>7</logEventID>" in text
        assert f"<logEventPEN>{IEEE_2030_5_PEN}</logEventPEN>" in text
        assert f"<profileID>{PROFILE_IEEE_2030_5}</profileID>" in text

    def test_log_event_code_emitted_verbatim(self) -> None:
        """The builder no longer derives the code from an alarm bitmap -- the
        caller passes an IEEE-assigned code (CSIP s5.2.5.3) and it goes out
        as-is. Local Emergency is 14."""
        text = create_log_event_xml(log_event_code=14).decode("utf-8")
        assert "<logEventCode>14</logEventCode>" in text

    def test_code_zero_is_valid(self) -> None:
        """Code 0 is Over Current -- a real alarm, not "no alarm". Callers must
        not pass an empty bitmap here (see alarm_bits_to_log_events)."""
        xml = create_log_event_xml(log_event_code=0)
        text = xml.decode("utf-8")
        assert "<logEventCode>0</logEventCode>" in text

    def test_custom_function_set(self) -> None:
        xml = create_log_event_xml(log_event_code=0, function_set=0)
        text = xml.decode("utf-8")
        assert "<functionSet>0</functionSet>" in text

    def test_custom_profile_id(self) -> None:
        xml = create_log_event_xml(log_event_code=0, profile_id=1)
        text = xml.decode("utf-8")
        assert "<profileID>1</profileID>" in text

    def test_details_included(self) -> None:
        xml = create_log_event_xml(log_event_code=0, details="Test alarm")
        text = xml.decode("utf-8")
        assert "<details>Test alarm</details>" in text

    def test_details_truncated_to_32_chars(self) -> None:
        long_details = "x" * 50
        xml = create_log_event_xml(log_event_code=0, details=long_details)
        text = xml.decode("utf-8")
        assert f"<details>{'x' * 32}</details>" in text

    def test_no_details_when_none(self) -> None:
        xml = create_log_event_xml(log_event_code=0, details=None)
        text = xml.decode("utf-8")
        assert "<details>" not in text

    def test_returns_bytes(self) -> None:
        result = create_log_event_xml(log_event_code=0)
        assert isinstance(result, bytes)

    def test_utf8_encoding(self) -> None:
        result = create_log_event_xml(log_event_code=0)
        assert result.startswith(b'<?xml version="1.0" encoding="UTF-8"?>')

    def test_server_2018_compat(self) -> None:
        xml = create_log_event_xml(log_event_code=0, server_2018_compat=True)
        text = xml.decode("utf-8")
        assert "schemaVer" not in text
        assert "<LogEvent" in text


class TestExtractAlarmStatus:
    """Tests for extract_alarm_status."""

    def test_int_value(self) -> None:
        assert extract_alarm_status({"alarmStatus": 42}) == 42

    def test_zero(self) -> None:
        assert extract_alarm_status({"alarmStatus": 0}) == 0

    def test_missing_key(self) -> None:
        assert extract_alarm_status({}) == 0

    def test_bytes_value(self) -> None:
        # 4 bytes big-endian for value 256
        assert extract_alarm_status({"alarmStatus": b"\x00\x00\x01\x00"}) == 256

    def test_none_defaults_to_zero(self) -> None:
        assert extract_alarm_status({"alarmStatus": None}) == 0


def test_created_time_override_lands_in_created_date_time():
    xml = create_log_event_xml(log_event_code=1, created_time=1234567890)
    assert b"<createdDateTime>1234567890</createdDateTime>" in xml


class TestCsipAlarmCodeMapping:
    """CSIP Implementation Guide s5.2.5.3 Table 14: each DER alarm bit maps to
    an IEEE-assigned LogEvent code ``2*bit``, with ``2*bit+1`` for its
    return-to-normal counterpart."""

    def test_full_table(self) -> None:
        expected = {
            0: (0, 1),  # Over Current
            1: (2, 3),  # Over Voltage
            2: (4, 5),  # Under Voltage
            3: (6, 7),  # Over Frequency
            4: (8, 9),  # Under Frequency
            5: (10, 11),  # Voltage Imbalance
            6: (12, 13),  # Current Imbalance
            7: (14, 15),  # Local Emergency
            8: (16, 17),  # Remote Emergency
            9: (18, 19),  # Low Input Power
            10: (20, 21),  # Phase Rotation
        }
        for bit, (code, rtn) in expected.items():
            assert alarm_log_event_code(bit) == code, bit
            assert alarm_log_event_code(bit, returned_to_normal=True) == rtn, bit

    def test_codes_fit_uint8(self) -> None:
        """logEventCode is UInt8 in the XSD."""
        for bit in DER_ALARM_NAMES:
            assert 0 <= alarm_log_event_code(bit, returned_to_normal=True) <= 255

    def test_reserved_bit_has_no_code(self) -> None:
        with pytest.raises(ValueError, match=r"no IEEE 2030\.5-assigned"):
            alarm_log_event_code(11)


class TestAlarmTransitions:
    def test_bit_set_emits_alarm_code(self) -> None:
        """The reported case: SunSpec bit 7 (0x80) -> Local Emergency = 14."""
        assert alarm_bits_to_log_events(0, 0x80) == [(7, 14)]

    def test_bit_cleared_emits_rtn_code(self) -> None:
        assert alarm_bits_to_log_events(0x80, 0) == [(7, 15)]

    def test_unchanged_emits_nothing(self) -> None:
        """A persisting alarm is not re-posted every cycle (CSIP s4.6.3:
        reported "as they occur")."""
        assert alarm_bits_to_log_events(0x80, 0x80) == []
        assert alarm_bits_to_log_events(0, 0) == []

    def test_multiple_bits_fan_out_in_bit_order(self) -> None:
        # bits 0 and 2 -> Over Current (0) and Under Voltage (4)
        assert alarm_bits_to_log_events(0, 0b101) == [(0, 0), (2, 4)]

    def test_simultaneous_set_and_clear(self) -> None:
        # bit 7 clears (RTN 15), bit 8 sets (Remote Emergency 16)
        assert alarm_bits_to_log_events(0x80, 0x100) == [(7, 15), (8, 16)]

    def test_reserved_bits_skipped(self) -> None:
        """SunSpec populates bits 11+, which IEEE reserves -- no code exists,
        so they produce no LogEvent (they still ride in DERStatus)."""
        assert alarm_bits_to_log_events(0, 1 << 11) == []
        # A reserved bit alongside a mapped one doesn't suppress the mapped one.
        assert alarm_bits_to_log_events(0, (1 << 11) | 0x80) == [(7, 14)]


class TestUnmappedAlarmBits:
    def test_reports_reserved_bits(self) -> None:
        assert unmapped_alarm_bits((1 << 11) | (1 << 16) | 0x80) == [11, 16]

    def test_empty_when_all_mapped(self) -> None:
        assert unmapped_alarm_bits(0x80) == []
        assert unmapped_alarm_bits(0) == []
