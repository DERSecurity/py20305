"""Tests for LFDI extraction utilities."""

from dataclasses import dataclass

from pydantic import BaseModel

from py20305.forwarders.lfdi_extraction import (
    extract_client_id,
    extract_lfdi,
)


class TestExtractLfdi:
    """Tests for extract_lfdi function."""

    def test_direct_metadata_lfdi(self) -> None:
        metadata = {"lfdi": "abc123def456789012345678901234567890"}
        result = extract_lfdi(metadata, None)
        assert result == "abc123def456789012345678901234567890"

    def test_direct_metadata_uppercase_lfdi(self) -> None:
        metadata = {"LFDI": "ABC123DEF456"}
        result = extract_lfdi(metadata, None)
        assert result == "ABC123DEF456"

    def test_direct_metadata_client_lfdi(self) -> None:
        metadata = {"client_lfdi": "client-device-lfdi"}
        result = extract_lfdi(metadata, None)
        assert result == "client-device-lfdi"

    def test_direct_metadata_client_id(self) -> None:
        metadata = {"client_id": "device-123"}
        result = extract_lfdi(metadata, None)
        assert result == "device-123"

    def test_direct_metadata_priority(self) -> None:
        # lfdi should take priority over client_id
        metadata = {"lfdi": "primary", "client_id": "secondary"}
        result = extract_lfdi(metadata, None)
        assert result == "primary"

    def test_nested_client_lfdi(self) -> None:
        metadata = {"client": {"lfdi": "nested-lfdi"}}
        result = extract_lfdi(metadata, None)
        assert result == "nested-lfdi"

    def test_direct_client_lfdi(self) -> None:
        metadata = {"client_lfdi": "direct-client-lfdi"}
        result = extract_lfdi(metadata, None)
        assert result == "direct-client-lfdi"

    def test_payload_dict_lfdi(self) -> None:
        metadata: dict[str, str] = {}
        payload = {"lfdi": "payload-lfdi", "data": "other"}
        result = extract_lfdi(metadata, payload)
        assert result == "payload-lfdi"

    def test_payload_dict_mrid(self) -> None:
        metadata: dict[str, str] = {}
        payload = {"mRID": "device-mrid-value"}
        result = extract_lfdi(metadata, payload)
        assert result == "device-mrid-value"

    def test_payload_nested_lfdi(self) -> None:
        metadata: dict[str, str] = {}
        payload = {"device": {"config": {"lfdi": "deeply-nested"}}}
        result = extract_lfdi(metadata, payload)
        assert result == "deeply-nested"

    def test_payload_list_with_lfdi(self) -> None:
        metadata: dict[str, str] = {}
        payload = [{"other": "data"}, {"lfdi": "list-item-lfdi"}]
        result = extract_lfdi(metadata, payload)
        assert result == "list-item-lfdi"

    def test_payload_string_hex_pattern(self) -> None:
        metadata: dict[str, str] = {}
        # 32 hex chars
        payload = "contains abc123def456789012345678901234ab hex string"
        result = extract_lfdi(metadata, payload)
        assert result == "abc123def456789012345678901234ab"

    def test_payload_bytes_lfdi(self) -> None:
        metadata: dict[str, str] = {}
        # 16 bytes = 32 hex chars
        payload = {"device_id": b"\x12\x34\x56\x78" * 4}
        result = extract_lfdi(metadata, payload)
        assert result == "12345678" * 4

    def test_client_lfdi_fallback(self) -> None:
        metadata: dict[str, str] = {}
        result = extract_lfdi(metadata, None, client_lfdi="fallback-agg")
        assert result == "fallback-agg"

    def test_client_lfdi_bytes(self) -> None:
        metadata: dict[str, str] = {}
        result = extract_lfdi(metadata, None, client_lfdi=b"\xab\xcd")  # type: ignore[arg-type]
        assert result == "abcd"

    def test_unknown_default(self) -> None:
        result = extract_lfdi({}, None)
        assert result == "Unknown"

    def test_empty_metadata_values_ignored(self) -> None:
        metadata = {"lfdi": "", "client_id": "   ", "LFDI": None}
        result = extract_lfdi(metadata, None)
        assert result == "Unknown"

    def test_unknown_metadata_value_ignored(self) -> None:
        metadata = {"lfdi": "unknown", "LFDI": "Unknown"}
        result = extract_lfdi(metadata, None)
        assert result == "Unknown"

    def test_pydantic_v2_model_payload(self) -> None:
        class DeviceModel(BaseModel):
            lfdi: str
            name: str

        metadata: dict[str, str] = {}
        payload = DeviceModel(lfdi="pydantic-lfdi", name="Device1")
        result = extract_lfdi(metadata, payload)
        assert result == "pydantic-lfdi"

    def test_dataclass_payload(self) -> None:
        @dataclass
        class DeviceData:
            mRID: str
            value: int

        metadata: dict[str, str] = {}
        payload = DeviceData(mRID="dataclass-mrid", value=42)
        # dataclasses without model_dump aren't auto-converted,
        # but the recursive search handles dicts
        result = extract_lfdi(metadata, {"nested": payload.__dict__})
        assert result == "dataclass-mrid"


