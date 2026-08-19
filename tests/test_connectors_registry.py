"""Tests for ConnectorConfigRegistry and LazyConnectorProxy."""

from __future__ import annotations

import asyncio

import pytest

from py20305.connectors.config import (
    CustomDeviceConfig,
    PrintDemoDeviceConfig,
    SunSpecDeviceConfig,
)
from py20305.connectors.registry import (
    ConnectorConfigRegistry,
    ConnectorRegistryError,
    LazyConnectorProxy,
)


class TestLazyConnectorProxy:
    def test_deferred_construction(self):
        calls = []

        def factory():
            calls.append(1)
            return "instance"

        proxy = LazyConnectorProxy(name="test", factory=factory)
        assert len(calls) == 0
        assert proxy.resolve() == "instance"
        assert len(calls) == 1

    def test_resolve_only_once(self):
        calls = []

        def factory():
            calls.append(1)
            return "instance"

        proxy = LazyConnectorProxy(name="test", factory=factory)
        proxy.resolve()
        proxy.resolve()
        assert len(calls) == 1

    def test_getattr_delegates(self):
        proxy = LazyConnectorProxy(name="test", factory=lambda: "hello")
        assert proxy.upper() == "HELLO"

    def test_repr_lazy(self):
        proxy = LazyConnectorProxy(name="test", factory=lambda: None)
        assert "lazy" in repr(proxy)

    def test_repr_initialized(self):
        proxy = LazyConnectorProxy(name="test", factory=lambda: "instance")
        proxy.resolve()
        assert "initialized" in repr(proxy)

    @pytest.mark.asyncio
    async def test_aresolve_constructs_off_event_loop(self):
        """``aresolve`` must run the factory on a worker thread so a slow
        connector constructor (Modbus scan + retries) doesn't block the
        event loop -- the long tail behind a slow ``GET /``."""
        import asyncio
        import threading

        loop_thread_id = threading.get_ident()
        factory_thread_id: list[int] = []

        def factory():
            factory_thread_id.append(threading.get_ident())
            return "instance"

        proxy = LazyConnectorProxy(name="test", factory=factory)
        result = await proxy.aresolve()

        assert result == "instance"
        assert len(factory_thread_id) == 1
        assert factory_thread_id[0] != loop_thread_id, (
            "aresolve must dispatch factory() to a worker thread; running "
            "construction on the event loop reintroduces the stall."
        )
        # Sanity: caller is still on the original loop after the await.
        assert asyncio.get_running_loop() is asyncio.get_event_loop()

    @pytest.mark.asyncio
    async def test_aresolve_caches_after_first_call(self):
        """Once resolved, ``aresolve`` returns the cached instance with no
        thread hop -- subsequent calls cost the same as ``resolve``."""
        import threading

        factory_calls: list[int] = []

        def factory():
            factory_calls.append(threading.get_ident())
            return "instance"

        proxy = LazyConnectorProxy(name="test", factory=factory)
        await proxy.aresolve()
        await proxy.aresolve()
        await proxy.aresolve()

        assert len(factory_calls) == 1, "factory must run exactly once across N aresolves"

    @pytest.mark.asyncio
    async def test_aresolve_returns_same_instance_as_resolve(self):
        """``aresolve`` and ``resolve`` must return the same cached object;
        callers mixing the two must not see a different connector."""
        proxy = LazyConnectorProxy(name="test", factory=lambda: object())
        a = await proxy.aresolve()
        b = proxy.resolve()
        assert a is b

    @pytest.mark.asyncio
    async def test_aresolve_serializes_concurrent_first_touch(self):
        """Two coroutines hitting an unresolved proxy in parallel must run
        the factory exactly once -- otherwise the SunSpec connector would
        open two Modbus TCP connections to the same server on first
        dashboard load (the per-device 4 cycle types waking together at
        startup is the exact fan-out scenario)."""
        import asyncio

        factory_calls: list[int] = []

        def factory():
            factory_calls.append(1)
            return object()

        proxy = LazyConnectorProxy(name="test", factory=factory)

        # Fire several concurrent aresolves before any of them can write
        # ``_instance``. Without the lock, all N pass the
        # ``_instance is None`` check, all N spawn the factory.
        results = await asyncio.gather(
            proxy.aresolve(),
            proxy.aresolve(),
            proxy.aresolve(),
            proxy.aresolve(),
            proxy.aresolve(),
        )

        assert len(factory_calls) == 1, (
            f"factory must run exactly once even under concurrent first-touch; "
            f"got {len(factory_calls)} runs (lock removed?)"
        )
        # All callers must observe the same cached instance.
        assert all(r is results[0] for r in results), (
            "concurrent aresolves returned different instances; "
            "the second-and-later calls bypassed the cache"
        )

    def test_aresolve_lock_rebinds_when_event_loop_changes(self):
        """A proxy reused across event loops must NOT raise the
        "Lock bound to a different event loop" RuntimeError. Same
        regression class as ``ConnectorSunSpec._get_lock`` -- the lock
        rebinds lazily to the running loop on each call.

        Sync test (not ``@pytest.mark.asyncio``) because it has to
        create its own loops; pytest-asyncio sets one up before each
        async test and ``run_until_complete`` on a second loop while
        one is already running raises ``RuntimeError``.
        """
        import asyncio

        # Each loop gets its own proxy because the cache (``_instance``)
        # is loop-agnostic -- once construction completes on loop A,
        # ``aresolve`` on loop B short-circuits without touching the
        # lock at all and the rebind path is never exercised.
        proxy_a = LazyConnectorProxy(name="test_a", factory=lambda: object())
        proxy_b = LazyConnectorProxy(name="test_b", factory=lambda: object())

        loop_a = asyncio.new_event_loop()
        try:
            loop_a.run_until_complete(proxy_a.aresolve())
            lock_a = proxy_a._aresolve_lock
            loop_bound_a = proxy_a._aresolve_lock_loop
        finally:
            loop_a.close()

        loop_b = asyncio.new_event_loop()
        try:
            # Reuse proxy_a on a fresh loop -- this is the rebind path.
            loop_b.run_until_complete(proxy_b.aresolve())  # warm proxy_b first

            # Now exercise the rebind: a fresh proxy with a lock left
            # behind from loop_a should not raise on loop_b.
            proxy_c = LazyConnectorProxy(name="test_c", factory=lambda: object())

            # Construct the stale lock inside an active loop so its
            # internal loop reference is resolvable; on Python 3.13
            # creating asyncio.Lock() with no current loop emits a
            # DeprecationWarning and warm-binds in a way that confuses
            # the rebind path. Allocating it via run_until_complete is
            # equivalent to how a real proxy first acquired it on
            # loop_a, then survived loop_a's close.
            stale_loop = asyncio.new_event_loop()
            try:
                stale_lock = stale_loop.run_until_complete(_make_lock_async())
            finally:
                stale_loop.close()
            object.__setattr__(proxy_c, "_aresolve_lock", stale_lock)
            object.__setattr__(proxy_c, "_aresolve_lock_loop", stale_loop)

            loop_b.run_until_complete(proxy_c.aresolve())  # must not raise
            lock_c = proxy_c._aresolve_lock
            loop_bound_c = proxy_c._aresolve_lock_loop
        finally:
            loop_b.close()

        assert loop_bound_a is loop_a
        assert loop_bound_c is loop_b
        assert lock_a is not lock_c


