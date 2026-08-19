"""Tests for the composition of the two telemetry managers.

Both managers are covered on their own elsewhere. What is proven here is that
something starts them, with the hrefs discovery found and the poll rates the
server asked for -- a manager that works and is never constructed posts
nothing.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from py20305.client.state import EndDeviceState
from py20305.models.sep import EndDevice1, Sfditype, TimeType
from py20305.telemetry import TelemetryCoordinator

LFDI_A = "a" * 40
LFDI_B = "b" * 40


def _write_client_cert(tmp_path: Path) -> Path:
    """A real self-signed certificate and key in one PEM.

    Real rather than a placeholder because ``build_client`` builds an SSL
    context from it, so a stub string fails before any wiring is reached.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "coordinator-test")])
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
    out = tmp_path / "client.pem"
    out.write_bytes(
        cert.public_bytes(serialization.Encoding.PEM)
        + key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return out


def _end_device(lfdi: str, *, post_rate: int | None = None) -> EndDeviceState:
    """An EndDevice as discovery leaves it, with every child href resolved."""
    return EndDeviceState(
        device=EndDevice1(
            href="/edev/1",
            post_rate=post_rate,
            s_fdi=Sfditype(value=0),
            changed_time=TimeType(value=0),
        ),
        href="/edev/1",
        lfdi=bytes.fromhex(lfdi),
        log_event_list_href="/edev/1/lel",
        der_capability_href="/der/1/dercap",
        der_settings_href="/der/1/derg",
        der_status_href="/der/1/ders",
        der_availability_href="/der/1/dera",
    )


def _client(tmp_path: Path, *, mup: str | None = "/mup", devices=(LFDI_A,), post_rate=None):
    from py20305.cli import build_client
    from py20305.config import ClientConfig

    cert = _write_client_cert(tmp_path)
    config = ClientConfig.model_validate(
        {
            "server": {"url": "https://server.example.com:8443"},
            "tls": {"client_cert": str(cert), "client_key": str(cert), "ca_cert": str(cert)},
            "devices": [{"type": "print_demo", "lfdi": lfdi} for lfdi in devices],
            "telemetry": {"enabled": True, "post_rate_seconds": 60},
        }
    )
    client, _ = build_client(config)
    client.state.mup_list_href = mup
    client.state.end_devices = {
        f"/edev/{n}": _end_device(lfdi, post_rate=post_rate)
        for n, lfdi in enumerate(devices, start=1)
    }
    return client, config


def _coordinator(client, config, **kw) -> TelemetryCoordinator:
    from py20305.cli import _connector_resolver

    return TelemetryCoordinator(
        client,
        lfdis=[device.lfdi for device in config.devices],
        connector_resolver=_connector_resolver(client.dispatcher),
        post_rate_seconds=config.telemetry.post_rate_seconds,
        der_capability_poll_rate_seconds=config.telemetry.der_capability_poll_rate_seconds,
        der_settings_poll_rate_seconds=config.telemetry.der_settings_poll_rate_seconds,
        **kw,
    )


class TestBothManagersStart:
    """The gap this exists to close: DerResourceManager had no call site."""

    @pytest.mark.asyncio
    async def test_der_resources_are_started_for_every_device(self, tmp_path):
        client, config = _client(tmp_path, devices=(LFDI_A, LFDI_B))
        client.state.end_devices = {
            "/edev/1": _end_device(LFDI_A),
            "/edev/2": _end_device(LFDI_B),
        }
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            assert coordinator.der_resources is not None
            assert set(coordinator.der_resources.active_devices) == {LFDI_A, LFDI_B}
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_the_discovered_der_hrefs_are_used(self, tmp_path):
        client, config = _client(tmp_path)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            state = coordinator.der_resources._devices[LFDI_A]
            assert state.capability_href == "/der/1/dercap"
            assert state.settings_href == "/der/1/derg"
            assert state.status_href == "/der/1/ders"
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_metering_receives_the_log_event_and_availability_hrefs(self, tmp_path):
        """Both were dropped by the runner, so neither resource was ever sent."""
        client, config = _client(tmp_path)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            state = coordinator.telemetry._devices[LFDI_A]
            assert state.log_event_list_href == "/edev/1/lel"
            assert state.der_availability_href == "/der/1/dera"
        finally:
            await coordinator.shutdown()


class TestMirrorUsagePointIsIndependent:
    """A server expects DERCapability whether or not it mirrors readings."""

    @pytest.mark.asyncio
    async def test_der_resources_run_without_a_mirror_usage_point_list(self, tmp_path, caplog):
        client, config = _client(tmp_path, mup=None)
        coordinator = _coordinator(client, config)
        with caplog.at_level("WARNING", logger="py20305.telemetry.coordinator"):
            coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            assert coordinator.telemetry is None
            assert coordinator.der_resources is not None
            assert coordinator.der_resources.active_devices == [LFDI_A]
            assert any("MirrorUsagePointList" in r.message for r in caplog.records)
        finally:
            await coordinator.shutdown()


class TestPostRate:
    """IEEE 2030.5 says the server's postRate governs subordinate resources."""

    @pytest.mark.asyncio
    async def test_the_servers_post_rate_wins(self, tmp_path):
        client, config = _client(tmp_path, post_rate=120)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            assert coordinator.telemetry._devices[LFDI_A].post_rate == 120
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_the_configured_rate_is_the_fallback(self, tmp_path):
        client, config = _client(tmp_path, post_rate=None)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            assert coordinator.telemetry._devices[LFDI_A].post_rate == 60
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_a_zero_post_rate_is_not_taken_literally(self, tmp_path):
        """Zero means the server declined to specify, not "post constantly"."""
        client, config = _client(tmp_path, post_rate=0)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            assert coordinator.telemetry._devices[LFDI_A].post_rate == 60
        finally:
            await coordinator.shutdown()


class TestRediscovery:
    """Hrefs captured once go stale when the server's topology moves."""

    @pytest.mark.asyncio
    async def test_moved_hrefs_are_picked_up(self, tmp_path):
        client, config = _client(tmp_path)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()
            moved = _end_device(LFDI_A)
            moved.der_capability_href = "/api/v2/der/1/dercap"
            moved.log_event_list_href = "/api/v2/edev/1/lel"
            client.state.end_devices = {"/edev/1": moved}

            await coordinator.restart_device_telemetry()

            assert (
                coordinator.der_resources._devices[LFDI_A].capability_href
                == "/api/v2/der/1/dercap"
            )
            assert (
                coordinator.telemetry._devices[LFDI_A].log_event_list_href
                == "/api/v2/edev/1/lel"
            )
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_a_late_mirror_usage_point_list_starts_metering(self, tmp_path):
        """The first DeviceCapability lacked the link; a later one has it."""
        client, config = _client(tmp_path, mup=None)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()
            assert coordinator.telemetry is None

            client.state.mup_list_href = "/mup"
            await coordinator.restart_device_telemetry()

            assert coordinator.telemetry is not None
            assert coordinator.telemetry._devices[LFDI_A].post_rate == 60
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_a_device_the_server_has_forgotten_stays_registered(self, tmp_path):
        """Configuration says what to manage; discovery says only where."""
        client, config = _client(tmp_path)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()
            client.state.end_devices = {}

            await coordinator.restart_device_telemetry()

            assert coordinator.der_resources.active_devices == [LFDI_A]
            assert coordinator.der_resources._devices[LFDI_A].capability_href is None
        finally:
            await coordinator.shutdown()


class TestDevicesComeAndGo:
    """A host application registers and drops devices while the client runs."""

    @pytest.mark.asyncio
    async def test_a_device_named_after_construction_is_registered(self, tmp_path):
        """Naming a device explicitly is the request; skipping it silently is not."""
        client, config = _client(tmp_path)
        client.state.end_devices["/edev/2"] = _end_device(LFDI_B)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry(LFDI_B)

            assert LFDI_B in coordinator.der_resources.active_devices
            assert LFDI_B in coordinator.telemetry._devices
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_stopping_a_device_drops_it_from_both_managers(self, tmp_path):
        client, config = _client(tmp_path)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()

            coordinator.stop_device_telemetry(LFDI_A)

            assert coordinator.der_resources.active_devices == []
            assert LFDI_A not in coordinator.telemetry._devices
        finally:
            await coordinator.shutdown()

    @pytest.mark.asyncio
    async def test_a_stopped_device_is_not_restarted_by_rediscovery(self, tmp_path):
        """Unregistered means removed, not merely idle until the next poll."""
        client, config = _client(tmp_path)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        try:
            coordinator.start_device_telemetry()
            coordinator.stop_device_telemetry(LFDI_A)

            await coordinator.restart_device_telemetry()

            assert coordinator.der_resources.active_devices == []
        finally:
            await coordinator.shutdown()


class TestShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_stops_both_schedulers(self, tmp_path):
        client, config = _client(tmp_path)
        coordinator = _coordinator(client, config)
        coordinator.setup()
        coordinator.start_device_telemetry()

        await coordinator.shutdown()

        assert not coordinator.telemetry._scheduler._tasks
        assert not coordinator.der_resources._scheduler._tasks

    @pytest.mark.asyncio
    async def test_shutdown_is_safe_before_setup(self, tmp_path):
        client, config = _client(tmp_path)

        await _coordinator(client, config).shutdown()


class TestRunnerWiring:
    """Proving the coordinator works is not proving the runner uses it."""

    @pytest.mark.asyncio
    async def test_the_runner_starts_der_resource_puts(self, tmp_path):
        from py20305.cli import _start_telemetry

        client, config = _client(tmp_path)
        coordinator = _start_telemetry(client, config)
        assert coordinator is not None
        try:
            assert coordinator.der_resources is not None
            assert coordinator.der_resources.active_devices == [LFDI_A]
        finally:
            await coordinator.shutdown()

    def test_nothing_starts_when_telemetry_is_off(self, tmp_path):
        from py20305.cli import _start_telemetry

        client, config = _client(tmp_path)
        config = config.model_copy(
            update={"telemetry": config.telemetry.model_copy(update={"enabled": False})}
        )

        assert _start_telemetry(client, config) is None

    @pytest.mark.asyncio
    async def test_rediscovery_reaches_the_coordinator(self, tmp_path):
        """The hook is wired at client construction, before the coordinator exists."""
        from py20305.cli import _start_telemetry, build_client
        from py20305.config import ClientConfig

        cert = _write_client_cert(tmp_path)
        config = ClientConfig.model_validate(
            {
                "server": {"url": "https://server.example.com:8443"},
                "tls": {"client_cert": str(cert), "client_key": str(cert), "ca_cert": str(cert)},
                "devices": [{"type": "print_demo", "lfdi": LFDI_A}],
                "telemetry": {"enabled": True, "post_rate_seconds": 60},
            }
        )
        client, _ = build_client(config)
        client.state.mup_list_href = "/mup"
        client.state.end_devices = {"/edev/1": _end_device(LFDI_A)}
        coordinator = _start_telemetry(client, config)
        assert coordinator is not None
        try:
            coordinator.restart_device_telemetry = AsyncMock()

            await client._on_structural_change()

            coordinator.restart_device_telemetry.assert_awaited_once()
        finally:
            await coordinator.shutdown()
