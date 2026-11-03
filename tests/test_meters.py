"""Tests for meter devices: configuration, the MirrorUsagePoint, energy and the posted sign.

A meter is configured with a role. The role sets the MUP roleFlags (IEEE
2030.5-2023 RoleFlagsType; CSIP-AUS v1.1a Annex A), where the MUP is posted,
and the frame a meter's real power is reported in. The expected roleFlags,
flowDirection, uom and accumulationBehaviour values below are taken from those
documents, not from the code.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from py20305.connectors.config import DeviceConfig
from py20305.telemetry.mup import (
    DeviceRole,
    PowerDirection,
    ReadingProfile,
    compute_sfdi,
    create_meter_reading_list,
    create_mup,
)
from py20305.xml.serialization import to_xml, validate_xml_result

SITE_LFDI = "5e" * 20
METER_LFDI = "3c" * 20
DER_LFDI = "d0" * 20

_DEVICE = TypeAdapter(DeviceConfig)


def _device(**fields: Any) -> Any:
    return _DEVICE.validate_python({"type": "print_demo", "lfdi": METER_LFDI, **fields})


def _slot_mrid(lfdi: str, slot: int) -> bytes:
    """Return the mRID of MUP slot ``slot``, laid out as ``_create_mrid`` documents.

    SFDI (12 digits), the slot (one digit below 10, two from 10), the leading
    LFDI hex (11 or 10 characters), and the IANA PEN 53630 in 8 hex digits.
    """
    sfdi = f"{compute_sfdi(lfdi):012d}"
    if slot < 10:
        return bytes.fromhex(f"{sfdi}{slot:01d}{lfdi[:11]}{53630:08x}")
    return bytes.fromhex(f"{sfdi}{slot:02d}{lfdi[:10]}{53630:08x}")


MONITORING = {
    "W": 1500.0,
    "Var": -300.0,
    "Hz": 50.0,
    "V": 230.0,
    "PF": 0.98,
    "VA": 1530.0,
    "A": 6.5,
}


# -- Configuration ---------------------------------------------------------------------


class TestRoleConfiguration:
    def test_a_device_is_a_der_unless_configured_otherwise(self):
        device = _device()

        assert device.role == "der"
        assert device.revenue_quality is False
        assert device.mirror_under == "site"

    def test_an_unknown_role_is_rejected(self):
        with pytest.raises(ValidationError, match="role"):
            _device(role="submeter")

    def test_mirror_under_is_accepted_on_a_site_meter(self):
        assert _device(role="site_meter", mirror_under="own").mirror_under == "own"

    @pytest.mark.parametrize("role", ["der", "der_meter"])
    def test_mirror_under_on_a_device_that_is_not_a_site_meter_is_rejected(self, role):
        with pytest.raises(ValidationError, match="mirror_under applies to role site_meter"):
            _device(role=role, mirror_under="site")

    def test_mirror_under_is_rejected_on_a_print_device_that_is_not_a_site_meter(self):
        with pytest.raises(ValidationError, match="mirror_under applies to role site_meter"):
            _DEVICE.validate_python(
                {"type": "print_demo", "lfdi": METER_LFDI, "mirror_under": "own"}
            )

    def test_a_print_device_is_not_given_the_role(self):
        device = _DEVICE.validate_python(
            {"type": "print_demo", "lfdi": METER_LFDI, "role": "der_meter"}
        )

        assert device.connector_kwargs() == {}


# -- MirrorUsagePoint -----------------------------------------------------------------


class TestRoleFlags:
    @pytest.mark.parametrize(
        ("role", "revenue_quality", "expected"),
        [
            # isMirror | isDER | isSubmeter
            (DeviceRole.DER, False, b"\x00\x49"),
            (DeviceRole.DER_METER, False, b"\x00\x49"),
            # isMirror | isPremisesAggregationPoint
            (DeviceRole.SITE_METER, False, b"\x00\x03"),
            # ... | isRevenueQuality
            (DeviceRole.SITE_METER, True, b"\x00\x13"),
            (DeviceRole.DER_METER, True, b"\x00\x59"),
        ],
    )
    def test_the_role_sets_the_role_flags(self, role, revenue_quality, expected):
        mup = create_mup(METER_LFDI, MONITORING, 300, role=role, revenue_quality=revenue_quality)

        assert mup.role_flags.value == expected

    def test_a_der_mup_keeps_its_historical_role_flags(self):
        assert create_mup(DER_LFDI, MONITORING, 300).role_flags.value == b"\x00\x49"


class TestPostedUnder:
    def test_the_device_lfdi_is_the_meters_own_by_default(self):
        mup = create_mup(METER_LFDI, MONITORING, 300, role=DeviceRole.SITE_METER)

        assert mup.device_lfdi == bytes.fromhex(METER_LFDI)

    def test_the_device_lfdi_is_the_site_when_mirrored_under_it(self):
        mup = create_mup(
            METER_LFDI, MONITORING, 300, role=DeviceRole.SITE_METER, device_lfdi=SITE_LFDI
        )

        assert mup.device_lfdi == bytes.fromhex(SITE_LFDI)

    def test_mrids_derive_from_the_meters_lfdi_not_the_site(self):
        mup = create_mup(
            METER_LFDI, MONITORING, 300, role=DeviceRole.SITE_METER, device_lfdi=SITE_LFDI
        )

        assert mup.m_rid.value == _slot_mrid(METER_LFDI, 0)
        assert mup.mirror_meter_reading[0].m_rid.value == _slot_mrid(METER_LFDI, 1)

    def test_a_site_meter_under_the_site_shares_no_mrid_with_the_sites_der(self):
        """Single-device mode: the DER's LFDI is the site EndDevice's LFDI."""
        der = create_mup(SITE_LFDI, MONITORING, 300)
        meter = create_mup(
            METER_LFDI, MONITORING, 300, role=DeviceRole.SITE_METER, device_lfdi=SITE_LFDI
        )

        def mrids(mup: Any) -> set[bytes]:
            return {mup.m_rid.value} | {mmr.m_rid.value for mmr in mup.mirror_meter_reading}

        assert der.device_lfdi == meter.device_lfdi
        assert not mrids(der) & mrids(meter)

    def test_a_site_meter_mup_validates_against_the_schema(self):
        mup = create_mup(
            METER_LFDI,
            {**MONITORING, "WhFwd": 1000, "WhRev": 20},
            300,
            role=DeviceRole.SITE_METER,
            revenue_quality=True,
            device_lfdi=SITE_LFDI,
        )

        valid, error = validate_xml_result(to_xml(mup))

        assert valid, error


# -- Energy readings ------------------------------------------------------------------


ENERGY = {**MONITORING, "WhFwd": 123456.7, "WhRev": 890.2}


def _by_mrid(mmrs: Any) -> dict[bytes, Any]:
    return {mmr.m_rid.value: mmr for mmr in mmrs}


class TestEnergyReadings:
    @pytest.mark.parametrize(("slot", "key", "flow"), [(61, "WhFwd", 1), (62, "WhRev", 19)])
    def test_an_energy_total_is_a_summation_in_wh_with_a_fixed_direction(self, slot, key, flow):
        mup = create_mup(METER_LFDI, ENERGY, 300, role=DeviceRole.SITE_METER)

        reading_type = _by_mrid(mup.mirror_meter_reading)[_slot_mrid(METER_LFDI, slot)].reading_type
        assert reading_type.uom.value == 72
        assert reading_type.accumulation_behaviour.value == 9
        assert reading_type.flow_direction.value == flow
        assert reading_type.kind.value == 12
        assert reading_type.power_of_ten_multiplier.value == 0

    def test_energy_totals_are_posted_as_whole_watt_hours(self):
        readings = create_meter_reading_list(METER_LFDI, ENERGY, timestamp=1_800_000_000)

        mmrs = _by_mrid(readings.mirror_meter_reading)
        assert mmrs[_slot_mrid(METER_LFDI, 61)].reading.value == 123456
        assert mmrs[_slot_mrid(METER_LFDI, 62)].reading.value == 890

    @pytest.mark.parametrize("direction", list(PowerDirection))
    def test_energy_slots_do_not_collide_with_power_slots(self, direction):
        mup = create_mup(METER_LFDI, {**ENERGY, "ACType": 2}, 300, power_direction=direction)

        mrids = [mmr.m_rid.value for mmr in mup.mirror_meter_reading]
        assert len(mrids) == len(set(mrids))
        assert _slot_mrid(METER_LFDI, 61) in mrids
        assert _slot_mrid(METER_LFDI, 62) in mrids

    def test_no_energy_reading_is_registered_for_a_device_that_supplies_none(self):
        mup = create_mup(DER_LFDI, MONITORING, 300)

        mrids = {mmr.m_rid.value for mmr in mup.mirror_meter_reading}
        assert _slot_mrid(DER_LFDI, 61) not in mrids
        assert _slot_mrid(DER_LFDI, 62) not in mrids


# -- Posted sign ----------------------------------------------------------------------

#: mRID -> slot for every slot a MUP can register.
_SLOT_BY_MRID = {_slot_mrid(METER_LFDI, slot): slot for slot in range(1, 63)}

#: A meter reporting 2000 W and 100 var in its role's frame, then the same
#: reversed. A site meter reports import positive; a DER meter reports the DER's
#: output positive.
_FRAME = {"Hz": 50.0, "V": 230.0, "PF": 0.95, "VA": 2002.5, "A": 8.7}
POSITIVE = {**_FRAME, "W": 2000.0, "Var": 100.0}
NEGATIVE = {**_FRAME, "W": -2000.0, "Var": -100.0}


SITE_IMPORTING = POSITIVE
SITE_EXPORTING = NEGATIVE


def _posted(monitoring: dict[str, Any], **kwargs: Any) -> dict[int, int]:
    """Return the posted reading values by slot."""
    readings = create_meter_reading_list(METER_LFDI, monitoring, timestamp=0, **kwargs)
    return {_SLOT_BY_MRID[m.m_rid.value]: m.reading.value for m in readings.mirror_meter_reading}


class TestSiteMeterPostedSign:
    """A site meter's import is posted as import under every encoding.

    IEEE 2030.5 FlowDirectionType 1 (Forward) is energy delivered to the
    premises, and 4 (Net) is forward minus reverse. The load convention posts
    export negative. So for a site meter, import is Forward, positive Net and
    positive under SIGNED_LOAD_CONVENTION.
    """

    def test_import_is_posted_positive_under_net(self):
        posted = _posted(
            SITE_IMPORTING,
            power_direction=PowerDirection.NET,
            role=DeviceRole.SITE_METER,
        )

        assert (posted[31], posted[32]) == (2000, 100)

    def test_export_is_posted_negative_under_net(self):
        posted = _posted(
            SITE_EXPORTING,
            power_direction=PowerDirection.NET,
            role=DeviceRole.SITE_METER,
        )

        assert (posted[31], posted[32]) == (-2000, -100)

    def test_import_is_posted_forward_under_split(self):
        posted = _posted(
            SITE_IMPORTING,
            power_direction=PowerDirection.SPLIT,
            role=DeviceRole.SITE_METER,
        )

        assert (posted[41], posted[42]) == (2000, 100), "Forward W and var"
        assert (posted[51], posted[52]) == (0, 0), "Reverse W and var"

    def test_export_is_posted_reverse_under_split(self):
        posted = _posted(
            SITE_EXPORTING,
            power_direction=PowerDirection.SPLIT,
            role=DeviceRole.SITE_METER,
        )

        assert (posted[41], posted[42]) == (0, 0), "Forward W and var"
        assert (posted[51], posted[52]) == (2000, 100), "Reverse W and var"

    def test_import_is_posted_positive_under_the_signed_load_profile(self):
        posted = _posted(
            SITE_IMPORTING,
            profile=ReadingProfile.SIGNED_LOAD_CONVENTION,
            role=DeviceRole.SITE_METER,
        )

        assert (posted[1], posted[2]) == (2000, 100), "W and var"
        assert posted[5] == 950, "PF takes the sign of the posted var"

    def test_export_is_posted_negative_under_the_signed_load_profile(self):
        posted = _posted(
            SITE_EXPORTING,
            profile=ReadingProfile.SIGNED_LOAD_CONVENTION,
            role=DeviceRole.SITE_METER,
        )

        assert (posted[1], posted[2]) == (-2000, -100), "W and var"
        assert posted[5] == -950, "PF takes the sign of the posted var"

    def test_a_der_meters_output_is_posted_negative_under_the_signed_load_profile(self):
        """A DER meter's export is the DER's output, which the load convention negates."""
        monitoring = dict(POSITIVE)

        posted = _posted(
            monitoring,
            profile=ReadingProfile.SIGNED_LOAD_CONVENTION,
            role=DeviceRole.DER_METER,
        )

        assert (posted[1], posted[2]) == (-2000, -100), "W and var"


