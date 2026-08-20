"""Tests for the command-line runner.

The runner is the part a deployment depends on and the library deliberately
does not do: reading configuration, configuring logging, surviving a server
that is not up yet, and stopping when asked. Each of those is a way a
production client fails at 3am, so each is tested here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from py20305 import cli as cli_module
from py20305.cli import (
    EXIT_CONFIG_ERROR,
    EXIT_CONNECT_FAILED,
    EXIT_OK,
    _register_if_needed,
    connect_with_retry,
    main,
    parse_args,
    setup_logging,
)
from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2PayloadError,
    Sep2ProtocolError,
    Sep2TlsError,
)
from py20305.config import ClientConfig, LoggingConfig

_CERT = """-----BEGIN CERTIFICATE-----
MIIBIjCBygIUJ4Wl3xL4qk8jvmH5nHwq2m8Vd6owCgYIKoZIzj0EAwIwFDESMBAG
A1UEAwwJdGVzdC1jZXJ0MB4XDTI2MDEwMTAwMDAwMFoXDTMwMDEwMTAwMDAwMFow
FDESMBAGA1UEAwwJdGVzdC1jZXJ0MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE
-----END CERTIFICATE-----
"""


def _config(tmp_path: Path, **overrides: object) -> ClientConfig:
    data: dict = {
        "server": {"url": "https://server.example.com:8443"},
        "tls": {
            "client_cert": str(tmp_path / "client.pem"),
            "client_key": str(tmp_path / "client.key"),
            "ca_cert": str(tmp_path / "ca.pem"),
        },
    }
    data.update(overrides)  # type: ignore[arg-type]
    return ClientConfig.model_validate(data)


class TestArgumentParsing:
    def test_config_is_required(self) -> None:
        with pytest.raises(SystemExit):
            parse_args([])

    def test_accepts_short_and_long_config_flags(self) -> None:
        assert parse_args(["-c", "a.yaml"]).config == Path("a.yaml")
        assert parse_args(["--config", "a.yaml"]).config == Path("a.yaml")

    def test_log_level_override_is_constrained(self) -> None:
        assert parse_args(["-c", "a.yaml", "--log-level", "DEBUG"]).log_level == "DEBUG"
        with pytest.raises(SystemExit):
            parse_args(["-c", "a.yaml", "--log-level", "CHATTY"])


class TestLoggingSetup:
    def test_writes_to_a_file_when_configured(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "client.log"
        setup_logging(LoggingConfig(level="INFO", file=target))
        logging.getLogger("py20305.test").info("hello from the runner")

        for handler in logging.getLogger().handlers:
            handler.flush()
        assert target.is_file(), "log file was not created"
        assert "hello from the runner" in target.read_text(encoding="utf-8")

    def test_creates_the_parent_directory(self, tmp_path: Path) -> None:
        """A packaged deployment's log directory may not exist on first boot."""
        target = tmp_path / "a" / "b" / "c.log"
        setup_logging(LoggingConfig(level="INFO", file=target))
        assert target.parent.is_dir()

    def test_level_is_applied(self, tmp_path: Path) -> None:
        setup_logging(LoggingConfig(level="WARNING", file=tmp_path / "x.log"))
        assert logging.getLogger().level == logging.WARNING


