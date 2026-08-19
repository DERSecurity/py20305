"""TelemetryManager for IEEE 2030.5 MirrorUsagePoint posting.

Manages the lifecycle of telemetry posting for DER devices:
- Creates MUP on first metering cycle
- Posts meter readings on subsequent cycles
- Handles 404 recovery by re-creating MUP
- Posts LogEvents when alarm status is non-zero
- PUTs DERAvailability when href is discovered
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

from py20305.client.errors import Sep2ConnectionError, Sep2ProtocolError
from py20305.client.polling import PollScheduler
from py20305.client.timebase import ServerTimebase
from py20305.connectors.base import ConnectorError
from py20305.models.sep import MirrorUsagePoint, MirrorUsagePointList
from py20305.readings import (
    QUALITY_SUFFIX,
    DeviceSnapshot,
    DirectConnectorSource,
    MeasurementSource,
    Quality,
)
from py20305.telemetry.der_availability import build_der_availability
from py20305.telemetry.log_events import (
    DER_ALARM_NAMES,
    FUNCTION_SET_DER,
    IEEE_2030_5_PEN,
    MAPPED_ALARM_MASK,
    PROFILE_IEEE_2030_5,
    alarm_bits_to_log_events,
    create_log_event_xml,
    extract_alarm_status,
    unmapped_alarm_bits,
)
from py20305.telemetry.mup import create_meter_reading_list, create_mup
from py20305.xml.serialization import to_xml

if TYPE_CHECKING:
    from py20305.client.http import Sep2Client
    from py20305.connectors.base import BaseConnector, ReadingOverride
    from py20305.connectors.device_telemetry import DeviceTelemetryEmitter

logger = logging.getLogger(__name__)


def _monitoring_payload(
    snapshot: DeviceSnapshot,
) -> tuple[dict[str, Any], dict[str, ReadingOverride] | None]:
    """Rebuild the raw ``fetch_monitoring`` shape the MUP builder expects.

    ``create_meter_reading_list`` reads per-cycle quality inline as
    ``"<key>__quality"``, so the store's structured entries are flattened back
    into that shape here. The bridge disappears when the MUP builder is taught
    to consume snapshots directly, which belongs with the telemetry split
    rather than with the store.
    """
    values: dict[str, Any] = {}
    for key, entry in snapshot.entries.items():
        values[key] = entry.value
        if entry.protocol_quality is not None:
            values[f"{key}{QUALITY_SUFFIX}"] = entry.protocol_quality
    # Passed through whole, including overrides for keys absent from this
    # cycle: create_mup registers the full ReadingType set and those still
    # apply. None rather than {} only because that's what the connector
    # contract yields when it supplies no overrides at all.
    overrides = snapshot.reading_overrides
    return values, dict(overrides) if overrides else None


@dataclass
class DeviceTelemetryState:
    """Per-device telemetry tracking state."""

    lfdi: str
    mup_posted: bool = False
    mup_href: str | None = None
    post_rate: int = 300
    log_event_list_href: str | None = None
    der_availability_href: str | None = None
    log_event_id_counter: int = 0
    #: Last alarm bitmap seen from the connector. LogEvents are posted on
    #: TRANSITION (CSIP s4.6.3: alarms and their return-to-normal messages are
    #: reported "as they occur"), so a persisting alarm isn't re-posted every
    #: metering cycle and a cleared bit still produces its RTN event.
    last_alarm_status: int = 0
    posted_log_events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=20))
    # Set when the server rejects a MUP / readings POST with 403 Forbidden
    # (a conformant server enforces IEEE 2030.5-2023 §10.11.3 Rule e: only the MUP's
    # creator may POST to it). Retrying with the same LFDI cannot make the
    # request succeed -- something external must change (server admin clears
    # state, the operator restarts metering with a different identity, etc.).
    # While ``telemetry_blocked`` is set, the metering cycle skips POST
    # attempts to avoid quietly hammering an unaccepting server.
    #
    # Sticky-flag invariants:
    # * Idempotent ``TelemetryManager.start_metering(lfdi=...)`` calls (a
    #   no-op restart from config reload, etc.) PRESERVE the block. The
    #   carry-over in ``start_metering`` copies ``telemetry_blocked`` /
    #   ``blocked_reason`` from any existing state for this LFDI; otherwise
    #   a re-call would clear the block and resume hammering.
    # * Process restart clears the block (the in-memory state is gone).
    # * ``stop_metering(lfdi)`` followed by ``start_metering(lfdi, ...)``
    #   clears the block. ``stop_metering`` removes the device entry
    #   entirely, so the next ``start_metering`` finds no existing state
    #   to inherit from and defaults to ``telemetry_blocked=False``. This
    #   is the documented recovery path for an operator who has fixed the
    #   server-side identity issue.
    telemetry_blocked: bool = False
    blocked_reason: str | None = None


class TelemetryManager:
    """Manages telemetry posting for all devices.

    The manager handles the full MUP lifecycle:
    1. On first metering cycle, creates and POSTs the MUP
    2. On subsequent cycles, POSTs meter reading updates
    3. On 404 response, resets state to re-create MUP

    Additionally, on each cycle (after the MUP/readings stage):
    4. POSTs LogEvent if alarm status is non-zero
    5. PUTs DERAvailability if href is discovered

    Each posting stage is independently error-handled so failures
    don't cascade across posting types.

    LFDI keys are normalized to lowercase to avoid case mismatch bugs.
    """

    def __init__(
        self,
        client: Sep2Client,
        mup_list_href: str | Callable[[], str],
        connector_resolver: Callable[[str], BaseConnector | Awaitable[BaseConnector]],
        is_provisioned: Callable[[str], bool] | None = None,
        source: MeasurementSource | None = None,
        device_telemetry: DeviceTelemetryEmitter | None = None,
    ) -> None:
        """Initialize the TelemetryManager.

        Args:
            client: IEEE 2030.5 HTTP client
            mup_list_href: Server path for MirrorUsagePointList, or a callable
                returning it. Pass a callable when the path can change under
                you -- rediscovery after an upstream restart moves it, and a
                snapshot would keep posting where the server no longer serves.
            is_provisioned: Optional gate. When set, the metering cycle posts a
                device's MirrorUsagePoint/readings only if ``is_provisioned(lfdi)``
                returns True (e.g. the LFDI is in the server's EndDeviceList).
                Re-checked each cycle, so mirroring starts/stops as the device is
                provisioned/removed. ``None`` (default) mirrors every device.
            connector_resolver: Callback to resolve LFDI -> connector.
                May be either sync (``Callable[[str], BaseConnector]``)
                or async (``Callable[[str], Awaitable[BaseConnector]]``);
                the metering cycle awaits the result iff it's awaitable.
                Async resolvers are preferred so first-touch construction
                of a real Modbus connector can offload its scan +
                readiness retries to a worker thread instead of blocking
                the event loop (see ``_aresolve_connector``
                on the client).
            source: Where measurements come from. Omitted, the manager reads
                the connector directly on every cycle, which is correct for a
                deployment with one upstream interface. A host application serving
                several passes a store-backed source instead, so the metering
                cycle and a concurrent management-API request collapse onto one
                device poll -- the manager does not know which it was given.
            device_telemetry: Reports each device read to the monitoring
                system. Applies to the source constructed here; a caller
                passing its own ``source`` configures it there instead.
                Optional and disabled by default.
        """
        self._client = client
        # Server timebase for telemetry timestamp defaults. isinstance guard
        # keeps AsyncMock clients in tests (whose .timebase is a Mock)
        # falling back to an identity timebase; production Sep2Client always
        # carries the real shared instance.
        tb = getattr(client, "timebase", None)
        self._timebase = tb if isinstance(tb, ServerTimebase) else ServerTimebase()
        #: Read through a callable when one is given, because an upstream
        #: restart makes the client rediscover its resource paths and the
        #: MirrorUsagePointList can move. A snapshot taken at construction
        #: would keep posting to the path the server no longer serves.
        self._mup_list_href_source = (
            mup_list_href if callable(mup_list_href) else (lambda: mup_list_href)
        )
        self._resolve_connector = connector_resolver
        self._is_provisioned = is_provisioned
        # `device_telemetry` reaches the read path only through the source
        # built here; a caller supplying its own `source` wires it there.
        self._source = source or DirectConnectorSource(
            connector_resolver, telemetry=device_telemetry
        )
        self._devices: dict[str, DeviceTelemetryState] = {}
        self._scheduler = PollScheduler()

    def start_metering(
        self,
        lfdi: str,
        post_rate: int,
        log_event_list_href: str | None = None,
        der_availability_href: str | None = None,
    ) -> None:
        """Start metering loop for a device.

        Args:
            lfdi: Device LFDI
            post_rate: Posting interval in seconds
            log_event_list_href: Server path for LogEventList POST
            der_availability_href: Server path for DERAvailability PUT
        """
        lfdi_norm = lfdi.lower()
        existing = self._devices.get(lfdi_norm)
        new_state = DeviceTelemetryState(
            lfdi=lfdi_norm,
            post_rate=post_rate,
            log_event_list_href=log_event_list_href,
            der_availability_href=der_availability_href,
        )
        if existing is not None:
            # Preserve MUP state from a previous metering session so we
            # don't re-POST a MirrorUsagePoint that the server already
            # knows about.
            if existing.mup_posted:
                new_state.mup_posted = existing.mup_posted
                new_state.mup_href = existing.mup_href
                new_state.log_event_id_counter = existing.log_event_id_counter
            # Alarm-transition state is independent of MUP registration, so it
            # carries over unconditionally: an idempotent start_metering (config
            # reload) must not re-announce alarms that are already active.
            new_state.last_alarm_status = existing.last_alarm_status
            # Preserve the Rule e block across an idempotent start_metering
            # call (e.g. config reload, a host application starting device telemetry
            # re-running for an already-started device). Without this carry-
            # over, a re-call of start_metering would silently clear the
            # block and resume hammering an unaccepting server. The
            # documented recovery path is stop_metering -> start_metering:
            # stop_metering removes the device entry entirely, so the
            # second start_metering finds no `existing` to inherit from
            # and the new state defaults to telemetry_blocked=False.
            new_state.telemetry_blocked = existing.telemetry_blocked
            new_state.blocked_reason = existing.blocked_reason
        self._devices[lfdi_norm] = new_state
        # The planner acquires on this cadence; the metering cycle reads what
        # it stored. Declared as post_rate so a default deployment sees the
        # same device poll rate it did before acquisition was decoupled.
        self._source.declare(lfdi_norm, float(post_rate))
        callback: Callable[[], Awaitable[None]] = partial(self._metering_cycle, lfdi_norm)
        self._scheduler.schedule(
            f"metering_{lfdi_norm}",
            post_rate,
            callback,
        )
        logger.info("Started metering for device %s at %ds interval", lfdi_norm[:8], post_rate)

    def stop_metering(self, lfdi: str) -> None:
        """Stop metering for a device.

        Args:
            lfdi: Device LFDI
        """
        lfdi_norm = lfdi.lower()
        if lfdi_norm in self._devices:
            del self._devices[lfdi_norm]
            self._source.release(lfdi_norm)
            # Scheduler will handle task cleanup on next cancel_all

    async def _metering_cycle(self, lfdi: str) -> None:
        """Single metering cycle: MUP/readings, then LogEvent and DERAvailability.

        Each posting stage is independently try/excepted so failures in one
        don't prevent the others from executing.
        """
        state = self._devices.get(lfdi)
        if state is None:
            logger.warning("Metering cycle for unknown device %s", lfdi[:8])
            return

        # Server enforced Rule e and rejected this device's POST. The blocked
        # flag is sticky -- retrying with the same LFDI can't recover, so we
        # short-circuit here rather than hammering the server every cycle.
        # The diagnostics entry that set the flag is the operator-visible
        # signal; the per-cycle log here stays at debug to avoid log spam.
        if state.telemetry_blocked:
            logger.debug(
                "Telemetry cycle skipped for %s (blocked: %s)",
                state.lfdi[:8],
                state.blocked_reason or "unspecified",
            )
            return

        # only_mirror_discovered_devices gate: skip mirroring a device that the
        # server hasn't provisioned (not in its EndDeviceList). Re-checked each
        # cycle, so it starts automatically once the device is provisioned.
        if self._is_provisioned is not None and not self._is_provisioned(lfdi):
            logger.debug(
                "Metering cycle for %s skipped: device not in the server's "
                "EndDeviceList (only_mirror_discovered_devices)",
                state.lfdi[:8],
            )
            return

        logger.debug(
            "Metering cycle for %s: mup_posted=%s, mup_href=%s",
            state.lfdi[:8],
            state.mup_posted,
            state.mup_href,
        )

        connector_name = "unknown"
        try:
            # First-touch construction can block (Modbus scan + retries);
            # the async resolver path offloads that to a worker thread on
            # the first call and short-circuits on subsequent cycles
            #. Sync resolvers are still accepted for backward
            # compat with embedded callers that wire in a custom callback.
            # Still resolved here for the LogEvent and DERAvailability stages,
            # which read the connector directly.
            result = self._resolve_connector(lfdi)
            connector = await result if inspect.isawaitable(result) else result
            connector_name = type(connector).__name__
        except Exception as e:
            self._report_acquisition_failure(lfdi, connector_name, e)
            return

        # The source decides whether that costs a device read. Freshness comes
        # back already resolved against whatever demand it knows about, so the
        # cycle below reasons about quality without knowing the cadence.
        #
        # The source resolves the connector itself rather than being handed the
        # one resolved above: taking a pre-resolved connector would put the
        # southbound object back into an interface whose whole purpose is to
        # keep it out. That costs a second resolver call per cycle, which is a
        # cache hit for any memoizing resolver -- the documented requirement on
        # both this source and AcquisitionService.
        snapshot = await self._source.read(lfdi)

        if snapshot.last_success is None:
            # Never read successfully -- there is no value to post, not even a
            # stale one. Distinct from comm-lost with history, which does post.
            self._report_acquisition_failure(lfdi, connector_name, snapshot.error)
            return

        if snapshot.quality is not Quality.GOOD:
            # Keep posting. MUP.013/014 are replace-on-receipt, so
            # omitting leaves the pre-outage value live on the server with no
            # staleness marker -- the fabrication that flagging avoids.
            self._report_acquisition_failure(lfdi, connector_name, snapshot.error)

        monitoring, overrides = _monitoring_payload(snapshot)
        # The acquisition instant, carried into the server timebase rather
        # than read from it. A second now() call would reintroduce post time.
        acquired_at = int(snapshot.last_success + self._timebase.offset())
        # Post cadence, not acquisition cadence -- see create_meter_reading_list.
        # The planner may read sooner (a tighter consumer) or much later (a
        # device in backoff); the next POST is the one thing we can promise.
        next_update = int(self._timebase.now()) + state.post_rate
        stale = snapshot.quality is not Quality.GOOD

        # Stage 1: MUP creation or meter readings
        if not state.mup_posted:
            await self._post_mup(state, monitoring, overrides)
        else:
            await self._post_readings(
                state,
                monitoring,
                overrides,
                timestamp=acquired_at,
                next_update_time=next_update,
                stale=stale,
            )

        # Stage 2: LogEvent POST (alarm-driven)
        await self._post_log_event(state, connector)

        # Stage 3: DERAvailability PUT
        await self._put_der_availability(state, connector)

    def _report_acquisition_failure(
        self, lfdi: str, connector_name: str, error: Exception | None
    ) -> None:
        """Report a failed monitoring acquisition to diagnostics.

        Kept here rather than in the store layer so the operator-facing
        identity (source, dedup key, wording) stays with the consumer that
        cares about it -- the management API describes the same failure
        differently.
        """
        if error is None:
            return

        from py20305.diagnostics import report

        details = {"lfdi": lfdi, "connector": connector_name, "error": str(error)}
        if isinstance(error, ConnectorError | OSError):
            report(
                "warnings",
                f"Connector unreachable for {lfdi[:8]} ({connector_name}): {error}",
                source="telemetry",
                dedup_key=f"telemetry:{lfdi}:connector_unreachable",
                details=details,
            )
        else:
            report(
                "warnings",
                f"Failed to fetch monitoring for {lfdi[:8]} from {connector_name}: {error}",
                source="telemetry",
                dedup_key=f"telemetry:{lfdi}:fetch_monitoring",
                details=details,
                exc_info=error,
            )

    async def _post_mup(
        self,
        state: DeviceTelemetryState,
        monitoring_data: dict[str, Any],
        overrides: dict[str, ReadingOverride] | None = None,
    ) -> None:
        """POST MUP to server, track location and server-preferred postRate.

        After posting MUP, we do NOT post readings on this cycle.
        Readings begin on the next metering cycle.

        IEEE B.17.1: The server MAY modify postRate to indicate its preferred
        posting rate. We read back the created MUP to honour the server's rate.
        """
        mup = create_mup(state.lfdi, monitoring_data, state.post_rate, overrides)
        body = to_xml(mup, server_2018_compat=self._client.server_2018_compat)

        try:
            location = await self._client.post_bytes(self._mup_list_href_source(), body)
            state.mup_posted = True

            if location:
                state.mup_href = location
                logger.debug("MUP posted for %s, location: %s", state.lfdi[:8], location)
                await self._readback_post_rate(state, location)
            else:
                logger.debug("MUP posted for %s, no location header", state.lfdi[:8])
        except Sep2ProtocolError as e:
            if e.status_code == 403:
                # IEEE 2030.5-2023 §10.11.3 Rule e + a.4: the body's mRID
                # already exists on the server and is owned by a different
                # LFDI. The server treats this as a duplicate-mRID re-POST
                # from a non-creator and refuses. Same blocking semantics
                # as the readings 403 -- retry can't help.
                from py20305.diagnostics import report

                state.telemetry_blocked = True
                state.blocked_reason = (
                    f"403 Forbidden on POST {self._mup_list_href_source()} -- the MUP "
                    "mRID already exists on the server under a different "
                    "creator LFDI (Rule e). Manual intervention required."
                )
                report(
                    "errors",
                    (
                        f"Telemetry blocked for {state.lfdi[:8]}: server returned "
                        f"403 Forbidden on initial POST {self._mup_list_href_source()}. "
                        "The MUP's mRID is already owned by a different LFDI on "
                        "the server (per IEEE 2030.5-2023 §10.11.3 Rule e). "
                        "Subsequent metering cycles will skip until restarted."
                    ),
                    source="telemetry",
                    dedup_key=f"telemetry:{state.lfdi}:rule_e_blocked",
                    details={
                        "lfdi": state.lfdi,
                        "op": "mup_post",
                        "mup_list_href": self._mup_list_href_source(),
                        "status_code": 403,
                        "error": str(e),
                    },
                )
                return
            from py20305.client.errors import compat_hint_suffix
            from py20305.diagnostics import report

            hint = compat_hint_suffix(e, self._client.server_2018_compat)
            report(
                "warnings",
                (
                    f"Failed to POST MUP for {state.lfdi[:8]}: "
                    f"status={e.status_code}, error={e}{hint}"
                ),
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:mup_post",
                details={
                    "lfdi": state.lfdi,
                    "op": "mup_post",
                    "status_code": e.status_code,
                    "error": str(e),
                },
            )
        except Sep2ConnectionError as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Server unreachable posting MUP for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:mup_post",
                details={"lfdi": state.lfdi, "op": "mup_post", "error": str(e)},
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Unexpected error posting MUP for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:mup_post",
                details={"lfdi": state.lfdi, "op": "mup_post", "error": str(e)},
                exc_info=True,
            )

    async def _readback_post_rate(
        self,
        state: DeviceTelemetryState,
        mup_href: str,
    ) -> None:
        """GET the created MUP and adopt server-preferred postRate if changed."""
        try:
            mup = await self._client.get(mup_href, MirrorUsagePoint)
            if mup.post_rate is not None and mup.post_rate != state.post_rate:
                logger.info(
                    "Server adjusted postRate for %s: %d -> %d",
                    state.lfdi[:8],
                    state.post_rate,
                    mup.post_rate,
                )
                state.post_rate = mup.post_rate
        except Exception:
            logger.debug("Could not read back MUP postRate for %s", state.lfdi[:8])

    async def _post_readings(
        self,
        state: DeviceTelemetryState,
        monitoring_data: dict[str, Any],
        overrides: dict[str, ReadingOverride] | None = None,
        *,
        timestamp: int | None = None,
        next_update_time: int | None = None,
        stale: bool = False,
    ) -> None:
        """POST meter readings to MUP location.

        ``timestamp`` is the acquisition instant in the server timebase.
        Defaults to post time only for callers that have no acquisition to
        cite, which after the poll planner is just tests.
        """
        # Discover MUP href if not known
        if not state.mup_href:
            state.mup_href = await self._discover_mup_href(state.lfdi)

        if not state.mup_href:
            logger.warning("No MUP href for %s, cannot post readings", state.lfdi[:8])
            return

        readings = create_meter_reading_list(
            state.lfdi,
            monitoring_data,
            timestamp=int(self._timebase.now()) if timestamp is None else timestamp,
            overrides=overrides,
            post_rate=state.post_rate,
            next_update_time=next_update_time,
            stale=stale,
        )
        body = to_xml(readings, server_2018_compat=self._client.server_2018_compat)

        try:
            await self._client.post_bytes(state.mup_href, body)
            logger.debug("Readings posted for %s", state.lfdi[:8])
        except Sep2ProtocolError as e:
            if e.status_code in (400, 404):
                # 404: MUP was deleted by server, reset state to recreate.
                # 400: server rejected the readings POST. The new IEEE
                # 2030.5-2023 §10.11.3 Rule h.3 enforcement on /mup/{N}
                # produces 400 in two scenarios we can recover from:
                # (a) version skew -- server upgraded ahead of a client
                # that still uses unstable mRIDs; (b) server lost
                # /upt/{N}/mr state while /mup/{N} survived. Both recover
                # via re-POST /mup, which goes through Rule a.4 and rebuilds
                # /upt/{N}/mr from the readings loop. We don't know for
                # sure that 400 means "stale state" (it could also be a
                # malformed body) but the recovery cost is low: a redundant
                # MUP re-POST either succeeds or also 400s, in which case
                # the operator sees a persistent failure on the MUP path
                # -- a much clearer signal than an infinite readings retry.
                logger.info("MUP %s for %s, will recreate", e.status_code, state.lfdi[:8])
                state.mup_posted = False
                state.mup_href = None
            elif e.status_code == 403:
                # A conformant server (IEEE 2030.5-2023 §10.11.3 Rule e) rejects
                # POSTs to a MUP from any LFDI other than its creator's.
                # Retrying with the same LFDI cannot recover. Mark the
                # device as blocked so subsequent cycles skip cleanly,
                # and surface to the operator at error level. Recreating
                # the MUP from a different LFDI is intentionally NOT
                # automatic -- it would either succeed (defeating the
                # protection) or also 403 with no progress.
                from py20305.diagnostics import report

                state.telemetry_blocked = True
                state.blocked_reason = (
                    f"403 Forbidden on POST {state.mup_href} -- the server "
                    "treats this LFDI as not the MUP creator (Rule e). "
                    "Manual intervention required."
                )
                report(
                    "errors",
                    (
                        f"Telemetry blocked for {state.lfdi[:8]}: server returned "
                        f"403 Forbidden on POST {state.mup_href}. The MUP was "
                        "created by a different client (per IEEE 2030.5-2023 "
                        "§10.11.3 Rule e). Subsequent metering cycles will "
                        "skip POSTs until the device is restarted or the "
                        "server-side state is cleared."
                    ),
                    source="telemetry",
                    dedup_key=f"telemetry:{state.lfdi}:rule_e_blocked",
                    details={
                        "lfdi": state.lfdi,
                        "op": "readings_post",
                        "mup_href": state.mup_href,
                        "status_code": 403,
                        "error": str(e),
                    },
                )
            else:
                from py20305.client.errors import compat_hint_suffix
                from py20305.diagnostics import report

                hint = compat_hint_suffix(e, self._client.server_2018_compat)
                report(
                    "warnings",
                    f"Failed to POST readings for {state.lfdi[:8]}: {e}{hint}",
                    source="telemetry",
                    dedup_key=f"telemetry:{state.lfdi}:readings_post",
                    details={
                        "lfdi": state.lfdi,
                        "op": "readings_post",
                        "status_code": e.status_code,
                        "error": str(e),
                    },
                )
        except Sep2ConnectionError as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Server unreachable posting readings for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:readings_post",
                details={"lfdi": state.lfdi, "op": "readings_post", "error": str(e)},
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Unexpected error posting readings for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:readings_post",
                details={"lfdi": state.lfdi, "op": "readings_post", "error": str(e)},
                exc_info=True,
            )

    async def _post_log_event(
        self,
        state: DeviceTelemetryState,
        connector: BaseConnector,
    ) -> None:
        """POST LogEvents for DER alarm transitions (CSIP s4.6.3 / s5.2.5.3).

        One LogEvent per changed alarm bit: a newly-set bit posts its alarm
        code, a newly-cleared bit posts its return-to-normal code. A persisting
        alarm posts nothing (alarms are reported "as they occur"), and a clear
        to zero still posts the RTNs -- so this deliberately has no
        ``alarm == 0`` early-out.
        """
        if not state.log_event_list_href:
            return

        try:
            status = await connector.fetch_status()
            alarm = extract_alarm_status(status)
            events = alarm_bits_to_log_events(state.last_alarm_status, alarm)
            # Sync bits that can never produce a LogEvent (IEEE-reserved, 11+)
            # straight into the baseline -- there is nothing to post for them,
            # so they must not keep re-appearing in the diff every cycle. Bits
            # that DO map are advanced one at a time below, only once their
            # POST has landed.
            state.last_alarm_status = (state.last_alarm_status & MAPPED_ALARM_MASK) | (
                alarm & ~MAPPED_ALARM_MASK
            )

            unmapped = unmapped_alarm_bits(alarm)
            if unmapped:
                from py20305.diagnostics import report

                report(
                    "info",
                    f"DER alarm bits {unmapped} on {state.lfdi[:8]} have no "
                    "IEEE 2030.5-assigned LogEvent code (bits 11+ are reserved); "
                    "they are still reported in DERStatus alarmStatus.",
                    source="telemetry",
                    dedup_key=f"telemetry:{state.lfdi}:unmapped_alarm_bits",
                    details={"lfdi": state.lfdi, "bits": unmapped, "alarm_status": alarm},
                )

            for bit, code in events:
                await self._post_and_record_log_event(state, code, details=DER_ALARM_NAMES.get(bit))
                # Advance the baseline one bit at a time, AFTER the POST lands.
                # If a later POST fails, the bits already delivered stay synced
                # and the rest remain pending, so the next cycle retries exactly
                # the transitions the server never received (CSIP s4.6.3 requires
                # alarms and their RTNs to be reported).
                if alarm & (1 << bit):
                    state.last_alarm_status |= 1 << bit
                else:
                    state.last_alarm_status &= ~(1 << bit)
                logger.debug(
                    "LogEvent posted for %s: bit=%d code=%d (%s)",
                    state.lfdi[:8],
                    bit,
                    code,
                    DER_ALARM_NAMES.get(bit, "?"),
                )
        except (Sep2ConnectionError, ConnectorError, OSError) as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Failed to post LogEvent for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:logevent_post",
                details={"lfdi": state.lfdi, "op": "logevent_post", "error": str(e)},
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Unexpected error posting LogEvent for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:logevent_post",
                details={"lfdi": state.lfdi, "op": "logevent_post", "error": str(e)},
                exc_info=True,
            )

    async def post_log_event(
        self,
        state: DeviceTelemetryState,
        alarm_status: int,
        details: str | None = None,
    ) -> None:
        """POST a manually triggered LogEvent for a device.

        Args:
            state: Device telemetry state (must have log_event_list_href).
            alarm_status: Alarm status bitmap.
            details: Optional human-readable detail string.

        Raises:
            Sep2ProtocolError: If the POST fails.
            ValueError: If device has no log_event_list_href.
        """
        if not state.log_event_list_href:
            raise ValueError(f"Device {state.lfdi[:8]} has no log_event_list_href")
        # Fan out one LogEvent per set alarm bit, using the IEEE-assigned code
        # for each. alarm_status=0 means "no alarms" and posts nothing -- code 0
        # is Over Current, so emitting it for an empty bitmap would report a
        # fault that isn't there.
        for bit, code in alarm_bits_to_log_events(0, alarm_status):
            await self._post_and_record_log_event(
                state, code, details=details or DER_ALARM_NAMES.get(bit)
            )
            logger.debug("LogEvent triggered for %s: bit=%d code=%d", state.lfdi[:8], bit, code)

    async def _post_and_record_log_event(
        self,
        state: DeviceTelemetryState,
        log_event_code: int,
        details: str | None = None,
    ) -> None:
        """POST one LogEvent and record it for the management API.

        Single place that builds the wire body and the ``posted_log_events``
        entry, so the code surfaced by ``GET /api/v1/logevents`` can't drift
        from the code actually sent.
        """
        assert state.log_event_list_href is not None  # callers check
        xml = create_log_event_xml(
            log_event_code,
            log_event_id=state.log_event_id_counter,
            details=details,
            server_2018_compat=self._client.server_2018_compat,
            created_time=int(self._timebase.now()),
        )
        await self._client.post_bytes(state.log_event_list_href, xml)
        state.posted_log_events.append(
            {
                "logEventID": state.log_event_id_counter,
                "functionSet": FUNCTION_SET_DER,
                "logEventCode": log_event_code,
                "logEventPEN": IEEE_2030_5_PEN,
                "profileID": PROFILE_IEEE_2030_5,
                "timestamp": time.time(),
            }
        )
        state.log_event_id_counter += 1

    _LOG_EVENT_BURST_COUNT = 5  # BASIC-027 requires 5 LogEvents per trigger

    async def post_log_event_burst(
        self,
        lfdi: str,
        alarm_status: int = 1,
        details: str | None = None,
        interval: float = 1.0,
    ) -> int:
        """Post a burst of LogEvents for BASIC-027 conformance.

        Posts exactly ``_LOG_EVENT_BURST_COUNT`` LogEvents with incrementing
        logEventIDs, spaced by *interval* seconds so each has a distinct
        createdDateTime.

        ``alarm_status`` must resolve to exactly ONE mapped alarm bit. The
        burst posts that bit's code N times, so the returned count is a true
        POST count (the caller derives ``log_event_ids`` from it). Routing a
        multi-bit bitmap through here would post N x bits events while
        reporting N -- and BASIC-027 wants exactly N.

        Args:
            lfdi: Device LFDI (case-insensitive).
            alarm_status: Alarm bitmap with exactly one IEEE-mapped bit set.
            details: Optional human-readable detail string.
            interval: Seconds between each POST (0 = no delay).

        Returns:
            Number of events successfully posted.

        Raises:
            ValueError: If the device is unknown, has no log_event_list_href,
                or ``alarm_status`` does not resolve to exactly one mapped bit.
        """
        state = self.get_device_state(lfdi)
        if state is None:
            raise ValueError(f"Device {lfdi[:8]}... not found in telemetry manager")
        if not state.log_event_list_href:
            raise ValueError(f"Device {lfdi[:8]}... has no log_event_list_href")

        events = alarm_bits_to_log_events(0, alarm_status)
        if len(events) != 1:
            raise ValueError(
                f"LogEvent burst needs exactly one IEEE-mapped alarm bit; "
                f"alarm_status=0x{alarm_status:X} resolves to {len(events)} "
                f"(0 = no mapped bit set -- bits 11+ are reserved; "
                f">1 = multi-bit, which would post {len(events)} events per iteration)"
            )
        bit, code = events[0]

        posted = 0
        for i in range(self._LOG_EVENT_BURST_COUNT):
            if interval > 0 and i > 0:
                await asyncio.sleep(interval)
            try:
                await self._post_and_record_log_event(
                    state, code, details=details or DER_ALARM_NAMES.get(bit)
                )
                posted += 1
            except Exception:
                logger.exception("LogEvent burst POST %d failed for %s", i, lfdi[:8])
                break

        logger.info(
            "LogEvent burst for %s: posted %d/%d events",
            lfdi[:8],
            posted,
            self._LOG_EVENT_BURST_COUNT,
        )
        return posted

    def find_device_with_log_events(self) -> str | None:
        """Return the first active device LFDI that has a log_event_list_href."""
        for lfdi, state in self._devices.items():
            if state.log_event_list_href:
                return lfdi
        return None

    async def _put_der_availability(
        self,
        state: DeviceTelemetryState,
        connector: BaseConnector,
    ) -> None:
        """PUT DERAvailability if href is discovered."""
        if not state.der_availability_href:
            return

        try:
            data = await connector.fetch_availability()
            model = build_der_availability(data, default_time=int(self._timebase.now()))
            await self._client.put_bytes(
                state.der_availability_href,
                to_xml(model, server_2018_compat=self._client.server_2018_compat),
            )
            logger.debug("DERAvailability PUT for %s", state.lfdi[:8])
        except (Sep2ConnectionError, ConnectorError, OSError) as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Failed to PUT DERAvailability for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:der_availability_put",
                details={
                    "lfdi": state.lfdi,
                    "op": "der_availability_put",
                    "error": str(e),
                },
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Unexpected error putting DERAvailability for {state.lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{state.lfdi}:der_availability_put",
                details={
                    "lfdi": state.lfdi,
                    "op": "der_availability_put",
                    "error": str(e),
                },
                exc_info=True,
            )

    async def _discover_mup_href(self, lfdi: str) -> str | None:
        """Scan server MUP list to find our MUP by deviceLFDI.

        This is a fallback when the server didn't return a Location header
        after the MUP POST.
        """
        try:
            pages = await self._client.get_list(self._mup_list_href_source(), MirrorUsagePointList)
            for page in pages:
                for mup in page.mirror_usage_point or []:
                    if mup.device_lfdi and mup.device_lfdi.hex().lower() == lfdi:
                        logger.debug("Discovered MUP href for %s: %s", lfdi[:8], mup.href)
                        return mup.href
        except Sep2ConnectionError as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Server unreachable discovering MUP for {lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{lfdi}:mup_discovery",
                details={"lfdi": lfdi, "op": "mup_discovery", "error": str(e)},
            )
        except Exception as e:
            from py20305.diagnostics import report

            report(
                "warnings",
                f"Failed to discover MUP href for {lfdi[:8]}: {e}",
                source="telemetry",
                dedup_key=f"telemetry:{lfdi}:mup_discovery",
                details={"lfdi": lfdi, "op": "mup_discovery", "error": str(e)},
                exc_info=True,
            )

        return None

    @property
    def active_devices(self) -> list[str]:
        """Return list of LFDIs with active metering."""
        return list(self._devices.keys())

    def get_device_state(self, lfdi: str) -> DeviceTelemetryState | None:
        """Get telemetry state for a device."""
        return self._devices.get(lfdi.lower())

    def get_all_posted_log_events(self) -> list[dict[str, Any]]:
        """Return all posted log events across all devices."""
        events: list[dict[str, Any]] = []
        for state in self._devices.values():
            for ev in state.posted_log_events:
                events.append({**ev, "device": state.lfdi[:16]})
        events.sort(key=lambda e: e["timestamp"])
        return events

    async def shutdown(self) -> None:
        """Stop all metering tasks."""
        await self._scheduler.cancel_all()
        self._devices.clear()
        logger.info("TelemetryManager shutdown complete")
