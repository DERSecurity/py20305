"""Reconnect behavior of the MQTT forwarder, against a fake aiomqtt client.

The fake replaces the ``aiomqtt`` module the forwarder imports. It raises
aiomqtt's own exception types with the codes aiomqtt uses, so the forwarder
sees the same errors and messages a real dropped connection produces. Time is
injected: the fake sleep records each reconnect delay and advances a fake
clock, so backoff and report throttling are checked without waiting.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import logging
import sys
import threading
import types
from collections.abc import Callable
from typing import Any

import aiomqtt
import pytest
from aiomqtt.exceptions import MqttConnectError
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
from pydantic import ValidationError

from py20305 import diagnostics
from py20305.diagnostics import DiagnosticsStore
from py20305.forwarders import (
    ConnectionTelemetryConfig,
    ConnectionTelemetryEmitter,
    ForwarderManager,
    MQTTForwarderAdapter,
)
from py20305.forwarders.base import (
    EventFrame,
    MessageDirection,
    MessageFrame,
    TelemetryFrame,
    TelemetryPoint,
)
from py20305.forwarders.config import MQTTForwarderConfig
from py20305.forwarders.mqtt_forwarder import _MAX_PUBLISH_ATTEMPTS, MQTTForwarder

# paho-mqtt result codes, as aiomqtt reports them.
MQTT_ERR_NO_CONN = 4
MQTT_ERR_CONN_LOST = 7

MESSAGE_TOPIC = "site/out/2030-5-raw"
TELEMETRY_TOPIC = "site/out/telemetry"
DENIED_TOPIC = "site/denied"


class FakeBroker:
    """The broker behind the fake aiomqtt module, and the clients connected to it."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []
        self.published: list[tuple[str, bytes]] = []
        #: Decides whether the next connect succeeds.
        self.accept: Callable[[], bool] = lambda: True
        #: When set, each connect waits for it before answering.
        self.connect_gate: asyncio.Event | None = None
        #: Raised once by the next publish, when set.
        self.publish_error: Exception | None = None
        #: Raised by a refused connect.
        self.refusal: Exception = aiomqtt.MqttError("[Errno 111] Connection refused")
        #: When set, each connect runs in an executor thread that waits for it,
        #: the way aiomqtt runs paho's blocking connect.
        self.connect_thread_gate: threading.Event | None = None
        #: When true, the broker closes each connection right after accepting it.
        self.drop_after_connect = False
        #: A publish to this topic makes the broker close the connection.
        self.closing_topic: str | None = None
        #: How many more publishes to ``closing_topic`` close the connection.
        #: None means every one does.
        self.closes_left: int | None = None
        #: When set, each publish takes a permit before it completes.
        self.publish_permits: asyncio.Semaphore | None = None

    def refuse_next(self, count: int, error: Exception | None = None) -> None:
        """Refuse the next ``count`` connects, then accept.

        ``error`` replaces the default connection refused error.
        """
        if error is not None:
            self.refusal = error
        remaining = [count]

        def accept() -> bool:
            if remaining[0] > 0:
                remaining[0] -= 1
                return False
            return True

        self.accept = accept

    def drop(self, *, signal: bool = True) -> None:
        """Close the live connections from the broker side.

        With ``signal``, the client resolves its disconnect future the way
        aiomqtt does when the socket closes. Without it, the loss shows only
        when the next publish fails.
        """
        for client in self.clients:
            if not client.connected:
                continue
            client.connected = False
            if signal:
                client._disconnected.set_exception(
                    aiomqtt.MqttCodeError(MQTT_ERR_CONN_LOST, "Unexpected disconnection")
                )

    def payloads_on(self, topic: str) -> list[bytes]:
        """Return the payloads published to ``topic``, in order."""
        return [payload for published_topic, payload in self.published if published_topic == topic]


class FakePaho:
    """Stands in for the paho client inside aiomqtt, which holds the broker session."""

    def __init__(self) -> None:
        self.session_open = False
        #: True once a disconnect closed an open session.
        self.closed_open_session = False

    def disconnect(self) -> None:
        # Like paho, a disconnect before the connect opened the session has
        # no effect: the connect that follows resets the state.
        if self.session_open:
            self.closed_open_session = True
        self.session_open = False


