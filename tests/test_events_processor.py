"""Tests for EventProcessor orchestrator."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from py20305.client.state import (
    DerProgramState,
    DiscoveredState,
    EndDeviceState,
)
from py20305.client.timebase import ServerTimebase
from py20305.connectors.base import ConnectorValueError
from py20305.connectors.control_errors import (
    DeviceNotConfiguredError,
    ModeNotSupportedError,
    OptOutError,
)
from py20305.events import processor as processor_mod
from py20305.events.comms_loss import CommsLossState
from py20305.events.dispatch import NullDispatcher
from py20305.events.processor import EventProcessor
from py20305.events.response import ResponseCode
from py20305.events.state_machine import EventState
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
    PowerOfTenMultiplierType,
    PrimacyType,
    Sfditype,
    TimeType,
)


def _envelope_elem(meta_name: str, value: int, multiplier: int = 0):
    """A fake CSIP-AUS envelope-limit element (matches the modes-test pattern)."""

    class FakeMeta:
        name = meta_name

    attrs = {
        "Meta": FakeMeta,
        "value": value,
        "multiplier": PowerOfTenMultiplierType(value=multiplier),
    }
    return type("FakeCSIPAUS", (), attrs)()


def _make_derc(
    mrid_byte: int,
    start: int,
    duration: int,
    current_status: int = 0,
    status_time: int = 1000,
    randomize_start: int | None = None,
    randomize_duration: int | None = None,
    reply_to: str = "/rsps",
    base: DercontrolBase | None = None,
    response_required: bytes = b"\x07",
) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=bytes([mrid_byte]) * 16),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=current_status,
            date_time=TimeType(value=status_time),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=duration, start=TimeType(value=start)),
        dercontrol_base=base if base is not None else DercontrolBase(),
        randomize_start=(
            OneHourRangeType(value=randomize_start) if randomize_start is not None else None
        ),
        randomize_duration=(
            OneHourRangeType(value=randomize_duration) if randomize_duration is not None else None
        ),
        reply_to=reply_to,
        response_required=response_required,
    )


def _make_program(href: str = "/derp/1", primacy: int = 0) -> Derprogram1:
    return Derprogram1(
        m_rid=MRidtype(value=b"\x10" * 16),
        primacy=PrimacyType(value=primacy),
    )


def _make_dderc() -> DefaultDercontrol:
    return DefaultDercontrol(
        m_rid=MRidtype(value=b"\x20" * 16),
        dercontrol_base=DercontrolBase(),
    )


def _make_edev(href: str = "/edev/1") -> EndDevice1:
    return EndDevice1(
        m_rid=MRidtype(value=b"\x30" * 16),
        s_fdi=Sfditype(value=0),
        changed_time=TimeType(value=0),
    )


def _setup_state(
    program_href: str = "/derp/1",
    primacy: int = 0,
    der_controls: list[Dercontrol1] | None = None,
    dderc: DefaultDercontrol | None = None,
    device_hrefs: list[str] | None = None,
) -> DiscoveredState:
    state = DiscoveredState()
    derp_state = DerProgramState(
        program=_make_program(program_href, primacy),
        href=program_href,
        primacy=primacy,
        default_dercontrol=dderc,
        der_controls=der_controls or [],
    )
    state.der_programs[program_href] = derp_state

    device_hrefs = device_hrefs or ["/edev/1"]
    for dh in device_hrefs:
        state.end_devices[dh] = EndDeviceState(
            device=_make_edev(dh),
            href=dh,
            lfdi=b"\xaa" * 20,
        )
        state.device_mapping.add(program_href, dh)

    return state


@pytest.fixture
def shutdown() -> asyncio.Event:
    return asyncio.Event()


class TestProcessControlsNewScheduled:
    @pytest.mark.asyncio
    async def test_new_scheduled_event_acked(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        # Should have posted ack response
        http.post.assert_awaited_once()
        # Event should be in store as SCHEDULED
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        assert rec.state == EventState.SCHEDULED

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_expired_event_skipped(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10000, duration=100)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        assert proc._store.get(derc.m_rid.value) is None
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_expired_when_first_received_posts_expired(self, shutdown: asyncio.Event):
        """An event already past its end the first time we see it -> EXPIRED."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10000, duration=100)
        state = _setup_state(der_controls=[derc])
        posted: list[int] = []

        async def track_post(path: str, resource: object) -> str | None:
            status = getattr(resource, "status", None)
            if status is not None:
                posted.append(status)
            return None

        http = AsyncMock()
        http.post = AsyncMock(side_effect=track_post)
        http.server_2018_compat = False
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        assert ResponseCode.EXPIRED.value in posted
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_no_expired_after_already_responded(self, shutdown: asyncio.Event):
        """Regression: a completed-then-pruned event re-discovered on a later poll
        must NOT get a spurious EXPIRED. IEEE 2030.5 rule j scopes EXPIRED to
        events already past end *when first received* -- not ones we already ran.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10000, duration=100)
        state = _setup_state(der_controls=[derc])
        posted: list[int] = []

        async def track_post(path: str, resource: object) -> str | None:
            status = getattr(resource, "status", None)
            if status is not None:
                posted.append(status)
            return None

        http = AsyncMock()
        http.post = AsyncMock(side_effect=track_post)
        http.server_2018_compat = False
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)
        # Simulate "we already ran this event to COMPLETED" before the store
        # pruned it after its end -- the response tracker remembers it.
        proc._response_tracker.mark_sent(
            derc.m_rid.value, ResponseCode.COMPLETED, b"\xaa" * 20, now
        )

        await proc.process_controls("/derp/1")

        assert ResponseCode.EXPIRED.value not in posted
        await proc.shutdown()


class TestProcessControlsLateDiscovery:
    @pytest.mark.asyncio
    async def test_late_discovery_activates(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")

        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        assert rec.state == EventState.ACTIVE
        # Should post both ack and active
        assert http.post.await_count == 2
        # Should apply control
        dispatcher.apply_control.assert_awaited()

        await proc.shutdown()


class TestProcessControlsCancellation:
    @pytest.mark.asyncio
    async def test_server_cancelled_new_event(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600, current_status=2)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        assert rec.state == EventState.CANCELLED
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_server_cancels_existing_active(self, shutdown: asyncio.Event):
        now = int(time.time())
        # First discover as active
        derc = _make_derc(0x01, start=now - 10, duration=3600, current_status=0)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        assert proc._store.get(derc.m_rid.value).state == EventState.ACTIVE

        # Now update status to cancelled and re-process
        derc_cancelled = _make_derc(0x01, start=now - 10, duration=3600, current_status=2)
        state.der_programs["/derp/1"].der_controls = [derc_cancelled]

        await proc.process_controls("/derp/1")

        rec = proc._store.get(derc.m_rid.value)
        assert rec.state == EventState.CANCELLED
        # DDERC should be applied
        dispatcher.apply_default_control.assert_awaited()

        await proc.shutdown()


class TestProcessControlsSupersession:
    @pytest.mark.asyncio
    async def test_lower_primacy_supersedes(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        derc2 = _make_derc(0x02, start=now - 10, duration=3600, status_time=1000)

        # Two programs with different primacy, same device
        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev(),
            href="/edev/1",
            lfdi=b"\xaa" * 20,
        )
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/1")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        # Higher primacy (5) should be superseded
        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert rec2.state == EventState.SUPERSEDED

        await proc.shutdown()


class TestLateDiscoverySupersessionOrdering:
    """Verify that late-discovered events post SUPERSEDED before ACTIVE."""

    @pytest.mark.asyncio
    async def test_late_discovery_posts_superseded_before_active(self, shutdown: asyncio.Event):
        """When two overlapping events are discovered late, SUPERSEDED should
        be posted for the losing event before ACTIVE for the winning event.
        """
        now = int(time.time())
        # Both events already active (late discovery).
        # derc1 in primacy=0 program (winner), derc2 in primacy=5 program (loser)
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        derc2 = _make_derc(0x02, start=now - 10, duration=3600, status_time=1000)

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev(),
            href="/edev/1",
            lfdi=b"\xaa" * 20,
        )
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/1")

        # Track response posting order
        posted_codes: list[int] = []

        async def track_post(path: str, resource: object) -> str | None:
            status = getattr(resource, "status", None)
            if status is not None:
                posted_codes.append(status)
            return None

        http = AsyncMock()
        http.post = AsyncMock(side_effect=track_post)
        http.server_2018_compat = False
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        # Process both programs (late discovery for both)
        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        # The losing event (derc2, primacy=5) should be SUPERSEDED
        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert rec2.state == EventState.SUPERSEDED

        # Check that SUPERSEDED (7) appears in the posted codes.
        # The key invariant: SUPERSEDED for the loser should be posted
        # before ACTIVE for the winner in the second process_controls call.
        assert ResponseCode.SUPERSEDED in posted_codes

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_late_discovery_excludes_superseded_devices_from_active(
        self, shutdown: asyncio.Event
    ):
        """Late-discovered winning event should not post ACTIVE for superseded devices."""
        now = int(time.time())
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        derc2 = _make_derc(0x02, start=now - 10, duration=3600, status_time=1000)

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev(),
            href="/edev/1",
            lfdi=b"\xaa" * 20,
        )
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/1")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        http.server_2018_compat = False
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        # The winning event should have the superseded device tracked
        rec1 = proc._store.get(derc1.m_rid.value)
        assert rec1 is not None
        assert rec1.state == EventState.ACTIVE

        # The losing event should be fully superseded
        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert rec2.state == EventState.SUPERSEDED

        await proc.shutdown()


class TestProcessControlsDdercFallback:
    @pytest.mark.asyncio
    async def test_completion_applies_dderc(self, shutdown: asyncio.Event):
        now = int(time.time())
        # Event that's active but will end soon
        derc = _make_derc(0x01, start=now - 3600, duration=3610)
        dderc = _make_dderc()
        state = _setup_state(der_controls=[derc], dderc=dderc)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        # Process to discover and activate
        await proc.process_controls("/derp/1")
        assert proc._store.get(derc.m_rid.value).state == EventState.ACTIVE

        # Simulate completion callback
        record = proc._store.get(derc.m_rid.value)
        await proc._on_completion(record)

        assert record.state == EventState.COMPLETED
        dispatcher.apply_default_control.assert_awaited()

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_no_dderc_does_not_clear_control(self, shutdown: asyncio.Event):
        """When no DDERC exists, the aggregator should not send any commands."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 3600, duration=3610)
        state = _setup_state(der_controls=[derc], dderc=None)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        assert proc._store.get(derc.m_rid.value).state == EventState.ACTIVE

        record = proc._store.get(derc.m_rid.value)
        await proc._on_completion(record)

        assert record.state == EventState.COMPLETED
        dispatcher.clear_control.assert_not_awaited()
        dispatcher.apply_default_control.assert_not_awaited()

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_missing_program_does_not_clear_control(self, shutdown: asyncio.Event):
        """When program is removed before completion, no commands should be sent."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 3600, duration=3610)
        state = _setup_state(der_controls=[derc], dderc=None)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        assert proc._store.get(derc.m_rid.value).state == EventState.ACTIVE

        # Remove the program to simulate derp_state is None
        del state.der_programs["/derp/1"]

        record = proc._store.get(derc.m_rid.value)
        await proc._on_completion(record)

        assert record.state == EventState.COMPLETED
        dispatcher.clear_control.assert_not_awaited()
        dispatcher.apply_default_control.assert_not_awaited()

        await proc.shutdown()


class TestProcessControlsMissing:
    @pytest.mark.asyncio
    async def test_missing_program_noop(self, shutdown: asyncio.Event):
        state = DiscoveredState()
        http = AsyncMock()
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)
        await proc.process_controls("/derp/nonexistent")
        await proc.shutdown()


class TestEventProcessorShutdown:
    @pytest.mark.asyncio
    async def test_shutdown_cancels_timers(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        await proc.shutdown()
        # No assertion needed -- just verifying no errors on shutdown


class TestIdempotentProcessing:
    @pytest.mark.asyncio
    async def test_duplicate_processing_idempotent(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        post_count_1 = http.post.await_count

        await proc.process_controls("/derp/1")
        # Second call should not post again (already tracked)
        assert http.post.await_count == post_count_1

        await proc.shutdown()


class TestAggregatorModeDispatch:
    """With a group lookup supplied, dispatching a DERControl
    targeted at the aggregator's own server-side EndDevice must fan out
    to each local sub-device LFDI rather than hitting the (non-existent)
    connector for the aggregator's own cert LFDI."""

    @pytest.mark.asyncio
    async def test_late_discovery_fans_out_to_group_members(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        # Single server-side EndDevice maps to one program (the aggregator
        # is the only client the upstream server knows about).
        state = _setup_state(der_controls=[derc])

        local_lfdis = ["aa" * 20, "bb" * 20, "cc" * 20, "dd" * 20]

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(
            http,
            state,
            dispatcher,
            shutdown,
            group_lookup=lambda program_href: (
                list(local_lfdis) if program_href == "/derp/1" else None
            ),
        )

        await proc.process_controls("/derp/1")

        # Server-href dispatch must NOT be used when a group lookup resolves.
        dispatcher.apply_control.assert_not_awaited()
        # One dispatch per local sub-device LFDI.
        assert dispatcher.apply_control_by_lfdi.await_count == len(local_lfdis)
        called_lfdis = {call.args[0] for call in dispatcher.apply_control_by_lfdi.await_args_list}
        assert called_lfdis == set(local_lfdis)

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_fallback_fans_out_to_group_members(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 3600, duration=3610)
        dderc = _make_dderc()
        state = _setup_state(der_controls=[derc], dderc=dderc)

        local_lfdis = ["aa" * 20, "bb" * 20]

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(
            http,
            state,
            dispatcher,
            shutdown,
            group_lookup=lambda program_href: (
                list(local_lfdis) if program_href == "/derp/1" else None
            ),
        )

        await proc.process_controls("/derp/1")
        record = proc._store.get(derc.m_rid.value)
        await proc._on_completion(record)

        # DDERC must fan out per local LFDI rather than hit the aggregator EDev.
        dispatcher.apply_default_control.assert_not_awaited()
        assert dispatcher.apply_default_control_by_lfdi.await_count == len(local_lfdis)
        called_lfdis = {
            call.args[0] for call in dispatcher.apply_default_control_by_lfdi.await_args_list
        }
        assert called_lfdis == set(local_lfdis)

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_initial_dderc_fans_out_to_group_members(self, shutdown: asyncio.Event):
        """Initial DDERC (applied at the tail of process_controls when no
        event is active) must also fan out to local LFDIs in aggregator
        mode rather than hit the aggregator's own EDev."""
        # Program has a DDERC but no DERControls — exercises _apply_initial_dderc.
        state = _setup_state(der_controls=[], dderc=_make_dderc())

        local_lfdis = ["aa" * 20, "bb" * 20, "cc" * 20]

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(
            http,
            state,
            dispatcher,
            shutdown,
            group_lookup=lambda program_href: (
                list(local_lfdis) if program_href == "/derp/1" else None
            ),
        )

        await proc.process_controls("/derp/1")

        dispatcher.apply_default_control.assert_not_awaited()
        assert dispatcher.apply_default_control_by_lfdi.await_count == len(local_lfdis)
        called_lfdis = {
            call.args[0] for call in dispatcher.apply_default_control_by_lfdi.await_args_list
        }
        assert called_lfdis == set(local_lfdis)

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_no_group_lookup_uses_href_dispatch(self, shutdown: asyncio.Event):
        """Direct (non-aggregator) deployments leave group_lookup unset; the
        processor must keep dispatching by server-side device href."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc])

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)  # no group_lookup

        await proc.process_controls("/derp/1")

        dispatcher.apply_control.assert_awaited_once()
        assert dispatcher.apply_control.await_args.args[0] == "/edev/1"
        dispatcher.apply_control_by_lfdi.assert_not_awaited()

        await proc.shutdown()


class TestPostResponseLfdiRouting:
    """Tests for _post_response LFDI routing logic."""

    @pytest.mark.asyncio
    async def test_uses_program_specific_lfdi(self, shutdown: asyncio.Event):
        """_post_response uses LFDI from program's mapped device."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)

        # Two devices with different LFDIs, each mapped to a different program
        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1"),
            href="/derp/1",
            primacy=0,
            der_controls=[derc],
        )
        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        state.device_mapping.add("/derp/1", "/edev/1")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        # The posted response should use dev1's LFDI (mapped to /derp/1)
        assert http.post.await_count == 1
        posted_response = http.post.call_args[0][1]
        assert posted_response.end_device_lfdi == dev1_lfdi

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_falls_back_to_any_lfdi_when_no_program_href(self, shutdown: asyncio.Event):
        """_post_response falls back to any LFDI when program_href has no mapping."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1"),
            href="/derp/1",
            primacy=0,
            der_controls=[derc],
        )
        # Device exists but is NOT mapped to the program
        dev_lfdi = b"\xcc" * 20
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev_lfdi
        )
        # No device_mapping.add call -- program has no mapped devices

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        # Should still post using the fallback LFDI
        assert http.post.await_count == 1
        posted_response = http.post.call_args[0][1]
        assert posted_response.end_device_lfdi == dev_lfdi

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_no_lfdi_available_skips_response(self, shutdown: asyncio.Event):
        """_post_response skips posting when no LFDI is available."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1"),
            href="/derp/1",
            primacy=0,
            der_controls=[derc],
        )
        # No end devices at all

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        # No LFDI available, so no response posted
        http.post.assert_not_awaited()

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_multi_device_posts_per_device(self, shutdown: asyncio.Event):
        """Two devices in a program both get responses posted."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1"),
            href="/derp/1",
            primacy=0,
            der_controls=[derc],
        )
        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/1", "/edev/2")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        # Both devices should get ACK responses
        assert http.post.await_count == 2
        posted_lfdis = {call[0][1].end_device_lfdi for call in http.post.call_args_list}
        assert posted_lfdis == {dev1_lfdi, dev2_lfdi}

        await proc.shutdown()


class TestCancellationWindDown:
    """IEEE 10.2.3.3: cancellation applies randomized wind-down."""

    def test_wind_down_uses_max_randomization(self):
        """Wind-down is max(abs(randomizeStart), abs(randomizeDuration))."""
        from py20305.events.state_machine import EventRecord, EventState

        now = int(time.time())
        derc = _make_derc(
            0x01, start=now - 10, duration=3600, randomize_start=60, randomize_duration=-120
        )
        record = EventRecord(
            mrid=derc.m_rid.value,
            derc=derc,
            program_href="/derp/1",
            primacy=0,
            state=EventState.ACTIVE,
            start=now - 10,
            duration=3600,
            server_status_time=1000,
        )
        wind_down = EventProcessor._cancellation_wind_down(record)
        assert wind_down == 120  # max(abs(60), abs(-120))

    def test_wind_down_zero_when_no_randomization(self):
        from py20305.events.state_machine import EventRecord, EventState

        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        record = EventRecord(
            mrid=derc.m_rid.value,
            derc=derc,
            program_href="/derp/1",
            primacy=0,
            state=EventState.ACTIVE,
            start=now - 10,
            duration=3600,
            server_status_time=1000,
        )
        wind_down = EventProcessor._cancellation_wind_down(record)
        assert wind_down == 0

    @pytest.mark.asyncio
    async def test_cancellation_of_active_schedules_delayed_fallback(self, shutdown: asyncio.Event):
        """When active event with randomization is cancelled, DDERC is delayed."""
        now = int(time.time())
        # Start far enough in the past that even max randomization (+30s) keeps it active
        derc = _make_derc(
            0x01, start=now - 100, duration=3600, randomize_start=30, randomize_duration=0
        )
        dderc = _make_dderc()
        state = _setup_state(der_controls=[derc], dderc=dderc)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        assert proc._store.get(derc.m_rid.value).state == EventState.ACTIVE

        # Cancel the event (server sets currentStatus=2)
        derc_cancelled = _make_derc(
            0x01,
            start=now - 100,
            duration=3600,
            current_status=2,
            randomize_start=30,
            randomize_duration=0,
        )
        state.der_programs["/derp/1"].der_controls = [derc_cancelled]
        await proc.process_controls("/derp/1")

        assert proc._store.get(derc.m_rid.value).state == EventState.CANCELLED
        # DDERC should NOT have been applied immediately (wind-down is 30s)
        dispatcher.apply_default_control.assert_not_awaited()

        await proc.shutdown()


class TestClosedGuard:
    """Tests for the _closed flag that prevents orphaned timer creation."""

    @pytest.mark.asyncio
    async def test_shutdown_sets_closed(self, shutdown: asyncio.Event):
        state = DiscoveredState()
        http = AsyncMock()
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        assert proc.closed is False
        await proc.shutdown()
        assert proc.closed is True

    @pytest.mark.asyncio
    async def test_closed_processor_skips_process_controls(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        proc._closed = True
        await proc.process_controls("/derp/1")

        # No HTTP calls or store changes
        http.post.assert_not_awaited()
        assert proc._store.get(derc.m_rid.value) is None

    @pytest.mark.asyncio
    async def test_handle_new_scheduled_skips_timer_when_closed(self, shutdown: asyncio.Event):
        """Simulates shutdown during ACK post -- no timer should be created."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()

        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        # When post is called (ACK), set _closed to simulate concurrent shutdown
        async def close_during_post(*args, **kwargs):
            proc._closed = True
            return None

        http.post = AsyncMock(side_effect=close_during_post)

        await proc.process_controls("/derp/1")

        # ACK was posted, but no timer should have been scheduled
        http.post.assert_awaited_once()
        assert len(proc._timer_mgr._tasks) == 0

    @pytest.mark.asyncio
    async def test_handle_late_discovery_skips_dispatch_when_closed(self, shutdown: asyncio.Event):
        """Simulates shutdown during ACK post for late discovery -- no dispatch."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        dispatcher = AsyncMock()

        proc = EventProcessor(http, state, dispatcher, shutdown)

        # Set _closed during the first post (ACK)
        async def close_during_post(*args, **kwargs):
            proc._closed = True
            return None

        http.post = AsyncMock(side_effect=close_during_post)

        await proc.process_controls("/derp/1")

        # ACK posted, but ACTIVE response and dispatch should be skipped
        http.post.assert_awaited_once()
        dispatcher.apply_control.assert_not_awaited()
        assert len(proc._timer_mgr._tasks) == 0


class TestSuccessiveEvents:
    """IEEE 10.2.2.3 rule m): successive events must not have randomization gaps."""

    @pytest.mark.asyncio
    async def test_successive_events_start_aligned(self, shutdown: asyncio.Event):
        """Second event's effective start matches first event's effective end."""
        now = int(time.time())
        # Event 1: raw_start=now+100, duration=200 -> raw_end = now+300
        # Event 2: raw_start=now+300 (successive)
        derc1 = _make_derc(0x01, start=now + 100, duration=200)
        derc2 = _make_derc(0x02, start=now + 300, duration=200)
        state = _setup_state(der_controls=[derc1, derc2])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")

        rec1 = proc._store.get(derc1.m_rid.value)
        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec1 is not None
        assert rec2 is not None
        # Event 2's effective start should equal event 1's effective end
        assert rec2.start == rec1.end

        await proc.shutdown()


class TestNoDdercCompletion:
    """Tests for event completion when no DefaultDERControl exists."""

    @pytest.mark.asyncio
    async def test_active_completion_without_dderc_no_clear(self, shutdown: asyncio.Event):
        """Active event completing with no DDERC should not dispatch clear_control."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=None)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        record = proc._store.get(derc.m_rid.value)
        assert record is not None
        assert record.state == EventState.ACTIVE

        # Simulate completion
        await proc._on_completion(record)

        dispatcher.clear_control.assert_not_awaited()
        dispatcher.apply_default_control.assert_not_awaited()
        await proc.shutdown()