@pytest.mark.asyncio
class TestConnectWithRetry:
    async def test_returns_immediately_when_the_server_is_up(self, tmp_path: Path) -> None:
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock()
        assert await connect_with_retry(client, _config(tmp_path)) is True
        assert client.connect.await_count == 1

    async def test_retries_until_the_server_appears(self, tmp_path: Path) -> None:
        """A gateway client normally starts before the server is reachable."""
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(
            side_effect=[Sep2ConnectionError("refused"), Sep2ConnectionError("refused"), None]
        )
        config = _config(
            tmp_path, connection={"initial_delay_seconds": 0.01, "max_delay_seconds": 0.02}
        )

        with patch("py20305.cli.asyncio.sleep", new=AsyncMock()):
            assert await connect_with_retry(client, config) is True
        assert client.connect.await_count == 3

    async def test_gives_up_after_the_configured_budget(self, tmp_path: Path) -> None:
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2ConnectionError("refused"))
        config = _config(
            tmp_path,
            connection={"max_attempts": 3, "initial_delay_seconds": 0.01},
        )

        with patch("py20305.cli.asyncio.sleep", new=AsyncMock()):
            assert await connect_with_retry(client, config) is False
        assert client.connect.await_count == 3

    async def test_does_not_retry_when_told_not_to(self, tmp_path: Path) -> None:
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2ConnectionError("refused"))
        config = _config(tmp_path, connection={"retry_forever": False})

        assert await connect_with_retry(client, config) is False
        assert client.connect.await_count == 1

    async def test_backoff_grows_and_is_capped(self, tmp_path: Path) -> None:
        """Unbounded backoff would leave a client asleep for hours."""
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=[Sep2ConnectionError("x")] * 5 + [None])
        config = _config(
            tmp_path,
            connection={
                "initial_delay_seconds": 1.0,
                "backoff_factor": 10.0,
                "max_delay_seconds": 20.0,
            },
        )

        slept: list[float] = []
        async def record(seconds: float) -> None:
            slept.append(seconds)

        with patch("py20305.cli.asyncio.sleep", new=record):
            await connect_with_retry(client, config)

        assert slept[0] == 1.0
        assert max(slept) <= 20.0, f"backoff exceeded the cap: {slept}"
        assert slept == sorted(slept), f"backoff did not increase monotonically: {slept}"


@pytest.mark.asyncio
class TestRetryScope:
    """Retrying a permanent failure forever means never reporting it."""

    @pytest.mark.parametrize(
        "error",
        [
            Sep2ProtocolError("404 not found", 404),
            Sep2PayloadError("malformed XML", path="/dcap", body_length=12),
        ],
    )
    async def test_a_permanent_failure_is_not_retried(
        self, error: Exception, tmp_path: Path
    ) -> None:
        """A protocol or payload error means the same thing on every attempt.

        Sep2TlsError is deliberately absent from this list: the transport
        raises it once its own handshake retries are exhausted and the HTTP
        layer treats it as an unreachable peer, so it *is* retried. See
        TestTlsFailureIsReachability.
        """
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=error)

        with pytest.raises(type(error)):
            await connect_with_retry(client, _config(tmp_path))
        assert client.connect.await_count == 1, "a permanent failure was retried"

    async def test_a_stop_during_backoff_returns_promptly(self, tmp_path: Path) -> None:
        """A systemd stop during an outage must not wait for the server."""
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2ConnectionError("refused"))
        config = _config(tmp_path, connection={"initial_delay_seconds": 30.0})

        stop = asyncio.Event()

        async def request_stop() -> None:
            await asyncio.sleep(0)
            stop.set()

        asyncio.ensure_future(request_stop())
        result = await asyncio.wait_for(
            connect_with_retry(client, config, stop), timeout=5.0
        )
        assert result is False


class TestDeviceResolution:
    def test_each_device_resolves_to_its_own_connector(self, tmp_path: Path) -> None:
        """Returning one LFDI for every href silently misroutes every control.

        With two or more devices, a resolver that ignores the href sends every
        control to one connector -- or to none, since the registry is keyed by
        the configured LFDIs.
        """
        from py20305.cli import make_lfdi_resolver

        first, second = "a" * 40, "b" * 40
        one, two = MagicMock(), MagicMock()
        one.lfdi = bytes.fromhex(first)
        two.lfdi = bytes.fromhex(second)

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.state.end_devices = {"/edev/1": one, "/edev/2": two}

        resolve = make_lfdi_resolver(lambda: client, sole_device=None)
        assert resolve("/edev/1") == first
        assert resolve("/edev/2") == second, "both devices resolved to the same connector"

    def test_a_single_device_resolves_before_discovery(self) -> None:
        """The unambiguous case must still work with no discovered state."""
        from py20305.cli import make_lfdi_resolver

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.state.end_devices = {}
        resolve = make_lfdi_resolver(lambda: client, sole_device="a" * 40)
        assert resolve("/edev/1") == "a" * 40

    def test_an_unknown_href_resolves_to_nothing_with_several_devices(self) -> None:
        """Guessing here would apply a control to the wrong physical device."""
        from py20305.cli import make_lfdi_resolver

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.state.end_devices = {}
        assert make_lfdi_resolver(lambda: client, sole_device=None)("/edev/9") is None


