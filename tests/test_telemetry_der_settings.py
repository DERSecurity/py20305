"""Tests for DERSettings Pydantic model builder."""

from __future__ import annotations

import time
from xml.etree.ElementTree import fromstring

import pytest

from py20305.models.sep.sep import (
    ActivePower,
    AmpereHour,
    ApparentPower,
    Dersettings,
    PowerFactor,
    ReactivePower,
    VoltageRms,
    WattHour,
)
from py20305.telemetry.der_settings import build_der_settings
from py20305.xml.serialization import from_xml, to_xml, validate_xml

NS = "urn:ieee:std:2030.5:ns"
NS_CSIPAUS = "https://csipaus.org/ns"


def _print_demo_configuration() -> dict:
    """Representative configuration data (loosely based on PrintDemoConnector)
    that exercises every DERSettings field, including the storage settings."""
    return {
        "WMax": 10000,
        "WMaxOvrExt": 8500,
        "WOvrExtPF": 0.850,
        "WMaxUndExt": 8500,
        "WUndExtPF": 0.850,
        "VAMax": 10000,
        "VarMaxInj": 4400,
        "VarMaxAbs": 4400,
        "WChaRteMax": 10000,
        "VAChaRteMax": 10000,
        "WDisChaRteMax": 10000,
        "VADisChaRteMax": 10000,
        "WhMax": 30000,
        "AhMax": 120,
        "VNom": 240,
        "VMax": 264,
        "VMin": 211,
        "CtrlModes": 93323888,
    }


