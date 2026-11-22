"""Tests for randomizing each device's event timing on its own.

IEEE 2030.5-2023 §10.2.3.2.2 and §10.2.3.2.3 have each device draw its own
start and duration offsets, so a client acting for several EndDevices spreads
them out instead of moving them as one block. The draws are made known by
replacing ``random.randint`` in the randomization module with a fixed sequence.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest

from py20305.client.state import DerProgramState, DiscoveredState, EndDeviceState
from py20305.events.dispatch import NullDispatcher
from py20305.events.processor import EventProcessor
from py20305.events.response import ResponseCode
from py20305.events.shared_state import MemoryEventStateStore
from py20305.events.state_machine import EventRecord, EventState
from py20305.events.supersession import compute_supersession
from py20305.models.sep.sep import (
    DateTimeInterval,
    Dercontrol1,
    DercontrolBase,
    Derprogram1,
    EndDevice1,
    EventStatus,
    MRidtype,
    OneHourRangeType,
    PerCentControlType,
    PrimacyType,
    Sfditype,
    TimeType,
)
from py20305.server_profile import Edition, ServerProfile

DEV_1, DEV_2 = "/edev/1", "/edev/2"
LFDI_1, LFDI_2 = b"\x01" * 20, b"\x02" * 20


def _now() -> int:
    return int(time.time())


def _derc(mrid: int, start: int, *, randomize_start: int | None = None) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=bytes([mrid]) * 16),
        creation_time=TimeType(value=1),
        event_status=EventStatus(
            current_status=0, date_time=TimeType(value=1), potentially_superseded=False
        ),
        interval=DateTimeInterval(duration=3600, start=TimeType(value=start)),
        dercontrol_base=DercontrolBase(op_mod_max_lim_w=PerCentControlType(value=5000)),
        randomize_start=(
            OneHourRangeType(value=randomize_start) if randomize_start is not None else None
        ),
        reply_to="/rsps",
        response_required=b"\x03",
    )


def _state(controls: list[Dercontrol1]) -> DiscoveredState:
    """Return state with EndDevices 1 and 2 both under DERProgram /derp/1."""
    state = DiscoveredState()
    state.der_programs["/derp/1"] = DerProgramState(
        program=Derprogram1(m_rid=MRidtype(value=b"\x10" * 16), primacy=PrimacyType(value=1)),
        href="/derp/1",
        primacy=1,
        der_controls=list(controls),
        der_controls_complete=True,
    )
    for href, lfdi in ((DEV_1, LFDI_1), (DEV_2, LFDI_2)):
        state.end_devices[href] = EndDeviceState(
            device=EndDevice1(
                m_rid=MRidtype(value=lfdi[:16]),
                s_fdi=Sfditype(value=0),
                changed_time=TimeType(value=0),
            ),
            href=href,
            lfdi=lfdi,
        )
        state.device_mapping.add("/derp/1", href)
    return state


def _http() -> AsyncMock:
    http = AsyncMock()
    http.profile = ServerProfile(base=Edition.E2023)
    http.post = AsyncMock(return_value=None)
    return http


class RecordingDispatcher(NullDispatcher):
    """Records the device of each control it applies, in order."""

    def __init__(self) -> None:
        self.controls: list[str] = []

    async def apply_control(self, device_href, derc, curves) -> None:  # type: ignore[override]
        self.controls.append(device_href)


@pytest.fixture
def draws(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make each random draw take the next value of the returned list."""
    values: list[int] = []
    iterator: Iterator[int] | None = None

    def randint(_low: int, _high: int) -> int:
        nonlocal iterator
        if iterator is None:
            iterator = iter(values)
        return next(iterator)

    monkeypatch.setattr("py20305.events.randomization.random.randint", randint)
    return values


def _statuses(http: AsyncMock) -> list[tuple[bytes, int]]:
    return [(c.args[1].end_device_lfdi, c.args[1].status) for c in http.post.await_args_list]


