"""Run a client from a configuration file.

    py20305 --config client.yaml

What this adds over constructing a :class:`~py20305.client.csip_client.CsipClient`
yourself is the part a deployment needs and a library should not assume: reading
configuration from disk, configuring logging, retrying the initial connection
instead of exiting when the server is briefly unavailable, and shutting down
cleanly when the supervisor asks.

Deliberately not a supervisor itself. It retries the connection, but a crashed
process is left for systemd, Docker or whatever else is already responsible for
restarting it -- reimplementing that here would be a worse version of a solved
problem, and one that hides failures from the thing meant to observe them.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import ssl
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from py20305.client import CsipClient, TlsConfig
from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2Error,
    Sep2TlsError,
)
from py20305.config import ClientConfig, ConfigError, load_config
from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.security import compute_cert_fingerprint, compute_lfdi
from py20305.version_info import get_package_version, get_version_string

if TYPE_CHECKING:
    from py20305.api import ClientAPIService
    from py20305.config import LoggingConfig
    from py20305.forwarders import ForwarderConfig, ForwarderManager
    from py20305.telemetry.coordinator import TelemetryCoordinator

logger = logging.getLogger("py20305.cli")


@dataclass
class ConnectionState:
    """What the runner is doing about the upstream connection.

    Published on the app so the management API can report "disconnected,
    retrying" rather than simply failing to answer. That is the state an
    operator most wants to see, and the API is least useful if it is only
    available once the thing it reports on is already working.
    """

    phase: str = "connecting"
    detail: str | None = None
    attempts: int = 0
    retry_in_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "detail": self.detail,
            "attempts": self.attempts,
            "retry_in_seconds": self.retry_in_seconds,
        }

#: Exit codes. Distinguished so a supervisor can tell "this will never work"
#: from "the server was down", and not restart-loop on the former.
EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_CONNECT_FAILED = 3


def setup_logging(config: LoggingConfig) -> None:
    """Configure logging for the runner.

    Called only from here, never from library code, which uses ``getLogger``
    and leaves handler policy to whoever is embedding it. A library that
    configures the root logger takes that decision away from its host.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if config.file is not None:
        config.file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(config.file, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, config.level),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    # aiohttp logs every request at INFO, which buries this client's own
    # messages under traffic the operator did not ask to see.
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="py20305",
        description="Run an IEEE 2030.5 / CSIP client against a utility server.",
    )
    parser.add_argument(
        "-c", "--config", required=True, type=Path, help="Path to the configuration file"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the configuration and report the client identity, then exit.",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override the configured log level.",
    )
    parser.add_argument("--version", action="version", version=get_version_string())
    return parser.parse_args(argv)


