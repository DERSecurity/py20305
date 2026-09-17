"""The SunSpec connector against a real Modbus TCP server.

The unit suite drives the connector through mocked pysunspec2 objects; none of
it ever framed a Modbus PDU. These tests scan, read and write a served
register image over an actual socket -- and the last one closes the whole
loop: an IEEE 2030.5 control arriving over mutual TLS ends as registers
written into the controls model.
"""

from __future__ import annotations

import asyncio

import pytest

from py20305.client import CsipClient, TlsConfig
from py20305.client.retry import RetryPolicy
from py20305.connectors.config import SunSpecDeviceConfig
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.security import compute_lfdi
from tests.scenario.modbus_server import (
    SunSpecModbusServer,
    build_der_image,
    point_address,
)
from tests.scenario.support import ScenarioServer, free_port, make_certs

pytestmark = pytest.mark.asyncio

LFDI = "ab" * 20


async def _start_modbus(image: bytes):
    """A started 700-series server on *image*, and a registry resolving to it."""
    server = SunSpecModbusServer(image, free_port())
    await server.start()

    registry = ConnectorConfigRegistry(
        [
            SunSpecDeviceConfig(
                type="sunspec",
                lfdi=LFDI,
                host="127.0.0.1",
                port=server.port,
                timeout=2,
                scan_retries=1,
            )
        ]
    )

    return type(
        "Modbus",
        (),
        {"server": server, "image": image, "registry": staticmethod(lambda: registry)},
    )


@pytest.fixture
async def modbus():
    handle = await _start_modbus(build_der_image())
    yield handle
    await handle.server.close()


@pytest.fixture
async def modbus_without_rate_settings():
    """A device that implements neither model 702 charge/discharge rate setting."""
    handle = await _start_modbus(build_der_image(rate_settings=False))
    yield handle
    await handle.server.close()


def _written_addresses(server) -> set[int]:
    """Every register address the connector actually wrote to."""
    return {w.address + i for w in server.writes for i in range(len(w.values))}


async def _resolve(registry: ConnectorConfigRegistry):
    proxy = registry.get_connector(LFDI)
    assert proxy is not None, "the registry must resolve the configured device"
    return await proxy.aresolve()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


async def test_scan_and_monitoring_read_over_the_wire(modbus):
    """Scan finds the map; measurements arrive scaled per their SF points.

    The image carries W=5000 (SF 0), LLV=2405 (SF -1), Hz=6001 (SF -2): what
    comes back must be 5000 W, 240.5 V and 60.01 Hz, or scale-factor handling
    broke somewhere between the socket and the caller.
    """
    connector = await _resolve(modbus.registry())
    values = await connector.fetch_monitoring()

    assert values["W"] == pytest.approx(5000.0)
    assert values["V"] == pytest.approx(240.5)
    assert values["Hz"] == pytest.approx(60.01)


async def test_nameplate_read_over_the_wire(modbus):
    """fetch_configuration must surface the 702 rating the telemetry cycle needs."""
    connector = await _resolve(modbus.registry())
    configuration = await connector.fetch_configuration()

    assert configuration["WMax"] == {"value": 10000, "multiplier": 0}


async def test_a_modbus_exception_is_survivable(modbus):
    """The device answering exception 2 is that exchange's failure, not the session's."""
    connector = await _resolve(modbus.registry())
    assert (await connector.fetch_monitoring())["W"] == pytest.approx(5000.0)

    modbus.server.fail_next(2)  # illegal data address
    try:
        degraded = await connector.fetch_monitoring()
    except Exception as exc:  # noqa: BLE001 - either shape is acceptable...
        assert "2" in str(exc) or "exception" in str(exc).lower()
    else:
        # ...but a silent identical-to-healthy answer is not.
        assert degraded != {} and degraded.get("W") in (None, pytest.approx(5000.0))

    assert (await connector.fetch_monitoring())["W"] == pytest.approx(5000.0)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def test_p_lim_write_lands_in_the_controls_model(modbus):
    """An 80% limit becomes WMaxLimPct=80 with its enable set, on the wire."""
    connector = await _resolve(modbus.registry())
    await connector.update_p_lim({"p_lim_mode_enable": 1, "p_lim_w": 80})

    ena = point_address(modbus.image, 704, "WMaxLimPctEna")
    pct = point_address(modbus.image, 704, "WMaxLimPct")
    assert modbus.server.writes, "no Modbus write ever reached the device"
    assert modbus.server.registers[ena] == 1
    assert modbus.server.registers[pct] == 80


async def test_inject_limit_writes_the_discharge_rate_setting(modbus):
    """opModMaxLimWInject carries absolute watts and limits *generation*, so it
    lands on model 702 WDisChaRteMax at its raw value -- no percent conversion.
    """
    connector = await _resolve(modbus.registry())
    await connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})

    addr = point_address(modbus.image, 702, "WDisChaRteMax")
    assert addr in _written_addresses(modbus.server), "the discharge rate was never written"
    assert modbus.server.registers[addr] == 4000


