"""Tests for southbound device telemetry.

Two layers, deliberately. The emitter tests pin what an envelope says; the
chokepoint tests drive a dispatcher and a reading source end to end and assert
an event came out. Only the second kind catches a wiring regression -- an
emitter can be perfect and reach nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from py20305.connectors.base import BaseConnector
from py20305.connectors.device_telemetry import (
    MAX_ERROR_CHARS,
    DeviceTelemetryEmitter,
    device_endpoint,
)
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.forwarders.base import EventFrame
from py20305.forwarders.config import DeviceTelemetryConfig
from py20305.forwarders.mqtt_forwarder import PROTOCOL_MESSAGE_TOPIC_SUFFIX
from py20305.readings import DirectConnectorSource

LFDI_A = "a" * 40


class RecordingForwarder:
    """Stands in for the forwarder manager, keeping what was queued."""

    def __init__(self) -> None:
        self.events: list[EventFrame] = []

    def queue_event(self, event: EventFrame) -> None:
        self.events.append(event)


class FakeTcpConnector:
    endpoint_id = "10.0.0.7:1502"
    telemetry_protocol = "modbus"


class FakeModbusConnector:
    telemetry_protocol = "modbus"


class FakeSerialConnector:
    endpoint_id = "rtu:/dev/ttyUSB0"


def body_of(event: EventFrame) -> dict[str, Any]:
    """The decoded message body inside a queued envelope."""
    return json.loads(event.payload["payload"]["data"])


def make_emitter(
    *, enabled: bool = True, topic_suffix: str = "", forwarder: Any = None
) -> tuple[DeviceTelemetryEmitter, RecordingForwarder]:
    fw = forwarder if forwarder is not None else RecordingForwarder()
    config = DeviceTelemetryConfig(enabled=enabled, topic_suffix=topic_suffix)
    return DeviceTelemetryEmitter(fw, config, client_id="site-a"), fw


# -- Endpoint resolution -----------------------------------------------------


class TestDeviceEndpoint:
    def test_tcp_endpoint_splits_host_and_port(self):
        endpoint = device_endpoint(FakeTcpConnector())
        assert endpoint is not None
        assert (endpoint.ip, endpoint.port) == ("10.0.0.7", 1502)

    def test_serial_device_reported_by_its_line(self):
        """RTU has no IP. Reporting the line is honest; inventing an address is not."""
        endpoint = device_endpoint(FakeSerialConnector())
        assert endpoint is not None
        assert endpoint.ip == "rtu:/dev/ttyUSB0"

    def test_serial_line_survives_a_colon_in_its_path(self):
        """The whole line is kept, rather than being split on its last colon.

        A by-path serial identifier contains colons, and its final segment can
        parse as an integer -- ``.../pci-0000:14``. Splitting on the last colon
        the way a ``host:port`` address is split would report a truncated line
        and a port number the device does not have.
        """
        connector = Mock()
        connector.endpoint_id = "rtu:/dev/serial/by-path/pci-0000:14"
        endpoint = device_endpoint(connector)
        assert endpoint is not None
        assert endpoint.ip == "rtu:/dev/serial/by-path/pci-0000:14"
        assert endpoint.port == 502

    def test_connector_without_an_address_yields_none(self):
        """A custom connector may expose nothing. That is not an error."""
        assert device_endpoint(object()) is None

    def test_non_string_endpoint_is_ignored(self):
        connector = Mock()
        connector.endpoint_id = 1502
        assert device_endpoint(connector) is None

    def test_unparseable_port_falls_back_to_the_modbus_default(self):
        connector = Mock()
        connector.endpoint_id = "device.local:not-a-port"
        endpoint = device_endpoint(connector)
        assert endpoint is not None
        assert endpoint.port == 502


# -- What an envelope says ---------------------------------------------------


class TestEmittedEnvelope:
    def test_a_read_is_upstream(self):
        """Direction follows the data, not the initiator."""
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1500}, connector=FakeTcpConnector(), lfdi=LFDI_A)
        assert fw.events[0].payload["direction"] == "upstream"

    def test_a_write_is_downstream(self):
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {"value": 80}, connector=FakeTcpConnector())
        assert fw.events[0].payload["direction"] == "downstream"

    def test_protocol_comes_from_the_connector(self):
        """A consumer tells the two halves apart by this field, so it must be true."""
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1500}, connector=FakeModbusConnector())
        assert fw.events[0].payload["protocol"] == "modbus"

    def test_a_connector_that_claims_nothing_is_not_called_modbus(self):
        """The demo connector reaches no wire; recording it as Modbus is a lie."""
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1500}, connector=object())
        assert fw.events[0].payload["protocol"] == "other"

    def test_an_unknown_protocol_does_not_break_the_data_path(self):
        emitter, fw = make_emitter()
        connector = Mock()
        connector.telemetry_protocol = "smoke-signals"
        emitter.record_read("dev1", {"W": 1500}, connector=connector)
        assert fw.events[0].payload["protocol"] == "other"

    def test_read_carries_the_points_verbatim(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1500, "PF": 0.98})
        body = body_of(fw.events[0])
        assert body["points"] == {"W": 1500, "PF": 0.98}
        assert body["operation"] == "read"

    def test_write_carries_its_control_and_params(self):
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {"value": 80})
        body = body_of(fw.events[0])
        assert body["control"] == "p_lim"
        assert body["params"] == {"value": 80}
        assert body["operation"] == "write"

    def test_lfdi_becomes_the_client_id_when_known(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1}, lfdi=LFDI_A)
        assert fw.events[0].payload["client_id"] == LFDI_A

    def test_device_identifies_the_message_when_no_lfdi_is_known(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1})
        assert fw.events[0].payload["client_id"] == "dev1"

    def test_events_default_to_the_protocol_message_topic(self):
        """Empty topic_suffix means "alongside the existing traffic"."""
        emitter, fw = make_emitter(topic_suffix="")
        emitter.record_read("dev1", {"W": 1})
        assert fw.events[0].topic_suffix == PROTOCOL_MESSAGE_TOPIC_SUFFIX

    def test_an_operator_can_separate_them(self):
        emitter, fw = make_emitter(topic_suffix="out/device")
        emitter.record_read("dev1", {"W": 1})
        assert fw.events[0].topic_suffix == "out/device"


# -- What is and is not reported ---------------------------------------------


class TestReportingRules:
    def test_rejected_write_is_reported_with_its_error(self):
        """The northbound side may still believe it succeeded."""
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {"value": 80}, error="modbus exception 2")
        assert len(fw.events) == 1
        assert fw.events[0].payload["is_valid"] is False
        assert fw.events[0].payload["validation_error"] == "modbus exception 2"
        assert body_of(fw.events[0])["error"] == "modbus exception 2"

    def test_successful_write_is_valid(self):
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {"value": 80})
        assert fw.events[0].payload["is_valid"] is True
        assert "error" not in body_of(fw.events[0])

    def test_empty_read_is_not_reported(self):
        """An empty envelope is indistinguishable from a device reporting all-zero."""
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {})
        assert fw.events == []

    def test_device_error_text_is_bounded(self):
        """It originates outside this process and lands on an operator's topic."""
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {}, error="x" * (MAX_ERROR_CHARS + 500))
        reported = body_of(fw.events[0])["error"]
        assert len(reported) < MAX_ERROR_CHARS + 100
        assert "truncated" in reported

    def test_off_by_default(self):
        """An operator who has not asked for this does not start publishing on upgrade."""
        assert DeviceTelemetryConfig().enabled is False

    def test_disabled_emits_nothing(self):
        emitter, fw = make_emitter(enabled=False)
        emitter.record_read("dev1", {"W": 1})
        emitter.record_write("dev1", "p_lim", {"value": 80})
        assert fw.events == []

    def test_without_a_forwarder_it_is_not_enabled(self):
        emitter = DeviceTelemetryEmitter(None, DeviceTelemetryConfig(enabled=True))
        assert emitter.enabled is False
        emitter.record_read("dev1", {"W": 1})  # must not raise

    def test_attach_forwarder_makes_it_live(self):
        """The transport is built after the client's components exist."""
        emitter = DeviceTelemetryEmitter(None, DeviceTelemetryConfig(enabled=True))
        fw = RecordingForwarder()
        emitter.attach_forwarder(fw)
        emitter.record_read("dev1", {"W": 1})
        assert len(fw.events) == 1


