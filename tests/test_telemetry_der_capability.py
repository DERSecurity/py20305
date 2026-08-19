"""Tests for DERCapability Pydantic model builder."""

from __future__ import annotations

from xml.etree.ElementTree import fromstring

import pytest

from py20305.models.sep.sep import (
    ActivePower,
    ApparentPower,
    Dercapability,
    PowerFactor,
    ReactivePower,
    ReactiveSusceptance,
    VoltageRms,
)
from py20305.telemetry.der_capability import build_der_capability
from py20305.xml.serialization import from_xml, to_xml, validate_xml

NS = "urn:ieee:std:2030.5:ns"
NS_CSIPAUS = "https://csipaus.org/ns"


def _print_demo_nameplate() -> dict:
    """Representative nameplate data (loosely based on PrintDemoConnector) that
    exercises every DERCapability field, including the storage ratings."""
    return {
        "WMaxRtg": 15000,
        "WOvrExtRtg": 15000,
        "WOvrExtRtgPF": 0.800,
        "WUndExtRtg": 15000,
        "WUndExtRtgPF": 0.800,
        "VAMaxRtg": 15000,
        "VarMaxInjRtg": 4400,
        "VarMaxAbsRtg": 4400,
        "WChaRteMaxRtg": 15000,
        "VAChaRteMaxRtg": 15000,
        "WDisChaRteMaxRtg": 15000,
        "VADisChaRteMaxRtg": 15000,
        "WhMaxRtg": 30000,
        "VNomRtg": 240,
        "VMaxRtg": 264,
        "VMinRtg": 211,
        "ReactSusceptRtg": 0,
        "NorOpCatRtg": 1,
        "AbnOpCatRtg": 1,
        "CtrlModes": 93323888,
    }