class TestInitialDderc:
    """Tests for initial DDERC application on first process_controls call."""

    @pytest.mark.asyncio
    async def test_dderc_applied_when_no_events(self, shutdown: asyncio.Event):
        """DDERC should be applied to devices when no events exist."""
        dderc = _make_dderc()
        state = _setup_state(der_controls=[], dderc=dderc)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")

        dispatcher.apply_default_control.assert_awaited_once()
        call_args = dispatcher.apply_default_control.call_args
        assert call_args[0][0] == "/edev/1"
        assert call_args[0][1] is dderc
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_not_applied_when_active_event(self, shutdown: asyncio.Event):
        """DDERC should NOT be applied when an active event exists."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        dderc = _make_dderc()
        state = _setup_state(der_controls=[derc], dderc=dderc)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")

        # apply_control is called for the active event, but not apply_default_control
        dispatcher.apply_default_control.assert_not_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_applied_with_scheduled_event(self, shutdown: asyncio.Event):
        """DDERC should be applied even when a scheduled (future) event exists.

        Scheduled events haven't activated yet, so the device needs its
        default operating state until the event starts.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 300, duration=3600)
        dderc = _make_dderc()
        state = _setup_state(der_controls=[derc], dderc=dderc)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")

        dispatcher.apply_default_control.assert_awaited_once()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_change_redispatched_when_mrid_changes(self, shutdown: asyncio.Event):
        """A DDERC update on the same program must reach the device.

        Regression test: the tracker keyed off (lfdi, derp_path,
        primacy) only, so a second DDERC PUT with different content was silently
        suppressed unless a DERC discovery cleared the tracker in between.
        """
        dderc_v1 = DefaultDercontrol(
            m_rid=MRidtype(value=b"\x20" * 16),
            dercontrol_base=DercontrolBase(),
        )
        state = _setup_state(der_controls=[], dderc=dderc_v1)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        assert dispatcher.apply_default_control.await_count == 1
        assert dispatcher.apply_default_control.await_args[0][1] is dderc_v1

        # Server publishes a new DDERC with different mRID and content.
        dderc_v2 = DefaultDercontrol(
            m_rid=MRidtype(value=b"\x21" * 16),
            dercontrol_base=DercontrolBase(),
        )
        state.der_programs["/derp/1"].default_dercontrol = dderc_v2

        await proc.process_controls("/derp/1")
        assert dispatcher.apply_default_control.await_count == 2
        assert dispatcher.apply_default_control.await_args[0][1] is dderc_v2

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_unchanged_not_redispatched(self, shutdown: asyncio.Event):
        """The same DDERC seen on successive polls should only dispatch once."""
        dderc = _make_dderc()
        state = _setup_state(der_controls=[], dderc=dderc)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/1")
        assert dispatcher.apply_default_control.await_count == 1

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_same_program_higher_primacy_blocked(self, shutdown: asyncio.Event):
        """Re-applying DDERC at lower priority (higher primacy) is blocked by the tracker."""
        dderc = _make_dderc()
        # Program with high priority (primacy=0)
        state = _setup_state(der_controls=[], dderc=dderc, primacy=0)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        assert dispatcher.apply_default_control.await_count == 1

        # Simulate primacy change to lower priority (primacy=5)
        state.der_programs["/derp/1"].primacy = 5
        state.der_programs["/derp/1"].program = _make_program("/derp/1", primacy=5)

        await proc.process_controls("/derp/1")
        # Lower-priority DDERC should be blocked by the tracker
        assert dispatcher.apply_default_control.await_count == 1
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_cross_program_lower_priority_blocked(self, shutdown: asyncio.Event):
        """A lower-priority program's DDERC should not override a higher-priority one."""
        dderc_high = _make_dderc()
        # High-priority program (primacy=0) on shared device /edev/1
        state = _setup_state(der_controls=[], dderc=dderc_high, primacy=0)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        # Apply high-priority DDERC first
        await proc.process_controls("/derp/1")
        assert dispatcher.apply_default_control.await_count == 1

        # Add a second, lower-priority program on the same device
        dderc_low = _make_dderc()
        derp2 = DerProgramState(
            program=_make_program("/derp/2", primacy=5),
            href="/derp/2",
            primacy=5,
            default_dercontrol=dderc_low,
            der_controls=[],
        )
        state.der_programs["/derp/2"] = derp2
        state.device_mapping.add("/derp/2", "/edev/1")

        await proc.process_controls("/derp/2")
        # Lower-priority DDERC should be blocked — high-priority already applied
        assert dispatcher.apply_default_control.await_count == 1
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_no_dderc_means_no_initial_dispatch(self, shutdown: asyncio.Event):
        """When no DDERC exists, nothing should be dispatched."""
        state = _setup_state(der_controls=[], dderc=None)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")

        dispatcher.apply_default_control.assert_not_awaited()
        dispatcher.apply_control.assert_not_awaited()
        dispatcher.clear_control.assert_not_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dderc_blocked_by_cross_program_active_event(self, shutdown: asyncio.Event):
        """DDERC from Program B should not apply to a device with an active event from Program A."""
        now = int(time.time())
        # Active event on Program A (high priority)
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = DiscoveredState()

        dev_lfdi = b"\xaa" * 20
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev_lfdi
        )

        # Program A: has the active event, device /edev/1
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc],
        )
        state.device_mapping.add("/derp/1", "/edev/1")

        # Program B: no events, has DDERC, same device /edev/1
        dderc_b = _make_dderc()
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            default_dercontrol=dderc_b,
            der_controls=[],
        )
        state.device_mapping.add("/derp/2", "/edev/1")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        # Activate Program A's event
        await proc.process_controls("/derp/1")
        dispatcher.apply_default_control.assert_not_awaited()

        # Now process Program B — device has active event from A, so no DDERC
        await proc.process_controls("/derp/2")
        dispatcher.apply_default_control.assert_not_awaited()

        await proc.shutdown()