@pytest.mark.asyncio
class TestRegistration:
    async def test_registers_when_the_server_does_not_know_this_client(self) -> None:
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.state.end_devices = {}
        client.register_end_device = AsyncMock(return_value="/edev/1")
        client.trigger_rediscovery = AsyncMock()

        await _register_if_needed(client, "a" * 40)
        client.register_end_device.assert_awaited_once()

    async def test_does_not_register_twice(self) -> None:
        """Registering an existing device creates a duplicate EndDevice."""
        known = MagicMock()
        known.lfdi = bytes.fromhex("a" * 40)
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.state.end_devices = {"/edev/1": known}
        client.register_end_device = AsyncMock()

        await _register_if_needed(client, "a" * 40)
        client.register_end_device.assert_not_awaited()

    async def test_matches_regardless_of_case(self) -> None:
        known = MagicMock()
        known.lfdi = "A" * 40
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.state.end_devices = {"/edev/1": known}
        client.register_end_device = AsyncMock()

        await _register_if_needed(client, "a" * 40)
        client.register_end_device.assert_not_awaited()

    async def test_a_refused_registration_is_not_fatal(self) -> None:
        """Many servers provision devices out of band and refuse in-band."""
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.state.end_devices = {}
        client.register_end_device = AsyncMock(side_effect=RuntimeError("403 forbidden"))

        await _register_if_needed(client, "a" * 40)  # must not raise


@pytest.mark.asyncio
class TestTlsFailureIsReachability:
    """A failed handshake is an unreachable peer, not a configuration verdict.

    The transport raises Sep2TlsError once its own handshake retries are
    exhausted, and the HTTP layer records it as unreachable. Treating it as a
    permanent configuration fault stops the process with exit 2 -- which the
    documented systemd unit then refuses to restart, on a server-side problem
    that may clear on its own.
    """

    async def test_a_handshake_failure_is_retried(self, tmp_path: Path) -> None:
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=[Sep2TlsError("handshake failed"), None])
        config = _config(tmp_path, connection={"initial_delay_seconds": 0.01})

        with patch.object(cli_module, "_sleep_or_stop", new=AsyncMock(return_value=False)):
            assert await connect_with_retry(client, config) is True
        assert client.connect.await_count == 2, "a handshake failure was treated as fatal"

    async def test_it_exits_unreachable_not_configuration(self, tmp_path: Path) -> None:
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2TlsError("handshake failed"))
        client.shutdown = AsyncMock()
        config = _config(tmp_path, connection={"retry_forever": False})

        with patch.object(cli_module, "build_client", return_value=(client, "a" * 40)):
            assert await cli_module.run(config) == EXIT_CONNECT_FAILED


@pytest.mark.asyncio
class TestApiFailureDuringOutage:
    async def test_an_api_that_dies_while_retrying_is_noticed(self, tmp_path: Path) -> None:
        """With retry_forever, nothing else would ever observe it."""
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2ConnectionError("refused"))
        client.shutdown = AsyncMock()
        config = _config(tmp_path, connection={"initial_delay_seconds": 60.0})

        async def dies_immediately() -> None:
            await asyncio.sleep(0)
            raise OSError("address already in use")

        api_task_holder: dict[str, asyncio.Task] = {}

        async def fake_serve(*_a: object, **_k: object) -> tuple[asyncio.Task[None], None]:
            task = asyncio.create_task(dies_immediately())
            api_task_holder["t"] = task
            return task, None

        with (
            patch.object(cli_module, "build_client", return_value=(client, "a" * 40)),
            patch.object(cli_module, "_serve_api", new=fake_serve),
        ):
            code = await asyncio.wait_for(cli_module.run(config), timeout=10.0)

        assert code == EXIT_CONNECT_FAILED, "a dead API during an outage went unnoticed"