# -- Failure containment -----------------------------------------------------


class TestNeverRaisesIntoTheDataPath:
    def test_a_broken_transport_does_not_propagate(self):
        """Telemetry that can break a control write is worse than none."""
        broken = Mock()
        broken.queue_event.side_effect = RuntimeError("broker gone")
        emitter, _ = make_emitter(forwarder=broken)

        emitter.record_write("dev1", "p_lim", {"value": 80})

        assert emitter.emit_failures == 1

    def test_repeated_failures_are_counted(self):
        """Telemetry that stopped working must not look like nothing to report."""
        broken = Mock()
        broken.queue_event.side_effect = RuntimeError("broker gone")
        emitter, _ = make_emitter(forwarder=broken)

        for _ in range(3):
            emitter.record_read("dev1", {"W": 1})

        assert emitter.emit_failures == 3


# -- The assembled paths -----------------------------------------------------


class _WritableConnector(BaseConnector):
    """A connector whose control write can be made to fail."""

    endpoint_id = "10.0.0.7:1502"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.applied: list[dict[str, Any]] = []

    async def update_p_lim(self, params: dict[str, Any]) -> None:
        if self.fail:
            raise RuntimeError("device rejected it")
        self.applied.append(params)


def _registry_for(mapping: dict[str, Any]) -> Mock:
    registry = Mock()

    def get_connector(lfdi: str):
        connector = mapping.get(lfdi)
        if connector is None:
            return None
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=connector)
        return proxy

    registry.get_connector.side_effect = get_connector
    return registry