def build_client(config: ClientConfig) -> tuple[CsipClient, str]:
    """Construct a client from configuration, returning it and its own LFDI."""
    tls = TlsConfig(
        client_cert=config.tls.client_cert,
        client_key=config.tls.client_key,
        ca_cert=config.tls.ca_cert,
        check_hostname=config.tls.check_hostname,
        additional_ciphers=config.tls.additional_ciphers,
    )
    own_lfdi = compute_lfdi(config.tls.client_cert.read_text())

    registry = ConnectorConfigRegistry(config.devices)

    sole_device = config.devices[0].lfdi if len(config.devices) == 1 else None
    holder: dict[str, CsipClient] = {}
    resolve_lfdi = make_lfdi_resolver(lambda: holder.get("client"), sole_device)

    # Built before the client, because the dispatcher takes the emitter and the
    # client takes the dispatcher. Both are None when nothing is configured,
    # and every path downstream of them is a no-op in that case.
    forwarder = build_forwarder(config, own_lfdi)
    telemetry = (
        DeviceTelemetryEmitter(
            forwarder, config.forwarders.device_telemetry, client_id=own_lfdi
        )
        if config.forwarders is not None
        else None
    )
    # Enabled with nowhere to publish. Device telemetry rides the forwarder's
    # transport rather than owning one, so this configuration silently records
    # nothing -- which looks exactly like a device that is never read or
    # written, and is worth saying out loud.
    if (
        config.forwarders is not None
        and config.forwarders.device_telemetry.enabled
        and forwarder is None
    ):
        logger.warning(
            "device telemetry is enabled but no forwarder is configured or enabled; "
            "nothing will be published. Configure `forwarders.mqtt` alongside it."
        )

    client = CsipClient(
        config.server.url,
        tls=tls,
        dispatcher=ConnectorDispatcher(
            registry, lfdi_resolver=resolve_lfdi, telemetry=telemetry
        ),
        dcap_path=config.server.dcap_path,
        registration_pins=config.registration_pins or None,
        server_2018_compat=config.server.server_2018_compat,
        use_server_time=config.server.use_server_time,
    )
    # The transport reads this to publish each exchange, and `run` reads it
    # back to start and stop the transport -- so the manager needs no second
    # channel out of here and this function keeps its shape.
    client.http.forwarder = forwarder

    # Connection telemetry: the client's own connection outcomes, on their own
    # topic. Attached through the observer seam so the client stays ignorant
    # of the forwarder package; the emitter rides the same transport as the
    # protocol messages.
    if config.forwarders is not None and config.forwarders.connection_telemetry.enabled:
        if forwarder is None:
            # Same silent-nothing failure mode as device telemetry above: an
            # enabled channel with no transport records nothing and looks like
            # a client that never connects.
            logger.warning(
                "connection telemetry is enabled but no forwarder is configured or "
                "enabled; nothing will be published. Configure `forwarders.mqtt` "
                "alongside it."
            )
        else:
            from py20305.forwarders.connection_telemetry import ConnectionTelemetryEmitter

            emitter = ConnectionTelemetryEmitter(
                forwarder,
                config.forwarders.connection_telemetry,
                product_version=get_package_version(),
            )
            parsed = urlparse(config.server.url)
            emitter.set_server(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                base_url=config.server.url,
            )
            client.http.connection_observer = emitter

    # Subscribe/notify. Attached after construction because the manager needs
    # the client's transport, which exists only once the client does. The
    # client starts and stops the listener with its own lifecycle.
    if config.subscription.enabled:
        from py20305.subscription.manager import SubscriptionManager
        from py20305.subscription.notification_server import NotificationServer

        notification_server = NotificationServer(
            host=config.subscription.notification_host,
            port=config.subscription.notification_port,
            tls=tls,
            client_cert_mode=config.subscription.notification_client_cert_mode,
        )
        # The validator guarantees external_host when enabled.
        external_host = config.subscription.notification_external_host
        assert external_host is not None
        manager = SubscriptionManager(
            client=client.http,
            notification_uri=notification_server.build_notification_uri(external_host),
            server_2018_compat=config.server.server_2018_compat,
            # A renewal answered 401/404 means the server has forgotten us;
            # rediscovery re-subscribes against its current state. Wrapped to
            # drop the bool trigger_rediscovery returns, which the manager's
            # callback contract does not carry.
            on_subscription_lost=_rediscover_quietly(client),
        )
        client.attach_subscriptions(manager, notification_server)
        logger.info(
            "subscriptions on; notifications delivered to %s",
            notification_server.build_notification_uri(external_host),
        )
    # Configuring a schema directory has to actually turn validation on.
    # Without this the validator stays unset and every forwarded frame is
    # reported valid, which is worse than not offering the setting at all.
    if config.forwarders is not None and config.forwarders.schema_dir is not None:
        client.http.set_schema_validator(str(config.forwarders.schema_dir))
    holder["client"] = client
    return client, own_lfdi


def build_forwarder(config: ClientConfig, own_lfdi: str) -> ForwarderManager | None:
    """Assemble the forwarder transport from configuration.

    Returns None when nothing is configured or every configured forwarder is
    disabled, so a deployment that does not forward never starts a broker
    connection and never imports the MQTT client.

    Args:
        config: The loaded configuration.
        own_lfdi: This client's own LFDI, used to attribute an exchange that
            names no device.
    """
    forwarders = config.forwarders
    if forwarders is None or not forwarders.has_enabled_forwarders():
        return None

    from py20305.forwarders import ForwarderManager, MQTTForwarder, MQTTForwarderAdapter

    manager = ForwarderManager()
    if forwarders.mqtt is not None and forwarders.mqtt.enabled:
        manager.add_forwarder(MQTTForwarderAdapter(MQTTForwarder(forwarders.mqtt)))
        logger.info(
            "forwarding to %s:%s under %s",
            forwarders.mqtt.endpoint,
            forwarders.mqtt.port,
            forwarders.mqtt.topic_base,
        )
    # Set after the forwarders are registered so it propagates to each of them.
    manager.client_lfdi = own_lfdi
    return manager


