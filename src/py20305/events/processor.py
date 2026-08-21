"""Top-level event processor orchestrating the DERControl lifecycle.

Called from poll callbacks after resource discovery. Processes DERControls
through the event state machine: randomize, classify, upsert, supersede,
activate, complete, and prune.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from typing import Any

from py20305.client.http import Sep2Client
from py20305.client.state import DerProgramState, DiscoveredState
from py20305.client.timebase import ServerTimebase
from py20305.commands import CommandOrigin
from py20305.connectors.base import ScheduleNotification
from py20305.events.active_modes import ActiveModeTracker
from py20305.events.comms_loss import CommsLossState
from py20305.events.dderc import DdercTracker
from py20305.events.dispatch import ControlDispatcher
from py20305.events.modes_bitmask import get_active_mode_names
from py20305.events.randomization import RandomizationCache
from py20305.events.response import (
    ResponseCode,
    ResponseTracker,
    post_der_response,
    response_code_for_dispatch_error,
)
from py20305.events.state_machine import EventRecord, EventState, EventStore
from py20305.events.supersession import compute_supersession
from py20305.events.timers import EventTimerManager
from py20305.json_form import (
    serialize_default_der_control,
    serialize_der_control,
)
from py20305.models.sep.sep import Dercontrol1

logger = logging.getLogger(__name__)

#: CSIP-AUS Dynamic-Operating-Envelope limit fields. They ride in
#: ``DERControlBase.other_element`` as parsed extension objects (each with
#: ``.value`` / ``.multiplier``), not as first-class base attributes -- mirrors
#: ``connectors.modes._translate_csipaus_power_limit``.
_DOE_ENVELOPE_NAMES = ("opModExpLimW", "opModImpLimW", "opModGenLimW", "opModLoadLimW")

#: Ceiling on how long a device's activation response waits on its
#: dispatch. ACTIVE is now posted only after the device's apply returns, so this
#: bounds how far past the event's effective start that response can land -- the
#: instant CORE-022 measures against. Deliberately far below the connector layer's
#: 30s CONTROL_DEADLINE. It gates the *response* only: a slower device is
#: reported rejected while its apply keeps running under the connector's own
#: deadline, so the control still reaches it.
ACTIVATION_DISPATCH_DEADLINE = 5.0


def _resolve_watts(value: int, multiplier: int) -> int | float:
    """Resolve ``value * 10**multiplier`` to watts.

    A negative ``multiplier`` (PowerOfTenMultiplierType permits it) would make
    ``value * 10**multiplier`` a float; divide instead and keep an ``int`` when
    the magnitude is whole so the JSON payload doesn't leak a stray float.
    """
    if multiplier >= 0:
        return value * int(10**multiplier)
    scaled = value / int(10**-multiplier)
    return int(scaled) if scaled.is_integer() else scaled


def _extract_doe_envelope(base: Any) -> dict[str, dict[str, int | float]]:
    """Pull the CSIP-AUS envelope limits out of a DERControlBase.

    Returns ``{name: {"value", "multiplier", "watts"}}`` for each present limit,
    or ``{}`` when the control carries no envelope -- the signal the caller uses
    to decide whether a ``doe`` projection is warranted. ``watts`` is the
    resolved magnitude (``value * 10**multiplier``) so an optimizer doesn't have
    to re-apply the exponent; it is an ``int`` unless a negative multiplier makes
    it fractional.
    """
    out: dict[str, dict[str, int | float]] = {}
    for elem in getattr(base, "other_element", None) or []:
        meta = getattr(getattr(elem, "Meta", None), "name", None) or getattr(
            getattr(getattr(elem, "__class__", None), "Meta", None), "name", None
        )
        if meta not in _DOE_ENVELOPE_NAMES:
            continue
        if not (hasattr(elem, "value") and hasattr(elem, "multiplier")):
            continue
        multiplier = elem.multiplier
        if hasattr(multiplier, "value"):
            multiplier = multiplier.value
        value = int(elem.value)
        multiplier = int(multiplier)
        out[meta] = {
            "value": value,
            "multiplier": multiplier,
            "watts": _resolve_watts(value, multiplier),
        }
    return out


class EventProcessor:
    """Orchestrates DERControl event processing.

    Wires together EventStore, RandomizationCache, supersession,
    response posting, DDERC tracking, timers, and control dispatch.
    """

    def __init__(
        self,
        http: Sep2Client,
        state: DiscoveredState,
        dispatcher: ControlDispatcher,
        shutdown: asyncio.Event,
        *,
        state_ready: asyncio.Event | None = None,
        group_lookup: Callable[[str], list[str] | None] | None = None,
        comms_loss: CommsLossState | None = None,
        timebase: ServerTimebase | None = None,
    ) -> None:
        # ``group_lookup`` is an optional fan-out hook, unset by default.
        # Given a server-side program href, it returns the list of local
        # sub-device LFDIs that program should fan out to, or None to fall
        # back to 1-to-1 dispatch by server-side device href. Bookkeeping
        # (responses, supersession, mode tracking) stays in terms of the
        # server-side hrefs the server actually knows about; only the
        # connector-dispatch step is expanded.
        self._http = http
        self._state = state
        self._dispatcher = dispatcher
        self._shutdown = shutdown
        self._state_ready = state_ready
        self._group_lookup = group_lookup
        self._comms_loss = comms_loss or CommsLossState()
        # Server timebase for time-of-day-sensitive reads (classification,
        # timer firing, response timestamps). The default identity instance
        # behaves exactly like the local clock.
        self._timebase = timebase or ServerTimebase()
        self._store = EventStore()
        self._rand_cache = RandomizationCache()
        self._response_tracker = ResponseTracker()
        self._dderc_tracker = DdercTracker()
        self._timer_mgr = EventTimerManager(shutdown)
        self._mode_tracker = ActiveModeTracker()
        self._closed = False
        # Schedule-notification relay (informational push to connectors).
        # Fire-on-change dedup keyed by (stream, mrid_hex) -> last content signature.
        self._relay_snapshots: dict[tuple[str, str], tuple[Any, ...]] = {}
        # Strong refs to in-flight fire-and-forget relay tasks.
        self._relay_tasks: set[asyncio.Task[None]] = set()
        # Strong refs to control dispatches still running past the activation
        # response ceiling. They are shielded from the ceiling's timeout so
        # the device still gets the control; this keeps them from being GC'd
        # mid-flight and gives shutdown something to cancel.
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    @property
    def closed(self) -> bool:
        """Whether this processor has been shut down."""
        return self._closed

    async def process_controls(self, program_href: str) -> None:
        """Process all DERControls for a given DERProgram.

        Called after polling discovers or refreshes DERControl data.
        """
        if self._closed:
            return

        derp_state = self._state.der_programs.get(program_href)
        if derp_state is None:
            return

        # Relay the program's DefaultDERControl baseline (add/change only).
        self._relay_default_baseline(program_href, derp_state)

        # Server timebase (FSA-scoped): classification against server-epoch
        # eff_start/eff_end must follow the head-end's clock.
        now = int(self._timebase.now(self._fsa_scope(program_href)))
        primacy = derp_state.primacy
        n_controls = len(derp_state.der_controls)
        logger.debug(
            "Processing %d controls for program %s (primacy=%d)",
            n_controls,
            program_href,
            primacy,
        )

        for derc in derp_state.der_controls:
            mrid = derc.m_rid.value
            mrid_short = mrid.hex()[:8]
            interval = derc.interval
            raw_start = interval.start.value
            raw_duration = interval.duration

            # Randomize timing
            start_off, dur_off = self._rand_cache.get_offsets(derc, raw_start, raw_duration)

            # IEEE 10.2.2.3 rule m): successive events must not have gaps
            # from randomization. If a predecessor's raw end == this raw start,
            # override this event's effective start to match predecessor's
            # effective end.
            predecessor = self._find_successive_predecessor(program_href, raw_start)
            if predecessor is not None:
                new_start_off = predecessor.end - raw_start
                if new_start_off != start_off:
                    logger.debug(
                        "Successive event %s: adjusting start offset %d -> %d "
                        "(predecessor %s eff_end=%d)",
                        mrid_short,
                        start_off,
                        new_start_off,
                        predecessor.mrid.hex()[:8],
                        predecessor.end,
                    )
                    start_off = new_start_off
                    self._rand_cache.override_start_offset(mrid, start_off, raw_start, raw_duration)

            eff_start = raw_start + start_off
            eff_duration = raw_duration + dur_off

            # Server status time for tie-breaking
            server_status_time = 0
            if derc.event_status is not None and derc.event_status.date_time is not None:
                server_status_time = derc.event_status.date_time.value

            # Check if already tracked
            existing = self._store.get(mrid)
            if existing is not None:
                # Update the derc reference (server may have updated fields)
                existing.derc = derc
                existing.server_status_time = server_status_time
                if derc.creation_time is not None:
                    existing.creation_time = derc.creation_time.value
                # Check server cancellation
                if self._is_server_cancelled(derc) and existing.state in (
                    EventState.SCHEDULED,
                    EventState.ACTIVE,
                ):
                    logger.debug(
                        "Server cancelled event %s (was %s)", mrid_short, existing.state.value
                    )
                    await self._handle_cancellation(existing)
                    if self._closed:
                        return
                else:
                    # Server refreshed the control's fields without a state
                    # change; relay only if the actionable content changed
                    # (fire-on-change dedup in _emit_relay).
                    self._relay_control_event(existing, "updated")
                continue

            # Classify new event
            eff_end = eff_start + eff_duration

            # Comms-loss recovery: skip events at or before the resume boundary
            # (the window we opted out of during the outage). Only later
            # schedules resume. The boundary outlives the mode: skipped events
            # are not stored, so it must gate every poll until the window has
            # elapsed -- the client clears it only once it is in the past.
            if (
                self._comms_loss.resume_after_epoch is not None
                and eff_start <= self._comms_loss.resume_after_epoch
            ):
                logger.debug(
                    "Skipping event %s in opted-out window (start=%d <= resume_after=%d)",
                    mrid_short,
                    eff_start,
                    self._comms_loss.resume_after_epoch,
                )
                continue

            if now > eff_end:
                # IEEE 2030.5 §10.2.2.3 rule j: EXPIRED is only for an event whose
                # end is already past *when first received*. If we already
                # responded to this event (e.g. ran it to COMPLETED and the store
                # then pruned it after its end), a later poll re-discovering it
                # must NOT re-post EXPIRED -- it would contradict the COMPLETED we
                # already sent. (Response posting is also gated on responseRequired
                # inside post_der_response.)
                if self._response_tracker.has_responded(mrid):
                    logger.debug(
                        "Skipping EXPIRED for already-handled event %s "
                        "(re-discovered %ds after end)",
                        mrid_short,
                        now - eff_end,
                    )
                    continue
                logger.debug(
                    "Event %s already expired when received (ended %ds ago)",
                    mrid_short,
                    now - eff_end,
                )
                await self._post_response(derc, ResponseCode.EXPIRED, program_href)
                if self._closed:
                    return
                continue

            state = EventState.ACTIVE if now >= eff_start else EventState.SCHEDULED
            logger.info(
                "New event %s: state=%s start=T+%ds duration=%ds primacy=%d",
                mrid_short,
                state.value,
                eff_start - now,
                eff_duration,
                primacy,
            )

            # IEEE 10.2.2.3 rule e): use creationTime for tie-breaking
            creation_time = 0
            if derc.creation_time is not None:
                creation_time = derc.creation_time.value

            record = EventRecord(
                mrid=mrid,
                derc=derc,
                program_href=program_href,
                primacy=primacy,
                state=state,
                start=eff_start,
                duration=eff_duration,
                server_status_time=server_status_time,
                creation_time=creation_time,
            )

            # Check server cancellation before adding
            if self._is_server_cancelled(derc):
                record.state = EventState.CANCELLED
                self._store.upsert(record)
                logger.info("Event %s pre-cancelled by server", mrid_short)
                self._relay_control_event(record, "cancelled")
                await self._post_response(derc, ResponseCode.CANCELLED, program_href)
                if self._closed:
                    return
                continue

            self._store.upsert(record)
            # New event signals fresh state for affected devices — clear
            # stale DDERC tracker entries across ALL programs so initial
            # DDERC is re-evaluated (a device may belong to multiple programs).
            dev_hrefs = self._state.device_mapping.program_to_devices.get(program_href, [])
            affected_lfdis: set[bytes] = set()
            for dh in dev_hrefs:
                lfdi = self._get_device_lfdi(dh)
                if lfdi is not None:
                    affected_lfdis.add(lfdi)
            if affected_lfdis:
                self._dderc_tracker.clear_devices(affected_lfdis)

            if state == EventState.SCHEDULED:
                await self._handle_new_scheduled(record)
            else:
                await self._handle_late_discovery(record, derp_state)
            if self._closed:
                return

        # Run supersession across all non-terminal events
        await self._run_supersession()
        if self._closed:
            return

        # Prune expired
        expired = self._store.prune_expired(now)
        # Drop relay snapshots for events that left the store so the
        # fire-on-change cache doesn't grow without bound (both the control and
        # its doe projection, if any).
        for expired_mrid in expired:
            self._relay_snapshots.pop(("control", expired_mrid.hex()), None)
            self._relay_snapshots.pop(("doe", expired_mrid.hex()), None)
        self._rand_cache.prune(now)
        self._response_tracker.prune(now)

        # Apply DDERC to devices that have no active events.
        # Per IEEE 2030.5 §10.10, the DefaultDERControl is the baseline
        # operating state and should be active whenever no event overrides it.
        await self._apply_initial_dderc(program_href)

    def cancel_program(self, program_href: str) -> None:
        """Cancel all events from a removed program (Gap 3: IEEE 8.8.3).

        Transitions all scheduled/active events from the given program to
        CANCELLED and cancels any pending timers.
        """
        cancelled_count = 0
        for record in self._store.all_active_states():
            if record.program_href == program_href:
                mrid_short = record.mrid.hex()[:8]
                logger.info("Cancelling event %s from removed program %s", mrid_short, program_href)
                self._store.transition(record.mrid, EventState.CANCELLED)
                self._timer_mgr.cancel(record.mrid)
                # cancel_program is sync, but runs inside the event loop, so
                # _emit_relay's loop.create_task succeeds.
                self._relay_control_event(record, "cancelled")
                cancelled_count += 1
        # The program is gone -- drop its baseline relay slot so the cache stays
        # bounded by live program count.
        self._relay_snapshots.pop(("default_baseline", program_href), None)
        if cancelled_count:
            logger.info(
                "Cancelled %d event(s) from removed program %s", cancelled_count, program_href
            )

    async def shutdown(self) -> None:
        """Cancel all event timers + in-flight background tasks and mark closed."""
        self._closed = True
        await self._timer_mgr.cancel_all()
        # Cancel/await fire-and-forget relay tasks and any control dispatch
        # still running past the activation ceiling, so shutdown is clean
        # (no "Task was destroyed but it is pending" warnings).
        background = list(self._relay_tasks) + list(self._dispatch_tasks)
        for task in background:
            task.cancel()
        if background:
            await asyncio.gather(*background, return_exceptions=True)

    async def _handle_new_scheduled(self, record: EventRecord) -> None:
        """Post ack and schedule activation timer for a newly discovered scheduled event."""
        mrid_short = record.mrid.hex()[:8]
        logger.debug("Event %s: posting ACK, scheduling activation timer", mrid_short)
        await self._post_response(record.derc, ResponseCode.ACKNOWLEDGED, record.program_href)
        if self._closed:
            return
        self._timer_mgr.schedule_activation(
            record, self._on_activation, now_fn=self._now_fn_for(record.program_href)
        )
        self._relay_control_event(record, "scheduled")

    async def _handle_late_discovery(self, record: EventRecord, derp_state: object) -> None:
        """Handle an event discovered after its start time (already active).

        Mirrors the ``_on_activation()`` flow: run supersession BEFORE posting
        ACTIVE so that SUPERSEDED responses go out first when multiple
        overlapping events are discovered late in the same poll cycle.
        """
        mrid_short = record.mrid.hex()[:8]
        if self._comms_loss.active:
            logger.info("Event %s: comms-loss active -- opting out (late discovery)", mrid_short)
            await self._opt_out_event(record)
            return
        logger.info("Event %s: late discovery (already active), posting ACK", mrid_short)
        await self._post_response(record.derc, ResponseCode.ACKNOWLEDGED, record.program_href)
        if self._closed:
            return

        # IEEE 10.2.2.3 rule l): SUPERSEDED before ACTIVE.
        await self._run_supersession()
        if self._closed:
            return

        await self._apply_and_respond(record)
        if self._closed:
            return
        self._relay_control_event(record, "active")

        # Schedule completion timer
        self._timer_mgr.schedule_completion(
            record, self._on_completion, now_fn=self._now_fn_for(record.program_href)
        )

    async def _handle_cancellation(self, record: EventRecord) -> None:
        """Handle server cancellation of an event.

        IEEE 10.2.3.3: Cancellation of an active event applies randomization
        to the abbreviated duration. The wind-down period is
        max(abs(randomizeStart), abs(randomizeDuration)) seconds.
        """
        mrid_short = record.mrid.hex()[:8]
        was_active = record.state == EventState.ACTIVE
        logger.info("Event %s: cancelled (was %s)", mrid_short, record.state.value)
        self._store.transition(record.mrid, EventState.CANCELLED)
        self._timer_mgr.cancel(record.mrid)
        self._relay_control_event(record, "cancelled")
        # A cancellation tells a device the event it was running has been called
        # off. A device that was told the event was rejected was never running it,
        # so it is not told again. An event cancelled while still SCHEDULED
        # never dispatched, so every device in the program still hears about it.
        await self._post_started_device_lifecycle(record, ResponseCode.CANCELLED, mrid_short)

        if was_active:
            wind_down = self._cancellation_wind_down(record)
            if wind_down > 0:
                logger.info(
                    "Event %s: wind-down %ds before DDERC fallback",
                    mrid_short,
                    wind_down,
                )
                self._timer_mgr.schedule_delayed_callback(
                    record, wind_down, self._apply_dderc_fallback, "wind-down"
                )
            else:
                await self._apply_dderc_fallback(record)

    @staticmethod
    def _cancellation_wind_down(record: EventRecord) -> int:
        """Compute wind-down duration for cancellation randomization.

        IEEE 10.2.3.3: abbreviated duration is the greater of
        abs(randomizeStart) and abs(randomizeDuration).
        """
        derc = record.derc
        rs = abs(derc.randomize_start.value) if derc.randomize_start is not None else 0
        rd = abs(derc.randomize_duration.value) if derc.randomize_duration is not None else 0
        return max(rs, rd)

    async def _on_activation(self, record: EventRecord) -> None:
        """Activation timer callback: transition scheduled -> active.

        If a rediscovery lock is configured, waits for any in-progress
        rediscovery to complete before reading state — this keeps timers
        firing on-time while ensuring the device mapping is consistent.
        """
        # Wait for state to be repopulated after _state.clear() during rediscovery
        if self._state_ready is not None and not self._state_ready.is_set():
            await self._state_ready.wait()

        now = self._timebase.now(self._fsa_scope(record.program_href))
        # Elapsed dispatch duration is measured on the monotonic clock: the
        # timebase can jump if a Time observation lands mid-dispatch, which
        # would corrupt (even negate) a timebase-vs-timebase subtraction.
        dispatch_started = time.monotonic()
        mrid_short = record.mrid.hex()[:8]
        if record.state != EventState.SCHEDULED:
            logger.debug("Event %s: activation skipped (state=%s)", mrid_short, record.state.value)
            return
        delta = now - record.start
        logger.info(
            "Event %s: SCHEDULED -> ACTIVE (fired %.2fs %s intended start)",
            mrid_short,
            abs(delta),
            "after" if delta >= 0 else "before",
        )
        self._store.transition(record.mrid, EventState.ACTIVE)

        if self._comms_loss.active:
            logger.info("Event %s: comms-loss active -- opting out instead of applying", mrid_short)
            await self._opt_out_event(record)
            return

        # IEEE 10.2.2.3 rule l): SUPERSEDED SHALL be POSTed at the Effective
        # Start Time of the superseding event.  Run supersession BEFORE
        # dispatching so the SUPERSEDED responses go out ahead of any ACTIVE.
        # Supersession stands whether or not this event's dispatch then
        # succeeds.
        await self._run_supersession()
        if self._closed:
            return

        await self._apply_and_respond(record)
        if self._closed:
            return

        # Absolute "after intended start" follows the server timebase; the
        # elapsed duration uses the monotonic anchor captured above and spans
        # the whole fleet -- individual devices responded as they finished.
        dispatch_done = self._timebase.now(self._fsa_scope(record.program_href))
        logger.info(
            "Event %s: all devices dispatched (%.2fs after intended start, %.2fs for the slowest)",
            mrid_short,
            dispatch_done - record.start,
            time.monotonic() - dispatch_started,
        )
        self._relay_control_event(record, "active")
        self._timer_mgr.schedule_completion(
            record, self._on_completion, now_fn=self._now_fn_for(record.program_href)
        )

    async def _on_completion(self, record: EventRecord) -> None:
        """Completion timer callback: transition active -> completed.

        Waits for any in-progress rediscovery before reading state.
        """
        # Wait for state to be repopulated after _state.clear() during rediscovery
        if self._state_ready is not None and not self._state_ready.is_set():
            await self._state_ready.wait()

        now = self._timebase.now(self._fsa_scope(record.program_href))
        mrid_short = record.mrid.hex()[:8]
        if record.state != EventState.ACTIVE:
            logger.debug("Event %s: completion skipped (state=%s)", mrid_short, record.state.value)
            return
        delta = now - record.end
        logger.info(
            "Event %s: ACTIVE -> COMPLETED (fired %.2fs %s intended end)",
            mrid_short,
            abs(delta),
            "after" if delta >= 0 else "before",
        )
        self._store.transition(record.mrid, EventState.COMPLETED)
        self._relay_control_event(record, "completed")
        await self._post_started_device_lifecycle(record, ResponseCode.COMPLETED, mrid_short)
        await self._apply_dderc_fallback(record)

    async def _post_started_device_lifecycle(
        self, record: EventRecord, code: ResponseCode, mrid_short: str
    ) -> None:
        """Post a post-activation lifecycle code to the devices that started.

        Used for COMPLETED and CANCELLED: both say something about how an event
        the device was running ended, so neither applies to a device that was told
        the event was rejected.

        Stated positively rather than as "every program device except the
        superseded and rejected ones", because the
        program's device list is not stable across an event: a rediscovery can
        repopulate it under a different program href, or with different members.
        Subtracting from a list that has since changed produced two wrong answers
        -- a rejected device was told COMPLETED once its program mapping had gone
        (the list was empty, so the any-LFDI fallback fired and never consulted
        the exclusions), and a device added to the program mid-event would be told
        an event completed that it never ran.

        The fallback is kept for the one case it was meant for: an event that never
        dispatched at all, whose ACTIVE also went out against any available LFDI
        (see ``_apply_and_respond``), and an event cancelled while still SCHEDULED,
        which every device in the program needs to hear about. Without a dispatch
        there is nothing to have rejected, so there is no exclusion to honour.
        """
        dispatched = record.applied_devices or record.rejected_devices
        if not dispatched:
            await self._post_response(
                record.derc,
                code,
                record.program_href,
                exclude_devices=record.superseded_devices or None,
            )
            return

        audience = record.applied_devices - record.superseded_devices
        if not audience:
            logger.info(
                "Event %s: no device was running it (%d rejected, %d superseded); posting no %s",
                mrid_short,
                len(record.rejected_devices),
                len(record.superseded_devices),
                code.name,
            )
            return
        await self._post_response_for_devices(record.derc, code, audience)

    async def _run_supersession(self) -> None:
        """Run supersession algorithm and apply transitions.

        IEEE 10.2.2.3 rule l): SUPERSEDED SHALL be POSTed at the Effective
        Start Time of the superseding event.  When the superseding event is
        still SCHEDULED and the superseded event is already ACTIVE, we defer
        all supersession bookkeeping until the superseding event activates
        (handled by ``_on_activation`` calling this method again).
        """
        events = self._store.all_active_states()
        if len(events) < 2:
            return

        device_map = self._build_program_device_map()
        results = compute_supersession(events, device_map)

        for result in results:
            superseded = self._store.get(result.superseded_mrid)
            if superseded is None:
                continue
            if superseded.state in (
                EventState.CANCELLED,
                EventState.SUPERSEDED,
                EventState.COMPLETED,
            ):
                continue

            superseding = self._store.get(result.superseding_mrid)
            if superseding is None:
                continue

            # Defer when the superseding event hasn't started yet and the
            # superseded event is already active.  The active event should
            # run normally until the higher-priority event actually starts.
            if superseding.state == EventState.SCHEDULED and superseded.state == EventState.ACTIVE:
                continue

            # Post per-device SUPERSEDED responses for newly affected devices
            newly_superseded = result.affected_devices - superseded.superseded_devices
            superseded.superseded_devices.update(result.affected_devices)

            # A device that was told this event was rejected was never running it,
            # so a supersession notice would assert a lifecycle step it never
            # reached. Only the *response* is filtered: the bookkeeping set
            # above still records every affected device, because it drives dispatch
            # exclusion, the fully-superseded check below, and the DDERC fallback.
            #
            # Subtraction rather than the positive ``applied_devices`` test used for
            # COMPLETED/CANCELLED: an event still SCHEDULED has applied nothing yet
            # and can legitimately be superseded before it ever starts, so it must
            # still announce.
            announce = newly_superseded - superseded.rejected_devices
            if announce:
                await self._post_response_for_devices(
                    superseded.derc, ResponseCode.SUPERSEDED, announce
                )

            # Track per-mode supersession per device
            if result.affected_modes:
                for dev in result.affected_devices:
                    existing = superseded.superseded_modes.get(dev, frozenset())
                    superseded.superseded_modes[dev] = existing | result.affected_modes

            # If all devices for this program are superseded, fully supersede
            program_devices = device_map.get(superseded.program_href, set())
            if program_devices and superseded.superseded_devices >= program_devices:
                was_active = superseded.state == EventState.ACTIVE
                logger.info(
                    "Event %s: SUPERSEDED by %s (was %s)",
                    superseded.mrid.hex()[:8],
                    result.superseding_mrid.hex()[:8],
                    superseded.state.value,
                )
                self._store.transition(superseded.mrid, EventState.SUPERSEDED)
                self._timer_mgr.cancel(superseded.mrid)
                self._relay_control_event(superseded, "superseded")

                # Per-device SUPERSEDED responses were already posted above
                # as each device was added to newly_superseded.
                if was_active:
                    await self._apply_dderc_fallback(superseded)

    def _build_program_device_map(self) -> dict[str, set[str]]:
        """Build mapping from program_href to set of device hrefs."""
        result: dict[str, set[str]] = {}
        for href, devices in self._state.device_mapping.program_to_devices.items():
            result[href] = set(devices)
        return result

    async def _apply_and_respond(self, record: EventRecord) -> None:
        """Dispatch the control, then report each device by its own outcome.

        Status 2 asserts the event started, so it is posted for a device only
        once that device's apply has returned successfully. A failure reports
        the matching rejection code instead and marks the device, which
        keeps it out of the COMPLETED response at the event's end.
        """
        mrid_short = record.mrid.hex()[:8]

        async def respond(dev_href: str, failure: BaseException | None) -> None:
            if failure is None:
                record.applied_devices.add(dev_href)
                await self._post_response_for_devices(record.derc, ResponseCode.ACTIVE, {dev_href})
                return
            record.rejected_devices.add(dev_href)
            # Modes are registered for every target before dispatch, since the
            # outcome isn't known yet. Release this device's registration now that
            # it is: leaving it would have the tracker believe a control that was
            # never applied still owns those modes, so a later event releasing the
            # same mode would look "still covered" and skip its DDERC fallback.
            self._mode_tracker.unregister(dev_href, record.mrid)
            code = response_code_for_dispatch_error(failure)
            logger.info("Event %s: %s rejected -> %s", mrid_short, dev_href, code.name)
            await self._post_response_for_devices(record.derc, code, {dev_href})

        outcomes = await self._apply_control_to_devices(record, on_result=respond)
        if outcomes:
            return
        # No dispatch targets -- the program has no device mapping, so nothing
        # could fail. Keep the existing any-LFDI ACTIVE announcement rather than
        # leaving the server with no notice that the event started.
        await self._post_response(
            record.derc,
            ResponseCode.ACTIVE,
            record.program_href,
            exclude_devices=record.superseded_devices or None,
        )

    async def _apply_control_to_devices(
        self,
        record: EventRecord,
        on_result: Callable[[str, BaseException | None], Awaitable[None]] | None = None,
    ) -> dict[str, BaseException | None]:
        """Apply a DERControl to all devices in the program's group (parallel).

        Returns the per-device outcome keyed by device href: ``None`` where the
        control was applied, otherwise the failure. ``on_result`` is awaited for
        each device as soon as *that* device's dispatch settles, so a caller can
        post its response without waiting on the rest of the fleet.
        """
        devices = self._state.device_mapping.program_to_devices.get(record.program_href, [])
        derp_state = self._state.der_programs.get(record.program_href)
        curves = derp_state.der_curves if derp_state else []
        mrid_short = record.mrid.hex()[:8]
        # dict.fromkeys, not a set: one write per device, in discovery order.
        # DeviceMapping no longer admits a repeated pair, but this list is the
        # dispatch target list and a caller may populate state itself -- and a
        # device dispatched twice is written to twice, which reaches hardware.
        targets = list(
            dict.fromkeys(d for d in devices if d not in record.superseded_devices)
        )
        logger.debug("Event %s: applying control to %d device(s)", mrid_short, len(targets))
        if not targets:
            return {}

        # Track active modes per device for per-mode DDERC fallback
        active_modes = get_active_mode_names(record.derc.dercontrol_base)
        for dev_href in targets:
            self._mode_tracker.register(dev_href, record.mrid, active_modes)

        # A caller may supply a ``group_lookup`` that resolves one
        # server-side EndDevice href to several local sub-device LFDIs,
        # in which case dispatch is by LFDI. Unset by default.
        local_lfdis = self._group_local_lfdis(record.program_href)
        if local_lfdis is not None:
            return await self._dispatch_by_lfdi_group(
                record, local_lfdis, targets, curves, on_result
            )

        async def dispatch_and_report(dev_href: str) -> tuple[str, BaseException | None]:
            failure = await self._dispatch_one(
                mrid_short, dev_href, self._dispatcher.apply_control(dev_href, record.derc, curves)
            )
            if on_result is not None:
                await on_result(dev_href, failure)
            return dev_href, failure

        pairs = await asyncio.gather(*[dispatch_and_report(d) for d in targets])
        return dict(pairs)

    @staticmethod
    def _log_late_dispatch(mrid_short: str, label: str, task: asyncio.Task[None]) -> None:
        """Consume and report a dispatch that finished after its rejection was sent.

        Retrieving the exception is the point: nothing awaits these tasks once
        the ceiling has fired, so a failure would sit unretrieved until GC and
        surface as a bare "Task exception was never retrieved". It also answers
        the question the rejection left open -- whether the control eventually
        landed on the device.
        """
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Event %s: late dispatch to %s failed: %s", mrid_short, label, exc)
        else:
            logger.info(
                "Event %s: late dispatch to %s applied after its rejection was sent",
                mrid_short,
                label,
            )

    async def _dispatch_one(
        self, mrid_short: str, label: str, coro: Coroutine[Any, Any, None]
    ) -> BaseException | None:
        """Await one device's dispatch under the concurrency ceiling. Returns the failure.

        The ceiling bounds when this device's *response* goes out, not how long
        the control application gets. On expiry the apply is left running under
        the connector's own deadline -- shielded, so ``wait_for`` does not
        cancel it -- and a rejection is reported for the device now. A slow
        device therefore still receives the control; the client has simply
        stopped claiming the event started on it.
        """
        task = asyncio.ensure_future(coro)
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=ACTIVATION_DISPATCH_DEADLINE)
        except TimeoutError as exc:
            logger.warning(
                "Event %s: dispatch to %s still running after %.1fs; reporting a "
                "rejection now while the apply continues",
                mrid_short,
                label,
                ACTIVATION_DISPATCH_DEADLINE,
            )
            # Past the ceiling nothing awaits this task, so a later failure would
            # sit unretrieved until GC and surface with no context.
            task.add_done_callback(
                lambda finished: self._log_late_dispatch(mrid_short, label, finished)
            )
            return exc
        except Exception as exc:
            logger.warning("Event %s: dispatch to %s failed: %s", mrid_short, label, exc)
            return exc
        return None

    async def _dispatch_by_lfdi_group(
        self,
        record: EventRecord,
        local_lfdis: list[str],
        targets: list[str],
        curves: list[Any],
        on_result: Callable[[str, BaseException | None], Awaitable[None]] | None,
    ) -> dict[str, BaseException | None]:
        """Dispatch by local sub-device LFDI and collapse onto the server's hrefs.

        A group lookup resolves one server-side EndDevice to N local
        sub-devices, but the server only knows the aggregate -- there is no
        response channel for an individual sub-device. So the aggregate is
        reported rejected only when *every* sub-device failed; a partial failure
        still performed the event, and reporting it as unsupported would
        misstate a fleet that largely complied. This collapse also forces a
        barrier here: the aggregate outcome is not known until all N return.
        """
        mrid_short = record.mrid.hex()[:8]
        failures = await asyncio.gather(
            *[
                self._dispatch_one(
                    mrid_short,
                    lfdi,
                    self._dispatcher.apply_control_by_lfdi(lfdi, record.derc, curves),
                )
                for lfdi in local_lfdis
            ]
        )
        failed = [f for f in failures if f is not None]
        aggregate = failed[0] if len(failed) == len(failures) else None
        if failed and aggregate is None:
            logger.info(
                "Event %s: %d/%d sub-devices failed; aggregate still reported ACTIVE",
                mrid_short,
                len(failed),
                len(failures),
            )
        if on_result is not None:
            for dev_href in targets:
                await on_result(dev_href, aggregate)
        return dict.fromkeys(targets, aggregate)

    def _fsa_scope(self, program_href: str) -> str | None:
        """Timebase scope for a program (IEEE 9.2.3): its discovering FSA."""
        ps = self._state.der_programs.get(program_href)
        return ps.discovered_from_fsa_href if ps else None

    def _now_fn_for(self, program_href: str) -> Callable[[], int]:
        """Clock for a program's timers: server timebase, FSA-scoped.

        The scope is bound at creation (a program's FSA parentage is stable
        for a record's lifetime) so the timer loop doesn't re-resolve state
        on every tick.
        """
        tb = self._timebase
        scope = self._fsa_scope(program_href)
        return lambda: int(tb.now(scope))

    def _group_local_lfdis(self, program_href: str) -> list[str] | None:
        """Return local sub-device LFDIs for ``program_href``, if grouped.

        Returns None when no ``group_lookup`` is configured or the callback
        finds no group for this program -- in which case the dispatcher
        dispatches 1-to-1 by server-side href, which is the default.
        """
        if self._group_lookup is None:
            return None
        local_lfdis = self._group_lookup(program_href)
        return local_lfdis if local_lfdis else None

    # ------------------------------------------------------------------
    # Schedule-notification relay (informational push; not a control path)
    # ------------------------------------------------------------------

    def _affected_lfdis_for_program(
        self, program_href: str, exclude_hrefs: set[str] | None = None
    ) -> list[str]:
        """Resolve the hex LFDIs a program's events scope to.

        A supplied group lookup resolves the local sub-device LFDIs
        (`_group_local_lfdis`, already hex `str`); otherwise the program's
        EndDevice hrefs map to LFDIs via `_get_device_lfdi` (`bytes` -> `.hex()`),
        excluding ``exclude_hrefs`` (superseded devices) the same way
        ``_apply_control_to_devices`` does, so the relayed scope matches dispatch.
        """
        local = self._group_local_lfdis(program_href)
        if local is not None:
            return list(local)
        exclude = exclude_hrefs or set()
        out: list[str] = []
        for dev_href in self._state.device_mapping.program_to_devices.get(program_href, []):
            if dev_href in exclude:
                continue
            lfdi = self._get_device_lfdi(dev_href)
            if lfdi is not None:
                out.append(lfdi.hex())
        return out

    def _relay_control_event(self, record: EventRecord, transition: str) -> None:
        """Relay a DERControl lifecycle change to affected connectors (stream=control)."""
        derc = record.derc
        current_status = derc.event_status.current_status if derc.event_status is not None else None
        randomization = (
            derc.randomize_start.value if derc.randomize_start is not None else None,
            derc.randomize_duration.value if derc.randomize_duration is not None else None,
        )
        notification = ScheduleNotification(
            stream="control",
            transition=transition,
            status=record.state.value,
            current_status=current_status,
            mrid=record.mrid.hex(),
            program_href=record.program_href,
            primacy=record.primacy,
            start=record.start,
            duration=record.duration,
            end=record.end,
            affected_lfdis=self._affected_lfdis_for_program(
                record.program_href, record.superseded_devices
            ),
            randomization=randomization,
            payload=serialize_der_control(derc),
        )
        # Lifecycle transitions are distinct events and always fire; the per-poll
        # "updated" refresh is content-deduped so an unchanged control doesn't re-relay.
        self._emit_relay(notification, dedup=(transition == "updated"))

        # `doe` projection: when this control carries CSIP-AUS envelope limits,
        # also emit a `doe` notification carrying only those limits, so an
        # optimizer can track operating envelopes without setpoint noise. Same
        # envelope/transition as `control`; deduped independently (keyed by
        # ("doe", mrid)) on the envelope alone -- a setpoint-only change updates
        # `control` but not `doe`.
        envelope = _extract_doe_envelope(derc.dercontrol_base)
        if envelope:
            doe = replace(notification, stream="doe", payload={"control_base": envelope})
            self._emit_relay(doe, dedup=(transition == "updated"))

    def _relay_default_baseline(self, program_href: str, derp_state: DerProgramState) -> None:
        """Relay a DefaultDERControl add/change (stream=default_baseline).

        Driven each `process_controls` pass; content-deduped so an unchanged
        baseline doesn't re-relay. First sighting -> ``default_added``, a content
        change -> ``default_updated``. A baseline has no EventStatus/interval, so
        status/timing fields are null.
        """
        dderc = derp_state.default_dercontrol
        if dderc is None:
            return
        mrid = dderc.m_rid.value.hex()
        # One baseline slot per program (not per mRID) so a DDERC mRID swap is a
        # "default_updated" and the cache stays bounded by program count.
        key = ("default_baseline", program_href)
        transition = "default_added" if key not in self._relay_snapshots else "default_updated"
        notification = ScheduleNotification(
            stream="default_baseline",
            transition=transition,
            status=None,
            current_status=None,
            mrid=mrid,
            program_href=program_href,
            primacy=derp_state.primacy,
            start=None,
            duration=None,
            end=None,
            affected_lfdis=self._affected_lfdis_for_program(program_href),
            randomization=(None, None),
            payload=serialize_default_der_control(dderc) or {},
        )
        self._emit_relay(notification, dedup=True, dedup_key=key)

    def _emit_relay(
        self,
        notification: ScheduleNotification,
        *,
        dedup: bool,
        dedup_key: tuple[str, str] | None = None,
    ) -> None:
        """Schedule the relay as a fire-and-forget task, applying fire-on-change.

        The content signature excludes ``transition`` so that the per-poll
        ``updated``/``default_updated`` path suppresses unchanged content while
        genuine lifecycle transitions (which the caller fires with ``dedup=False``)
        always go out.

        ``dedup_key`` overrides the snapshot key. Defaults to ``(stream, mrid)``
        (one slot per event). The baseline path passes ``(stream, program_href)``
        so a program keeps a single baseline slot regardless of mRID churn --
        bounding the cache to program count and turning a DDERC mRID swap into a
        ``default_updated`` rather than a fresh ``default_added`` + leaked slot.
        """
        # Actionable-content signature only: event identity, EventState,
        # effective interval, randomization, scope, and the DERControlBase.
        # Deliberately excludes transition, raw current_status, and the volatile
        # serialized fields (creation_time, event_status.date_time) that churn
        # each poll -- so an unchanged control isn't re-relayed as "updated".
        # ``mrid`` is included because the baseline key is program-scoped, so a
        # DDERC mRID swap must still register as a change.
        content_sig: tuple[Any, ...] = (
            notification.mrid,
            notification.status,
            notification.start,
            notification.duration,
            tuple(sorted(notification.affected_lfdis)),
            notification.randomization,
            json.dumps(notification.payload.get("control_base"), sort_keys=True, default=str),
        )
        key = dedup_key if dedup_key is not None else (notification.stream, notification.mrid)
        if dedup and self._relay_snapshots.get(key) == content_sig:
            return
        self._relay_snapshots[key] = content_sig
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - no running loop (unexpected sync path)
            return
        task = loop.create_task(
            self._dispatcher.relay_schedule_notification(
                list(notification.affected_lfdis), notification
            )
        )
        self._relay_tasks.add(task)
        task.add_done_callback(self._relay_tasks.discard)

    async def enter_comms_loss(self) -> None:
        """Opt out of every active event and revert devices to the planning limit.

        Loss-of-communications entry: each active event is marked
        opted-out, its timers cancelled, and its devices reverted to DDERC (or
        cleared to the connector safe-default where no DDERC exists). No IEEE
        2030.5 responses are posted -- comms are down. The resume boundary is
        raised to the latest opted-out event end so recovery resumes only the
        schedule that follows it. Events stay in ACTIVE state (the ``opted_out``
        flag is the marker), so supersession and classification are unaffected.
        """
        active = [r for r in self._store.by_state(EventState.ACTIVE) if not r.opted_out]
        if not active:
            return
        boundary = self._comms_loss.resume_after_epoch or 0
        self._comms_loss.resume_after_epoch = max(boundary, max(r.end for r in active))
        # Flag all opted-out first so per-record reverts see no "other active"
        # event still covering a shared device.
        for record in active:
            record.opted_out = True
            self._timer_mgr.cancel(record.mrid)
        for record in active:
            await self._apply_dderc_fallback(record, clear_if_no_dderc=True)

    async def _opt_out_event(self, record: EventRecord) -> None:
        """Opt a single event out (comms-loss gate on activation/late discovery).

        Marks the event opted-out, raises the resume boundary to its end,
        cancels its timers, and reverts its devices to DDERC/clear. No response
        is posted.
        """
        record.opted_out = True
        boundary = self._comms_loss.resume_after_epoch or 0
        self._comms_loss.resume_after_epoch = max(boundary, record.end)
        self._timer_mgr.cancel(record.mrid)
        await self._apply_dderc_fallback(record, clear_if_no_dderc=True)

    async def _apply_dderc_fallback(
        self, record: EventRecord, *, clear_if_no_dderc: bool = False
    ) -> None:
        """Apply per-mode DDERC fallback for modes released by the completing event.

        When an event completes/cancels/supersedes, unregister its modes from the
        tracker. Devices whose released modes are not covered by any other active
        event get DDERC applied. If no modes were tracked (empty DercontrolBase),
        fall back to checking whether other active events cover the device.

        ``clear_if_no_dderc`` is the comms-loss opt-out failsafe: a device
        with no DDERC on any program is dispatched a connector clear instead of
        being left untouched. Opted-out events (``record.opted_out``) are ignored
        when deciding whether another active event still covers a device, so
        opting out every active event reverts the device to its planning limit.
        """
        all_devices = self._state.device_mapping.program_to_devices.get(record.program_href, [])
        # Exclude devices that were superseded on this event — they were never
        # owned by this event and their DDERC fallback is handled by whichever
        # event actually controlled them.
        devices = [d for d in all_devices if d not in record.superseded_devices]
        active_events = [r for r in self._store.by_state(EventState.ACTIVE) if not r.opted_out]
        # Devices with no DDERC anywhere: cleared to the connector safe-default
        # only in the comms-loss opt-out flow; the completion path leaves
        # them untouched as before.
        clear_targets: list[tuple[str, bytes]] = []

        # Unregister modes and compute per-device fallback needs
        needs_fallback: list[str] = []
        for dev_href in devices:
            released = self._mode_tracker.unregister(dev_href, record.mrid)
            if released:
                # Some modes were released and not covered by other events
                needs_fallback.append(dev_href)
            elif not self._device_has_other_active(dev_href, record.mrid, active_events):
                # No modes tracked (empty base) but no other active events either
                needs_fallback.append(dev_href)

        if not needs_fallback:
            return

        # For each device, find the highest-priority program with a DDERC.
        # A shared device may belong to multiple programs; on event completion,
        # it should revert to the best (lowest primacy number) DDERC available.
        dderc_targets: list[tuple[str, bytes, DerProgramState]] = []
        for dev_href in needs_fallback:
            lfdi = self._get_device_lfdi(dev_href)
            if lfdi is None:
                continue
            best = self._best_dderc_program_for_device(dev_href)
            if best is None:
                if clear_if_no_dderc:
                    clear_targets.append((dev_href, lfdi))
                else:
                    logger.debug(
                        "Event %s: no DDERC for device %s, no revert action taken",
                        record.mrid.hex()[:8],
                        dev_href,
                    )
                continue
            if self._dderc_tracker.should_apply(lfdi, best.href, best.primacy):
                dderc_targets.append((dev_href, lfdi, best))

        # Dispatch DDERC in parallel, then update tracker for successes.
        # With a group lookup supplied, each server-side EDev resolves
        # to the program's local sub-device LFDIs; dispatch by LFDI then.
        # Cache the lookup per target so a mid-await groups rebuild can't
        # change the fan-out width between the build and attribution loops.
        if dderc_targets:
            # default_dercontrol is guaranteed non-None by _best_dderc_program_for_device
            plans: list[tuple[str, bytes, DerProgramState, list[str] | None]] = [
                (dev_href, lfdi, best_prog, self._group_local_lfdis(best_prog.href))
                for dev_href, lfdi, best_prog in dderc_targets
            ]
            # Both reasons land on the same DDERC write, and they read very
            # differently in an audit trail: "an event ended so the default came
            # back" versus "the upstream went away so we fell to the planning
            # limit". Distinguishing them here is what makes the recorded origin
            # answer why a setpoint moved.
            origin = (
                CommandOrigin.COMMS_LOSS if self._comms_loss.active else CommandOrigin.DDERC_REAPPLY
            )
            coros: list[Coroutine[Any, Any, None]] = []
            for dev_href, _, best_prog, local_lfdis in plans:
                if local_lfdis is not None:
                    coros.extend(
                        self._dispatcher.apply_default_control_by_lfdi(
                            lfdi,
                            best_prog.default_dercontrol,  # type: ignore[arg-type]
                            best_prog.der_curves,
                            origin=origin,
                        )
                        for lfdi in local_lfdis
                    )
                else:
                    coros.append(
                        self._dispatcher.apply_default_control(
                            dev_href,
                            best_prog.default_dercontrol,  # type: ignore[arg-type]
                            best_prog.der_curves,
                            origin=origin,
                        )
                    )
            results = await asyncio.gather(*coros, return_exceptions=True)

            # The tracker is keyed by the server-side device LFDI (one entry
            # per dderc_target); collapse per-fan-out results down to one
            # success per target so the tracker isn't double-recorded.
            idx = 0
            for dev_href, lfdi, best_prog, local_lfdis in plans:
                n = len(local_lfdis) if local_lfdis is not None else 1
                target_results = results[idx : idx + n]
                idx += n
                failure: Exception | None = next(
                    (r for r in target_results if isinstance(r, Exception)), None
                )
                if failure is not None:
                    logger.warning(
                        "Event %s: DDERC dispatch to %s failed: %s",
                        record.mrid.hex()[:8],
                        dev_href,
                        failure,
                    )
                else:
                    self._dderc_tracker.record_application(
                        lfdi, best_prog.href, record.mrid, best_prog.primacy
                    )

        if clear_targets:
            # No DDERC to fall back to: clear the device to the connector
            # safe-default. Any fan-out is per-program (the same grouping
            # as the DDERC path above), so it is
            # built once -- not per target device, which would dispatch
            # duplicate clears to every local sub-device. Otherwise clear each
            # server-side device href directly.
            local_lfdis = self._group_local_lfdis(record.program_href)
            clear_coros: list[Coroutine[Any, Any, None]] = []
            if local_lfdis is not None:
                # dict.fromkeys: group_lookup and DeviceMapping.add don't dedup,
                # so guard against dispatching the same clear twice.
                clear_coros.extend(
                    self._dispatcher.clear_control_by_lfdi(local)
                    for local in dict.fromkeys(local_lfdis)
                )
            else:
                clear_coros.extend(
                    self._dispatcher.clear_control(dev_href)
                    for dev_href in dict.fromkeys(dev_href for dev_href, _lfdi in clear_targets)
                )
            clear_results = await asyncio.gather(*clear_coros, return_exceptions=True)
            for failure in (r for r in clear_results if isinstance(r, Exception)):
                logger.warning(
                    "Event %s: comms-loss clear dispatch failed: %s",
                    record.mrid.hex()[:8],
                    failure,
                )

    async def _apply_initial_dderc(self, program_href: str) -> None:
        """Apply DDERC to devices with no active events from any program.

        Called at the end of process_controls to ensure devices are in their
        default operating state when no event overrides it. Only applies if:
        - The program has a DefaultDERControl
        - The device has no ACTIVE event from ANY program
        - No wind-down timers are pending (cancelled event with delayed fallback)
        - The DDERC hasn't already been applied (tracked by DdercTracker)
        """
        derp_state = self._state.der_programs.get(program_href)
        if derp_state is None or derp_state.default_dercontrol is None:
            logger.debug(
                "Initial DDERC skip %s: no DDERC (state=%s, dderc=%s)",
                program_href,
                derp_state is not None,
                derp_state.default_dercontrol is not None if derp_state else False,
            )
            return

        # Skip if any cancelled event from this program still has pending
        # timers (wind-down delay before DDERC fallback).
        cancelled = self._store.by_state(EventState.CANCELLED)
        if any(
            evt.program_href == program_href and self._timer_mgr.has_pending(evt.mrid)
            for evt in cancelled
        ):
            logger.debug("Initial DDERC skip %s: cancelled event with pending timer", program_href)
            return

        devices = self._state.device_mapping.program_to_devices.get(program_href, [])
        if not devices:
            logger.debug("Initial DDERC skip %s: no devices in mapping", program_href)
            return

        # Build set of devices that have an ACTIVE event in ANY program.
        # These devices are already under event control and must not get DDERC.
        # Opted-out events (comms-loss) don't control their devices -- the device
        # was reverted to DDERC -- so they're excluded here, consistent with
        # _apply_dderc_fallback's "other active" check. A device the event was
        # *rejected* on is the same situation and excluded for the same reason:
        # the control was refused, so nothing is holding that device, and without
        # this it would sit under no control at all for the event's whole duration
        # rather than under its default.
        active_events = [e for e in self._store.by_state(EventState.ACTIVE) if not e.opted_out]
        active_devices: set[str] = set()
        for evt in active_events:
            evt_devs = self._state.device_mapping.program_to_devices.get(evt.program_href, [])
            for d in evt_devs:
                if d not in evt.superseded_devices and d not in evt.rejected_devices:
                    active_devices.add(d)

        curves = derp_state.der_curves
        dderc_mrid = derp_state.default_dercontrol.m_rid.value

        targets: list[tuple[str, bytes]] = []
        for dev_href in devices:
            if dev_href in active_devices:
                continue
            lfdi = self._get_device_lfdi(dev_href)
            if lfdi is None:
                continue

            if self._dderc_tracker.should_apply_initial(
                lfdi, program_href, dderc_mrid, derp_state.primacy
            ):
                targets.append((dev_href, lfdi))
            else:
                logger.debug(
                    "Initial DDERC skip %s: tracker blocked for %s (primacy=%d)",
                    program_href,
                    dev_href,
                    derp_state.primacy,
                )

        if not targets:
            logger.debug("Initial DDERC skip %s: no eligible devices", program_href)
            return

        logger.info(
            "Applying initial DDERC from %s to %d device(s)",
            program_href,
            len(targets),
        )
        # With a group lookup supplied, each server-side EDev resolves
        # to the program's local sub-device LFDIs.
        local_lfdis = self._group_local_lfdis(program_href)
        coros: list[Coroutine[Any, Any, None]] = []
        for dev_href, _ in targets:
            if local_lfdis is not None:
                coros.extend(
                    self._dispatcher.apply_default_control_by_lfdi(
                        lfdi, derp_state.default_dercontrol, curves
                    )
                    for lfdi in local_lfdis
                )
            else:
                coros.append(
                    self._dispatcher.apply_default_control(
                        dev_href, derp_state.default_dercontrol, curves
                    )
                )
        results = await asyncio.gather(*coros, return_exceptions=True)

        per_target_n = len(local_lfdis) if local_lfdis is not None else 1
        idx = 0
        for dev_href, lfdi in targets:
            target_results = results[idx : idx + per_target_n]
            idx += per_target_n
            failure: Exception | None = next(
                (r for r in target_results if isinstance(r, Exception)), None
            )
            if failure is not None:
                logger.warning("Initial DDERC dispatch to %s failed: %s", dev_href, failure)
            else:
                self._dderc_tracker.record_application(
                    lfdi, program_href, dderc_mrid, derp_state.primacy
                )

    def _device_has_other_active(
        self, dev_href: str, exclude_mrid: bytes, active_events: list[EventRecord]
    ) -> bool:
        """Check if any active event (other than exclude_mrid) applies to dev_href.

        A device the event was rejected on is not under its control -- the same
        reasoning that excludes superseded devices and opted-out events. Counting
        it would withhold the DDERC fallback on the strength of a control that was
        never applied.
        """
        for evt in active_events:
            if evt.mrid == exclude_mrid:
                continue
            prog_devices = self._state.device_mapping.program_to_devices.get(evt.program_href, [])
            if (
                dev_href in prog_devices
                and dev_href not in evt.superseded_devices
                and dev_href not in evt.rejected_devices
            ):
                return True
        return False

    def _best_dderc_program_for_device(self, dev_href: str) -> DerProgramState | None:
        """Find the highest-priority program with a DDERC that covers this device."""
        best: DerProgramState | None = None
        for prog_href, prog_devices in self._state.device_mapping.program_to_devices.items():
            if dev_href not in prog_devices:
                continue
            derp = self._state.der_programs.get(prog_href)
            if derp is None or derp.default_dercontrol is None:
                continue
            if best is None or derp.primacy < best.primacy:
                best = derp
        return best

    def _get_device_lfdi(self, dev_href: str) -> bytes | None:
        """Look up LFDI for a device href."""
        edev_state = self._state.end_devices.get(dev_href)
        if edev_state is not None:
            return edev_state.lfdi
        return None

    async def _post_response(
        self,
        derc: object,
        code: ResponseCode,
        program_href: str | None = None,
        exclude_devices: set[str] | None = None,
    ) -> None:
        """Post a DER response for each device in the program.

        Multi-device conformance (AGG tests) requires one response per device.
        The ResponseTracker deduplicates on ``(mrid, code, lfdi)`` so each
        device gets exactly one response per code.

        Args:
            derc: The DERControl being responded to
            code: The response code
            program_href: The program href containing the control (used to find devices)
            exclude_devices: Device hrefs to skip (e.g. superseded devices)
        """
        if not isinstance(derc, Dercontrol1):
            return

        devices = (
            self._state.device_mapping.program_to_devices.get(program_href, [])
            if program_href
            else []
        )

        if not devices:
            # No program mapping — fall back to any available LFDI.
            if exclude_devices:
                # The fallback guesses a device. With an exclusion set in play we
                # cannot check the guess against it (the mapping that would
                # resolve href -> LFDI is the thing that is missing), and posting
                # a lifecycle code to a device that was superseded or rejected
                # misreports the event. Silence is the safer error here.
                logger.warning(
                    "No device mapping for %s and %d device(s) excluded; skipping the "
                    "any-LFDI %s fallback rather than risk posting to an excluded device",
                    program_href,
                    len(exclude_devices),
                    code.name,
                )
                return
            lfdi = self._get_any_lfdi()
            if lfdi is None:
                logger.warning("No device LFDI available for response posting")
                return
            await post_der_response(
                self._http,
                derc,
                code,
                lfdi,
                self._response_tracker,
                now_ts=int(self._timebase.now()),
            )
            return

        for dev_href in devices:
            if exclude_devices and dev_href in exclude_devices:
                continue
            lfdi = self._get_device_lfdi(dev_href)
            if lfdi is not None:
                await post_der_response(
                    self._http,
                    derc,
                    code,
                    lfdi,
                    self._response_tracker,
                    now_ts=int(self._timebase.now()),
                )

    async def _post_response_for_devices(
        self, derc: object, code: ResponseCode, device_hrefs: frozenset[str] | set[str]
    ) -> None:
        """Post a DER response for specific devices only."""
        if not isinstance(derc, Dercontrol1):
            return
        for dev_href in device_hrefs:
            lfdi = self._get_device_lfdi(dev_href)
            if lfdi is not None:
                await post_der_response(
                    self._http,
                    derc,
                    code,
                    lfdi,
                    self._response_tracker,
                    now_ts=int(self._timebase.now()),
                )

    def _get_any_lfdi(self) -> bytes | None:
        """Get any available device LFDI for response posting."""
        for edev in self._state.end_devices.values():
            return edev.lfdi
        return None

    def _find_successive_predecessor(self, program_href: str, raw_start: int) -> EventRecord | None:
        """Find an event in the same program whose raw end equals raw_start.

        Used for IEEE 10.2.2.3 rule m) successive events detection.
        """
        for record in self._store.all_active_states():
            if record.program_href != program_href:
                continue
            # Check if raw end of predecessor matches raw start of this event
            raw_end = record.derc.interval.start.value + record.derc.interval.duration
            if raw_end == raw_start:
                return record
        return None

    @staticmethod
    def _is_server_cancelled(derc: object) -> bool:
        if not isinstance(derc, Dercontrol1):
            return False
        return derc.event_status is not None and derc.event_status.current_status == 2