class TestPerDeviceRandomization:
    async def test_two_devices_of_one_event_draw_independent_offsets(self, draws: list[int]):
        draws.extend([100, 400])
        start = _now() + 7200
        proc = EventProcessor(
            _http(),
            _state([_derc(1, start, randomize_start=600)]),
            NullDispatcher(),
            asyncio.Event(),
        )
        try:
            await proc.process_controls("/derp/1")

            record = proc._store.get(b"\x01" * 16)
            assert record is not None
            assert record.window(DEV_1) == (start + 100, start + 100 + 3600)
            assert record.window(DEV_2) == (start + 400, start + 400 + 3600)
            assert (record.start, record.end) == (start + 100, start + 400 + 3600)
        finally:
            await proc.shutdown()

    async def test_each_device_is_dispatched_at_its_own_start(self, draws: list[int]):
        draws.extend([0, 2])
        dispatcher = RecordingDispatcher()
        http = _http()
        proc = EventProcessor(
            http,
            _state([_derc(1, _now() + 1, randomize_start=2)]),
            dispatcher,
            asyncio.Event(),
        )
        try:
            await proc.process_controls("/derp/1")
            await asyncio.sleep(1.6)

            assert dispatcher.controls == [DEV_1]
            assert (LFDI_2, int(ResponseCode.ACTIVE)) not in _statuses(http)

            await asyncio.sleep(2.0)

            assert dispatcher.controls == [DEV_1, DEV_2]
            assert (LFDI_2, int(ResponseCode.ACTIVE)) in _statuses(http)
        finally:
            await proc.shutdown()

    async def test_successive_events_chain_on_each_device(self, draws: list[int]):
        draws.extend([100, 300])
        start = _now() + 7200
        first = _derc(1, start, randomize_start=600)
        second = _derc(2, start + 3600, randomize_start=600)
        store = MemoryEventStateStore()
        proc = EventProcessor(
            _http(), _state([first, second]), NullDispatcher(), asyncio.Event(), state_store=store
        )
        try:
            await proc.process_controls("/derp/1")

            earlier = proc._store.get(b"\x01" * 16)
            later = proc._store.get(b"\x02" * 16)
            assert earlier is not None and later is not None
            assert later.window(DEV_1)[0] == earlier.window(DEV_1)[1] == start + 100 + 3600
            assert later.window(DEV_2)[0] == earlier.window(DEV_2)[1] == start + 300 + 3600
            assert store.claim_offsets((b"\x02" * 16).hex(), DEV_2, (0, 0)) == (300, 0)
        finally:
            await proc.shutdown()

    async def test_a_late_event_starts_each_device_at_now_plus_its_own_offset(
        self, draws: list[int]
    ):
        draws.extend([1, 2])
        dispatcher = RecordingDispatcher()
        http = _http()
        raw_start = _now() - 100
        proc = EventProcessor(
            http,
            _state([_derc(1, raw_start, randomize_start=3)]),
            dispatcher,
            asyncio.Event(),
        )
        try:
            before = _now()
            await proc.process_controls("/derp/1")

            record = proc._store.get(b"\x01" * 16)
            assert record is not None
            assert record.state is EventState.SCHEDULED
            assert before + 1 <= record.window(DEV_1)[0] <= _now() + 1
            assert before + 2 <= record.window(DEV_2)[0] <= _now() + 2
            # The specified end is kept (rule k), with no duration randomization here.
            assert record.window(DEV_1)[1] == record.window(DEV_2)[1] == raw_start + 3600
            assert dispatcher.controls == []

            await asyncio.sleep(3.2)

            assert dispatcher.controls == [DEV_1, DEV_2]
            active = [lfdi for lfdi, status in _statuses(http) if status == ResponseCode.ACTIVE]
            assert active == [LFDI_1, LFDI_2]
        finally:
            await proc.shutdown()

    async def test_a_taking_over_process_reuses_each_devices_stored_offsets(self):
        store = MemoryEventStateStore()
        mrid = (b"\x01" * 16).hex()
        store.claim_offsets(mrid, DEV_1, (123, 0))
        store.claim_offsets(mrid, DEV_2, (456, 0))
        start = _now() + 7200
        proc = EventProcessor(
            _http(),
            _state([_derc(1, start, randomize_start=600)]),
            NullDispatcher(),
            asyncio.Event(),
            state_store=store,
        )
        try:
            await proc.process_controls("/derp/1")

            record = proc._store.get(b"\x01" * 16)
            assert record is not None
            assert record.window(DEV_1)[0] == start + 123
            assert record.window(DEV_2)[0] == start + 456
        finally:
            await proc.shutdown()

    async def test_a_device_moved_by_snapshot_keeps_its_window(self, draws: list[int]):
        draws.extend([100, 400])
        store = MemoryEventStateStore()
        start = _now() + 7200
        derc = _derc(1, start, randomize_start=600)
        a = EventProcessor(
            _http(), _state([derc]), NullDispatcher(), asyncio.Event(), state_store=store
        )
        await a.process_controls("/derp/1")
        await a.release_devices([DEV_2])
        await a.shutdown()

        b = EventProcessor(
            _http(), _state([derc]), NullDispatcher(), asyncio.Event(), state_store=store
        )
        try:
            b.load_devices([DEV_2])

            record = b._store.get(b"\x01" * 16)
            assert record is not None
            assert record.window(DEV_2) == (start + 400, start + 400 + 3600)
        finally:
            await b.shutdown()


def _record(mrid: int, primacy: int, window: tuple[int, int]) -> EventRecord:
    derc = _derc(mrid, window[0])
    return EventRecord(
        mrid=derc.m_rid.value,
        derc=derc,
        program_href=f"/derp/{mrid}",
        primacy=primacy,
        state=EventState.SCHEDULED,
        start=90,
        duration=20,
        server_status_time=1,
        device_windows={DEV_1: window},
    )


class TestPerDeviceSupersession:
    def test_an_empty_device_window_supersedes_nothing(self):
        empty = _record(1, primacy=1, window=(100, 100))
        running = _record(2, primacy=2, window=(90, 110))

        results = compute_supersession([empty, running], {"/derp/1": {DEV_1}, "/derp/2": {DEV_1}})

        assert results == []

    def test_overlapping_device_windows_supersede(self):
        winner = _record(1, primacy=1, window=(95, 105))
        loser = _record(2, primacy=2, window=(90, 110))

        results = compute_supersession([winner, loser], {"/derp/1": {DEV_1}, "/derp/2": {DEV_1}})

        assert [r.superseded_mrid for r in results] == [loser.mrid]
