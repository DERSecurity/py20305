"""Tests for moving EndDevices between client processes that act as one client.

The processes share a subscription store and an event state store. One
process releases an EndDevice and another takes it over; the loopback server
records every Response, and each process's dispatcher records every control it
applies, so a Response or a control sent twice, or never, shows.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from py20305.client.csip_client import CsipClient
from py20305.client.scope import DeviceSet
from py20305.client.state import DerProgramState, DiscoveredState, EndDeviceState
from py20305.events.comms_loss import CommsLossState
from py20305.events.dispatch import NullDispatcher
from py20305.events.processor import EventProcessor
from py20305.events.shared_state import EventStateStore, MemoryEventStateStore
from py20305.events.state_machine import EventRecord, EventState
from py20305.models.sep.sep import (
    DateTimeInterval,
    DefaultDercontrol,
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
from py20305.subscription.manager import SubscriptionManager
from py20305.subscription.store import MemorySubscriptionStore
from py20305.xml.serialization import to_xml
from tests.scale_out_server import NOTIFY, Control, FakeTreeServer, notification_xml

C1 = "C1000000000000000000000000000001"
C2 = "C2000000000000000000000000000002"
ACK, ACTIVE, CANCELLED = 1, 2, 6


class RecordingDispatcher(NullDispatcher):
    """Records each control and each default control it is asked to apply."""

    def __init__(self) -> None:
        self.controls: list[tuple[str, str]] = []
        self.defaults: list[str] = []

    async def apply_control(self, device_href, derc, curves) -> None:  # type: ignore[override]
        self.controls.append((device_href, derc.m_rid.value.hex().upper()))

    async def apply_default_control(self, device_href, dderc, curves, **_kw) -> None:  # type: ignore[override]
        self.defaults.append(device_href)


class Process:
    """One client process: its client, device set and dispatcher."""

    def __init__(
        self,
        base_url: str,
        subs: MemorySubscriptionStore,
        events: EventStateStore | None,
        devices: list[int],
        *,
        own: bool,
    ) -> None:
        self.devices = DeviceSet(hrefs=[f"/edev/{i}" for i in devices])
        self.dispatcher = RecordingDispatcher()
        self.client = CsipClient(
            base_url,
            reconcile_enabled=False,
            dispatcher=self.dispatcher,
            device_filter=self.devices,
            own_end_device=own,
            event_state_store=events,
        )
        self.client.attach_subscriptions(SubscriptionManager(self.client.http, NOTIFY, store=subs))


async def _two_processes(aiohttp_server, fake: FakeTreeServer, events: EventStateStore | None):
    server = await aiohttp_server(fake.app())
    base_url = str(server.make_url("")).rstrip("/")
    subs = MemorySubscriptionStore()
    a = Process(base_url, subs, events, [1], own=False)
    b = Process(base_url, subs, events, [0], own=True)
    await b.client.connect()
    await a.client.connect()
    return a, b


async def _move_device_1(a: Process, b: Process) -> bool:
    """Release EndDevice 1 from process A and take it over in process B."""
    a.devices.discard(hrefs=["/edev/1"])
    await a.client.release_devices(["/edev/1"])
    await a.client.shutdown()
    b.devices.add(hrefs=["/edev/1"])
    return await b.client.take_over_devices(["/edev/1"])


def _now() -> int:
    return int(time.time())


class TestHandover:
    async def test_a_running_event_is_carried_on_without_a_second_response_or_control(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() - 5)]})
        a, b = await _two_processes(aiohttp_server, fake, MemoryEventStateStore())
        try:
            await a.client.poll_now()
            assert fake.responses_for(1) == [(C1, ACK), (C1, ACTIVE)]
            assert a.dispatcher.controls == [("/edev/1", C1)]

            assert await _move_device_1(a, b)

            assert fake.responses_for(1) == [(C1, ACK), (C1, ACTIVE)]
            assert b.dispatcher.controls == []
            record = b.client._event_processor._store.get(bytes.fromhex(C1))
            assert record is not None
            assert record.state is EventState.ACTIVE
            assert record.applied_devices == {"/edev/1"}
        finally:
            await b.client.shutdown()

    async def test_the_taking_over_process_answers_a_later_cancellation_once(self, aiohttp_server):
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() - 5)]})
        a, b = await _two_processes(aiohttp_server, fake, MemoryEventStateStore())
        try:
            await a.client.poll_now()
            assert await _move_device_1(a, b)

            fake.controls[1][0].current_status = 2
            await b.client.poll_now()
            await b.client.poll_now()

            assert fake.responses_for(1) == [(C1, ACK), (C1, ACTIVE), (C1, CANCELLED)]
        finally:
            await b.client.shutdown()

    async def test_an_event_due_while_no_process_acted_is_applied_once_after_take_over(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() + 2)]})
        a, b = await _two_processes(aiohttp_server, fake, MemoryEventStateStore())
        try:
            await a.client.poll_now()
            assert fake.responses_for(1) == [(C1, ACK)]
            a.devices.discard(hrefs=["/edev/1"])
            await a.client.release_devices(["/edev/1"])
            await a.client.shutdown()
            await asyncio.sleep(2.5)

            b.devices.add(hrefs=["/edev/1"])
            assert await b.client.take_over_devices(["/edev/1"])
            for _ in range(40):
                if b.dispatcher.controls:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.1)

            assert a.dispatcher.controls == []
            assert b.dispatcher.controls == [("/edev/1", C1)]
            assert fake.responses_for(1) == [(C1, ACK), (C1, ACTIVE)]
            assert fake.responses_for(0) == []
        finally:
            await b.client.shutdown()

    async def test_a_control_announced_during_the_take_over_is_handled_once(self, aiohttp_server):
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() - 5)]})
        a, b = await _two_processes(aiohttp_server, fake, MemoryEventStateStore())
        try:
            await a.client.poll_now()
            a.devices.discard(hrefs=["/edev/1"])
            await a.client.release_devices(["/edev/1"])
            await a.client.shutdown()
            b.devices.add(hrefs=["/edev/1"])
            sub = {
                s.subscribed_resource: s.subscription_uri
                for s in b.client.subscription_manager.active_subscriptions  # type: ignore[union-attr]
            }

            fake.hold = "/edev/1/fsa"
            take_over = asyncio.ensure_future(b.client.take_over_devices(["/edev/1"]))
            await asyncio.wait_for(fake.reached.wait(), 5)
            fake.controls[1].append(Control(C2, start=_now() + 7200))
            acted = await b.client.process_notification(
                notification_xml("/derp/1/derc", sub["/derp/1/derc"])
            )
            fake.release.set()
            assert await take_over
            await b.client.poll_now()

            assert acted is True
            assert fake.responses_for(1) == [(C1, ACK), (C1, ACTIVE), (C2, ACK)]
        finally:
            await b.client.shutdown()

    async def test_a_response_posted_before_the_snapshot_was_saved_is_not_posted_again(
        self, aiohttp_server
    ):
        events = MemoryEventStateStore()
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() + 7200)]})
        a, b = await _two_processes(aiohttp_server, fake, events)
        try:
            await a.client.poll_now()
            assert fake.responses_for(1) == [(C1, ACK)]
            a.devices.discard(hrefs=["/edev/1"])
            await a.client.release_devices(["/edev/1"])
            await a.client.shutdown()
            # The process stopped after the POST and before its snapshot was written.
            snapshot = events.load_device("/edev/1")
            assert snapshot is not None
            events.save_device("/edev/1", {**snapshot, "events": []})

            b.devices.add(hrefs=["/edev/1"])
            assert await b.client.take_over_devices(["/edev/1"])
            await b.client.poll_now()

            assert fake.responses_for(1) == [(C1, ACK)]
        finally:
            await b.client.shutdown()

    async def test_a_response_posted_without_any_snapshot_is_not_posted_again(self, aiohttp_server):
        events = MemoryEventStateStore()
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() + 7200)]})
        a, b = await _two_processes(aiohttp_server, fake, events)
        try:
            await a.client.poll_now()
            assert fake.responses_for(1) == [(C1, ACK)]
            a.devices.discard(hrefs=["/edev/1"])
            await a.client.release_devices(["/edev/1"])
            await a.client.shutdown()
            # The process stopped after its first Response and before any snapshot.
            events._devices.pop("/edev/1")

            b.devices.add(hrefs=["/edev/1"])
            assert await b.client.take_over_devices(["/edev/1"])
            await b.client.poll_now()

            assert fake.responses_for(1) == [(C1, ACK)]
        finally:
            await b.client.shutdown()

    async def test_a_take_over_whose_rediscovery_fails_resumes_nothing(
        self, aiohttp_server, monkeypatch
    ):
        events = MemoryEventStateStore()
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() - 5)]})
        a, b = await _two_processes(aiohttp_server, fake, events)
        try:
            await a.client.poll_now()
            a.devices.discard(hrefs=["/edev/1"])
            await a.client.release_devices(["/edev/1"])
            await a.client.shutdown()
            # The device never received the running control.
            snapshot = events.load_device("/edev/1")
            assert snapshot is not None
            snapshot["events"][0]["applied"] = False
            events.save_device("/edev/1", snapshot)
            b.devices.add(hrefs=["/edev/1"])
            refresh = AsyncMock(side_effect=RuntimeError("DERControl list unreadable"))
            monkeypatch.setattr("py20305.client.csip_client.refresh_der_controls", refresh)

            assert not await b.client.take_over_devices(["/edev/1"])
            assert b.dispatcher.controls == []

            monkeypatch.undo()
            assert await b.client.take_over_devices(["/edev/1"])
            assert b.dispatcher.controls == [("/edev/1", C1)]
        finally:
            await b.client.shutdown()

    async def test_a_device_taken_over_without_a_snapshot_joins_a_running_event(
        self, aiohttp_server
    ):
        # Both EndDevices are served by DERProgram 0, whose control is running.
        fake = FakeTreeServer(
            devices=2, program_of={1: 0}, controls={0: [Control(C1, start=_now() - 5)]}
        )
        server = await aiohttp_server(fake.app())
        base_url = str(server.make_url("")).rstrip("/")
        b = Process(base_url, MemorySubscriptionStore(), MemoryEventStateStore(), [0], own=True)
        await b.client.connect()
        try:
            await b.client.poll_now()
            assert b.dispatcher.controls == [("/edev/0", C1)]

            b.devices.add(hrefs=["/edev/1"])
            assert await b.client.take_over_devices(["/edev/1"])

            assert b.dispatcher.controls == [("/edev/0", C1), ("/edev/1", C1)]
            assert fake.responses_for(1) == [(C1, ACK), (C1, ACTIVE)]
        finally:
            await b.client.shutdown()

    async def test_without_an_event_state_store_the_taking_over_process_starts_again(
        self, aiohttp_server
    ):
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() - 5)]})
        a, b = await _two_processes(aiohttp_server, fake, None)
        try:
            await a.client.poll_now()
            assert await _move_device_1(a, b)
            await b.client.poll_now()

            assert b.dispatcher.controls == [("/edev/1", C1)]
        finally:
            await b.client.shutdown()

    async def test_releasing_a_device_posts_nothing_and_writes_nothing(self, aiohttp_server):
        fake = FakeTreeServer(devices=2, controls={1: [Control(C1, start=_now() - 5)]})
        a, b = await _two_processes(aiohttp_server, fake, MemoryEventStateStore())
        try:
            await a.client.poll_now()
            posted = list(fake.responses)
            a.devices.discard(hrefs=["/edev/1"])

            await a.client.release_devices(["/edev/1"])

            assert fake.responses == posted
            assert a.dispatcher.controls == [("/edev/1", C1)]
            assert a.dispatcher.defaults == []
            assert a.client._event_processor._store.get(bytes.fromhex(C1)) is None
        finally:
            await a.client.shutdown()
            await b.client.shutdown()


# ---------------------------------------------------------------------------
# The processor on its own: randomization, responses, loss of communications
# ---------------------------------------------------------------------------


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


def _state(device_href: str, lfdi: bytes, controls: list[Dercontrol1]) -> DiscoveredState:
    state = DiscoveredState()
    state.der_programs["/derp/1"] = DerProgramState(
        program=Derprogram1(m_rid=MRidtype(value=b"\x10" * 16), primacy=PrimacyType(value=1)),
        href="/derp/1",
        primacy=1,
        default_dercontrol=DefaultDercontrol(
            m_rid=MRidtype(value=b"\x20" * 16), dercontrol_base=DercontrolBase()
        ),
        der_controls=list(controls),
        der_controls_complete=True,
    )
    state.end_devices[device_href] = EndDeviceState(
        device=EndDevice1(
            m_rid=MRidtype(value=b"\x30" * 16),
            s_fdi=Sfditype(value=0),
            changed_time=TimeType(value=0),
        ),
        href=device_href,
        lfdi=lfdi,
    )
    state.device_mapping.add("/derp/1", device_href)
    return state


def _http() -> AsyncMock:
    http = AsyncMock()
    http.profile = ServerProfile(base=Edition.E2023)
    http.post = AsyncMock(return_value=None)
    return http


def _add_device(state: DiscoveredState, device_href: str, lfdi: bytes) -> None:
    """Add an EndDevice to ``state`` under DERProgram /derp/1."""
    state.end_devices[device_href] = EndDeviceState(
        device=EndDevice1(
            m_rid=MRidtype(value=lfdi[:16]),
            s_fdi=Sfditype(value=0),
            changed_time=TimeType(value=0),
        ),
        href=device_href,
        lfdi=lfdi,
    )
    state.device_mapping.add("/derp/1", device_href)


def _processor(
    state: DiscoveredState, store: EventStateStore, dispatcher: NullDispatcher | None = None
) -> EventProcessor:
    return EventProcessor(
        _http(), state, dispatcher or NullDispatcher(), asyncio.Event(), state_store=store
    )


def _entry(derc: Dercontrol1, state: str, **marks: object) -> dict:
    """Return one event entry of a device snapshot for ``derc``, unrandomized."""
    entry = {
        "mrid": derc.m_rid.value.hex(),
        "program_href": "/derp/1",
        "primacy": 1,
        "state": state,
        "start": derc.interval.start.value,
        "duration": derc.interval.duration,
        "server_status_time": 1,
        "creation_time": 1,
        "control": to_xml(derc).decode(),
        "applied": False,
        "rejected": False,
        "superseded": False,
        "superseded_modes": None,
        "opted_out": False,
    }
    entry.update(marks)
    return entry


def _snapshot(lfdi: bytes, events: list[dict], resume_after: int | None = None) -> dict:
    return {
        "version": 1,
        "lfdi": lfdi.hex(),
        "events": events,
        "defaults": [],
        "comms_loss": {"active": False, "resume_after": resume_after},
    }


class FailingStore(MemoryEventStateStore):
    """An event state store whose snapshot saves fail."""

    def save_device(self, device_href: str, snapshot: dict) -> None:
        raise OSError("store unavailable")


class BlockingDispatcher(RecordingDispatcher):
    """A dispatcher whose control writes wait until ``release`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def apply_control(self, device_href, derc, curves) -> None:  # type: ignore[override]
        self.entered.set()
        await self.release.wait()
        await super().apply_control(device_href, derc, curves)


class TestHandoverEdgeCases:
    async def test_a_released_device_keeps_a_pending_default_fallback_delay(self):
        store = MemoryEventStateStore()
        lfdi = b"\x01" * 20
        derc = _derc(1, _now() - 700, randomize_start=600)
        a = _processor(_state("/edev/1", lfdi, [derc]), store)
        await a.process_controls("/derp/1")
        cancelled = _derc(1, _now() - 700, randomize_start=600)
        cancelled.event_status.current_status = 2  # type: ignore[union-attr]
        a._state.der_programs["/derp/1"].der_controls = [cancelled]
        await a.process_controls("/derp/1")
        await a.release_devices(["/edev/1"])
        await a.shutdown()

        dispatcher = RecordingDispatcher()
        b = _processor(_state("/edev/1", lfdi, [cancelled]), store, dispatcher)
        try:
            b.load_devices(["/edev/1"])
            await b.process_controls("/derp/1")

            assert b._timer_mgr.has_pending(b"\x01" * 16)
            assert dispatcher.defaults == []
        finally:
            await b.shutdown()

    async def test_a_boundary_taken_over_with_a_device_does_not_gate_a_healthy_one(self):
        store = MemoryEventStateStore()
        lfdi_1, lfdi_2 = b"\x01" * 20, b"\x02" * 20
        store.save_device("/edev/1", _snapshot(lfdi_1, [], resume_after=_now() + 3600))
        state = _state("/edev/2", lfdi_2, [_derc(1, _now() - 5)])
        _add_device(state, "/edev/1", lfdi_1)
        dispatcher = RecordingDispatcher()
        b = _processor(state, store, dispatcher)
        try:
            b.load_devices(["/edev/1"])
            await b.process_controls("/derp/1")

            assert ("/edev/2", (b"\x01" * 16).hex().upper()) in dispatcher.controls
        finally:
            await b.shutdown()

    async def test_an_event_superseded_here_resumes_for_a_device_still_running_it(self):
        store = MemoryEventStateStore()
        lfdi_1, lfdi_2 = b"\x01" * 20, b"\x02" * 20
        derc = _derc(1, _now() - 5)
        store.save_device("/edev/1", _snapshot(lfdi_1, [_entry(derc, "active", applied=True)]))
        state = _state("/edev/2", lfdi_2, [derc])
        _add_device(state, "/edev/1", lfdi_1)
        b = _processor(state, store)
        record = EventRecord(
            mrid=derc.m_rid.value,
            derc=derc,
            program_href="/derp/1",
            primacy=1,
            state=EventState.SUPERSEDED,
            start=derc.interval.start.value,
            duration=derc.interval.duration,
            server_status_time=1,
            superseded_devices={"/edev/2"},
        )
        b._store.upsert(record)
        try:
            b.load_devices(["/edev/1"])
            await b.resume_devices(["/edev/1"])

            assert record.state is EventState.ACTIVE
            assert record.superseded_devices == {"/edev/2"}
            assert b._timer_mgr.has_pending(record.mrid)
        finally:
            await b.shutdown()

    async def test_an_incoming_opt_out_does_not_opt_out_this_processes_devices(self):
        store = MemoryEventStateStore()
        lfdi_1, lfdi_2 = b"\x01" * 20, b"\x02" * 20
        derc = _derc(1, _now() - 5)
        store.save_device(
            "/edev/1",
            _snapshot(lfdi_1, [_entry(derc, "active", applied=True, opted_out=True)]),
        )
        b = _processor(_state("/edev/2", lfdi_2, [derc]), store)
        try:
            await b.process_controls("/derp/1")
            _add_device(b._state, "/edev/1", lfdi_1)

            b.load_devices(["/edev/1"])

            record = b._store.get(derc.m_rid.value)
            assert record is not None
            assert record.opted_out is False
            assert "/edev/1" in record.rejected_devices
        finally:
            await b.shutdown()

    async def test_a_partly_superseded_device_keeps_the_modes_it_runs(self):
        store = MemoryEventStateStore()
        lfdi = b"\x01" * 20
        derc = _derc(1, _now() - 5)
        entry = _entry(
            derc, "active", applied=True, superseded=True, superseded_modes=["opModFixedW"]
        )
        store.save_device("/edev/1", _snapshot(lfdi, [entry]))
        b = _processor(_state("/edev/1", lfdi, [derc]), store)
        try:
            b.load_devices(["/edev/1"])

            assert b._mode_tracker.covered("/edev/1")
        finally:
            await b.shutdown()

    async def test_a_restored_event_past_its_end_is_not_applied(self):
        store = MemoryEventStateStore()
        lfdi = b"\x01" * 20
        derc = _derc(1, _now() - 3605)
        store.save_device("/edev/1", _snapshot(lfdi, [_entry(derc, "active")]))
        dispatcher = RecordingDispatcher()
        b = _processor(_state("/edev/1", lfdi, [derc]), store, dispatcher)
        try:
            b.load_devices(["/edev/1"])
            await b.resume_devices(["/edev/1"])
            await asyncio.sleep(0.1)

            assert dispatcher.controls == []
            statuses = [call.args[1].status for call in b._http.post.await_args_list]
            assert ACTIVE not in statuses
        finally:
            await b.shutdown()

    async def test_a_failed_final_save_keeps_the_devices_and_raises(self):
        store = FailingStore()
        derc = _derc(1, _now() - 5)
        a = _processor(_state("/edev/1", b"\x01" * 20, [derc]), store)
        try:
            await a.process_controls("/derp/1")

            with pytest.raises(RuntimeError, match="could not save"):
                await a.release_devices(["/edev/1"])

            assert "/edev/1" not in a._released
            assert a._store.get(derc.m_rid.value) is not None
        finally:
            await a.shutdown()

    async def test_release_waits_for_a_control_write_in_flight(self):
        store = MemoryEventStateStore()
        dispatcher = BlockingDispatcher()
        a = _processor(_state("/edev/1", b"\x01" * 20, [_derc(1, _now() - 5)]), store, dispatcher)
        try:
            processing = asyncio.ensure_future(a.process_controls("/derp/1"))
            await asyncio.wait_for(dispatcher.entered.wait(), 5)
            release = asyncio.ensure_future(a.release_devices(["/edev/1"]))
            await asyncio.sleep(0.05)
            assert not release.done()

            dispatcher.release.set()
            await processing
            await release

            snapshot = store.load_device("/edev/1")
            assert snapshot is not None
            assert snapshot["events"][0]["applied"] is True
        finally:
            dispatcher.release.set()
            await a.shutdown()

    async def test_a_successive_events_adjusted_start_is_the_shared_one(self):
        store = MemoryEventStateStore()
        start = _now() + 7200
        first = _derc(1, start, randomize_start=600)
        second = _derc(2, start + 3600, randomize_start=600)
        store.claim_offsets(first.m_rid.value.hex(), (300, 0))
        a = _processor(_state("/edev/1", b"\x01" * 20, [first, second]), store)
        try:
            await a.process_controls("/derp/1")

            record = a._store.get(second.m_rid.value)
            assert record is not None
            assert record.start == start + 3600 + 300
            assert store._offsets[second.m_rid.value.hex()] == (300, 0)
        finally:
            await a.shutdown()


class TestSharedProcessorState:
    async def test_processing_controls_saves_each_device_snapshot(self):
        store = MemoryEventStateStore()
        proc = EventProcessor(
            _http(),
            _state("/edev/1", b"" * 20, [_derc(1, _now() - 5)]),
            NullDispatcher(),
            asyncio.Event(),
            state_store=store,
        )
        try:
            await proc.process_controls("/derp/1")

            snapshot = store.load_device("/edev/1")
            assert snapshot is not None
            assert [(e["mrid"], e["state"], e["applied"]) for e in snapshot["events"]] == [
                ((b"" * 16).hex(), "active", True)
            ]
        finally:
            await proc.shutdown()

    async def test_two_processes_give_one_event_the_same_randomized_start(self):
        store = MemoryEventStateStore()
        start = _now() + 7200
        procs = [
            EventProcessor(
                _http(),
                _state(f"/edev/{i}", bytes([i]) * 20, [_derc(1, start, randomize_start=3600)]),
                NullDispatcher(),
                asyncio.Event(),
                state_store=store,
            )
            for i in (1, 2)
        ]
        try:
            for proc in procs:
                await proc.process_controls("/derp/1")
            starts = {proc._store.get(b"\x01" * 16).start for proc in procs}  # type: ignore[union-attr]

            assert len(starts) == 1
        finally:
            for proc in procs:
                await proc.shutdown()

    async def test_stored_randomization_offsets_are_used(self):
        store = MemoryEventStateStore()
        store.claim_offsets((b"\x01" * 16).hex(), (123, 0))
        start = _now() + 7200
        proc = EventProcessor(
            _http(),
            _state("/edev/1", b"\x01" * 20, [_derc(1, start, randomize_start=3600)]),
            NullDispatcher(),
            asyncio.Event(),
            state_store=store,
        )
        try:
            await proc.process_controls("/derp/1")

            assert proc._store.get(b"\x01" * 16).start == start + 123  # type: ignore[union-attr]
        finally:
            await proc.shutdown()

    async def test_each_response_is_recorded_as_it_is_posted(self):
        store = MemoryEventStateStore()
        proc = EventProcessor(
            _http(),
            _state("/edev/1", b"\x01" * 20, [_derc(1, _now() + 7200)]),
            NullDispatcher(),
            asyncio.Event(),
            state_store=store,
        )
        try:
            await proc.process_controls("/derp/1")

            assert [(m, s) for m, s, _at in store.responses((b"\x01" * 20).hex())] == [
                ((b"\x01" * 16).hex(), ACK)
            ]
        finally:
            await proc.shutdown()

    async def test_a_process_taking_over_during_a_loss_of_communications_does_not_fall_back_again(
        self,
    ):
        store = MemoryEventStateStore()
        lfdi = b"\x01" * 20
        derc = _derc(1, _now() - 5)
        first = RecordingDispatcher()
        a = EventProcessor(
            _http(),
            _state("/edev/1", lfdi, [derc]),
            first,
            asyncio.Event(),
            comms_loss=CommsLossState(),
            state_store=store,
        )
        await a.process_controls("/derp/1")
        await a.enter_comms_loss()
        await a.release_devices(["/edev/1"])
        await a.shutdown()
        assert first.defaults == ["/edev/1"]

        second = RecordingDispatcher()
        lost = CommsLossState(active=True)
        b = EventProcessor(
            _http(),
            _state("/edev/1", lfdi, [derc]),
            second,
            asyncio.Event(),
            comms_loss=lost,
            state_store=store,
        )
        try:
            b.load_devices(["/edev/1"])
            await b.process_controls("/derp/1")
            await b.resume_devices(["/edev/1"])

            record = b._store.get(b"\x01" * 16)
            assert record is not None
            assert record.opted_out is True
            assert b._device_resume_after("/edev/1") == record.end
            assert second.controls == []
            assert second.defaults == []
        finally:
            await b.shutdown()

    async def test_a_running_event_the_device_never_received_is_applied_after_take_over(self):
        store = MemoryEventStateStore()
        lfdi = b"" * 20
        derc = _derc(1, _now() - 5)
        a = EventProcessor(
            _http(),
            _state("/edev/1", lfdi, [derc]),
            NullDispatcher(),
            asyncio.Event(),
            state_store=store,
        )
        await a.process_controls("/derp/1")
        await a.release_devices(["/edev/1"])
        await a.shutdown()
        # The process stopped after the event went active and before the device got it.
        snapshot = store.load_device("/edev/1")
        assert snapshot is not None
        snapshot["events"][0]["applied"] = False
        store.save_device("/edev/1", snapshot)
        kept = [r for r in store.responses(lfdi.hex()) if r[1] != ACTIVE]
        store._responses[lfdi.hex()] = kept

        dispatcher = RecordingDispatcher()
        http = _http()
        b = EventProcessor(
            http, _state("/edev/1", lfdi, [derc]), dispatcher, asyncio.Event(), state_store=store
        )
        try:
            b.load_devices(["/edev/1"])
            await b.process_controls("/derp/1")
            await b.resume_devices(["/edev/1"])
            await b.resume_devices(["/edev/1"])

            assert dispatcher.controls == [("/edev/1", (b"" * 16).hex().upper())]
            statuses = [call.args[1].status for call in http.post.await_args_list]
            assert statuses == [ACTIVE]
        finally:
            await b.shutdown()