class TestBuildDerCapability:
    """Tests for build_der_capability model construction."""

    def test_integer_value(self) -> None:
        model = build_der_capability({"WMaxRtg": 15000})
        assert isinstance(model.rtg_max_w, ActivePower)
        assert model.rtg_max_w.value == 15000
        assert model.rtg_max_w.multiplier.value == 0

    def test_float_pf_value(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000, "WOvrExtRtgPF": 0.800})
        assert isinstance(model.rtg_over_excited_pf, PowerFactor)
        assert model.rtg_over_excited_pf.displacement == 8
        assert model.rtg_over_excited_pf.multiplier.value == -1

    def test_direct_int_category(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000, "NorOpCatRtg": 1})
        assert model.rtg_normal_category == 1

    def test_ctrl_modes_bitmap(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000, "CtrlModes": 93323888})
        assert model.modes_supported.value == (93323888).to_bytes(4, "big")

    def test_missing_optional_fields_are_none(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        assert model.rtg_max_va is None
        assert model.rtg_max_var is None
        assert model.rtg_over_excited_pf is None
        assert model.rtg_normal_category is None

    def test_none_value_omitted(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000, "VAMaxRtg": None})
        assert model.rtg_max_va is None

    def test_storage_ratings_present(self) -> None:
        model = build_der_capability(
            {
                "WMaxRtg": 1000,
                "WDisChaRteMaxRtg": 15000,
                "VADisChaRteMaxRtg": 16000,
                "WhMaxRtg": 30000,
                "AhMaxRtg": 120,
            }
        )
        assert model.rtg_max_discharge_rate_w is not None
        assert model.rtg_max_discharge_rate_w.value == 15000
        assert model.rtg_max_discharge_rate_va is not None
        assert model.rtg_max_discharge_rate_va.value == 16000
        assert model.rtg_max_wh is not None
        assert model.rtg_max_wh.value == 30000
        assert model.rtg_max_ah is not None
        assert model.rtg_max_ah.value == 120

    def test_storage_ratings_zero_reported_as_is(self) -> None:
        # A key present with 0 is serialized as 0, not omitted.
        model = build_der_capability(
            {"WMaxRtg": 1000, "WDisChaRteMaxRtg": 0, "WhMaxRtg": 0, "AhMaxRtg": 0}
        )
        assert model.rtg_max_discharge_rate_w is not None
        assert model.rtg_max_discharge_rate_w.value == 0
        assert model.rtg_max_wh is not None
        assert model.rtg_max_wh.value == 0
        assert model.rtg_max_ah is not None
        assert model.rtg_max_ah.value == 0

    def test_storage_ratings_omitted_when_missing(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        assert model.rtg_max_discharge_rate_w is None
        assert model.rtg_max_discharge_rate_va is None
        assert model.rtg_max_wh is None
        assert model.rtg_max_ah is None

    def test_wmax_required(self) -> None:
        with pytest.raises(ValueError, match="WMaxRtg is required"):
            build_der_capability({})

    def test_wmax_none_raises(self) -> None:
        with pytest.raises(ValueError, match="WMaxRtg is required"):
            build_der_capability({"WMaxRtg": None})

    def test_der_type_default(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        assert model.type_value.value == 83

    def test_der_type_custom(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000}, der_type=4)
        assert model.type_value.value == 4

    def test_zero_reactive_susceptance(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000, "ReactSusceptRtg": 0})
        assert isinstance(model.rtg_reactive_susceptance, ReactiveSusceptance)
        assert model.rtg_reactive_susceptance.value == 0

    def test_ctrl_modes_default_when_absent(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        assert model.modes_supported.value == b"\x00\x00\x00\x00"

    def test_full_print_demo(self) -> None:
        nameplate = _print_demo_nameplate()
        model = build_der_capability(nameplate)

        assert isinstance(model.rtg_max_w, ActivePower)
        assert isinstance(model.rtg_over_excited_w, ActivePower)
        assert isinstance(model.rtg_over_excited_pf, PowerFactor)
        assert isinstance(model.rtg_under_excited_w, ActivePower)
        assert isinstance(model.rtg_under_excited_pf, PowerFactor)
        assert isinstance(model.rtg_max_va, ApparentPower)
        assert isinstance(model.rtg_max_var, ReactivePower)
        assert isinstance(model.rtg_max_var_neg, ReactivePower)
        assert isinstance(model.rtg_max_charge_rate_w, ActivePower)
        assert isinstance(model.rtg_max_charge_rate_va, ApparentPower)
        assert isinstance(model.rtg_vnom, VoltageRms)
        assert isinstance(model.rtg_max_v, VoltageRms)
        assert isinstance(model.rtg_min_v, VoltageRms)
        assert isinstance(model.rtg_reactive_susceptance, ReactiveSusceptance)
        assert model.rtg_normal_category == 1
        assert model.rtg_abnormal_category == 1


class TestDerCapabilityXml:
    """XML serialization roundtrip and schema validation tests."""

    def test_xml_roundtrip(self) -> None:
        nameplate = _print_demo_nameplate()
        model = build_der_capability(nameplate)
        xml = to_xml(model)
        parsed = from_xml(xml, Dercapability)

        assert parsed.rtg_max_w.value == 15000
        assert parsed.rtg_max_va.value == 15000
        assert parsed.rtg_max_var.value == 4400
        assert parsed.rtg_over_excited_pf.displacement == 8
        assert parsed.rtg_normal_category == 1
        assert parsed.type_value.value == 83

    def test_xml_has_namespace(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        xml = to_xml(model)
        assert NS.encode() in xml

    def test_xml_returns_bytes(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        assert isinstance(to_xml(model), bytes)

    def test_xml_schema_validation_minimal(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        xml = to_xml(model)
        errors = validate_xml(xml)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_xml_schema_validation_full(self) -> None:
        nameplate = _print_demo_nameplate()
        model = build_der_capability(nameplate)
        xml = to_xml(model)
        errors = validate_xml(xml)
        assert errors == [], f"Schema validation errors: {errors}"

    def test_xml_modes_before_ratings(self) -> None:
        """modesSupported should appear before rtg* elements in schema order."""
        model = build_der_capability({"WMaxRtg": 1000, "CtrlModes": 1})
        xml = to_xml(model)
        text = xml.decode("utf-8")
        modes_pos = text.index("modesSupported")
        rtg_pos = text.index("rtgMaxW")
        assert modes_pos < rtg_pos

    def test_xml_omits_none_fields(self) -> None:
        model = build_der_capability({"WMaxRtg": 1000})
        xml = to_xml(model)
        root = fromstring(xml.decode("utf-8"))

        assert root.find(f"{{{NS}}}rtgMaxVA") is None
        assert root.find(f"{{{NS}}}rtgMaxVar") is None
        assert root.find(f"{{{NS}}}rtgMaxW") is not None


class TestCsipAusDoeModesSupported:
    """CSIP-AUS doeModesSupported extension on DERCapability (connector-provided,
    defaults to all four DOE limits, only in csip_aus_mode)."""

    def test_default_all_four_when_not_provided(self):
        cap = build_der_capability({"WMaxRtg": 5000}, csip_aus_mode=True)
        assert len(cap.other_element) == 1
        assert cap.other_element[0].value == b"\x0f"  # exp | imp | gen | load

    def test_connector_provided_value_overrides_default(self):
        cap = build_der_capability({"WMaxRtg": 5000, "DoeModesSupported": 0x0F}, csip_aus_mode=True)
        assert cap.other_element[0].value == b"\x0f"

    def test_omitted_without_csip_aus_mode(self):
        cap = build_der_capability({"WMaxRtg": 5000, "DoeModesSupported": 0x03})
        assert cap.other_element == []

    def test_serializes_with_csipaus_namespace(self):
        cap = build_der_capability({"WMaxRtg": 5000, "DoeModesSupported": 0x0F}, csip_aus_mode=True)
        xml = to_xml(cap, include_csipaus=True)
        assert b"<csipaus:doeModesSupported>0F</csipaus:doeModesSupported>" in xml

    def test_doe_modes_supported_is_last_child(self):
        """The CSIP-AUS extension is appended via xs:extension, so it must be
        the last child (after modesSupported and the rtg* elements). Emitting
        it before the base sequence makes a CSIP-AUS server reject the
        DERCapability PUT with an 'element is not expected' schema error."""
        cap = build_der_capability(
            {"WMaxRtg": 5000, "CtrlModes": 0x03, "DoeModesSupported": 0x0F},
            csip_aus_mode=True,
        )
        root = fromstring(to_xml(cap, include_csipaus=True).decode("utf-8"))
        children = list(root)
        assert children[-1].tag == f"{{{NS_CSIPAUS}}}doeModesSupported"
        tags = [c.tag for c in children]
        assert tags.index(f"{{{NS_CSIPAUS}}}doeModesSupported") > tags.index(
            f"{{{NS}}}modesSupported"
        )


def test_doe_modes_supported_masks_reserved_bits_and_rejects_bool():
    # reserved/out-of-range bits are masked to the 4 defined DOE bits
    cap = build_der_capability({"WMaxRtg": 5000, "DoeModesSupported": 0xFF}, csip_aus_mode=True)
    assert cap.other_element[0].value == b"\x0f"
    # negative can't overflow to_bytes (masked)
    cap = build_der_capability({"WMaxRtg": 5000, "DoeModesSupported": -1}, csip_aus_mode=True)
    assert cap.other_element[0].value == b"\x0f"
    # bool is not a valid bitmap -> falls back to the default (all four)
    cap = build_der_capability({"WMaxRtg": 5000, "DoeModesSupported": True}, csip_aus_mode=True)
    assert cap.other_element[0].value == b"\x0f"
