"""Forwarder manager for routing messages to multiple forwarders.

The ForwarderManager acts as a facade that routes MessageFrame objects
to one or more registered forwarders (MQTT, file, console, etc.).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from py20305.forwarders.base import (
    BaseForwarder,
    EventFrame,
    MessageFrame,
    TelemetryFrame,
)

if TYPE_CHECKING:
    from py20305.forwarders.config import ForwarderConfig

logger = logging.getLogger(__name__)


class ForwarderManager:
    """Routes messages to multiple forwarders.

    The manager handles:
    - Registration of forwarders
    - Lifecycle management (start/stop all forwarders)
    - Message routing to all registered forwarders
    - Aggregated statistics

    Usage:
        manager = ForwarderManager()
        manager.add_forwarder(mqtt_adapter)
        await manager.start()

        # Queue messages - they're routed to all forwarders
        manager.queue_message(frame)

        await manager.stop()
    """

    def __init__(self) -> None:
        """Initialize the forwarder manager."""
        self._forwarders: list[BaseForwarder] = []
        #: Forwarders whose `start` raised. Identity-keyed, because a
        #: forwarder is not necessarily hashable by value.
        self._failed: set[BaseForwarder] = set()
        self._running = False
        self._client_lfdi: str | None = None
        self._forwarder_id: str | None = None
        self._source_host: str | None = None

    @property
    def running(self) -> bool:
        """Return whether the manager is running."""
        return self._running

    @property
    def forwarders(self) -> list[BaseForwarder]:
        """Return list of registered forwarders."""
        return list(self._forwarders)

    @property
    def client_lfdi(self) -> str | None:
        """Return this client's own LFDI, used as an identifier fallback."""
        return self._client_lfdi

    @client_lfdi.setter
    def client_lfdi(self, value: str | None) -> None:
        """Set this client's own LFDI and propagate it to forwarders that use it."""
        self._client_lfdi = value
        for forwarder in self._forwarders:
            if hasattr(forwarder, "client_lfdi"):
                forwarder.client_lfdi = value

    @property
    def forwarder_id(self) -> str | None:
        """Return the configured forwarder ID."""
        return self._forwarder_id

    @forwarder_id.setter
    def forwarder_id(self, value: str | None) -> None:
        """Set forwarder ID and propagate to forwarders that support it."""
        self._forwarder_id = value
        for forwarder in self._forwarders:
            if hasattr(forwarder, "forwarder_id"):
                forwarder.forwarder_id = value

    @property
    def source_host(self) -> str | None:
        """Return source hostname for forwarded messages."""
        return self._source_host

    @source_host.setter
    def source_host(self, value: str | None) -> None:
        """Set source hostname and propagate to forwarders that support it."""
        self._source_host = value
        for forwarder in self._forwarders:
            if hasattr(forwarder, "source_host"):
                forwarder.source_host = value

    def add_forwarder(self, forwarder: BaseForwarder) -> None:
        """Register a forwarder.

        Args:
            forwarder: Forwarder instance to add
        """
        if forwarder in self._forwarders:
            logger.warning("Forwarder %s already registered", forwarder.name)
            return

        self._forwarders.append(forwarder)

        # Propagate properties to new forwarder
        if self._client_lfdi and hasattr(forwarder, "client_lfdi"):
            forwarder.client_lfdi = self._client_lfdi
        if self._forwarder_id and hasattr(forwarder, "forwarder_id"):
            forwarder.forwarder_id = self._forwarder_id
        if self._source_host and hasattr(forwarder, "source_host"):
            forwarder.source_host = self._source_host

        logger.info("Registered forwarder: %s", forwarder.name)

    def remove_forwarder(self, forwarder: BaseForwarder) -> bool:
        """Unregister a forwarder.

        Args:
            forwarder: Forwarder instance to remove

        Returns:
            True if removed, False if not found
        """
        try:
            self._forwarders.remove(forwarder)
            logger.info("Removed forwarder: %s", forwarder.name)
            return True
        except ValueError:
            return False

    async def start(self) -> None:
        """Start all registered forwarders.

        Forwarders that fail to start are logged but don't prevent
        other forwarders from starting.
        """
        if self._running:
            logger.warning("ForwarderManager already running")
            return

        logger.info("Starting ForwarderManager with %d forwarders", len(self._forwarders))

        from py20305.diagnostics import report

        for forwarder in self._forwarders:
            try:
                await forwarder.start()
            except Exception as e:
                self._failed.add(forwarder)
                report(
                    "warnings",
                    f"Failed to start forwarder {forwarder.name}: {e}",
                    source="forwarder",
                    dedup_key=f"forwarder_lifecycle:{forwarder.name}:start",
                    details={"forwarder": forwarder.name, "phase": "start", "error": str(e)},
                )

        self._running = True

    async def stop(self) -> None:
        """Stop all registered forwarders gracefully."""
        if not self._running:
            return

        from py20305.diagnostics import report

        logger.info("Stopping ForwarderManager")

        for forwarder in self._forwarders:
            try:
                await forwarder.stop()
            except Exception as e:
                report(
                    "warnings",
                    f"Error stopping forwarder {forwarder.name}: {e}",
                    source="forwarder",
                    dedup_key=f"forwarder_lifecycle:{forwarder.name}:stop",
                    details={"forwarder": forwarder.name, "phase": "stop", "error": str(e)},
                )

        self._running = False
        logger.info("ForwarderManager stopped")

    def queue_message(self, frame: MessageFrame) -> None:
        """Route a message to all registered forwarders.

        Messages are queued to each forwarder. Errors in one forwarder
        don't affect others.

        Args:
            frame: Message frame to route
        """
        if not self._running:
            logger.debug("ForwarderManager not running, dropping message")
            return

        for forwarder in self._forwarders:
            try:
                forwarder.queue_message(frame)
            except Exception as e:
                logger.error("Error queueing to forwarder %s: %s", forwarder.name, e)

    def failed_forwarders(self) -> list[BaseForwarder]:
        """Registered forwarders whose ``start`` raised and has not since succeeded.

        A forwarder whose broker was unreachable at startup drops everything
        handed to it, while the manager reports itself running -- so "the
        manager started" is not the same question as "anything is being
        delivered", and a caller retrying needs the second.

        Recorded when the start fails rather than read back off the forwarder:
        ``BaseForwarder`` does not require a ``running`` attribute, so asking
        for one would classify any implementation without it as healthy and
        never retry the very forwarders this exists for.
        """
        return [f for f in self._forwarders if f in self._failed]

    async def retry_failed(self) -> int:
        """Try to start the forwarders that are not running. Returns how many came up.

        Separate from :meth:`start` so a caller can drive it on whatever
        cadence suits it. Without this a broker that is briefly unreachable
        when the client boots -- which is exactly when a broker restarted
        alongside it would be -- silently disables forwarding for the lifetime
        of the process.
        """
        recovered = 0
        for forwarder in self.failed_forwarders():
            try:
                await forwarder.start()
            except Exception as e:
                logger.debug("Forwarder %s still unavailable: %s", forwarder.name, e)
                continue
            self._failed.discard(forwarder)
            recovered += 1
            logger.info("Forwarder %s reconnected", forwarder.name)
        return recovered

    def queue_event(self, event: EventFrame) -> None:
        """Route an event to all registered forwarders.

        Mirrors :meth:`queue_message`: non-blocking, and one forwarder's
        failure does not affect the others. Forwarders that do not carry
        events drop them.

        Args:
            event: Event frame to route
        """
        if not self._running:
            logger.debug("ForwarderManager not running, dropping event")
            return

        for forwarder in self._forwarders:
            try:
                forwarder.queue_event(event)
            except Exception as e:
                logger.error("Error queueing event to forwarder %s: %s", forwarder.name, e)

    def queue_telemetry(self, frame: TelemetryFrame) -> None:
        """Route measured device state to all registered forwarders.

        A forwarder that does not carry telemetry drops it (the default
        implementation on ``AbstractForwarder``), so this is safe to call
        regardless of which forwarders are registered.
        """
        if not self._running:
            logger.debug("ForwarderManager not running, dropping telemetry")
            return

        for forwarder in self._forwarders:
            try:
                forwarder.queue_telemetry(frame)
            except Exception as e:
                logger.error("Error queueing telemetry to forwarder %s: %s", forwarder.name, e)

    def get_statistics(self) -> dict[str, Any]:
        """Return aggregated statistics from all forwarders."""
        return {
            "running": self._running,
            "forwarder_count": len(self._forwarders),
            "client_lfdi": self._client_lfdi,
            "forwarders": {f.name: f.get_statistics() for f in self._forwarders},
        }

    @classmethod
    async def from_config(cls, config: ForwarderConfig) -> ForwarderManager:
        """Create and configure a ForwarderManager from config.

        Args:
            config: Forwarder configuration

        Returns:
            Configured ForwarderManager instance
        """
        manager = cls()

        if config.mqtt is not None and config.mqtt.enabled:
            try:
                from py20305.forwarders.mqtt_adapter import MQTTForwarderAdapter
                from py20305.forwarders.mqtt_forwarder import MQTTForwarder

                mqtt = MQTTForwarder(config.mqtt)
                manager.add_forwarder(MQTTForwarderAdapter(mqtt))
            except Exception as e:
                from py20305.diagnostics import report

                report(
                    "errors",
                    f"Failed to instantiate MQTT forwarder: {e}",
                    source="mqtt",
                    details={"error": str(e)},
                )

        return manager
