"""Tests for the SunSpec connector's conversion to the IEEE 2030.5 connector contract.

The Modbus core is covered by ``test_connectors_sunspec_core.py`` and the wire
by ``tests/scenario``. These tests pin the conversion: readings and their
PhaseCodes, status codes, ratings and settings names, and control parameters.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import py20305
from py20305.connectors.base import BaseConnector
from py20305.connectors.config import SunSpecDeviceConfig
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.connectors.sunspec import _ieee2030_5 as convert
from py20305.connectors.sunspec.connector import SunSpecConnector
from py20305.telemetry.mup import ReadingProfile


def _phases(readings: list[Any]) -> dict[tuple[int, int], float]:
    return {(r.uom, r.phase): r.value for r in readings}


# -- Readings --------------------------------------------------------------------


def test_a_single_phase_inverter_states_its_voltage_and_current_phases() -> None:
    readings = convert.readings(
        {"W": 3000, "V": 240.0, "A": 12.5, "ACType": 0}, line_to_line_voltage=True
    )

    assert _phases(readings) == {(38, 0): 3000, (29, 132): 240.0, (5, 128): 12.5}


def test_a_split_phase_inverter_reports_lines_one_and_two_and_one_pair() -> None:
    values = {
        "V": 240.0,
        "A": 10.0,
        "ACType": 1,
        "VL1": 120.0,
        "VL2": 121.0,
        "VL3": 122.0,
        "VL1L2": 241.0,
        "VL2L3": 242.0,
    }

    readings = convert.readings(values, line_to_line_voltage=True)

    voltages = sorted((r.phase, r.data_qualifier, r.value) for r in readings if r.uom == 29)
    assert voltages == [(65, 2, 121.0), (129, 2, 120.0), (132, 2, 240.0), (132, 12, 241.0)]
    assert _phases(readings)[(5, 132)] == 10.0


def test_a_meters_average_voltage_has_no_phase() -> None:
    readings = convert.readings({"V": 230.0, "ACType": 2}, line_to_line_voltage=False)

    assert _phases(readings) == {(29, 0): 230.0}


def test_the_signed_load_profile_phases_the_power_totals() -> None:
    values = {"W": 1.0, "Var": 2.0, "VA": 3.0, "PF": 0.9, "WHAvail": 5.0, "ACType": 2}

    default = _phases(convert.readings(values, line_to_line_voltage=True))
    signed = _phases(convert.readings(values, line_to_line_voltage=True, phased_totals=True))

    assert {k for k in default if k[0] in (38, 63, 61, 72)} == {(38, 0), (63, 0), (61, 0), (72, 0)}
    assert {k for k in signed if k[0] in (38, 63, 61, 72)} == {
        (38, 224),
        (63, 224),
        (61, 224),
        (72, 224),
    }
    assert (65, 0) in signed, "power factor is not a total"


def test_meter_energy_totals_are_forward_and_reverse_summations() -> None:
    readings = convert.readings({"WhFwd": 10.0, "WhRev": 2.0}, line_to_line_voltage=False)

    assert sorted((r.flow_direction, r.accumulation_behaviour, r.kind) for r in readings) == [
        (1, 9, 12),
        (19, 9, 12),
    ]


def test_values_that_are_missing_or_not_numbers_give_no_reading() -> None:
    readings = convert.readings({"W": None, "Hz": True, "VA": "5"}, line_to_line_voltage=True)

    assert readings == []


# -- Status ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inv_st", "expected"), [(0, 1), (3, 4), (6, 7), (7, 8), (8, None), (None, None)]
)
def test_inverter_status_is_the_state_plus_one(inv_st: Any, expected: Any) -> None:
    assert convert.inverter_status(inv_st) == expected


def test_status_codes_for_a_running_connected_inverter() -> None:
    raw = {
        "inverterStatus": {"dateTime": 1, "value": 3},
        "connectStatus": {"dateTime": 1, "value": 1},
        "operationalModeStatus": {"dateTime": 1, "value": 1},
        "alarmStatus": (1 << 8) | (1 << 11) | (1 << 0),
        "readingTime": 1,
    }

    status = convert.status(raw)

    assert status["inverterStatus"]["value"] == 4
    assert status["operationalModeStatus"]["value"] == 2
    assert status["connectStatus"]["value"] == 0b11
    assert status["genConnectStatus"]["value"] == 0b111
    assert status["alarmStatus"] == (1 << 3) | (1 << 2), "ground fault is not carried"


def test_a_faulted_disconnected_inverter_reports_the_2018_fault_bit() -> None:
    raw = {
        "inverterStatus": {"value": 6},
        "connectStatus": {"value": 0},
        "operationalModeStatus": {"value": 0},
    }

    status = convert.status(raw)

    assert status["connectStatus"]["value"] == 0
    assert status["genConnectStatus"]["value"] == 1 << 4
    assert status["operationalModeStatus"]["value"] == 1


# -- Ratings and settings --------------------------------------------------------


class _Point:
    def __init__(self, value: Any) -> None:
        self.value = value


def test_ratings_take_their_element_names_and_the_current_rating() -> None:
    model_702 = type("M", (), {"AMaxRtg": _Point(140), "A_SF": _Point(-1)})()

    nameplate = convert.nameplate(
        {"WMaxRtg": {"value": 5, "multiplier": 3}, "CtrlModes": 3, "Unknown": 1}, model_702
    )

    assert nameplate == {
        "rtgMaxW": {"value": 5, "multiplier": 3},
        "modesSupported": 3,
        "rtgMaxA": {"value": 140, "multiplier": -1},
    }


def test_settings_take_their_element_names() -> None:
    settings = convert.settings({"WMax": 1, "WMaxOvrExt": 2, "DoeModesEnabled": 4})

    assert settings == {"setMaxW": 1, "doeModesEnabled": 4}


# -- Controls --------------------------------------------------------------------


def test_max_lim_w_is_a_percent_of_wmax() -> None:
    assert convert.Controls().translate("opModMaxLimW", {"enabled": True, "value": 5000}) == (
        "update_p_lim",
        {"p_lim_mode_enable": 1, "p_lim_w": 50.0},
    )


def test_a_withdrawn_element_turns_its_function_off() -> None:
    assert convert.Controls().translate("opModGenLimW", {"enabled": False}) == (
        "update_gen_lim",
        {"gen_lim_mode_enable": 0},
    )


def test_ramp_time_is_passed_to_the_core() -> None:
    _, params = convert.Controls().translate(
        "opModFixedW", {"enabled": True, "value": 2500, "rampTms": 300}
    )

    assert params == {"WSetEna": 1, "WSetMod": 0, "WSet": 25.0, "ramp_tms": 300}


def test_the_two_fixed_power_factors_share_one_write() -> None:
    controls = convert.Controls()
    pf = {"displacement": 95, "multiplier": -2, "excitation": True}

    controls.translate("opModFixedPFInjectW", {"enabled": True, "value": pf})
    _, both = controls.translate("opModFixedPFAbsorbW", {"enabled": True, "value": pf})
    _, inject_cleared = controls.translate("opModFixedPFInjectW", {"enabled": False})

    expected = {"pf": 0.95, "excitation": True, "mode": 1}
    assert both == {"inj": expected, "abs": expected}
    assert inject_cleared == {"inj": {"mode": 0}, "abs": expected}


def test_connect_and_energize_are_anded() -> None:
    controls = convert.Controls()

    assert controls.translate("opModEnergize", {"enabled": True, "value": False})[1] == {
        "connected": False
    }
    assert controls.translate("opModConnect", {"enabled": True, "value": True})[1] == {
        "connected": False
    }
    assert controls.translate("opModEnergize", {"enabled": False})[1] == {"connected": True}


def test_a_curve_becomes_its_scaled_points() -> None:
    curve = {
        "CurveData": [{"xvalue": 95, "yvalue": 50}, {"xvalue": 105, "yvalue": -50}],
        "xMultiplier": 0,
        "yMultiplier": 0,
        "yRefType": 2,
        "openLoopTms": 500,
    }

    method, params = convert.Controls().translate(
        "opModVoltWatt", {"enabled": True, "value": curve}
    )

    assert method == "update_pv"
    assert (params["pv_curve_v_pts"], params["pv_curve_p_pts"]) == ([95, 105], [50, -50])
    assert (params["pv_deptref"], params["pv_olrt"]) == (1, 5.0)


def test_an_element_the_core_cannot_write_is_refused() -> None:
    with pytest.raises(KeyError):
        convert.Controls().translate("opModWattPF", {"enabled": True, "value": 1})


# -- The connector class -----------------------------------------------------------


def test_every_writable_element_has_its_own_method() -> None:
    for element, method in (
        ("opModFixedW", "update_op_mod_fixed_w"),
        ("opModVoltVar", "update_op_mod_volt_var"),
        ("opModConnect", "update_op_mod_connect"),
        ("opModGenLimW", "update_op_mod_gen_lim_w"),
    ):
        assert element in convert.CORE_METHOD
        assert vars(SunSpecConnector)[method] is not getattr(BaseConnector, method)


def test_an_element_it_cannot_write_keeps_the_base_no_op() -> None:
    assert SunSpecConnector.update_op_mod_watt_pf is BaseConnector.update_op_mod_watt_pf


def test_the_registry_passes_the_reading_profile_to_the_connector(monkeypatch) -> None:
    built: list[dict[str, Any]] = []

    class _Recorder:
        takes_reading_profile = True

        def __init__(self, **kwargs: Any) -> None:
            built.append(kwargs)

    registry = ConnectorConfigRegistry(
        [SunSpecDeviceConfig(lfdi="ab" * 20)],
        reading_profile=ReadingProfile.SIGNED_LOAD_CONVENTION,
    )
    monkeypatch.setattr(registry, "_import_class", lambda _path: _Recorder)

    registry.get_connector("ab" * 20).resolve()

    assert built[0]["reading_profile"] is ReadingProfile.SIGNED_LOAD_CONVENTION
    assert built[0]["role"] == "der"


# -- Isolation ---------------------------------------------------------------------

#: SunSpec point and method names that must not appear outside the SunSpec package.
_SUNSPEC_NAMES = re.compile(
    r"\b(ACType|InvSt|ConnSt|WMaxRtg|WMaxLimPct|WSetPct|LLV|CtrlModes|WChaRteMax|"
    r"VarMaxInj|TotWhImp|update_p_lim|update_qv|update_const_pf)\b"
)


def test_no_sunspec_names_reach_the_rest_of_the_package() -> None:
    root = Path(py20305.__file__).parent
    leaks = [
        f"{path.relative_to(root)}: {match.group(0)}"
        for path in root.rglob("*.py")
        if "sunspec" not in path.relative_to(root).parts
        for match in _SUNSPEC_NAMES.finditer(path.read_text(encoding="utf-8"))
    ]

    assert not leaks


async def test_a_refused_power_factor_is_not_sent_again() -> None:
    """A write the core refuses leaves the merged power factor state as it was."""
    connector = object.__new__(SunSpecConnector)
    connector._controls = convert.Controls()
    sent: list[dict[str, Any]] = []

    class _Core:
        async def update_const_pf(self, params: dict[str, Any]) -> None:
            if params["inj"].get("pf", 0) > 1:
                raise ValueError("power factor out of range")
            sent.append(params)

    connector._core = _Core()
    bad = {"displacement": 150, "multiplier": -2, "excitation": False}
    good = {"displacement": 95, "multiplier": -2, "excitation": False}

    with pytest.raises(ValueError):
        await connector.update_op_mod_fixed_pf_inject_w({"enabled": True, "value": bad})
    await connector.update_op_mod_fixed_pf_absorb_w({"enabled": True, "value": good})

    assert sent == [
        {"inj": {"mode": 0}, "abs": {"pf": 0.95, "excitation": False, "mode": 1}},
    ]


async def test_a_refused_write_does_not_undo_a_write_made_while_it_ran() -> None:
    """Control writes are serialized, so a rollback cannot erase a later element."""
    import asyncio

    connector = object.__new__(SunSpecConnector)
    connector._controls = convert.Controls()
    connector._lock = None
    sent: list[dict[str, Any]] = []
    release = asyncio.Event()

    class _Core:
        calls = 0

        async def update_connect(self, params: dict[str, Any]) -> None:
            _Core.calls += 1
            if _Core.calls == 1:
                await release.wait()
                raise ConnectionError("device did not answer")
            sent.append(params)

    connector._core = _Core()

    disconnect = asyncio.create_task(
        connector.update_op_mod_connect({"enabled": True, "value": False})
    )
    await asyncio.sleep(0)
    deenergize = asyncio.create_task(
        connector.update_op_mod_energize({"enabled": True, "value": False})
    )
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(ConnectionError):
        await disconnect
    await deenergize
    await connector.update_op_mod_connect({"enabled": True, "value": True})

    assert sent == [{"connected": False}, {"connected": False}], "still de-energized"


def test_a_read_without_the_wiring_uses_the_one_the_device_last_stated() -> None:
    values = {"W": 3000.0, "V": 480.0, "A": 6.0, "WL1": 1000.0, "WL2": 1000.0, "WL3": 1000.0}

    stated = convert.readings({**values, "ACType": 2}, line_to_line_voltage=True)
    lost = convert.readings(values, line_to_line_voltage=True, wiring=2)
    unknown = convert.readings(values, line_to_line_voltage=True)

    assert _phases(lost) == _phases(stated)
    assert (29, 224) in _phases(lost), "the system voltage keeps its three-phase code"
    assert (29, 224) not in _phases(unknown), "a device that never stated its wiring has none"


async def test_the_connector_remembers_the_wiring_between_reads() -> None:
    connector = object.__new__(SunSpecConnector)
    connector._line_to_line_voltage = True
    connector._phased_totals = False
    connector._wiring = None
    reads = [
        {"W": 3000.0, "V": 480.0, "ACType": 2, "WL1": 1000.0, "WL2": 1000.0, "WL3": 1000.0},
        {"W": 3000.0, "V": 480.0},
    ]

    class _Core:
        async def fetch_monitoring(self) -> dict[str, Any]:
            return reads.pop(0)

    connector._core = _Core()

    first = await connector.fetch_monitoring()
    second = await connector.fetch_monitoring()

    assert (29, 224) in _phases(first)
    assert (29, 224) in _phases(second)