class TestLazyConnectorProxyPermanentFailureCache:
    """Permanent construction failures (factory raises with
    ``permanent=True``) get cached so the polling cycles don't re-attempt
    construction every cycle. Without the cache, the demo aggregator's
    diagnostics panel filled up with 6+ entries/min per misconfigured
    device because every metering / settings / status cycle re-ran the
    full Modbus scan against a server whose answer wouldn't change."""

    @pytest.mark.asyncio
    async def test_aresolve_caches_permanent_failure_and_reraises_without_factory(self):
        """Second aresolve must re-raise without re-running the factory."""
        from py20305.connectors.base import ConnectorConnectionError
        from py20305.connectors.registry import LazyConnectorProxy

        factory_calls: list[int] = []

        def factory():
            factory_calls.append(1)
            raise ConnectorConnectionError("modbus exception 4", permanent=True)

        proxy = LazyConnectorProxy(name="test", factory=factory)

        with pytest.raises(ConnectorConnectionError, match="modbus exception 4"):
            await proxy.aresolve()
        with pytest.raises(ConnectorConnectionError, match="modbus exception 4"):
            await proxy.aresolve()
        with pytest.raises(ConnectorConnectionError, match="modbus exception 4"):
            await proxy.aresolve()

        assert len(factory_calls) == 1, (
            f"factory must run exactly once for a permanent failure within TTL; "
            f"got {len(factory_calls)} runs (cache not honored?)"
        )

    @pytest.mark.asyncio
    async def test_aresolve_does_not_cache_transient_failure(self):
        """Transient failures (no ``permanent=True``) must NOT be cached --
        a momentary connection-refused must be retried on the next cycle
        instead of waiting for the TTL."""
        from py20305.connectors.registry import LazyConnectorProxy

        factory_calls: list[int] = []

        def factory():
            factory_calls.append(1)
            raise ConnectionRefusedError("transient")

        proxy = LazyConnectorProxy(name="test", factory=factory)

        with pytest.raises(ConnectionRefusedError):
            await proxy.aresolve()
        with pytest.raises(ConnectionRefusedError):
            await proxy.aresolve()

        assert len(factory_calls) == 2, (
            f"transient failures must NOT cache; expected 2 factory runs, got {len(factory_calls)}"
        )

    @pytest.mark.asyncio
    async def test_aresolve_retries_after_ttl_expires(self, monkeypatch):
        """After the TTL expires, the next aresolve runs the factory again --
        a device the operator repaired must recover without an aggregator
        restart."""
        from py20305.connectors import registry as registry_mod
        from py20305.connectors.base import ConnectorConnectionError
        from py20305.connectors.registry import LazyConnectorProxy

        # Shorten the TTL to 0 so the second aresolve always sees an
        # expired cache without us needing to advance time.
        monkeypatch.setattr(registry_mod, "_PERMANENT_FAILURE_TTL_SECONDS", 0.0)

        factory_calls: list[int] = []
        succeed_after = 1

        def factory():
            factory_calls.append(1)
            if len(factory_calls) <= succeed_after:
                raise ConnectorConnectionError("modbus exception 4", permanent=True)
            return "recovered"

        proxy = LazyConnectorProxy(name="test", factory=factory)

        with pytest.raises(ConnectorConnectionError):
            await proxy.aresolve()

        # TTL=0 -> next call must re-run the factory.
        result = await proxy.aresolve()
        assert result == "recovered"
        assert len(factory_calls) == 2

    def test_resolve_sync_path_also_caches_permanent_failure(self):
        """Sync ``resolve()`` honors the same cache so callers that mix
        sync and async access (``proxy.resolve()`` from a sync route +
        ``await proxy.aresolve()`` from an async route) see consistent
        cache state."""
        from py20305.connectors.base import ConnectorConnectionError
        from py20305.connectors.registry import LazyConnectorProxy

        factory_calls: list[int] = []

        def factory():
            factory_calls.append(1)
            raise ConnectorConnectionError("permanent", permanent=True)

        proxy = LazyConnectorProxy(name="test", factory=factory)

        with pytest.raises(ConnectorConnectionError):
            proxy.resolve()
        with pytest.raises(ConnectorConnectionError):
            proxy.resolve()

        assert len(factory_calls) == 1