def make_lfdi_resolver(
    get_client: Callable[[], CsipClient | None], sole_device: str | None
) -> Callable[[str], str | None]:
    """Map a server-side EndDevice href to the LFDI of the device it addresses.

    A control names a device by href; the connector registry is keyed by LFDI.
    The discovered EndDevice carries both, so the mapping comes from what the
    server said rather than from a guess. Returning one configured LFDI for
    every href would mean that with two or more devices every control resolves
    to the wrong connector, or to none, and is silently never applied.

    ``sole_device`` is the fallback for the single-device case, where the
    mapping is unambiguous and discovery may not have run yet. With several
    devices there is no safe fallback, so an unknown href resolves to nothing
    and the dispatcher reports it rather than guessing.
    """

    def resolve(device_href: str) -> str | None:
        client = get_client()
        state = client.state.end_devices.get(device_href) if client is not None else None
        if state is not None:
            lfdi = state.lfdi
            return (lfdi.hex() if isinstance(lfdi, bytes) else str(lfdi)).lower()
        return sole_device

    return resolve


async def connect_with_retry(
    client: CsipClient,
    config: ClientConfig,
    stop: asyncio.Event | None = None,
    state: ConnectionState | None = None,
    reconnect: asyncio.Event | None = None,
) -> bool:
    """Connect, retrying with backoff while the server is unreachable.

    Returns True once connected, False if the attempt budget ran out or the
    caller asked to stop. A client on a gateway usually outlives the server's
    outages, so the default is to keep trying rather than exit and depend on
    something else noticing.

    Only a *transport* failure is retried: a connection error, or the TLS
    error the transport raises once its own handshake retries are exhausted.
    The HTTP layer classifies that second one as an unreachable peer, and it
    is raised for a server-side handshake problem as readily as a local one,
    so it is retried rather than treated as fatal. Certificate material that
    is genuinely unusable -- missing, malformed, or a key that does not match
    -- fails when the SSL context is built, before any of this, and is
    reported as a configuration fault there.

    A protocol or payload error is not retried. It produces the same result
    every time, so retrying only delays the operator seeing it -- and with the
    default of retrying forever, it would never be seen at all.

    ``stop`` is awaited alongside the backoff, so a shutdown signal during an
    outage takes effect immediately. Without it, a systemd stop would hang
    until the server came back, which is exactly when it will not.

    ``state`` is updated as the attempts proceed so the management API can
    report the outage, and ``reconnect`` short-circuits the wait when an
    operator asks for a retry now rather than at the end of the backoff.
    """
    delay = min(config.connection.initial_delay_seconds, config.connection.max_delay_seconds)
    attempt = 0

    while True:
        if stop is not None and stop.is_set():
            logger.info("stop requested before a connection was established")
            return False

        attempt += 1
        if state is not None:
            state.phase = "connecting"
            state.attempts = attempt
            state.retry_in_seconds = None
        try:
            await client.connect()
        except (Sep2ConnectionError, Sep2TlsError) as exc:
            budget = config.connection.max_attempts
            exhausted = budget and attempt >= budget
            if exhausted or not config.connection.retry_forever:
                logger.error("could not reach the server after %d attempt(s): %s", attempt, exc)
                if state is not None:
                    state.phase = "failed"
                    state.detail = str(exc)
                return False

            logger.warning(
                "connection attempt %d failed (%s); retrying in %.0fs", attempt, exc, delay
            )
            if state is not None:
                state.phase = "disconnected"
                state.detail = str(exc)
                state.retry_in_seconds = delay
            if await _sleep_or_stop(delay, stop, reconnect):
                if stop is not None and stop.is_set():
                    logger.info("stop requested while waiting to retry")
                    return False
                logger.info("reconnect requested; retrying now")
                if reconnect is not None:
                    reconnect.clear()
            delay = min(
                delay * config.connection.backoff_factor,
                config.connection.max_delay_seconds,
            )
        else:
            if attempt > 1:
                logger.info("connected after %d attempts", attempt)
            if state is not None:
                state.phase = "connected"
                state.detail = None
                state.retry_in_seconds = None
            return True


