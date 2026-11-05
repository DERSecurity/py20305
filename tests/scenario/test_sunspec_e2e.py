"""The SunSpec connector end to end, against a served three-phase inverter.

The device is :func:`build_full_der_image`: models 1, 701, 702, 703, 704 and
705 over a real Modbus TCP socket. The connector is built from configuration
through the registry, as the client builds it. Every check is on IEEE 2030.5
output or on the registers the device holds:

- Monitoring becomes readings whose MirrorMeterReading mRIDs are the ones
  release 0.16.x registered for the same device
  (``tests/fixtures/sunspec_0_16_mup.json``, recorded by
  ``record_sunspec_0_16.py``), except the system voltage and current, which
  now state their PhaseCode and so register under mRIDs of their own.
- Ratings, settings and status use the DERCapability, DERSettings and
  DERStatus names and codes.
- An opModFixedW, an opModVoltVar curve and opModConnect reach the device's
  registers.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from py20305.connectors.config import SunSpecDeviceConfig
from py20305.connectors.modes import translate_controls
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.models.sep.sep import (
    CurveData,
    DercontrolBase,
    Dercurve1,
    DercurveLink,
    DercurveType,
    DerunitRefType,
    MRidtype,
    PowerOfTenMultiplierType,
    SignedPerCentControlType,
    TimeType,
)
from py20305.telemetry.mup import ReadingProfile, create_mup
from tests.scenario.modbus_server import (
    FULL_DER,
    SunSpecModbusServer,
    build_full_der_image,
    point_address,
)
from tests.scenario.support import free_port

pytestmark = pytest.mark.asyncio

LFDI = "ab" * 20
FIXTURE = Path(__file__).parent.parent / "fixtures" / "sunspec_0_16_mup.json"

#: MirrorMeterReadings 0.16.x registered whose ReadingType is now different,
#: by reading profile. The system voltage is the line-to-line average across
#: ABC and the system current a total across ABC, so both carry PhaseCode 224
#: now. Under the signed-load profile 0.16 already tagged the current with
#: the wiring's phase, so only the voltage changes there.
PHASED_SYSTEM = {
    ReadingProfile.DEFAULT: {"Voltage", "Current"},
    ReadingProfile.SIGNED_LOAD_CONVENTION: {"Voltage"},
}


async def _connect(server: SunSpecModbusServer, profile: ReadingProfile) -> Any:
    """Return the device's connector, built from configuration as the client builds it."""
    registry = ConnectorConfigRegistry(
        [
            SunSpecDeviceConfig(
                lfdi=LFDI, host="127.0.0.1", port=server.port, timeout=2, scan_retries=1
            )
        ],
        reading_profile=profile,
    )
    proxy = registry.get_connector(LFDI)
    assert proxy is not None
    return await proxy.aresolve()


@pytest.fixture
async def served():
    image = build_full_der_image()
    server = SunSpecModbusServer(image, free_port())
    await server.start()
    yield server, image
    await server.close()


@pytest.fixture
async def device(served):
    server, image = served
    return await _connect(server, ReadingProfile.DEFAULT), server, image


def _mmrs(readings: list[Any], profile: ReadingProfile) -> dict[str, dict[str, Any]]:
    mup = create_mup(LFDI, readings, 300, profile=profile)

    def code(field: Any) -> int | None:
        return None if field is None else int(field.value)

    return {
        mmr.description: {
            "mRID": mmr.m_rid.value.hex().upper(),
            "uom": code(mmr.reading_type.uom),
            "phase": code(mmr.reading_type.phase),
        }
        for mmr in mup.mirror_meter_reading
    }


# -- Reads -------------------------------------------------------------------------


#: The profiles release 0.16 had; the fixture records its mRIDs for these.
@pytest.mark.parametrize("profile", [ReadingProfile.DEFAULT, ReadingProfile.SIGNED_LOAD_CONVENTION])
async def test_readings_register_under_the_mrids_0_16_used(served, profile) -> None:
    connector = await _connect(served[0], profile)
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))[profile.value]

    posted = _mmrs(await connector.fetch_monitoring(), profile)
    posted_mrids = {entry["mRID"] for entry in posted.values()}

    changed = PHASED_SYSTEM[profile]
    kept = {name: entry for name, entry in expected.items() if name not in changed}
    missing = {name: e["mRID"] for name, e in kept.items() if e["mRID"] not in posted_mrids}
    assert not missing, f"0.16 mRIDs no longer posted: {missing}"
    by_mrid = {entry["mRID"]: entry for entry in posted.values()}
    for name, entry in kept.items():
        now = by_mrid[entry["mRID"]]
        assert (now["uom"], now["phase"]) == (entry["uom"], entry["phase"]), name
    for name in changed:
        assert expected[name]["mRID"] not in posted_mrids, f"{name} kept its unphased mRID"


async def test_readings_carry_the_phase_of_what_they_measure(device) -> None:
    connector, _server, _image = device

    readings = await connector.fetch_monitoring()

    phases = {(r.uom, r.phase): r.value for r in readings}
    assert phases[(29, 224)] == pytest.approx(FULL_DER["LLV"]), "system voltage is line to line"
    assert phases[(5, 224)] == pytest.approx(FULL_DER["A"]), "system current is a total"
    for line, phase in ((1, 129), (2, 65), (3, 33)):
        assert phases[(29, phase)] == pytest.approx(FULL_DER["VL"][line - 1]), "line to neutral"
    for key, phase in (("VL1L2", 132), ("VL2L3", 66), ("VL3L1", 40)):
        assert phases[(29, phase)] == pytest.approx(FULL_DER[key]), "line to line"
    assert phases[(38, 0)] == pytest.approx(FULL_DER["W"])
    assert phases[(38, 128)] == pytest.approx(FULL_DER["WL"][0])
    assert phases[(33, 0)] == pytest.approx(FULL_DER["Hz"])