async def _make_lock_async() -> asyncio.Lock:
    """Allocate an ``asyncio.Lock`` from inside a running loop.

    Module-level helper used by ``test_aresolve_lock_rebinds_when_event_
    loop_changes`` to avoid the Python 3.13 ``DeprecationWarning`` that
    fires when ``asyncio.Lock()`` is constructed without a current loop.
    """
    return asyncio.Lock()


class TestConnectorConfigRegistry:
    def test_empty_config(self):
        registry = ConnectorConfigRegistry([])
        assert registry.get_connector("abcabcababcabcababcabcababcabcababcabcab") is None

    def test_device_lookup(self):
        registry = ConnectorConfigRegistry(
            [
                PrintDemoDeviceConfig(lfdi="1234123412341234123412341234123412341234"),
            ]
        )
        proxy = registry.get_connector("1234123412341234123412341234123412341234")
        assert proxy is not None
        connector = proxy.resolve()
        assert connector.connector_name == "PrintDemoConnector"

    def test_case_insensitive_lfdi(self):
        # Construction uses uppercase; lookup uses lowercase. Both
        # should resolve because the registry normalises case.
        registry = ConnectorConfigRegistry(
            [
                PrintDemoDeviceConfig(lfdi="ABCD1234" * 5),
            ]
        )
        proxy = registry.get_connector("abcd1234" * 5)
        assert proxy is not None

    def test_missing_connector_returns_none(self):
        registry = ConnectorConfigRegistry([])
        assert registry.get_connector("ffeeddccffeeddccffeeddccffeeddccffeeddcc") is None

    def test_sunspec_device_lookup(self):
        registry = ConnectorConfigRegistry(
            [
                SunSpecDeviceConfig(
                    lfdi="4561456145614561456145614561456145614561", host="10.0.0.1", port=502
                ),
            ]
        )
        proxy = registry.get_connector("4561456145614561456145614561456145614561")
        assert proxy is not None

    def test_custom_device_invalid_class_path_raises(self):
        registry = ConnectorConfigRegistry(
            [
                CustomDeviceConfig(
                    lfdi="4561456145614561456145614561456145614561",
                    class_path="nonexistent.module.Class",
                ),
            ]
        )
        proxy = registry.get_connector("4561456145614561456145614561456145614561")
        with pytest.raises(ConnectorRegistryError, match="Unable to import"):
            proxy.resolve()

    def test_iter_device_specs(self):
        devices = [
            PrintDemoDeviceConfig(lfdi="4141414141414141414141414141414141414141"),
            PrintDemoDeviceConfig(lfdi="4242424242424242424242424242424242424242"),
        ]
        registry = ConnectorConfigRegistry(devices)
        specs = registry.iter_device_specs()
        assert len(specs) == 2

    def test_proxy_cached_on_second_access(self):
        registry = ConnectorConfigRegistry(
            [
                PrintDemoDeviceConfig(lfdi="4561456145614561456145614561456145614561"),
            ]
        )
        p1 = registry.get_connector("4561456145614561456145614561456145614561")
        p2 = registry.get_connector("4561456145614561456145614561456145614561")
        assert p1 is p2

    def test_custom_device_class_path_resolution(self):
        registry = ConnectorConfigRegistry(
            [
                CustomDeviceConfig(
                    lfdi="4561456145614561456145614561456145614561",
                    class_path="py20305.connectors.print_demo.PrintDemoConnector",
                ),
            ]
        )
        proxy = registry.get_connector("4561456145614561456145614561456145614561")
        assert proxy is not None
        connector = proxy.resolve()
        assert connector.connector_name == "PrintDemoConnector"

    def test_custom_device_with_kwargs(self):
        registry = ConnectorConfigRegistry(
            [
                CustomDeviceConfig(
                    lfdi="4561456145614561456145614561456145614561",
                    class_path="py20305.connectors.print_demo.PrintDemoConnector",
                    init_kwargs={},
                ),
            ]
        )
        proxy = registry.get_connector("4561456145614561456145614561456145614561")
        assert proxy is not None
        connector = proxy.resolve()
        assert connector.connector_name == "PrintDemoConnector"
