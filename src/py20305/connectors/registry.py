"""Connector configuration registry with lazy instantiation.

Loads connector definitions from typed DeviceConfig objects and materializes
them lazily on first access. Supports case-insensitive LFDI lookup.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

# How long the proxy remembers a *permanent* construction failure before
# letting the polling cycles try again. 5 minutes balances "operator
# fixes the device, sees recovery soon" against "don't re-scan a known-
# broken slave on every metering cycle." Transient failures aren't
# cached -- they retry on the next aresolve.
_PERMANENT_FAILURE_TTL_SECONDS: float = 300.0


if TYPE_CHECKING:
    from py20305.connectors.config import DeviceConfig


class ConnectorRegistryError(RuntimeError):
    """Raised when a connector configuration cannot be materialized."""


class LazyConnectorProxy:
    """Defers connector construction until the first attribute access."""

    def __init__(self, name: str, factory: Callable[[], Any]) -> None:
        # Use object.__setattr__ to avoid triggering __getattr__
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_instance", None)
        # Lazily-allocated asyncio.Lock that serialises ``aresolve`` calls
        # so two concurrent first-touch awaits don't both spawn the
        # factory (which for ``ConnectorSunSpec`` would mean two TCP
        # connections + two scans against the same Modbus slave). The
        # lock binds to whichever loop first awaited it; rebind if the
        # running loop changes (crash-recovery rebuild + session-scoped
        # test fixtures) -- same pattern as
        # ``ConnectorSunSpec._get_lock``.
        object.__setattr__(self, "_aresolve_lock", None)
        object.__setattr__(self, "_aresolve_lock_loop", None)
        # Cache for permanent construction failures (e.g. Modbus
        # exception codes 1-4 from ``ConnectorSunSpec``'s scan). Without
        # the cache, every poll cycle re-runs the factory and re-scans
        # the broken slave -- on a demo deployment that drove
        # the diagnostics panel counts up by ~6/min per misconfigured
        # device. With the cache, the proxy re-raises the same exception
        # for ``_PERMANENT_FAILURE_TTL_SECONDS`` before letting another
        # construction attempt through, so a device that comes back
        # online (firmware reload, model registration) recovers without
        # a restart.
        object.__setattr__(self, "_permanent_failure", None)
        object.__setattr__(self, "_permanent_failure_at", None)

    def _check_cached_permanent_failure(self) -> None:
        """Re-raise the cached construction failure if it's still fresh.

        Called by both ``resolve`` and ``aresolve`` before running the
        factory. After the TTL expires the cache is cleared so the next
        call attempts construction again -- a device that operators
        repaired sees recovery within ``_PERMANENT_FAILURE_TTL_SECONDS``
        without a restart.
        """
        if self._permanent_failure is None or self._permanent_failure_at is None:
            return
        age = time.monotonic() - self._permanent_failure_at
        if age < _PERMANENT_FAILURE_TTL_SECONDS:
            raise self._permanent_failure
        # TTL expired -- clear and let the factory run again.
        logger.info(
            "LazyConnectorProxy %s: permanent-failure cache expired after %.0fs; "
            "retrying construction",
            self._name,
            age,
        )
        object.__setattr__(self, "_permanent_failure", None)
        object.__setattr__(self, "_permanent_failure_at", None)

    def _record_permanent_failure(self, exc: BaseException) -> None:
        """Cache a permanent construction failure for the TTL window."""
        object.__setattr__(self, "_permanent_failure", exc)
        object.__setattr__(self, "_permanent_failure_at", time.monotonic())

    def resolve(self) -> Any:
        """Return the underlying connector, creating it on demand.

        Synchronous: blocks the calling thread for the duration of the
        factory (which for ``ConnectorSunSpec`` includes a Modbus scan
        with retries). Use ``aresolve`` from the async hot paths so the
        event loop can keep handling other requests during construction.

        Permanent construction failures (factory raised
        ``ConnectorConnectionError(permanent=True)``) are cached for
        ``_PERMANENT_FAILURE_TTL_SECONDS`` and re-raised without
        re-running the factory.
        """
        if self._instance is not None:
            return self._instance
        self._check_cached_permanent_failure()
        try:
            instance = self._factory()
        except Exception as exc:
            if getattr(exc, "permanent", False):
                self._record_permanent_failure(exc)
            raise
        object.__setattr__(self, "_instance", instance)
        return instance

    def _get_aresolve_lock(self) -> asyncio.Lock:
        """Return an ``asyncio.Lock`` bound to the running loop.

        Lazy-allocate on first call; rebind if the loop changes since
        the last allocation. ``asyncio.Lock`` instances bind to the
        loop that first awaited them and raise ``RuntimeError`` if
        reused on another loop -- which happens whenever the registry
        outlives its loop (crash-recovery rebuild; session-scoped
        fixtures in tests).
        """
        loop = asyncio.get_running_loop()
        if self._aresolve_lock is None or self._aresolve_lock_loop is not loop:
            object.__setattr__(self, "_aresolve_lock", asyncio.Lock())
            object.__setattr__(self, "_aresolve_lock_loop", loop)
        return self._aresolve_lock  # type: ignore[no-any-return]

    async def aresolve(self) -> Any:
        """Async resolver: runs ``resolve`` on a worker thread.

        First-touch construction of a real Modbus connector takes seconds
        (TCP handshake + scan + readiness retries). Calling it from an
        ``async def`` route on the client's event loop blocks every
        other coroutine for that whole window. This wrapper offloads the
        work via ``asyncio.to_thread`` so the loop stays responsive.

        Once constructed, returns the cached instance with no thread hop,
        so subsequent calls cost the same as ``resolve``.

        The lock-protected double-check around the to_thread guarantees
        the factory runs at most once per proxy even when N coroutines
        race for first-touch construction. Without it, the per-device
        4 cycle types (metering / capability / settings / status) firing
        close together at startup could each spawn their own
        ``ConnectorSunSpec.__init__`` and open duplicate Modbus
        connections -- the exact fan-out this wrapper exists to make
        safe. Note: ``asyncio.to_thread`` itself
        uses the default ``ThreadPoolExecutor`` (size
        ``min(32, cpu_count + 4)``), so concurrent first-touch awaits
        across *different* proxies can still queue behind the pool;
        wall-clock for first dashboard load with N >> pool_size devices
        is bounded by ``ceil(N / pool_size) * scan_time``.
        """
        if self._instance is not None:
            return self._instance
        # Fast-path: a cached permanent failure within TTL re-raises
        # without taking the lock or hitting a worker thread, so the
        # 4 cycle types polling a known-broken device cost almost
        # nothing per cycle.
        self._check_cached_permanent_failure()
        async with self._get_aresolve_lock():
            # Double-check: another coroutine may have completed the
            # construction while we were waiting on the lock.
            if self._instance is not None:
                return self._instance
            self._check_cached_permanent_failure()
            return await asyncio.to_thread(self.resolve)

    def __getattr__(self, item: str) -> Any:
        return getattr(self.resolve(), item)

    def __repr__(self) -> str:
        state = "initialized" if self._instance is not None else "lazy"
        return f"<LazyConnectorProxy name={self._name!r} state={state}>"


#: Connector class for each ``type`` in
#: :data:`~py20305.connectors.config.DeviceConfig`, as an import
#: path so configuring a device you don't have never imports its driver.
#: ``custom`` is absent by design -- it carries its own ``class_path``.
CONNECTOR_TYPE_MAP: dict[str, str] = {
    "sunspec": "py20305.connectors.sunspec.ConnectorSunSpec",
    "print_demo": "py20305.connectors.print_demo.PrintDemoConnector",
}


class ConnectorConfigRegistry:
    """Resolves a device LFDI to its connector, constructing it on first use.

    Holds typed device configurations and hands out a
    :class:`LazyConnectorProxy` per device. Nothing opens a socket or scans a
    device until something asks for that device's connector, so a
    configuration naming an offline device costs nothing until it is used.

    LFDI lookup is case-insensitive: certificate tooling differs on hex case
    and a device should resolve either way.

    An application with device types of its own supplies a ``factory_resolver``
    rather than subclassing. That covers the cases ``class_path`` cannot: a
    connector that needs shared runtime state, one reached over a transport
    this package knows nothing about, or one whose configuration is a model
    defined downstream.
    """

    def __init__(
        self,
        devices: list[DeviceConfig],
        factory_resolver: Callable[[Any], Callable[[], Any] | None] | None = None,
    ) -> None:
        """Build a registry from typed device configs.

        Args:
            devices: The configured devices. Each is resolved to a connector
                class through :data:`CONNECTOR_TYPE_MAP`, or through its own
                ``class_path`` for
                :class:`~py20305.connectors.config.CustomDeviceConfig`.
            factory_resolver: Consulted for every device before the built-in
                resolution runs. Return a zero-argument callable constructing
                the connector to claim the device, or ``None`` to let this
                package handle it. Called once per device at first resolution,
                not on every lookup, and the callable it returns is invoked
                lazily like any other -- so a resolver may close over
                expensive state without paying for it up front.
        """
        self._device_configs: dict[str, DeviceConfig] = {}
        self._device_proxies: dict[str, LazyConnectorProxy] = {}
        self._factory_resolver = factory_resolver

        for device in devices:
            self._device_configs[device.lfdi] = device

    def get_connector(self, lfdi: str) -> LazyConnectorProxy | None:
        """Return a proxy for the device connector mapped to lfdi.

        LFDI comparison is case-insensitive, and the proxy is cached under the
        configured spelling so it is returned whichever spelling is asked for.
        Caching under the caller's spelling instead would mean a caller using
        a different case never hits the cache -- rebuilding the proxy and the
        connector on every lookup, which for a Modbus device is a fresh
        connection and scan each time, and which defeats the proxy's own
        failure cache and single-flight lock.
        """
        canonical = self._canonical_lfdi(lfdi)
        if canonical is None:
            return None

        cached = self._device_proxies.get(canonical)
        if cached is not None:
            return cached

        proxy = self._build_lazy_proxy(self._device_configs[canonical])
        self._device_proxies[canonical] = proxy
        return proxy

    def _canonical_lfdi(self, lfdi: str) -> str | None:
        """Return the configured spelling of ``lfdi``, or ``None`` if unknown."""
        if lfdi in self._device_configs:
            return lfdi
        wanted = lfdi.lower()
        for stored in self._device_configs:
            if stored.lower() == wanted:
                return stored
        return None

    def iter_device_specs(self) -> list[DeviceConfig]:
        """Return all registered device configurations."""
        return list(self._device_configs.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_lazy_proxy(self, device: DeviceConfig) -> LazyConnectorProxy:
        # Three branches, in precedence order. A downstream ``factory_resolver``
        # gets first refusal, so an application can own device types this
        # package has never heard of. A ``custom`` device carries its own
        # ``class_path`` and ``init_kwargs``, the escape hatch for a connector
        # that needs no shared state. Everything else resolves through the map
        # and unpacks its model into kwargs, minus the fields every device
        # config shares.
        #
        # The proxy wraps all three identically, so a downstream connector gets
        # the same lazy construction, permanent-failure caching and
        # single-flight ``aresolve`` as a bundled one.
        from py20305.connectors.config import CustomDeviceConfig

        if self._factory_resolver is not None:
            resolved = self._factory_resolver(device)
            if resolved is not None:
                return LazyConnectorProxy(name=device.lfdi, factory=resolved)

        if isinstance(device, CustomDeviceConfig):
            class_path = device.class_path
            kwargs = device.init_kwargs
        else:
            try:
                class_path = CONNECTOR_TYPE_MAP[device.type]
            except KeyError as exc:
                known = ", ".join(sorted(CONNECTOR_TYPE_MAP))
                raise ConnectorRegistryError(
                    f"Unknown device type {device.type!r}. Known types: {known}. "
                    "For a connector this package does not ship, either configure "
                    "the device as type 'custom' with a class_path pointing at "
                    "your BaseConnector subclass, or pass a factory_resolver that "
                    "claims this type."
                ) from exc
            kwargs = device.model_dump(exclude={"type", "lfdi", "description", "pin"})

        def factory() -> Any:
            cls = self._import_class(class_path)
            return cls(**kwargs)

        return LazyConnectorProxy(name=device.lfdi, factory=factory)

    def _import_class(self, class_path: str) -> type:
        try:
            module_name, class_name = class_path.rsplit(".", 1)
        except ValueError as exc:
            raise ConnectorRegistryError(f"Invalid class path '{class_path}'.") from exc

        try:
            module = importlib.import_module(module_name)
            return getattr(module, class_name)  # type: ignore[no-any-return]
        except (ImportError, AttributeError) as exc:
            raise ConnectorRegistryError(
                f"Unable to import connector class '{class_path}'."
            ) from exc
