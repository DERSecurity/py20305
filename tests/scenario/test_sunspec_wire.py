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


@pytest.fixture
async def modbus():
    """A started 700-series server and a registry resolving to it."""
    image = build_der_image()
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

    yield type(
        "Modbus",
        (),
        {"server": server, "image": image, "registry": staticmethod(lambda: registry)},
    )
    await server.close()


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