@pytest.mark.asyncio
class TestOutageVisibility:
    """The API's value during an outage is reporting that there is one."""

    async def test_connection_state_is_published_while_retrying(self, tmp_path: Path) -> None:
        from py20305.cli import ConnectionState

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=[Sep2ConnectionError("refused"), None])
        config = _config(tmp_path, connection={"initial_delay_seconds": 0.01})
        state = ConnectionState()

        seen: list[str] = []
        original = cli_module._sleep_or_stop

        async def watch(seconds, stop, reconnect=None):  # type: ignore[no-untyped-def]
            seen.append(state.phase)
            return await original(0, stop, reconnect)

        with patch.object(cli_module, "_sleep_or_stop", watch):
            assert await connect_with_retry(client, config, None, state) is True

        assert "disconnected" in seen, f"never reported the outage: {seen}"
        assert state.phase == "connected"
        assert state.to_dict()["attempts"] == 2

    async def test_reconnect_cuts_the_backoff_short(self, tmp_path: Path) -> None:
        """An operator asking to retry now should not wait out the backoff."""
        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=[Sep2ConnectionError("refused"), None])
        config = _config(tmp_path, connection={"initial_delay_seconds": 60.0})
        reconnect = asyncio.Event()

        async def ask_soon() -> None:
            await asyncio.sleep(0)
            reconnect.set()

        asyncio.ensure_future(ask_soon())
        result = await asyncio.wait_for(
            connect_with_retry(client, config, None, None, reconnect), timeout=5.0
        )
        assert result is True
        assert client.connect.await_count == 2

    async def test_a_failed_state_is_reported_when_giving_up(self, tmp_path: Path) -> None:
        from py20305.cli import ConnectionState

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2ConnectionError("refused"))
        config = _config(tmp_path, connection={"retry_forever": False})
        state = ConnectionState()

        assert await connect_with_retry(client, config, None, state) is False
        assert state.phase == "failed"