async def _sleep_or_stop(
    seconds: float, stop: asyncio.Event | None, reconnect: asyncio.Event | None = None
) -> bool:
    """Sleep, returning True if an event fired before the time elapsed."""
    events = [e for e in (stop, reconnect) if e is not None]
    if not events:
        await asyncio.sleep(seconds)
        return False

    waiters = [asyncio.create_task(event.wait()) for event in events]
    try:
        done, pending = await asyncio.wait(
            waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
        )
        return bool(done)
    finally:
        for task in waiters:
            task.cancel()


async def _register_if_needed(client: CsipClient, own_lfdi: str) -> None:
    """Register this client's EndDevice unless the server already has it.

    Unconditional registration would create a second EndDevice for the same
    physical client on every restart, and the utility would see one device as
    several. A server that refuses registration outright is not fatal: many
    are provisioned out of band, and the client still runs against them.
    """
    known = {
        (device.lfdi.hex() if isinstance(device.lfdi, bytes) else str(device.lfdi)).lower()
        for device in client.state.end_devices.values()
    }
    if own_lfdi.lower() in known:
        logger.info("EndDevice already registered for %s", own_lfdi)
        return

    try:
        href = await client.register_end_device(lfdi=own_lfdi, device_category=0)
    except Exception as exc:  # noqa: BLE001 - any failure here is non-fatal
        logger.warning(
            "in-band registration did not succeed (%s); continuing, since the server "
            "may expect this device to be provisioned out of band",
            exc,
        )
        return

    logger.info("registered EndDevice at %s", href)
    await client.trigger_rediscovery()


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """Ask the client to stop on SIGINT/SIGTERM.

    Setting an event rather than cancelling mid-operation lets the run loop
    finish what it is doing and shut down in order. SIGTERM is what a
    supervisor sends; it does not exist on Windows, and add_signal_handler is
    unimplemented there, so both are best-effort.
    """
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)


