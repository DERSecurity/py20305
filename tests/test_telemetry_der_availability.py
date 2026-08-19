"""Tests for DERAvailability Pydantic model builder."""

from __future__ import annotations

from unittest.mock import patch
from xml.etree.ElementTree import fromstring

from py20305.models.sep.sep import ActivePower, Deravailability, PerCent, ReactivePower
from py20305.telemetry.der_availability import build_der_availability
from py20305.xml.serialization import from_xml, to_xml, validate_xml

NS = "urn:ieee:std:2030.5:ns"


class TestBuildDerAvailability:
    """Tests for build_der_availability model construction."""

    def test_all_fields_present(self) -> None:
        data = {
            "availabilityDuration": 86400,
            "maxChargeDuration": 3600,
            "readingTime": 1700000000,
            "reserveChargePercent": 5000,
            "reservePercent": 8000,
            "statVarAvail": {"value": 5000, "multiplier": 0},
            "statWAvail": {"value": 10000, "multiplier": 1},
        }
        model = build_der_availability(data)

        assert isinstance(model, Deravailability)
        assert model.availability_duration == 86400
        assert model.max_charge_duration == 3600
        assert model.reading_time.value == 1700000000
        assert isinstance(model.reserve_charge_percent, PerCent)
        assert model.reserve_charge_percent.value == 5000
        assert isinstance(model.reserve_percent, PerCent)
        assert model.reserve_percent.value == 8000

    def test_none_fields_omitted(self) -> None:
        data = {
            "availabilityDuration": None,
            "maxChargeDuration": None,
            "readingTime": 1700000000,
            "reserveChargePercent": None,
            "reservePercent": None,
            "statVarAvail": None,
            "statWAvail": None,
        }
        model = build_der_availability(data)

        assert model.availability_duration is None
        assert model.max_charge_duration is None
        assert model.reserve_charge_percent is None
        assert model.reserve_percent is None
        assert model.stat_var_avail is None
        assert model.stat_wavail is None
        assert model.reading_time.value == 1700000000

    def test_reading_time_defaults_to_now(self) -> None:
        with patch("py20305.telemetry.der_availability.time") as mock_time:
            mock_time.time.return_value = 1700000099.5
            model = build_der_availability({"readingTime": None})

        assert model.reading_time.value == 1700000099

    def test_empty_data_defaults_reading_time(self) -> None:
        with patch("py20305.telemetry.der_availability.time") as mock_time:
            mock_time.time.return_value = 1700000000.0
            model = build_der_availability({})

        assert model.reading_time.value == 1700000000

    def test_stat_wavail_model(self) -> None:
        data = {
            "readingTime": 1700000000,
            "statWAvail": {"value": 10000, "multiplier": 2},
        }
        model = build_der_availability(data)

        assert isinstance(model.stat_wavail, ActivePower)
        assert model.stat_wavail.value == 10000
        assert model.stat_wavail.multiplier.value == 2

    def test_stat_var_avail_model(self) -> None:
        data = {
            "readingTime": 1700000000,
            "statVarAvail": {"value": 5000, "multiplier": 0},
        }
        model = build_der_availability(data)

        assert isinstance(model.stat_var_avail, ReactivePower)
        assert model.stat_var_avail.value == 5000
        assert model.stat_var_avail.multiplier.value == 0

    def test_power_element_defaults_zero(self) -> None:
        """Empty dict defaults multiplier=0, value=0."""
        data = {
            "readingTime": 1700000000,
            "statWAvail": {},
        }
        model = build_der_availability(data)

        assert isinstance(model.stat_wavail, ActivePower)
        assert model.stat_wavail.value == 0
        assert model.stat_wavail.multiplier.value == 0


class TestDerAvailabilityXml:
    """XML serialization roundtrip and schema validation tests."""

    def test_xml_roundtrip_all_fields(self) -> None:
        data = {
            "availabilityDuration": 86400,
            "maxChargeDuration": 3600,
            "readingTime": 1700000000,
            "reserveChargePercent": 5000,
            "reservePercent": 8000,
            "statVarAvail": {"value": 5000, "multiplier": 0},
            "statWAvail": {"value": 10000, "multiplier": 1},
        }
        model = build_der_availability(data)
        xml = to_xml(model)
        parsed = from_xml(xml, Deravailability)

        assert parsed.availability_duration == 86400
        assert parsed.max_charge_duration == 3600
        assert parsed.reading_time.value == 1700000000
        assert parsed.reserve_charge_percent is not None
        assert parsed.reserve_charge_percent.value == 5000
        assert parsed.stat_wavail is not None
        assert parsed.stat_wavail.value == 10000

    def test_xml_omits_none_fields(self) -> None:
        data = {"readingTime": 1700000000}
        model = build_der_availability(data)
        xml = to_xml(model)
        root = fromstring(xml.decode("utf-8"))

        assert root.find(f"{{{NS}}}availabilityDuration") is None
        assert root.find(f"{{{NS}}}statWAvail") is None
        assert root.findtext(f"{{{NS}}}readingTime") == "1700000000"

    def test_xml_returns_bytes(self) -> None:
        model = build_der_availability({"readingTime": 1700000000})
        assert isinstance(to_xml(model), bytes)

    def test_xml_has_namespace(self) -> None:
        model = build_der_availability({"readingTime": 1700000000})
        xml = to_xml(model)
        assert NS.encode() in xml

    def test_xml_schema_validation(self) -> None:
        data = {
            "availabilityDuration": 86400,
            "readingTime": 1700000000,
            "reservePercent": 8000,
            "statWAvail": {"value": 10000, "multiplier": 0},
        }
        model = build_der_availability(data)
        xml = to_xml(model)
        errors = validate_xml(xml)
        assert errors == [], f"Schema validation errors: {errors}"


def test_default_time_used_when_reading_time_missing():
    model = build_der_availability({}, default_time=1234)
    assert model.reading_time.value == 1234


def test_connector_reading_time_wins_over_default_time():
    model = build_der_availability({"readingTime": 99}, default_time=1234)
    assert model.reading_time.value == 99


def test_connector_reading_time_zero_preserved():
    """A connector-supplied readingTime of 0 is a value, not 'missing'."""
    model = build_der_availability({"readingTime": 0}, default_time=1234)
    assert model.reading_time.value == 0