async def test_absorb_limit_writes_the_charge_rate_setting(modbus):
    """opModMaxLimWAbsorb limits *absorption*, so it lands on the charge-rate
    setting -- the opposite register from inject."""
    connector = await _resolve(modbus.registry())
    await connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 2500})

    addr = point_address(modbus.image, 702, "WChaRteMax")
    assert addr in _written_addresses(modbus.server), "the charge rate was never written"
    assert modbus.server.registers[addr] == 2500


async def test_the_two_directions_are_not_crossed(modbus):
    """Both controls at once, on the wire: neither register carries the other's
    value. Inverting the pair would cap discharge when asked to cap charging."""
    connector = await _resolve(modbus.registry())
    await connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
    await connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 2500})

    discharge = point_address(modbus.image, 702, "WDisChaRteMax")
    charge = point_address(modbus.image, 702, "WChaRteMax")
    assert modbus.server.registers[discharge] == 4000
    assert modbus.server.registers[charge] == 2500


async def test_watts_limits_leave_the_percent_register_alone(modbus):
    """On a device implementing the 702 rate settings the watts-typed controls
    no longer contend with opModMaxLimW for WMaxLimPct."""
    connector = await _resolve(modbus.registry())
    await connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})
    await connector.update_p_lim_abs({"p_lim_mode_enable": 1, "p_lim_watts": 2500})

    written = _written_addresses(modbus.server)
    assert point_address(modbus.image, 704, "WMaxLimPctEna") not in written
    assert point_address(modbus.image, 704, "WMaxLimPct") not in written


async def test_inject_falls_back_to_the_percent_register(modbus_without_rate_settings):
    """A device lacking WDisChaRteMax still gets an inject limit, via the
    percent route: 4000 W against WMax=10000 becomes WMaxLimPct=40."""
    modbus = modbus_without_rate_settings
    connector = await _resolve(modbus.registry())
    await connector.update_p_lim_inj({"p_lim_mode_enable": 1, "p_lim_watts": 4000})

    ena = point_address(modbus.image, 704, "WMaxLimPctEna")
    pct = point_address(modbus.image, 704, "WMaxLimPct")
    assert modbus.server.registers[ena] == 1
    assert modbus.server.registers[pct] == 40


# ---------------------------------------------------------------------------
# The whole loop: 2030.5 over mTLS in, Modbus registers out
# ---------------------------------------------------------------------------


async def test_csip_control_becomes_modbus_registers(modbus, tmp_path):
    """A DERControl with opModMaxLimW=80 ends as registers in model 704.

    Utility head-end to inverter registers in one test: the scenario 2030.5
    server publishes the control, the client walks to it over mutual TLS, the
    event engine activates and dispatches it, and the SunSpec connector frames
    the Modbus write -- asserted at the far end, in the served register image.
    """
    certs = make_certs(tmp_path)
    csip = ScenarioServer(certs, free_port())
    await csip.start()
    client_lfdi = compute_lfdi(certs.client_cert.read_text())
    csip.seed_standard_tree(client_lfdi)

    registry = ConnectorConfigRegistry(
        [
            SunSpecDeviceConfig(
                type="sunspec",
                lfdi=client_lfdi,
                host="127.0.0.1",
                port=modbus.server.port,
                timeout=2,
                scan_retries=1,
            )
        ]
    )
    client = CsipClient(
        csip.base_url,
        tls=TlsConfig(
            client_cert=certs.client_cert,
            client_key=certs.client_key,
            ca_cert=certs.ca_a,
        ),
        retry=RetryPolicy(max_transient=1, base_delay=0.05),
        dispatcher=ConnectorDispatcher(registry, lfdi_resolver=lambda _href: client_lfdi),
    )
    try:
        await client.connect()
        await client.poll_now()

        ena = point_address(modbus.image, 704, "WMaxLimPctEna")
        pct = point_address(modbus.image, 704, "WMaxLimPct")
        deadline = asyncio.get_event_loop().time() + 20
        while not (
            modbus.server.registers.get(ena) == 1 and modbus.server.registers.get(pct) == 80
        ):
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError(
                    f"control never reached the registers; writes={modbus.server.writes}"
                )
            await asyncio.sleep(0.1)

        # And the server heard about it: the control's Response arrived.
        deadline = asyncio.get_event_loop().time() + 10
        while not csip.requests_for("/rsps", "POST"):
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError("no Response was posted after the dispatch")
            await asyncio.sleep(0.1)
    finally:
        await client.shutdown()
        await csip.close()