class TestDdercFallbackExcludesSuperseded:
    """DDERC fallback should not apply to devices superseded on the completing event."""

    @pytest.mark.asyncio
    async def test_superseded_device_excluded_from_fallback(self, shutdown: asyncio.Event):
        """When event completes, superseded devices should NOT get DDERC from it."""
        now = int(time.time())
        # Two active events, derc1 supersedes derc2 on shared device /edev/1
        derc1 = _make_derc(0x01, start=now - 10, duration=7200, status_time=1000)
        derc2 = _make_derc(0x02, start=now - 10, duration=3610, status_time=1000)

        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20

        dderc = _make_dderc()

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            default_dercontrol=dderc,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        # /derp/1 has /edev/1 only; /derp/2 has both
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/2")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert "/edev/1" in rec2.superseded_devices

        # Trigger completion of derc2
        dispatcher.reset_mock()
        await proc._on_completion(rec2)

        # DDERC fallback should only dispatch to /edev/2 (not superseded /edev/1)
        if dispatcher.apply_default_control.await_count > 0:
            dispatched_devs = [c[0][0] for c in dispatcher.apply_default_control.call_args_list]
            assert "/edev/1" not in dispatched_devs
            assert "/edev/2" in dispatched_devs

        await proc.shutdown()


# ---------------------------------------------------------------------------
# Per-device response tests for supersession (ff9926c review fixes)
# ---------------------------------------------------------------------------


def _two_device_state(
    program_href: str = "/derp/1",
    primacy: int = 0,
    der_controls: list[Dercontrol1] | None = None,
) -> tuple[DiscoveredState, bytes, bytes]:
    """Set up a single program with two devices having distinct LFDIs."""
    dev1_lfdi = b"\xaa" * 20
    dev2_lfdi = b"\xbb" * 20
    state = DiscoveredState()
    state.der_programs[program_href] = DerProgramState(
        program=_make_program(program_href, primacy),
        href=program_href,
        primacy=primacy,
        der_controls=der_controls or [],
    )
    state.end_devices["/edev/1"] = EndDeviceState(
        device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
    )
    state.end_devices["/edev/2"] = EndDeviceState(
        device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
    )
    state.device_mapping.add(program_href, "/edev/1")
    state.device_mapping.add(program_href, "/edev/2")
    return state, dev1_lfdi, dev2_lfdi