class TestExitCodes:
    """A supervisor should be able to tell a permanent failure from a transient one."""

    def test_a_missing_config_exits_with_the_config_code(self, tmp_path: Path) -> None:
        assert main(["--config", str(tmp_path / "absent.yaml")]) == EXIT_CONFIG_ERROR

    def test_an_invalid_config_exits_with_the_config_code(self, tmp_path: Path) -> None:
        path = tmp_path / "client.json"
        path.write_text(json.dumps({"server": {"url": "http://not-tls"}}), encoding="utf-8")
        assert main(["--config", str(path)]) == EXIT_CONFIG_ERROR

    def test_check_validates_without_connecting(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "client.pem").write_text(_CERT, encoding="utf-8")
        path = tmp_path / "client.json"
        path.write_text(
            json.dumps(
                {
                    "server": {"url": "https://server.example.com:8443"},
                    "tls": {
                        "client_cert": "client.pem",
                        "client_key": "client.key",
                        "ca_cert": "ca.pem",
                    },
                }
            ),
            encoding="utf-8",
        )

        with patch("py20305.cli.compute_lfdi", return_value="b" * 40):
            code = main(["--config", str(path), "--check"])

        assert code == EXIT_OK
        out = capsys.readouterr().out
        assert "configuration OK" in out
        assert "b" * 40 in out, "--check must report the LFDI the utility has to register"

    def test_an_enabled_api_that_cannot_start_is_a_startup_failure(
        self, tmp_path: Path
    ) -> None:
        """Reporting healthy without a component the operator configured is worse
        than refusing to start: a supervisor sees nothing wrong."""
        path = tmp_path / "client.json"
        path.write_text(
            json.dumps(
                {
                    "server": {"url": "https://server.example.com:8443"},
                    "tls": {"client_cert": "c.pem", "client_key": "c.key", "ca_cert": "ca.pem"},
                    "api": {"enabled": True},
                }
            ),
            encoding="utf-8",
        )

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock()
        client.shutdown = AsyncMock()

        with (
            patch.object(cli_module, "build_client", return_value=(client, "a" * 40)),
            patch.object(cli_module, "_serve_api", new=AsyncMock(return_value=(None, None))),
        ):
            assert main(["--config", str(path)]) == EXIT_CONFIG_ERROR

    def test_an_unwritable_log_path_is_a_configuration_error(self, tmp_path: Path) -> None:
        """It happens before logging exists to report it."""
        path = tmp_path / "client.json"
        path.write_text(
            json.dumps(
                {
                    "server": {"url": "https://server.example.com:8443"},
                    "tls": {"client_cert": "c.pem", "client_key": "c.key", "ca_cert": "ca.pem"},
                    "logging": {"level": "INFO", "file": "logs/client.log"},
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            cli_module, "setup_logging", side_effect=OSError("read-only file system")
        ):
            assert main(["--config", str(path)]) == EXIT_CONFIG_ERROR

    def test_unusable_certificate_material_is_a_configuration_error(
        self, tmp_path: Path
    ) -> None:
        """A key that cannot be loaded fails when the SSL context is built."""
        path = tmp_path / "client.json"
        path.write_text(
            json.dumps(
                {
                    "server": {"url": "https://server.example.com:8443"},
                    "tls": {"client_cert": "c.pem", "client_key": "c.key", "ca_cert": "ca.pem"},
                }
            ),
            encoding="utf-8",
        )

        with patch.object(
            cli_module, "build_client", side_effect=ssl.SSLError("key values mismatch")
        ):
            assert main(["--config", str(path)]) == EXIT_CONFIG_ERROR

    def test_a_server_that_never_answers_exits_distinctly(self, tmp_path: Path) -> None:
        """Not the config code: the configuration may be perfect."""
        path = tmp_path / "client.json"
        path.write_text(
            json.dumps(
                {
                    "server": {"url": "https://server.example.com:8443"},
                    "tls": {"client_cert": "c.pem", "client_key": "c.key", "ca_cert": "ca.pem"},
                    "connection": {"retry_forever": False},
                }
            ),
            encoding="utf-8",
        )

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2ConnectionError("refused"))
        client.shutdown = AsyncMock()

        with patch("py20305.cli.build_client", return_value=(client, "a" * 40)):
            assert main(["--config", str(path)]) == EXIT_CONNECT_FAILED
        client.shutdown.assert_awaited(), "the client must be shut down even on a failed start"


@pytest.mark.asyncio
class TestGracefulShutdown:
    async def test_a_stop_signal_ends_the_run_and_shuts_down(self, tmp_path: Path) -> None:
        """Setting the event, rather than cancelling mid-operation, lets the
        client finish what it is doing and close its session in order."""
        from py20305 import cli

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock()
        client.poll_now = AsyncMock()
        client.shutdown = AsyncMock()
        client.state.end_devices = {}
        client.register_end_device = AsyncMock(return_value="/edev/1")
        client.trigger_rediscovery = AsyncMock()

        async def forever() -> None:
            await asyncio.Event().wait()

        client.run = forever

        stopper: dict[str, asyncio.Event] = {}

        def capture(stop: asyncio.Event) -> None:
            stopper["event"] = stop

        async def connected() -> None:
            # Let the run loop start, then ask it to stop.
            await asyncio.sleep(0)
            stopper["event"].set()

        client.connect = AsyncMock(side_effect=connected)

        with (
            patch.object(cli, "build_client", return_value=(client, "a" * 40)),
            patch.object(cli, "_install_signal_handlers", capture),
        ):
            assert await cli.run(_config(tmp_path)) == EXIT_OK

        client.shutdown.assert_awaited_once()

    async def test_stopping_during_an_outage_is_not_a_failure(self, tmp_path: Path) -> None:
        """A stop mid-retry must not report the failure code.

        A supervisor configured to restart on failure would otherwise restart
        a client the operator had just stopped -- during an outage, which is
        exactly when a stop is most likely to arrive mid-retry.
        """
        from py20305 import cli

        client = MagicMock()
        # build_client always sets this; None means nothing configured.
        client.http.forwarder = None
        client.connect = AsyncMock(side_effect=Sep2ConnectionError("refused"))
        client.shutdown = AsyncMock()

        def stop_immediately(stop: asyncio.Event) -> None:
            stop.set()

        with (
            patch.object(cli, "build_client", return_value=(client, "a" * 40)),
            patch.object(cli, "_install_signal_handlers", stop_immediately),
        ):
            assert await cli.run(_config(tmp_path)) == EXIT_OK

        client.shutdown.assert_awaited_once()
