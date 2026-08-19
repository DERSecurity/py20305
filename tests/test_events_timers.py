"""Tests for EventTimerManager."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from py20305.events.state_machine import EventRecord, EventState
from py20305.events.timers import ACTIVATION_LEAD, COMPLETION_LEAD, EventTimerManager
from py20305.models.sep.sep import (
    DateTimeInterval,
    Dercontrol1,
    DercontrolBase,
    EventStatus,
    MRidtype,
    TimeType,
)


def _make_record(
    mrid_byte: int = 0x01,
    state: EventState = EventState.SCHEDULED,
    start: int | None = None,
    duration: int = 10,
) -> EventRecord:
    if start is None:
        start = int(time.time()) + 1  # 1 second from now
    return EventRecord(
        mrid=bytes([mrid_byte]) * 16,
        derc=Dercontrol1(
            m_rid=MRidtype(value=bytes([mrid_byte]) * 16),
            creation_time=TimeType(value=900),
            event_status=EventStatus(
                current_status=0,
                date_time=TimeType(value=950),
                potentially_superseded=False,
            ),
            interval=DateTimeInterval(duration=duration, start=TimeType(value=start)),
            dercontrol_base=DercontrolBase(),
        ),
        program_href="/derp/1",
        primacy=0,
        state=state,
        start=start,
        duration=duration,
        server_status_time=950,
    )


@pytest.mark.asyncio
async def test_activation_fires():
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    now = int(time.time())
    record = _make_record(start=now + ACTIVATION_LEAD)  # fires ~immediately

    mgr.schedule_activation(record, callback)
    await asyncio.sleep(0.5)

    callback.assert_awaited_once_with(record)
    await mgr.cancel_all()


@pytest.mark.asyncio
async def test_completion_fires():
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    now = int(time.time())
    # end = start + duration; fire at end - COMPLETION_LEAD
    # So start + duration - COMPLETION_LEAD = now -> start = now + COMPLETION_LEAD - duration
    duration = 10
    start = now + COMPLETION_LEAD - duration
    record = _make_record(start=start, duration=duration)

    mgr.schedule_completion(record, callback)
    await asyncio.sleep(0.5)

    callback.assert_awaited_once_with(record)
    await mgr.cancel_all()


@pytest.mark.asyncio
async def test_cancel_prevents_fire():
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    now = int(time.time())
    record = _make_record(start=now + ACTIVATION_LEAD + 1)

    mgr.schedule_activation(record, callback)
    mgr.cancel(record.mrid)
    await asyncio.sleep(0.3)

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_stops_timer():
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    now = int(time.time())
    record = _make_record(start=now + ACTIVATION_LEAD + 5)

    mgr.schedule_activation(record, callback)
    await asyncio.sleep(0.05)
    shutdown.set()
    await asyncio.sleep(0.2)

    callback.assert_not_awaited()
    await mgr.cancel_all()


@pytest.mark.asyncio
async def test_skips_cancelled_event():
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    now = int(time.time())
    record = _make_record(start=now + ACTIVATION_LEAD, state=EventState.SCHEDULED)

    mgr.schedule_activation(record, callback)
    # Cancel the event record's state before timer fires
    record.state = EventState.CANCELLED
    await asyncio.sleep(0.5)

    callback.assert_not_awaited()
    await mgr.cancel_all()


@pytest.mark.asyncio
async def test_cancel_all():
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    now = int(time.time())
    r1 = _make_record(0x01, start=now + ACTIVATION_LEAD + 5)
    r2 = _make_record(0x02, start=now + ACTIVATION_LEAD + 5)

    mgr.schedule_activation(r1, callback)
    mgr.schedule_activation(r2, callback)
    await mgr.cancel_all()
    await asyncio.sleep(0.1)

    callback.assert_not_awaited()


@pytest.mark.asyncio
async def test_now_fn_shifts_firing_to_server_time():
    """A skewed now_fn (server timebase) fires a far-future local target now."""
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    now = int(time.time())
    record = _make_record(start=now + 3600)  # an hour away on the local clock

    mgr.schedule_activation(record, callback, now_fn=lambda: int(time.time()) + 3600)
    await asyncio.sleep(0.5)

    callback.assert_awaited_once_with(record)
    await mgr.cancel_all()


@pytest.mark.asyncio
async def test_now_fn_update_reaims_mid_sleep():
    """remaining is recomputed each tick, so an offset change mid-sleep
    re-aims the firing instant without rescheduling."""
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()

    offset = 0
    now = int(time.time())
    record = _make_record(start=now + 3600)

    mgr.schedule_activation(record, callback, now_fn=lambda: int(time.time()) + offset)
    await asyncio.sleep(0.3)
    callback.assert_not_awaited()  # still an hour away

    offset = 3600  # server time observation jumps past the target
    await asyncio.sleep(1.5)  # coarse tick is 1s when remaining > 10
    callback.assert_awaited_once_with(record)
    await mgr.cancel_all()


@pytest.mark.asyncio
async def test_delayed_callback_stays_on_local_clock():
    """Wind-down delays are elapsed-time semantics: a delayed callback isn't
    accelerated by any server-time skew (no now_fn hook exposed)."""
    shutdown = asyncio.Event()
    mgr = EventTimerManager(shutdown)
    callback = AsyncMock()
    record = _make_record()

    mgr.schedule_delayed_callback(record, 3600, callback)
    await asyncio.sleep(0.3)

    callback.assert_not_awaited()
    await mgr.cancel_all()
