"""Intrusion-detection wire tests: the three MQTT channels, end to end.

A security-monitoring platform watching this client consumes three streams
over one MQTT connection:

- **Upstream** -- the client's northbound IEEE 2030.5 exchanges, as
  ``ProtocolMessage`` envelopes on the protocol-message topic.
- **Session tracking** -- the client's own connection outcomes, as OCSF
  Network Activity events on the connection-events topic. This is the stream
  a passive capture cannot produce: a TLS failure or a refused connection is
  known only to the client, inside the session.
- **Downstream** -- the SunSpec/Modbus reads and writes the client performs
  against the DER, as ``protocol: modbus`` envelopes.

The unit suites drive each emitter against recording stubs; none of them ever
framed an MQTT packet. These tests run the real client against a scriptable
2030.5 server, a served Modbus register image and a real MQTT broker socket,
and assert on the bytes that reached the broker -- so a regression anywhere
between the request path and the wire fails here, not at a deployment.
"""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from py20305.client import CsipClient, Sep2Error, TlsConfig
from py20305.client.retry import RetryPolicy
from py20305.connectors.config import SunSpecDeviceConfig
from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.forwarders import (
    PROTOCOL_MESSAGE_TOPIC_SUFFIX,
    ConnectionTelemetryConfig,
    ConnectionTelemetryEmitter,
    DeviceTelemetryConfig,
    ForwarderManager,
    MQTTForwarder,
    MQTTForwarderAdapter,
    MQTTForwarderConfig,
)
from py20305.security import compute_lfdi
from tests.scenario.modbus_server import SunSpecModbusServer, build_der_image
from tests.scenario.mqtt_broker import ScenarioMqttBroker
from tests.scenario.support import ScenarioServer, free_port, make_certs

pytestmark = pytest.mark.asyncio


if sys.platform == "win32":
    # The forwarder's MQTT library drives its socket through the loop's
    # reader/writer callbacks, which Windows's default proactor loop does not
    # implement. CI runs these tests on Linux, where the default already
    # works and this fixture is not defined at all.
    @pytest.fixture(scope="module")
    def event_loop_policy():
        """A selector loop on Windows: the MQTT transport needs ``add_writer``."""
        return asyncio.WindowsSelectorEventLoopPolicy()


TOPIC_BASE = "testsite"
RAW_TOPIC = f"{TOPIC_BASE}/{PROTOCOL_MESSAGE_TOPIC_SUFFIX}"
EVENTS_TOPIC = f"{TOPIC_BASE}/out/connection-events"


def payloads_on(broker: ScenarioMqttBroker, topic: str) -> list[dict]:
    """JSON-decode every message the broker received on ``topic``."""
    return [json.loads(m.payload) for m in broker.messages_on(topic)]


async def build_stack(
    tmp_path,
    *,
    coalesce_window_seconds: float = 0.0,
    with_modbus: bool = False,
    server_port: int | None = None,
) -> SimpleNamespace:
    """Assemble the client the way the runner does, over real sockets.

    One MQTT broker double, one forwarder manager carrying all three
    channels, one 2030.5 scenario server (unless ``server_port`` points the
    client somewhere nothing listens), and optionally a served Modbus image
    behind the dispatcher.
    """
    broker = ScenarioMqttBroker(free_port())
    await broker.start()

    manager = ForwarderManager()
    manager.add_forwarder(
        MQTTForwarderAdapter(
            MQTTForwarder(
                MQTTForwarderConfig(
                    endpoint="127.0.0.1",
                    port=broker.port,
                    topic_base=TOPIC_BASE,
                )
            )
        )
    )
    await manager.start()
    assert not manager.failed_forwarders(), "the forwarder must reach the broker"

    certs = make_certs(tmp_path)
    client_lfdi = compute_lfdi(certs.client_cert.read_text())
    manager.client_lfdi = client_lfdi

    csip: ScenarioServer | None = None
    if server_port is None:
        csip = ScenarioServer(certs, free_port())
        await csip.start()
        csip.seed_standard_tree(client_lfdi)
        server_port = csip.port

    modbus: SunSpecModbusServer | None = None
    dispatcher = None
    if with_modbus:
        modbus = SunSpecModbusServer(build_der_image(), free_port())
        await modbus.start()
        registry = ConnectorConfigRegistry(
            [
                SunSpecDeviceConfig(
                    type="sunspec",
                    lfdi=client_lfdi,
                    host="127.0.0.1",
                    port=modbus.port,
                    timeout=2,
                    scan_retries=1,
                )
            ]
        )
        dispatcher = ConnectorDispatcher(
            registry,
            lfdi_resolver=lambda _href: client_lfdi,
            telemetry=DeviceTelemetryEmitter(
                manager, DeviceTelemetryConfig(enabled=True), client_id=client_lfdi
            ),
        )

    base_url = f"https://127.0.0.1:{server_port}"
    client = CsipClient(
        base_url,
        tls=TlsConfig(
            client_cert=certs.client_cert,
            client_key=certs.client_key,
            ca_cert=certs.ca_a,
        ),
        retry=RetryPolicy(max_transient=1, max_tls=1, base_delay=0.05),
        dispatcher=dispatcher,
    )
    client.http.forwarder = manager

    emitter = ConnectionTelemetryEmitter(
        manager,
        ConnectionTelemetryConfig(enabled=True, coalesce_window_seconds=coalesce_window_seconds),
        product_version="test",
    )
    emitter.set_server("127.0.0.1", server_port, base_url=base_url)
    client.http.connection_observer = emitter

    async def close() -> None:
        await client.shutdown()
        await manager.stop()  # drains the publish queue before returning
        if modbus is not None:
            await modbus.close()
        if csip is not None:
            await csip.close()
        await broker.close()

    return SimpleNamespace(
        broker=broker,
        manager=manager,
        client=client,
        emitter=emitter,
        certs=certs,
        base_url=base_url,
        csip=csip,
        modbus=modbus,
        close=close,
    )


