"""Tests for MirrorUsagePoint creation."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from py20305.telemetry.mup import (
    DEFAULT_PEN,
    READING_TYPE_SPECS,
    ROLE_FLAGS_DER,
    SERVICE_CATEGORY_ELECTRICITY,
    STATUS_ON,
    _create_mrid,
    compute_sfdi,
    create_meter_reading_list,
    create_mup,
)
from py20305.telemetry.scaling import (
    FLOW_NORMAL,
    FLOW_REVERSE,
)


def _per_line_monitoring(ac_type: int, lines: int, base: dict) -> dict:
    """Build a monitoring-data dict with system readings plus
    ``lines``-worth of per-line points (L1..L{lines}).
    """
    data = dict(base)
    data["ACType"] = ac_type
    for line in range(1, lines + 1):
        offset = 10 * line
        data[f"WL{line}"] = 100.0 + offset
        data[f"VarL{line}"] = -20.0 + offset
        data[f"VL{line}"] = 240.0 + line  # 241, 242, 243
        data[f"PFL{line}"] = 0.95 + (line * 0.01)
        data[f"VAL{line}"] = 110.0 + offset
        data[f"AL{line}"] = 4.5 + line
    return data


# Sample LFDI for testing (40 hex characters)
SAMPLE_LFDI = "1234567890abcdef1234567890abcdef12345678"


@pytest.fixture
def sample_monitoring_data() -> dict:
    """Sample monitoring data from a connector."""
    return {
        "W": 1000.0,
        "Var": -200.0,
        "Hz": 60.0,
        "V": 240.0,
        "PF": 0.98,
        "VA": 1020.0,
        "A": 4.25,
    }


@pytest.fixture
def zero_monitoring_data() -> dict:
    """Monitoring data with all zeros."""
    return {
        "W": 0.0,
        "Var": 0.0,
        "Hz": 0.0,
        "V": 0.0,
        "PF": 0.0,
        "VA": 0.0,
        "A": 0.0,
    }


class TestComputeSfdi:
    """Tests for SFDI calculation from LFDI."""

    def test_basic_computation(self):
        sfdi = compute_sfdi(SAMPLE_LFDI)
        assert isinstance(sfdi, int)
        # SFDI should be a positive integer
        assert sfdi > 0

    def test_luhn_check_digit(self):
        # First 9 hex chars of SAMPLE_LFDI: "123456789"
        # int("123456789", 16) = 4886718345
        # Sum of digits: 4+8+8+6+7+1+8+3+4+5 = 54
        # Check: (10 - 54%10) % 10 = (10 - 4) % 10 = 6
        sfdi = compute_sfdi(SAMPLE_LFDI)
        sfdi_str = str(sfdi)
        assert sfdi_str[-1] == "6"

    def test_case_insensitive(self):
        upper = compute_sfdi(SAMPLE_LFDI.upper())
        lower = compute_sfdi(SAMPLE_LFDI.lower())
        assert upper == lower


class TestCreateMup:
    """Tests for MirrorUsagePoint creation."""

    def test_has_7_meter_readings(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        assert len(mup.mirror_meter_reading) == 7

    def test_mup_has_required_fields(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)

        assert mup.m_rid is not None
        assert mup.description is not None
        assert mup.description.startswith("DER ")
        assert mup.version.value == 1
        # CSIP-AUS: DER roleFlags SHALL be 0x49 = isMirror|isDER|isSubmeter.
        assert ROLE_FLAGS_DER == 0x49
        assert mup.role_flags.value == b"\x00\x49"
        assert mup.service_category_kind.value == SERVICE_CATEGORY_ELECTRICITY
        assert mup.status == STATUS_ON
        assert mup.post_rate == 300

    def test_device_lfdi_stored_as_bytes(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        # deviceLFDI should be bytes
        assert isinstance(mup.device_lfdi, bytes)
        # Should match the LFDI (lowercase normalized)
        assert mup.device_lfdi.hex().lower() == SAMPLE_LFDI.lower()

    def test_lfdi_case_normalized(self, sample_monitoring_data):
        mup_upper = create_mup(SAMPLE_LFDI.upper(), sample_monitoring_data, post_rate=300)
        mup_lower = create_mup(SAMPLE_LFDI.lower(), sample_monitoring_data, post_rate=300)
        # Both should produce the same device_lfdi
        assert mup_upper.device_lfdi == mup_lower.device_lfdi

    def test_meter_readings_have_mrid(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        for mmr in mup.mirror_meter_reading:
            assert mmr.m_rid is not None
            assert mmr.m_rid.value is not None
            # mRID should be 16 bytes (128 bits)
            assert len(mmr.m_rid.value) == 16

    def test_meter_readings_have_descriptions(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        descriptions = [mmr.description for mmr in mup.mirror_meter_reading]
        expected = [
            "Real Power",
            "Reactive Power",
            "Frequency",
            "Voltage",
            "Power Factor",
            "Apparent Power",
            "Current",
        ]
        assert descriptions == expected

    def test_meter_readings_have_reading_types(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        for mmr in mup.mirror_meter_reading:
            assert mmr.reading_type is not None

    def test_reading_types_match_spec(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)

        # W reading type
        rt_w = mup.mirror_meter_reading[0].reading_type
        assert rt_w.commodity.value == 1
        assert rt_w.data_qualifier.value == 12
        assert rt_w.kind.value == 37
        assert rt_w.uom.value == 38
        assert rt_w.power_of_ten_multiplier.value == 0

        # Var reading type
        rt_var = mup.mirror_meter_reading[1].reading_type
        assert rt_var.uom.value == 63

        # Hz reading type
        rt_hz = mup.mirror_meter_reading[2].reading_type
        assert rt_hz.uom.value == 33
        assert rt_hz.power_of_ten_multiplier.value == -3

        # V reading type (unique: average; no phase tag because the
        # SunSpec source is LLV, a line-to-line aggregate, not phase-
        # specific). PhaseCode is omitted; an absent attribute is
        # equivalent to PhaseCode=0 ("Not Applicable") per the XSD.
        rt_v = mup.mirror_meter_reading[3].reading_type
        assert rt_v.data_qualifier.value == 2  # average
        assert rt_v.uom.value == 29
        assert rt_v.power_of_ten_multiplier.value == -1
        assert rt_v.phase is None

        # PF reading type
        rt_pf = mup.mirror_meter_reading[4].reading_type
        assert rt_pf.kind.value == 0  # PF uses kind=0
        assert rt_pf.uom.value == 65
        assert rt_pf.power_of_ten_multiplier.value == -3

        # VA reading type
        rt_va = mup.mirror_meter_reading[5].reading_type
        assert rt_va.uom.value == 61

        # A reading type
        rt_a = mup.mirror_meter_reading[6].reading_type
        assert rt_a.uom.value == 5
        assert rt_a.power_of_ten_multiplier.value == -1

    def test_voltage_phase_override_sets_phase_code(self, sample_monitoring_data):
        """A connector can set the system voltage's phase code. CSIP-AUS rejects
        NOT_APPLICABLE (0) for voltage; print_demo tags it A-N (129)."""
        from py20305.connectors.base import ReadingOverride

        mup = create_mup(
            SAMPLE_LFDI,
            sample_monitoring_data,
            post_rate=300,
            overrides={"V": ReadingOverride(phase=129)},
        )
        rt_v = mup.mirror_meter_reading[3].reading_type
        assert rt_v.uom.value == 29
        assert rt_v.phase.value == 129

    def test_every_reading_type_has_instantaneous_accumulation_behaviour(
        self, sample_monitoring_data
    ):
        # Every published ReadingType must carry accumulationBehaviour=12
        # (Instantaneous) -- all our readings are point-in-time samples.
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        assert mup.mirror_meter_reading  # sanity: readings exist
        for mmr in mup.mirror_meter_reading:
            assert mmr.reading_type.accumulation_behaviour is not None
            assert mmr.reading_type.accumulation_behaviour.value == 12

    def test_negative_var_sets_reverse_flow(self, sample_monitoring_data):
        # sample_monitoring_data has Var=-200
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        rt_var = mup.mirror_meter_reading[1].reading_type
        assert rt_var.flow_direction.value == FLOW_REVERSE

    def test_positive_w_sets_normal_flow(self, sample_monitoring_data):
        # sample_monitoring_data has W=1000
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        rt_w = mup.mirror_meter_reading[0].reading_type
        assert rt_w.flow_direction.value == FLOW_NORMAL


class TestCreateMeterReadingList:
    """Tests for MirrorMeterReadingList creation."""

    def test_has_7_readings(self, sample_monitoring_data):
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)
        assert len(mrl.mirror_meter_reading) == 7

    def test_omits_readings_for_absent_keys(self):
        # A connector that supplies only real power posts only a W reading --
        # the other quantities are omitted, not zero-filled.
        mrl = create_meter_reading_list(SAMPLE_LFDI, {"W": 1500.0})
        assert len(mrl.mirror_meter_reading) == 1
        assert mrl.mirror_meter_reading[0].description == "Real Power"
        assert mrl.mirror_meter_reading[0].reading.value == 1500
        assert mrl.all == 1 and mrl.results == 1

    def test_omits_readings_for_none_values(self):
        # Present-but-None (e.g. a transient read failure) is omitted too, so it
        # doesn't post a misleading 0.
        mrl = create_meter_reading_list(SAMPLE_LFDI, {"W": 1000.0, "V": None, "Hz": 60.0})
        descs = {mmr.description for mmr in mrl.mirror_meter_reading}
        assert descs == {"Real Power", "Frequency"}

    def test_mup_still_registers_full_set_when_readings_omitted(self):
        # create_mup registers the full ReadingType set regardless of which
        # values are present, so omitted readings stay registered (compliant
        # subset posting; mRIDs are slot-stable).
        mup = create_mup(SAMPLE_LFDI, {"W": 1500.0}, post_rate=300)
        assert len(mup.mirror_meter_reading) == 7

    def test_readings_have_values(self, sample_monitoring_data):
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)
        for mmr in mrl.mirror_meter_reading:
            assert mmr.reading is not None
            assert mmr.reading.value is not None

    def test_readings_have_timestamps(self, sample_monitoring_data):
        timestamp = 1700000000
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data, timestamp=timestamp)
        for mmr in mrl.mirror_meter_reading:
            assert mmr.reading.time_period is not None
            assert mmr.reading.time_period.start.value == timestamp

    def test_duration_matches_reading_type(self, sample_monitoring_data):
        """Interval readings (Average, e.g. Voltage) carry duration = postRate
        (CSIP-AUS: the averaging window SHALL match the MUP postRate);
        instantaneous readings (dataQualifier 12) keep duration 0."""
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data, post_rate=60)
        by_desc = {
            mmr.description: mmr.reading.time_period.duration for mmr in mrl.mirror_meter_reading
        }
        # Voltage is the one Average (dataQualifier=2) reading -> duration = postRate.
        assert by_desc["Voltage"] == 60
        # The instantaneous readings stay at 0.
        for desc in ("Real Power", "Reactive Power", "Frequency", "Current"):
            assert by_desc[desc] == 0

    def test_readings_carry_valid_quality_flag(self, sample_monitoring_data):
        # Every reading sets qualityFlags with bit 0 (valid) set.
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)
        assert mrl.mirror_meter_reading
        for mmr in mrl.mirror_meter_reading:
            assert mmr.reading.quality_flags == b"\x00\x01"
            assert int.from_bytes(mmr.reading.quality_flags, "big") & 0x01

    def test_scaled_values(self, sample_monitoring_data):
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)

        # W: no scaling, value=1000
        assert mrl.mirror_meter_reading[0].reading.value == 1000

        # Var: no scaling, abs(-200)=200
        assert mrl.mirror_meter_reading[1].reading.value == 200

        # Hz: x1000, 60*1000=60000
        assert mrl.mirror_meter_reading[2].reading.value == 60000

        # V: x10, 240*10=2400
        assert mrl.mirror_meter_reading[3].reading.value == 2400

        # PF: x1000, 0.98*1000=980
        assert mrl.mirror_meter_reading[4].reading.value == 980

        # VA: no scaling, value=1020
        assert mrl.mirror_meter_reading[5].reading.value == 1020

        # A: x10, 4.25*10=42 (truncated)
        assert mrl.mirror_meter_reading[6].reading.value == 42

    def test_list_counts_set(self, sample_monitoring_data):
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)
        assert mrl.all == 7
        assert mrl.results == 7

    def test_default_timestamp_is_current(self, sample_monitoring_data):
        before = int(time.time())
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)
        after = int(time.time())

        ts = mrl.mirror_meter_reading[0].reading.time_period.start.value
        assert before <= ts <= after

    def test_reading_type_excluded(self, sample_monitoring_data):
        """IEEE 10.11.3 rule n): ReadingType excluded from subsequent POSTs."""
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)
        for mmr in mrl.mirror_meter_reading:
            assert mmr.reading_type is None


class TestReadingTypeSpecs:
    """Tests for the reading type specifications."""

    def test_spec_count(self):
        assert len(READING_TYPE_SPECS) == 7

    def test_all_keys_present(self):
        keys = {spec.key for spec in READING_TYPE_SPECS}
        expected = {"W", "Var", "Hz", "V", "PF", "VA", "A"}
        assert keys == expected

    def test_all_specs_have_scale_func(self):
        for spec in READING_TYPE_SPECS:
            assert spec.scale_func is not None
            assert callable(spec.scale_func)


class TestMridGeneration:
    """Tests for mRID generation."""

    def test_mrid_is_16_bytes(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        assert len(mup.m_rid.value) == 16

    def test_mrids_are_unique(self, sample_monitoring_data):
        """Each meter reading should have a unique mRID."""
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        mrids = [mmr.m_rid.value for mmr in mup.mirror_meter_reading]
        mrids.append(mup.m_rid.value)
        # All should be unique
        assert len(mrids) == len(set(mrids))

    @patch("py20305.telemetry.mup.time.time")
    def test_mrid_contains_pen(self, mock_time, sample_monitoring_data):
        mock_time.return_value = 1700000000.0
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        mrid_hex = mup.m_rid.value.hex()
        # Last 8 hex chars should be the PEN
        pen_hex = mrid_hex[-8:]
        assert int(pen_hex, 16) == DEFAULT_PEN


class TestMridStability:
    """IEEE 2030.5-2023 §10.11.3 Rule h: the server matches readings POSTs
    to existing MeterReadings by mRID. Rule n further says ReadingType
    SHOULD NOT be included in subsequent POSTs. So an aggregator that
    re-generates mRIDs each cycle looks like "all-new mRIDs, no
    ReadingType" to the server, which falls under Rule h.3 (400 Bad
    Request). A real third-party (Envoy) was rejecting our reading POSTs
    with::

        "MirrorMeterReading <mrid> has no readingType and doesn't match a
         prior MirrorMeterReading"

    These tests pin down that the same logical reading produces the same
    mRID across calls, regardless of wall-clock time.
    """

    def test_create_mup_mrids_stable_across_calls(self, sample_monitoring_data):
        """Calling ``create_mup`` twice for the same LFDI yields the same
        MUP mRID and the same set of MMR mRIDs."""
        mup_a = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        mup_b = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)

        assert mup_a.m_rid.value == mup_b.m_rid.value
        a_mmr_mrids = [m.m_rid.value for m in mup_a.mirror_meter_reading]
        b_mmr_mrids = [m.m_rid.value for m in mup_b.mirror_meter_reading]
        assert a_mmr_mrids == b_mmr_mrids

    def test_create_meter_reading_list_mrids_stable_across_calls(self, sample_monitoring_data):
        """Calling ``create_meter_reading_list`` twice for the same LFDI
        yields identical MMR mRIDs in identical order. This is the
        subsequent-POST path: each metering cycle must re-use the same
        identifiers so the server matches per Rule h.1."""
        a = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)
        b = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)

        a_mrids = [m.m_rid.value for m in a.mirror_meter_reading]
        b_mrids = [m.m_rid.value for m in b.mirror_meter_reading]
        assert a_mrids == b_mrids

    def test_mup_post_and_readings_post_share_mrids(self, sample_monitoring_data):
        """The MMR mRIDs in the initial ``create_mup`` body must exactly
        match the mRIDs in subsequent ``create_meter_reading_list`` bodies.
        This is the cross-call invariant that the previous timestamp-baked
        implementation violated -- and that Envoy correctly flagged with
        ``no readingType and doesn't match a prior MirrorMeterReading``.
        """
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data)

        mup_mrids = [m.m_rid.value for m in mup.mirror_meter_reading]
        mrl_mrids = [m.m_rid.value for m in mrl.mirror_meter_reading]
        assert mup_mrids == mrl_mrids

    def test_different_lfdis_produce_different_mrids(self, sample_monitoring_data):
        """Different devices must still get distinct mRIDs -- otherwise
        two devices on the same server would collide in the MUP list."""
        other_lfdi = "FFEEDDCC" + SAMPLE_LFDI[8:]
        a = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        b = create_mup(other_lfdi, sample_monitoring_data, post_rate=300)

        assert a.m_rid.value != b.m_rid.value
        a_mmrs = {m.m_rid.value for m in a.mirror_meter_reading}
        b_mmrs = {m.m_rid.value for m in b.mirror_meter_reading}
        assert a_mmrs.isdisjoint(b_mmrs)

    def test_lfdi_case_does_not_affect_mrid(self, sample_monitoring_data):
        """LFDI is case-insensitive (hex). Upper- and lower-case forms of
        the same LFDI must yield the same mRIDs so a device that posts
        with upper-case once and lower-case later isn't seen as a new
        identity."""
        upper = create_meter_reading_list(SAMPLE_LFDI.upper(), sample_monitoring_data)
        lower = create_meter_reading_list(SAMPLE_LFDI.lower(), sample_monitoring_data)

        upper_mrids = [m.m_rid.value for m in upper.mirror_meter_reading]
        lower_mrids = [m.m_rid.value for m in lower.mirror_meter_reading]
        assert upper_mrids == lower_mrids

    def test_system_mrids_unchanged_when_per_line_added(self, sample_monitoring_data):
        """The first eight mRIDs (MUP + the seven system readings) must
        be byte-identical regardless of whether per-line readings get
        appended. This is the compatibility pin: a server that has
        already stored MeterReadings under the legacy 7-reading layout
        must keep matching them after the upgrade."""
        three_phase = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        legacy = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        widened = create_mup(SAMPLE_LFDI, three_phase, post_rate=300)

        assert legacy.m_rid.value == widened.m_rid.value
        legacy_mrids = [m.m_rid.value for m in legacy.mirror_meter_reading]
        widened_mrids = [m.m_rid.value for m in widened.mirror_meter_reading[: len(legacy_mrids)]]
        assert legacy_mrids == widened_mrids


class TestPerLineReadings:
    """Tests for ACType-driven per-line MirrorMeterReading emission.

    System readings (W/Var/Hz/V/PF/VA/A) are always emitted. Per-line
    readings appear only on multi-phase devices, with one block per
    line INCLUDING L1 -- the system readings are aggregates (totals, and
    a line-to-line average voltage), not phase-A values. Single-phase
    emits no per-line block (there the system readings really are L1).
    Hz is grid-wide and stays system-only -- per-line specs have 6
    metrics, not 7.
    """

    def test_single_phase_emits_only_system_readings(self, sample_monitoring_data):
        data = dict(sample_monitoring_data)
        data["ACType"] = 0  # SINGLE_PHASE
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        assert len(mup.mirror_meter_reading) == 7

    def test_missing_actype_falls_back_to_system_only(self, sample_monitoring_data):
        """Connectors that don't surface ACType (anything other than
        SunSpec, today) must keep the historical 7-reading behaviour."""
        assert "ACType" not in sample_monitoring_data
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300)
        assert len(mup.mirror_meter_reading) == 7

    def test_split_phase_appends_l1_and_l2_blocks(self, sample_monitoring_data):
        data = _per_line_monitoring(ac_type=1, lines=2, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        # 7 system + 6 L1 + 6 L2 = 19
        assert len(mup.mirror_meter_reading) == 19
        descriptions = [m.description for m in mup.mirror_meter_reading]
        assert descriptions[:7] == [
            "Real Power",
            "Reactive Power",
            "Frequency",
            "Voltage",
            "Power Factor",
            "Apparent Power",
            "Current",
        ]
        assert descriptions[7:13] == [
            "Real Power L1",
            "Reactive Power L1",
            "Voltage L1",
            "Power Factor L1",
            "Apparent Power L1",
            "Current L1",
        ]
        assert descriptions[13:] == [
            "Real Power L2",
            "Reactive Power L2",
            "Voltage L2",
            "Power Factor L2",
            "Apparent Power L2",
            "Current L2",
        ]

    def test_three_phase_appends_l1_l2_and_l3_blocks(self, sample_monitoring_data):
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        # 7 system + 6 L1 + 6 L2 + 6 L3 = 25
        assert len(mup.mirror_meter_reading) == 25

    def test_per_line_voltage_uses_neutral_phase_code(self, sample_monitoring_data):
        """Per-line voltage uses the phase-to-neutral PhaseCode (neutral
        bit set): L1=129, L2=65, L3=33."""
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        by_desc = {m.description: m for m in mup.mirror_meter_reading}
        assert by_desc["Voltage L1"].reading_type.phase.value == 129
        assert by_desc["Voltage L2"].reading_type.phase.value == 65
        assert by_desc["Voltage L3"].reading_type.phase.value == 33

    def test_per_line_non_voltage_uses_phase_only_code(self, sample_monitoring_data):
        """Real/reactive/apparent power, PF, and current per line use
        the phase-only PhaseCode (no neutral bit): L1=128, L2=64, L3=32."""
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        by_desc = {m.description: m for m in mup.mirror_meter_reading}
        for non_v in (
            "Real Power L1",
            "Reactive Power L1",
            "Power Factor L1",
            "Apparent Power L1",
            "Current L1",
        ):
            assert by_desc[non_v].reading_type.phase.value == 128, non_v
        for non_v in (
            "Real Power L2",
            "Reactive Power L2",
            "Power Factor L2",
            "Apparent Power L2",
            "Current L2",
        ):
            assert by_desc[non_v].reading_type.phase.value == 64, non_v
        for non_v in (
            "Real Power L3",
            "Reactive Power L3",
            "Power Factor L3",
            "Apparent Power L3",
            "Current L3",
        ):
            assert by_desc[non_v].reading_type.phase.value == 32, non_v

    def test_per_line_values_round_trip(self, sample_monitoring_data):
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mrl = create_meter_reading_list(SAMPLE_LFDI, data)
        by_desc = {m.description: m for m in mrl.mirror_meter_reading}
        # WL2 = 100 + 20 = 120, scaled 1:1.
        assert by_desc["Real Power L2"].reading.value == 120
        # VL3 = 243, scaled x10 = 2430.
        assert by_desc["Voltage L3"].reading.value == 2430

    def test_per_line_mrids_unique_across_lines(self, sample_monitoring_data):
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        mrids = [m.m_rid.value for m in mup.mirror_meter_reading]
        mrids.append(mup.m_rid.value)
        assert len(mrids) == len(set(mrids))

    def test_per_line_mrids_stable_across_calls(self, sample_monitoring_data):
        """Per-line mRIDs must stay stable across cycles, just like
        system mRIDs, so the server keeps matching them via Rule h.1."""
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        a = create_meter_reading_list(SAMPLE_LFDI, data)
        b = create_meter_reading_list(SAMPLE_LFDI, data)
        assert [m.m_rid.value for m in a.mirror_meter_reading] == [
            m.m_rid.value for m in b.mirror_meter_reading
        ]

    def test_mup_post_and_readings_post_share_per_line_mrids(self, sample_monitoring_data):
        """Per-line mRIDs in the initial MUP body must match the
        per-line mRIDs in subsequent reading-list bodies."""
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        mrl = create_meter_reading_list(SAMPLE_LFDI, data)
        mup_mrids = [m.m_rid.value for m in mup.mirror_meter_reading]
        mrl_mrids = [m.m_rid.value for m in mrl.mirror_meter_reading]
        assert mup_mrids == mrl_mrids

    def test_l1_block_present_with_phase_a_codes(self, sample_monitoring_data):
        """Regression: L1 was silently dropped from the MUP.

        ``_all_specs_for`` used to start the per-line loop at L2 on the
        assumption that the system readings already represent L1. They don't
        on a multi-phase device -- W/Var/VA/A are totals and V is a
        line-to-line AVERAGE with no PhaseCode -- so a consumer keying on
        PhaseCode 129 (Phase A-N, the L1-N voltage some utilities use as an
        aggregator liveness heartbeat) saw nothing at all.
        """
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        by_desc = {m.description: m for m in mup.mirror_meter_reading}

        for desc in (
            "Real Power L1",
            "Reactive Power L1",
            "Voltage L1",
            "Power Factor L1",
            "Apparent Power L1",
            "Current L1",
        ):
            assert desc in by_desc, f"{desc} missing from the MUP"

        # The L1-N voltage specifically: PhaseCode 129 per IEEE 2030.5.
        assert by_desc["Voltage L1"].reading_type.phase.value == 129
        # ...and it must be a real reading, distinct from the untagged
        # system voltage (LLV aggregate), which carries no phase at all.
        assert by_desc["Voltage"].reading_type.phase is None

    def test_l1_readings_carry_values(self, sample_monitoring_data):
        """L1 values must actually reach the readings POST, not just the MUP
        registration -- a stale/absent L1 was the reported symptom."""
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mrl = create_meter_reading_list(SAMPLE_LFDI, data)
        by_desc = {m.description: m for m in mrl.mirror_meter_reading}
        # WL1 = 100 + 10 = 110, scaled 1:1.
        assert by_desc["Real Power L1"].reading.value == 110
        # VL1 = 241, scaled x10 = 2410.
        assert by_desc["Voltage L1"].reading.value == 2410

    def test_l1_mrids_use_reserved_slots_and_dont_disturb_l2_l3(self, sample_monitoring_data):
        """L1 occupies the previously-unused slots 10..15, so adding it is
        additive: existing L2/L3 mRIDs are unchanged and keep matching the
        server's MeterReadings per IEEE 2030.5 s10.11.3 Rule h.1."""
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        by_desc = {m.description: m.m_rid.value for m in mup.mirror_meter_reading}

        # Slot index is encoded as the two digits after the 12-digit SFDI.
        def slot(desc: str) -> int:
            return int(by_desc[desc].hex()[12:14])

        assert slot("Real Power L1") == 10
        assert slot("Current L1") == 15
        assert slot("Real Power L2") == 16
        assert slot("Real Power L3") == 22

        # L2/L3 mRIDs must be byte-identical to what a pre-fix aggregator
        # produced -- they derive from (lfdi, slot, pen) only.
        assert by_desc["Real Power L2"] == _create_mrid(SAMPLE_LFDI, 16).value
        assert by_desc["Real Power L3"] == _create_mrid(SAMPLE_LFDI, 22).value

    def test_single_phase_still_emits_no_l1_block(self, sample_monitoring_data):
        """Single-phase is unchanged: the system readings genuinely are L1,
        so a duplicate block would only bloat the MUP."""
        data = _per_line_monitoring(ac_type=0, lines=1, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        assert len(mup.mirror_meter_reading) == 7
        assert not any(m.description.endswith("L1") for m in mup.mirror_meter_reading)

    def test_wider_mrids_are_still_16_bytes(self, sample_monitoring_data):
        """Per-line indices use a 2-digit / 10-char-salt encoding; the
        wire format must still be exactly 128 bits (16 bytes)."""
        data = _per_line_monitoring(ac_type=2, lines=3, base=sample_monitoring_data)
        mup = create_mup(SAMPLE_LFDI, data, post_rate=300)
        for m in mup.mirror_meter_reading:
            assert len(m.m_rid.value) == 16


class TestReadingOverrides:
    """Connector-supplied per-quantity ReadingType / qualityFlags overrides."""

    def test_field_overrides_applied_to_reading_type(self, sample_monitoring_data):
        from py20305.connectors.base import ReadingOverride

        overrides = {
            "W": ReadingOverride(
                data_qualifier=8, accumulation_behaviour=9, kind=12, uom=72, multiplier=3
            )
        }
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300, overrides=overrides)
        rt_w = mup.mirror_meter_reading[0].reading_type
        assert rt_w.data_qualifier.value == 8
        assert rt_w.accumulation_behaviour.value == 9
        assert rt_w.kind.value == 12
        assert rt_w.uom.value == 72
        assert rt_w.power_of_ten_multiplier.value == 3

    def test_negative_multiplier_override_accepted(self, sample_monitoring_data):
        from py20305.connectors.base import ReadingOverride

        # powerOfTenMultiplier is signed (IEEE 2030.5: -9..9); a negative override
        # is valid (the common case -- the built-in Hz/V specs use -3/-1).
        ov = {"W": ReadingOverride(multiplier=-3)}
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300, overrides=ov)
        assert mup.mirror_meter_reading[0].reading_type.power_of_ten_multiplier.value == -3

    def test_out_of_range_multiplier_falls_back(self, sample_monitoring_data):
        from py20305.connectors.base import ReadingOverride

        ov = {"W": ReadingOverride(multiplier=42)}  # outside the -9..9 range
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300, overrides=ov)
        assert (
            mup.mirror_meter_reading[0].reading_type.power_of_ten_multiplier.value == 0
        )  # default

    def test_unlisted_keys_and_fields_keep_defaults(self, sample_monitoring_data):
        from py20305.connectors.base import ReadingOverride

        overrides = {"W": ReadingOverride(data_qualifier=8)}
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300, overrides=overrides)
        rt_w = mup.mirror_meter_reading[0].reading_type
        # only the named field changed; the rest of W keeps defaults
        assert rt_w.data_qualifier.value == 8
        assert rt_w.accumulation_behaviour.value == 12
        assert rt_w.uom.value == 38
        # an unlisted key (Var) is entirely default
        rt_var = mup.mirror_meter_reading[1].reading_type
        assert rt_var.data_qualifier.value == 12

    def test_static_quality_flags_override(self, sample_monitoring_data):
        from py20305.connectors.base import ReadingOverride

        overrides = {"W": ReadingOverride(quality_flags=0x20)}  # derived (bit 5)
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data, overrides=overrides)
        assert mrl.mirror_meter_reading[0].reading.quality_flags == b"\x00\x20"
        # other readings keep the default valid flag
        assert mrl.mirror_meter_reading[1].reading.quality_flags == b"\x00\x01"

    def test_invalid_field_override_falls_back(self, sample_monitoring_data):
        from py20305.connectors.base import ReadingOverride

        overrides = {"W": ReadingOverride(data_qualifier=-5, kind=True)}  # invalid: negative, bool
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300, overrides=overrides)
        rt_w = mup.mirror_meter_reading[0].reading_type
        assert rt_w.data_qualifier.value == 12  # default kept
        assert rt_w.kind.value == 37  # default kept

    def test_invalid_quality_flags_falls_back(self, sample_monitoring_data):
        from py20305.connectors.base import ReadingOverride

        overrides = {"W": ReadingOverride(quality_flags=999999)}  # out of 16-bit range
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data, overrides=overrides)
        assert mrl.mirror_meter_reading[0].reading.quality_flags == b"\x00\x01"

    def test_no_overrides_unchanged(self, sample_monitoring_data):
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300, overrides=None)
        assert mup.mirror_meter_reading[0].reading_type.accumulation_behaviour.value == 12
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data, overrides=None)
        assert mrl.mirror_meter_reading[0].reading.quality_flags == b"\x00\x01"

    def test_non_readingoverride_value_falls_back_without_raising(self, sample_monitoring_data):
        # A buggy in-process connector could put a non-ReadingOverride value in
        # the map (the manager only checks the top level is a dict). It must fall
        # back to defaults, not AttributeError out of MUP creation.
        bad = {"W": "not a ReadingOverride"}
        mup = create_mup(SAMPLE_LFDI, sample_monitoring_data, post_rate=300, overrides=bad)
        assert mup.mirror_meter_reading[0].reading_type.data_qualifier.value == 12  # default
        mrl = create_meter_reading_list(SAMPLE_LFDI, sample_monitoring_data, overrides=bad)
        assert mrl.mirror_meter_reading[0].reading.quality_flags == b"\x00\x01"  # default valid

    def test_per_cycle_quality_beats_static_override(self):
        from py20305.connectors.base import ReadingOverride

        # W carries both a static override (derived) and a per-cycle value
        # (questionable) -- the per-cycle value wins for that sample.
        mon = {"W": 1500.0, "W__quality": 0x10, "V": 240.0}
        ov = {"W": ReadingOverride(quality_flags=0x20)}
        mrl = create_meter_reading_list(SAMPLE_LFDI, mon, overrides=ov)
        q = {m.description: m.reading.quality_flags for m in mrl.mirror_meter_reading}
        assert q["Real Power"] == b"\x00\x10"  # per-cycle
        assert q["Voltage"] == b"\x00\x01"  # no quality -> default valid

    def test_per_cycle_quality_without_static(self):
        mon = {"W": 1500.0, "W__quality": 0x10}
        mrl = create_meter_reading_list(SAMPLE_LFDI, mon)
        assert mrl.mirror_meter_reading[0].reading.quality_flags == b"\x00\x10"

    def test_invalid_per_cycle_quality_falls_back_to_static(self):
        from py20305.connectors.base import ReadingOverride

        mon = {"W": 1500.0, "W__quality": 999999}  # out of 16-bit range
        ov = {"W": ReadingOverride(quality_flags=0x20)}
        mrl = create_meter_reading_list(SAMPLE_LFDI, mon, overrides=ov)
        assert mrl.mirror_meter_reading[0].reading.quality_flags == b"\x00\x20"  # static

    def test_quality_key_is_not_posted_as_a_reading(self):
        mon = {"W": 1500.0, "W__quality": 0x10}
        mrl = create_meter_reading_list(SAMPLE_LFDI, mon)
        assert [m.description for m in mrl.mirror_meter_reading] == ["Real Power"]
