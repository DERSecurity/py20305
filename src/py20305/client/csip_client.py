"""High-level CSIP client with lifecycle, polling, and 404 recovery."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any

from py20305.client.discovery import (
    _extract_href,
    discover,
    refresh_der_controls,
    refresh_der_controls_for_program,
    refresh_der_programs,
    refresh_end_device_lists,
    refresh_function_set_assignments,
    refresh_tariffs,
)
from py20305.client.errors import (
    Sep2Error,
    Sep2NoContentError,
    Sep2PayloadError,
    Sep2ProtocolError,
    Sep2RedirectError,
)
from py20305.client.http import Sep2Client
from py20305.client.poll_rate import DEFAULT_POLL_RATE, normalize_poll_rate
from py20305.client.polling import PollScheduler
from py20305.client.retry import RetryPolicy
from py20305.client.state import DiscoveredState
from py20305.client.timebase import ServerTimebase, observe_time_resource
from py20305.client.tls import TlsConfig
from py20305.events.comms_loss import CommsLossState
from py20305.events.dispatch import ControlDispatcher, NullDispatcher
from py20305.events.processor import EventProcessor
from py20305.events.tariff import TariffProcessor
from py20305.subscription.manager import (
    StoredNotification,
    SubscriptionManager,
)
from py20305.subscription.notification_server import (
    STATUS_CANCELLED,
    STATUS_DEFAULT,
    STATUS_DEFINITION_CHANGED,
    STATUS_RESOURCE_DELETED,
    STATUS_RESOURCE_MOVED,
    NotificationServer,
)

logger = logging.getLogger(__name__)

# Maps subscription resource_type to the poll scheduler key it covers, so an
# active subscription suppresses the corresponding poll (the server pushes
# notifications instead). Every subscribable resource type that also has a poll
# loop MUST appear here, or the client subscribes AND keeps polling it --
# TestPollSuppression.test_subscribable_list_types_all_mapped guards against that.
# One key may cover several types: the "derp" poll fetches the program, its
# controls, and the DefaultDERControl together.
_RESOURCE_TYPE_TO_POLL_KEY: dict[str, str] = {
    "EndDeviceList": "edev",
    "FSAList": "fsa",
    "DERProgramList": "derp",
    "DERControlList": "derp",
    "DefaultDERControl": "derp",
    "TariffProfileList": "tariff",
    "TimeTariffIntervalList": "tariff",
}

# Subscription resource_type -> notification routing category. The type is set at
# subscribe time from the discovered topology (which link we subscribed to), so
# classifying by it is independent of how the server names its URLs -- unlike
# parsing the notification path. The path heuristics (_is_structural_resource /
# _is_control_resource) are only a fallback when the notification's subscription
# isn't tracked locally. DERProgramList is in neither set -> a plain re-poll.
_STRUCTURAL_RESOURCE_TYPES: frozenset[str] = frozenset({"EndDeviceList", "FSAList"})
_CONTROL_RESOURCE_TYPES: frozenset[str] = frozenset({"DERControlList", "DefaultDERControl"})
# Pricing notifications re-poll the tariff tree (refresh + relay) rather than
# rediscover or targeted-fetch a control.
_TARIFF_RESOURCE_TYPES: frozenset[str] = frozenset({"TariffProfileList", "TimeTariffIntervalList"})

# Hard cap on rediscovery re-runs within a single trigger_rediscovery() call.
# Each pass clears the pending flag; a structural notification arriving mid-pass
# re-sets it and the loop runs again. A server that echoes a structural
# notification on every subscription write -- a behavior seen in the field --
# could keep that flag set indefinitely, so we bound the loop as a safety net.
# Normal convergence takes 1-2 passes.
_MAX_REDISCOVERY_PASSES = 5

# After this many consecutive poll failures, treat a subsequent recovery
# as "the upstream server probably restarted" and trigger full
# rediscovery instead of just re-subscribing. Rationale: a single
# transient failure (network blip, brief 5xx) recovers naturally via
# the next poll tick, but sustained failure followed by recovery
# almost always means the server lost its in-memory state -- its
# subscription store is empty, telemetry MUPs are gone, and our
# cached resource paths may not even resolve. Rediscovery rebuilds
# everything; without it the client stays half-wired indefinitely
# (subscriptions marked active on our side, gone on the server's,
# notifications never arriving).
_SERVER_RESTART_DETECTION_THRESHOLD: int = 3


class CsipClient:
    """Async IEEE 2030.5 CSIP client with discovery, polling, and shutdown."""

    def __init__(
        self,
        base_url: str,
        *,
        tls: TlsConfig | None = None,
        retry: RetryPolicy | None = None,
        dispatcher: ControlDispatcher | None = None,
        subscription_manager: SubscriptionManager | None = None,
        notification_server: NotificationServer | None = None,
        on_structural_change: Callable[[], Awaitable[None]] | None = None,
        on_device_removed: Callable[[str], Awaitable[None]] | None = None,
        server_2018_compat: bool = False,
        always_send_alarm_status: bool = False,
        request_headers: dict[str, str] | None = None,
        dcap_path: str = "/dcap",
        heartbeat_enabled: bool = True,
        connection_heartbeat_seconds: int = 120,
        reconcile_enabled: bool = True,
        reconcile_interval_seconds: int = 0,
        renewal_interval_seconds: int = 86400,
        group_lookup: Callable[[str], list[str] | None] | None = None,
        registration_pins: dict[str, int] | None = None,
        comms_loss_seconds: int = 0,
        comms_loss_eval_seconds: int = 30,
        use_server_time: bool = True,
        time_drift_warn_seconds: int = 30,
        pricing_enabled: bool = False,
    ) -> None:
        #: Application-level server timebase :
        #: time-of-day-sensitive operations follow the head-end's Time resource;
        #: the OS clock is never touched. Shared via Sep2Client (discovery,
        #: telemetry) and EventProcessor.
        self._timebase = ServerTimebase(
            enabled=use_server_time, drift_warn_seconds=time_drift_warn_seconds
        )
        self._http = Sep2Client(
            base_url,
            tls=tls,
            retry=retry,
            server_2018_compat=server_2018_compat,
            always_send_alarm_status=always_send_alarm_status,
            request_headers=request_headers,
            timebase=self._timebase,
        )
        self._state = DiscoveredState()
        #: Pricing function set opt-in (config-driven). Preserved across
        #: rediscovery (state.clear() keeps it) and gates tariff discovery/poll.
        self._state.pricing_enabled = pricing_enabled
        self._heartbeat_enabled = heartbeat_enabled
        self._connection_heartbeat_seconds = connection_heartbeat_seconds
        self._reconcile_enabled = reconcile_enabled
        self._reconcile_interval_seconds = reconcile_interval_seconds
        self._renewal_interval_seconds = renewal_interval_seconds
        self._scheduler = PollScheduler(heartbeat_enabled=heartbeat_enabled)
        self._shutdown_event = asyncio.Event()
        self._rediscovery_lock = asyncio.Lock()
        self._state_ready = asyncio.Event()
        self._state_ready.set()  # state is valid initially
        self._dispatcher: ControlDispatcher = dispatcher or NullDispatcher()
        #: Loss-of-communications mode. Shared with the EventProcessor so the
        #: time-based detector here can flip it and the processor gates control
        #: application on it. Zero seconds disables detection entirely.
        if comms_loss_seconds > 0 and comms_loss_eval_seconds <= 0:
            # A non-positive cadence would make the scheduler busy-loop
            # (wait_for timeout of 0). The configuration model enforces ge=1; this
            # guards direct constructions.
            raise ValueError(
                "comms_loss_eval_seconds must be positive when comms-loss "
                "detection is enabled (comms_loss_seconds > 0)"
            )
        self._comms_loss_seconds = comms_loss_seconds
        self._comms_loss_eval_seconds = comms_loss_eval_seconds
        self._comms_loss = CommsLossState()
        #: The client's own certificate LFDI, captured at connect() for in-band
        #: self-reregistration on comms-loss recovery. Cert-derived and stable.
        self._own_lfdi: str | None = None
        self._event_processor = EventProcessor(
            self._http,
            self._state,
            self._dispatcher,
            self._shutdown_event,
            state_ready=self._state_ready,
            group_lookup=group_lookup,
            comms_loss=self._comms_loss,
            timebase=self._timebase,
        )
        #: Pricing function set: relays active-interval prices to connectors.
        #: Constructed unconditionally but only driven by the tariff poll when
        #: pricing is enabled (see _start_polls).
        self._tariff_processor = TariffProcessor(
            self._http, self._state, self._dispatcher, timebase=self._timebase
        )
        self._subscription_manager = subscription_manager
        self._notification_server = notification_server
        self._on_structural_change = on_structural_change
        self._on_device_removed = on_device_removed
        self._dcap_path = dcap_path
        #: LFDI hex -> expected registration PIN. Verified once during the
        #: initial connect() discovery (not on rediscovery). Empty/None
        #: disables the check.
        self._registration_pins = registration_pins or None
        self._renewal_task: asyncio.Task[None] | None = None
        #: Gap 4: Track consecutive poll failures for connectivity recovery
        self._poll_failure_count = 0
        #: Set when trigger_rediscovery() is called while rediscovery is already running.
        #: The running rediscovery will loop once more after completing.
        self._rediscovery_pending = False
        #: One-shot guard for the "no SubscriptionListLink anywhere" warning.
        #: ``_auto_subscribe`` is called on every poll (default 900s) and after every
        #: rediscovery; without this guard a server that never exposes the link would
        #: emit a WARNING ~96 times/day. Reset to False once a link is discovered so a
        #: subsequent server-side fix is also logged (and a regression re-warns).
        self._no_sub_link_warned = False

    @property
    def dispatcher(self) -> ControlDispatcher:
        """The dispatcher this client applies controls through."""
        return self._dispatcher

    @property
    def subscription_manager(self) -> SubscriptionManager | None:
        """The subscription manager, when subscriptions are wired."""
        return self._subscription_manager

    def attach_subscriptions(
        self,
        manager: SubscriptionManager,
        notification_server: NotificationServer | None = None,
    ) -> None:
        """Wire subscribe/notify into this client after construction.

        The manager needs this client's transport, which exists only once the
        client does, so a caller building both cannot pass them to
        ``__init__`` -- construction order forces attachment to come second.
        Attach before ``connect()``: auto-subscription and poll suppression
        consult the manager during connection.
        """
        self._subscription_manager = manager
        if notification_server is not None:
            self._notification_server = notification_server
            notification_server.on_notification = self._handle_notification

    @property
    def http(self) -> Sep2Client:
        return self._http

    @property
    def state(self) -> DiscoveredState:
        return self._state

    @property
    def timebase(self) -> ServerTimebase:
        return self._timebase

    async def run_redirect_probe(self) -> dict[str, Any]:
        """Run ERR-001 HTTP-to-HTTPS redirect probe using this client's session."""
        from py20305.client.redirect_probe import run_redirect_probe

        return await run_redirect_probe(self._http.host, self._http)

    async def register_end_device(
        self,
        *,
        lfdi: str,
        device_category: int | None = None,
        edev_list_href: str | None = None,
        check_duplicate: bool = True,
    ) -> str | None:
        """In-band registration: POST a managed EndDevice to the server's
        EndDeviceList and return the ``Location`` (the new ``/edev/{id}`` href).

        ``lfdi`` is a 40-hex string; ``sFDI`` is derived from it (never
        free-entered). ``device_category`` is an optional DeviceCategoryType
        bitmap. The target list defaults to the discovered
        ``dcap.end_device_list_link`` unless ``edev_list_href`` overrides it.

        Raises ``ValueError`` for a missing EndDeviceList href or, when
        ``check_duplicate``, an LFDI the server already lists (IEEE 2030.5 §8.5.3:
        clients SHALL NOT POST a duplicate EndDevice). Raises ``Sep2ProtocolError``
        on a server rejection status.
        """
        from py20305.models.sep.sep import (
            DeviceCategoryType,
            EndDevice,
            Sfditype,
            TimeType,
        )
        from py20305.security.identity import compute_sfdi

        lfdi_bytes = bytes.fromhex(lfdi)
        href = edev_list_href or _extract_href(
            self._state.dcap.end_device_list_link if self._state.dcap else None
        )
        if not href:
            raise ValueError(
                "No EndDeviceList href -- the server's DeviceCapability has not been discovered"
            )

        if check_duplicate and await self._server_lists_end_device(lfdi, edev_list_href=href):
            raise ValueError(f"An EndDevice with LFDI {lfdi} is already registered")

        end_device = EndDevice(
            l_fdi=lfdi_bytes,
            s_fdi=Sfditype(value=compute_sfdi(lfdi)),
            changed_time=TimeType(value=int(self._timebase.now())),
        )
        if device_category is not None:
            if not 0 <= device_category < 2**32:
                raise ValueError("device_category must be between 0 and 4294967295 (32-bit bitmap)")
            end_device.device_category = DeviceCategoryType(
                value=device_category.to_bytes(4, "big")
            )
        return await self._http.post(href, end_device)

    async def _server_lists_end_device(self, lfdi: str, edev_list_href: str | None = None) -> bool:
        """Whether the server's EndDeviceList currently contains ``lfdi``.

        Live paged GET against the discovered EndDeviceList (or
        ``edev_list_href``). Raises ``ValueError`` when no EndDeviceList href is
        known. Used for the §8.5.3 duplicate check in ``register_end_device``
        and to make comms-loss recovery reregistration conditional: some servers
        (e.g. certain utility head-ends) drop EndDevices during an outage and
        expect the client to re-register, while others retain them and must not
        receive a duplicate POST.
        """
        from py20305.models.sep.sep import EndDeviceList

        href = edev_list_href or _extract_href(
            self._state.dcap.end_device_list_link if self._state.dcap else None
        )
        if not href:
            raise ValueError(
                "No EndDeviceList href -- the server's DeviceCapability has not been discovered"
            )
        lfdi_bytes = bytes.fromhex(lfdi)
        for page in await self._http.get_list(href, EndDeviceList):
            for existing in page.end_device or []:
                if existing.l_fdi == lfdi_bytes:
                    return True
        return False

    async def connect(self) -> None:
        """Run initial resource discovery and auto-subscribe if configured."""
        if self._notification_server and not self._notification_server.running:
            await self._notification_server.start()
        await discover(
            self._http,
            self._state,
            registration_pins=self._registration_pins,
            dcap_path=self._dcap_path,
        )
        # Capture the client's own LFDI for comms-loss self-reregistration.
        # Cert-derived and stable across reset_session, so a single capture holds.
        if self._own_lfdi is None:
            self._own_lfdi = self._http.client_lfdi
        if self._subscription_manager:
            if self._subscription_manager.active_subscriptions:
                valid, removed = await self._subscription_manager.validate_restored_subscriptions()
                logger.info("Subscription validation: %d valid, %d removed", valid, removed)
            await self._auto_subscribe()
            self._update_poll_suppression()
            if self._subscription_manager.active_subscriptions:
                self._renewal_task = asyncio.create_task(
                    self._subscription_manager.start_renewal_task(
                        self._shutdown_event, interval_seconds=self._renewal_interval_seconds
                    )
                )

    async def run(self) -> None:
        """Start polling and block until shutdown is signaled."""
        logger.info("Starting poll loop")
        # Process controls from initial discovery immediately
        for href in list(self._state.der_programs):
            await self._event_processor.process_controls(href)
        self._start_polls()
        await self._shutdown_event.wait()
        logger.info("Poll loop exiting (shutdown signaled)")

    def _start_polls(self) -> None:
        rates = self._state.poll_rates
        logger.debug("Scheduling polls: %s", {k: v for k, v in rates.items() if v is not None})

        dcap_rate = rates.get("dcap")
        if dcap_rate is not None:
            self._scheduler.schedule("dcap", dcap_rate, self._poll_dcap)

        edev_rate = rates.get("edev")
        if edev_rate is not None:
            self._scheduler.schedule("edev", edev_rate, self._poll_edev)

        fsa_rate = rates.get("fsa")
        if fsa_rate is not None:
            self._scheduler.schedule("fsa", fsa_rate, self._poll_fsa)

        derp_rate = rates.get("derp")
        if derp_rate is not None:
            self._scheduler.schedule("derp", derp_rate, self._poll_derp)

        # Pricing function set (opt-in). Uses the tariff pollRate captured in
        # discovery, or the default when a tariff tree hasn't been seen yet (so
        # tariffs that appear later are still picked up).
        if self._state.pricing_enabled:
            tariff_rate = rates.get("tariff") or DEFAULT_POLL_RATE
            self._scheduler.schedule("tariff", tariff_rate, self._poll_tariff)

        time_rate = rates.get("time")
        if time_rate is not None:
            self._scheduler.schedule("time", time_rate, self._poll_time)

        # Connectivity liveness heartbeat (issue: stale server_alive). Runs on a
        # short, fixed cadence independent of resource poll rates so server_alive
        # / last_contact_epoch can't go stale-true while only telemetry PUT/POSTs
        # are in flight. Never subscription-suppressed; idle-gated in the probe.
        if self._connection_heartbeat_seconds > 0:
            self._scheduler.schedule(
                "connectivity", self._connection_heartbeat_seconds, self._connectivity_probe
            )

        # Loss-of-communications detector.
        # Time-based: enters when last_contact_epoch is older than the window,
        # recovers on the first fresh contact. The connectivity heartbeat above
        # keeps issuing GETs during the outage so contact refreshes on restore.
        # Re-scheduled on every rediscovery (_start_polls re-runs), so it
        # self-rearms.
        if self._comms_loss_seconds > 0:
            self._scheduler.schedule(
                "comms_loss", self._comms_loss_eval_seconds, self._comms_loss_probe
            )

        # SubscriptionList reconcile (issue: server drops a subscription after a
        # notification-delivery failure, and we never notice). Polls the server's
        # SubscriptionList and re-establishes any locally-active subscription it's
        # missing. Runs only when subscriptions exist; never subscription-
        # suppressed (the SubscriptionList itself isn't subscribed -- rule (r)).
        if self._reconcile_enabled and self._subscription_manager is not None:
            sub_rate = self._sub_poll_rate()
            if sub_rate is not None:
                self._scheduler.schedule("sub", sub_rate, self._poll_sub_reconcile)

    def _sub_poll_rate(self) -> int | None:
        """Cadence for the SubscriptionList reconcile poll, in seconds.

        Follows the server-advertised SubscriptionList ``pollRate`` (captured into
        ``poll_rates["sub"]`` at discovery, default 900s) unless
        ``reconcile_interval_seconds`` is set (>0), which forces a fixed override.
        The value is clamped to the same safe lower bound as other poll rates.
        """
        override = self._reconcile_interval_seconds
        raw_rate = override if override > 0 else self._state.poll_rates.get("sub")
        return normalize_poll_rate(raw_rate, resource_key="sub", default=DEFAULT_POLL_RATE)

    async def _poll_sub_reconcile(self) -> None:
        """Reconcile local subscriptions against the server's SubscriptionList."""
        if self._subscription_manager is None:
            return
        agg_sub_href = self._find_agg_sub_href()
        if agg_sub_href is None:
            return  # no SubscriptionList discovered yet; nothing to reconcile
        if not self._subscription_manager.active_subscriptions:
            return  # nothing to reconcile
        await self._subscription_manager.reconcile_with_server(agg_sub_href)

    async def _connectivity_probe(self) -> None:
        """Cheap liveness GET, but only when the connection isn't confirmed alive.

        The IEEE chain audit now runs at the TLS handshake, so *any* successful
        request -- a GET or a telemetry PUT/POST -- both sets ``server_alive`` True
        and refreshes ``last_validated_epoch``. So the gate is: skip only when the
        connection is currently alive *and* a request confirmed it within the
        heartbeat window. The probe still fires while ``server_alive`` is False, or
        when nothing has kept the connection warm within the window (e.g. a
        poll-suppressing subscription with only sparse traffic) -- issuing a small
        GET (Time, falling back to DeviceCapability) whose outcome refreshes
        ``server_alive`` via the normal request path. Telemetry reviving
        ``server_alive`` directly is what removes the old stuck-false-after-
        reconnect failure mode. Errors are expected when the server is down and are
        left to the poll loop's handler.
        """
        from py20305.models.sep.sep import DeviceCapability, Time

        if self._http.server_alive:
            last_validated = self._http.last_validated_epoch
            if (
                last_validated is not None
                and (time.time() - last_validated) < self._connection_heartbeat_seconds
            ):
                return  # alive and a validating GET confirmed it within the window

        if self._state.time_href:
            # Free timebase refresh: the probe already GETs Time, so feed the
            # response into the shared timebase instead of discarding it.
            t = await self._http.get(self._state.time_href, Time)
            observe_time_resource(self._timebase, t, href=self._state.time_href)
        else:
            await self._http.get(self._dcap_path, DeviceCapability)

    async def _comms_loss_probe(self) -> None:
        """Detect and recover from loss of communications (time-based).

        Enters comms-loss mode when the last reachable contact is older than
        ``comms_loss_seconds``; recovers on the first contact fresher than that.
        Never fires before the first contact (``last_contact_epoch is None``),
        avoiding cold-start false positives. Owns both the enter and exit edges,
        independent of the count-based server-restart detection in
        ``_note_successful_contact``.
        """
        # Boundary epochs are server-derived (event ends), so compare against
        # the server timebase; the silence detection below stays on the local
        # clock (elapsed, drift-immune). Integer comparison (like the recovery
        # clear) so a fractional now() can't drop the gate up to ~1s early.
        boundary = self._comms_loss.resume_after_epoch
        if (
            not self._comms_loss.active
            and boundary is not None
            and int(self._timebase.now()) > boundary
        ):
            # The opted-out window has fully elapsed: no event starting at/before
            # the boundary can still be pending, so the resume-after gate is done.
            self._comms_loss.resume_after_epoch = None
            logger.info("Comms-loss resume boundary passed (epoch %d); cleared", boundary)
        lce = self._http.last_contact_epoch
        if lce is None:
            return
        elapsed = int(time.time() - lce)
        if not self._comms_loss.active:
            if elapsed >= self._comms_loss_seconds:
                await self._enter_comms_loss(elapsed)
        elif elapsed < self._comms_loss_seconds:
            await self._recover_from_comms_loss()

    async def _enter_comms_loss(self, elapsed: int) -> None:
        """Flag comms-loss mode and opt out of active events (idempotent)."""
        if self._comms_loss.active:
            return
        self._comms_loss.active = True
        from py20305.diagnostics import report

        report(
            "warnings",
            f"Loss of communications: no upstream contact for {elapsed}s "
            f"(>= {self._comms_loss_seconds}s). Opting out of active events and "
            "managing the DER at the planning limit (DefaultDERControl).",
            source="client",
            dedup_key="comms_loss:entered",
            details={"elapsed_seconds": elapsed, "threshold": self._comms_loss_seconds},
        )
        logger.warning("Entering loss-of-communications mode (silent for %ds)", elapsed)
        await self._event_processor.enter_comms_loss()

    async def _recover_from_comms_loss(self) -> None:
        """Recover once communications are restored.

        Reregisters the client's own EndDevice in-band, re-polls the latest
        schedules (superseding prior ones), resumes events after the last
        opted-out event via the resume-after boundary, then clears the mode.
        """
        logger.info("Communications restored -- recovering from loss-of-communications mode")
        # 1. In-band self-reregistration, only when the server no longer
        #    lists our EndDevice: some head-ends remove EndDevices during an
        #    outage and expect re-registration, while others retain them and
        #    must not receive a duplicate POST (IEEE 2030.5 §8.5.3).
        #    Best-effort: a failure is logged and recovery continues (the
        #    re-poll below is what actually resumes control). The client method
        #    (unlike the web layer) does not refuse the own LFDI;
        #    check_duplicate=False avoids re-fetching the list we just checked.
        if self._own_lfdi is not None:
            try:
                if await self._server_lists_end_device(self._own_lfdi):
                    logger.info(
                        "Comms-loss recovery: own EndDevice still listed by the "
                        "server; skipping in-band reregistration"
                    )
                else:
                    await self.register_end_device(lfdi=self._own_lfdi, check_duplicate=False)
            except (Sep2Error, ValueError) as exc:
                logger.warning("Comms-loss recovery: self-reregistration failed: %s", exc)
        # 2. Re-poll schedules, supersede, rebuild timers, manage idle devices per
        #    DDERC. The resume-after boundary makes process_controls skip the
        #    events opted out during the outage.
        if not await self.trigger_rediscovery():
            # Rediscovery raised (or coalesced): the schedule wasn't rebuilt. Stay
            # in comms-loss mode so scheduled events remain opted out, and let the
            # next probe tick retry the whole recovery (reregister + re-poll). The
            # boundary must stay set for that retry.
            logger.warning(
                "Comms-loss recovery: rediscovery did not complete; staying in "
                "loss-of-communications mode, will retry on the next probe tick"
            )
            return
        # 3. Clear the mode now the fresh schedule has been processed. The
        #    resume-after boundary is NOT cleared while it is still in the
        #    future: recovery's re-poll skips (does not store) opted-out-window
        #    events, so a later routine poll would re-classify them as new and
        #    late-apply them if the gate dropped early. The probe clears the
        #    boundary once it has passed. Opted-out event records likewise keep
        #    their opted_out flag until they prune (their end is at/before the
        #    boundary): they must not resume and must stay excluded from
        #    "other active" DDERC checks.
        self._comms_loss.active = False
        boundary = self._comms_loss.resume_after_epoch
        if boundary is not None and int(self._timebase.now()) > boundary:
            self._comms_loss.resume_after_epoch = None
        logger.info(
            "Loss-of-communications mode cleared%s",
            (
                f" (resume-after boundary {boundary} retained until it passes)"
                if self._comms_loss.resume_after_epoch is not None
                else ""
            ),
        )

    async def _poll_dcap(self) -> None:
        await self._poll_with_404_recovery(self._do_poll_dcap)

    async def _poll_edev(self) -> None:
        await self._poll_with_404_recovery(self._do_poll_edev)

    async def _poll_fsa(self) -> None:
        await self._poll_with_404_recovery(self._do_poll_fsa)

    async def _poll_derp(self) -> None:
        await self._poll_with_404_recovery(self._do_poll_derp)

    async def _poll_tariff(self) -> None:
        await self._poll_with_404_recovery(self._do_poll_tariff)

    async def _poll_time(self) -> None:
        await self._poll_with_404_recovery(self._do_poll_time)

    async def _do_poll_dcap(self) -> None:
        from py20305.models.sep.sep import DeviceCapability

        logger.debug("Polling dcap")
        self._state.dcap = await self._http.get(self._dcap_path, DeviceCapability)

        # Late MirrorUsagePoint adoption: if the link was absent at discovery but
        # this DeviceCapability now advertises one (the server brought the
        # MirrorUsagePoint function set online after connect), trigger rediscovery
        # so telemetry is wired up without a restart. Rediscovery re-extracts
        # mup_list_href and re-attempts telemetry setup via the structural-change
        # path (_restart_device_telemetry).
        if (
            self._state.mup_list_href is None
            and _extract_href(self._state.dcap.mirror_usage_point_list_link)
            and self._on_structural_change
        ):
            logger.info("MirrorUsagePointListLink now advertised, triggering rediscovery")
            await self._on_structural_change()

    async def _do_poll_edev(self) -> None:
        logger.debug(
            "Polling edev: refreshing end device lists for %d devices", len(self._state.end_devices)
        )
        await refresh_end_device_lists(self._http, self._state)

    def _subscribed_fsa_hrefs(self) -> set[str]:
        """FSA list hrefs with an active subscription -- rule (r): don't poll them."""
        mgr = self._subscription_manager
        if mgr is None:
            return set()
        return {
            s.subscribed_resource for s in mgr.active_subscriptions if s.resource_type == "FSAList"
        }

    async def _do_poll_fsa(self) -> None:
        logger.debug(
            "Polling fsa: refreshing FSA lists for %d end devices", len(self._state.end_devices)
        )
        # Skip FSA lists we're subscribed to (rule r); a cancelled FSA is still
        # polled while its still-subscribed siblings are not.
        removed_programs = await refresh_function_set_assignments(
            self._http, self._state, skip_hrefs=self._subscribed_fsa_hrefs()
        )

        # Gap 3: cancel events from removed FSA programs (IEEE 8.8.3)
        if removed_programs:
            logger.info(
                "Cancelling events from %d program(s) due to FSA removal",
                len(removed_programs),
            )
            for program_href in removed_programs:
                self._event_processor.cancel_program(program_href)
                # Clean up state
                self._state.der_programs.pop(program_href, None)

    async def _do_poll_derp(self) -> None:
        logger.debug(
            "Polling derp: refreshing DER programs for %d end devices", len(self._state.end_devices)
        )
        removed_programs = await refresh_der_programs(self._http, self._state) or []
        for program_href in removed_programs:
            self._event_processor.cancel_program(program_href)
        n_progs = len(self._state.der_programs)
        logger.debug("Polling derp: refreshing controls for %d programs", n_progs)
        await refresh_der_controls(self._http, self._state)
        for href in list(self._state.der_programs):
            await self._event_processor.process_controls(href)

    async def _do_poll_tariff(self) -> None:
        """Pricing poll: re-walk the tariff tree, then relay any active-interval
        price change to connectors. No-op unless pricing is enabled."""
        await refresh_tariffs(self._http, self._state)
        await self._tariff_processor.process_tariffs()

    async def _do_poll_derp_targeted(self, subscribed_resource: str) -> bool:
        """Fetch controls for the single program that owns *subscribed_resource*.

        Returns True if a targeted refresh was performed, False if the
        resource could not be resolved to a known program (caller should
        fall back to full re-poll).
        """
        program_href = self._state.find_program_for_resource(subscribed_resource)
        if program_href is None:
            logger.warning(
                "Cannot resolve %s to a known program, falling back to full re-poll",
                subscribed_resource,
            )
            return False

        logger.debug(
            "Targeted DERP refresh for program %s (from %s)",
            program_href,
            subscribed_resource,
        )
        found = await refresh_der_controls_for_program(self._http, self._state, program_href)
        if found:
            await self._event_processor.process_controls(program_href)
        return found

    async def _do_poll_time(self) -> None:
        from py20305.models.sep.sep import Time

        if self._state.time_href:
            self._state.time = await self._http.get(self._state.time_href, Time)
            observe_time_resource(self._timebase, self._state.time, href=self._state.time_href)

    async def _note_successful_contact(self) -> None:
        """Clear the poll-failure streak after the server is reachable again.

        Runs after a successful poll *or* a benign 204 (both prove the server
        responded). Gap 4 connectivity-recovery, two regimes:
          * Short failure (count < threshold) -- likely a network blip or brief
            5xx; re-check subscription state but keep current discovery state.
          * Sustained failure (count >= threshold) -- the server probably
            restarted; its in-memory subscription store, telemetry
            MUPs, and resource paths may all differ now, so trigger full
            rediscovery rather than leaving the client half-wired with dead
            subscriptions.
        """
        if self._poll_failure_count == 0:
            return
        recovered_after = self._poll_failure_count
        self._poll_failure_count = 0
        if recovered_after >= _SERVER_RESTART_DETECTION_THRESHOLD:
            logger.info(
                "Server reachable again after %d consecutive poll failures -- "
                "treating as upstream restart and triggering full rediscovery",
                recovered_after,
            )
            # A genuine reconnect: the server's subscription store is presumed gone,
            # so drop any honoured status=1 suppression and re-attempt every desired
            # resource from a clean session. This is the only place the
            # suppression is cleared -- not on every rediscovery.
            if self._subscription_manager is not None:
                self._subscription_manager.clear_resubscribe_suppression()
            await self.trigger_rediscovery()
        else:
            logger.info(
                "Connectivity recovered after %d failure(s), re-checking subscriptions",
                recovered_after,
            )
            if self._subscription_manager:
                await self._auto_subscribe()

    async def _poll_with_404_recovery(
        self,
        poll_fn: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await poll_fn()
            await self._note_successful_contact()

        except Sep2NoContentError:
            # 204 No Content on a polled resource is benign (the resource is
            # present but empty) -- nothing to act on. Must be caught before the
            # generic Sep2ProtocolError below (it's a subclass) so an unexpected
            # 204 can never crash the poll loop (CSIP [GEN.037]). It is still a
            # *successful* contact, though, so it clears the failure streak and
            # runs the same recovery path as a normal poll.
            logger.debug("Poll got 204 No Content; treating as empty (benign)")
            await self._note_successful_contact()
        except Sep2RedirectError as exc:
            # Persistent redirect (the retry loop already retried transient ones):
            # resources moved, so re-discover from dcap (IEEE 2030.5 §5.5.2.7)
            # rather than propagating an error.
            logger.info("Poll got redirect -> %s; triggering rediscovery", exc.location or "(none)")
            self._poll_failure_count = 0
            await self.trigger_rediscovery()
        except Sep2ProtocolError as exc:
            self._poll_failure_count += 1
            if exc.status_code == 404:
                logger.warning("Got 404 during poll, triggering rediscovery")
                self._poll_failure_count = 0
                await self.trigger_rediscovery()
            else:
                raise
        except Sep2PayloadError as exc:
            # Server replied 200 but body was malformed/empty. This is an
            # operational issue with the upstream, not a client bug. Log a
            # clean one-line warning -- the message already includes the
            # path, length, and snippet -- and continue polling.
            self._poll_failure_count += 1
            logger.warning("Poll skipped due to unparseable response: %s", exc)
        except Exception:
            self._poll_failure_count += 1
            raise

    async def poll_now(self) -> int:
        """Trigger an immediate DER program control poll.

        Unlike trigger_rediscovery(), this does NOT reset the event processor
        or re-run discovery. It simply refreshes DERControl lists for all
        known programs and processes any new/changed controls through the
        existing event processor.

        Returns the number of programs polled.
        """
        logger.info("Immediate derp poll requested")
        await self._do_poll_derp()
        n = len(self._state.der_programs)
        logger.info("Immediate derp poll complete: %d program(s)", n)
        return n

    def _update_poll_suppression(self) -> None:
        """Suppress or unsuppress polls based on active subscriptions."""
        mgr = self._subscription_manager
        if mgr is None:
            return

        active_poll_keys = {
            _RESOURCE_TYPE_TO_POLL_KEY[rt]
            for rt in mgr.subscribed_resource_types()
            if rt in _RESOURCE_TYPE_TO_POLL_KEY
        }

        # FSA granularity: the "fsa" poll refreshes every
        # end device's FSA list, but suppression is per-key -- so a single
        # subscribed FSA would suppress polling of a *different*, cancelled FSA on
        # another EndDevice, losing its structural changes. Keep the fsa poll
        # running whenever a discovered FSA list is NOT actively subscribed (it
        # needs poll-only observation); _do_poll_fsa skips the subscribed ones
        # (rule r), so the still-subscribed siblings are never redundantly polled.
        subscribed_fsa = self._subscribed_fsa_hrefs()
        all_fsa = {
            _extract_href(ed.device.function_set_assignments_list_link)
            for ed in self._state.end_devices.values()
        }
        all_fsa.discard(None)
        if all_fsa - subscribed_fsa:
            active_poll_keys.discard("fsa")

        # The tariff processor is poll-driven for active-interval transitions: it
        # has no event timers (unlike DERControl), so an interval that simply
        # becomes active by wall-clock produces no server change and no
        # notification. Keep the tariff poll running even when subscribed -- the
        # subscription only accelerates pickup of schedule *edits* between polls.
        active_poll_keys.discard("tariff")

        # Suppress keys covered by subscriptions
        for key in active_poll_keys:
            self._scheduler.suppress(key)

        # Unsuppress keys no longer covered
        for key in _RESOURCE_TYPE_TO_POLL_KEY.values():
            if key not in active_poll_keys:
                self._scheduler.unsuppress(key)

    def _find_agg_sub_href(self) -> str | None:
        """Find the client device's SubscriptionListLink href."""
        for edev_state in self._state.end_devices.values():
            if edev_state.subscription_list_href:
                return edev_state.subscription_list_href
        return None

    def _compute_desired_subscriptions(self) -> set[tuple[str, str]]:
        """Return the set of (subscribed_resource, resource_type) tuples to subscribe to.

        Pure function over DiscoveredState -- no I/O. Used by both
        ``_auto_subscribe`` and ``trigger_rediscovery`` reconciliation.
        """
        desired: set[tuple[str, str]] = set()

        # EndDeviceList
        edev_list_href = _extract_href(
            self._state.dcap.end_device_list_link if self._state.dcap else None
        )
        if edev_list_href:
            desired.add((edev_list_href, "EndDeviceList"))

        for edev_state in self._state.end_devices.values():
            # FSAList (only if server advertises subscribable)
            fsa_href = _extract_href(edev_state.device.function_set_assignments_list_link)
            if fsa_href and edev_state.fsa_list_subscribable:
                desired.add((fsa_href, "FSAList"))

            # DERProgramList per FSA (only if server advertises subscribable)
            if edev_state.derp_list_subscribable:
                for fsa in edev_state.fsa_list:
                    derp_href = _extract_href(fsa.derprogram_list_link)
                    if derp_href:
                        desired.add((derp_href, "DERProgramList"))

        # DERControlList and DefaultDERControl per program
        for derp_state in self._state.der_programs.values():
            derc_href = _extract_href(derp_state.program.dercontrol_list_link)
            if derc_href and derp_state.derc_list_subscribable:
                desired.add((derc_href, "DERControlList"))
            dderc_href = _extract_href(derp_state.program.default_dercontrol_link)
            if dderc_href and derp_state.dderc_subscribable:
                desired.add((dderc_href, "DefaultDERControl"))

        # TimeTariffIntervalList per rate component (Pricing; only if subscribable).
        # tariff_profiles is only populated when pricing is enabled, but gate
        # explicitly so intent is clear.
        if self._state.pricing_enabled:
            for tp_state in self._state.tariff_profiles.values():
                for rc_state in tp_state.rate_components:
                    if rc_state.tti_list_subscribable and rc_state.tti_list_href:
                        desired.add((rc_state.tti_list_href, "TimeTariffIntervalList"))

        return desired

    async def _auto_subscribe(self) -> None:
        """Subscribe to key resources using the client's SubscriptionListLink."""
        mgr = self._subscription_manager
        if mgr is None:
            return

        agg_sub_href = self._find_agg_sub_href()
        if not agg_sub_href:
            # The server didn't expose a SubscriptionListLink on any discovered
            # EndDevice, so there's nowhere to POST a Subscription resource and the
            # client silently falls back to polling. CSIP CORE-018 Setup #1
            # requires servers that support subscriptions to include this link on
            # the client's EndDevice; without it the ``subscribable`` attribute is
            # an empty advertisement. Warn once so this isn't invisible.
            if not self._no_sub_link_warned:
                logger.warning(
                    "Subscriptions disabled: server did not expose a "
                    "SubscriptionListLink on any discovered EndDevice. Falling "
                    "back to polling. (CSIP CORE-018 Setup #1 requires the "
                    "EndDevice for this client's SFDI/LFDI to include a "
                    "SubscriptionListLink when the server supports subscriptions.)"
                )
                self._no_sub_link_warned = True
            return

        if self._no_sub_link_warned:
            logger.info(
                "Subscriptions now available: SubscriptionListLink discovered at %s",
                agg_sub_href,
            )
            self._no_sub_link_warned = False

        for resource, rtype in self._compute_desired_subscriptions():
            if mgr.resubscribe_suppressed(resource):
                # Server cancelled this resource's subscription (status=1); honour
                # it (poll-only) instead of re-subscribing. Cleared on rediscovery.
                continue
            await mgr.subscribe(agg_sub_href, resource, rtype)

    async def _handle_notification(self, notification: object) -> None:
        """Process a received IEEE 2030.5 notification.

        Routes STATUS_DEFAULT notifications based on subscribed_resource path:
        - DERControlList changes (/derc): re-poll DERPs (fast path)
        - EndDevice/FSA/DERProgram changes: trigger full rediscovery (structural)

        STATUS_RESOURCE_DELETED with an EndDevice path triggers device removal.
        """
        from py20305.models.sep.sep import Notification

        if not isinstance(notification, Notification):
            logger.warning("Unexpected notification type: %s", type(notification))
            return

        mgr = self._subscription_manager
        if mgr is None:
            return

        # Dedup: skip if we already processed this resource recently.
        # Only STATUS_DEFAULT (data-change) notifications are deduped. Lifecycle
        # statuses (cancel/moved/definition-changed/deleted) are distinct one-shot
        # events, never duplicates of a preceding change -- deduping them swallows
        # a cancellation that arrives right after a change for the same resource
        # (conformance test ERR-002: add_fsa_err status=0 then cancel_subscriptions status=1
        # on /edev/2/fsa), so the cancellation is never honoured. Re-processing a
        # lifecycle notification is idempotent, so exempting them is safe.
        # Exception 1: a structural notification arriving while rediscovery is running must
        # not be completely lost — set the pending flag so the running rediscovery loops
        # once more and picks up the change.
        # Exception 2: DERControlList / DefaultDERControl notifications skip dedup entirely.
        # The targeted-fetch handler is a single GET against one URL, so it's cheap to repeat,
        # and burst notifications (e.g. conformance test BASIC-015's 23-call
        # cancel_derc) must each be
        # processed — deduping them means we only see snapshots from the earliest notifications
        # and miss the trailing state where the burst's later cancellations apply.
        # Classify by the subscription's topology-derived resource_type (naming-
        # independent); the path heuristics inside _is_control/_is_structural are
        # only the fallback when this subscription isn't tracked.
        resource_type = mgr.resource_type_for(notification.subscription_uri)
        if (
            notification.status == STATUS_DEFAULT
            and not self._is_control(notification.subscribed_resource, resource_type)
            and mgr.is_duplicate_notification(notification.subscribed_resource)
        ):
            if self._is_structural(notification.subscribed_resource, resource_type) and (
                self._rediscovery_lock.locked()
            ):
                self._rediscovery_pending = True
            logger.debug("Ignoring duplicate notification for %s", notification.subscribed_resource)
            return

        # List/non-list filter: non-list subscriptions should not fire on list changes
        if not mgr.should_process_notification(
            notification.subscription_uri, notification.subscribed_resource
        ):
            logger.debug(
                "Ignoring notification (list/non-list filter) for %s",
                notification.subscribed_resource,
            )
            return

        import time

        if notification.status == STATUS_DEFAULT:
            mgr.record_notification(
                StoredNotification(
                    subscribed_resource=notification.subscribed_resource,
                    status=notification.status,
                    subscription_uri=notification.subscription_uri,
                    created_at=time.time(),
                )
            )
            await self._route_default_notification(notification.subscribed_resource, resource_type)
        elif notification.status == STATUS_CANCELLED:
            # Server cancelled with no further info (rule n, status=1). Honour it:
            # stop treating the sub as active and DON'T auto-re-subscribe -- fall
            # back to polling the resource (permitted once the sub is inactive per
            # rule r). The suppression persists across rediscovery and is cleared
            # only on a genuine reconnect, so an unrelated rediscovery
            # can't undo the honoured cancellation.
            mgr.mark_cancelled(notification.subscription_uri)
            mgr.remove_notifications_for(notification.subscribed_resource)
            mgr.suppress_resubscribe(notification.subscribed_resource)
            self._update_poll_suppression()
        elif notification.status == STATUS_RESOURCE_MOVED:
            # Resource URI changed -> the server terminated the sub (rule h,
            # status=2). Record the new location for visibility, then rediscover
            # so we re-subscribe at the resource's new URI (discovery walks the
            # tree; new_resource_uri alone lacks the resource_type/context).
            mgr.mark_cancelled(notification.subscription_uri)
            self._update_poll_suppression()
            if notification.new_resource_uri:
                mgr.record_notification(
                    StoredNotification(
                        subscribed_resource=notification.subscribed_resource,
                        status=notification.status,
                        subscription_uri=notification.subscription_uri,
                        created_at=time.time(),
                        new_resource_uri=notification.new_resource_uri,
                    )
                )
            if self._on_structural_change:
                await self._on_structural_change()
            else:
                await self.trigger_rediscovery()
        elif notification.status == STATUS_DEFINITION_CHANGED:
            mgr.mark_cancelled(notification.subscription_uri)
            self._update_poll_suppression()
        elif notification.status == STATUS_RESOURCE_DELETED:
            mgr.mark_cancelled(notification.subscription_uri)
            self._update_poll_suppression()
            await self._handle_resource_deleted(notification.subscribed_resource)

    def _is_structural(self, resource_path: str, resource_type: str | None = None) -> bool:
        """Classify a notification as structural (EndDevice/FSA -> rediscovery).

        Prefers the subscription's topology-derived ``resource_type`` (naming-
        independent); falls back to the path heuristic only when the type is
        unknown (the notification's subscription isn't tracked locally).
        """
        if resource_type is not None:
            return resource_type in _STRUCTURAL_RESOURCE_TYPES
        return self._is_structural_resource(resource_path)

    def _is_control(self, resource_path: str, resource_type: str | None = None) -> bool:
        """Classify a notification as a control change (DERControl -> targeted fetch).

        Prefers ``resource_type``; falls back to the path heuristic when unknown.
        """
        if resource_type is not None:
            return resource_type in _CONTROL_RESOURCE_TYPES
        return self._is_control_resource(resource_path)

    def _is_structural_resource(self, resource_path: str) -> bool:
        """Return True if the resource path represents a structural change.

        Path-heuristic fallback used when the notification's subscription type
        isn't known. Only EndDevice and FSA changes require full rediscovery (they
        alter the device mapping); DERProgram and DERControl changes are handled
        by a lighter re-poll via ``_do_poll_derp``.
        """
        path = resource_path.rstrip("/")
        parts = path.strip("/").split("/")
        if not parts:
            return True

        has_control = "derc" in parts or "dderc" in parts
        has_program = "derp" in parts

        # EndDeviceList (/edev) or a single EndDevice (/edev/<id>). Match ``edev``
        # as a path segment near the end rather than at position 0, so a server
        # base-path prefix (e.g. /api/v2/edev) still resolves. Deeper paths
        # (.../edev/<id>/fsa, .../der/...) fall through to the fsa branch.
        if "edev" in parts:
            edev_idx = parts.index("edev")
            if len(parts) - edev_idx <= 2:
                return True

        # FSA changes (contains /fsa but not /derp or /derc)
        return "fsa" in parts and not has_program and not has_control

    @staticmethod
    def _is_control_resource(resource_path: str) -> bool:
        """Return True if the path is a DERControlList or DefaultDERControl.

        These are candidates for targeted fetch (single-program refresh)
        rather than a full re-poll of all programs.
        """
        path = resource_path.rstrip("/")
        return path.endswith("/derc") or path.endswith("/dderc")

    def _is_tariff(self, resource_path: str, resource_type: str | None = None) -> bool:
        """Classify a notification as a Pricing change (tariff -> tariff re-poll).

        Prefers ``resource_type``; falls back to the path heuristic when unknown.
        """
        if resource_type is not None:
            return resource_type in _TARIFF_RESOURCE_TYPES
        return self._is_tariff_resource(resource_path)

    @staticmethod
    def _is_tariff_resource(resource_path: str) -> bool:
        """Return True if the path is a TariffProfileList or TimeTariffIntervalList.

        Path-heuristic fallback used when the subscription's type isn't tracked.
        The tariff tree convention is ``/tp/<n>/rc/<n>/tti`` (see the Pricing plan).
        """
        path = resource_path.rstrip("/")
        return path.endswith("/tti") or path.endswith("/tp")

    async def _route_default_notification(
        self, subscribed_resource: str, resource_type: str | None = None
    ) -> None:
        """Route a STATUS_DEFAULT notification based on resource type.

        ``resource_type`` (the subscription's topology-derived type) drives the
        classification when known; otherwise the path heuristic is the fallback.
        """
        if self._is_structural(subscribed_resource, resource_type):
            logger.info(
                "Structural change notification for %s, triggering rediscovery",
                subscribed_resource,
            )
            if self._on_structural_change:
                await self._on_structural_change()
            else:
                await self.trigger_rediscovery()
        else:
            # Skip re-poll if rediscovery is in progress — the device mapping
            # is in flux and process_controls would produce incorrect results.
            # Rediscovery will pick up the changes when it re-fetches controls.
            if self._rediscovery_lock.locked():
                logger.debug(
                    "Skipping control re-poll for %s (rediscovery in progress)",
                    subscribed_resource,
                )
                return

            # Pricing change: refresh the tariff tree and relay any active-interval
            # price change (parallel to the DER path; not a control or structural).
            if self._is_tariff(subscribed_resource, resource_type):
                logger.info(
                    "Tariff change notification for %s, re-polling tariffs",
                    subscribed_resource,
                )
                await self._do_poll_tariff()
                return

            # Targeted fetch for DERControlList / DefaultDERControl notifications
            if self._is_control(subscribed_resource, resource_type):
                logger.info(
                    "Control change notification for %s, targeted fetch",
                    subscribed_resource,
                )
                # Record ancestor paths in the dedup cache BEFORE the targeted
                # fetch's awaits. The server sends a sibling DERProgramList
                # notification alongside the DERControlList one (its
                # DERControlListLink.all count changed); recording ancestry up
                # front suppresses that sibling instead of letting it race ahead
                # into a redundant full re-poll during our await window. Recorded
                # unconditionally: on the rare targeted-fetch failure we fall
                # through to a full re-poll below, which covers the sibling too.
                mgr = self._subscription_manager
                if mgr:
                    mgr.record_notification_ancestry(subscribed_resource)
                if await self._do_poll_derp_targeted(subscribed_resource):
                    return
                logger.info(
                    "Targeted fetch failed for %s, falling back to full re-poll",
                    subscribed_resource,
                )

            logger.info(
                "Control change notification for %s, re-polling DERPs",
                subscribed_resource,
            )
            await self._do_poll_derp()

    async def _handle_resource_deleted(self, subscribed_resource: str) -> None:
        """Handle STATUS_RESOURCE_DELETED notification.

        If the deleted resource is an EndDevice, call the device-removal callback.
        Otherwise trigger rediscovery.
        """
        path = subscribed_resource.rstrip("/")
        parts = path.strip("/").split("/")

        # EndDevice deletion: /edev/X (exactly 2 segments)
        if len(parts) == 2 and parts[0] == "edev":
            logger.info("EndDevice deleted: %s", subscribed_resource)
            if self._on_device_removed:
                await self._on_device_removed(subscribed_resource)
            return

        # Other resource deletions trigger rediscovery
        logger.info("Resource deleted: %s, triggering rediscovery", subscribed_resource)
        if self._on_structural_change:
            await self._on_structural_change()
        else:
            await self.trigger_rediscovery()

    async def trigger_rediscovery(self) -> bool:
        """Re-run discovery and restart polling. Safe to call concurrently.

        If called while rediscovery is already running, sets a pending flag so
        the running rediscovery will loop once more after completing. This ensures
        structural change notifications received mid-run are not silently dropped.

        Returns True when a rediscovery pass completed (state rebuilt, polls
        re-armed); False when discovery raised (polls are still re-armed so the
        heartbeat can recover) or when the call coalesced into an already-running
        rediscovery. Most callers ignore the result; the comms-loss recovery path
        uses it to avoid clearing the outage gate on a failed re-poll.
        """
        if self._rediscovery_lock.locked():
            self._rediscovery_pending = True
            logger.debug("Rediscovery already in progress, will re-run after completion")
            return False

        async with self._rediscovery_lock:
            passes = 0
            # A status=1 resubscribe-suppression is deliberately NOT cleared here.
            # Rediscovery is frequent (structural-change notifications trigger it),
            # so clearing on every pass would undo an honoured server cancellation
            # -- a status=1 that lands just before an unrelated rediscovery would be
            # wiped and the resource re-subscribed. The suppression is
            # cleared only on a genuine reconnect (upstream-restart detection in
            # _note_successful_contact), where the server's subscription store is
            # presumed gone. The cancelled resource is still observed via polling
            # (mark_cancelled unsuppresses its poll key), so honouring the
            # cancellation does not lose changes.
            while True:
                self._rediscovery_pending = False
                passes += 1
                logger.info("Rediscovery: resetting polls (preserving event processor)")
                await self._scheduler.cancel_all()
                self._scheduler = PollScheduler(heartbeat_enabled=self._heartbeat_enabled)
                # Cancel renewal task (reconciliation handles subscriptions)
                if self._renewal_task and not self._renewal_task.done():
                    self._renewal_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await self._renewal_task
                    self._renewal_task = None
                # Remember active programs before clearing state
                previous_programs = set(self._state.der_programs.keys())
                # Signal that state is being rebuilt — event timers that fire
                # during this window will wait until discover() repopulates it.
                self._state_ready.clear()
                # A rediscovery is commonly triggered by a *reconnect* (upstream
                # restart detection). If the server flaps -- recovers just long
                # enough to trigger this, then drops again -- a GET here (discover
                # or a refresh) raises. The scheduler was already rebuilt empty
                # above, so we MUST still re-arm it in that case: otherwise no
                # poll and no connectivity heartbeat fire, and server_alive is
                # stuck false forever while telemetry keeps last_contact fresh but
                # never chain-validates. _start_polls() therefore runs on both the
                # success and failure paths.
                try:
                    try:
                        # Clear discovered state but preserve subscriptions for reconciliation.
                        # Registration PIN verification is a one-shot startup check
                        # (done in connect()), so it's intentionally not repeated on
                        # rediscovery -- no registration_pins passed here.
                        self._state.clear()
                        await discover(
                            self._http,
                            self._state,
                            dcap_path=self._dcap_path,
                        )
                    finally:
                        # Always unblock timer callbacks — if discover() failed they will
                        # see empty state (harmless no-ops) rather than hanging forever.
                        self._state_ready.set()
                    if self._subscription_manager:
                        desired = self._compute_desired_subscriptions()
                        agg_sub_href = self._find_agg_sub_href()
                        if agg_sub_href and desired:
                            # renew_kept=False: rediscovery must not re-POST kept
                            # subscriptions. Re-POSTing makes some servers echo a
                            # current-state notification, which we classify as a
                            # structural change and rediscover again -- an infinite
                            # loop. The periodic renewal task owns subscription
                            # lifetime; rediscovery only reconciles membership.
                            result = await self._subscription_manager.reconcile(
                                agg_sub_href, desired, renew_kept=False
                            )
                            logger.info(
                                "Subscription reconciliation: kept=%d cancelled=%d"
                                " created=%d renewed=%d",
                                result.kept,
                                result.cancelled,
                                result.created,
                                result.renewed,
                            )
                            # renew_kept=False keeps subs without verifying them, so a
                            # subscription whose server-side URI has drifted would still
                            # be reported under its stale local URI. Adopt the server's
                            # current URIs so post-rediscovery state matches the server
                            # (a stale URI otherwise 404s on a later cancel/renew).
                            # reestablish_missing=False: adopt-only, no re-POST -- a
                            # re-POST here risks the rediscovery<->echo loop.
                            await self._subscription_manager.reconcile_with_server(
                                agg_sub_href, reestablish_missing=False
                            )
                        else:
                            # No subscription list or no desired subs; cancel remaining
                            await self._subscription_manager.cancel_all()
                        self._update_poll_suppression()
                        if self._subscription_manager.active_subscriptions:
                            self._renewal_task = asyncio.create_task(
                                self._subscription_manager.start_renewal_task(
                                    self._shutdown_event,
                                    interval_seconds=self._renewal_interval_seconds,
                                )
                            )
                    # Re-fetch FSA and program hierarchy to catch resources added between
                    # discover() and subscription creation (same race as DERControl refresh).
                    await refresh_function_set_assignments(self._http, self._state)
                    pruned_programs = await refresh_der_programs(self._http, self._state) or []
                    if self._subscription_manager:
                        await self._auto_subscribe()
                        self._update_poll_suppression()
                    # Cancel events from programs that no longer exist after rediscovery
                    current_programs = set(self._state.der_programs.keys())
                    all_removed = (previous_programs - current_programs) | set(pruned_programs)
                    for removed in all_removed:
                        self._event_processor.cancel_program(removed)
                    # Re-fetch DER controls before processing: data from discover() may be
                    # stale if controls were written during subscription reconciliation.
                    await refresh_der_controls(self._http, self._state)
                    # Process controls discovered during rediscovery immediately
                    for href in list(self._state.der_programs):
                        await self._event_processor.process_controls(href)
                    self._start_polls()
                    logger.info("Rediscovery complete")
                except Exception as exc:
                    # Re-arm the scheduler so the connectivity heartbeat (and polls)
                    # keep running: the heartbeat's validating GET recovers
                    # server_alive, and a later successful poll re-triggers
                    # rediscovery to rebuild state. Return rather than re-raise --
                    # the caller is often a poll loop or the reconnect path, and the
                    # empty-scheduler condition is now healed.
                    from py20305.diagnostics import report

                    report(
                        "warnings",
                        f"Rediscovery failed ({exc}); re-armed polls so the "
                        "connectivity heartbeat can recover server_alive",
                        source="client",
                        dedup_key="rediscovery:failed",
                        details={"error": str(exc)},
                        exc_info=True,
                    )
                    self._start_polls()
                    return False
                if not self._rediscovery_pending:
                    break
                if passes >= _MAX_REDISCOVERY_PASSES:
                    self._rediscovery_pending = False
                    from py20305.diagnostics import report

                    report(
                        "warnings",
                        f"Rediscovery still pending after {passes} passes; stopping to "
                        "avoid an unbounded rediscovery/notification loop. A server that "
                        "echoes a structural notification on every subscription write can "
                        "cause this.",
                        source="client",
                        dedup_key="rediscovery:max_passes",
                        details={"passes": passes},
                    )
                    logger.warning(
                        "Rediscovery still pending after %d passes; stopping to avoid an "
                        "unbounded rediscovery<->notification loop",
                        passes,
                    )
                    break
                logger.info(
                    "Re-running rediscovery (structural change received during previous run)"
                )
        return True

    async def shutdown(self, timeout: float = 10.0) -> None:
        """Gracefully stop polling, event timers, and close the HTTP session."""
        self._shutdown_event.set()
        if self._renewal_task and not self._renewal_task.done():
            self._renewal_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._renewal_task
        if self._subscription_manager:
            await self._subscription_manager.cancel_all()
        if self._notification_server:
            await self._notification_server.stop()
        await self._event_processor.shutdown()
        await self._tariff_processor.shutdown()
        await self._scheduler.cancel_all(timeout=timeout)
        await self._http.close()

    async def __aenter__(self) -> CsipClient:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_val: BaseException | None,
        _exc_tb: TracebackType | None,
    ) -> None:
        await self.shutdown()