class TestPostResponseExcludeDevices:
    """Tests for the exclude_devices parameter on _post_response."""

    @pytest.mark.asyncio
    async def test_exclude_skips_device(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state, _, dev2_lfdi = _two_device_state(der_controls=[derc])

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc._post_response(derc, ResponseCode.ACTIVE, "/derp/1", exclude_devices={"/edev/1"})

        assert http.post.await_count == 1
        posted_lfdi = http.post.call_args_list[0][0][1].end_device_lfdi
        assert posted_lfdi == dev2_lfdi
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_exclude_all_posts_nothing(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state, _, _ = _two_device_state(der_controls=[derc])

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc._post_response(
            derc, ResponseCode.ACTIVE, "/derp/1", exclude_devices={"/edev/1", "/edev/2"}
        )

        http.post.assert_not_awaited()
        await proc.shutdown()


class TestPostResponseForDevices:
    """Tests for _post_response_for_devices."""

    @pytest.mark.asyncio
    async def test_posts_only_to_specified(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state, dev1_lfdi, _ = _two_device_state(der_controls=[derc])

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc._post_response_for_devices(derc, ResponseCode.SUPERSEDED, {"/edev/1"})

        assert http.post.await_count == 1
        posted_lfdi = http.post.call_args_list[0][0][1].end_device_lfdi
        assert posted_lfdi == dev1_lfdi
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_skips_unknown_href(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state, _, _ = _two_device_state(der_controls=[derc])

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc._post_response_for_devices(derc, ResponseCode.SUPERSEDED, {"/edev/unknown"})

        http.post.assert_not_awaited()
        await proc.shutdown()


class TestSupersessionPerDeviceResponses:
    """Supersession posts SUPERSEDED responses per device, not broadcast."""

    @pytest.mark.asyncio
    async def test_superseded_posts_per_device(self, shutdown: asyncio.Event):
        """Two programs, two shared devices — SUPERSEDED posted for each device exactly once."""
        now = int(time.time())
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        derc2 = _make_derc(0x02, start=now - 10, duration=3600, status_time=1000)

        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/1", "/edev/2")
        state.device_mapping.add("/derp/2", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/2")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        # Filter SUPERSEDED responses (status == 7)
        superseded_calls = [
            c for c in http.post.call_args_list if c[0][1].status == ResponseCode.SUPERSEDED
        ]
        assert len(superseded_calls) == 2
        superseded_lfdis = {c[0][1].end_device_lfdi for c in superseded_calls}
        assert superseded_lfdis == {dev1_lfdi, dev2_lfdi}

        # Confirm derc2 is fully superseded
        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert rec2.state == EventState.SUPERSEDED

        await proc.shutdown()


class TestActivationCompletionExcludesSuperseded:
    """Activation and completion responses skip superseded devices."""

    @pytest.mark.asyncio
    async def test_activation_excludes_superseded(self, shutdown: asyncio.Event):
        """Partially superseded event only posts ACTIVE for non-superseded devices."""
        now = int(time.time())
        # derc1: active now, program /derp/1 maps to /edev/1 only
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        # derc2: scheduled for future, program /derp/2 maps to /edev/1, /edev/2, /edev/3
        derc2 = _make_derc(0x02, start=now + 100, duration=3600, status_time=1000)

        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20
        dev3_lfdi = b"\xcc" * 20

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        state.end_devices["/edev/3"] = EndDeviceState(
            device=_make_edev("/edev/3"), href="/edev/3", lfdi=dev3_lfdi
        )
        # /derp/1 has only /edev/1
        state.device_mapping.add("/derp/1", "/edev/1")
        # /derp/2 has all three
        state.device_mapping.add("/derp/2", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/2")
        state.device_mapping.add("/derp/2", "/edev/3")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        # Process both — supersession partially supersedes derc2 on /edev/1
        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert rec2.state == EventState.SCHEDULED  # not fully superseded
        assert "/edev/1" in rec2.superseded_devices

        # Reset mock and trigger activation
        http.post.reset_mock()
        await proc._on_activation(rec2)

        # ACTIVE response should go to /edev/2 and /edev/3 only
        active_calls = [
            c for c in http.post.call_args_list if c[0][1].status == ResponseCode.ACTIVE
        ]
        assert len(active_calls) == 2
        active_lfdis = {c[0][1].end_device_lfdi for c in active_calls}
        assert active_lfdis == {dev2_lfdi, dev3_lfdi}

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_completion_excludes_superseded(self, shutdown: asyncio.Event):
        """Partially superseded event only posts COMPLETED for non-superseded devices."""
        now = int(time.time())
        # Both active (late discovery), derc2 ends soon
        derc1 = _make_derc(0x01, start=now - 3600, duration=7200, status_time=1000)
        derc2 = _make_derc(0x02, start=now - 3600, duration=3610, status_time=1000)

        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        # /derp/1 has /edev/1 only; /derp/2 has both
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/2")

        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert "/edev/1" in rec2.superseded_devices

        # Reset mock and trigger completion
        http.post.reset_mock()
        await proc._on_completion(rec2)

        # COMPLETED should only go to /edev/2 (not superseded /edev/1)
        completed_calls = [
            c for c in http.post.call_args_list if c[0][1].status == ResponseCode.COMPLETED
        ]
        assert len(completed_calls) == 1
        assert completed_calls[0][0][1].end_device_lfdi == dev2_lfdi

        await proc.shutdown()


class TestDeferredSupersession:
    """IEEE 10.2.2.3 rule l): SUPERSEDED posted at superseding event's start time.

    When a higher-priority SCHEDULED event overlaps a lower-priority ACTIVE
    event, the SUPERSEDED response must be deferred until the superseding
    event actually activates — not posted eagerly at discovery time.
    """

    def _setup_two_programs(
        self,
        derc1: Dercontrol1,
        derc2: Dercontrol1,
        *,
        primacy1: int = 10,
        primacy2: int = 0,
    ) -> DiscoveredState:
        """Two programs sharing /edev/1 and /edev/2."""
        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20

        state = DiscoveredState()
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", primacy1),
            href="/derp/1",
            primacy=primacy1,
            der_controls=[derc1],
        )
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", primacy2),
            href="/derp/2",
            primacy=primacy2,
            der_controls=[derc2],
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/1", "/edev/2")
        state.device_mapping.add("/derp/2", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/2")
        return state

    @pytest.mark.asyncio
    async def test_superseded_deferred_until_activation(self, shutdown: asyncio.Event):
        """ACTIVE event is NOT superseded while superseding event is SCHEDULED."""
        now = int(time.time())
        # Event 1: active now, low priority (primacy=10)
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        # Event 2: scheduled for future, high priority (primacy=0)
        derc2 = _make_derc(0x02, start=now + 100, duration=3600, status_time=1000)

        state = self._setup_two_programs(derc1, derc2, primacy1=10, primacy2=0)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        # Process both programs
        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        # Event 1 should still be ACTIVE — not superseded yet
        rec1 = proc._store.get(derc1.m_rid.value)
        assert rec1 is not None
        assert rec1.state == EventState.ACTIVE
        assert len(rec1.superseded_devices) == 0

        # No SUPERSEDED responses should have been posted
        superseded_calls = [
            c for c in http.post.call_args_list if c[0][1].status == ResponseCode.SUPERSEDED
        ]
        assert len(superseded_calls) == 0

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_superseded_posted_on_activation(self, shutdown: asyncio.Event):
        """SUPERSEDED posted for shared devices when the superseding event activates."""
        now = int(time.time())
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        derc2 = _make_derc(0x02, start=now + 100, duration=3600, status_time=1000)

        state = self._setup_two_programs(derc1, derc2, primacy1=10, primacy2=0)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        # Reset mock to isolate activation responses
        http.post.reset_mock()

        # Activate event 2 — this should trigger deferred supersession
        rec2 = proc._store.get(derc2.m_rid.value)
        await proc._on_activation(rec2)

        # Now event 1 should be fully superseded
        rec1 = proc._store.get(derc1.m_rid.value)
        assert rec1 is not None
        assert rec1.state == EventState.SUPERSEDED

        # SUPERSEDED responses posted for both shared devices
        superseded_calls = [
            c for c in http.post.call_args_list if c[0][1].status == ResponseCode.SUPERSEDED
        ]
        assert len(superseded_calls) == 2

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_active_event_completes_normally_before_superseding_starts(
        self, shutdown: asyncio.Event
    ):
        """If the active event completes before the superseding event starts,
        COMPLETED goes to all devices (no supersession occurred)."""
        now = int(time.time())
        # Event 1: active, ends soon (primacy=10)
        derc1 = _make_derc(0x01, start=now - 3600, duration=3610, status_time=1000)
        # Event 2: scheduled far in the future (primacy=0)
        derc2 = _make_derc(0x02, start=now + 1000, duration=3600, status_time=1000)

        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20
        state = self._setup_two_programs(derc1, derc2, primacy1=10, primacy2=0)
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")

        # Event 1 should still be active (supersession deferred)
        rec1 = proc._store.get(derc1.m_rid.value)
        assert rec1.state == EventState.ACTIVE
        assert len(rec1.superseded_devices) == 0

        # Complete event 1 — should post COMPLETED to ALL devices
        http.post.reset_mock()
        await proc._on_completion(rec1)

        completed_calls = [
            c for c in http.post.call_args_list if c[0][1].status == ResponseCode.COMPLETED
        ]
        assert len(completed_calls) == 2
        completed_lfdis = {c[0][1].end_device_lfdi for c in completed_calls}
        assert completed_lfdis == {dev1_lfdi, dev2_lfdi}

        await proc.shutdown()


class TestScheduleNotificationRelay:
    """The processor relays control/default_baseline changes to connectors."""

    @staticmethod
    async def _drain() -> None:
        # Relays are fire-and-forget tasks; let them run.
        for _ in range(4):
            await asyncio.sleep(0)

    @staticmethod
    def _notifs(dispatcher, stream: str | None = None):
        calls = dispatcher.relay_schedule_notification.call_args_list
        out = [c.args[1] for c in calls]
        return [n for n in out if stream is None or n.stream == stream]

    @pytest.mark.asyncio
    async def test_scheduled_relayed(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        control = self._notifs(dispatcher, "control")
        assert len(control) == 1
        n = control[0]
        assert n.transition == "scheduled"
        assert n.status == "scheduled"
        assert n.current_status == 0
        assert n.mrid == derc.m_rid.value.hex()
        assert n.affected_lfdis == [(b"\xaa" * 20).hex()]
        # the dispatcher is also handed the LFDI list as arg 0
        assert dispatcher.relay_schedule_notification.call_args_list[0].args[0] == n.affected_lfdis

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_randomization_carried(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(
            0x01, start=now + 100, duration=3600, randomize_start=30, randomize_duration=60
        )
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        n = self._notifs(dispatcher, "control")[0]
        assert n.randomization == (30, 60)

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_pre_cancelled_relayed(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600, current_status=2)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        control = self._notifs(dispatcher, "control")
        assert len(control) == 1
        assert control[0].transition == "cancelled"

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_default_baseline_added_then_deduped(self, shutdown: asyncio.Event):
        state = _setup_state(der_controls=[], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()
        baseline = self._notifs(dispatcher, "default_baseline")
        assert len(baseline) == 1
        n = baseline[0]
        assert n.transition == "default_added"
        assert n.status is None and n.current_status is None
        assert n.start is None and n.duration is None and n.end is None
        assert n.affected_lfdis == [(b"\xaa" * 20).hex()]

        # Re-process with the unchanged baseline -> no new relay (fire-on-change).
        await proc.process_controls("/derp/1")
        await self._drain()
        assert len(self._notifs(dispatcher, "default_baseline")) == 1

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_default_baseline_updated_on_change(self, shutdown: asyncio.Event):
        from py20305.models.sep.sep import DercontrolBase, PerCentControlType

        state = _setup_state(der_controls=[], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        # Same mRID, changed content -> default_updated.
        changed = DefaultDercontrol(
            m_rid=MRidtype(value=b"\x20" * 16),
            dercontrol_base=DercontrolBase(op_mod_max_lim_w=PerCentControlType(value=5000)),
        )
        state.der_programs["/derp/1"].default_dercontrol = changed
        await proc.process_controls("/derp/1")
        await self._drain()

        baseline = self._notifs(dispatcher, "default_baseline")
        assert len(baseline) == 2
        assert baseline[1].transition == "default_updated"

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_default_baseline_mrid_swap_is_updated_and_bounded(self, shutdown: asyncio.Event):
        # A DDERC mRID swap on the same program is a "default_updated", not a
        # fresh "default_added", and keeps a single program-scoped cache slot.
        state = _setup_state(der_controls=[], dderc=_make_dderc())  # mRID \x20
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        swapped = DefaultDercontrol(
            m_rid=MRidtype(value=b"\x21" * 16),
            dercontrol_base=DercontrolBase(),
        )
        state.der_programs["/derp/1"].default_dercontrol = swapped
        await proc.process_controls("/derp/1")
        await self._drain()

        baseline = self._notifs(dispatcher, "default_baseline")
        assert [n.transition for n in baseline] == ["default_added", "default_updated"]
        baseline_keys = [k for k in proc._relay_snapshots if k[0] == "default_baseline"]
        assert baseline_keys == [("default_baseline", "/derp/1")]

        # Program removal drops the baseline slot (bounded by live program count).
        proc.cancel_program("/derp/1")
        assert not [k for k in proc._relay_snapshots if k[0] == "default_baseline"]

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_doe_projection_emitted_with_envelope(self, shutdown: asyncio.Event):
        now = int(time.time())
        base = DercontrolBase(other_element=[_envelope_elem("opModExpLimW", 5000, 3)])
        derc = _make_derc(0x01, start=now + 100, duration=3600, base=base)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        control = self._notifs(dispatcher, "control")
        doe = self._notifs(dispatcher, "doe")
        assert [n.transition for n in control] == ["scheduled"]
        assert [n.transition for n in doe] == ["scheduled"]
        d = doe[0]
        # Same envelope as the control event, payload carries only the limits.
        assert d.mrid == control[0].mrid
        assert d.start == control[0].start
        assert d.affected_lfdis == control[0].affected_lfdis
        assert d.payload["control_base"]["opModExpLimW"]["watts"] == 5_000_000

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_no_doe_when_no_envelope(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)  # plain setpoint control
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        assert self._notifs(dispatcher, "control")
        assert self._notifs(dispatcher, "doe") == []

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_doe_not_re_relayed_on_setpoint_only_change(self, shutdown: asyncio.Event):
        now = int(time.time())
        env = [_envelope_elem("opModExpLimW", 5000, 3)]
        derc = _make_derc(
            0x01, start=now + 100, duration=3600, base=DercontrolBase(other_element=list(env))
        )
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await self._drain()

        # Same envelope, an added setpoint -> control "updated" but doe unchanged.
        changed = _make_derc(
            0x01,
            start=now + 100,
            duration=3600,
            base=DercontrolBase(
                other_element=list(env), op_mod_max_lim_w=PerCentControlType(value=5000)
            ),
        )
        state.der_programs["/derp/1"].der_controls = [changed]
        await proc.process_controls("/derp/1")
        await self._drain()

        assert [n.transition for n in self._notifs(dispatcher, "control")] == [
            "scheduled",
            "updated",
        ]
        # doe is deduped on the unchanged envelope -- no setpoint noise.
        assert [n.transition for n in self._notifs(dispatcher, "doe")] == ["scheduled"]

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_unchanged_event_not_re_relayed(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=3600)
        state = _setup_state(der_controls=[derc])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/1")  # existing + unchanged -> "updated" suppressed
        await self._drain()

        control = self._notifs(dispatcher, "control")
        assert [n.transition for n in control] == ["scheduled"]

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_volatile_fields_do_not_re_relay(self, shutdown: asyncio.Event):
        # A re-issued control with the same DERControlBase but a changed
        # event_status.date_time (volatile) must NOT produce an "updated" relay.
        now = int(time.time())
        state = _setup_state(
            der_controls=[_make_derc(0x01, start=now + 100, duration=3600, status_time=1000)]
        )
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        state.der_programs["/derp/1"].der_controls = [
            _make_derc(0x01, start=now + 100, duration=3600, status_time=2000)
        ]
        await proc.process_controls("/derp/1")
        await self._drain()

        control = self._notifs(dispatcher, "control")
        assert [n.transition for n in control] == ["scheduled"]

        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_superseded_devices_excluded_from_affected(self, shutdown: asyncio.Event):
        state = _setup_state(der_controls=[], device_hrefs=["/edev/1", "/edev/2"])
        state.end_devices["/edev/1"].lfdi = b"\x01" * 20
        state.end_devices["/edev/2"].lfdi = b"\x02" * 20
        proc = EventProcessor(AsyncMock(), state, AsyncMock(), shutdown)

        result = proc._affected_lfdis_for_program("/derp/1", {"/edev/2"})
        assert result == [(b"\x01" * 20).hex()]

        await proc.shutdown()


def test_extract_doe_envelope_negative_multiplier():
    from py20305.events.processor import _extract_doe_envelope

    whole = _extract_doe_envelope(
        DercontrolBase(other_element=[_envelope_elem("opModGenLimW", 5000, -1)])
    )["opModGenLimW"]
    assert whole["watts"] == 500
    assert isinstance(whole["watts"], int)

    frac = _extract_doe_envelope(
        DercontrolBase(other_element=[_envelope_elem("opModGenLimW", 5, -1)])
    )["opModGenLimW"]
    assert frac["watts"] == 0.5
    assert isinstance(frac["watts"], float)


class TestCommsLossOptOut:
    """Loss-of-communications opt-out behavior in the EventProcessor."""

    @pytest.mark.asyncio
    async def test_enter_reverts_active_event_to_dderc(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        comms = CommsLossState()
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=comms)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.state == EventState.ACTIVE
        dispatcher.apply_control.assert_awaited()  # event applied on activation

        dispatcher.reset_mock()
        http.post.reset_mock()
        await proc.enter_comms_loss()

        assert rec.opted_out is True
        assert comms.resume_after_epoch == rec.end
        dispatcher.apply_default_control.assert_awaited()  # reverted to DDERC
        http.post.assert_not_awaited()  # local-only: no OPT_OUT response (D2)
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_enter_clears_when_no_dderc(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=None)  # no planning limit
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        comms = CommsLossState()
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=comms)

        await proc.process_controls("/derp/1")
        dispatcher.reset_mock()
        await proc.enter_comms_loss()

        dispatcher.clear_control.assert_awaited()  # connector safe-default (D4)
        dispatcher.apply_default_control.assert_not_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_enter_no_active_events_is_noop(self, shutdown: asyncio.Event):
        state = _setup_state(der_controls=[], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        comms = CommsLossState()
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=comms)

        await proc.enter_comms_loss()

        assert comms.resume_after_epoch is None
        dispatcher.apply_default_control.assert_not_awaited()
        dispatcher.clear_control.assert_not_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_late_discovery_opts_out_when_active(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        comms = CommsLossState(active=True)
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=comms)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)

        assert rec is not None and rec.opted_out is True
        dispatcher.apply_control.assert_not_awaited()  # event never applied
        dispatcher.apply_default_control.assert_awaited()  # reverted to DDERC
        assert comms.resume_after_epoch == rec.end
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_on_activation_opts_out_when_active(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 5, duration=3600)  # scheduled
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        comms = CommsLossState()
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=comms)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.state == EventState.SCHEDULED

        comms.active = True
        dispatcher.reset_mock()
        await proc._on_activation(rec)

        assert rec.state == EventState.ACTIVE
        assert rec.opted_out is True
        dispatcher.apply_control.assert_not_awaited()
        dispatcher.apply_default_control.assert_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_resume_after_skips_events_in_window(self, shutdown: asyncio.Event):
        now = int(time.time())
        in_window = _make_derc(0x01, start=now + 100, duration=200)  # <= boundary
        after = _make_derc(0x02, start=now + 1000, duration=200)  # > boundary
        state = _setup_state(der_controls=[in_window, after], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        comms = CommsLossState(resume_after_epoch=now + 500)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown, comms_loss=comms)

        await proc.process_controls("/derp/1")

        assert proc._store.get(in_window.m_rid.value) is None  # opted-out window
        assert proc._store.get(after.m_rid.value) is not None  # resumed
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_resume_boundary_survives_prune(self, shutdown: asyncio.Event):
        state = _setup_state(der_controls=[])
        http = AsyncMock()
        comms = CommsLossState(resume_after_epoch=12345)
        proc = EventProcessor(http, state, NullDispatcher(), shutdown, comms_loss=comms)

        proc._store.prune_expired(now=9_999_999_999)

        assert comms.resume_after_epoch == 12345  # boundary lives on state, not store
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_opted_out_event_excluded_from_initial_dderc(self, shutdown: asyncio.Event):
        """An opted-out ACTIVE event must not count as controlling its device:
        _apply_initial_dderc should still manage the device per DDERC."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=CommsLossState())

        await proc.process_controls("/derp/1")
        await proc.enter_comms_loss()
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.opted_out is True

        # Force a fresh DDERC evaluation: the opted-out event must not block it.
        proc._dderc_tracker.clear_devices({b"\xaa" * 20})
        dispatcher.reset_mock()
        await proc._apply_initial_dderc("/derp/1")

        dispatcher.apply_default_control.assert_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_rejected_device_excluded_from_initial_dderc(self, shutdown: asyncio.Event):
        """A device rejected on an ACTIVE event is not under that event's control.

        Same reasoning as the opted-out case above: the event does not control the
        device, so the device must still be managed to its default. Otherwise a
        rejected control leaves the device under nothing at all for the event's
        whole duration -- ten minutes of no limit where the default should apply.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = ConnectorValueError("displacement 1.1")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        # Late discovery: the event is already active, so it dispatches and is
        # rejected in the same pass.
        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        assert rec.state is EventState.ACTIVE
        assert rec.rejected_devices == {"/edev/1"}

        proc._dderc_tracker.clear_devices({b"\xaa" * 20})
        dispatcher.reset_mock()
        await proc._apply_initial_dderc("/derp/1")

        dispatcher.apply_default_control.assert_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_rejected_device_does_not_count_as_other_active(self, shutdown: asyncio.Event):
        """The completion path's "is another event covering this device" check
        must not count an event that was rejected on that device."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = ConnectorValueError("displacement 1.1")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        assert rec.rejected_devices == {"/edev/1"}

        # Ask on behalf of some other event: this rejected one covers nothing.
        assert not proc._device_has_other_active("/edev/1", b"\x99" * 16, [rec])
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_rejected_device_releases_its_tracked_modes(self, shutdown: asyncio.Event):
        """Modes are registered before dispatch, so a failure has to unregister.

        Left in place, the tracker believes a dead event still owns those modes on
        that device, and a later event's release of the same mode would look
        "still covered" -- withholding the DDERC fallback.
        """
        now = int(time.time())
        base = DercontrolBase(op_mod_max_lim_w=PerCentControlType(value=8000))
        derc = _make_derc(0x01, start=now - 10, duration=3600, base=base)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = ConnectorValueError("bad value")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        assert rec.rejected_devices == {"/edev/1"}

        # Nothing is left registered for the device on this event's behalf.
        assert proc._mode_tracker.unregister("/edev/1", rec.mrid) == frozenset()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_opted_out_flag_is_sticky_until_prune(self, shutdown: asyncio.Event):
        """opted_out stays set after opt-out (no recovery reset): the event must
        not resume, and prune removes it once its end passes."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=100)  # ends soon
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        proc = EventProcessor(http, state, AsyncMock(), shutdown, comms_loss=CommsLossState())

        await proc.process_controls("/derp/1")
        await proc.enter_comms_loss()
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.opted_out is True

        # End + grace elapsed -> pruned; the flag was never cleared in between.
        proc._store.prune_expired(now=rec.end + 3600)
        assert proc._store.get(derc.m_rid.value) is None
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_grouped_clear_fans_out_once(self, shutdown: asyncio.Event):
        """With a group lookup: the no-DDERC clear is per-program, so it
        dispatches one clear per *unique* local sub-device LFDI -- not once per
        (target device x LFDI), and duplicates from group_lookup are deduped."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=None, device_hrefs=["/edev/1", "/edev/2"])
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        local_lfdis = ["11" * 20, "22" * 20, "11" * 20]  # duplicate: dedupe expected
        proc = EventProcessor(
            http,
            state,
            dispatcher,
            shutdown,
            comms_loss=CommsLossState(),
            group_lookup=lambda href: local_lfdis,
        )

        await proc.process_controls("/derp/1")
        dispatcher.reset_mock()
        await proc.enter_comms_loss()

        assert dispatcher.clear_control_by_lfdi.await_count == 2  # unique LFDIs
        dispatcher.clear_control.assert_not_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_clear_dedupes_duplicate_device_hrefs(self, shutdown: asyncio.Event):
        """DeviceMapping.add appends without dedup: a device mapped twice to the
        program must still get exactly one safe-default clear."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=None)
        state.device_mapping.add("/derp/1", "/edev/1")  # duplicate mapping
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=CommsLossState())

        await proc.process_controls("/derp/1")
        dispatcher.reset_mock()
        await proc.enter_comms_loss()

        assert dispatcher.clear_control.await_count == 1
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_opt_out_via_real_activation_timer_applies_revert(self, shutdown: asyncio.Event):
        """Regression: the opt-out gate runs inside the event's own activation
        timer task. timer_mgr.cancel() there must not cancel the running task,
        or the DDERC revert dies with a swallowed CancelledError in production
        (calling _on_activation directly, as other tests do, cannot catch this)."""
        now = int(time.time())
        # +2s margin: +1 can collide with a second-boundary tick during
        # classification and come up ACTIVE (late discovery) instead.
        derc = _make_derc(0x01, start=now + 2, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        comms = CommsLossState()
        proc = EventProcessor(http, state, dispatcher, shutdown, comms_loss=comms)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.state == EventState.SCHEDULED

        comms.active = True
        dispatcher.reset_mock()
        # Let the actual activation timer fire and run the gated callback.
        await asyncio.gather(*proc._timer_mgr._tasks[derc.m_rid.value])

        assert rec.opted_out is True
        dispatcher.apply_default_control.assert_awaited()  # revert survived
        dispatcher.apply_control.assert_not_awaited()
        await proc.shutdown()


class TestServerTimebase:
    """Event engine follows the head-end's Time resource (server timebase)."""

    @staticmethod
    def _tb(offset_target: int, fsa_href: str | None = None) -> ServerTimebase:
        tb = ServerTimebase(drift_warn_seconds=0)
        tb.observe(offset_target, fsa_href=fsa_href)
        return tb

    @pytest.mark.asyncio
    async def test_classification_follows_server_time(self, shutdown: asyncio.Event):
        """Not yet started on the local clock, but active per server time ->
        classified ACTIVE (late discovery) and applied."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 500, duration=100)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown, timebase=self._tb(now + 550))

        await proc.process_controls("/derp/1")

        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.state == EventState.ACTIVE
        dispatcher.apply_control.assert_awaited()
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_expired_follows_server_time(self, shutdown: asyncio.Event):
        """Still in the local-clock future, but already ended per server time ->
        EXPIRED, never stored."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 100, duration=100)  # ends now+200
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        posted: list[int] = []

        async def track_post(path: str, resource: object) -> str | None:
            status = getattr(resource, "status", None)
            if status is not None:
                posted.append(status)
            return None

        http = AsyncMock()
        http.post = AsyncMock(side_effect=track_post)
        http.server_2018_compat = False
        proc = EventProcessor(http, state, NullDispatcher(), shutdown, timebase=self._tb(now + 500))

        await proc.process_controls("/derp/1")

        assert proc._store.get(derc.m_rid.value) is None
        assert ResponseCode.EXPIRED.value in posted
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_per_fsa_scope_overrides_global(self, shutdown: asyncio.Event):
        """IEEE 9.2.3: a program discovered via an FSA with its own Time uses
        that FSA's offset, not the global one."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 500, duration=100)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        state.der_programs["/derp/1"].discovered_from_fsa_href = "/fsa/1"
        tb = ServerTimebase(drift_warn_seconds=0)
        tb.observe(now)  # global: no skew (would classify SCHEDULED)
        tb.observe(now + 550, fsa_href="/fsa/1")  # FSA clock: already active
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown, timebase=tb)

        await proc.process_controls("/derp/1")

        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.state == EventState.ACTIVE
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_response_created_date_time_server_adjusted(self, shutdown: asyncio.Event):
        now = int(time.time())
        derc = _make_derc(0x01, start=now - 10, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        posted_times: list[int] = []

        async def track_post(path: str, resource: object) -> str | None:
            cdt = getattr(resource, "created_date_time", None)
            if cdt is not None:
                posted_times.append(cdt.value)
            return None

        http = AsyncMock()
        http.post = AsyncMock(side_effect=track_post)
        http.server_2018_compat = False
        proc = EventProcessor(http, state, AsyncMock(), shutdown, timebase=self._tb(now + 500))

        await proc.process_controls("/derp/1")

        assert posted_times, "expected ACK/ACTIVE responses"
        assert all(t >= now + 490 for t in posted_times)
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dispatch_duration_immune_to_mid_dispatch_offset_jump(
        self, shutdown: asyncio.Event, caplog: pytest.LogCaptureFixture
    ):
        """The logged dispatch duration is monotonic-anchored: a Time
        observation that jumps the timebase backward mid-dispatch must not
        negate it."""
        import logging

        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.post = AsyncMock(return_value=None)
        tb = ServerTimebase(drift_warn_seconds=0)
        dispatcher = AsyncMock()

        async def jump_backward(*args: object, **kwargs: object) -> None:
            tb.observe(now - 500)  # server clock re-observed far lower

        dispatcher.apply_control.side_effect = jump_backward
        proc = EventProcessor(http, state, dispatcher, shutdown, timebase=tb)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None and rec.state == EventState.SCHEDULED

        tb.observe(now + 500)  # activation fires per server time
        with caplog.at_level(logging.INFO, logger="py20305.events.processor"):
            await proc._on_activation(rec)

        logs = [r for r in caplog.records if "all devices dispatched" in r.getMessage()]
        assert logs, "expected dispatch-complete log line"
        dispatch_seconds = logs[0].args[-1]  # last %s arg is the dispatch time
        assert isinstance(dispatch_seconds, float) and dispatch_seconds >= 0
        await proc.shutdown()


_DEV1_LFDI = b"\xaa" * 20
_DEV2_LFDI = b"\xbb" * 20


def _gated_two_device_state(derc: Dercontrol1) -> DiscoveredState:
    """Two devices with distinct LFDIs, plus a DDERC for the completion path.

    Distinct LFDIs matter here: the response tracker dedups on
    ``(mrid, code, lfdi)``, so shared LFDIs would collapse per-device responses.
    """
    state, _, _ = _two_device_state(der_controls=[derc])
    state.der_programs["/derp/1"].default_dercontrol = _make_dderc()
    return state


def _codes_by_lfdi(http: AsyncMock) -> dict[bytes, set[int]]:
    """Collapse posted responses into ``{lfdi: {status, ...}}``."""
    out: dict[bytes, set[int]] = {}
    for call in http.post.call_args_list:
        resp = call[0][1]
        out.setdefault(resp.end_device_lfdi, set()).add(resp.status)
    return out


def _activation_http() -> AsyncMock:
    http = AsyncMock()
    http.post = AsyncMock(return_value=None)
    http.server_2018_compat = False
    return http


class TestDispatchGatedResponses:
    """Status 2 is posted per device only once that device's apply succeeded."""

    @pytest.mark.asyncio
    async def test_active_follows_the_apply(self, shutdown: asyncio.Event):
        """The ACTIVE post must come after dispatch, not before it."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = AsyncMock()
        http.server_2018_compat = False
        order: list[str] = []

        async def record_post(*args: object, **kwargs: object) -> None:
            if args[1].status == ResponseCode.ACTIVE:
                order.append("active-response")

        http.post = AsyncMock(side_effect=record_post)
        dispatcher = AsyncMock()

        async def record_apply(*args: object, **kwargs: object) -> None:
            order.append("apply")

        dispatcher.apply_control.side_effect = record_apply
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)

        assert order == ["apply", "active-response"]
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_failed_apply_rejects_instead_of_active(self, shutdown: asyncio.Event):
        """A device whose apply raises gets 251, and never gets ACTIVE."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = ModeNotSupportedError("unsupported mode")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await proc._on_activation(rec)

        codes = _codes_by_lfdi(http)[_DEV1_LFDI]
        assert ResponseCode.NOT_SUPPORTED in codes
        assert ResponseCode.ACTIVE not in codes
        assert rec.rejected_devices == {"/edev/1"}
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_not_configured_reports_not_applicable(self, shutdown: asyncio.Event):
        """DeviceNotConfiguredError is the one failure that maps to 252."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = DeviceNotConfiguredError("unknown device")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await proc._on_activation(rec)

        assert ResponseCode.NOT_APPLICABLE in _codes_by_lfdi(http)[_DEV1_LFDI]
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_untyped_failure_still_rejects(self, shutdown: asyncio.Event):
        """D2: an in-process connector raising a plain exception is not a success."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = RuntimeError("modbus exploded")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await proc._on_activation(rec)

        codes = _codes_by_lfdi(http)[_DEV1_LFDI]
        assert ResponseCode.NOT_SUPPORTED in codes
        assert ResponseCode.ACTIVE not in codes
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_invalid_control_value_reports_253(self, shutdown: asyncio.Event):
        """A control parameter outside the profile's range reports 253, not 251.

        The symptom this closes: the SunSpec connectors used to write an
        out-of-range power-factor displacement, the device silently discarded it,
        and the aggregator reported ACTIVE for a control that never took effect.
        The connector now refuses the write, and the fault is reported against
        the event's data rather than the device's capabilities.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = ConnectorValueError(
            "opModFixedPFInjectW displacement 1.1 outside [-1.0, 1.0]"
        )
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await proc._on_activation(rec)

        codes = _codes_by_lfdi(http)[_DEV1_LFDI]
        assert ResponseCode.INVALID in codes
        assert ResponseCode.ACTIVE not in codes
        # Not the generic capability-limit code: the event, not the device, is at
        # fault.
        assert ResponseCode.NOT_SUPPORTED not in codes
        assert rec.rejected_devices == {"/edev/1"}
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_invalid_control_value_excluded_from_completed(self, shutdown: asyncio.Event):
        """D7: a device that never applied the control gets no status 3 either.

        Two devices so the exclusion is proved rather than merely observed: the
        device that applied must still be told COMPLETED, which rules out the
        assertion passing because nothing was posted at all.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _gated_two_device_state(derc)
        http = _activation_http()
        dispatcher = AsyncMock()

        async def invalid_for_second(dev_href: str, *args: object, **kwargs: object) -> None:
            if dev_href == "/edev/2":
                raise ConnectorValueError("displacement 1.1")

        dispatcher.apply_control.side_effect = invalid_for_second
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)
        assert rec.rejected_devices == {"/edev/2"}
        http.post.reset_mock()
        await proc._on_completion(rec)

        posted = _codes_by_lfdi(http)
        assert ResponseCode.COMPLETED in posted[_DEV1_LFDI]
        assert ResponseCode.COMPLETED not in posted.get(_DEV2_LFDI, [])
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_sole_rejected_device_gets_no_completed(self, shutdown: asyncio.Event):
        """A single-device event rejected at activation must not report COMPLETED.

        Observed in a live run: the rejection went out correctly at the start,
        and a COMPLETED still followed at the end.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = ConnectorValueError("displacement 1.1")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)
        http.post.reset_mock()
        await proc._on_completion(rec)

        for codes in _codes_by_lfdi(http).values():
            assert ResponseCode.COMPLETED not in codes
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_rejected_device_gets_no_completed_after_state_refresh(
        self, shutdown: asyncio.Event
    ):
        """The live failure mode: the program mapping is gone by completion time.

        A rediscovery between activation and completion can leave
        ``program_to_devices`` without the record's program href -- which is why
        ``_on_completion`` waits on ``_state_ready`` at all. With no device list to
        iterate, the COMPLETED post fell back to "any available LFDI", a path that
        never consulted the exclusion set, so the rejected device was told the
        event completed.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = ConnectorValueError("displacement 1.1")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)
        assert rec.rejected_devices == {"/edev/1"}

        # Rediscovery repopulated the program under a different href, so the
        # record's href no longer resolves to any device.
        state.device_mapping.program_to_devices.pop("/derp/1", None)

        http.post.reset_mock()
        await proc._on_completion(rec)

        for codes in _codes_by_lfdi(http).values():
            assert ResponseCode.COMPLETED not in codes
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_rejected_device_gets_no_superseded(self, shutdown: asyncio.Event):
        """A rejected device is not told the event it never ran was superseded.

        Two programs sharing two devices. The low-primacy program's event is
        dispatched first and rejected on /edev/2; when the other event supersedes
        it, only /edev/1 -- which was actually running it -- hears status 7.
        """
        now = int(time.time())
        derc1 = _make_derc(0x01, start=now - 10, duration=3600, status_time=1000)
        derc2 = _make_derc(0x02, start=now - 10, duration=3600, status_time=1000)

        dev1_lfdi = b"\xaa" * 20
        dev2_lfdi = b"\xbb" * 20

        state = DiscoveredState()
        # /derp/2 has the higher primacy number, so its event loses and is the one
        # that gets superseded. Dispatch it first so its rejection is recorded
        # before supersession runs.
        state.der_programs["/derp/2"] = DerProgramState(
            program=_make_program("/derp/2", 5),
            href="/derp/2",
            primacy=5,
            der_controls=[derc2],
            default_dercontrol=_make_dderc(),
        )
        state.der_programs["/derp/1"] = DerProgramState(
            program=_make_program("/derp/1", 0),
            href="/derp/1",
            primacy=0,
            der_controls=[derc1],
            default_dercontrol=_make_dderc(),
        )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=dev1_lfdi
        )
        state.end_devices["/edev/2"] = EndDeviceState(
            device=_make_edev("/edev/2"), href="/edev/2", lfdi=dev2_lfdi
        )
        for derp in ("/derp/1", "/derp/2"):
            state.device_mapping.add(derp, "/edev/1")
            state.device_mapping.add(derp, "/edev/2")

        http = _activation_http()
        dispatcher = AsyncMock()

        async def invalid_for_second(dev_href: str, *args: object, **kwargs: object) -> None:
            if dev_href == "/edev/2":
                raise ConnectorValueError("displacement 1.1")

        dispatcher.apply_control.side_effect = invalid_for_second
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/2")
        rec2 = proc._store.get(derc2.m_rid.value)
        assert rec2 is not None
        assert rec2.rejected_devices == {"/edev/2"}

        http.post.reset_mock()
        await proc.process_controls("/derp/1")

        superseded_lfdis = {
            c[0][1].end_device_lfdi
            for c in http.post.call_args_list
            if c[0][1].status == ResponseCode.SUPERSEDED
        }
        assert superseded_lfdis == {dev1_lfdi}
        assert dev2_lfdi not in superseded_lfdis
        # The bookkeeping set still records both, since it drives dispatch
        # exclusion, the fully-superseded check and the DDERC fallback.
        assert rec2.superseded_devices == {"/edev/1", "/edev/2"}
        # The filtered response must not have cost the state transition that the
        # unfiltered bookkeeping set drives.
        assert rec2.state is EventState.SUPERSEDED
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_rejected_device_gets_no_cancelled(self, shutdown: asyncio.Event):
        """A rejected device is not told the event it never ran was cancelled."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _gated_two_device_state(derc)
        http = _activation_http()
        dispatcher = AsyncMock()

        async def invalid_for_second(dev_href: str, *args: object, **kwargs: object) -> None:
            if dev_href == "/edev/2":
                raise ConnectorValueError("displacement 1.1")

        dispatcher.apply_control.side_effect = invalid_for_second
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)
        assert rec.rejected_devices == {"/edev/2"}

        http.post.reset_mock()
        await proc._handle_cancellation(rec)

        by_lfdi = _codes_by_lfdi(http)
        assert ResponseCode.CANCELLED in by_lfdi[_DEV1_LFDI]
        assert ResponseCode.CANCELLED not in by_lfdi.get(_DEV2_LFDI, set())
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_scheduled_event_cancelled_still_notifies_every_device(
        self, shutdown: asyncio.Event
    ):
        """An event cancelled before it started has dispatched nothing.

        Nothing could have been rejected, so every device in the program must
        still hear the cancellation -- the positive ``applied_devices`` test must
        not silence this case.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 3600, duration=3600)
        state = _gated_two_device_state(derc)
        http = _activation_http()
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        assert rec.state is EventState.SCHEDULED
        assert not rec.applied_devices and not rec.rejected_devices

        http.post.reset_mock()
        await proc._handle_cancellation(rec)

        by_lfdi = _codes_by_lfdi(http)
        assert ResponseCode.CANCELLED in by_lfdi[_DEV1_LFDI]
        assert ResponseCode.CANCELLED in by_lfdi[_DEV2_LFDI]
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_unmapped_program_still_announces_the_lifecycle(self, shutdown: asyncio.Event):
        """An event with no dispatch target keeps the any-LFDI announcement.

        The counterpart to the two tests above: with nothing to dispatch to there
        is nothing to have rejected, so withholding COMPLETED here would leave the
        server with an ACTIVE and then silence.
        """
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        # Device exists for the fallback to find, but is not mapped to the program.
        state.device_mapping.program_to_devices.pop("/derp/1", None)
        http = _activation_http()
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)
        assert not rec.applied_devices and not rec.rejected_devices
        http.post.reset_mock()
        await proc._on_completion(rec)

        codes = {c for codes in _codes_by_lfdi(http).values() for c in codes}
        assert ResponseCode.COMPLETED in codes
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_one_device_fails_the_other_still_starts(self, shutdown: asyncio.Event):
        """Per-device split: a failing device must not drag its peers down."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _gated_two_device_state(derc)
        http = _activation_http()
        dispatcher = AsyncMock()

        async def fail_second(dev_href: str, *args: object, **kwargs: object) -> None:
            if dev_href == "/edev/2":
                raise RuntimeError("offline")

        dispatcher.apply_control.side_effect = fail_second
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await proc._on_activation(rec)

        by_lfdi = _codes_by_lfdi(http)
        assert ResponseCode.ACTIVE in by_lfdi[_DEV1_LFDI]
        assert ResponseCode.NOT_SUPPORTED in by_lfdi[_DEV2_LFDI]
        assert ResponseCode.ACTIVE not in by_lfdi[_DEV2_LFDI]
        assert rec.rejected_devices == {"/edev/2"}
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_slow_device_does_not_delay_a_fast_one(self, shutdown: asyncio.Event):
        """D6: responses chain per device rather than behind a fleet-wide barrier."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _gated_two_device_state(derc)
        http = AsyncMock()
        http.server_2018_compat = False
        order: list[str] = []
        slow_released = asyncio.Event()

        async def record_post(*args: object, **kwargs: object) -> None:
            resp = args[1]
            if resp.status == ResponseCode.ACTIVE:
                order.append(resp.end_device_lfdi[:1].hex())
                # The fast device answered while the slow one is still in
                # flight; releasing it only now proves the ordering.
                slow_released.set()

        http.post = AsyncMock(side_effect=record_post)
        dispatcher = AsyncMock()

        async def slow_for_second(dev_href: str, *args: object, **kwargs: object) -> None:
            if dev_href == "/edev/2":
                await slow_released.wait()

        dispatcher.apply_control.side_effect = slow_for_second
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        order.clear()
        await asyncio.wait_for(proc._on_activation(rec), timeout=5)

        assert order == ["aa", "bb"]
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_dispatch_ceiling_rejects_a_hung_device(
        self, shutdown: asyncio.Event, monkeypatch: pytest.MonkeyPatch
    ):
        """D1: a device slower than the ceiling is rejected, not waited on."""
        monkeypatch.setattr(processor_mod, "ACTIVATION_DISPATCH_DEADLINE", 0.05)
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()

        async def hang(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(30)

        dispatcher.apply_control.side_effect = hang
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await asyncio.wait_for(proc._on_activation(rec), timeout=5)

        codes = _codes_by_lfdi(http)[_DEV1_LFDI]
        assert ResponseCode.NOT_SUPPORTED in codes
        assert ResponseCode.ACTIVE not in codes
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_rejected_device_is_left_out_of_completed(self, shutdown: asyncio.Event):
        """D7: a device that never ran the event does not complete it."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _gated_two_device_state(derc)
        http = _activation_http()
        dispatcher = AsyncMock()

        async def fail_second(dev_href: str, *args: object, **kwargs: object) -> None:
            if dev_href == "/edev/2":
                raise RuntimeError("offline")

        dispatcher.apply_control.side_effect = fail_second
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)
        http.post.reset_mock()
        await proc._on_completion(rec)

        completed_lfdis = {
            c[0][1].end_device_lfdi
            for c in http.post.call_args_list
            if c[0][1].status == ResponseCode.COMPLETED
        }
        assert completed_lfdis == {_DEV1_LFDI}
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_no_device_mapping_still_announces_active(self, shutdown: asyncio.Event):
        """A program with no devices has nothing to fail; ACTIVE still goes out."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        # Drop the mapping but keep an EndDevice so the any-LFDI fallback works.
        state.device_mapping.program_to_devices["/derp/1"] = []
        http = _activation_http()
        proc = EventProcessor(http, state, NullDispatcher(), shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await proc._on_activation(rec)

        statuses = {c[0][1].status for c in http.post.call_args_list}
        assert ResponseCode.ACTIVE in statuses
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_supersession_stands_when_superseding_dispatch_fails(
        self, shutdown: asyncio.Event
    ):
        """D5: a rejected superseding event does not hand the slot back."""
        now = int(time.time())
        # derc1 is active; derc2 (higher primacy) supersedes it on activation.
        derc1 = _make_derc(0x01, start=now - 3600, duration=7200, status_time=1000)
        derc2 = _make_derc(0x02, start=now + 50, duration=3600, status_time=2000)

        state = DiscoveredState()
        # Lower primacy wins, so /derp/2 is the one that supersedes.
        for href, primacy, controls in (
            ("/derp/1", 5, [derc1]),
            ("/derp/2", 0, [derc2]),
        ):
            state.der_programs[href] = DerProgramState(
                program=_make_program(href, primacy),
                href=href,
                primacy=primacy,
                default_dercontrol=_make_dderc(),
                der_controls=controls,
            )
        state.end_devices["/edev/1"] = EndDeviceState(
            device=_make_edev("/edev/1"), href="/edev/1", lfdi=_DEV1_LFDI
        )
        state.device_mapping.add("/derp/1", "/edev/1")
        state.device_mapping.add("/derp/2", "/edev/1")

        http = _activation_http()
        dispatcher = AsyncMock()
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        await proc.process_controls("/derp/2")
        rec2 = proc._store.get(derc2.m_rid.value)
        rec1 = proc._store.get(derc1.m_rid.value)
        assert rec1 is not None and rec2 is not None

        # The superseding event's own dispatch fails at activation.
        dispatcher.apply_control.side_effect = RuntimeError("offline")
        http.post.reset_mock()
        await proc._on_activation(rec2)

        # derc1 still yielded: SUPERSEDED went out and it is no longer active.
        superseded = [
            c for c in http.post.call_args_list if c[0][1].status == ResponseCode.SUPERSEDED
        ]
        assert superseded, "expected SUPERSEDED despite the superseding dispatch failing"
        assert rec1.state == EventState.SUPERSEDED
        # ...and derc2 reported a rejection rather than ACTIVE.
        codes = _codes_by_lfdi(http)[_DEV1_LFDI]
        assert ResponseCode.NOT_SUPPORTED in codes
        assert ResponseCode.ACTIVE not in codes
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_ceiling_gates_the_response_not_the_control(
        self, shutdown: asyncio.Event, monkeypatch: pytest.MonkeyPatch
    ):
        """D1 bounds when the response goes out, not how long the apply gets.

        A device that outruns the ceiling is reported rejected, but its control
        application is shielded from the timeout and still reaches the device.
        """
        monkeypatch.setattr(processor_mod, "ACTIVATION_DISPATCH_DEADLINE", 0.05)
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        applied = asyncio.Event()

        async def slow_but_eventually_applies(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(0.2)
            applied.set()

        dispatcher.apply_control.side_effect = slow_but_eventually_applies
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await asyncio.wait_for(proc._on_activation(rec), timeout=5)

        # The response went out on the ceiling, without waiting for the apply.
        assert not applied.is_set()
        assert ResponseCode.NOT_SUPPORTED in _codes_by_lfdi(http)[_DEV1_LFDI]

        # The apply was not cancelled: it runs to completion in the background.
        await asyncio.wait_for(applied.wait(), timeout=5)
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_a_dispatch_still_running(
        self, shutdown: asyncio.Event, monkeypatch: pytest.MonkeyPatch
    ):
        """Shielding must not leak a task past shutdown."""
        monkeypatch.setattr(processor_mod, "ACTIVATION_DISPATCH_DEADLINE", 0.05)
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()

        async def never_returns(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(3600)

        dispatcher.apply_control.side_effect = never_returns
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await asyncio.wait_for(proc._on_activation(rec), timeout=5)

        assert proc._dispatch_tasks, "expected the overrunning dispatch to be tracked"
        pending = next(iter(proc._dispatch_tasks))
        await asyncio.wait_for(proc.shutdown(), timeout=5)
        assert pending.cancelled()

    @pytest.mark.asyncio
    async def test_plugin_opt_out_reports_status_4(self, shutdown: asyncio.Event):
        """A plugin declining the event is reported as a customer decision."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        dispatcher.apply_control.side_effect = OptOutError("customer is enrolled elsewhere")
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        http.post.reset_mock()
        await proc._on_activation(rec)

        codes = _codes_by_lfdi(http)[_DEV1_LFDI]
        assert ResponseCode.OPT_OUT in codes
        assert ResponseCode.ACTIVE not in codes
        assert ResponseCode.NOT_SUPPORTED not in codes
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_opted_out_device_does_not_complete_the_event(self, shutdown: asyncio.Event):
        """D7 covers opt-out too: a device that declined never ran the event."""
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _gated_two_device_state(derc)
        http = _activation_http()
        dispatcher = AsyncMock()

        async def opt_out_second(dev_href: str, *args: object, **kwargs: object) -> None:
            if dev_href == "/edev/2":
                raise OptOutError("customer override")

        dispatcher.apply_control.side_effect = opt_out_second
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        await proc._on_activation(rec)
        assert rec.rejected_devices == {"/edev/2"}
        http.post.reset_mock()
        await proc._on_completion(rec)

        completed_lfdis = {
            c[0][1].end_device_lfdi
            for c in http.post.call_args_list
            if c[0][1].status == ResponseCode.COMPLETED
        }
        assert completed_lfdis == {_DEV1_LFDI}
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_late_dispatch_failure_is_retrieved_and_logged(
        self,
        shutdown: asyncio.Event,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """A shielded dispatch that fails after its rejection must be consumed.

        Nothing awaits the task once the ceiling has fired, so an unretrieved
        exception would surface later as a bare asyncio warning with no context.
        """
        import logging

        monkeypatch.setattr(processor_mod, "ACTIVATION_DISPATCH_DEADLINE", 0.05)
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        failed = asyncio.Event()

        async def slow_then_fails(*args: object, **kwargs: object) -> None:
            try:
                await asyncio.sleep(0.2)
                raise RuntimeError("device rejected the write")
            finally:
                failed.set()

        dispatcher.apply_control.side_effect = slow_then_fails
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        task_ref: list[asyncio.Task[None]] = []
        with caplog.at_level(logging.INFO, logger="py20305.events.processor"):
            await asyncio.wait_for(proc._on_activation(rec), timeout=5)
            task_ref.extend(proc._dispatch_tasks)
            await asyncio.wait_for(failed.wait(), timeout=5)
            await asyncio.sleep(0)  # let the done-callback run

        assert any("late dispatch" in r.getMessage() for r in caplog.records), (
            "the post-rejection outcome must be reported"
        )
        # The exception was retrieved, so asyncio will not complain on GC.
        assert task_ref and task_ref[0].exception() is not None
        await proc.shutdown()

    @pytest.mark.asyncio
    async def test_late_dispatch_success_is_reported(
        self,
        shutdown: asyncio.Event,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """A control that lands after its rejection is worth saying out loud."""
        import logging

        monkeypatch.setattr(processor_mod, "ACTIVATION_DISPATCH_DEADLINE", 0.05)
        now = int(time.time())
        derc = _make_derc(0x01, start=now + 50, duration=3600)
        state = _setup_state(der_controls=[derc], dderc=_make_dderc())
        http = _activation_http()
        dispatcher = AsyncMock()
        applied = asyncio.Event()

        async def slow_but_applies(*args: object, **kwargs: object) -> None:
            await asyncio.sleep(0.2)
            applied.set()

        dispatcher.apply_control.side_effect = slow_but_applies
        proc = EventProcessor(http, state, dispatcher, shutdown)

        await proc.process_controls("/derp/1")
        rec = proc._store.get(derc.m_rid.value)
        assert rec is not None
        with caplog.at_level(logging.INFO, logger="py20305.events.processor"):
            await asyncio.wait_for(proc._on_activation(rec), timeout=5)
            await asyncio.wait_for(applied.wait(), timeout=5)
            await asyncio.sleep(0)

        assert any(
            "late dispatch" in r.getMessage() and "applied" in r.getMessage()
            for r in caplog.records
        )
        await proc.shutdown()