class TestExtractClientId:
    """Tests for extract_client_id function."""

    def test_metadata_lfdi(self) -> None:
        metadata = {"lfdi": "client-lfdi"}
        result = extract_client_id(metadata, None)
        assert result == "client-lfdi"

    def test_metadata_client_id(self) -> None:
        metadata = {"client_id": "client-id-value"}
        result = extract_client_id(metadata, None)
        assert result == "client-id-value"

    def test_metadata_client_id_camel(self) -> None:
        metadata = {"clientId": "camelCaseId"}
        result = extract_client_id(metadata, None)
        assert result == "camelCaseId"

    def test_lfdi_priority_over_client_id(self) -> None:
        metadata = {"lfdi": "primary", "client_id": "secondary"}
        result = extract_client_id(metadata, None)
        assert result == "primary"

    def test_payload_fallback(self) -> None:
        metadata: dict[str, str] = {}
        payload = {"lfdi": "payload-client-id"}
        result = extract_client_id(metadata, payload)
        assert result == "payload-client-id"

    def test_unknown_default(self) -> None:
        result = extract_client_id({}, None)
        assert result == "Unknown"

    def test_empty_values_ignored(self) -> None:
        metadata = {"lfdi": "", "client_id": None}
        result = extract_client_id(metadata, None)
        assert result == "Unknown"


class TestHexPatternMatching:
    """Tests for hex pattern regex matching."""

    def test_32_char_hex(self) -> None:
        # Minimum LFDI length
        metadata: dict[str, str] = {}
        payload = "id:12345678901234567890123456789012"
        result = extract_lfdi(metadata, payload)
        assert result == "12345678901234567890123456789012"

    def test_64_char_hex(self) -> None:
        # Maximum LFDI length
        metadata: dict[str, str] = {}
        hex_64 = "a" * 64
        payload = f"contains {hex_64} in text"
        result = extract_lfdi(metadata, payload)
        assert result == hex_64

    def test_mixed_case_hex(self) -> None:
        metadata: dict[str, str] = {}
        payload = "aBcDeF1234567890aBcDeF1234567890"
        result = extract_lfdi(metadata, payload)
        assert result == "aBcDeF1234567890aBcDeF1234567890"

    def test_hex_too_short_not_matched(self) -> None:
        metadata: dict[str, str] = {}
        # 31 chars - too short
        payload = "1234567890123456789012345678901"
        result = extract_lfdi(metadata, payload)
        assert result == "Unknown"

    def test_non_hex_not_matched(self) -> None:
        metadata: dict[str, str] = {}
        # Contains 'g' which is not hex
        payload = "12345678901234567890123456789g12"
        result = extract_lfdi(metadata, payload)
        assert result == "Unknown"