async def run(config: ClientConfig) -> int:
    """Run until stopped, returning the process exit code."""
    client, own_lfdi = build_client(config)
    logger.info("%s", get_version_string())
    logger.info("client LFDI: %s", own_lfdi)
    logger.info("server: %s", config.server.url)
    logger.info("devices: %d configured", len(config.devices))

    stop = asyncio.Event()
    reconnect = asyncio.Event()
    connection = ConnectionState()
    _install_signal_handlers(stop)

    # The try starts before the connect attempt, not after it. A client that
    # never reached the server still opened an HTTP session, and returning
    # early would leak it -- along with the connector any device configuration
    # had already constructed.
    api_task: asyncio.Task[None] | None = None
    api_service: ClientAPIService | None = None
    telemetry: TelemetryCoordinator | None = None
    # Started before the connect attempt, so the exchanges of a connection that
    # never succeeds are forwarded too -- those are the ones worth having.
    forwarder = client.http.forwarder
    retry_task: asyncio.Task[None] | None = None
    if forwarder is not None:
        await forwarder.start()
        # A broker that is briefly unreachable at boot -- which is exactly
        # where a broker restarted alongside this client would be -- leaves
        # its forwarder stopped while the manager reports itself running, and
        # every message after that is dropped. Retrying in the background is
        # what keeps that a delay rather than an outage for the process
        # lifetime.
        retry_task = asyncio.create_task(
            _retry_forwarders(forwarder, stop, config.forwarders)
        )
    try:
        # Started before connecting, not after. The API's whole value during an
        # outage is reporting that there is one -- and /reconnect exists to cut
        # the backoff short. Starting it only once connected means neither
        # works in the situation they were built for.
        api_task, api_service = await _serve_api(config, client, connection, reconnect)
        if config.api.enabled and api_task is None:
            return EXIT_CONFIG_ERROR

        # The connect loop is a task so the API can be watched alongside it.
        # With the default of retrying forever, an API that died during an
        # outage would otherwise go unnoticed until a connection succeeded --
        # which may be never, leaving the process alive without the interface
        # the operator configured and no error to say so.
        connect_task: asyncio.Task[bool] = asyncio.create_task(
            connect_with_retry(client, config, stop, connection, reconnect),
            name="connect",
        )
        watching: set[asyncio.Task[Any]] = {connect_task}
        if api_task is not None:
            watching.add(api_task)

        finished, _ = await asyncio.wait(watching, return_when=asyncio.FIRST_COMPLETED)

        if api_task is not None and api_task in finished:
            connect_task.cancel()
            api_error = None if api_task.cancelled() else api_task.exception()
            logger.error(
                "management API stopped before the client connected: %s",
                api_error or "no error",
            )
            return EXIT_CONNECT_FAILED

        if not await connect_task:
            # Being asked to stop is not a failure. Reporting one would make a
            # supervisor with Restart=on-failure restart a client the operator
            # had just stopped -- and during an outage, which is when a stop is
            # most likely to arrive mid-retry.
            if stop.is_set():
                return EXIT_OK
            return EXIT_CONNECT_FAILED

        if config.register_on_start:
            await _register_if_needed(client, own_lfdi)
        if config.server.poll_now_on_start:
            try:
                await client.poll_now()
            except Sep2Error as exc:
                # The server answered discovery and then went away. That is the
                # unreachable-server case, not an internal fault, so it gets the
                # documented exit code rather than a traceback and exit 1.
                logger.error("initial poll failed: %s", exc)
                return EXIT_CONNECT_FAILED

        # Started after discovery, because the server's MirrorUsagePointList
        # href comes from it. This is also what makes device reads happen at
        # all in the runner: without a metering cycle nothing calls the
        # connector's fetch_monitoring, so southbound telemetry would report
        # control writes and never the readings its documentation promises.
        telemetry = _start_telemetry(client, config, api_service)

        run_task: asyncio.Task[None] = asyncio.create_task(client.run(), name="csip-client")
        stop_task: asyncio.Task[bool] = asyncio.create_task(stop.wait(), name="stop-signal")

        # The API task is waited on too. Left out, a uvicorn that failed to
        # bind -- a port already in use is the ordinary case -- would leave the
        # process running with no management interface and no error, which is
        # the same thing the operator sees when the client itself is wedged.
        watched: set[asyncio.Task[Any]] = {run_task, stop_task}
        if api_task is not None:
            watched.add(api_task)

        done, pending = await asyncio.wait(watched, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()

        # Surface a crash in the run loop rather than reporting a clean exit.
        if (
            run_task in done
            and not run_task.cancelled()
            and (error := run_task.exception()) is not None
        ):
            logger.error("client stopped with an error: %s", error)
            return EXIT_CONNECT_FAILED

        if api_task is not None and api_task in done and not api_task.cancelled():
            api_error = api_task.exception()
            logger.error("management API stopped unexpectedly: %s", api_error or "no error")
            return EXIT_CONNECT_FAILED

        logger.info("stopping")
        return EXIT_OK
    finally:
        if telemetry is not None:
            # `shutdown`, not `stop_metering` per device: that one drops the
            # device's state and leaves the scheduler's tasks running until
            # something calls cancel_all, which nothing else here does.
            await telemetry.shutdown()
        if api_task is not None:
            api_task.cancel()
        if retry_task is not None:
            retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retry_task
        await client.shutdown()
        # After the client, so exchanges from its shutdown are queued before
        # the transport drains rather than after it has stopped accepting.
        if forwarder is not None:
            await forwarder.stop()
        logger.info("stopped")


def _rediscover_quietly(client: CsipClient) -> Callable[[], Awaitable[None]]:
    """Adapt trigger_rediscovery to the manager's no-result callback shape."""

    async def rediscover() -> None:
        await client.trigger_rediscovery()

    return rediscover


def _connector_resolver(dispatcher: ConnectorDispatcher) -> Callable[[str], Any]:
    """Resolve an LFDI to its connector, through the dispatcher's own registry.

    The metering cycle and the control path must share one registry: the proxy
    it hands out caches the constructed connector, and a second registry would
    open a second connection to the same device. ``aresolve`` is the async
    form, which keeps a first-touch Modbus scan off the event loop.
    """
    registry = dispatcher.registry

    def resolve(lfdi: str) -> Any:
        proxy = registry.get_connector(lfdi)
        if proxy is None:
            raise KeyError(f"no connector configured for {lfdi}")
        return proxy.aresolve()

    return resolve


def _attach_api_telemetry(
    api_service: ClientAPIService | None,
    coordinator: TelemetryCoordinator,
) -> None:
    """Point the management API at the managers the coordinator now holds.

    The API is served before discovery so it can report an outage, which means
    it was built before either manager existed. Called again after each
    rediscovery, because the metering manager can appear later.
    """
    if api_service is not None:
        api_service.attach_telemetry(coordinator.telemetry, coordinator.der_resources)


def _start_telemetry(
    client: CsipClient,
    config: ClientConfig,
    api_service: ClientAPIService | None = None,
) -> TelemetryCoordinator | None:
    """Begin reporting each configured device to the server.

    Both halves start together: readings mirrored as MirrorUsagePoints, and the
    DER resources PUT per device. A server exposing no MirrorUsagePointList
    stops the first only, which the coordinator says once rather than failing a
    cycle forever.

    Returns None when telemetry is off or no device is configured, because
    there is nothing to read in either case.
    """
    if not config.telemetry.enabled or not config.devices:
        return None

    dispatcher = client.dispatcher
    if not isinstance(dispatcher, ConnectorDispatcher):
        # An embedding caller may have supplied its own dispatcher, which owns
        # its device resolution. Reaching past it to build a second registry
        # would open a second connection per device.
        logger.warning("telemetry is enabled but this client has no connector registry")
        return None

    from py20305.telemetry import TelemetryCoordinator

    coordinator = TelemetryCoordinator(
        client,
        lfdis=[device.lfdi for device in config.devices],
        connector_resolver=_connector_resolver(dispatcher),
        post_rate_seconds=config.telemetry.post_rate_seconds,
        der_capability_poll_rate_seconds=config.telemetry.der_capability_poll_rate_seconds,
        der_settings_poll_rate_seconds=config.telemetry.der_settings_poll_rate_seconds,
        device_telemetry=dispatcher.telemetry,
    )
    coordinator.setup()
    coordinator.start_device_telemetry()
    _attach_api_telemetry(api_service, coordinator)

    # This hook *replaces* the client's own rediscovery on a structural
    # notification, so it has to perform it: restarting the managers against
    # state nobody rebuilt would re-read the hrefs that just changed, and the
    # late-MirrorUsagePointList path would keep seeing no link. The restart is
    # not done here -- it hangs off rediscovery below, which every rebuild
    # path reaches, including 404 and comms-loss recovery that no structural
    # notification precedes.
    async def on_structural_change() -> None:
        await client.trigger_rediscovery()

    async def on_rediscovered() -> None:
        await coordinator.restart_device_telemetry()
        # Re-read: a late MirrorUsagePointList creates the metering manager
        # after startup, and the service is holding the None it was given.
        _attach_api_telemetry(api_service, coordinator)

    # Attached after construction, not passed to the client's constructor,
    # because the coordinator reads the discovered state this function runs
    # after. Both look their target up on each call rather than binding once,
    # so replacing a method on the coordinator is honored.
    client.set_on_structural_change(on_structural_change)
    client.set_on_rediscovered(on_rediscovered)
    logger.info(
        "reporting %d device(s): readings and DERStatus every %ds, "
        "DERSettings every %ds, DERCapability every %ds",
        len(config.devices),
        config.telemetry.post_rate_seconds,
        config.telemetry.der_settings_poll_rate_seconds,
        config.telemetry.der_capability_poll_rate_seconds,
    )
    return coordinator


async def _retry_forwarders(
    forwarder: ForwarderManager,
    stop: asyncio.Event,
    config: ForwarderConfig | None,
) -> None:
    """Keep trying to start forwarders that are not running, until stopped.

    Only forwarders that failed are retried, so a healthy one is never
    restarted underneath its queue. The interval is deliberately unhurried:
    the failure this recovers from is a broker being down, and hammering it
    helps nobody.
    """
    interval = config.retry_interval_seconds if config is not None else 0
    if interval <= 0:
        return
    while not stop.is_set():
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval)
        if stop.is_set():
            return
        if not forwarder.failed_forwarders():
            continue
        recovered = await forwarder.retry_failed()
        if recovered:
            logger.info("forwarding resumed on %d forwarder(s)", recovered)