class TestWriteChokepoint:
    """Drives the dispatcher, not the emitter, so a lost call fails here."""

    @pytest.mark.asyncio
    async def test_applied_control_is_reported(self):
        connector = _WritableConnector()
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}), lambda _h: LFDI_A, telemetry=emitter
        )

        await dispatcher._apply_one(
            connector.update_p_lim,
            "update_p_lim",
            {"value": 80},
            lfdi=LFDI_A,
            origin=Mock(),
            label="dev1",
        )

        assert len(fw.events) == 1
        assert body_of(fw.events[0])["control"] == "p_lim"
        assert fw.events[0].payload["direction"] == "downstream"

    @pytest.mark.asyncio
    async def test_rejected_control_is_reported_and_still_raises(self):
        connector = _WritableConnector(fail=True)
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}), lambda _h: LFDI_A, telemetry=emitter
        )

        with pytest.raises(RuntimeError):
            await dispatcher._apply_one(
                connector.update_p_lim,
                "update_p_lim",
                {"value": 80},
                lfdi=LFDI_A,
                origin=Mock(),
                label="dev1",
            )

        assert len(fw.events) == 1
        assert fw.events[0].payload["is_valid"] is False

    @pytest.mark.asyncio
    async def test_device_address_comes_from_the_bound_method(self):
        """No signature widened to carry a connector through."""
        connector = _WritableConnector()
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}), lambda _h: LFDI_A, telemetry=emitter
        )

        await dispatcher._apply_one(
            connector.update_p_lim,
            "update_p_lim",
            {"value": 80},
            lfdi=LFDI_A,
            origin=Mock(),
            label="dev1",
        )

        # A write is sent *to* the device, so its address is the destination.
        assert fw.events[0].payload["destination"] == {"ip": "10.0.0.7", "port": 1502}

    @pytest.mark.asyncio
    async def test_without_telemetry_the_dispatcher_is_unchanged(self):
        connector = _WritableConnector()
        dispatcher = ConnectorDispatcher(_registry_for({LFDI_A: connector}), lambda _h: LFDI_A)

        await dispatcher._apply_one(
            connector.update_p_lim,
            "update_p_lim",
            {"value": 80},
            lfdi=LFDI_A,
            origin=Mock(),
            label="dev1",
        )

        assert connector.applied == [{"value": 80}]