async def test_ratings_and_settings_use_the_ieee_2030_5_names(device) -> None:
    connector, _server, _image = device

    nameplate = await connector.fetch_nameplate()
    settings = await connector.fetch_configuration()

    assert nameplate["rtgMaxW"] == {"value": 10000, "multiplier": 0}
    assert nameplate["rtgMaxVA"] == {"value": 10000, "multiplier": 0}
    assert nameplate["rtgMaxVar"] == {"value": 4400, "multiplier": 0}
    assert nameplate["rtgMaxA"] == {"value": 140, "multiplier": -1}
    assert nameplate["rtgVNom"] == {"value": 480, "multiplier": 0}
    assert settings["setMaxW"] == {"value": 9000, "multiplier": 0}
    assert settings["setVNom"] == {"value": 480, "multiplier": 0}


async def test_status_uses_the_ieee_2030_5_codes(device) -> None:
    connector, _server, _image = device

    status = await connector.fetch_status()

    assert status["inverterStatus"]["value"] == 4  # running
    assert status["operationalModeStatus"]["value"] == 2  # operational mode
    assert status["connectStatus"]["value"] == 0b11  # connected, energized
    assert status["genConnectStatus"]["value"] == 0b111  # connected, available, operating
    assert status["alarmStatus"] == 1 << 1  # over-voltage


# -- Writes ------------------------------------------------------------------------


async def _apply(connector: Any, base: DercontrolBase, curves: list[Dercurve1]) -> None:
    for method, params in translate_controls(base, curves):
        await getattr(connector, method)(params)


async def test_a_fixed_w_control_writes_the_setpoint(device) -> None:
    connector, server, image = device

    await _apply(connector, DercontrolBase(op_mod_fixed_w=SignedPerCentControlType(value=5000)), [])

    assert server.registers[point_address(image, 704, "WSetEna")] == 1
    assert server.registers[point_address(image, 704, "WSetMod")] == 0
    assert server.registers[point_address(image, 704, "WSetPct")] == 50

    await connector.update_op_mod_fixed_w({"enabled": False})
    assert server.registers[point_address(image, 704, "WSetEna")] == 0


async def test_a_volt_var_curve_is_written_and_adopted(device) -> None:
    connector, server, image = device
    curve = Dercurve1(
        href="/curve/vv",
        m_rid=MRidtype(value=b"\x02" * 16),
        description="volt-var",
        creation_time=TimeType(value=1000),
        curve_data=[
            CurveData(xvalue=x, yvalue=y) for x, y in ((92, 30), (98, 0), (102, 0), (108, -30))
        ],
        curve_type=DercurveType(value=11),
        x_multiplier=PowerOfTenMultiplierType(value=0),
        y_multiplier=PowerOfTenMultiplierType(value=0),
        y_ref_type=DerunitRefType(value=3),
    )

    await asyncio.wait_for(
        _apply(connector, DercontrolBase(op_mod_volt_var=DercurveLink(href="/curve/vv")), [curve]),
        15,
    )

    assert server.registers[point_address(image, 705, "Ena")] == 1
    assert server.registers[point_address(image, 705, "AdptCrvReq")] == 2
    assert server.registers[point_address(image, 705, "AdptCrvRslt")] == 1
    curve_values = _second_curve_points(server, image)
    assert curve_values == [(92, 30), (98, 0), (102, 0), (108, -30)]


def _second_curve_points(server: SunSpecModbusServer, image: bytes) -> list[tuple[int, int]]:
    """Return the (V, Var) points of model 705's second curve, as signed registers."""
    import sunspec2.device as ss

    base = point_address(image, 705, "ID")
    length = server.registers[base + 1]
    words = [server.registers[base + i] for i in range(length + 2)]
    model = ss.Model(705, data=b"".join(w.to_bytes(2, "big") for w in words))
    crv = model.Crv[1]
    return [(int(pt.V.value), int(pt.Var.value)) for pt in crv.Pt[: crv.ActPt.value]]


async def test_connect_and_disconnect_write_the_enter_service_permit(device) -> None:
    connector, server, image = device
    es = point_address(image, 703, "ES")

    await connector.update_op_mod_connect({"enabled": True, "value": False})
    assert server.registers[es] == 0, "disconnect ceases service"

    await connector.update_op_mod_connect({"enabled": True, "value": True})
    assert server.registers[es] == 1, "connect permits service again"

    await connector.update_op_mod_energize({"enabled": True, "value": False})
    assert server.registers[es] == 0, "de-energize ceases service"
    await connector.update_op_mod_energize({"enabled": False})
    assert server.registers[es] == 1, "withdrawing the element permits service again"


async def test_both_fixed_power_factors_apply_and_withdraw(device) -> None:
    connector, _server, _image = device
    pf = {"displacement": 95, "multiplier": -2, "excitation": False}

    await connector.update_op_mod_fixed_pf_inject_w({"enabled": True, "value": pf})
    await connector.update_op_mod_fixed_pf_absorb_w({"enabled": True, "value": pf})
    model = await asyncio.to_thread(connector._core._get_model, 704)
    assert (model.PFWInjEna.value, model.PFWAbsEna.value) == (1, 1)
    assert model.PFWInj.PF.cvalue == pytest.approx(0.95)
    assert model.PFWAbs.PF.cvalue == pytest.approx(0.95)

    await connector.update_op_mod_fixed_pf_inject_w({"enabled": False})
    await connector.update_op_mod_fixed_pf_absorb_w({"enabled": False})
    model = await asyncio.to_thread(connector._core._get_model, 704)
    assert (model.PFWInjEna.value, model.PFWAbsEna.value) == (0, 0)
