"""Top-level event processor orchestrating the DERControl lifecycle.

Called from poll callbacks after resource discovery. Processes DERControls
through the event state machine: randomize, classify, upsert, supersede,
activate, complete, and prune.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable, Iterator, Sequence
from dataclasses import replace
from functools import cache, partial
from typing import Any

from py20305.audit import AuditEmitter
from py20305.client.discovery import read_der_control_mrids
from py20305.client.http import Sep2Client
from py20305.client.state import DerProgramState, DiscoveredState
from py20305.client.timebase import ServerTimebase
from py20305.commands import CommandOrigin
from py20305.connectors.base import ScheduleNotification
from py20305.connectors.control_errors import DeviceNotConfiguredError, ModeNotSupportedError
from py20305.events.active_modes import ActiveModeTracker
from py20305.events.comms_loss import CommsLossState
from py20305.events.dderc import DdercTracker
from py20305.events.dispatch import ControlDispatcher
from py20305.events.modes_bitmask import get_active_mode_names
from py20305.events.randomization import NO_DEVICE, RandomizationCache
from py20305.events.response import (
    ResponseCode,
    ResponseTracker,
    post_der_response,
    response_code_for_dispatch_error,
)
from py20305.events.shared_state import SNAPSHOT_VERSION, EventStateStore
from py20305.events.state_machine import EventRecord, EventState, EventStore
from py20305.events.supersession import compared_mode_keys, compute_supersession
from py20305.events.timers import EventTimerManager
from py20305.history.events import EventHistory
from py20305.json_form import (
    safe_serialize,
    serialize_default_der_control,
    serialize_der_control,
)
from py20305.models.csipaus.elements import DOE_LIMIT_NAMES, doe_element_name, doe_limits
from py20305.models.sep.sep import (
    DefaultDercontrol,
    Dercontrol1,
    RespondableSubscribableIdentifiedObject,
)

logger = logging.getLogger(__name__)

#: CSIP-AUS Dynamic-Operating-Envelope limit fields. They ride in
#: ``DERControlBase.other_element``, not as first-class base attributes; see
#: ``models.csipaus.elements`` for how they are read.
_DOE_ENVELOPE_NAMES = tuple(sorted(DOE_LIMIT_NAMES))

#: Ceiling on how long a device's activation response waits on its
#: dispatch. ACTIVE is now posted only after the device's apply returns, so this
#: bounds how far past the event's effective start that response can land -- the
#: instant CORE-022 measures against. Deliberately far below the connector layer's
#: 30s CONTROL_DEADLINE. It gates the *response* only: a slower device is
#: reported rejected while its apply keeps running under the connector's own
#: deadline, so the control still reaches it.
ACTIVATION_DISPATCH_DEADLINE = 5.0

#: DefaultDERControl fields left out of its content digest: identity and
#: bookkeeping, which do not change what the device is told to do. Extension
#: content (``other_element``, ``any_attributes``) stays in.
_DEFAULT_BOOKKEEPING_FIELDS = (
    frozenset(RespondableSubscribableIdentifiedObject.model_fields) | {"updated_time"}
) - {"other_element", "any_attributes"}


def _default_content(dderc: DefaultDercontrol) -> bytes:
    """Return a digest of the settings a DefaultDERControl tells the device to use.

    A server that modifies a default keeps its mRID and increments its version
    (IEEE 2030.5-2023 VersionType), so the mRID cannot tell the tracker that the
    device needs the default written again. The digest can. The version itself
    is left out: a new version with the same settings needs no write.
    """
    settings = {
        name: getattr(dderc, name)
        for name in type(dderc).model_fields
        if name not in _DEFAULT_BOOKKEEPING_FIELDS
    }
    text = json.dumps(safe_serialize(settings), sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).digest()


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
    it fractional. Typed and generic elements are read alike.
    """
    return {
        name: {
            "value": value,
            "multiplier": multiplier,
            "watts": _resolve_watts(value, multiplier),
        }
        for name, (value, multiplier) in doe_limits(base).items()
    }


@cache
def _wire_names() -> dict[str, str]:
    """DERControlBase field names to their IEEE 2030.5 element names.

    Taken from the XML binding that serializes the model, so the two cannot
    disagree.
    """
    from py20305.models.sep.sep import DercontrolBase
    from py20305.xml.serialization import _context

    return {var.name: var.local_name for var in _context.build(DercontrolBase).get_all_vars()}


def _wire_mode_names(modes: Iterable[str]) -> list[str]:
    """Modes as the audit trail names them: by their element name on the wire,
    as every other identifier in its records is."""
    names = _wire_names()
    return sorted(names.get(mode, mode) for mode in modes)