async def drain(stack: SimpleNamespace) -> None:
    """Stop the forwarder so its queue is flushed to the broker."""
    await stack.manager.stop()


# ---------------------------------------------------------------------------
# Upstream: the northbound 2030.5 capture channel
# ---------------------------------------------------------------------------


async def test_upstream_channel_delivers_the_2030_5_exchanges(tmp_path):
    """Northbound requests arrive at the broker as 2030.5 protocol messages.

    Not "the forwarder was called": the JSON that crossed the MQTT socket
    carries ``protocol: "2030.5"`` and both wire directions -- what a
    consumer's parser actually keys on.
    """
    stack = await build_stack(tmp_path)
    try:
        await stack.client.connect()
        await stack.client.poll_now()
        await drain(stack)

        messages = payloads_on(stack.broker, RAW_TOPIC)
        assert messages, "no protocol message ever reached the broker"
        assert all(m["protocol"] == "2030.5" for m in messages)
        directions = {m["direction"] for m in messages}
        assert directions == {"upstream", "downstream"}, (
            "a captured exchange has a request and a response; one direction "
            f"missing means half the capture is gone: {directions}"
        )
    finally:
        await stack.close()


# ---------------------------------------------------------------------------
# Session tracking: the client's own connection outcomes
# ---------------------------------------------------------------------------


async def test_session_tracking_reports_each_validated_contact(tmp_path):
    """With coalescing off, every successful exchange lands as OCSF 4001.

    The events must name the server endpoint and the service, and at least
    one must carry the client's own source port -- the fact only the client
    can report and the reason this channel exists.
    """
    stack = await build_stack(tmp_path, coalesce_window_seconds=0.0)
    try:
        await stack.client.connect()
        await stack.client.poll_now()
        await drain(stack)

        events = payloads_on(stack.broker, EVENTS_TOPIC)
        assert events, "no connection event ever reached the broker"
        assert all(e["class_uid"] == 4001 for e in events)
        successes = [e for e in events if e["status"] == "Success"]
        assert successes, "a healthy poll cycle must report successes"
        for event in successes:
            assert event["activity_name"] == "Open"
            assert event["dst_endpoint"]["ip"] == "127.0.0.1"
            assert event["dst_endpoint"]["port"] == stack.csip.port
            assert event["dst_endpoint"]["svc_name"] == "ieee2030.5"
            assert event["metadata"]["product"]["name"] == "py20305"
        assert any(e.get("src_endpoint", {}).get("port") for e in successes), (
            "no event carried the client's local port; socket attribution is dead"
        )
    finally:
        await stack.close()


async def test_session_tracking_reports_a_refused_connection_with_its_reason(tmp_path):
    """A server that refuses the connection becomes a Refuse/Failure event.

    This is the record a passive sensor cannot produce, and its reason is its
    entire value -- so the assertion is on ``status_detail``, not just on the
    event existing.
    """
    stack = await build_stack(tmp_path, server_port=free_port())  # nothing listens
    try:
        with pytest.raises(Sep2Error):
            await stack.client.connect()
        await drain(stack)

        events = payloads_on(stack.broker, EVENTS_TOPIC)
        failures = [e for e in events if e["status"] == "Failure"]
        assert failures, "a refused connection must be reported"
        for event in failures:
            assert event["activity_name"] in ("Refuse", "Fail")
            assert event["status_detail"].strip(), "a failure without a reason is worthless"
            assert event["dst_endpoint"]["ip"] == "127.0.0.1"
    finally:
        await stack.close()