class FakeClient:
    """Implements the part of ``aiomqtt.Client`` the forwarder uses."""

    def __init__(
        self,
        broker: FakeBroker,
        *,
        hostname: str,
        port: int,
        tls_context: Any,
        identifier: str,
    ) -> None:
        self.broker = broker
        self.hostname = hostname
        self.port = port
        self.tls_context = tls_context
        self.identifier = identifier
        self.connected = False
        self.closed = False
        self._client = FakePaho()
        self._disconnected: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        broker.clients.append(self)

    def _client_connect(self) -> None:
        """Open the session. aiomqtt runs this in an executor thread."""
        gate = self.broker.connect_thread_gate
        if gate is not None:
            gate.wait(timeout=10)
        self._client.session_open = True

    async def __aenter__(self) -> FakeClient:
        if self.broker.connect_gate is not None:
            await self.broker.connect_gate.wait()
        if self.broker.connect_thread_gate is not None:
            await asyncio.get_running_loop().run_in_executor(None, self._client_connect)
        if not self.broker.accept():
            raise self.broker.refusal
        self.connected = True
        self._client.session_open = True
        if self.broker.drop_after_connect:
            asyncio.get_running_loop().call_soon(self.broker.drop)
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True
        self.connected = False
        self._client.session_open = False

    async def publish(self, topic: str, payload: bytes, qos: int = 0) -> None:
        if self.broker.publish_permits is not None:
            await self.broker.publish_permits.acquire()
        if self.broker.publish_error is not None:
            error, self.broker.publish_error = self.broker.publish_error, None
            raise error
        if self.connected and topic == self.broker.closing_topic:
            if self.broker.closes_left is None:
                self.broker.drop()
            elif self.broker.closes_left > 0:
                self.broker.closes_left -= 1
                self.broker.drop()
        if not self.connected:
            # What aiomqtt raises when paho reports MQTT_ERR_NO_CONN.
            raise aiomqtt.MqttCodeError(MQTT_ERR_NO_CONN, "Could not publish message")
        self.broker.published.append((topic, payload))


class FakeTime:
    """A clock and a sleep for the forwarder. Each sleep advances the clock by its delay."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []
        #: When set, each sleep also waits for it.
        self.gate: asyncio.Event | None = None
        #: When set, each sleep calls it after recording the delay.
        self.on_sleep: Callable[[], None] | None = None

    def clock(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.now += delay
        if self.on_sleep is not None:
            self.on_sleep()
        if self.gate is not None:
            await self.gate.wait()
        else:
            await asyncio.sleep(0)


class Recorder:
    """Records the broker connection outcomes a forwarder reports."""

    def __init__(self) -> None:
        self.successes: list[dict[str, Any]] = []
        self.failures: list[dict[str, Any]] = []

    def record_connection(self, **kwargs: Any) -> None:
        self.successes.append(kwargs)

    def record_connection_failure(self, **kwargs: Any) -> None:
        self.failures.append(kwargs)


@pytest.fixture
def broker(monkeypatch: pytest.MonkeyPatch) -> FakeBroker:
    """Replace the aiomqtt module the forwarder imports with one backed by a fake broker."""
    fake_broker = FakeBroker()
    module = types.ModuleType("aiomqtt")
    module.Client = lambda **kwargs: FakeClient(fake_broker, **kwargs)  # type: ignore[attr-defined]
    module.MqttError = aiomqtt.MqttError  # type: ignore[attr-defined]
    module.MqttCodeError = aiomqtt.MqttCodeError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aiomqtt", module)
    return fake_broker


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> DiagnosticsStore:
    fresh = DiagnosticsStore()
    monkeypatch.setattr(diagnostics, "_store", fresh)
    return fresh


def make_forwarder(
    fake_time: FakeTime,
    *,
    jitter: float = 0.0,
    queue_size: int = 1000,
    telemetry_device_limit: int = 1000,
    **config: Any,
) -> MQTTForwarder:
    return MQTTForwarder(
        MQTTForwarderConfig(endpoint="broker.example.com", port=1883, topic_base="site", **config),
        queue_size=queue_size,
        telemetry_device_limit=telemetry_device_limit,
        sleep=fake_time.sleep,
        clock=fake_time.clock,
        jitter=lambda: jitter,
    )


def message(name: str) -> MessageFrame:
    return MessageFrame(direction=MessageDirection.UPSTREAM, message_type=name, content={})


def telemetry(device: str, value: float = 1.0) -> TelemetryFrame:
    return TelemetryFrame(
        device=device,
        points={"W": TelemetryPoint(value=value, source_timestamp=1.0, quality="good")},
        quality="good",
    )


async def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    """Wait until ``condition`` is true, failing the test after ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


