"""Service layer behind the management API.

Holds the response shaping and the business logic so the route handlers in
:mod:`py20305.api.client_routes` stay thin.

:class:`ClientAPIService` operates on a :class:`~py20305.client.csip_client.CsipClient`,
a :class:`~py20305.telemetry.manager.TelemetryManager` and a
:class:`~py20305.telemetry.der_resource_manager.DerResourceManager`
and needs nothing else, so anything embedding the client gets the API for
free. It is also designed to be subclassed: an application managing many
devices can extend it with its own endpoints without reimplementing these.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, TypeVar

from py20305.api.serializers import (
    serialize_der_program,
    serialize_device_info,
    serialize_tariff_profile,
)
from py20305.client.timebase import ServerTimebase
from py20305.events.state_machine import EventState as _EventState
from py20305.json_form import (
    safe_serialize,
    serialize_mrid,
    unwrap_value,
)

if TYPE_CHECKING:
    from py20305.client.csip_client import CsipClient
    from py20305.telemetry.der_resource_manager import DerResourceManager
    from py20305.telemetry.manager import TelemetryManager

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _as_int(value: Any) -> int | None:
    """Unwrap a SEP scalar to ``int``, or ``None`` if it is not one.

    Tolerant on purpose: this reads fields off a resource that may be absent,
    partially populated, or a test double, and a missing time zone is a field
    to omit from the response rather than a request to fail.
    """
    unwrapped = unwrap_value(value)
    if isinstance(unwrapped, bool) or not isinstance(unwrapped, (int, float)):
        return None
    return int(unwrapped)


def unavailable_time_body(*, enabled: bool | None = None) -> dict[str, Any]:
    """The ``GET /time`` body for "no server-true reading exists".

    Shared with the route layer so the shape a consumer parses does not
    depend on *which* reason it got -- no observation yet, or no client
    connected at all.
    """
    return {
        "current_time": None,
        "local_time": None,
        "tz_offset": None,
        "dst_offset": None,
        "dst_active": False,
        "quality": None,
        "source": "unavailable",
        "offset_seconds": None,
        "age_seconds": None,
        "href": None,
        "timebase_enabled": enabled,
    }


class ClientAPIService:
    """Service layer for client-level API operations.

    Works with ``CsipClient``, ``TelemetryManager`` and ``DerResourceManager``
    -- the components anything embedding this client already has -- so no
    further wiring is needed to expose the API. Subclass it to add operations
    of your own; the routes in :mod:`py20305.api.client_routes`
    resolve against whatever instance the app is built with.
    """

    def __init__(
        self,
        client: CsipClient,
        telemetry: TelemetryManager | None = None,
        der_resources: DerResourceManager | None = None,
        http_msg_state: dict[str, Any] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._client = client
        self._telemetry = telemetry
        self._der_resources = der_resources
        self._http_msg_state = http_msg_state or {
            "enabled": False,
            "last_updated": None,
            "redirect_probe": None,
        }
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._loop = loop

    def attach_telemetry(
        self,
        telemetry: TelemetryManager | None = None,
        der_resources: DerResourceManager | None = None,
    ) -> None:
        """Supply the telemetry managers after construction.

        An API served during an outage has to start before discovery, and the
        managers report on hrefs discovery finds -- so they exist only later.
        Without this the telemetry endpoints would answer "not initialized" for
        the lifetime of a process that is posting readings.
        """
        self._telemetry = telemetry
        self._der_resources = der_resources

    async def _run_on_loop(self, coro: Coroutine[Any, Any, T]) -> T:
        """Await *coro* on the correct event loop.

        When a ``loop`` was provided at construction time (embedded consumer
        running the client on a background thread), the coroutine is scheduled
        on that loop via ``run_coroutine_threadsafe`` and the result is awaited
        in a thread-safe manner.  Otherwise the coroutine runs directly on the
        current (caller's) event loop.
        """
        if self._loop is None:
            return await coro
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return await asyncio.wrap_future(future)

    # -- Status ---------------------------------------------------------------

    def get_status(self) -> dict[str, Any]:
        """Return client status for /status endpoint."""
        state = self._client.state
        first_edev = next(iter(state.end_devices.values()), None)
        lfdi = first_edev.lfdi.hex() if first_edev else None

        http_client = self._client.http
        last_contact = http_client.last_contact_epoch
        # isinstance guard: tests stub http with Mocks; production Sep2Client
        # always carries the real shared timebase.
        tb = getattr(http_client, "timebase", None)
        timebase_snapshot = tb.snapshot() if isinstance(tb, ServerTimebase) else None
        result: dict[str, Any] = {
            "server_alive": http_client.server_alive,
            "server_host": http_client.host,
            "lfdi": lfdi,
            "devices_discovered": len(state.end_devices),
            "programs_discovered": len(state.der_programs),
            "poll_rates": {k: v for k, v in state.poll_rates.items() if v is not None},
            "status": "running",
            # Connectivity health across ALL request types (not just GET polls),
            # so a consumer can detect a dead server even while only telemetry
            # PUT/POSTs are in flight. Prefer `seconds_since_last_contact` over
            # `server_alive` for staleness checks.
            "last_contact_epoch": last_contact,
            # Clamped at 0 so a backwards clock step (NTP) can't yield a negative
            # age that breaks a consumer's staleness comparison.
            "seconds_since_last_contact": (
                max(0, int(time.time()) - last_contact) if last_contact is not None else None
            ),
            "consecutive_failures": http_client.consecutive_failures,
            # Application-level server timebase: offset/quality/age per
            # scope. Lets an operator see live clock drift against the
            # head-end.
            "timebase": timebase_snapshot,
        }
        if http_client.last_error is not None:
            result["last_error"] = http_client.last_error
        return result

    # -- Time -----------------------------------------------------------------

    def get_time(self) -> dict[str, Any]:
        """Return the head-end's current time, already corrected for local drift.

        The Time function set is the only clock a client on an isolated network
        can reach: field devices are routinely deployed where the head-end is
        the sole reachable host, so NTP is unavailable and the on-board clock
        free-runs. Such a device can read the offset out of ``/status`` and add
        it to its own clock, but that puts the arithmetic -- and the mistake of
        skipping the freshness and availability checks -- in every consumer.
        This endpoint does it once, here.

        ``current_time`` is the observed offset applied to the local clock, not
        a cached copy of the last ``currentTime`` seen, so it advances between
        polls instead of jumping. ``local_time`` is computed the same way
        rather than echoing the server's ``localTime`` element, which is a
        snapshot of the moment that resource was fetched and is stale by
        ``age_seconds``.

        ``source`` is ``"unavailable"`` when no Time resource has been observed
        yet, and every derived field is then ``None`` -- deliberately, rather
        than serving the raw local clock, which is the value most likely to be
        wrong and the one a caller is least able to detect.
        """
        timebase = getattr(self._client.http, "timebase", None)
        # isinstance rather than truthiness: tests stub http with Mocks, and a
        # Mock attribute would otherwise sail through and produce a Mock time.
        if not isinstance(timebase, ServerTimebase):
            return unavailable_time_body(enabled=None)

        snapshot = timebase.snapshot()
        observation = snapshot.get("global")
        server_now = timebase.server_now()
        if server_now is None or observation is None:
            return unavailable_time_body(enabled=bool(snapshot.get("enabled")))

        current_time = int(server_now)
        tz_offset, dst_offset, dst_active = self._local_offsets(current_time)
        local_time: int | None = None
        if tz_offset is not None:
            applied_dst = (dst_offset or 0) if dst_active else 0
            local_time = current_time + tz_offset + applied_dst

        return {
            "current_time": current_time,
            "local_time": local_time,
            "tz_offset": tz_offset,
            "dst_offset": dst_offset,
            "dst_active": dst_active,
            "quality": observation.get("quality"),
            "source": "server",
            "offset_seconds": observation.get("offset_seconds"),
            "age_seconds": observation.get("age_seconds"),
            "href": observation.get("href") or None,
            # Whether this client *applies* the offset to its own scheduling.
            # Independent of the reading above: the offset is observed and
            # reported either way, so a consumer syncing a clock is not
            # silently switched to local time by an operator's troubleshooting
            # setting.
            "timebase_enabled": bool(snapshot.get("enabled")),
        }

    def _local_offsets(self, current_time: int) -> tuple[int | None, int | None, bool]:
        """Return ``(tz_offset, dst_offset, dst_active)`` from the last Time resource.

        Whether daylight saving is in effect is decided against the server's own
        ``dstStartTime``/``dstEndTime`` instants rather than a local tz database,
        which the head-end may disagree with. Per IEEE 2030.5 a zero
        ``dstOffset`` means those two instants carry no meaning and are ignored.
        """
        time_resource = getattr(self._client.state, "time", None)
        tz_offset = _as_int(getattr(time_resource, "tz_offset", None))
        dst_offset = _as_int(getattr(time_resource, "dst_offset", None))
        if not dst_offset:
            return tz_offset, dst_offset, False

        start = _as_int(getattr(time_resource, "dst_start_time", None))
        end = _as_int(getattr(time_resource, "dst_end_time", None))
        if start is None or end is None:
            return tz_offset, dst_offset, False

        # A window whose end precedes its start is a southern-hemisphere season
        # crossing the year boundary, not bad data -- it is active outside the
        # interval rather than inside it.
        if start <= end:
            dst_active = start <= current_time < end
        else:
            dst_active = current_time >= start or current_time < end
        return tz_offset, dst_offset, dst_active

    # -- Devices --------------------------------------------------------------

    def get_devices(self) -> dict[str, Any]:
        """Return device list for /devices endpoint."""
        devices = []
        for href, edev_state in self._client.state.end_devices.items():
            devices.append(serialize_device_info(href, edev_state, is_client_mode=True))
        return {"devices": devices}

    # -- DER Programs & Controls ----------------------------------------------

    def get_der_programs(self) -> dict[str, Any]:
        """Return DER programs for debugging."""
        programs = []
        for href, program_state in self._client.state.der_programs.items():
            programs.append(serialize_der_program(href, program_state))
        return {"programs": programs}

    async def get_tariffs(self) -> dict[str, Any]:
        """Return discovered Pricing tariff profiles with per-interval prices.

        Reads the discovered tariff schedule from client state and fetches each
        interval's ConsumptionTariffInterval prices (on the client's loop). The
        active interval is flagged against the FSA-scoped server clock, matching
        the relay's selection.
        """
        state = self._client.state
        tps = list(getattr(state, "tariff_profiles", {}).values())
        if not tps:
            return {"profiles": []}
        intervals = [
            iv for tp in tps for rc in tp.rate_components for iv in rc.time_tariff_intervals
        ]
        prices_by_mrid = await self._run_on_loop(self._fetch_tariff_prices(intervals))
        timebase = getattr(self._client.http, "timebase", None)
        profiles = []
        for tp in tps:
            if isinstance(timebase, ServerTimebase):
                now = int(timebase.now(tp.discovered_from_fsa_href))
            else:
                now = int(time.time())
            profiles.append(serialize_tariff_profile(tp, prices_by_mrid, now))
        return {"profiles": profiles}

    async def _fetch_tariff_prices(self, intervals: list[Any]) -> dict[str, list[dict[str, Any]]]:
        """Fetch each interval's ConsumptionTariffInterval blocks, keyed by mRID.

        Runs on the client's event loop (via ``_run_on_loop``) so it uses the
        client's HTTP session. Per-interval fetch failures degrade to an empty
        price list rather than failing the whole page.
        """
        from py20305.models.sep.sep import ConsumptionTariffIntervalList

        async def _one(interval: Any) -> tuple[str | None, list[dict[str, Any]]]:
            mrid = serialize_mrid(interval.m_rid)
            link = getattr(interval, "consumption_tariff_interval_list_link", None)
            href = getattr(link, "href", None) if link is not None else None
            if not href:
                return mrid, []
            try:
                pages = await self._client.http.get_list(href, ConsumptionTariffIntervalList)
            except Exception:
                return mrid, []
            blocks = [
                {
                    "price": cti.price,
                    "startValue": cti.start_value,
                    "consumptionBlock": unwrap_value(cti.consumption_block),
                }
                for page in pages
                for cti in page.consumption_tariff_interval
            ]
            return mrid, blocks

        results = await asyncio.gather(*(_one(iv) for iv in intervals))
        return {mrid: blocks for mrid, blocks in results if mrid is not None}

    def get_derc_controls(self) -> dict[str, Any]:
        """Return DER control state for /derc_controls endpoint."""
        state = self._client.state

        dderc_dict: dict[str, Any] = {}
        default_controls: dict[str, Any] = {}
        for href, program_state in state.der_programs.items():
            if program_state.default_dercontrol:
                key = href.rsplit("/", 1)[-1]
                dderc_dict[key] = safe_serialize(program_state.default_dercontrol)
                default_controls[href] = safe_serialize(program_state.default_dercontrol)

        active_events: dict[str, Any] = {}
        scheduled_events: dict[str, Any] = {}

        client = self._client
        if hasattr(client, "_event_processor"):
            processor = client._event_processor
            store = processor._store

            for record in store.by_state(_EventState.ACTIVE):
                mrid_hex = record.mrid.hex()
                active_events[mrid_hex] = {
                    "mrid": mrid_hex,
                    "program_href": record.program_href,
                    "primacy": record.primacy,
                    "start": record.start,
                    "duration": record.duration,
                    "derc": safe_serialize(record.derc),
                }

            for record in store.by_state(_EventState.SCHEDULED):
                mrid_hex = record.mrid.hex()
                scheduled_events[mrid_hex] = {
                    "mrid": mrid_hex,
                    "program_href": record.program_href,
                    "primacy": record.primacy,
                    "start": record.start,
                    "duration": record.duration,
                    "derc": safe_serialize(record.derc),
                }

        return {
            "derc_controls": {
                "dderc_dict": dderc_dict,
                "default_controls": default_controls,
                "active_events": active_events,
                "scheduled_events": scheduled_events,
            }
        }

    def get_events(self) -> dict[str, Any]:
        """Return all events for /events endpoint."""
        events: dict[str, Any] = {
            "active": {},
            "scheduled": {},
            "completed": {},
            "cancelled": {},
            "superseded": {},
        }

        client = self._client
        if hasattr(client, "_event_processor"):
            processor = client._event_processor
            store = processor._store

            for state_name, state_enum in [
                ("active", _EventState.ACTIVE),
                ("scheduled", _EventState.SCHEDULED),
                ("completed", _EventState.COMPLETED),
                ("cancelled", _EventState.CANCELLED),
                ("superseded", _EventState.SUPERSEDED),
            ]:
                for record in store.by_state(state_enum):
                    mrid_hex = record.mrid.hex()
                    events[state_name][mrid_hex] = {
                        "mrid": mrid_hex,
                        "program_href": record.program_href,
                        "primacy": record.primacy,
                        "state": record.state.value,
                        "start": record.start,
                        "duration": record.duration,
                        "end": record.end,
                    }

        return {"events": events}

    def get_responses(self) -> dict[str, Any]:
        """Return all posted DER responses, grouped by mRID."""
        responses: dict[str, list[dict[str, Any]]] = {}

        client = self._client
        if hasattr(client, "_event_processor"):
            processor = client._event_processor
            tracker = processor._response_tracker
            for (mrid_bytes, code, lfdi), timestamp in tracker._sent.items():
                mrid_hex = mrid_bytes.hex()
                responses.setdefault(mrid_hex, []).append(
                    {
                        "status": int(code),
                        "status_name": code.name,
                        "lfdi": lfdi.hex(),
                        "timestamp": timestamp,
                    }
                )

        return {"responses": responses}

    # -- Log Events -----------------------------------------------------------

    def get_log_events(self) -> dict[str, Any]:
        """Return all posted log events."""
        telemetry = self._telemetry
        if telemetry is None:
            return {"events": [], "enabled": False}
        return {"events": telemetry.get_all_posted_log_events(), "enabled": True}

    async def trigger_log_event(
        self, alarm_status: int = 1, details: str | None = None
    ) -> dict[str, Any]:
        """Trigger a BASIC-027 LogEvent burst (fire-and-forget).

        Schedules the burst on the client's event loop and returns immediately.
        When a separate ``loop`` was provided at construction time the burst is
        dispatched there via ``run_coroutine_threadsafe``; otherwise it runs as
        a task on the current loop.
        """
        telemetry = self._telemetry
        if telemetry is None:
            return {"status": "error", "detail": "telemetry not initialized"}

        lfdi = telemetry.find_device_with_log_events()
        if lfdi is None:
            return {"status": "error", "detail": "no device with LogEventList href found"}

        burst_coro = telemetry.post_log_event_burst(
            lfdi, alarm_status=alarm_status, details=details, interval=1.0
        )
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(burst_coro, self._loop)
        else:
            task = asyncio.create_task(burst_coro)
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        return {"status": "triggered", "device": lfdi[:16], "alarm_status": alarm_status}

    # -- HTTP Message Logging / Redirect Probe --------------------------------

    def get_http_msg(self) -> dict[str, Any]:
        """Return HTTP message logging state for GET /httpmsg."""
        return self._http_msg_state

    async def set_http_msg(self, enabled: int) -> dict[str, Any]:
        """Enable/disable HTTP message logging. Runs redirect probe when enabling."""
        from datetime import UTC, datetime

        from py20305.client.redirect_probe import run_redirect_probe

        state = self._http_msg_state
        state["enabled"] = bool(enabled)
        state["last_updated"] = datetime.now(UTC).isoformat()

        if enabled:
            host = self._client.http.host
            state["redirect_probe"] = await self._run_on_loop(
                run_redirect_probe(host, self._client.http)
            )

        return state

    # -- System Operations ----------------------------------------------------

    async def trigger_rediscovery(self) -> dict[str, Any]:
        """Trigger resource rediscovery."""
        await self._run_on_loop(self._client.trigger_rediscovery())
        return {
            "status": "ok",
            "programs_discovered": len(self._client.state.der_programs),
            "devices_discovered": len(self._client.state.end_devices),
        }

    async def http_probe(self, path: str = "/dcap", http_port: int = 80) -> dict[str, Any]:
        """Issue an HTTP GET to the configured server and follow its redirect.

        The IEEE 2030.5 conformance error tests require the DUT to (a) issue
        an unencrypted HTTP GET, (b) parse the 301/302 ``Location`` header,
        and (c) re-issue the request over TLS at the redirect target. This
        exposes that sequence as one instrumentation call; it plays no part
        in normal operation.

        Only the configured server host is targeted -- the caller chooses a
        path and a port, never a host.
        """
        if not path.startswith("/"):
            path = "/" + path
        http = self._client.http
        http_url = f"http://{http.host}:{http_port}{path}"

        http_resp = await self._run_on_loop(http.get_raw(http_url))
        if http_resp.get("error"):
            return {"error": f"HTTP GET to {http_url} failed: {http_resp['error']}"}

        location: str | None = None
        for k, v in (http_resp.get("headers") or {}).items():
            if k.lower() == "location":
                location = v
                break

        https_resp_payload: dict[str, Any] | None = None
        redirect_followed = False
        status = int(http_resp.get("status_code") or 0)
        if status in (301, 302) and location:
            https_resp = await self._run_on_loop(http.get_raw(location))
            if https_resp.get("error"):
                https_resp_payload = {"error": https_resp["error"]}
            else:
                redirect_followed = True
                https_resp_payload = {
                    "status_code": int(https_resp.get("status_code") or 0),
                    "body_excerpt": (https_resp.get("body") or "")[:500],
                    "content_type": https_resp.get("content_type", ""),
                }

        return {
            "http_response": {"status_code": status, "location": location},
            "https_response": https_resp_payload,
            "redirect_followed": redirect_followed,
            "redirect_target": location,
        }

    def get_subscriptions(self) -> dict[str, Any]:
        """Return active subscriptions for the /subscriptions endpoint."""
        mgr = self._client.subscription_manager
        if mgr is None:
            return {"subscriptions": []}
        return {
            "subscriptions": [
                {
                    "subscription_uri": s.subscription_uri,
                    "subscribed_resource": s.subscribed_resource,
                    "notification_uri": s.notification_uri,
                    "resource_type": s.resource_type,
                    "status": s.status,
                    "created_at": s.created_at,
                }
                for s in mgr.active_subscriptions
            ]
        }

    def get_notifications(self) -> dict[str, Any]:
        """Return received notifications for the /notifications endpoint."""
        mgr = self._client.subscription_manager
        if mgr is None:
            return {"notifications": []}
        return {
            "notifications": [
                {
                    "subscribed_resource": n.subscribed_resource,
                    "status": n.status,
                    "subscription_uri": n.subscription_uri,
                    "new_resource_uri": n.new_resource_uri,
                    "created_at": n.created_at,
                }
                for n in mgr.notifications
            ]
        }

    async def poll_now(self) -> dict[str, Any]:
        """Trigger an immediate DER control poll."""
        programs_polled = await self._run_on_loop(self._client.poll_now())
        return {"status": "ok", "programs_polled": programs_polled}

    async def reset_tls_session(self) -> dict[str, Any]:
        """Reset the TLS session."""
        await self._run_on_loop(self._client.http.reset_session())
        return {"status": "ok"}

    async def update_ca_trust(self, ca_cert: str) -> dict[str, Any]:
        """Update CA trust store and reset TLS session."""

        async def _do_update() -> None:
            self._client.http.update_ca_trust(ca_cert)
            await self._client.http.reset_session()

        await self._run_on_loop(_do_update())
        return {"status": "ok", "ca_cert": ca_cert}

    def get_cert_type(self) -> dict[str, Any]:
        """Return certificate type information."""
        return {"cert_type": "client", "lfdi": None}

    async def set_cert_type(self, cert_path: str, key_path: str) -> dict[str, Any]:
        """Swap client certificate for COMM-004 testing."""

        async def _do_update() -> None:
            self._client.http.update_client_cert(cert_path, key_path)
            await self._client.http.reset_session()

        await self._run_on_loop(_do_update())
        return {"status": "ok"}