async def test_session_tracking_reports_a_tls_rejection(tmp_path):
    """The client refusing the server's certificate is reported as a TLS failure.

    A second client is pointed at the same server but trusts only the other
    test CA, so the handshake fails on its side. From outside the session
    this looks like a connection that simply ended; only the client knows it
    was a certificate rejection, which is exactly why this channel exists.
    """
    stack = await build_stack(tmp_path)
    try:
        untrusting = CsipClient(
            stack.base_url,
            tls=TlsConfig(
                client_cert=stack.certs.client_cert,
                client_key=stack.certs.client_key,
                ca_cert=stack.certs.ca_b,  # trusts nothing the server presents
            ),
            retry=RetryPolicy(max_transient=1, max_tls=1, base_delay=0.05),
        )
        untrusting.http.connection_observer = stack.emitter
        try:
            with pytest.raises(Sep2Error):
                await untrusting.connect()
        finally:
            await untrusting.shutdown()
        await drain(stack)

        events = payloads_on(stack.broker, EVENTS_TOPIC)
        tls_failures = [
            e for e in events if e["status"] == "Failure" and "TLS" in e.get("status_detail", "")
        ]
        assert tls_failures, "a TLS rejection must be reported with a TLS reason"
        assert all(e["activity_name"] == "Fail" for e in tls_failures)
    finally:
        await stack.close()


async def test_session_tracking_coalesces_successes_within_the_window(tmp_path):
    """With a wide window, a poll cycle's successes collapse to one record.

    The coalesced event must still answer what a per-attempt record would
    have: how many attempts, and over what interval. It is emitted when the
    client closes -- attempts accumulated when the window never closed are
    still attempts the log accounts for.
    """
    stack = await build_stack(tmp_path, coalesce_window_seconds=3600.0)
    try:
        await stack.client.connect()
        await stack.client.poll_now()

        # The client is up and polling; nothing has closed the window yet.
        await asyncio.sleep(0.2)
        assert not payloads_on(stack.broker, EVENTS_TOPIC), (
            "successes inside an open window must not be emitted one by one"
        )
    finally:
        # Shutdown flushes the observer; stopping the manager drains the queue.
        await stack.close()

    events = payloads_on(stack.broker, EVENTS_TOPIC)
    assert len(events) == 1, f"one window, one event; got {len(events)}"
    event = events[0]
    assert event["status"] == "Success"
    assert event["count"] >= 2, "a poll cycle makes several requests"
    assert event["start_time"] <= event["end_time"]
    assert event["duration"] == event["end_time"] - event["start_time"]


# ---------------------------------------------------------------------------
# Downstream: the southbound Modbus channel
# ---------------------------------------------------------------------------


async def test_downstream_channel_reports_the_modbus_write(tmp_path):
    """A 2030.5 control that reaches the inverter is reported as Modbus telemetry.

    The full loop: the utility publishes a control, the client dispatches it,
    the connector writes registers over a real Modbus socket -- and the same
    write arrives at the broker as a ``protocol: modbus`` envelope, so the
    monitoring platform sees the command that actually reached the equipment,
    not just the one that arrived over 2030.5.
    """
    stack = await build_stack(tmp_path, with_modbus=True)
    try:
        await stack.client.connect()
        await stack.client.poll_now()

        deadline = asyncio.get_event_loop().time() + 20
        while not stack.modbus.writes:
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError("the control never reached the Modbus server")
            await asyncio.sleep(0.1)

        await drain(stack)

        modbus_messages = [
            m for m in payloads_on(stack.broker, RAW_TOPIC) if m["protocol"] == "modbus"
        ]
        assert modbus_messages, "the write reached the device but never the broker"
        writes = [m for m in modbus_messages if m["direction"] == "downstream"]
        assert writes, "a control written to the device is downstream telemetry"
        assert any("p_lim" in json.dumps(m["payload"]) for m in writes), (
            "the reported write must carry the control it wrote"
        )
    finally:
        await stack.close()


# ---------------------------------------------------------------------------
# All three at once
# ---------------------------------------------------------------------------


async def test_all_three_channels_flow_over_one_broker_connection(tmp_path):
    """Upstream, session tracking and downstream all arrive, distinguishable.

    One client, one broker, one poll cycle with a control in it. The
    monitoring platform's three consumers each find their stream: 2030.5
    envelopes and Modbus envelopes split by ``protocol`` on the raw topic,
    OCSF events alone on theirs -- and neither topic contains the other's
    payload shape.
    """
    stack = await build_stack(tmp_path, coalesce_window_seconds=0.0, with_modbus=True)
    try:
        await stack.client.connect()
        await stack.client.poll_now()

        deadline = asyncio.get_event_loop().time() + 20
        while not stack.modbus.writes:
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError("the control never reached the Modbus server")
            await asyncio.sleep(0.1)

        await drain(stack)

        raw = payloads_on(stack.broker, RAW_TOPIC)
        events = payloads_on(stack.broker, EVENTS_TOPIC)

        assert any(m["protocol"] == "2030.5" for m in raw), "upstream channel silent"
        assert any(m["protocol"] == "modbus" for m in raw), "downstream channel silent"
        assert any(e["class_uid"] == 4001 for e in events), "session tracking silent"

        # The streams stay unmixed: every raw message is a protocol envelope,
        # every event is OCSF -- a consumer never has to sniff.
        assert all("protocol" in m and "class_uid" not in m for m in raw)
        assert all(e["class_uid"] == 4001 and "protocol" not in e for e in events)
    finally:
        await stack.close()