async def assert_stops_promptly(forwarder: MQTTForwarder) -> None:
    """Stop ``forwarder`` and fail unless stop returns within half a second."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    # The outer bound only keeps a broken stop from hanging the test. It is
    # long enough that a stop it has to cut short still fails the check below.
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(forwarder.stop(), timeout=5.0)
    assert loop.time() - started < 0.5


def other_tasks() -> list[asyncio.Task[Any]]:
    """Return the unfinished tasks other than the running test."""
    current = asyncio.current_task()
    return [task for task in asyncio.all_tasks() if task is not current and not task.done()]


def published_types(broker: FakeBroker) -> list[str]:
    import json

    return [json.loads(payload)["message_type"] for payload in broker.payloads_on(MESSAGE_TOPIC)]


class TestReconnectAfterLoss:
    async def test_a_broker_disconnect_during_publish_reconnects_and_publishes_the_message(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.drop(signal=False)

        forwarder.queue_message(message("A"))
        await wait_until(lambda: published_types(broker) == ["A"])

        assert forwarder.connected is True
        first, second = broker.clients
        assert first.closed is True
        assert second.identifier == first.identifier
        assert second.tls_context is first.tls_context
        await forwarder.stop()

    async def test_the_not_currently_connected_error_marks_the_link_down(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        """Regression: this message contains "connected", not "connection"."""
        not_connected = aiomqtt.MqttCodeError(MQTT_ERR_NO_CONN, "Could not publish message")
        # Pins aiomqtt's text on purpose: the old check looked for the word
        # "connection" in it, and this text does not contain that word.
        assert str(not_connected) == "[code:4] The client is not currently connected."
        fake_time.gate = asyncio.Event()
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.drop(signal=False)

        forwarder.queue_message(message("A"))
        await wait_until(lambda: not forwarder.connected)

        stats = forwarder.get_statistics()
        assert stats["last_disconnect_reason"] == str(not_connected)
        assert stats["disconnects"] == 1
        await forwarder.stop()

    async def test_a_loss_the_client_signals_marks_the_link_down_without_a_publish(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        fake_time.gate = asyncio.Event()
        forwarder = make_forwarder(fake_time)
        await forwarder.start()

        broker.drop(signal=True)
        await wait_until(lambda: not forwarder.connected, timeout=0.5)

        assert broker.published == []
        await forwarder.stop()

    async def test_a_publish_error_that_is_not_a_connection_error_leaves_the_link_up(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.publish_error = ValueError("payload rejected")

        forwarder.queue_message(message("A"))
        forwarder.queue_message(message("B"))
        await wait_until(lambda: published_types(broker) == ["B"])

        stats = forwarder.get_statistics()
        assert forwarder.connected is True
        assert stats["disconnects"] == 0
        assert stats["publish_errors"] == 1
        assert len(broker.clients) == 1
        await forwarder.stop()

    async def test_a_telemetry_frame_whose_publish_failed_is_published_after_reconnect(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.drop(signal=False)

        forwarder.queue_telemetry(telemetry("device0"))
        await wait_until(lambda: len(broker.payloads_on(TELEMETRY_TOPIC)) == 1)

        assert forwarder.get_statistics()["reconnects"] == 1
        await forwarder.stop()

    async def test_a_telemetry_frame_whose_publish_failed_keeps_its_place_ahead_of_others(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        import json

        forwarder = make_forwarder(fake_time)
        broker.publish_permits = asyncio.Semaphore(0)
        await forwarder.start()
        forwarder.queue_telemetry(telemetry("device0"))
        forwarder.queue_telemetry(telemetry("device1"))
        await wait_until(lambda: forwarder.get_statistics()["telemetry_pending"] == 1)

        broker.drop(signal=False)
        for _ in range(10):
            broker.publish_permits.release()
        await wait_until(lambda: len(broker.payloads_on(TELEMETRY_TOPIC)) == 2)

        devices = [json.loads(p)["device"] for p in broker.payloads_on(TELEMETRY_TOPIC)]
        assert devices == ["device0", "device1"]
        await forwarder.stop()

    async def test_a_telemetry_frame_whose_publish_failed_is_dropped_when_the_map_is_full(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        fake_time.gate = asyncio.Event()
        forwarder = make_forwarder(fake_time, telemetry_device_limit=1)
        broker.publish_permits = asyncio.Semaphore(0)
        await forwarder.start()
        forwarder.queue_telemetry(telemetry("device0"))
        await wait_until(lambda: forwarder.get_statistics()["telemetry_pending"] == 0)
        forwarder.queue_telemetry(telemetry("device1"))

        broker.drop(signal=False)
        broker.publish_permits.release()
        await wait_until(lambda: not forwarder.connected)

        stats = forwarder.get_statistics()
        assert stats["telemetry_pending"] == 1
        assert stats["telemetry_dropped"] == 1
        await forwarder.stop()

    async def test_one_reconnect_runs_when_many_publishes_fail_together(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.drop(signal=True)
        names = [f"M{index}" for index in range(20)]
        for name in names:
            forwarder.queue_message(message(name))

        await wait_until(lambda: published_types(broker) == names)

        stats = forwarder.get_statistics()
        assert len(broker.clients) == 2
        assert stats["disconnects"] == 1
        assert stats["reconnects"] == 1
        await forwarder.stop()


class TestBackoff:
    async def test_the_delay_doubles_up_to_the_cap_and_resets_after_a_publish_succeeds(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()

        broker.refuse_next(8)
        broker.drop()
        await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 1)
        assert fake_time.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]
        assert forwarder.get_statistics()["reconnect_backoff_seconds"] == 0.0
        forwarder.queue_message(message("A"))
        await wait_until(lambda: published_types(broker) == ["A"])

        broker.drop()
        await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 2)
        assert fake_time.sleeps[9:] == [1.0]
        await forwarder.stop()

    async def test_the_delay_resets_after_the_link_stays_up_for_the_maximum_delay(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()

        broker.refuse_next(3)
        broker.drop()
        await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 1)
        fake_time.now += 60.0

        broker.drop()
        await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 2)
        assert fake_time.sleeps == [1.0, 2.0, 4.0, 8.0, 1.0]
        await forwarder.stop()

    async def test_a_broker_that_drops_each_connection_at_once_gets_a_growing_delay(
        self,
        broker: FakeBroker,
        fake_time: FakeTime,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        recorder = Recorder()
        forwarder = make_forwarder(fake_time)
        forwarder.connection_observer = recorder  # type: ignore[assignment]
        await forwarder.start()
        started_at = fake_time.now

        with caplog.at_level(logging.INFO, logger="py20305"):
            broker.drop_after_connect = True
            broker.drop()
            await wait_until(lambda: len(fake_time.sleeps) >= 40)
            broker.drop_after_connect = False
            await wait_until(lambda: forwarder.connected)
            elapsed = fake_time.now - started_at

        assert fake_time.sleeps[:8] == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        restored = [r.getMessage() for r in caplog.records if "restored" in r.getMessage()]
        # The first loss, then one reminder per five minutes at most.
        allowed = 1 + int(elapsed // 300)
        assert 1 <= len(warnings) <= allowed
        assert len(recorder.failures) == len(warnings)
        # Each reported reconnect follows a reported failure.
        assert len(restored) <= len(warnings)
        assert len(recorder.successes) == 1 + len(restored)
        assert forwarder.get_statistics()["disconnects"] >= 40
        await forwarder.stop()

    async def test_the_configured_delays_set_the_backoff(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(
            fake_time, reconnect_initial_delay_seconds=0.5, reconnect_max_delay_seconds=3.0
        )
        await forwarder.start()

        broker.refuse_next(4)
        broker.drop()
        await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 1)

        assert fake_time.sleeps == [0.5, 1.0, 2.0, 3.0, 3.0]
        await forwarder.stop()

    async def test_jitter_shortens_each_delay_by_at_most_a_fifth(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time, jitter=0.5)
        await forwarder.start()

        broker.refuse_next(1)
        broker.drop()
        await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 1)

        assert fake_time.sleeps == [pytest.approx(0.9), pytest.approx(1.8)]
        await forwarder.stop()


class TestStartupFailure:
    async def test_a_failed_first_connect_keeps_retrying_and_then_connects(
        self, broker: FakeBroker, fake_time: FakeTime, store: DiagnosticsStore
    ) -> None:
        broker.refuse_next(3)
        forwarder = make_forwarder(fake_time)

        await forwarder.start()
        assert forwarder.running is True
        assert forwarder.connected is False
        (error,) = store.snapshot()["errors"]
        assert "broker.example.com:1883" in error["message"]

        forwarder.queue_message(message("A"))
        await wait_until(lambda: published_types(broker) == ["A"])

        assert fake_time.sleeps == [1.0, 2.0, 4.0]
        stats = forwarder.get_statistics()
        assert stats["reconnects"] == 1
        assert stats["disconnects"] == 0
        assert store.snapshot()["errors"] == []
        (restored,) = store.snapshot()["info"]
        assert restored["details"]["attempts"] == 4
        await forwarder.stop()

    async def test_require_initial_connection_makes_start_raise_and_not_retry(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        broker.refuse_next(1)
        forwarder = make_forwarder(fake_time, require_initial_connection=True)

        with pytest.raises(aiomqtt.MqttError):
            await forwarder.start()

        assert forwarder.running is False
        assert other_tasks() == []
        assert len(broker.clients) == 1

    async def test_a_manager_does_not_mark_a_retrying_forwarder_failed(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        broker.refuse_next(1)
        fake_time.gate = asyncio.Event()
        adapter = MQTTForwarderAdapter(make_forwarder(fake_time))
        manager = ForwarderManager()
        manager.add_forwarder(adapter)

        await manager.start()
        assert manager.failed_forwarders() == []
        assert manager.can_deliver_events is False

        fake_time.gate.set()
        await wait_until(lambda: manager.can_deliver_events)
        await manager.stop()


class TestDeliveryReadiness:
    async def test_held_connection_events_wait_while_down_and_go_out_after_reconnect(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        import json

        fake_time.gate = asyncio.Event()
        forwarder = make_forwarder(fake_time)
        adapter = MQTTForwarderAdapter(forwarder)
        manager = ForwarderManager()
        manager.add_forwarder(adapter)
        emitter = ConnectionTelemetryEmitter(
            manager,
            ConnectionTelemetryConfig(enabled=True, coalesce_successes=False),
            product_version="1.0",
        )
        manager.connection_observer = emitter
        events_topic = "site/out/connection-events"

        await manager.start()
        # The first connection is reported before the manager is running, so
        # it is held until released.
        emitter.release_held()
        await wait_until(lambda: len(broker.payloads_on(events_topic)) == 1)
        assert adapter.can_deliver is True

        broker.drop()
        await wait_until(lambda: not adapter.can_deliver)
        assert manager.can_deliver_events is False
        assert emitter.held_events == 1
        emitter.release_held()
        assert emitter.held_events == 1
        assert forwarder.get_statistics()["events_queued"] == 1

        fake_time.gate.set()
        await wait_until(lambda: len(broker.payloads_on(events_topic)) == 3)
        assert adapter.can_deliver is True
        assert manager.can_deliver_events is True
        assert emitter.held_events == 0
        statuses = [json.loads(p)["status"] for p in broker.payloads_on(events_topic)]
        assert statuses == ["Success", "Failure", "Success"]
        await manager.stop()


class TestShutdown:
    async def test_stop_during_a_backoff_wait_returns_promptly_without_leaking_tasks(
        self, broker: FakeBroker, fake_time: FakeTime, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake_time.gate = asyncio.Event()  # never set: the wait lasts until stop
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.drop()
        await wait_until(lambda: len(fake_time.sleeps) == 1)

        with caplog.at_level(logging.ERROR, logger="asyncio"):
            await assert_stops_promptly(forwarder)
            gc.collect()

        assert forwarder.running is False
        assert other_tasks() == []
        assert "Task was destroyed" not in caplog.text

    async def test_stop_during_a_connect_attempt_returns_promptly_without_leaking_tasks(
        self, broker: FakeBroker, fake_time: FakeTime, caplog: pytest.LogCaptureFixture
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.connect_gate = asyncio.Event()  # never set: the attempt lasts until stop
        broker.drop()
        await wait_until(lambda: len(broker.clients) == 2)

        with caplog.at_level(logging.ERROR, logger="asyncio"):
            await assert_stops_promptly(forwarder)
            gc.collect()

        assert forwarder.running is False
        assert other_tasks() == []
        assert "Task was destroyed" not in caplog.text

    async def test_a_connect_that_completes_after_stop_is_closed(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        gate = threading.Event()
        broker.connect_thread_gate = gate
        try:
            broker.drop()
            await wait_until(lambda: len(broker.clients) == 2)
            late = broker.clients[1]

            await assert_stops_promptly(forwarder)
            assert late._client.session_open is False
            gate.set()
            await wait_until(lambda: late._client.closed_open_session)
        finally:
            gate.set()

        assert late._client.session_open is False
        assert other_tasks() == []

    async def test_stop_while_connected_still_drains_the_buffers(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        forwarder.queue_message(message("A"))

        await forwarder.stop()

        assert published_types(broker) == ["A"]


class TestReporting:
    async def test_a_long_outage_reports_each_transition_once_and_failures_at_a_throttled_rate(
        self,
        broker: FakeBroker,
        fake_time: FakeTime,
        store: DiagnosticsStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        outage_seconds = 9 * 3600
        recorder = Recorder()
        forwarder = make_forwarder(fake_time)
        forwarder.connection_observer = recorder  # type: ignore[assignment]
        await forwarder.start()
        assert len(recorder.successes) == 1

        with caplog.at_level(logging.INFO, logger="py20305"):
            ends_at = fake_time.now + outage_seconds
            broker.accept = lambda: fake_time.now >= ends_at
            broker.drop()
            for index in range(50):
                forwarder.queue_message(message(f"M{index}"))
            await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 1, timeout=20)

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
        lost = [m for m in warnings if "lost" in m]
        still_down = [m for m in warnings if "still unreachable" in m]
        restored = [m for m in infos if "restored" in m]

        assert len(lost) == 1
        assert len(restored) == 1
        assert "connection attempts" in restored[0]
        # One reminder per five minutes of a nine-hour outage, at most.
        assert 100 <= len(still_down) <= outage_seconds // 300
        assert len(warnings) == len(lost) + len(still_down)
        # Hundreds of attempts, but failure events only at the reminder rate.
        assert len(fake_time.sleeps) > 500
        assert len(recorder.failures) == len(lost) + len(still_down)
        assert len(recorder.successes) == 2
        # The outage warning is cleared once the link is back.
        assert store.snapshot()["warnings"] == []
        assert published_types(broker) == [f"M{index}" for index in range(50)]
        await forwarder.stop()

    async def test_a_full_queue_logs_one_warning_while_the_broker_is_down(
        self,
        broker: FakeBroker,
        fake_time: FakeTime,
        store: DiagnosticsStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        fake_time.gate = asyncio.Event()
        forwarder = make_forwarder(fake_time, queue_size=2)
        await forwarder.start()
        broker.drop()
        await wait_until(lambda: not forwarder.connected)

        with caplog.at_level(logging.WARNING, logger="py20305"):
            for index in range(10):
                forwarder.queue_message(message(f"M{index}"))

        full = [r for r in caplog.records if "queue full" in r.getMessage()]
        assert len(full) == 1
        assert forwarder.get_statistics()["messages_dropped"] == 8
        (entry,) = [w for w in store.snapshot()["warnings"] if "queue full" in w["message"]]
        assert entry["count"] == 8

        fake_time.gate.set()
        await wait_until(lambda: published_types(broker) == ["M8", "M9"])
        await forwarder.stop()

    async def test_a_refusal_for_bad_credentials_is_reported_as_an_error_and_retried(
        self,
        broker: FakeBroker,
        fake_time: FakeTime,
        store: DiagnosticsStore,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        not_authorized = MqttConnectError(ReasonCode(PacketTypes.CONNACK, "Not authorized"))
        forwarder = make_forwarder(fake_time)
        await forwarder.start()

        with caplog.at_level(logging.WARNING, logger="py20305"):
            broker.refuse_next(3, error=not_authorized)
            broker.drop()
            await wait_until(lambda: forwarder.get_statistics()["reconnects"] == 1)

        # Three refusals within 15 s: one error, not one per attempt.
        assert fake_time.sleeps == [1.0, 2.0, 4.0, 8.0]
        (error,) = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
        assert "refused the connection" in error
        assert "Not authorized" in error
        # The error is cleared once the connection is back.
        assert store.snapshot()["errors"] == []
        await forwarder.stop()

    async def test_a_queue_kept_full_by_a_slow_broker_logs_one_warning_until_it_drains(
        self,
        broker: FakeBroker,
        fake_time: FakeTime,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        permits = asyncio.Semaphore(0)
        broker.publish_permits = permits
        forwarder = make_forwarder(fake_time, queue_size=2)
        await forwarder.start()

        def queue_full_warnings() -> int:
            return len([r for r in caplog.records if "queue full" in r.getMessage()])

        with caplog.at_level(logging.WARNING, logger="py20305"):
            forwarder.queue_message(message("A"))
            await wait_until(lambda: forwarder.get_statistics()["capture_queue_size"] == 0)
            for name in ["B", "C", "D"]:
                forwarder.queue_message(message(name))
            assert queue_full_warnings() == 1

            # A publishes and the loop takes the next item, so the queue
            # never drains.
            permits.release()
            await wait_until(lambda: forwarder.get_statistics()["capture_queue_size"] == 1)
            for name in ["E", "F"]:
                forwarder.queue_message(message(name))
            assert queue_full_warnings() == 1

            for _ in range(10):
                permits.release()
            await wait_until(lambda: forwarder.get_statistics()["queue_size"] == 0)
            for name in ["G", "H", "I", "J"]:
                forwarder.queue_message(message(name))
            assert queue_full_warnings() == 2

        for _ in range(10):
            permits.release()
        await forwarder.stop()


class TestUndeliverableItem:
    async def test_an_item_whose_publish_closes_the_connection_is_dropped_after_the_limit(
        self, broker: FakeBroker, fake_time: FakeTime, caplog: pytest.LogCaptureFixture
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        broker.closing_topic = DENIED_TOPIC

        with caplog.at_level(logging.WARNING, logger="py20305"):
            forwarder.queue_event(EventFrame(payload={"n": 1}, topic_suffix="denied"))
            forwarder.queue_message(message("GOOD"))
            await wait_until(lambda: published_types(broker) == ["GOOD"])

        stats = forwarder.get_statistics()
        assert broker.payloads_on(DENIED_TOPIC) == []
        assert stats["queue_size"] == 0
        assert stats["publish_errors"] == _MAX_PUBLISH_ATTEMPTS
        assert stats["reconnects"] == _MAX_PUBLISH_ATTEMPTS
        dropped = [r.getMessage() for r in caplog.records if "Dropped" in r.getMessage()]
        assert len(dropped) == 1
        assert DENIED_TOPIC in dropped[0]
        await forwarder.stop()

    async def test_a_telemetry_frame_whose_publish_closes_the_connection_is_dropped(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()

        def queue_newer_frame() -> None:
            if len(fake_time.sleeps) == 1:
                forwarder.queue_telemetry(telemetry("device0", 2.0))

        # A newer frame for the same device, queued during the first wait,
        # counts as the same item.
        fake_time.on_sleep = queue_newer_frame
        broker.closing_topic = TELEMETRY_TOPIC
        forwarder.queue_telemetry(telemetry("device0", 1.0))
        await wait_until(
            lambda: (
                forwarder.connected
                and forwarder.get_statistics()["telemetry_pending"] == 0
                and forwarder.get_statistics()["publish_errors"] >= _MAX_PUBLISH_ATTEMPTS
            )
        )

        stats = forwarder.get_statistics()
        assert stats["telemetry_superseded"] == 1
        assert stats["reconnects"] == _MAX_PUBLISH_ATTEMPTS
        assert stats["publish_errors"] == _MAX_PUBLISH_ATTEMPTS
        forwarder.queue_message(message("A"))
        await wait_until(lambda: published_types(broker) == ["A"])
        assert forwarder.get_statistics()["reconnects"] == _MAX_PUBLISH_ATTEMPTS
        await forwarder.stop()

    async def test_the_attempt_count_resets_when_a_newer_frame_for_the_device_publishes(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()

        def queue_newer_frame() -> None:
            if len(fake_time.sleeps) == _MAX_PUBLISH_ATTEMPTS - 1:
                forwarder.queue_telemetry(telemetry("device0", 2.0))

        # The first frame fails twice. A newer frame replaces it during the
        # second wait and publishes.
        fake_time.on_sleep = queue_newer_frame
        broker.closing_topic = TELEMETRY_TOPIC
        broker.closes_left = _MAX_PUBLISH_ATTEMPTS - 1
        forwarder.queue_telemetry(telemetry("device0", 1.0))
        await wait_until(lambda: len(broker.payloads_on(TELEMETRY_TOPIC)) == 1)

        # One more failure for the device starts a new count.
        broker.closes_left = 1
        forwarder.queue_telemetry(telemetry("device0", 3.0))
        await wait_until(lambda: len(broker.payloads_on(TELEMETRY_TOPIC)) == 2)
        assert forwarder.get_statistics()["telemetry_superseded"] == 1
        await forwarder.stop()


class TestTlsReload:
    async def test_each_reconnect_attempt_loads_the_tls_files_again(
        self, broker: FakeBroker, fake_time: FakeTime, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        forwarder = make_forwarder(fake_time)
        loads: list[int] = []
        monkeypatch.setattr(forwarder, "_create_ssl_context", lambda: loads.append(1))
        await forwarder.start()
        assert len(loads) == 1

        broker.refuse_next(2)
        broker.drop()
        await wait_until(lambda: forwarder.connected and len(broker.clients) == 4)

        assert len(loads) == 4
        await forwarder.stop()


class TestStatistics:
    async def test_stats_report_disconnects_reconnects_reason_and_backoff(
        self, broker: FakeBroker, fake_time: FakeTime
    ) -> None:
        forwarder = make_forwarder(fake_time)
        stats = forwarder.get_statistics()
        assert stats["disconnects"] == 0
        assert stats["reconnects"] == 0
        assert stats["last_disconnect_reason"] is None
        assert stats["reconnect_backoff_seconds"] == 0.0

        fake_time.gate = asyncio.Event()
        await forwarder.start()
        broker.refuse_next(1)
        broker.drop()
        await wait_until(lambda: len(fake_time.sleeps) == 1)

        lost = aiomqtt.MqttCodeError(MQTT_ERR_CONN_LOST, "Unexpected disconnection")
        stats = forwarder.get_statistics()
        assert stats["connected"] is False
        assert stats["disconnects"] == 1
        assert stats["reconnects"] == 0
        assert stats["last_disconnect_reason"] == str(lost)
        assert stats["reconnect_backoff_seconds"] == 1.0

        fake_time.gate.set()
        await wait_until(lambda: forwarder.connected)
        stats = forwarder.get_statistics()
        assert stats["reconnects"] == 1
        assert stats["disconnects"] == 1
        assert stats["reconnect_backoff_seconds"] == 0.0
        await forwarder.stop()


class TestReconnectConfig:
    def test_the_defaults_are_one_second_and_one_minute(self) -> None:
        config = MQTTForwarderConfig(endpoint="broker.example.com")
        assert config.reconnect_initial_delay_seconds == 1.0
        assert config.reconnect_max_delay_seconds == 60.0
        assert config.require_initial_connection is False

    @pytest.mark.parametrize("delay", [0.0, -1.0, float("nan"), float("inf")])
    def test_a_delay_that_is_not_finite_and_positive_is_rejected(self, delay: float) -> None:
        with pytest.raises(ValidationError, match="reconnect delays"):
            MQTTForwarderConfig(
                endpoint="broker.example.com", reconnect_initial_delay_seconds=delay
            )

    def test_a_maximum_below_the_initial_delay_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="reconnect_max_delay_seconds"):
            MQTTForwarderConfig(
                endpoint="broker.example.com",
                reconnect_initial_delay_seconds=10.0,
                reconnect_max_delay_seconds=5.0,
            )


class TestUnexpectedLoopError:
    async def test_an_unexpected_error_in_the_publish_loop_pauses_before_the_next_pass(
        self, broker: FakeBroker, fake_time: FakeTime, store: DiagnosticsStore
    ) -> None:
        forwarder = make_forwarder(fake_time)
        await forwarder.start()
        failures = 3

        def failing_check() -> None:
            nonlocal failures
            if failures:
                failures -= 1
                raise RuntimeError("unexpected")

        forwarder._client_reported_loss = failing_check  # type: ignore[method-assign]
        forwarder.queue_message(message("A"))
        await wait_until(lambda: published_types(broker) == ["A"])

        assert fake_time.sleeps.count(1.0) >= 3
        assert forwarder.get_statistics()["publish_errors"] >= 3
        await forwarder.stop()