class _ReadableConnector(BaseConnector):
    endpoint_id = "10.0.0.7:1502"

    def __init__(self, values: dict[str, Any] | None = None, fail: bool = False) -> None:
        self._values = values if values is not None else {"W": 1500}
        self.fail = fail

    async def fetch_monitoring(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("device unreachable")
        return self._values

    def reading_overrides(self) -> dict[str, Any]:
        return {}


class TestReadChokepoint:
    """Drives the reading source, not the emitter."""

    @pytest.mark.asyncio
    async def test_successful_read_is_reported(self):
        emitter, fw = make_emitter()
        source = DirectConnectorSource(lambda _d: _ReadableConnector(), telemetry=emitter)

        await source.read("dev1")

        assert len(fw.events) == 1
        assert body_of(fw.events[0])["points"] == {"W": 1500}
        assert fw.events[0].payload["direction"] == "upstream"

    @pytest.mark.asyncio
    async def test_failed_read_is_not_reported(self):
        """A connector that raised produced no reading; an envelope would invent one."""
        emitter, fw = make_emitter()
        source = DirectConnectorSource(lambda _d: _ReadableConnector(fail=True), telemetry=emitter)

        snapshot = await source.read("dev1")

        assert snapshot.error is not None
        assert fw.events == []

    @pytest.mark.asyncio
    async def test_empty_read_is_not_reported(self):
        emitter, fw = make_emitter()
        source = DirectConnectorSource(lambda _d: _ReadableConnector(values={}), telemetry=emitter)

        await source.read("dev1")

        assert fw.events == []

    @pytest.mark.asyncio
    async def test_without_telemetry_reads_are_unchanged(self):
        source = DirectConnectorSource(lambda _d: _ReadableConnector())
        snapshot = await source.read("dev1")
        assert snapshot.entries["W"].value == 1500


# -- Runner wiring -----------------------------------------------------------


def _write_client_cert(tmp_path) -> Path:
    """A real self-signed certificate and key in one PEM.

    Real rather than a placeholder because `build_client` builds an SSL context
    from it, so a stub string fails before any wiring is reached.
    """
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "wiring-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    path = tmp_path / "client.pem"
    path.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return path


class TestRunnerWiring:
    """The runner has to reach all of this, or none of it ships."""

    @staticmethod
    def _config(tmp_path, forwarders: dict[str, Any] | None) -> Any:
        from py20305.config import ClientConfig

        cert = _write_client_cert(tmp_path)
        raw: dict[str, Any] = {
            "server": {"url": "https://server.example.com:8443"},
            "tls": {
                "client_cert": str(cert),
                "client_key": str(cert),
                "ca_cert": str(cert),
            },
        }
        if forwarders is not None:
            raw["forwarders"] = forwarders
        return ClientConfig.model_validate(raw)

    def test_no_forwarders_section_builds_no_transport(self, tmp_path):
        from py20305.cli import build_client

        client, _ = build_client(self._config(tmp_path, None))
        assert client.http.forwarder is None

    def test_disabled_mqtt_builds_no_transport(self, tmp_path):
        """Nothing enabled means no broker connection is ever opened."""
        from py20305.cli import build_client

        config = self._config(
            tmp_path, {"mqtt": {"endpoint": "broker.example.com", "enabled": False}}
        )
        client, _ = build_client(config)
        assert client.http.forwarder is None

    def test_enabled_mqtt_is_reachable_from_the_transport(self, tmp_path):
        from py20305.cli import build_client

        config = self._config(tmp_path, {"mqtt": {"endpoint": "broker.example.com"}})
        client, own_lfdi = build_client(config)

        manager = client.http.forwarder
        assert manager is not None
        assert len(manager.forwarders) == 1
        assert manager.client_lfdi == own_lfdi

    def test_device_telemetry_reaches_the_dispatcher(self, tmp_path):
        """The emitter must arrive at the chokepoint, not merely be constructed."""
        from py20305.cli import build_client

        config = self._config(
            tmp_path,
            {
                "mqtt": {"endpoint": "broker.example.com"},
                "device_telemetry": {"enabled": True},
            },
        )
        client, _ = build_client(config)

        emitter = client._dispatcher._telemetry
        assert emitter is not None
        assert emitter.enabled is True

    def test_device_telemetry_off_by_default_in_the_runner(self, tmp_path):
        from py20305.cli import build_client

        config = self._config(tmp_path, {"mqtt": {"endpoint": "broker.example.com"}})
        client, _ = build_client(config)
        assert client._dispatcher._telemetry.enabled is False

    def test_broker_credentials_resolve_against_the_config_file(self, tmp_path):
        """A relative path means the same thing wherever the client is started."""
        from py20305.config import ClientConfig

        _write_client_cert(tmp_path)
        config = ClientConfig.model_validate(
            {
                "server": {"url": "https://s.example.com"},
                "tls": {
                    "client_cert": "client.pem",
                    "client_key": "client.pem",
                    "ca_cert": "client.pem",
                },
                "forwarders": {
                    "mqtt": {
                        "endpoint": "broker.example.com",
                        "ca_path": "broker-ca.pem",
                        "cert_path": "broker.pem",
                        "key_path": "broker.key",
                    }
                },
            }
        ).resolve_paths(tmp_path)

        mqtt = config.forwarders.mqtt
        assert mqtt.ca_path == (tmp_path / "broker-ca.pem").resolve()
        assert mqtt.cert_path == (tmp_path / "broker.pem").resolve()
        assert mqtt.key_path == (tmp_path / "broker.key").resolve()

    def test_unknown_forwarder_key_is_rejected(self, tmp_path):
        """A misspelled key must not silently do nothing."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            self._config(tmp_path, {"mqtt": {"endpoint": "b.example.com", "topic_bass": "x"}})


# -- Review findings ---------------------------------------------------------


class TestErrorTextIsBoundedEverywhere:
    """The envelope carries the device's error twice; both are the device's input."""

    def test_validation_error_is_bounded_too(self):
        """Capping only the payload leaves the other field unbounded.

        `validation_error` is a top-level envelope field, so a device returning
        a megabyte of text still puts a megabyte on the operator's topic if only
        the body copy is capped.
        """
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {}, error="x" * (MAX_ERROR_CHARS + 5000))

        reported = fw.events[0].payload["validation_error"]
        assert len(reported) < MAX_ERROR_CHARS + 100
        assert "truncated" in reported

    def test_both_copies_agree(self):
        """One bound, applied once -- not two independent caps that can drift."""
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {}, error="y" * (MAX_ERROR_CHARS + 5000))

        payload = fw.events[0].payload
        assert payload["validation_error"] == body_of(fw.events[0])["error"]

    def test_a_short_error_is_untouched(self):
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {}, error="modbus exception 2")

        assert fw.events[0].payload["validation_error"] == "modbus exception 2"


class TestTelemetryWithoutATransportIsAnnounced:
    """Enabling it with nowhere to publish looks identical to an idle device."""

    def test_it_warns_when_no_forwarder_is_configured(self, tmp_path, caplog):
        from py20305.cli import build_client

        config = TestRunnerWiring._config(tmp_path, {"device_telemetry": {"enabled": True}})
        with caplog.at_level("WARNING", logger="py20305.cli"):
            build_client(config)

        assert any("device telemetry is enabled" in r.message for r in caplog.records)

    def test_it_stays_quiet_when_a_transport_exists(self, tmp_path, caplog):
        from py20305.cli import build_client

        config = TestRunnerWiring._config(
            tmp_path,
            {"mqtt": {"endpoint": "broker.example.com"}, "device_telemetry": {"enabled": True}},
        )
        with caplog.at_level("WARNING", logger="py20305.cli"):
            build_client(config)

        assert not any("device telemetry is enabled" in r.message for r in caplog.records)

    def test_it_stays_quiet_when_telemetry_is_off(self, tmp_path, caplog):
        from py20305.cli import build_client

        config = TestRunnerWiring._config(tmp_path, {"device_telemetry": {"enabled": False}})
        with caplog.at_level("WARNING", logger="py20305.cli"):
            build_client(config)

        assert not any("device telemetry is enabled" in r.message for r in caplog.records)


# -- The real transport, not a stand-in -------------------------------------


class TestEventsSurviveTheRealTransport:
    """A recording double proved the emitter works and the transport did not.

    Every test above hands the emitter a fake forwarder, so none of them
    noticed that the adapter the runner actually registers inherited a
    `queue_event` that drops the event. These drive the real objects.
    """

    @staticmethod
    def _real_stack():
        from py20305.forwarders import (
            ForwarderManager,
            MQTTForwarder,
            MQTTForwarderAdapter,
            MQTTForwarderConfig,
        )

        forwarder = MQTTForwarder(MQTTForwarderConfig(endpoint="broker.example.com"))
        adapter = MQTTForwarderAdapter(forwarder)
        manager = ForwarderManager()
        manager.add_forwarder(adapter)
        return manager, adapter, forwarder

    def test_the_adapter_carries_events_to_the_forwarder(self):
        """Inheriting the drop-by-default means the whole feature is a no-op."""
        manager, adapter, forwarder = self._real_stack()
        adapter._running = True
        forwarder._running = True

        emitter = DeviceTelemetryEmitter(
            manager, DeviceTelemetryConfig(enabled=True), client_id="site-a"
        )
        manager._running = True
        emitter.record_read("dev1", {"W": 1500}, connector=FakeTcpConnector())

        assert forwarder._queue.qsize() == 1, "the event never reached the MQTT forwarder"

    def test_the_queued_item_is_the_event(self):
        manager, adapter, forwarder = self._real_stack()
        adapter._running = True
        forwarder._running = True
        manager._running = True

        emitter = DeviceTelemetryEmitter(manager, DeviceTelemetryConfig(enabled=True))
        emitter.record_read("dev1", {"W": 1500})

        queued = forwarder._queue.get_nowait()
        assert isinstance(queued, EventFrame)
        assert queued.kind == "device-telemetry"

    def test_a_stopped_adapter_drops_rather_than_queues(self):
        manager, adapter, forwarder = self._real_stack()
        manager._running = True
        adapter._running = False

        emitter = DeviceTelemetryEmitter(manager, DeviceTelemetryConfig(enabled=True))
        emitter.record_read("dev1", {"W": 1500})

        assert forwarder._queue.qsize() == 0


class TestReadsAreReachableInProduction:
    """`DirectConnectorSource` is only ever built by `TelemetryManager`."""

    def test_the_manager_wires_the_emitter_into_the_source_it_builds(self):
        from unittest.mock import MagicMock

        from py20305.telemetry.manager import TelemetryManager

        emitter, _ = make_emitter()
        manager = TelemetryManager(
            client=MagicMock(),
            mup_list_href="/mup",
            connector_resolver=lambda _lfdi: _ReadableConnector(),
            device_telemetry=emitter,
        )

        assert manager._source._telemetry is emitter

    @pytest.mark.asyncio
    async def test_a_read_through_the_manager_is_reported(self):
        """End to end: construct it the way a consumer does, then read."""
        from unittest.mock import MagicMock

        from py20305.telemetry.manager import TelemetryManager

        emitter, fw = make_emitter()
        manager = TelemetryManager(
            client=MagicMock(),
            mup_list_href="/mup",
            connector_resolver=lambda _lfdi: _ReadableConnector(),
            device_telemetry=emitter,
        )

        await manager._source.read("dev1")

        assert len(fw.events) == 1
        assert body_of(fw.events[0])["points"] == {"W": 1500}

    def test_a_caller_supplied_source_is_left_alone(self):
        """It configures its own; overriding it would be surprising."""
        from unittest.mock import MagicMock

        from py20305.readings import DirectConnectorSource
        from py20305.telemetry.manager import TelemetryManager

        own = DirectConnectorSource(lambda _lfdi: _ReadableConnector())
        emitter, _ = make_emitter()
        manager = TelemetryManager(
            client=MagicMock(),
            mup_list_href="/mup",
            connector_resolver=lambda _lfdi: _ReadableConnector(),
            source=own,
            device_telemetry=emitter,
        )

        assert manager._source is own


class TestEndpointsFollowTheDirection:
    """Source is where an exchange came from; destination is where it went."""

    def test_a_read_has_the_device_as_source(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1}, connector=FakeTcpConnector())

        payload = fw.events[0].payload
        assert payload["source"] == {"ip": "10.0.0.7", "port": 1502}

    def test_a_write_has_the_device_as_destination(self):
        """The inverter receives the command; it does not issue it.

        Reporting the device as `source` for a write tells a collector the
        equipment originated the control, which inverts the fact the record
        exists to establish.
        """
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "p_lim", {"value": 80}, connector=FakeTcpConnector())

        payload = fw.events[0].payload
        assert payload["destination"] == {"ip": "10.0.0.7", "port": 1502}
        assert payload["source"] != payload["destination"]

    def test_both_directions_carry_both_ends(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"W": 1}, connector=FakeTcpConnector())
        emitter.record_write("dev1", "p_lim", {"value": 80}, connector=FakeTcpConnector())

        for event in fw.events:
            assert "source" in event.payload
            assert "destination" in event.payload


class TestConfiguredSchemaValidationIsTurnedOn:
    """An accepted setting that changes nothing is worse than no setting."""

    def test_schema_dir_reaches_the_validator(self, tmp_path):
        from unittest.mock import patch

        from py20305.cli import build_client

        schemas = tmp_path / "schemas"
        schemas.mkdir()
        config = TestRunnerWiring._config(
            tmp_path,
            {"mqtt": {"endpoint": "broker.example.com"}, "schema_dir": str(schemas)},
        )
        with patch("py20305.client.http.Sep2Client.set_schema_validator") as configure:
            build_client(config)

        configure.assert_called_once()
        assert str(schemas) in configure.call_args.args[0]

    def test_no_schema_dir_leaves_validation_alone(self, tmp_path):
        from unittest.mock import patch

        from py20305.cli import build_client

        config = TestRunnerWiring._config(tmp_path, {"mqtt": {"endpoint": "broker.example.com"}})
        with patch("py20305.client.http.Sep2Client.set_schema_validator") as configure:
            build_client(config)

        configure.assert_not_called()


# -- Recovering a forwarder whose broker was down ---------------------------


class TestForwarderRetry:
    """A broker down at boot must be a delay, not an outage for the process."""

    @staticmethod
    def _manager_with_failing_forwarder():
        from py20305.forwarders import ForwarderManager

        class Flaky:
            name = "flaky"

            def __init__(self) -> None:
                self.running = False
                self.attempts = 0

            async def start(self) -> None:
                self.attempts += 1
                if self.attempts < 2:
                    raise OSError("broker unreachable")
                self.running = True

            async def stop(self) -> None:
                self.running = False

            def queue_message(self, frame): ...
            def queue_event(self, event): ...
            def get_statistics(self): return {}

        manager = ForwarderManager()
        flaky = Flaky()
        manager.add_forwarder(flaky)
        return manager, flaky

    @pytest.mark.asyncio
    async def test_a_failed_start_leaves_the_forwarder_visible_as_failed(self):
        """The manager reports itself running even when nothing can deliver."""
        manager, flaky = self._manager_with_failing_forwarder()

        await manager.start()

        assert manager.running is True
        assert manager.failed_forwarders() == [flaky]

    @pytest.mark.asyncio
    async def test_retry_brings_it_back(self):
        manager, flaky = self._manager_with_failing_forwarder()
        await manager.start()

        recovered = await manager.retry_failed()

        assert recovered == 1
        assert manager.failed_forwarders() == []

    @pytest.mark.asyncio
    async def test_a_healthy_forwarder_is_not_restarted(self):
        """Restarting one underneath its queue would drop what it holds."""
        manager, flaky = self._manager_with_failing_forwarder()
        await manager.start()
        await manager.retry_failed()
        before = flaky.attempts

        await manager.retry_failed()

        assert flaky.attempts == before

    @pytest.mark.asyncio
    async def test_retry_survives_a_forwarder_that_keeps_failing(self):
        from py20305.forwarders import ForwarderManager

        class AlwaysDown:
            name = "down"
            running = False

            async def start(self) -> None:
                raise OSError("still down")

            async def stop(self) -> None: ...
            def queue_message(self, frame): ...
            def queue_event(self, event): ...
            def get_statistics(self): return {}

        manager = ForwarderManager()
        manager.add_forwarder(AlwaysDown())
        await manager.start()

        assert await manager.retry_failed() == 0


# -- The runner reads its devices -------------------------------------------


class TestRunnerReadsDevices:
    """Without a metering cycle nothing calls the connector, so nothing is read."""

    @staticmethod
    def _client_and_config(tmp_path, *, telemetry: bool, mup: str | None = "/mup"):
        from py20305.cli import build_client
        from py20305.config import ClientConfig

        cert = _write_client_cert(tmp_path)
        config = ClientConfig.model_validate(
            {
                "server": {"url": "https://server.example.com:8443"},
                "tls": {
                    "client_cert": str(cert),
                    "client_key": str(cert),
                    "ca_cert": str(cert),
                },
                "devices": [{"type": "print_demo", "lfdi": LFDI_A}],
                "telemetry": {"enabled": telemetry, "post_rate_seconds": 60},
                "forwarders": {
                    "mqtt": {"endpoint": "broker.example.com"},
                    "device_telemetry": {"enabled": True},
                },
            }
        )
        client, _ = build_client(config)
        client.state.mup_list_href = mup
        return client, config

    @pytest.mark.asyncio
    async def test_metering_starts_when_telemetry_is_enabled(self, tmp_path):
        from py20305.cli import _start_telemetry

        client, config = self._client_and_config(tmp_path, telemetry=True)
        coordinator = _start_telemetry(client, config)
        assert coordinator is not None
        manager = coordinator.telemetry

        assert manager is not None
        # Not just "a manager exists" -- every configured device has to be
        # metered, or the runner still reads nothing.
        assert set(manager._devices) == {d.lfdi.lower() for d in config.devices}
        for device in config.devices:
            manager.stop_metering(device.lfdi)

    @pytest.mark.asyncio
    async def test_it_reports_reads_through_the_same_emitter_as_writes(self, tmp_path):
        """Both halves of a device's traffic land on one channel."""
        from py20305.cli import _start_telemetry

        client, config = self._client_and_config(tmp_path, telemetry=True)
        coordinator = _start_telemetry(client, config)
        assert coordinator is not None
        manager = coordinator.telemetry

        assert manager is not None
        assert manager._source._telemetry is client.dispatcher.telemetry
        assert manager._source._telemetry is not None
        for device in config.devices:
            manager.stop_metering(device.lfdi)

    @pytest.mark.asyncio
    async def test_it_resolves_through_the_dispatcher_registry(self, tmp_path):
        """A second registry would open a second connection to the same device."""
        from py20305.cli import _connector_resolver

        client, _ = self._client_and_config(tmp_path, telemetry=True)
        resolve = _connector_resolver(client.dispatcher)

        connector = await resolve(LFDI_A)
        assert connector is not None
        # The same registry hands back the same instance, which is the point.
        assert await resolve(LFDI_A) is connector

    def test_nothing_starts_when_telemetry_is_off(self, tmp_path):
        from py20305.cli import _start_telemetry

        client, config = self._client_and_config(tmp_path, telemetry=False)
        assert _start_telemetry(client, config) is None

    @pytest.mark.asyncio
    async def test_no_metering_and_a_warning_when_the_server_offers_nowhere_to_post(
        self, tmp_path, caplog
    ):
        """A cycle that fails forever is worse than saying so once.

        The DER resource PUTs are a separate conversation and still start; only
        the readings have nowhere to go.
        """
        from py20305.cli import _start_telemetry

        client, config = self._client_and_config(tmp_path, telemetry=True, mup=None)
        with caplog.at_level("WARNING", logger="py20305.telemetry.coordinator"):
            coordinator = _start_telemetry(client, config)

        assert coordinator is not None
        try:
            assert coordinator.telemetry is None
            assert coordinator.der_resources is not None
            assert any("MirrorUsagePointList" in r.message for r in caplog.records)
        finally:
            await coordinator.shutdown()


class TestMeteringSurvivesRediscovery:
    """An upstream restart moves resource paths; a snapshot goes stale."""

    @pytest.mark.asyncio
    async def test_a_moved_mup_list_is_picked_up(self, tmp_path):
        from py20305.cli import _start_telemetry

        client, config = TestRunnerReadsDevices._client_and_config(tmp_path, telemetry=True)
        coordinator = _start_telemetry(client, config)
        assert coordinator is not None
        manager = coordinator.telemetry
        assert manager is not None
        try:
            assert manager._mup_list_href_source() == "/mup"

            client.state.mup_list_href = "/api/v2/mup"
            assert manager._mup_list_href_source() == "/api/v2/mup"
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_a_cleared_path_falls_back_to_the_last_known_one(self, tmp_path):
        """Rediscovery clears state before repopulating it; posting must not break."""
        from py20305.cli import _start_telemetry

        client, config = TestRunnerReadsDevices._client_and_config(tmp_path, telemetry=True)
        coordinator = _start_telemetry(client, config)
        assert coordinator is not None
        manager = coordinator.telemetry
        assert manager is not None
        try:
            client.state.mup_list_href = None
            assert manager._mup_list_href_source() == "/mup"
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_the_scheduler(self, tmp_path):
        """stop_metering drops device state and leaves the poll tasks running."""
        from py20305.cli import _start_telemetry

        client, config = TestRunnerReadsDevices._client_and_config(tmp_path, telemetry=True)
        coordinator = _start_telemetry(client, config)
        assert coordinator is not None

        await coordinator.shutdown()

        assert not coordinator.telemetry._scheduler._tasks
        assert not coordinator.der_resources._scheduler._tasks


class TestRunnerStopsMetering:
    """Proving shutdown works is not proving the runner calls it."""

    @pytest.mark.asyncio
    async def test_the_runner_shuts_the_metering_manager_down(self, tmp_path):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from py20305 import cli as cli_module

        client = MagicMock()
        client.http.forwarder = None
        client.connect = AsyncMock()
        client.run = AsyncMock(side_effect=lambda: asyncio.sleep(0))
        client.shutdown = AsyncMock()
        client.poll_now = AsyncMock()

        manager = MagicMock()
        manager.shutdown = AsyncMock()

        config = TestRunnerWiring._config(tmp_path, None)
        with (
            patch.object(cli_module, "build_client", return_value=(client, LFDI_A)),
            patch.object(cli_module, "_start_telemetry", return_value=manager),
            patch.object(cli_module, "_register_if_needed", new=AsyncMock()),
            patch.object(cli_module, "_install_signal_handlers"),
        ):
            await cli_module.run(config)

        manager.shutdown.assert_awaited_once()


class TestRunnerDrivesTheRetry:
    """The manager's retry is only useful if the packaged command runs it."""

    @staticmethod
    def _runner_pieces(tmp_path, interval, runtime=0.3):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from py20305.forwarders import ForwarderManager

        class Flaky:
            name = "flaky"

            def __init__(self) -> None:
                self.attempts = 0

            async def start(self) -> None:
                self.attempts += 1
                if self.attempts < 2:
                    raise OSError("broker unreachable")

            async def stop(self) -> None: ...
            def queue_message(self, frame): ...
            def queue_event(self, event): ...
            def get_statistics(self): return {}

        manager = ForwarderManager()
        flaky = Flaky()
        manager.add_forwarder(flaky)

        client = MagicMock()
        client.http.forwarder = manager
        client.connect = AsyncMock()
        # A plain callable returning a coroutine: an AsyncMock resolves
        # immediately, so `run` would return before the retry loop's first
        # interval elapsed and the test would prove nothing.
        client.run = lambda: asyncio.sleep(runtime)
        client.shutdown = AsyncMock()
        client.poll_now = AsyncMock()

        config = TestRunnerWiring._config(
            tmp_path,
            {"mqtt": {"endpoint": "broker.example.com"}, "retry_interval_seconds": interval},
        )
        return client, config, flaky

    @pytest.mark.asyncio
    async def test_a_failed_forwarder_is_retried_while_the_client_runs(self, tmp_path):
        from unittest.mock import AsyncMock, patch

        from py20305 import cli as cli_module

        client, config, flaky = self._runner_pieces(tmp_path, interval=1, runtime=2.5)
        with (
            patch.object(cli_module, "build_client", return_value=(client, LFDI_A)),
            patch.object(cli_module, "_register_if_needed", new=AsyncMock()),
            patch.object(cli_module, "_install_signal_handlers"),
        ):
            await cli_module.run(config)

        assert flaky.attempts >= 2, "the runner never retried the failed forwarder"
        assert client.http.forwarder.failed_forwarders() == []

    @pytest.mark.asyncio
    async def test_a_zero_interval_disables_retrying(self, tmp_path):
        """An operator who turns it off must not have it run anyway."""
        from unittest.mock import AsyncMock, patch

        from py20305 import cli as cli_module

        client, config, flaky = self._runner_pieces(tmp_path, interval=0)
        with (
            patch.object(cli_module, "build_client", return_value=(client, LFDI_A)),
            patch.object(cli_module, "_register_if_needed", new=AsyncMock()),
            patch.object(cli_module, "_install_signal_handlers"),
        ):
            await cli_module.run(config)

        assert flaky.attempts == 1


# -- The runner can subscribe -------------------------------------------------


class TestRunnerSubscriptionWiring:
    """Config must reach the client, or the packaged command stays poll-only."""

    @staticmethod
    def _config(tmp_path, sub: dict | None):
        from py20305.config import ClientConfig

        cert = _write_client_cert(tmp_path)
        raw: dict = {
            "server": {"url": "https://server.example.com:8443"},
            "tls": {
                "client_cert": str(cert),
                "client_key": str(cert),
                "ca_cert": str(cert),
            },
        }
        if sub is not None:
            raw["subscription"] = sub
        return ClientConfig.model_validate(raw)

    def test_enabled_wires_manager_and_listener(self, tmp_path):
        from py20305.cli import build_client

        config = self._config(
            tmp_path,
            {"enabled": True, "notification_external_host": "dut.example.com"},
        )
        client, _ = build_client(config)

        assert client.subscription_manager is not None
        assert client._notification_server is not None
        # The advertised callback carries the configured external host, not
        # the bind address -- a server cannot deliver to 0.0.0.0.
        assert "dut.example.com" in client.subscription_manager._notification_uri

    def test_listener_delivers_into_the_client(self, tmp_path):
        """The wiring the aggregator does by private pokes, now by the seam."""
        from py20305.cli import build_client

        config = self._config(
            tmp_path,
            {"enabled": True, "notification_external_host": "dut.example.com"},
        )
        client, _ = build_client(config)

        assert client._notification_server.on_notification == client._handle_notification

    def test_disabled_stays_poll_only(self, tmp_path):
        from py20305.cli import build_client

        client, _ = build_client(self._config(tmp_path, None))
        assert client.subscription_manager is None

    def test_enabled_without_external_host_is_a_config_error(self, tmp_path):
        """Advertising 0.0.0.0 would subscribe with an unreachable callback."""
        import pydantic

        with pytest.raises(pydantic.ValidationError):
            self._config(tmp_path, {"enabled": True})