async def _serve_api(
    config: ClientConfig,
    client: CsipClient,
    connection: ConnectionState,
    reconnect: asyncio.Event,
) -> tuple[asyncio.Task[None] | None, ClientAPIService | None]:
    """Start the management API when configured.

    Returns None when it is not enabled, and also when it is enabled but
    cannot start -- the caller treats that second case as a startup failure,
    because a process reporting healthy while a component the operator
    explicitly configured is absent is worse than one that refuses to start.
    """
    if not config.api.enabled:
        return None, None

    try:
        import uvicorn

        from py20305.api import ClientAPIService, create_app
    except ImportError:
        logger.error(
            "api.enabled is set but the API dependencies are missing; "
            "install them with `pip install py20305[api]`"
        )
        return None, None

    # Serve *this* client. Passing a getter that always returns None would
    # leave every endpoint reporting "not_connected" for the lifetime of the
    # process, which looks like a broken client rather than a broken wiring.
    service = ClientAPIService(client=client)
    app = create_app(lambda: service)
    pem = config.tls.client_cert.read_text()
    app.state.lfdi = compute_lfdi(pem)
    app.state.fingerprint = compute_cert_fingerprint(pem)
    app.state.connection = connection
    app.state.reconnect_event = reconnect

    server = uvicorn.Server(
        uvicorn.Config(app, host=config.api.host, port=config.api.port, log_level="warning")
    )
    logger.info("management API on http://%s:%d", config.api.host, config.api.port)
    return asyncio.create_task(server.serve(), name="management-api"), service


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        # Before logging is configured, so this goes straight to stderr.
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.log_level:
        config = config.model_copy(
            update={"logging": config.logging.model_copy(update={"level": args.log_level})}
        )

    try:
        setup_logging(config.logging)
    except OSError as exc:
        # An unwritable log directory is a configuration fault like any other,
        # and it happens before logging exists to report it -- so this goes to
        # stderr and exits with the documented code rather than as a traceback.
        print(f"error: could not configure logging: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    if args.check:
        try:
            lfdi = compute_lfdi(config.tls.client_cert.read_text())
        except (OSError, ValueError) as exc:
            # ValueError as well as OSError: a certificate can be present and
            # unreadable *as a certificate*, which is a configuration problem
            # and should report as one rather than as a traceback.
            print(f"error: could not read the client certificate: {exc}", file=sys.stderr)
            return EXIT_CONFIG_ERROR
        print(f"configuration OK: {args.config}")
        print(f"  server      : {config.server.url}")
        print(f"  client LFDI : {lfdi}")
        print(f"  devices     : {len(config.devices)}")
        return EXIT_OK

    try:
        return asyncio.run(run(config))
    except (OSError, ValueError, ssl.SSLError) as exc:
        # The TLS material is only truly checked when it is loaded, which is
        # inside the run. A missing key, malformed PEM or a key that does not
        # match its certificate arrives here, and it is a configuration fault:
        # it deserves the documented exit code rather than a traceback and the
        # generic 1 that tells a supervisor nothing.
        #
        # Sep2TlsError is deliberately absent. The transport raises it once its
        # own handshake retries are exhausted, and the HTTP layer treats it as
        # an unreachable peer -- so a server-side handshake problem would stop
        # the process here with the documented systemd unit then refusing to
        # restart it. It is retried as a connection failure instead.
        logger.error("could not start: %s", exc)
        return EXIT_CONFIG_ERROR
    except KeyboardInterrupt:
        # Windows, where add_signal_handler is unavailable and Ctrl-C arrives
        # as an exception instead.
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