class TestBuildDerSettings:
    """Tests for build_der_settings model construction."""

    def test_integer_value(self) -> None:
        model = build_der_settings({"WMax": 10000}, updated_time=0)
        assert isinstance(model.set_max_w, ActivePower)
        assert model.set_max_w.value == 10000
        assert model.set_max_w.multiplier.value == 0

    def test_float_pf_value(self) -> None:
        model = build_der_settings({"WMax": 1000, "WOvrExtPF": 0.85}, updated_time=0)
        assert isinstance(model.set_min_pfover_excited, PowerFactor)
        assert model.set_min_pfover_excited.displacement == 85
        assert model.set_min_pfover_excited.multiplier.value == -2

    def test_ctrl_modes_bitmap(self) -> None:
        model = build_der_settings({"WMax": 1000, "CtrlModes": 93323888}, updated_time=0)
        assert model.modes_enabled is not None
        assert model.modes_enabled.value == (93323888).to_bytes(4, "big")

    def test_non_standard_fields_dropped(self) -> None:
        """WMaxOvrExt and WMaxUndExt are not valid DERSettings fields per XSD."""
        model = build_der_settings(
            {"WMax": 1000, "WMaxOvrExt": 8500, "WMaxUndExt": 8500}, updated_time=0
        )
        # These fields don't exist on the model
        assert not hasattr(model, "set_over_excited_w")

    def test_missing_optional_fields_are_none(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        assert model.set_max_va is None
        assert model.set_max_var is None
        assert model.modes_enabled is None

    def test_none_value_omitted(self) -> None:
        model = build_der_settings({"WMax": 1000, "VAMax": None}, updated_time=0)
        assert model.set_max_va is None

    def test_discharge_rate_present(self) -> None:
        model = build_der_settings(
            {"WMax": 1000, "WDisChaRteMax": 15000, "VADisChaRteMax": 16000}, updated_time=0
        )
        assert model.set_max_discharge_rate_w is not None
        assert model.set_max_discharge_rate_w.value == 15000
        assert model.set_max_discharge_rate_va is not None
        assert model.set_max_discharge_rate_va.value == 16000

    def test_discharge_rate_zero_reported_as_is(self) -> None:
        model = build_der_settings({"WMax": 1000, "WDisChaRteMax": 0}, updated_time=0)
        assert model.set_max_discharge_rate_w is not None
        assert model.set_max_discharge_rate_w.value == 0

    def test_discharge_rate_omitted_when_missing(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        assert model.set_max_discharge_rate_w is None
        assert model.set_max_discharge_rate_va is None

    def test_energy_capacity_present(self) -> None:
        model = build_der_settings({"WMax": 1000, "WhMax": 30000, "AhMax": 120}, updated_time=0)
        assert model.set_max_wh is not None
        assert model.set_max_wh.value == 30000
        assert model.set_max_ah is not None
        assert model.set_max_ah.value == 120

    def test_energy_capacity_zero_reported_as_is(self) -> None:
        model = build_der_settings({"WMax": 1000, "WhMax": 0, "AhMax": 0}, updated_time=0)
        assert model.set_max_wh is not None
        assert model.set_max_wh.value == 0
        assert model.set_max_ah is not None
        assert model.set_max_ah.value == 0

    def test_energy_capacity_omitted_when_missing(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        assert model.set_max_wh is None
        assert model.set_max_ah is None

    def test_wmax_required(self) -> None:
        with pytest.raises(ValueError, match="WMax is required"):
            build_der_settings({}, updated_time=0)

    def test_updated_time_set(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=1707000000)
        assert model.updated_time.value == 1707000000

    def test_updated_time_defaults_to_now(self) -> None:
        before = int(time.time())
        model = build_der_settings({"WMax": 1000})
        after = int(time.time())
        assert before <= model.updated_time.value <= after

    def test_grad_w_default_zero(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        assert model.set_grad_w == 0

    def test_grad_w_from_connector(self) -> None:
        model = build_der_settings({"WMax": 1000, "GradW": 100}, updated_time=0)
        assert model.set_grad_w == 100

    def test_grad_w_float_truncated(self) -> None:
        model = build_der_settings({"WMax": 1000, "GradW": 50.7}, updated_time=0)
        assert model.set_grad_w == 50

    def test_full_print_demo(self) -> None:
        config = _print_demo_configuration()
        model = build_der_settings(config, updated_time=1707000000)

        assert isinstance(model.set_max_w, ActivePower)
        assert isinstance(model.set_min_pfover_excited, PowerFactor)
        assert isinstance(model.set_min_pfunder_excited, PowerFactor)
        assert isinstance(model.set_max_va, ApparentPower)
        assert isinstance(model.set_max_var, ReactivePower)
        assert isinstance(model.set_max_var_neg, ReactivePower)
        assert isinstance(model.set_max_charge_rate_w, ActivePower)
        assert isinstance(model.set_max_charge_rate_va, ApparentPower)
        assert isinstance(model.set_max_wh, WattHour)
        assert isinstance(model.set_max_ah, AmpereHour)
        assert isinstance(model.set_vnom, VoltageRms)
        assert isinstance(model.set_max_v, VoltageRms)
        assert isinstance(model.set_min_v, VoltageRms)
        assert model.updated_time.value == 1707000000


class TestDerSettingsXml:
    """XML serialization roundtrip and schema validation tests."""

    def test_xml_roundtrip(self) -> None:
        config = _print_demo_configuration()
        model = build_der_settings(config, updated_time=1707000000)
        xml = to_xml(model)
        parsed = from_xml(xml, Dersettings)

        assert parsed.set_max_w.value == 10000
        assert parsed.set_max_va.value == 10000
        assert parsed.set_max_var.value == 4400
        assert parsed.set_min_pfover_excited.displacement == 85
        assert parsed.updated_time.value == 1707000000
        assert parsed.set_grad_w == 0

    def test_xml_has_namespace(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        xml = to_xml(model)
        assert NS.encode() in xml

    def test_xml_returns_bytes(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        assert isinstance(to_xml(model), bytes)

    def test_xml_schema_validation_minimal(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=1707000000)
        xml = to_xml(model)
        errors = validate_xml(xml)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_xml_schema_validation_full(self) -> None:
        config = _print_demo_configuration()
        model = build_der_settings(config, updated_time=1707000000)
        xml = to_xml(model)
        errors = validate_xml(xml)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_xml_omits_none_fields(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        xml = to_xml(model)
        root = fromstring(xml.decode("utf-8"))

        assert root.find(f"{{{NS}}}setMaxVA") is None
        assert root.find(f"{{{NS}}}setMaxVar") is None
        assert root.find(f"{{{NS}}}setMaxW") is not None

    def test_xml_grad_w_plain_integer(self) -> None:
        model = build_der_settings({"WMax": 1000}, updated_time=0)
        xml = to_xml(model)
        root = fromstring(xml.decode("utf-8"))

        grad_w = root.find(f"{{{NS}}}setGradW")
        assert grad_w is not None
        assert grad_w.text == "0"
        # Should not have sub-elements (plain int, not ActivePower)
        assert grad_w.find(f"{{{NS}}}multiplier") is None

    def test_non_standard_fields_not_in_xml(self) -> None:
        config = _print_demo_configuration()
        model = build_der_settings(config, updated_time=0)
        xml = to_xml(model)
        text = xml.decode("utf-8")
        assert "setOverExcitedW" not in text
        assert "rtgUnderExcitedW" not in text


class TestCsipAusDoeModesEnabled:
    """CSIP-AUS doeModesEnabled extension on DERSettings (connector-provided,
    defaults to export + import limits, only in csip_aus_mode)."""

    def test_default_export_import_when_not_provided(self):
        st = build_der_settings({"WMax": 5000}, csip_aus_mode=True)
        assert len(st.other_element) == 1
        assert st.other_element[0].value == b"\x03"  # opModExpLimW | opModImpLimW

    def test_connector_provided_value(self):
        st = build_der_settings({"WMax": 5000, "DoeModesEnabled": 0x05}, csip_aus_mode=True)
        assert st.other_element[0].value == b"\x05"

    def test_omitted_without_csip_aus_mode(self):
        st = build_der_settings({"WMax": 5000, "DoeModesEnabled": 0x05})
        assert st.other_element == []

    def test_serializes_with_csipaus_namespace(self):
        st = build_der_settings({"WMax": 5000, "DoeModesEnabled": 0x0F}, csip_aus_mode=True)
        xml = to_xml(st, include_csipaus=True)
        assert b"<csipaus:doeModesEnabled>0F</csipaus:doeModesEnabled>" in xml

    def test_doe_modes_enabled_is_last_child(self):
        """The CSIP-AUS extension is appended via xs:extension, so it must be
        the last child (after updatedTime). Emitting it before the base
        sequence makes a CSIP-AUS server reject the DERSettings PUT with an
        'element is not expected' schema error."""
        st = build_der_settings(
            {"WMax": 5000, "CtrlModes": 0x03, "DoeModesEnabled": 0x03},
            updated_time=1707000000,
            csip_aus_mode=True,
        )
        root = fromstring(to_xml(st, include_csipaus=True).decode("utf-8"))
        children = list(root)
        assert children[-1].tag == f"{{{NS_CSIPAUS}}}doeModesEnabled"
        tags = [c.tag for c in children]
        assert tags.index(f"{{{NS_CSIPAUS}}}doeModesEnabled") > tags.index(f"{{{NS}}}updatedTime")


def test_doe_modes_enabled_masks_reserved_bits_and_rejects_bool():
    st = build_der_settings({"WMax": 5000, "DoeModesEnabled": 0xFF}, csip_aus_mode=True)
    assert st.other_element[0].value == b"\x0f"
    st = build_der_settings({"WMax": 5000, "DoeModesEnabled": -1}, csip_aus_mode=True)
    assert st.other_element[0].value == b"\x0f"
    st = build_der_settings({"WMax": 5000, "DoeModesEnabled": True}, csip_aus_mode=True)
    assert st.other_element[0].value == b"\x03"