def _record_response(
    history: EventHistory,
    mrid: bytes,
    code: ResponseCode,
    lfdi: bytes,
    at: int,
    error: str | None,
) -> None:
    """Store one response POST in the event history."""
    history.record_response(
        event_mrid=mrid.hex().upper(),
        lfdi=lfdi.hex(),
        status=int(code),
        status_name=code.name,
        at=float(at),
        posted=error is None,
        error=error,
    )


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
        audit: AuditEmitter | None = None,
        state_store: EventStateStore | None = None,
    ) -> None:
        # ``state_store`` shares the event state of each EndDevice with other
        # processes acting as the same client, so one can take a device over
        # from another (see py20305.events.shared_state). None keeps it here.
        #
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
        # Lifecycle records for the audit trail. Disabled until the host
        # configures it, so every call below is a no-op by default.
        self._audit = audit if audit is not None else AuditEmitter()
        self._audit_failures = 0
        #: Events recorded as expired or skipped, by mRID, with their program.
        #: They are not stored, so this is what stops each poll recording them
        #: again. Kept while the program still serves them, not by age: both
        #: kinds can be past their end and still served, poll after poll.
        self._audit_unstored_seen: dict[bytes, str] = {}
        self._store = EventStore()
        self._state_store = state_store
        self._rand_cache = RandomizationCache(
            claim=self._claim_offsets if state_store is not None else None
        )
        self._response_tracker = ResponseTracker()
        if state_store is not None:
            self._response_tracker.on_sent = self._record_sent
        #: The snapshot last saved for each EndDevice, so an unchanged one is
        #: not written again.
        self._saved: dict[str, dict[str, Any]] = {}
        #: EndDevices handed to another process: their snapshots are not
        #: written from here any more.
        self._released: set[str] = set()
        #: EndDevices loaded by load_devices that resume_devices has not
        #: resumed yet. Their timers are left to resume_devices.
        self._awaiting_resume: set[str] = set()
        #: EndDevices taken over whose Response ledger is restored once
        #: discovery gives their LFDI.
        self._ledger_pending: set[str] = set()
        #: Loss-of-communications resume boundaries loaded with EndDevices
        #: taken over, by device href. They gate only those devices' events.
        self._imported_resume_after: dict[str, int] = {}
        #: Local epoch at which a cancelled event's delayed default-control
        #: fallback is due, by mRID, so a snapshot can carry it.
        self._fallback_due: dict[bytes, int] = {}
        #: Event work in progress: process_controls, activation and completion
        #: callbacks, take-over resumption and loss-of-communications entry.
        #: release_devices waits until none is running.
        self._busy = 0
        self._idle = asyncio.Event()
        self._idle.set()
        #: Each event's control as XML, with the object it was serialized from.
        self._control_xml: dict[bytes, tuple[Dercontrol1, str]] = {}
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
        with self._working():
            try:
                await self._process_controls(program_href)
            finally:
                self._persist()

    @contextlib.contextmanager
    def _working(self) -> Iterator[None]:
        """Count one piece of event work as running, for :meth:`release_devices`."""
        self._busy += 1
        self._idle.clear()
        try:
            yield
        finally:
            self._busy -= 1
            if self._busy == 0:
                self._idle.set()

    async def _process_controls(self, program_href: str) -> None:
        """Process the DERControls of one DERProgram; see :meth:`process_controls`."""
        if self._closed:
            return

        derp_state = self._state.der_programs.get(program_href)
        if derp_state is None:
            return
        self._restore_pending_ledgers()

        # Relay the program's DefaultDERControl baseline (add/change only).
        self._relay_default_baseline(program_href, derp_state)

        # Server timebase (FSA-scoped): classification against server-epoch
        # eff_start/eff_end must follow the head-end's clock.
        now = int(self._timebase.now(self._fsa_scope(program_href)))
        self._prune_imported_boundaries(now)
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
                    self._add_missing_windows(existing, now)
                continue

            # Each device draws its own randomization (IEEE 2030.5-2023
            # §10.2.3.2.2, §10.2.3.2.3). The event spans every device's window.
            devices = self._unique_devices(program_href)
            windows = self._draw_windows(derc, program_href, devices, now, late=True)
            eff_start = min(start for start, _ in windows.values())
            eff_end = max(end for _, end in windows.values())
            eff_duration = eff_end - eff_start

            # Comms-loss recovery: skip events at or before the resume boundary
            # (the window we opted out of during the outage). Only later
            # schedules resume. The boundary outlives the mode: skipped events
            # are not stored, so it must gate every poll until the window has
            # elapsed -- the client clears it only once it is in the past.
            if (
                self._comms_loss.resume_after_epoch is not None
                and eff_start <= self._comms_loss.resume_after_epoch
            ):
                self._audit_unstored(
                    derc, program_href, primacy, eff_start, eff_duration, "skipped"
                )
                logger.debug(
                    "Skipping event %s in opted-out window (start=%d <= resume_after=%d)",
                    mrid_short,
                    eff_start,
                    self._comms_loss.resume_after_epoch,
                )
                continue
            if self._gated_by_imported_boundary(program_href, eff_start):
                self._audit_unstored(
                    derc, program_href, primacy, eff_start, eff_duration, "skipped"
                )
                logger.debug(
                    "Skipping event %s: in the opted-out window of the devices taken over",
                    mrid_short,
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
                self._audit_unstored(
                    derc, program_href, primacy, eff_start, eff_duration, "expired"
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
                device_windows={d: windows[d] for d in devices},
            )

            # Check server cancellation before adding
            if self._is_server_cancelled(derc):
                record.state = EventState.CANCELLED
                self._store.upsert(record)
                logger.info("Event %s pre-cancelled by server", mrid_short)
                self._audit_transition(record, None, "cancelled")
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

        # IEEE 2030.5-2023 §10.2.2.3 rule p): clients SHALL consider events
        # removed from the server before the end of their Effective Scheduled
        # Period as cancelled. The 2023 edition notes this differs from earlier
        # revisions, where currentStatus=2 was the only cancellation signal.
        #
        # Gated on der_controls_complete: a failed or unparseable DERControl
        # fetch leaves the list empty, and that is indistinguishable from a
        # server that removed every control. Reconciling against an incomplete
        # snapshot would cancel every live event on one transient error.
        if derp_state.der_controls_complete:
            served = {derc.m_rid.value for derc in derp_state.der_controls}
            # "before the end of their Effective Scheduled Period" is part of
            # the rule, not a detail: a server may drop an event once it is
            # over, and such an event completed rather than being cancelled.
            # The record can still be ACTIVE here because _on_completion blocks
            # on _state_ready for the length of a rediscovery, and a rediscovery
            # ends by refreshing the list and calling this method -- cancelling
            # would report status 6 for an event that ran its course and
            # suppress the status 3 the blocked completion still owes.
            removed = [
                record
                for record in self._store.all_active_states()
                if record.program_href == program_href
                and record.mrid not in served
                and now < record.end
            ]
            if removed and derp_state.der_controls_pages > 1:
                # A server that drops or adds a control between two page reads
                # shifts the later pages, and the read can miss a control the
                # server still serves. Cancellation is final, so the list is
                # read again now and only a control missing from both reads is
                # cancelled. A failed second read cancels nothing this pass.
                reread = await read_der_control_mrids(self._http, derp_state)
                if self._closed:
                    return
                removed = [] if reread is None else [r for r in removed if r.mrid not in reread]
            for record in removed:
                # Another task can end the event during an await in this loop.
                if record.state not in (EventState.SCHEDULED, EventState.ACTIVE):
                    continue
                logger.info(
                    "Event %s removed from program %s while %s -- cancelling (rule p)",
                    record.mrid.hex()[:8],
                    program_href,
                    record.state.value,
                )
                await self._handle_cancellation(record)
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
            self._fallback_due.pop(expired_mrid, None)
        for ended, device in self._rand_cache.prune(now):
            self._control_xml.pop(ended, None)
            if self._state_store is not None:
                try:
                    self._state_store.forget_offsets(ended.hex(), device)
                except Exception:
                    logger.warning("Could not forget the offsets of %s", ended.hex()[:8])
        self._forget_unstored_no_longer_served(program_href)
        self._response_tracker.prune(now)

        # Apply DDERC to devices that have no active events.
        # Per IEEE 2030.5 §10.10, the DefaultDERControl is the baseline
        # operating state and should be active whenever no event overrides it.
        await self._apply_initial_dderc(program_href)

    def _forget_unstored_no_longer_served(self, program_href: str) -> None:
        """Forget expired and skipped events the program no longer serves.

        Only on a complete fetch: an empty list from a failed one would forget
        every event, and the next poll would record them all again.
        """
        derp_state = self._state.der_programs.get(program_href)
        if derp_state is None or not derp_state.der_controls_complete:
            return
        served = {derc.m_rid.value for derc in derp_state.der_controls}
        for mrid, program in list(self._audit_unstored_seen.items()):
            if program == program_href and mrid not in served:
                del self._audit_unstored_seen[mrid]

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
                from_state = record.state.value
                self._store.transition(record.mrid, EventState.CANCELLED)
                self._audit_transition(record, from_state, "cancelled")
                self._timer_mgr.cancel(record.mrid)
                # cancel_program is sync, but runs inside the event loop, so
                # _emit_relay's loop.create_task succeeds.
                self._relay_control_event(record, "cancelled")
                cancelled_count += 1
        # The program is gone -- drop its baseline relay slot so the cache stays
        # bounded by live program count.
        self._relay_snapshots.pop(("default_baseline", program_href), None)
        for mrid, program in list(self._audit_unstored_seen.items()):
            if program == program_href:
                del self._audit_unstored_seen[mrid]
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
        self._schedule_activations(record)
        self._relay_control_event(record, "scheduled")
        self._audit_transition(record, None, "scheduled")

    def _draw_windows(
        self,
        derc: Dercontrol1,
        program_href: str,
        devices: Sequence[str],
        now: int,
        *,
        late: bool,
    ) -> dict[str, tuple[int, int]]:
        """Return each device's effective ``(start, end)`` for a DERControl.

        Each device draws its own start and duration offsets (IEEE 2030.5-2023
        §10.2.3.2.2, §10.2.3.2.3), keyed by event and device so the device
        keeps them across polls and, through a shared store, across
        processes. ``devices`` empty gives one window under the key
        ``NO_DEVICE``. Under a group lookup every device shares one draw: the
        lookup dispatches to the group's local devices together.

        §10.2.2.3 rule m.1: a successive event starts on each device at the
        predecessor's effective end on that device. Rule k, applied when
        ``late`` (an event first received here): a device whose effective
        start has passed while the specified end is still ahead starts at the
        current time plus the absolute value of its start offset, and keeps
        the specified end plus its duration offset.
        """
        mrid = derc.m_rid.value
        raw_start = derc.interval.start.value
        raw_duration = derc.interval.duration
        raw_end = raw_start + raw_duration
        predecessor = self._find_successive_predecessor(program_href, raw_start)
        shared = self._group_local_lfdis(program_href) is not None
        keys = [NO_DEVICE] if shared or not devices else list(devices)
        randomized_start = derc.randomize_start is not None and derc.randomize_start.value != 0
        drawn: dict[str, tuple[int, int]] = {}
        for key in keys:
            successive = predecessor.window(key)[1] - raw_start if predecessor is not None else None
            start_off, dur_off = self._rand_cache.get_offsets(
                derc, raw_start, raw_duration, device=key, start_offset=successive
            )
            if successive is not None and successive != start_off:
                logger.debug(
                    "Successive event %s on %s: adjusting start offset %d -> %d",
                    mrid.hex()[:8],
                    key or "its program",
                    start_off,
                    successive,
                )
                start_off = successive
                self._rand_cache.override_start_offset(
                    mrid, start_off, raw_start, raw_duration, device=key
                )
                self._share_adjusted_offsets(mrid, key, (start_off, dur_off))
            start = raw_start + start_off
            end = start + raw_duration + dur_off
            if late and predecessor is None and randomized_start and start < now < raw_end:
                end = raw_end + dur_off
                start = min(now + abs(start_off), end)
            drawn[key] = (start, end)
        if shared or not devices:
            return dict.fromkeys(devices, drawn[NO_DEVICE]) or drawn
        return drawn

    def _add_missing_windows(self, record: EventRecord, now: int) -> None:
        """Draw windows for devices mapped to a running or scheduled event after it arrived.

        A device whose window starts later gets its own activation timer. One
        whose window has already started is not dispatched here: a device
        that joins a running event is applied by :meth:`resume_devices`.
        """
        if record.state not in (EventState.SCHEDULED, EventState.ACTIVE):
            return
        missing = [
            d for d in self._unique_devices(record.program_href) if d not in record.device_windows
        ]
        if not missing:
            return
        windows = self._draw_windows(record.derc, record.program_href, missing, now, late=False)
        for device, (start, end) in windows.items():
            record.set_window(device, start, end)
        if record.opted_out:
            return
        later = [d for d in missing if windows[d][0] > now and d not in self._awaiting_resume]
        if later:
            self._schedule_activations(record, later)

    def _schedule_activations(
        self, record: EventRecord, devices: Iterable[str] | None = None
    ) -> None:
        """Schedule activation at each device's own window start.

        Devices that share a start share one timer. An event without device
        windows gets one timer at its start. ``devices`` None schedules every
        device not started yet.
        """
        now_fn = self._now_fn_for(record.program_href)
        if not record.device_windows:
            self._timer_mgr.schedule_activation(record, self._activate, now_fn=now_fn)
            return
        pending = (
            set(devices)
            if devices is not None
            else set(record.device_windows) - record.started_devices - record.ended_devices
        )
        groups: dict[int, set[str]] = {}
        for device in pending:
            if device in record.device_windows:
                groups.setdefault(record.device_windows[device][0], set()).add(device)
        for start, group in sorted(groups.items()):
            self._timer_mgr.schedule_at(
                record,
                start,
                partial(self._activate, devices=frozenset(group)),
                "activation",
                now_fn=now_fn,
            )

    def _schedule_completions(self, record: EventRecord, devices: set[str] | None) -> None:
        """Schedule completion at each started device's own window end.

        Devices that share an end share one timer. ``devices`` None, or an
        event without device windows, gets one timer at the event's end.
        """
        now_fn = self._now_fn_for(record.program_href)
        if devices is None or not record.device_windows:
            self._timer_mgr.schedule_completion(record, self._complete, now_fn=now_fn)
            return
        groups: dict[int, set[str]] = {}
        for device in devices:
            if device in record.device_windows and device not in record.ended_devices:
                groups.setdefault(record.device_windows[device][1], set()).add(device)
        for end, group in sorted(groups.items()):
            self._timer_mgr.schedule_at(
                record,
                end,
                partial(self._complete, devices=frozenset(group)),
                "completion",
                now_fn=now_fn,
            )

    def _due_devices(
        self, record: EventRecord, devices: Iterable[str] | None, now: int, *, first: bool
    ) -> set[str] | None:
        """Return the devices an activation starts the event on, or None for every device.

        None only for an event without device windows. ``devices`` None takes
        every device. The first activation also takes the program's devices
        that have no window yet, as the event's start applied to them before
        windows were drawn per device.
        """
        if not record.device_windows:
            return None
        due = set(record.device_windows) if devices is None else set(devices)
        if first:
            due |= {
                d
                for d in self._unique_devices(record.program_href)
                if d not in record.device_windows
            }
        return {
            d
            for d in due
            if d not in record.started_devices
            and d not in record.ended_devices
            and d not in self._released
        }

    async def _handle_late_discovery(self, record: EventRecord, derp_state: object) -> None:
        """Handle an event discovered after its start time (already active).

        Mirrors the ``_on_activation()`` flow: run supersession BEFORE posting
        ACTIVE so that SUPERSEDED responses go out first when multiple
        overlapping events are discovered late in the same poll cycle.
        """
        mrid_short = record.mrid.hex()[:8]
        if self._comms_loss.active:
            logger.info("Event %s: comms-loss active -- opting out (late discovery)", mrid_short)
            await self._opt_out_event(record, from_state=None)
            return
        # A device whose own window has already ended (its duration offset
        # ended it early) received the event after it expired there: it is
        # ignored on that device and answered EXPIRED (IEEE 2030.5-2023
        # §10.2.2.3 rule j), and the others go on.
        now = self._event_now(record)
        expired = {d for d, (_, end) in record.device_windows.items() if end <= now}
        record.ended_devices |= expired
        logger.info("Event %s: late discovery (already active), posting ACK", mrid_short)
        await self._post_response(
            record.derc,
            ResponseCode.ACKNOWLEDGED,
            record.program_href,
            exclude_devices=expired or None,
        )
        if expired:
            await self._post_response_for_devices(record.derc, ResponseCode.EXPIRED, expired)
        if self._closed:
            return

        # Devices whose own window has started run the event now; the others
        # start at their own start (IEEE 2030.5-2023 §10.2.3.2.2).
        started = [d for d, (start, end) in record.device_windows.items() if start <= now < end]
        due = self._due_devices(record, started, now, first=True)
        if due is not None:
            record.started_devices |= due

        # IEEE 10.2.2.3 rule l): SUPERSEDED before ACTIVE.
        await self._run_supersession()
        if self._closed:
            return

        if due is None:
            await self._apply_and_respond(record)
        elif due:
            await self._apply_and_respond(record, only=due)
        if self._closed:
            return
        self._relay_control_event(record, "active")
        self._audit_activation(record, from_state=None)

        self._schedule_completions(record, due)
        if record.device_windows:
            self._schedule_activations(record)

    async def _handle_cancellation(self, record: EventRecord) -> None:
        """Handle server cancellation of an event.

        IEEE 10.2.3.3: Cancellation of an active event applies randomization
        to the abbreviated duration. The wind-down period is
        max(abs(randomizeStart), abs(randomizeDuration)) seconds.
        """
        mrid_short = record.mrid.hex()[:8]
        was_active = record.state == EventState.ACTIVE
        logger.info("Event %s: cancelled (was %s)", mrid_short, record.state.value)
        from_state = record.state.value
        self._store.transition(record.mrid, EventState.CANCELLED)
        self._timer_mgr.cancel(record.mrid)
        self._relay_control_event(record, "cancelled")
        self._audit_transition(record, from_state, "cancelled")
        # A cancellation tells a device the event it was running has been called
        # off. A device that was told the event was rejected was never running it,
        # so it is not told again. An event cancelled while still SCHEDULED
        # never dispatched, so every device in the program still hears about it.
        # A device whose own window already ended was told COMPLETED instead.
        ended = set(record.ended_devices)
        await self._post_started_device_lifecycle(
            record, ResponseCode.CANCELLED, mrid_short, exclude=ended
        )
        if record.applied_devices or record.rejected_devices:
            # Devices still waiting for their own start acknowledged the event
            # and hear that it is cancelled.
            waiting = {
                d
                for d in record.device_windows
                if d not in record.started_devices and d not in ended
            } - record.superseded_devices
            if waiting:
                await self._post_response_for_devices(record.derc, ResponseCode.CANCELLED, waiting)

        if was_active:
            wind_down = self._cancellation_wind_down(record)
            if wind_down > 0:
                logger.info(
                    "Event %s: wind-down %ds before DDERC fallback",
                    mrid_short,
                    wind_down,
                )
                self._fallback_due[record.mrid] = int(time.time()) + wind_down
                self._timer_mgr.schedule_delayed_callback(
                    record, wind_down, self._wind_down, "wind-down"
                )
            else:
                await self._cancel_fallback(record)

    async def _wind_down(self, record: EventRecord) -> None:
        """Wind-down timer callback: fall back after a cancellation, then save the shared state.

        Counted as event work, so :meth:`release_devices` waits for its
        default-control writes before it saves and hands the devices over.
        """
        with self._working():
            # No longer due once it runs: a snapshot saved from here on must not
            # have the next process run the fallback again.
            self._fallback_due.pop(record.mrid, None)
            try:
                await self._cancel_fallback(record)
            finally:
                self._persist()

    async def _cancel_fallback(self, record: EventRecord) -> None:
        """Fall back to the default after a cancellation, on the devices still running it.

        Devices whose own window had not started, or had already ended (and
        fell back then), are left as they are.
        """
        waiting = {d for d in record.device_windows if d not in record.started_devices}
        await self._apply_dderc_fallback(record, exclude=(record.ended_devices | waiting) or None)

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

    async def _activate(self, record: EventRecord, devices: frozenset[str] | None = None) -> None:
        """Activation timer callback: activate the event, then save the shared state."""
        with self._working():
            try:
                await self._on_activation(record, devices)
            finally:
                self._persist()

    async def _complete(self, record: EventRecord, devices: frozenset[str] | None = None) -> None:
        """Completion timer callback: complete the event, then save the shared state."""
        with self._working():
            try:
                await self._on_completion(record, devices)
            finally:
                self._persist()

    async def _on_activation(
        self, record: EventRecord, devices: Iterable[str] | None = None
    ) -> None:
        """Activation timer callback: start the event on ``devices``.

        Each device starts at its own window start (IEEE 2030.5-2023
        §10.2.3.2.2), so one event can have several activations. The first
        transitions the event from SCHEDULED to ACTIVE; a later one starts it
        on the devices named. ``devices`` None starts it on every device whose
        start has come, and acts only on a SCHEDULED event.

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
        first = record.state == EventState.SCHEDULED
        if not first and (devices is None or record.state != EventState.ACTIVE or record.opted_out):
            logger.debug("Event %s: activation skipped (state=%s)", mrid_short, record.state.value)
            return
        if first:
            delta = now - record.start
            logger.info(
                "Event %s: SCHEDULED -> ACTIVE (fired %.2fs %s intended start)",
                mrid_short,
                abs(delta),
                "after" if delta >= 0 else "before",
            )
            self._store.transition(record.mrid, EventState.ACTIVE)

            if self._comms_loss.active:
                logger.info(
                    "Event %s: comms-loss active -- opting out instead of applying", mrid_short
                )
                await self._opt_out_event(record, from_state=EventState.SCHEDULED.value)
                return

        due = self._due_devices(record, devices, int(now), first=first)
        if due is not None:
            if not due and not first:
                return
            record.started_devices |= due

        # IEEE 10.2.2.3 rule l): SUPERSEDED SHALL be POSTed at the Effective
        # Start Time of the superseding event.  Run supersession BEFORE
        # dispatching so the SUPERSEDED responses go out ahead of any ACTIVE.
        # Supersession stands whether or not this event's dispatch then
        # succeeds.
        await self._run_supersession()
        if self._closed:
            return

        if due is None:
            await self._apply_and_respond(record)
        elif due:
            await self._apply_and_respond(record, only=due)
        if self._closed:
            return
        if not first:
            self._schedule_completions(record, due)
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
        self._audit_activation(record, from_state=EventState.SCHEDULED.value)
        self._schedule_completions(record, due)

    async def _on_completion(
        self, record: EventRecord, devices: Iterable[str] | None = None
    ) -> None:
        """Completion timer callback: end the event on ``devices``.

        Each device ends at its own window end (IEEE 2030.5-2023
        §10.2.3.2.3). Ending it on some devices posts their COMPLETED and
        falls them back to the default; the event goes on for the others. The
        last devices to end transition it from ACTIVE to COMPLETED. ``devices``
        None ends it on every device that has not ended.

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
        earlier = set(record.ended_devices)
        if devices is not None and record.device_windows:
            group = set(devices) - earlier - self._released
            record.ended_devices |= group
            if not set(record.device_windows) <= record.ended_devices:
                logger.info("Event %s: ended on %d device(s)", mrid_short, len(group))
                audience = (record.applied_devices - record.superseded_devices) & group
                if audience:
                    await self._post_response_for_devices(
                        record.derc, ResponseCode.COMPLETED, audience
                    )
                await self._apply_dderc_fallback(record, only=group)
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
        self._audit_transition(record, EventState.ACTIVE.value, "completed")
        await self._post_started_device_lifecycle(
            record, ResponseCode.COMPLETED, mrid_short, exclude=earlier
        )
        await self._apply_dderc_fallback(record, exclude=earlier or None)

    async def _post_started_device_lifecycle(
        self,
        record: EventRecord,
        code: ResponseCode,
        mrid_short: str,
        *,
        exclude: set[str] | None = None,
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
        there is nothing to have rejected, so there is no exclusion to honor.

        ``exclude`` names devices already told how the event ended on them,
        whose own window ended earlier.
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

        audience = record.applied_devices - record.superseded_devices - (exclude or set())
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

            # Defer, per device, when the superseding event hasn't started on
            # the device yet and the superseded event is running there. It
            # runs normally until the higher-priority event starts on that
            # device, whose start can differ from the other devices' starts.
            affected = frozenset(
                d
                for d in result.affected_devices
                if not (self._started_on(superseded, d) and not self._started_on(superseding, d))
            )
            if not affected:
                continue

            # Post per-device SUPERSEDED responses for newly affected devices
            newly_superseded = affected - superseded.superseded_devices
            superseded.superseded_devices.update(affected)

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
                for dev in affected:
                    existing = superseded.superseded_modes.get(dev, frozenset())
                    superseded.superseded_modes[dev] = existing | result.affected_modes
                    # The superseding event owns these modes now. Leaving them
                    # on the superseded event's registration would have the
                    # tracker read them as still covered when the superseding
                    # event ends, so nothing would release them until the
                    # superseded event ended too, or ever, when another event
                    # is running on the device then.
                    self._mode_tracker.release(dev, superseded.mrid, result.affected_modes)

            # If all devices for this program are superseded, fully supersede
            program_devices = device_map.get(superseded.program_href, set())
            fully = bool(program_devices) and superseded.superseded_devices >= program_devices
            if newly_superseded and not fully:
                # Not a transition: the event keeps running on its other
                # devices, so the record leaves its state where it was and
                # says which part was taken over.
                current = "opted_out" if superseded.opted_out else superseded.state.value
                self._audit_transition(
                    superseded,
                    current,
                    current,
                    partial(self._audit_supersession, superseded, result.superseding_mrid, True),
                )
            if fully:
                was_active = superseded.state == EventState.ACTIVE
                logger.info(
                    "Event %s: SUPERSEDED by %s (was %s)",
                    superseded.mrid.hex()[:8],
                    result.superseding_mrid.hex()[:8],
                    superseded.state.value,
                )
                from_state = superseded.state.value
                self._store.transition(superseded.mrid, EventState.SUPERSEDED)
                self._timer_mgr.cancel(superseded.mrid)
                self._relay_control_event(superseded, "superseded")
                self._audit_transition(
                    superseded,
                    from_state,
                    "superseded",
                    partial(self._audit_supersession, superseded, result.superseding_mrid, False),
                )

                # Per-device SUPERSEDED responses were already posted above
                # as each device was added to newly_superseded.
                if was_active:
                    await self._apply_dderc_fallback(superseded)

    @staticmethod
    def _started_on(record: EventRecord, device_href: str) -> bool:
        """Return whether ``record`` is running on ``device_href``."""
        return record.state == EventState.ACTIVE and record.runs_on(device_href)

    def _build_program_device_map(self) -> dict[str, set[str]]:
        """Build mapping from program_href to set of device hrefs."""
        result: dict[str, set[str]] = {}
        for href, devices in self._state.device_mapping.program_to_devices.items():
            result[href] = set(devices)
        return result

    async def _apply_and_respond(self, record: EventRecord, only: set[str] | None = None) -> None:
        """Dispatch the control, then report each device by its own outcome.

        Status 2 asserts the event started, so it is posted for a device only
        once that device's apply has returned successfully. A failure reports
        the matching rejection code instead and marks the device, which
        keeps it out of the COMPLETED response at the event's end.

        ``only`` limits the dispatch to those device hrefs, for devices taken
        over from another process while the event was running.
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

        outcomes = await self._apply_control_to_devices(record, on_result=respond, only=only)
        if outcomes or only is not None:
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
        only: set[str] | None = None,
    ) -> dict[str, BaseException | None]:
        """Apply a DERControl to all devices in the program's group (parallel).

        Returns the per-device outcome keyed by device href: ``None`` where the
        control was applied, otherwise the failure. ``on_result`` is awaited for
        each device as soon as *that* device's dispatch settles, so a caller can
        post its response without waiting on the rest of the fleet.
        """
        devices = self._unique_devices(record.program_href)
        derp_state = self._state.der_programs.get(record.program_href)
        curves = derp_state.der_curves if derp_state else []
        mrid_short = record.mrid.hex()[:8]
        targets = [
            d for d in devices if d not in record.superseded_devices and (only is None or d in only)
        ]
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
                mrid_short,
                dev_href,
                self._dispatcher.apply_control(dev_href, record.derc, curves),
                record=record,
            )
            if on_result is not None:
                await on_result(dev_href, failure)
            return dev_href, failure

        pairs = await asyncio.gather(*[dispatch_and_report(d) for d in targets])
        return dict(pairs)

    def _log_late_dispatch(
        self,
        mrid_short: str,
        label: str,
        task: asyncio.Task[None],
        record: EventRecord | None = None,
    ) -> None:
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
        if record is not None and self._audit.enabled:
            # The server was told this device rejected the event; this is the
            # record of whether the apply then reached it.
            try:
                self._audit.late_dispatch(
                    event_mrid=record.mrid,
                    lfdi=self._lfdi_or_href(label),
                    applied=exc is None,
                    at=self._timebase.now(self._fsa_scope(record.program_href)),
                    error=None if exc is None else str(exc),
                    error_type=None if exc is None else type(exc).__name__,
                )
            except Exception:
                logger.debug("Audit record for a late dispatch failed", exc_info=True)
        if exc is not None:
            logger.warning("Event %s: late dispatch to %s failed: %s", mrid_short, label, exc)
        else:
            logger.info(
                "Event %s: late dispatch to %s applied after its rejection was sent",
                mrid_short,
                label,
            )

    async def _dispatch_one(
        self,
        mrid_short: str,
        label: str,
        coro: Coroutine[Any, Any, None],
        *,
        record: EventRecord | None = None,
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
                lambda finished: self._log_late_dispatch(mrid_short, label, finished, record)
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
                    record=record,
                )
                for lfdi in local_lfdis
            ]
        )
        for lfdi, failure in zip(local_lfdis, failures, strict=True):
            (record.applied_local if failure is None else record.rejected_local).add(lfdi)
        failed = [f for f in failures if f is not None]
        aggregate: BaseException | None = (
            failed[0] if failed and len(failed) == len(failures) else None
        )
        if not local_lfdis:
            # Every device in the group is a meter, so none can take the control.
            aggregate = DeviceNotConfiguredError(
                "no device in the group takes controls; every device is a meter"
            )
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

        A device the dispatcher says takes no controls, a meter, is left out of
        the group, so a site-wide control is not reported rejected because of
        the meters measuring it. A group of meters only is returned empty.
        """
        if self._group_lookup is None:
            return None
        local_lfdis = self._group_lookup(program_href)
        if not local_lfdis:
            return None
        # The ControlDispatcher protocol does not require ``takes_controls``, and
        # a dispatcher without it has every device take controls. Looked up on
        # the class, so an attribute an object creates on access is not taken
        # for the method.
        if getattr(type(self._dispatcher), "takes_controls", None) is None:
            return local_lfdis
        dispatcher: Any = self._dispatcher
        return [lfdi for lfdi in local_lfdis if dispatcher.takes_controls(lfdi)]

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

    def _audit_activation(self, record: EventRecord, *, from_state: str | None) -> None:
        """Record an event going active, once its dispatch has settled.

        Emitted after the per-device outcomes are in, not at the state change,
        so the applied and rejected lists are final. They are the outcome each
        device's dispatch returned, which is what the server was told. Under a
        group lookup they list the local devices one by one, so a partial
        failure the server's single response hides is visible here.
        """

        def outcomes() -> dict[str, Any]:
            if record.applied_local or record.rejected_local:
                return {
                    "applied_lfdis": list(record.applied_local),
                    "rejected_lfdis": list(record.rejected_local),
                }
            return {
                "applied_lfdis": self._lfdis_for_hrefs(record.applied_devices),
                "rejected_lfdis": self._lfdis_for_hrefs(record.rejected_devices),
            }

        self._audit_transition(record, from_state, EventState.ACTIVE.value, outcomes)

    def _audit_supersession(
        self, record: EventRecord, superseded_by: bytes, part: bool
    ) -> dict[str, Any]:
        """A ``superseded`` record's details; for a partial one, what was taken over.

        In the same terms as the rest of the trail: under a group lookup the
        server's EndDevice is the program's local devices, so each of them is
        listed with the modes taken over on the EndDevice.
        """
        if not part:
            return {"superseded_by": superseded_by}
        local = self._group_local_lfdis(record.program_href)
        if local is None:
            return {
                "superseded_by": superseded_by,
                "superseded_lfdis": self._lfdis_for_hrefs(record.superseded_devices),
                "superseded_modes": {
                    self._lfdi_or_href(dev): _wire_mode_names(modes)
                    for dev, modes in record.superseded_modes.items()
                },
            }
        modes: set[str] = set()
        for taken in record.superseded_modes.values():
            modes |= taken
        return {
            "superseded_by": superseded_by,
            "superseded_lfdis": list(local),
            "superseded_modes": (
                {lfdi: _wire_mode_names(modes) for lfdi in local} if modes else {}
            ),
        }

    def _audit_transition(
        self,
        record: EventRecord,
        from_state: str | None,
        to_state: str,
        details: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        """Hand one lifecycle transition to the audit trail.

        Never raises: the trail must not be able to stop an event from being
        processed, so a failure building the record is logged and dropped.
        ``details`` is a callable for the same reason -- what it computes
        resolves devices, and it runs here, inside the guard, rather than on
        the caller's control path.
        """
        if not self._audit.enabled:
            return
        if record.opted_out and to_state != "opted_out":
            # An opted-out event keeps its stored state, so supersession and
            # classification see it unchanged. Its trail has left that state.
            from_state = "opted_out"
        try:
            # The control itself rides on an event's first record, for the
            # local history only: the history has to describe a control after
            # the server stops serving it.
            control = (
                self._control_summary(record)
                if from_state is None and self._audit.keeps_history
                else None
            )
            self._audit.event_transition(
                event_mrid=record.mrid,
                program_href=record.program_href,
                from_state=from_state,
                to_state=to_state,
                effective_start=record.start,
                effective_duration=record.duration,
                primacy=record.primacy,
                lfdis=self._affected_lfdis_for_program(record.program_href),
                at=self._timebase.now(self._fsa_scope(record.program_href)),
                control=control,
                **(details() if details is not None else {}),
            )
        except Exception:
            self._audit_failures += 1
            log = logger.warning if self._audit_failures == 1 else logger.debug
            log(
                "Audit record for event %s failed; further failures log at debug",
                record.mrid.hex()[:8],
                exc_info=True,
            )

    def attach_event_history(self, history: EventHistory | None) -> None:
        """Keep each response this processor posts, or fails to post, in ``history``."""
        self._response_tracker.observer = (
            None if history is None else partial(_record_response, history)
        )

    def _control_summary(self, record: EventRecord) -> dict[str, Any]:
        """The control's description, settings and timing, and its program's name."""
        program = self._state.der_programs.get(record.program_href)
        return {
            **serialize_der_control(record.derc),
            "description": record.derc.description,
            "program_description": program.program.description if program else None,
        }

    def _audit_unstored(
        self,
        derc: Dercontrol1,
        program_href: str,
        primacy: int,
        start: int,
        duration: int,
        to_state: str,
    ) -> None:
        """Record an event the store never holds: expired on arrival, or skipped
        in an opted-out window.

        Once per event. Neither is stored, so the next poll meets it again, and
        a trail that recorded it every poll would contradict the rule that a
        re-poll of an unchanged event records nothing.
        """
        if not self._audit.enabled:
            return
        mrid = derc.m_rid.value
        if mrid in self._audit_unstored_seen:
            return
        self._audit_unstored_seen[mrid] = program_href
        transient = EventRecord(
            mrid=mrid,
            derc=derc,
            program_href=program_href,
            primacy=primacy,
            state=EventState.CANCELLED,
            start=start,
            duration=duration,
            server_status_time=0,
        )
        self._audit_transition(transient, None, to_state)

    def _lfdi_or_href(self, dev_href: str) -> str:
        """A device as its hex LFDI, or its href when the LFDI is not known.

        The href is kept rather than dropping the device: an audit record that
        silently loses a device reads as the device never having been involved.
        """
        lfdi = self._get_device_lfdi(dev_href)
        return lfdi.hex() if lfdi is not None else dev_href

    def _lfdis_for_hrefs(self, hrefs: set[str]) -> list[str]:
        return [self._lfdi_or_href(h) for h in hrefs]

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

    # ------------------------------------------------------------------
    # Shared event state: save, load, take over and release EndDevices
    # ------------------------------------------------------------------

    def _claim_offsets(self, mrid: bytes, device: str, offsets: tuple[int, int]) -> tuple[int, int]:
        """Return the shared offsets of ``mrid`` on ``device``, sharing ``offsets`` if none."""
        assert self._state_store is not None
        try:
            start, duration = self._state_store.claim_offsets(mrid.hex(), device, offsets)
        except Exception:
            logger.warning(
                "Could not share the randomization of %s; using this process's own",
                mrid.hex()[:8],
                exc_info=True,
            )
            return offsets
        return int(start), int(duration)

    def _record_sent(self, mrid: bytes, code: ResponseCode, lfdi: bytes, at: int) -> None:
        """Record one posted response in the shared event state."""
        assert self._state_store is not None
        self._state_store.record_response(lfdi.hex(), mrid.hex(), int(code), at)

    def _control_xml_of(self, record: EventRecord) -> str:
        """Return the record's DERControl as XML, serialized once per object."""
        from py20305.xml.serialization import to_xml

        cached = self._control_xml.get(record.mrid)
        if cached is not None and cached[0] is record.derc:
            return cached[1]
        xml = to_xml(record.derc).decode("utf-8")
        self._control_xml[record.mrid] = (record.derc, xml)
        return xml

    def _device_snapshot(self, dev_href: str, lfdi: bytes) -> dict[str, Any]:
        """Return the event state of one EndDevice in the shared snapshot format."""
        mapping = self._state.device_mapping.program_to_devices
        events: list[dict[str, Any]] = []
        for record in self._store.values():
            concerned = (
                dev_href in mapping.get(record.program_href, [])
                or dev_href in record.applied_devices
                or dev_href in record.rejected_devices
                or dev_href in record.superseded_devices
            )
            if not concerned:
                continue
            modes = record.superseded_modes.get(dev_href)
            events.append(
                {
                    "mrid": record.mrid.hex(),
                    "program_href": record.program_href,
                    "primacy": record.primacy,
                    "state": record.state.value,
                    "start": record.start,
                    "duration": record.duration,
                    "server_status_time": record.server_status_time,
                    "creation_time": record.creation_time,
                    "control": self._control_xml_of(record),
                    "window": list(record.window(dev_href)),
                    "started": dev_href in record.started_devices,
                    "ended": dev_href in record.ended_devices,
                    "applied": dev_href in record.applied_devices,
                    "rejected": dev_href in record.rejected_devices,
                    "superseded": dev_href in record.superseded_devices,
                    "superseded_modes": sorted(modes) if modes is not None else None,
                    "opted_out": record.opted_out,
                    "fallback_at": (
                        self._fallback_due.get(record.mrid)
                        if record.state == EventState.CANCELLED
                        and self._timer_mgr.has_pending(record.mrid)
                        else None
                    ),
                }
            )
        return {
            "version": SNAPSHOT_VERSION,
            "lfdi": lfdi.hex(),
            "events": events,
            "defaults": [
                {
                    "program_href": path,
                    "mrid": mrid.hex(),
                    "primacy": primacy,
                    "content": content.hex() if content is not None else None,
                }
                for path, mrid, primacy, content in self._dderc_tracker.entries_for(lfdi)
            ],
            "comms_loss": {
                "active": self._comms_loss.active,
                "resume_after": self._device_resume_after(dev_href),
            },
        }

    def _device_resume_after(self, dev_href: str) -> int | None:
        """Return the loss-of-communications resume boundary that applies to one device."""
        boundaries = [
            b
            for b in (
                self._comms_loss.resume_after_epoch,
                self._imported_resume_after.get(dev_href),
            )
            if b is not None
        ]
        return max(boundaries) if boundaries else None

    def _gated_by_imported_boundary(self, program_href: str, eff_start: int) -> bool:
        """Return whether a new event starts inside the opted-out window of its devices.

        True only when every device of the program was taken over with a
        resume boundary at or after ``eff_start``. A program that also serves
        devices without one runs the event on all of them.
        """
        if not self._imported_resume_after:
            return False
        devices = self._unique_devices(program_href)
        return bool(devices) and all(
            self._imported_resume_after.get(d, eff_start - 1) >= eff_start for d in devices
        )

    def _prune_imported_boundaries(self, now: int) -> None:
        """Drop imported resume boundaries that have passed."""
        for dev_href, boundary in list(self._imported_resume_after.items()):
            if now > boundary:
                del self._imported_resume_after[dev_href]

    def _restore_pending_ledgers(self) -> None:
        """Restore the Response ledger of taken-over devices discovery has found.

        Done before any control of theirs is processed, so a Response the
        previous process posted is not posted again, with or without a
        snapshot.
        """
        if not self._ledger_pending or self._state_store is None:
            return
        for dev_href in list(self._ledger_pending):
            edev = self._state.end_devices.get(dev_href)
            if edev is None:
                continue
            self._ledger_pending.discard(dev_href)
            self._restore_ledger(edev.lfdi)

    def _restore_ledger(self, lfdi: bytes) -> None:
        """Count the Responses recorded for ``lfdi`` in the shared state as sent."""
        assert self._state_store is not None
        for mrid_hex, status, at in self._state_store.responses(lfdi.hex()):
            self._response_tracker.restore(
                bytes.fromhex(mrid_hex), ResponseCode(status), lfdi, int(at)
            )

    def _share_adjusted_offsets(self, mrid: bytes, device: str, offsets: tuple[int, int]) -> None:
        """Replace a device's shared offsets with ones adjusted for a successive event."""
        if self._state_store is None:
            return
        try:
            self._state_store.forget_offsets(mrid.hex(), device)
            shared = self._state_store.claim_offsets(mrid.hex(), device, offsets)
        except Exception:
            logger.warning(
                "Could not share the adjusted timing of %s", mrid.hex()[:8], exc_info=True
            )
            return
        if tuple(shared) != offsets:
            logger.warning(
                "Event %s: another process stored a different timing while it was adjusted",
                mrid.hex()[:8],
            )

    def _persist(self, devices: Iterable[str] | None = None) -> set[str]:
        """Save the snapshot of each EndDevice this process acts for that changed.

        ``devices`` saves those devices, released ones included; None saves
        every device not released. A no-op without a shared store. A store
        that fails is logged and tried again at the next save; it never stops
        event processing. Returns the devices whose save failed.
        """
        failed: set[str] = set()
        if self._state_store is None:
            return failed
        if devices is None:
            hrefs = [h for h in self._state.end_devices if h not in self._released]
        else:
            hrefs = [h for h in devices if h in self._state.end_devices]
        for dev_href in hrefs:
            edev = self._state.end_devices[dev_href]
            try:
                snapshot = self._device_snapshot(dev_href, edev.lfdi)
                if self._saved.get(dev_href) == snapshot:
                    continue
                self._state_store.save_device(dev_href, snapshot)
            except Exception:
                logger.warning(
                    "Could not save the event state of %s; will retry", dev_href, exc_info=True
                )
                failed.add(dev_href)
                continue
            self._saved[dev_href] = snapshot
        return failed

    def load_devices(self, device_hrefs: Iterable[str]) -> int:
        """Load the saved event state of EndDevices taken over from another process.

        Merges each device's events into this processor: an event already held
        here gains the device's applied, rejected and superseded marks; an
        event not held here is created from the snapshot, with its saved
        timing, and its activation or completion timer is scheduled by
        :meth:`resume_devices`. Responses
        already posted for the devices are counted as sent, the DefaultDERControl
        last applied to each is remembered, and the loss-of-communications
        resume boundary is kept for those devices only. The Response ledger
        of a device without a snapshot is restored once discovery gives its
        LFDI. Returns how many snapshots were loaded.

        Call before the rediscovery that adds the devices, so the events are
        known when their controls are fetched, then :meth:`resume_devices`
        after it.
        """
        hrefs = list(device_hrefs)
        # This process acts for the devices again, with a store or without one.
        self._released.difference_update(hrefs)
        if self._state_store is None:
            return 0
        loaded = 0
        for dev_href in hrefs:
            self._saved.pop(dev_href, None)
            self._ledger_pending.add(dev_href)
            self._awaiting_resume.add(dev_href)
            snapshot = self._state_store.load_device(dev_href)
            if not snapshot or snapshot.get("version") != SNAPSHOT_VERSION:
                continue
            lfdi = bytes.fromhex(snapshot["lfdi"])
            for entry in snapshot.get("events", []):
                self._merge_event(dev_href, entry)
            for entry in snapshot.get("defaults", []):
                content = entry.get("content")
                self._dderc_tracker.record_application(
                    lfdi,
                    entry["program_href"],
                    bytes.fromhex(entry["mrid"]),
                    entry["primacy"],
                    content=bytes.fromhex(content) if content else None,
                )
            resume_after = snapshot.get("comms_loss", {}).get("resume_after")
            if resume_after is not None:
                current = self._imported_resume_after.get(dev_href, 0)
                self._imported_resume_after[dev_href] = max(current, int(resume_after))
            self._restore_ledger(lfdi)
            loaded += 1
        return loaded

    def _merge_event(self, dev_href: str, entry: dict[str, Any]) -> None:
        """Merge one EndDevice's view of an event into this processor's record of it."""
        from py20305.xml.serialization import from_xml

        mrid = bytes.fromhex(entry["mrid"])
        record = self._store.get(mrid)
        window = entry.get("window") or [entry["start"], entry["start"] + entry["duration"]]
        start, end = int(window[0]), int(window[1])
        if record is None:
            derc = from_xml(entry["control"], Dercontrol1)
            record = EventRecord(
                mrid=mrid,
                derc=derc,
                program_href=entry["program_href"],
                primacy=entry["primacy"],
                state=EventState(entry["state"]),
                start=start,
                duration=end - start,
                server_status_time=entry["server_status_time"],
                creation_time=entry["creation_time"],
                opted_out=entry["opted_out"],
            )
            self._store.upsert(record)
            fallback_at = entry.get("fallback_at")
            if record.state == EventState.CANCELLED and fallback_at is not None:
                # Keep the delayed default-control fallback, and the guard its
                # timer gives, for the time it still had to wait.
                self._fallback_due[mrid] = int(fallback_at)
                self._timer_mgr.schedule_delayed_callback(
                    record,
                    max(0, int(fallback_at) - int(time.time())),
                    self._wind_down,
                    "wind-down",
                )
        else:
            incoming = EventState(entry["state"])
            if (
                record.state in (EventState.SUPERSEDED, EventState.COMPLETED)
                and incoming in (EventState.SCHEDULED, EventState.ACTIVE)
                and not entry["superseded"]
                and not entry.get("ended")
            ):
                # Superseded or completed here only on this process's devices:
                # the incoming device's own window is still open, so the event
                # keeps its timers and its completion for that device. The local
                # devices stay superseded, or are marked ended, so they are not
                # told again how the event ended on them.
                if record.state == EventState.COMPLETED:
                    local = (
                        set(record.device_windows)
                        | record.applied_devices
                        | record.rejected_devices
                    )
                    record.started_devices |= local
                    record.ended_devices |= local
                record.state = incoming
            if entry["opted_out"] and not record.opted_out:
                # Opted out on the incoming device only. The event goes on for
                # this process's devices; the incoming device gets no control
                # and no further lifecycle Response for it.
                record.rejected_devices.add(dev_href)
        # The device keeps the window, and so the offsets, it drew.
        record.set_window(dev_href, start, end)
        raw_start = record.derc.interval.start.value
        raw_duration = record.derc.interval.duration
        self._rand_cache.seed(mrid, dev_href, (start - raw_start, end - start - raw_duration), end)
        if entry.get("started"):
            record.started_devices.add(dev_href)
        if entry.get("ended"):
            record.ended_devices.add(dev_href)
        if entry["applied"]:
            record.applied_devices.add(dev_href)
        if entry["rejected"]:
            record.rejected_devices.add(dev_href)
        if entry["superseded"]:
            record.superseded_devices.add(dev_href)
        if entry.get("superseded_modes") is not None:
            held = record.superseded_modes.get(dev_href, frozenset())
            record.superseded_modes[dev_href] = held | frozenset(entry["superseded_modes"])
        running = record.state == EventState.ACTIVE and not record.opted_out
        if running and entry["applied"] and not entry["opted_out"]:
            # Registered even when superseded on the device, as dispatch leaves
            # it: the tracker subtracts the modes other events cover.
            modes = get_active_mode_names(record.derc.dercontrol_base)
            self._mode_tracker.register(dev_href, mrid, modes)

    async def resume_devices(self, device_hrefs: Iterable[str]) -> None:
        """Apply running events to taken-over EndDevices that never received them.

        For a device the previous process stopped acting for in the middle of
        an activation, or one that joins a running event without a snapshot:
        the event is active, but the device was neither told it started nor
        told it was rejected. It gets the ACKNOWLEDGED Response unless one was
        posted for it, then the control and its ACTIVE Response. A device
        already marked applied or rejected gets nothing, so no control and no
        Response is sent twice. While in loss-of-communications mode the event
        is opted out instead, as on entering that mode.

        Not done for a program under a group lookup, whose dispatch goes to the
        group's local devices rather than to the EndDevices named here.

        Then schedules each taken-over device's own activation and completion
        at its window's start and end. They wait until now so an event does
        not activate before the devices it applies to are mapped. A device
        whose window has ended is not applied: on an active event it completes
        at once, with its Responses and fallback, and on a scheduled one it is
        left to expire.
        """
        with self._working():
            await self._resume_devices(set(device_hrefs))

    async def _resume_devices(self, taken: set[str]) -> None:
        """Resume taken-over devices; see :meth:`resume_devices`."""
        self._restore_pending_ledgers()
        self._awaiting_resume -= taken
        for record in self._store.values():
            if record.state not in (EventState.SCHEDULED, EventState.ACTIVE):
                continue
            if record.opted_out or self._closed:
                continue
            pending = self._time_taken_devices(record, taken)
            if not pending:
                continue
            if self._group_local_lfdis(record.program_href) is not None:
                logger.warning(
                    "Event %s: not applying to taken-over devices under a group lookup",
                    record.mrid.hex()[:8],
                )
                continue
            if self._comms_loss.active:
                await self._opt_out_event(record, from_state=EventState.ACTIVE.value)
                continue
            logger.info(
                "Event %s: applying to %d taken-over device(s)", record.mrid.hex()[:8], len(pending)
            )
            # A device that joins a running event is told it was received first,
            # as on late discovery. The tracker skips one already posted.
            await self._post_response_for_devices(record.derc, ResponseCode.ACKNOWLEDGED, pending)
            record.started_devices |= pending
            await self._apply_and_respond(record, only=pending)
            self._schedule_completions(record, pending)
        self._persist()

    def _time_taken_devices(self, record: EventRecord, taken: set[str]) -> set[str]:
        """Schedule taken-over devices' own timers. Return those to apply now.

        A device to apply now is in its window on a running event and was
        neither told the event started nor that it was rejected.
        """
        now = self._event_now(record)
        mapped = set(self._unique_devices(record.program_href))
        later: list[str] = []
        running: set[str] = set()
        apply_now: set[str] = set()
        for device in taken & (mapped | set(record.device_windows)):
            if device in record.ended_devices:
                continue
            start, end = record.window(device)
            # A device whose window started but that was neither applied nor
            # rejected was mid-activation when the previous process stopped.
            handled = device in (
                record.applied_devices | record.rejected_devices | record.superseded_devices
            )
            if handled:
                if record.state == EventState.ACTIVE:
                    record.started_devices.add(device)
                    running.add(device)
                continue
            if end <= now:
                if record.state == EventState.ACTIVE:
                    # Its window ended while no process acted for it: nothing is
                    # applied, and it completes at once.
                    running.add(device)
                else:
                    logger.info(
                        "Event %s: ended on %s while no process acted for it; not activated",
                        record.mrid.hex()[:8],
                        device,
                    )
                continue
            if start > now or record.state == EventState.SCHEDULED:
                later.append(device)
                continue
            apply_now.add(device)
        if later:
            self._schedule_activations(record, later)
        if running:
            self._schedule_completions(record, running)
        return apply_now

    def _event_now(self, record: EventRecord) -> int:
        """Return the server time on the clock of the event's program."""
        return int(self._timebase.now(self._fsa_scope(record.program_href)))

    async def release_devices(self, device_hrefs: Iterable[str]) -> None:
        """Stop acting for EndDevices another process will take over.

        New work leaves the devices out at once. Work already running for
        them (an activation, a control write, a Response) is allowed to finish
        first, so the snapshot saved next shows what reached the device and the
        server. Then their snapshots are saved, and their state forgotten: an
        event that concerns only released devices is forgotten here without a
        state change, a Response or a write to a device; its timers are
        cancelled and the taking-over process carries it on. An event that
        also concerns devices this process keeps loses only the released
        devices' marks. Call before the rediscovery that removes the devices,
        and not from a dispatcher or another event callback, which would wait
        for itself.

        Raises the store's error when a snapshot cannot be saved. The devices
        then stay with this process, their state kept, and the call can be
        made again.
        """
        released = set(device_hrefs)
        newly_released = released - self._released
        self._released |= released
        while True:
            if self._busy:
                await self._idle.wait()
                continue
            writes = [task for task in self._dispatch_tasks if not task.done()]
            if writes:
                await asyncio.gather(*writes, return_exceptions=True)
                continue
            break
        failed = self._persist(devices=released)
        if failed:
            self._released -= newly_released
            raise RuntimeError(
                f"could not save the event state of {', '.join(sorted(failed))}; "
                "the devices are still acted for here"
            )
        mapping = self._state.device_mapping.program_to_devices
        for dev_href in released:
            self._saved.pop(dev_href, None)
            self._ledger_pending.discard(dev_href)
            self._awaiting_resume.discard(dev_href)
            self._imported_resume_after.pop(dev_href, None)
            edev = self._state.end_devices.get(dev_href)
            if edev is not None:
                self._dderc_tracker.clear_devices({edev.lfdi})
        for record in self._store.values():
            devices = set(mapping.get(record.program_href, []))
            for dev_href in released:
                self._mode_tracker.unregister(dev_href, record.mrid)
            if devices and devices <= released:
                self._timer_mgr.cancel(record.mrid)
                self._store.remove(record.mrid)
                self._control_xml.pop(record.mrid, None)
                self._fallback_due.pop(record.mrid, None)
                continue
            record.applied_devices -= released
            record.rejected_devices -= released
            record.superseded_devices -= released
            record.started_devices -= released
            record.ended_devices -= released
            for dev_href in released:
                record.superseded_modes.pop(dev_href, None)
                record.device_windows.pop(dev_href, None)

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
        with self._working():
            await self._enter_comms_loss()

    async def _enter_comms_loss(self) -> None:
        """Opt out of every active event; see :meth:`enter_comms_loss`."""
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
            self._audit_transition(record, EventState.ACTIVE.value, "opted_out")
        for record in active:
            await self._apply_dderc_fallback(record, clear_if_no_dderc=True)
        self._persist()

    async def _opt_out_event(self, record: EventRecord, *, from_state: str | None) -> None:
        """Opt a single event out (comms-loss gate on activation/late discovery).

        Marks the event opted-out, raises the resume boundary to its end,
        cancels its timers, and reverts its devices to DDERC/clear. No response
        is posted.

        ``from_state`` is the state the audit record reports the event leaving:
        ``scheduled`` at activation, ``None`` for an event first seen already
        running.
        """
        record.opted_out = True
        self._audit_transition(record, from_state, "opted_out")
        boundary = self._comms_loss.resume_after_epoch or 0
        self._comms_loss.resume_after_epoch = max(boundary, record.end)
        self._timer_mgr.cancel(record.mrid)
        await self._apply_dderc_fallback(record, clear_if_no_dderc=True)

    async def _apply_dderc_fallback(
        self,
        record: EventRecord,
        *,
        clear_if_no_dderc: bool = False,
        only: set[str] | None = None,
        exclude: set[str] | None = None,
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

        ``only`` limits the fallback to those devices, whose own window ended;
        ``exclude`` leaves out devices that fell back already.
        """
        all_devices = [
            d
            for d in self._unique_devices(record.program_href)
            if (only is None or d in only) and (exclude is None or d not in exclude)
        ]
        # Asked in the vocabulary the supersession answered in. ``superseded_modes``
        # is recorded from the keys supersession compares, which leave out connect
        # and energize and do not reach extension elements, so measuring it against
        # every mode the event sets would read a device taken over whole as only
        # partly taken over -- for an event carrying nothing but CSIP-AUS limits,
        # always.
        owned = compared_mode_keys(record.derc.dercontrol_base)
        active_events = [r for r in self._store.by_state(EventState.ACTIVE) if not r.opted_out]
        # Devices with no DDERC anywhere: cleared to the connector safe-default
        # only in the comms-loss opt-out flow; the completion path leaves
        # them untouched as before.
        clear_targets: list[tuple[str, bytes]] = []

        # Unregister modes and compute per-device fallback needs
        needs_fallback: list[str] = []
        released_modes: dict[str, frozenset[str]] = {}
        for dev_href in all_devices:
            # Unregistered for every device, superseded ones included. Leaving
            # the registration would have the tracker believe this event still
            # owns those modes, so the event that took them over would release
            # nothing when it ends and they would stay on the device for good.
            released = self._mode_tracker.unregister(dev_href, record.mrid)
            released_modes[dev_href] = released
            # Dispatching is the separate question. A device another event took
            # over *entirely* is left to it: its activation is the write that
            # moves the device, and a fallback here would name this event as the
            # cause of a setpoint it no longer sets. Entirely, though -- a device
            # goes into ``superseded_devices`` when another event takes over any
            # one of this event's modes there, and the modes that event never
            # took are still this event's to release. Compared per mode.
            # ... and only while another event is actually running on the device.
            # Once the event that took it over has ended too, nothing is left to
            # bring the device back, so the last event to end releases it.
            if (
                dev_href in record.superseded_devices
                and record.superseded_modes.get(dev_href, frozenset()) >= owned
                and self._device_has_other_active(dev_href, record.mrid, active_events)
            ):
                continue
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
        # Each target: device href, LFDI, program, the default as sent, and the
        # content digest of the program's whole default.
        dderc_targets: list[tuple[str, bytes, DerProgramState, DefaultDercontrol, bytes]] = []
        # Modes the ended event set that the default does not carry. The
        # default only writes the modes it names, so these would stay in
        # effect on the device unless they are disabled here.
        withdraw_targets: list[tuple[str, frozenset[str]]] = []
        # Modes carried by the default of a device that falls back to one.
        default_modes: set[str] = set()
        for dev_href in needs_fallback:
            lfdi = self._get_device_lfdi(dev_href)
            if lfdi is None:
                continue
            best = self._best_dderc_program_for_device(dev_href)
            released = released_modes.get(dev_href, frozenset())
            if best is None:
                if clear_if_no_dderc:
                    clear_targets.append((dev_href, lfdi))
                elif released:
                    logger.debug(
                        "Event %s: no DDERC for device %s, disabling the modes it released",
                        record.mrid.hex()[:8],
                        dev_href,
                    )
                    withdraw_targets.append((dev_href, released))
                else:
                    logger.debug(
                        "Event %s: no DDERC for device %s, no revert action taken",
                        record.mrid.hex()[:8],
                        dev_href,
                    )
                continue
            dderc = best.default_dercontrol
            assert dderc is not None  # guaranteed by _best_dderc_program_for_device
            carried = get_active_mode_names(dderc.dercontrol_base)
            # A default is the baseline for the modes no event is controlling,
            # and this project does not let it override one. The default is
            # written whole, so where it names a mode an event that is still
            # running holds, applying it would take that mode from the event
            # that owns it. Withhold it there and disable what this event
            # released instead: the event still holding the device keeps what
            # is its, and its own end brings the default back for everything at
            # once. Recorded as a decision rather than quoted from the standard
            # -- see the note on `_modes_running_in`.
            # The default belongs to ``best.href``, which need not be the ending
            # event's program: under a group lookup it fans out to *that*
            # program's local devices, so that is what the write reaches.
            held = self._modes_running_in(self._reach_of(best.href, [dev_href]), active_events)
            taken = carried & held
            if taken:
                logger.debug(
                    "Event %s: sending the default for %s without %s, which "
                    "another running event is controlling",
                    record.mrid.hex()[:8],
                    dev_href,
                    sorted(taken),
                )
            # Taken here rather than in the dispatch loop below, which is still
            # before any await: a refresh during dispatch can replace the
            # program's default, and recording the replacement would have the
            # next pass skip a default the device never received.
            effective = self._default_without(dderc, taken) if taken else dderc
            written = carried - taken
            default_modes |= written
            uncovered = released - written
            if uncovered:
                withdraw_targets.append((dev_href, uncovered))
            # Nothing left to send only when the default had modes and every one
            # of them is being controlled. A default that names nothing still
            # goes out: it always did, and masking cannot have emptied it.
            emptied = bool(carried) and not written
            if not emptied and self._dderc_tracker.should_apply(lfdi, best.href, best.primacy):
                dderc_targets.append((dev_href, lfdi, best, effective, _default_content(dderc)))

        # Dispatch DDERC in parallel, then update tracker for successes.
        # With a group lookup supplied, each server-side EDev resolves
        # to the program's local sub-device LFDIs; dispatch by LFDI then.
        # Cache the lookup per target so a mid-await groups rebuild can't
        # change the fan-out width between the build and attribution loops.
        if dderc_targets:
            plans: list[
                tuple[str, bytes, DerProgramState, list[str] | None, DefaultDercontrol, bytes]
            ] = [
                (
                    dev_href,
                    lfdi,
                    best_prog,
                    self._group_local_lfdis(best_prog.href),
                    effective,
                    content,
                )
                for dev_href, lfdi, best_prog, effective, content in dderc_targets
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
            for dev_href, _, best_prog, local_lfdis, dderc, _content in plans:
                if local_lfdis is not None:
                    coros.extend(
                        self._dispatcher.apply_default_control_by_lfdi(
                            lfdi,
                            dderc,
                            best_prog.der_curves,
                            origin=origin,
                            cause_mrid=record.mrid,
                        )
                        for lfdi in local_lfdis
                    )
                else:
                    coros.append(
                        self._dispatcher.apply_default_control(
                            dev_href,
                            dderc,
                            best_prog.der_curves,
                            origin=origin,
                            cause_mrid=record.mrid,
                        )
                    )
            results = await asyncio.gather(*coros, return_exceptions=True)

            # The tracker is keyed by the server-side device LFDI (one entry
            # per dderc_target); collapse per-fan-out results down to one
            # success per target so the tracker isn't double-recorded.
            idx = 0
            for dev_href, lfdi, best_prog, local_lfdis, dderc, content in plans:
                n = len(local_lfdis) if local_lfdis is not None else 1
                target_results = results[idx : idx + n]
                idx += n
                failure = self._default_control_failure(target_results)
                if failure is not None:
                    logger.warning(
                        "Event %s: DDERC dispatch to %s failed: %s",
                        record.mrid.hex()[:8],
                        dev_href,
                        failure,
                    )
                else:
                    # The default's mRID, not the ended event's: the
                    # initial-default pass compares against it to skip a
                    # default that is already in place. Recorded for a default
                    # sent without some of its modes too, since that pass sends
                    # the same partial default to a device that still has a
                    # running event and would otherwise write it a second time.
                    # The event still running can bring the whole default back
                    # when it ends: this fallback checks primacy, not the mRID.
                    self._dderc_tracker.record_application(
                        lfdi,
                        best_prog.href,
                        dderc.m_rid.value,
                        best_prog.primacy,
                        get_active_mode_names(dderc.dercontrol_base),
                        content,
                    )

        if withdraw_targets:
            # Same fan-out as the DDERC path: by local LFDI when a group
            # lookup maps the server-side device to sub-devices, else by href.
            local_lfdis = self._group_local_lfdis(record.program_href)
            withdraw_coros: list[Coroutine[Any, Any, None]] = []
            if local_lfdis is not None:
                # A group lookup is keyed by program, so every server-side
                # device of the program stands for the same local devices. A
                # mode is disabled on them only when no server-side device
                # still needs it: none holds it from another event, and no
                # default restored above carries it. Sending each device's
                # released modes to every local device would disable a mode
                # that another event still controls there.
                still_needed = set(default_modes)
                for dev_href in all_devices:
                    still_needed |= self._mode_tracker.covered(dev_href, excluding=record.mrid)
                modes_for_program: frozenset[str] = (
                    frozenset().union(*(modes for _dev_href, modes in withdraw_targets))
                    - still_needed
                )
                if modes_for_program:
                    withdraw_coros.extend(
                        self._dispatcher.disable_modes_by_lfdi(
                            local, modes_for_program, cause_mrid=record.mrid
                        )
                        for local in dict.fromkeys(local_lfdis)
                    )
            else:
                withdraw_coros.extend(
                    self._dispatcher.disable_modes(dev_href, modes, cause_mrid=record.mrid)
                    for dev_href, modes in withdraw_targets
                )
            withdraw_results = await asyncio.gather(*withdraw_coros, return_exceptions=True)
            for failure in (r for r in withdraw_results if isinstance(r, Exception)):
                logger.warning(
                    "Event %s: disabling released modes failed: %s",
                    record.mrid.hex()[:8],
                    failure,
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
                    self._dispatcher.clear_control_by_lfdi(local, cause_mrid=record.mrid)
                    for local in dict.fromkeys(local_lfdis)
                )
            else:
                clear_coros.extend(
                    self._dispatcher.clear_control(dev_href, cause_mrid=record.mrid)
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
        """Apply the program's DefaultDERControl to each device, mode by mode.

        Called at the end of process_controls so a device is in its default
        operating state wherever no event overrides it. IEEE 2030.5-2023
        §10.10.4.2.1 reads per DERControl Mode: a mode with no active event in
        any program is managed by the default of the program with the lowest
        primacy that contains it. A device that has an active event therefore
        still takes the default's modes that no event controls there, and
        does not take the modes an event does control. Applies only if:
        - The program has a DefaultDERControl
        - No wind-down timers are pending (cancelled event with delayed fallback)
        - The DDERC hasn't already been applied (tracked by DdercTracker)
        - The default names a mode no active event controls on the device. A
          default that names no mode at all waits for the device's active
          events to end.
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

        devices = self._unique_devices(program_href)
        if not devices:
            logger.debug("Initial DDERC skip %s: no devices in mapping", program_href)
            return

        # Devices that have an ACTIVE event in ANY program. Opted-out events
        # (comms-loss) don't control their devices -- the device was reverted to
        # DDERC -- so they're excluded here, consistent with
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
                if (
                    d not in evt.superseded_devices
                    and d not in evt.rejected_devices
                    and evt.runs_on(d)
                ):
                    active_devices.add(d)

        dderc_mrid = derp_state.default_dercontrol.m_rid.value
        dderc_content = _default_content(derp_state.default_dercontrol)
        carried = get_active_mode_names(derp_state.default_dercontrol.dercontrol_base)

        # Each target is sent the default without the modes that something
        # else governs on it (IEEE 2030.5-2023 §10.10.4.2.1): the modes a
        # running event holds where the write lands, and the modes the default
        # of a higher-priority program names. A device with neither gets the
        # whole default. Under a group lookup the write reaches the program's
        # local devices whichever server-side device it was decided for, so every
        # target resolves to the same reach and the same held modes.
        targets: list[tuple[str, bytes, DefaultDercontrol, frozenset[str]]] = []
        for dev_href in devices:
            lfdi = self._get_device_lfdi(dev_href)
            if lfdi is None:
                continue

            held = self._modes_running_in(self._reach_of(program_href, [dev_href]), active_events)
            taken = carried & (held | self._higher_priority_default_modes(dev_href, derp_state))
            written = carried - taken
            if carried and not written:
                logger.debug(
                    "Initial DDERC skip %s: every mode it names is governed elsewhere on %s",
                    program_href,
                    dev_href,
                )
                continue

            if not self._dderc_tracker.should_apply_initial(
                lfdi, program_href, dderc_mrid, derp_state.primacy, written, dderc_content
            ):
                logger.debug(
                    "Initial DDERC skip %s: tracker blocked for %s (primacy=%d)",
                    program_href,
                    dev_href,
                    derp_state.primacy,
                )
                continue
            if not carried and dev_href in active_devices:
                logger.debug(
                    "Initial DDERC skip %s: %s has an active event and the default names no mode",
                    program_href,
                    dev_href,
                )
                continue
            if taken:
                logger.debug(
                    "Initial DDERC from %s sent to %s without %s, which something else governs",
                    program_href,
                    dev_href,
                    sorted(taken),
                )
            effective = (
                self._default_without(derp_state.default_dercontrol, taken)
                if taken
                else derp_state.default_dercontrol
            )
            targets.append((dev_href, lfdi, effective, written))

        if not targets:
            logger.debug("Initial DDERC skip %s: no eligible devices", program_href)
            return

        logger.info(
            "Applying initial DDERC from %s to %d device(s)",
            program_href,
            len(targets),
        )
        curves = derp_state.der_curves
        # With a group lookup supplied, each server-side EDev resolves
        # to the program's local sub-device LFDIs.
        local_lfdis = self._group_local_lfdis(program_href)
        coros: list[Coroutine[Any, Any, None]] = []
        for dev_href, _, dderc, _written in targets:
            if local_lfdis is not None:
                coros.extend(
                    self._dispatcher.apply_default_control_by_lfdi(lfdi, dderc, curves)
                    for lfdi in local_lfdis
                )
            else:
                coros.append(self._dispatcher.apply_default_control(dev_href, dderc, curves))
        results = await asyncio.gather(*coros, return_exceptions=True)

        per_target_n = len(local_lfdis) if local_lfdis is not None else 1
        idx = 0
        for dev_href, lfdi, _, written in targets:
            target_results = results[idx : idx + per_target_n]
            idx += per_target_n
            failure = self._default_control_failure(target_results)
            if failure is not None:
                logger.warning("Initial DDERC dispatch to %s failed: %s", dev_href, failure)
            else:
                # Recorded with the modes it carried, so the next poll does not
                # write the same modes again but does write one that was freed
                # since. The end of a running event still brings the whole
                # default back: the fallback checks primacy, and the program is
                # the same.
                self._dderc_tracker.record_application(
                    lfdi, program_href, dderc_mrid, derp_state.primacy, written, dderc_content
                )

    def _higher_priority_default_modes(
        self, dev_href: str, program: DerProgramState
    ) -> frozenset[str]:
        """The modes named by the default of a program that outranks ``program`` on a device.

        IEEE 2030.5-2023 §10.10.4.2.1 gives a mode with no active control to the
        default of the program with the lowest primacy that contains it, so a
        default does not write a mode that a lower-numbered program's default
        names. Read from the programs as they stand, not from what was written
        before: the record of earlier writes is cleared when an event arrives,
        and says nothing about precedence.
        """
        modes: set[str] = set()
        for other_href in self._state.device_mapping.device_to_programs.get(dev_href, []):
            other = self._state.der_programs.get(other_href)
            if (
                other is None
                or other.href == program.href
                or other.default_dercontrol is None
                or other.primacy >= program.primacy
            ):
                continue
            modes |= get_active_mode_names(other.default_dercontrol.dercontrol_base)
        return frozenset(modes)

    @staticmethod
    def _default_control_failure(results: Sequence[object]) -> Exception | None:
        """Return the first failure among one device's default-control writes, or None.

        A connector that implements none of the default's modes raises
        ``ModeNotSupportedError``. Retrying cannot change that, and a default
        has no response to reject, so it is not a failure to retry or warn about
        on every poll: the write is treated as done and logged at debug level.
        """
        for result in results:
            if isinstance(result, ModeNotSupportedError):
                logger.debug("Default control not applied: %s", result)
            elif isinstance(result, Exception):
                return result
        return None

    @staticmethod
    def _default_without(dderc: DefaultDercontrol, modes: frozenset[str]) -> DefaultDercontrol:
        """The default with ``modes`` taken out of it.

        A default is written whole, so the only way to let it set the modes no
        event is controlling without disturbing the ones an event is, is to
        send a copy that does not name them. Base modes are cleared by field
        name and the CSIP-AUS limits by element name, which is the pair
        :func:`get_active_mode_names` returns them in.
        """
        base = dderc.dercontrol_base
        cleared: dict[str, Any] = {name: None for name in modes if name in type(base).model_fields}
        kept = [
            element
            for element in (base.other_element or [])
            if doe_element_name(element) not in modes
        ]
        if len(kept) != len(base.other_element or []):
            cleared["other_element"] = kept
        return dderc.model_copy(update={"dercontrol_base": base.model_copy(update=cleared)})

    def _reach_of(self, program_href: str, devices: Sequence[str]) -> frozenset[str]:
        """What a write made for ``devices`` of ``program_href`` actually touches.

        Under a group lookup a program's write goes to its group's local
        devices whichever of its server-side devices it was decided for, so the
        group is the reach and the server-side device it was decided for says
        nothing about where it lands. Without one the write goes to the device
        it names. The two are tagged apart because a local LFDI and a device
        href are both strings and nothing stops them colliding.
        """
        local_lfdis = self._group_local_lfdis(program_href)
        if local_lfdis is not None:
            return frozenset(f"lfdi:{lfdi}" for lfdi in local_lfdis)
        return frozenset(f"href:{device}" for device in devices)

    def _modes_running_in(
        self, reach: frozenset[str], active_events: list[EventRecord]
    ) -> frozenset[str]:
        """The modes running events hold on anything ``reach`` touches.

        Asked of every active event rather than of a device list, because a
        group lookup maps *programs* to local devices: another program can
        cover server-side devices this one never names and still resolve to the
        same local device, and its modes are as much at risk from a write that
        lands there.
        """
        held: set[str] = set()
        for record in active_events:
            devices = list(
                self._state.device_mapping.program_to_devices.get(record.program_href, [])
            )
            if not reach & self._reach_of(record.program_href, devices):
                continue
            # Its registrations on the devices this write touches, not on all
            # of the program's. Without a group lookup the write lands on one
            # device, and an event registered on that program's other devices
            # may not be registered on this one -- rejected there, or
            # superseded there before it started. Counting those would hold a
            # mode back on a device nothing is controlling it on, and the
            # device would sit under neither the event nor the default.
            # With a group lookup the registrations are keyed by server-side
            # device while the write lands on local ones, so there is no
            # per-device correspondence to make and all of them count.
            one = frozenset({record.mrid})
            grouped = self._group_local_lfdis(record.program_href) is not None
            for device in devices:
                if grouped or f"href:{device}" in reach:
                    held |= self._mode_tracker.covered_among(device, one)
        return frozenset(held)

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
                and evt.runs_on(dev_href)
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

    def _unique_devices(self, program_href: str) -> list[str]:
        """The program's devices, each once, in discovery order.

        Every caller of this treats the list as dispatch targets, and a device
        listed twice is written to twice -- which for a real connector means two
        writes to hardware. ``DeviceMapping.add`` no longer admits a repeated
        pair, so this guards the case it cannot: state populated by an embedding
        application rather than by discovery.

        dict.fromkeys rather than a set because order is discovery order, which
        the response fan-out reports in.
        """
        devices = self._state.device_mapping.program_to_devices.get(program_href, [])
        # A device handed to another process gets no new dispatch from here.
        return [d for d in dict.fromkeys(devices) if d not in self._released]

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
            [
                d
                for d in self._state.device_mapping.program_to_devices.get(program_href, [])
                if d not in self._released
            ]
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
                supported_modes2=await self._supported_modes2(lfdi),
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
                    supported_modes2=await self._supported_modes2(lfdi),
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
                    supported_modes2=await self._supported_modes2(lfdi),
                )

    async def _supported_modes2(self, lfdi: bytes) -> int | None:
        """Return the DERControlType2 bits the device with ``lfdi`` can apply, if known.

        None when the server profile does not take ``modesResponded2``, since the
        response does not carry it.
        """
        profile = getattr(self._http, "profile", None)
        if profile is not None and not profile.sends_modes_responded2:
            return None
        supported = getattr(self._dispatcher, "supported_modes2", None)
        if supported is None:
            return None
        result = await supported(lfdi.hex())
        return result if isinstance(result, int) else None

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
