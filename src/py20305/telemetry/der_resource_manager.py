"""DerResourceManager for periodic PUT of DER resources.

Manages the lifecycle of DERCapability, DERSettings, and DERStatus
PUT operations for each device. Follows the TelemetryManager composition
pattern: owns a PollScheduler, accepts client + connector resolver.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from py20305.client.errors import (
    Sep2ConnectionError,
    Sep2ProtocolError,
    compat_hint_suffix,
)
from py20305.client.polling import PollScheduler
from py20305.client.timebase import ServerTimebase
from py20305.commands import CommandObserver, NullCommandObserver
from py20305.connectors.base import ConnectorError
from py20305.telemetry.der_capability import build_der_capability
from py20305.telemetry.der_settings import build_der_settings
from py20305.telemetry.der_status import build_der_status
from py20305.xml.serialization import to_xml

if TYPE_CHECKING:
    from py20305.client.http import Sep2Client
    from py20305.connectors.base import BaseConnector

logger = logging.getLogger(__name__)


@dataclass
class DeviceDerState:
    """Per-device DER resource tracking state."""

    lfdi: str
    capability_href: str | None = None
    settings_href: str | None = None
    status_href: str | None = None
    scheduled_keys: list[str] = field(default_factory=list)
    # Change detection cache: only PUT when connector data changes
    last_settings_data: dict[str, Any] | None = None


class DerResourceManager:
    """Manages periodic PUT of DER resources for all devices.

    Handles three resource types:
    - DERCapability (nameplate ratings from connector)
    - DERSettings (configuration from connector)
    - DERStatus (operational status from connector)

    Each resource is PUT on its own poll cycle. Errors are logged
    but never propagate (the poll loop must not crash).
    """

    def __init__(
        self,
        client: Sep2Client,
        connector_resolver: Callable[[str], BaseConnector | Awaitable[BaseConnector]],
        command_observer: CommandObserver | None = None,
    ) -> None:
        """Initialize the DerResourceManager.

        Args:
            client: IEEE 2030.5 HTTP client for PUT operations.
            connector_resolver: Callback to resolve LFDI -> connector.
                May be either sync (``Callable[[str], BaseConnector]``)
                or async (``Callable[[str], Awaitable[BaseConnector]]``);
                the cycle methods await the result iff it's awaitable.
                Async resolvers are preferred so first-touch construction
                of a real Modbus connector can offload its scan +
                readiness retries to a worker thread instead of blocking
                the event loop.
            command_observer: Offered the status this manager already fetches,
                as confirmation evidence for outstanding commands. Injected
                rather than imported for the same reason as on the dispatcher:
                the plane is a host-application concern and this is client code.
                Deliberately reuses the existing DERStatus read rather than
                adding one -- a second read of the same device to confirm a
                command would be a second acquisition path.
        """
        self._client = client
        # Server timebase for telemetry timestamp defaults. isinstance guard
        # keeps AsyncMock clients in tests (whose .timebase is a Mock)
        # falling back to an identity timebase; production Sep2Client always
        # carries the real shared instance.
        tb = getattr(client, "timebase", None)
        self._timebase = tb if isinstance(tb, ServerTimebase) else ServerTimebase()
        self._resolve_connector = connector_resolver
        # See ConnectorDispatcher: `is None` so a falsy-when-empty observer is
        # not silently swapped for the no-op.
        self._commands = NullCommandObserver() if command_observer is None else command_observer
        self._devices: dict[str, DeviceDerState] = {}
        self._scheduler = PollScheduler()

    def start_device(
        self,
        lfdi: str,
        capability_href: str | None,
        settings_href: str | None,
        status_href: str | None,
        *,
        capability_poll_rate: int = 86400,
        settings_poll_rate: int = 60,
        status_poll_rate: int = 300,
    ) -> None:
        """Start periodic DER resource PUTs for a device.

        Only schedules cycles for non-None hrefs. Each resource type runs
        at its own poll rate. DERCapability and DERSettings use change
        detection to skip PUTs when the data hasn't changed.

        Args:
            lfdi: Device LFDI.
            capability_href: Server path for DERCapability PUT.
            settings_href: Server path for DERSettings PUT.
            status_href: Server path for DERStatus PUT.
            capability_poll_rate: Interval for DERCapability PUTs (default 24h).
            settings_poll_rate: Interval for DERSettings PUTs (default 1min).
            status_poll_rate: Interval for DERStatus PUTs (default 300s).
        """
        lfdi_norm = lfdi.lower()
        state = DeviceDerState(
            lfdi=lfdi_norm,
            capability_href=capability_href,
            settings_href=settings_href,
            status_href=status_href,
        )
        self._devices[lfdi_norm] = state

        if capability_href:
            key = f"dercap_{lfdi_norm}"
            state.scheduled_keys.append(key)
            callback: Callable[[], Awaitable[None]] = partial(self._capability_cycle, lfdi_norm)
            self._scheduler.schedule(key, capability_poll_rate, callback)

        if settings_href:
            key = f"derset_{lfdi_norm}"
            state.scheduled_keys.append(key)
            callback = partial(self._settings_cycle, lfdi_norm)
            self._scheduler.schedule(key, settings_poll_rate, callback)

        if status_href:
            key = f"derstat_{lfdi_norm}"
            state.scheduled_keys.append(key)
            callback = partial(self._status_cycle, lfdi_norm)
            self._scheduler.schedule(key, status_poll_rate, callback)

        logger.info(
            "Started DER resources for %s (cap=%s@%ds, set=%s@%ds, stat=%s@%ds)",
            lfdi_norm[:8],
            bool(capability_href),
            capability_poll_rate,
            bool(settings_href),
            settings_poll_rate,
            bool(status_href),
            status_poll_rate,
        )

    async def _resolve_connector_async(self, lfdi: str) -> BaseConnector:
        """Resolve connector via the injected resolver.

        First-touch construction of a SunSpec connector runs a synchronous
        Modbus scan; an async resolver offloads that to a worker thread.
        Cache hits return without yielding the loop, so this stays cheap
        on every cycle after the first per-device call.

        Sync resolvers are still accepted for backward compat with
        embedded callers that wire in a custom callback -- the cycle
        awaits the result iff it's awaitable.
        """
        result = self._resolve_connector(lfdi)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _capability_cycle(self, lfdi: str) -> None:
        """Single capability PUT cycle.

        Always PUTs regardless of whether the data changed since last cycle.
        DERCapability is a small, idempotent payload, and the server may have
        cleared it (e.g. between certification test runs).
        """
        state = self._devices.get(lfdi)
        if state is None or state.capability_href is None:
            return

        try:
            connector = await self._resolve_connector_async(lfdi)
            nameplate = await connector.fetch_nameplate()
            model = build_der_capability(
                nameplate,
                der_type=connector.der_type,
                csip_aus_mode=self._client.csip_aus_mode,
            )
            await self._client.put_bytes(
                state.capability_href,
                to_xml(
                    model,
                    server_2018_compat=self._client.server_2018_compat,
                    include_csipaus=self._client.csip_aus_mode,
                ),
            )
            logger.debug("DERCapability PUT for %s", lfdi[:8])
        except Sep2ProtocolError as e:
            from py20305.diagnostics import report

            hint = compat_hint_suffix(e, self._client.server_2018_compat)
            report(
                "warnings",
                f"DERCapability PUT for {lfdi[:8]} rejected (HTTP {e.status_code}): {e}{hint}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:capability",
                details={
                    "lfdi": lfdi,
                    "resource": "DERCapability",
                    "status_code": e.status_code,
                    "error": str(e),
                },
            )
        except (Sep2ConnectionError, ConnectorError, OSError) as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"DERCapability PUT failed for {lfdi[:8]}: {e}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:capability",
                details={"lfdi": lfdi, "resource": "DERCapability", "error": str(e)},
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Unexpected error in DERCapability PUT for {lfdi[:8]}: {e}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:capability",
                details={"lfdi": lfdi, "resource": "DERCapability", "error": str(e)},
                exc_info=True,
            )

    async def _settings_cycle(self, lfdi: str) -> None:
        """Single settings PUT cycle (with change detection)."""
        state = self._devices.get(lfdi)
        if state is None or state.settings_href is None:
            return

        try:
            connector = await self._resolve_connector_async(lfdi)
            configuration = await connector.fetch_configuration()
            if configuration == state.last_settings_data:
                logger.debug("DERSettings unchanged for %s, skipping PUT", lfdi[:8])
                return
            try:
                model = build_der_settings(
                    configuration,
                    updated_time=int(self._timebase.now()),
                    csip_aus_mode=self._client.csip_aus_mode,
                )
            except ValueError as e:
                # The connector returned configuration missing a required field
                # (WMax / setMaxW is mandatory in the IEEE 2030.5 DERSettings
                # schema). This is a connector-contract problem, not an
                # unexpected crash -- report it as an actionable, deduplicated
                # warning instead of letting it fall through to the generic
                # handler that logs a full traceback every settings cycle.
                from py20305.diagnostics import report

                report(
                    "warnings",
                    f"DERSettings PUT skipped for {lfdi[:8]}: {e}. The connector's "
                    "fetch_configuration() must return a non-null WMax (nameplate "
                    "active power in watts); DERSettings cannot be built without it.",
                    source="der_resource",
                    dedup_key=f"der_put:{lfdi}:settings_config",
                    details={"lfdi": lfdi, "resource": "DERSettings", "error": str(e)},
                )
                return
            await self._client.put_bytes(
                state.settings_href,
                to_xml(
                    model,
                    server_2018_compat=self._client.server_2018_compat,
                    include_csipaus=self._client.csip_aus_mode,
                ),
            )
            state.last_settings_data = configuration
            logger.debug("DERSettings PUT for %s", lfdi[:8])
        except Sep2ProtocolError as e:
            from py20305.diagnostics import report

            hint = compat_hint_suffix(e, self._client.server_2018_compat)
            report(
                "warnings",
                f"DERSettings PUT for {lfdi[:8]} rejected (HTTP {e.status_code}): {e}{hint}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:settings",
                details={
                    "lfdi": lfdi,
                    "resource": "DERSettings",
                    "status_code": e.status_code,
                    "error": str(e),
                },
            )
        except (Sep2ConnectionError, ConnectorError, OSError) as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"DERSettings PUT failed for {lfdi[:8]}: {e}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:settings",
                details={"lfdi": lfdi, "resource": "DERSettings", "error": str(e)},
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Unexpected error in DERSettings PUT for {lfdi[:8]}: {e}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:settings",
                details={"lfdi": lfdi, "resource": "DERSettings", "error": str(e)},
                exc_info=True,
            )

    async def _status_cycle(self, lfdi: str) -> None:
        """Single status PUT cycle."""
        state = self._devices.get(lfdi)
        if state is None or state.status_href is None:
            return

        try:
            connector = await self._resolve_connector_async(lfdi)
            # Stamped before the read: the plane discards an observation that
            # began before a command was issued, since it cannot reflect it.
            read_started_at = time.time()
            status_data: dict[str, Any] = await connector.fetch_status()
            self._commands.record_readback(lfdi, status_data, read_started_at=read_started_at)
            model = build_der_status(
                status_data,
                server_2018_compat=self._client.server_2018_compat,
                always_send_alarm_status=self._client.always_send_alarm_status,
                default_time=int(self._timebase.now()),
            )
            await self._client.put_bytes(
                state.status_href,
                to_xml(model, server_2018_compat=self._client.server_2018_compat),
            )
            logger.debug("DERStatus PUT for %s", lfdi[:8])
        except Sep2ProtocolError as e:
            from py20305.diagnostics import report

            hint = compat_hint_suffix(e, self._client.server_2018_compat)
            report(
                "warnings",
                f"DERStatus PUT for {lfdi[:8]} rejected (HTTP {e.status_code}): {e}{hint}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:status",
                details={
                    "lfdi": lfdi,
                    "resource": "DERStatus",
                    "status_code": e.status_code,
                    "error": str(e),
                },
            )
        except (Sep2ConnectionError, ConnectorError, OSError) as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"DERStatus PUT failed for {lfdi[:8]}: {e}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:status",
                details={"lfdi": lfdi, "resource": "DERStatus", "error": str(e)},
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Unexpected error in DERStatus PUT for {lfdi[:8]}: {e}",
                source="der_resource",
                dedup_key=f"der_put:{lfdi}:status",
                details={"lfdi": lfdi, "resource": "DERStatus", "error": str(e)},
                exc_info=True,
            )

    def stop_device(self, lfdi: str) -> None:
        """Stop the DER resource PUTs for one device.

        Drops the device's state, which is what the cycles read -- each becomes
        a no-op immediately, and the scheduler's tasks are cleaned up by
        ``shutdown``. Mirrors ``TelemetryManager.stop_metering``.

        Args:
            lfdi: Device LFDI.
        """
        self._devices.pop(lfdi.lower(), None)

    @property
    def active_devices(self) -> list[str]:
        """Return list of LFDIs with active DER resource cycles."""
        return list(self._devices.keys())

    async def shutdown(self) -> None:
        """Stop all scheduled DER resource tasks."""
        await self._scheduler.cancel_all()
        self._devices.clear()
        logger.info("DerResourceManager shutdown complete")
