"""Composition of the two telemetry managers for one running client.

``TelemetryManager`` mirrors readings as MirrorUsagePoints; ``DerResourceManager``
PUTs DERCapability, DERSettings and DERStatus. Both need the same things --
which devices, at what rate, to which discovered hrefs -- and both have to be
restarted when rediscovery moves those hrefs. That shared bookkeeping lives
here rather than in the runner so a host application embedding this library
starts telemetry the same way the runner does, instead of reimplementing it and
drifting.

The coordinator owns lifecycle, not protocol: every decision about what a
resource contains stays in the managers it composes.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from py20305.client.csip_client import CsipClient
    from py20305.commands import CommandObserver
    from py20305.connectors.base import BaseConnector
    from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
    from py20305.readings import MeasurementSource
    from py20305.telemetry.der_resource_manager import DerResourceManager
    from py20305.telemetry.manager import TelemetryManager

logger = logging.getLogger(__name__)

#: DERCapability is nameplate data. It changes when hardware is replaced, so a
#: daily PUT is generous rather than sparse.
DEFAULT_CAPABILITY_POLL_RATE = 86400
#: DERSettings tracks configuration an operator can change at any time.
DEFAULT_SETTINGS_POLL_RATE = 60
DEFAULT_POST_RATE = 300


class TelemetryCoordinator:
    """Starts, restarts and stops the telemetry managers for a set of devices.

    Args:
        client: The connected client. Its discovered state supplies every
            href, so ``setup`` belongs after discovery rather than before it.
        lfdis: The devices to report on, as configured. Held as given: a
            device the server has temporarily forgotten stays registered,
            because configuration says what to manage and discovery says only
            where to send it.
        connector_resolver: Resolves an LFDI to its connector. Taken rather
            than derived from the client's dispatcher, so telemetry keeps no
            dependency on the connector layer and a host application can pass
            its own.
        post_rate_seconds: Fallback interval for readings and DERStatus, used
            when the server's ``EndDevice.postRate`` does not say.
        der_capability_poll_rate_seconds: Interval for DERCapability PUTs.
        der_settings_poll_rate_seconds: Interval for DERSettings PUTs.
        device_telemetry: Reports each device read to a monitoring system.
        source: Where measurements come from. Omitted, the metering cycle
            reads the connector directly; a host application serving several
            interfaces passes a store-backed source so a concurrent read does
            not become a second device poll.
        is_provisioned: Optional per-cycle gate on mirroring a device.
        command_observer: Offered the DERStatus the resource manager already
            reads, as confirmation evidence for outstanding commands.
    """

    def __init__(
        self,
        client: CsipClient,
        *,
        lfdis: Iterable[str],
        connector_resolver: Callable[[str], BaseConnector | Awaitable[BaseConnector]],
        post_rate_seconds: int = DEFAULT_POST_RATE,
        der_capability_poll_rate_seconds: int = DEFAULT_CAPABILITY_POLL_RATE,
        der_settings_poll_rate_seconds: int = DEFAULT_SETTINGS_POLL_RATE,
        device_telemetry: DeviceTelemetryEmitter | None = None,
        source: MeasurementSource | None = None,
        is_provisioned: Callable[[str], bool] | None = None,
        command_observer: CommandObserver | None = None,
    ) -> None:
        self._client = client
        self._resolve_connector = connector_resolver
        self._post_rate = post_rate_seconds
        self._capability_poll_rate = der_capability_poll_rate_seconds
        self._settings_poll_rate = der_settings_poll_rate_seconds
        self._device_telemetry = device_telemetry
        self._source = source
        self._is_provisioned = is_provisioned
        self._command_observer = command_observer
        self._started: dict[str, bool] = {lfdi.lower(): False for lfdi in lfdis}
        self._telemetry: TelemetryManager | None = None
        self._der_resources: DerResourceManager | None = None
        #: Last MirrorUsagePointList path seen. Rediscovery clears state before
        #: repopulating it, and posting through that window has to go somewhere.
        #: Non-empty whenever the metering manager exists, because that manager
        #: is only built once a path has been discovered.
        self._last_mup_list_href: str = ""
        #: Whether the missing-MirrorUsagePointList warning has been said. Every
        #: rediscovery re-attempts the metering setup, so without this a server
        #: that never mirrors readings would log the same line on every
        #: structural change.
        self._warned_no_mup = False

    @property
    def telemetry(self) -> TelemetryManager | None:
        """The metering manager, or None when the server mirrors nothing."""
        return self._telemetry

    @property
    def der_resources(self) -> DerResourceManager | None:
        """The DER resource manager, once ``setup`` has run."""
        return self._der_resources

    def setup(self) -> None:
        """Construct both managers. Safe to call again; each part is idempotent."""
        self._setup_telemetry()
        self._setup_der_resources()

    def _setup_telemetry(self) -> None:
        """Build the metering manager, if the server offers somewhere to post.

        Re-attempted on rediscovery, so a server that brings the
        MirrorUsagePoint function set online after connect starts mirroring
        without a restart.
        """
        if self._telemetry is not None:
            return

        mup_list_href = self._client.state.mup_list_href
        if not mup_list_href:
            # A warning rather than an error: this is a server that does not
            # mirror readings, which is a valid deployment. Said once, because
            # a cycle that fails forever tells the operator less.
            if not self._warned_no_mup:
                logger.warning(
                    "the server exposed no MirrorUsagePointList; no readings will be posted. "
                    "DER resource PUTs are unaffected"
                )
                self._warned_no_mup = True
            return

        from py20305.telemetry.manager import TelemetryManager

        self._last_mup_list_href = mup_list_href
        self._warned_no_mup = False
        self._telemetry = TelemetryManager(
            client=self._client.http,
            # Read live: an upstream restart makes the client rediscover, and
            # the MirrorUsagePointList can move.
            mup_list_href=self._mup_list_href,
            connector_resolver=self._resolve_connector,
            is_provisioned=self._is_provisioned,
            source=self._source,
            device_telemetry=self._device_telemetry,
        )

    def _setup_der_resources(self) -> None:
        """Build the DER resource manager.

        Unconditional, unlike metering: the hrefs it PUTs to are per-device and
        resolved at start time, and a server that mirrors no readings still
        expects DERCapability.
        """
        if self._der_resources is not None:
            return

        from py20305.telemetry.der_resource_manager import DerResourceManager

        self._der_resources = DerResourceManager(
            client=self._client.http,
            connector_resolver=self._resolve_connector,
            command_observer=self._command_observer,
        )

    def _mup_list_href(self) -> str:
        current = self._client.state.mup_list_href
        if current:
            self._last_mup_list_href = current
        return current or self._last_mup_list_href

    def start_device_telemetry(self, lfdi: str | None = None) -> None:
        """Start metering and DER resource PUTs for one device, or all of them.

        Devices already started are left alone, so this is safe to call when a
        single device is added. Naming a device that was not in ``lfdis``
        registers it: a host application that registers devices while the
        client runs is making the request explicitly, and skipping it silently
        would report nothing for that device.

        Args:
            lfdi: One device, or None for every registered device.
        """
        if lfdi is not None:
            self._started.setdefault(lfdi.lower(), False)
        targets = [lfdi.lower()] if lfdi is not None else list(self._started)
        for target in targets:
            if self._started.get(target, True):
                continue
            post_rate = self._device_post_rate(target)
            if self._telemetry is not None:
                log_href, avail_href = self._telemetry_hrefs(target)
                logger.info(
                    "starting telemetry for %s: post_rate=%ds, log_events=%s, availability=%s",
                    target[:8],
                    post_rate,
                    log_href,
                    avail_href,
                )
                self._telemetry.start_metering(
                    target,
                    post_rate,
                    log_event_list_href=log_href,
                    der_availability_href=avail_href,
                )
            self._start_der_resources_for_device(target, post_rate)
            self._started[target] = True

    def stop_device_telemetry(self, lfdi: str) -> None:
        """Stop reporting one device and unregister it.

        For a device the server has deleted, or one an operator removed. Both
        managers drop it, and a later rediscovery does not bring it back --
        unlike a device that is merely absent from the current discovery, which
        stays registered.

        Args:
            lfdi: Device LFDI.
        """
        target = lfdi.lower()
        if self._telemetry is not None:
            self._telemetry.stop_metering(target)
        if self._der_resources is not None:
            self._der_resources.stop_device(target)
        self._started.pop(target, None)

    async def restart_device_telemetry(self) -> None:
        """Re-read every href from freshly discovered state.

        Wired to the client's structural-change hook: an upstream restart or a
        topology change moves the paths both managers PUT to, and a snapshot
        taken at first discovery would keep posting where the server no longer
        serves. Devices absent from the new state stay registered and are
        started with no hrefs, which makes their cycles no-ops until the server
        publishes them again.
        """
        # Late MirrorUsagePointList adoption. Cheap to attempt, and the only
        # path by which a server that added the function set after connect ever
        # gets readings.
        self._setup_telemetry()

        for target in self._started:
            self._started[target] = False
        self.start_device_telemetry()

    def _device_post_rate(self, lfdi: str) -> int:
        """The server's ``EndDevice.postRate``, falling back to configuration.

        IEEE 2030.5 gives the server the say over how often subordinate
        resources are posted. Zero and absent both mean it declined to specify.
        """
        for state in self._client.state.end_devices.values():
            if state.lfdi.hex().lower() != lfdi:
                continue
            server_rate = state.device.post_rate
            if server_rate is not None and server_rate > 0:
                return int(server_rate)
        return self._post_rate

    def _telemetry_hrefs(self, lfdi: str) -> tuple[str | None, str | None]:
        """LogEventList and DERAvailability hrefs for a device.

        Several EndDevices can carry one LFDI, so hrefs are merged across every
        match, first non-None winning.
        """
        log_href: str | None = None
        avail_href: str | None = None
        for state in self._client.state.end_devices.values():
            if state.lfdi.hex().lower() != lfdi:
                continue
            if log_href is None:
                log_href = state.log_event_list_href
            if avail_href is None:
                avail_href = state.der_availability_href
        return log_href, avail_href

    def _start_der_resources_for_device(self, lfdi: str, post_rate: int) -> None:
        """Schedule the three DER resource PUTs using discovered hrefs.

        DERStatus follows the posting rate, because it is the one of the three
        that tracks what the device is doing now. Capability and settings keep
        their own rates.
        """
        if self._der_resources is None:
            return

        capability_href: str | None = None
        settings_href: str | None = None
        status_href: str | None = None
        for state in self._client.state.end_devices.values():
            if state.lfdi.hex().lower() != lfdi:
                continue
            capability_href = capability_href or state.der_capability_href
            settings_href = settings_href or state.der_settings_href
            status_href = status_href or state.der_status_href

        self._der_resources.start_device(
            lfdi=lfdi,
            capability_href=capability_href,
            settings_href=settings_href,
            status_href=status_href,
            capability_poll_rate=self._capability_poll_rate,
            settings_poll_rate=self._settings_poll_rate,
            status_poll_rate=post_rate,
        )

    async def shutdown(self) -> None:
        """Stop metering, then the DER resource PUTs.

        Metering first because it is the noisier of the two: a reading posted
        during teardown is a reading the server has to reconcile, while a
        DERStatus PUT is idempotent.
        """
        if self._telemetry is not None:
            await self._telemetry.shutdown()
        if self._der_resources is not None:
            await self._der_resources.shutdown()
