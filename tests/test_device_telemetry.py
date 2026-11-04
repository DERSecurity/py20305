"""Tests for southbound device telemetry.

Two layers, deliberately. The emitter tests pin what an envelope says; the
chokepoint tests drive a dispatcher and a reading source end to end and assert
an event came out. Only the second kind catches a wiring regression -- an
emitter can be perfect and reach nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from py20305.commands import CommandOrigin
from py20305.connectors.base import BaseConnector, Reading
from py20305.connectors.device_telemetry import (
    MAX_ERROR_CHARS,
    DeviceTelemetryEmitter,
)
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.observers import DeviceRecord
from py20305.readings import DirectConnectorSource
from tests.legacy_readings import KEYS, readings_from


@pytest.fixture(autouse=True)
def _working_directory_in_tmp(tmp_path, monkeypatch):
    """Run in a scratch directory.

    The runner keeps its operations history in the working directory by
    default, and the tests here drive the runner.
    """
    monkeypatch.chdir(tmp_path)


LFDI_A = "a" * 40


class RecordingForwarder:
    """A device observer keeping every record in order."""

    def __init__(self) -> None:
        self.events: list[DeviceRecord] = []

    def __call__(self, record: DeviceRecord) -> None:
        self.events.append(record)


class FakeTcpConnector:
    endpoint_id = "10.0.0.7:1502"
    telemetry_protocol = "modbus"


class FakeModbusConnector:
    telemetry_protocol = "modbus"


class FakeSerialConnector:
    endpoint_id = "rtu:/dev/ttyUSB0"


def body_of(record: DeviceRecord) -> dict[str, Any]:
    """The fields of a device record a test reads, by their old names."""
    return {
        "control": record.resource,
        "params": dict(record.values),
        "points": dict(record.values),
        "error": record.error,
        "operation": str(record.operation),
    }


def make_emitter() -> tuple[DeviceTelemetryEmitter, RecordingForwarder]:
    fw = RecordingForwarder()
    return DeviceTelemetryEmitter(fw), fw


# -- Endpoint resolution -----------------------------------------------------


# -- What an envelope says ---------------------------------------------------


# -- What is and is not reported ---------------------------------------------


# -- Failure containment -----------------------------------------------------


# -- The assembled paths -----------------------------------------------------


class _WritableConnector(BaseConnector):
    """A connector whose control write can be made to fail."""

    endpoint_id = "10.0.0.7:1502"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.applied: list[dict[str, Any]] = []

    async def update_op_mod_max_lim_w(self, params: dict[str, Any]) -> None:
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
            connector.update_op_mod_max_lim_w,
            "update_op_mod_max_lim_w",
            {"value": 80},
            lfdi=LFDI_A,
            origin=Mock(),
            label="dev1",
        )

        assert len(fw.events) == 1
        assert body_of(fw.events[0])["control"] == "op_mod_max_lim_w"
        assert fw.events[0].operation == "write"

    @pytest.mark.asyncio
    async def test_rejected_control_is_reported_and_still_raises(self):
        connector = _WritableConnector(fail=True)
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}), lambda _h: LFDI_A, telemetry=emitter
        )

        with pytest.raises(RuntimeError):
            await dispatcher._apply_one(
                connector.update_op_mod_max_lim_w,
                "update_op_mod_max_lim_w",
                {"value": 80},
                lfdi=LFDI_A,
                origin=Mock(),
                label="dev1",
            )

        assert len(fw.events) == 1
        assert fw.events[0].outcome == "failed"

    @pytest.mark.asyncio
    async def test_device_address_comes_from_the_bound_method(self):
        """No signature widened to carry a connector through."""
        connector = _WritableConnector()
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}), lambda _h: LFDI_A, telemetry=emitter
        )

        await dispatcher._apply_one(
            connector.update_op_mod_max_lim_w,
            "update_op_mod_max_lim_w",
            {"value": 80},
            lfdi=LFDI_A,
            origin=Mock(),
            label="dev1",
        )

        # A write is sent *to* the device, so its address is the destination.
        assert fw.events[0].endpoint == "10.0.0.7:1502"

    @pytest.mark.asyncio
    async def test_without_telemetry_the_dispatcher_is_unchanged(self):
        connector = _WritableConnector()
        dispatcher = ConnectorDispatcher(_registry_for({LFDI_A: connector}), lambda _h: LFDI_A)

        await dispatcher._apply_one(
            connector.update_op_mod_max_lim_w,
            "update_op_mod_max_lim_w",
            {"value": 80},
            lfdi=LFDI_A,
            origin=Mock(),
            label="dev1",
        )

        assert connector.applied == [{"value": 80}]


class RecordingCommandObserver:
    """Keeps every command the dispatcher reports, for audit assertions."""

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    def record_command(
        self,
        device: str,
        control: str,
        params: Any,
        *,
        origin: Any,
        at: float,
        error: str | None = None,
    ) -> None:
        self.commands.append(
            {
                "device": device,
                "control": control,
                "params": dict(params),
                "origin": origin,
                "error": error,
            }
        )

    def record_readback(self, device: str, observed: Any, *, read_started_at: float) -> None:
        pass


class _NativeClearConnector:
    """A connector with its own single-round-trip clear, like a remote proxy."""

    endpoint_id = "10.0.0.7:1502"
    telemetry_protocol = "modbus"

    def __init__(self) -> None:
        self.cleared = 0

    async def clear_control(self) -> None:
        self.cleared += 1


class _FanOutConnector:
    """A connector with no native clear: the dispatcher disables mode by mode."""

    endpoint_id = "10.0.0.8:1502"
    telemetry_protocol = "modbus"

    def __init__(self) -> None:
        self.applied: list[tuple[str, dict[str, Any]]] = []

    async def update_op_mod_max_lim_w(self, params: dict[str, Any]) -> None:
        self.applied.append(("update_op_mod_max_lim_w", params))

    async def update_op_mod_freq_droop(self, params: dict[str, Any]) -> None:
        self.applied.append(("update_op_mod_freq_droop", params))


class TestClearControlAudit:
    """A clear reaches the device like any other write, so it is recorded like
    one. It is the comms-loss safe default -- the single control action most
    worth being able to account for after the fact."""

    @pytest.mark.asyncio
    async def test_native_clear_is_one_audited_command(self):
        connector = _NativeClearConnector()
        observer = RecordingCommandObserver()
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}),
            lambda _h: LFDI_A,
            command_observer=observer,
            telemetry=emitter,
        )

        await dispatcher.clear_control_by_lfdi(LFDI_A)

        assert connector.cleared == 1
        assert len(observer.commands) == 1
        assert observer.commands[0]["device"] == LFDI_A
        assert observer.commands[0]["control"] == "clear_control"
        assert observer.commands[0]["origin"] == CommandOrigin.COMMS_LOSS

    @pytest.mark.asyncio
    async def test_native_clear_keeps_the_connector_attribution(self):
        """The native path wraps the call in a lambda, which has no bound
        connector to introspect. Losing the attribution there would report the
        one write that most needs it as generic with no destination."""
        connector = _NativeClearConnector()
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}),
            lambda _h: LFDI_A,
            telemetry=emitter,
        )

        await dispatcher.clear_control_by_lfdi(LFDI_A)

        assert len(fw.events) == 1
        assert fw.events[0].protocol == "modbus"
        # A clear is written *to* the device, so its address is the destination.
        assert fw.events[0].endpoint == "10.0.0.7:1502"
        assert body_of(fw.events[0])["control"] == "clear_control"

    @pytest.mark.asyncio
    async def test_fan_out_records_every_disable_write(self):
        connector = _FanOutConnector()
        observer = RecordingCommandObserver()
        emitter, fw = make_emitter()
        dispatcher = ConnectorDispatcher(
            _registry_for({LFDI_A: connector}),
            lambda _h: LFDI_A,
            command_observer=observer,
            telemetry=emitter,
        )

        await dispatcher.clear_control_by_lfdi(LFDI_A)

        # The connector exposes two modes, so the fan-out disables exactly those.
        assert {name for name, _ in connector.applied} == {
            "update_op_mod_max_lim_w",
            "update_op_mod_freq_droop",
        }
        assert {c["control"] for c in observer.commands} == {
            "op_mod_max_lim_w",
            "op_mod_freq_droop",
        }
        assert all(c["origin"] == CommandOrigin.COMMS_LOSS for c in observer.commands)
        # And each write reaches the monitoring stream with the device's address.
        assert len(fw.events) == 2
        assert all(e.endpoint == "10.0.0.8:1502" for e in fw.events)


class _ReadableConnector(BaseConnector):
    endpoint_id = "10.0.0.7:1502"

    def __init__(self, values: dict[str, Any] | None = None, fail: bool = False) -> None:
        self._values = values if values is not None else {"W": 1500}
        self.fail = fail

    async def fetch_monitoring(self) -> list[Reading]:
        if self.fail:
            raise RuntimeError("device unreachable")
        return readings_from(self._values)


class TestReadChokepoint:
    """Drives the reading source, not the emitter."""

    @pytest.mark.asyncio
    async def test_successful_read_is_reported(self):
        emitter, fw = make_emitter()
        source = DirectConnectorSource(lambda _d: _ReadableConnector(), telemetry=emitter)

        await source.read("dev1")

        assert len(fw.events) == 1
        assert body_of(fw.events[0])["points"] == {KEYS["W"]: 1500}
        assert fw.events[0].operation == "read"

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
        assert snapshot.entries[KEYS["W"]].value == 1500


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
    """The runner builds a client with no observers attached."""

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

    def test_the_runner_attaches_no_observers(self, tmp_path):
        from py20305.cli import build_client

        client, _ = build_client(self._config(tmp_path, None))
        assert not client.observers.any
        assert client.dispatcher.telemetry is None

    def test_a_forwarders_section_is_refused_with_its_replacement_named(self, tmp_path):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="observers"):
            self._config(tmp_path, {"mqtt": {"endpoint": "broker.example.com"}})


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
        assert body_of(fw.events[0])["points"] == {KEYS["W"]: 1500}

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


# -- Recovering a forwarder whose broker was down ---------------------------


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


class TestDeviceRecords:
    def test_a_read_carries_the_values_and_the_connector(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"k": 1500}, connector=FakeTcpConnector(), lfdi=LFDI_A)

        (record,) = fw.events
        assert record.operation == "read"
        assert record.resource == "monitoring"
        assert dict(record.values) == {"k": 1500}
        assert record.protocol == "modbus"
        assert record.endpoint == "10.0.0.7:1502"
        assert record.lfdi == LFDI_A

    def test_a_connector_that_claims_nothing_is_generic(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {"k": 1}, connector=object())
        assert fw.events[0].protocol == "generic"
        assert fw.events[0].endpoint is None

    def test_a_write_carries_its_cause(self):
        emitter, fw = make_emitter()
        emitter.record_write(
            "dev1",
            "op_mod_max_lim_w",
            {"enabled": True, "value": 8000},
            origin="ieee2030_5",
            applied_mrid=b"\x01" * 16,
            cause_mrid=b"\x02" * 16,
        )

        (record,) = fw.events
        assert record.operation == "write"
        assert record.outcome == "ok"
        assert record.applied_mrid == "01" * 16
        assert record.cause_mrid == "02" * 16
        assert record.origin == "ieee2030_5"

    def test_a_refused_write_is_refused(self):
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "c", {}, error="no authority", error_type="refused")
        assert fw.events[0].outcome == "refused"

    def test_a_failed_write_keeps_its_error_bounded(self):
        emitter, fw = make_emitter()
        emitter.record_write("dev1", "c", {}, error="x" * (MAX_ERROR_CHARS + 5000))
        assert fw.events[0].outcome == "failed"
        assert len(fw.events[0].error) < MAX_ERROR_CHARS + 100
        assert "truncated" in fw.events[0].error

    def test_an_empty_read_is_not_reported(self):
        emitter, fw = make_emitter()
        emitter.record_read("dev1", {})
        assert fw.events == []

    def test_without_an_observer_nothing_is_reported(self):
        emitter = DeviceTelemetryEmitter()
        assert not emitter.enabled
        emitter.record_read("dev1", {"k": 1})
        emitter.record_write("dev1", "c", {})

    def test_an_observer_that_raises_is_counted(self):
        def broken(_record):
            raise RuntimeError("observer bug")

        emitter = DeviceTelemetryEmitter(broken)
        emitter.record_read("dev1", {"k": 1})
        emitter.record_write("dev1", "c", {})
        assert emitter.observer_failures == 2

    def test_record_values_cannot_be_changed(self):
        emitter, fw = make_emitter()
        values = {"k": 1}
        emitter.record_read("dev1", values)
        values["k"] = 2
        assert fw.events[0].values["k"] == 1
        with pytest.raises(TypeError):
            fw.events[0].values["k"] = 3  # type: ignore[index]
