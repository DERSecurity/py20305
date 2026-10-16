"""Broker and notification-listener connection events, over real sockets.

The broker double and the notification listener run on loopback, so the
addresses and ports in each event are the ones the sockets really had.
"""

from __future__ import annotations

import asyncio
import json
import sys

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from py20305.forwarders import (
    ConnectionTelemetryConfig,
    ConnectionTelemetryEmitter,
    ForwarderManager,
    MQTTForwarder,
    MQTTForwarderAdapter,
    MQTTForwarderConfig,
)
from py20305.subscription.notification_server import NotificationServer
from tests.scenario.mqtt_broker import ScenarioMqttBroker
from tests.scenario.support import free_port

pytestmark = pytest.mark.asyncio

if sys.platform == "win32":
    # The MQTT library needs the loop's add_writer, which the Windows
    # proactor loop does not implement.
    @pytest.fixture(scope="module")
    def event_loop_policy():
        """Use a selector loop on Windows."""
        return asyncio.WindowsSelectorEventLoopPolicy()


TOPIC_BASE = "site"
EVENTS_TOPIC = f"{TOPIC_BASE}/out/connection-events"


def build(port: int) -> tuple[ForwarderManager, ConnectionTelemetryEmitter]:
    manager = ForwarderManager()
    manager.add_forwarder(
        MQTTForwarderAdapter(
            MQTTForwarder(
                MQTTForwarderConfig(endpoint="127.0.0.1", port=port, topic_base=TOPIC_BASE)
            )
        )
    )
    emitter = ConnectionTelemetryEmitter(
        manager,
        ConnectionTelemetryConfig(enabled=True, coalesce_successes=False),
        product_version="1.0",
        product_name="example-gateway",
    )
    manager.connection_observer = emitter
    return manager, emitter


async def test_broker_connection_is_reported_with_the_telemetry_role():
    broker = ScenarioMqttBroker(free_port())
    await broker.start()
    manager, emitter = build(broker.port)
    try:
        await manager.start()
        emitter.release_held()
        await manager.stop()
    finally:
        await broker.close()

    (event,) = [json.loads(m.payload) for m in broker.messages_on(EVENTS_TOPIC)]
    assert event["metadata"]["labels"] == ["role:telemetry"]
    assert event["metadata"]["product"]["name"] == "example-gateway"
    assert event["status"] == "Success"
    assert event["connection_info"]["direction"] == "Outbound"
    assert event["dst_endpoint"] == {"ip": "127.0.0.1", "port": broker.port, "svc_name": "mqtt"}
    assert event["src_endpoint"]["ip"] == "127.0.0.1"
    assert event["src_endpoint"]["port"] > 0


async def test_a_refused_broker_connection_is_delivered_once_the_broker_accepts():
    broker = ScenarioMqttBroker(free_port())
    await broker.start()
    broker.refuse_connections(True)
    manager, emitter = build(broker.port)
    try:
        await manager.start()
        assert manager.failed_forwarders(), "the broker refused, so the forwarder must fail"
        assert emitter.held_events == 1

        broker.refuse_connections(False)
        assert await manager.retry_failed() == 1
        emitter.release_held()
        await manager.stop()
    finally:
        await broker.close()

    events = [json.loads(m.payload) for m in broker.messages_on(EVENTS_TOPIC)]
    assert [e["status"] for e in events] == ["Failure", "Success"]
    failure = events[0]
    assert failure["metadata"]["labels"] == ["role:telemetry"]
    assert failure["status_detail"]
    assert failure["dst_endpoint"] == {"ip": "127.0.0.1", "port": broker.port, "svc_name": "mqtt"}
    assert failure["time"] <= events[1]["time"]


class Recorder:
    """Keeps the connection calls a listener makes."""

    def __init__(self) -> None:
        self.successes: list[dict] = []
        self.failures: list[dict] = []

    def record_connection(self, **kwargs: object) -> None:
        self.successes.append(kwargs)

    def record_connection_failure(self, **kwargs: object) -> None:
        self.failures.append(kwargs)


async def post_notification(mode: str) -> tuple[int, Recorder, int]:
    server = NotificationServer(host="127.0.0.1", port=0, tls=None, client_cert_mode=mode)  # type: ignore[arg-type]
    recorder = Recorder()
    server.connection_observer = recorder  # type: ignore[assignment]
    app = web.Application()
    app.router.add_post("/notify", server._handle_notify)
    client = TestClient(TestServer(app, host="127.0.0.1"))
    await client.start_server()
    port = client.port
    try:
        resp = await client.post("/notify", data=b"<not-xml/>")
        status = resp.status
    finally:
        await client.close()
    assert port is not None
    return status, recorder, port


async def test_a_notification_request_is_reported_as_an_inbound_server_connection():
    _, recorder, port = await post_notification("off")
    (call,) = recorder.successes
    assert call["role"] == "server"
    assert call["service"] == "ieee2030.5"
    assert int(call["direction"]) == 1
    assert call["dst"].port == port
    assert call["src"].ip == "127.0.0.1"
    assert call["src"].port > 0
    assert recorder.failures == []


async def test_a_notification_rejected_for_a_missing_certificate_is_reported_as_a_failure():
    status, recorder, _ = await post_notification("enforce")
    assert status == 401
    assert recorder.successes == []
    (call,) = recorder.failures
    assert call["reason"] == "No client certificate presented"
    assert call["status_code"] == "401"


async def test_a_notification_server_without_an_observer_still_answers():
    server = NotificationServer(host="127.0.0.1", port=0, tls=None, client_cert_mode="off")
    app = web.Application()
    app.router.add_post("/notify", server._handle_notify)
    client = TestClient(TestServer(app, host="127.0.0.1"))
    await client.start_server()
    try:
        resp = await client.post("/notify", data=b"<not-xml/>")
        assert resp.status == 400
    finally:
        await client.close()
    assert isinstance(resp, aiohttp.ClientResponse)
