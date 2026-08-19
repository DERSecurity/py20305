"""End-to-end loss-of-communications cycle: enter -> opt out -> recover -> resume.

Drives the CsipClient time-based probe against a real EventProcessor with a
seeded active event, exercising the enter path (opt-out to DDERC) and the
recovery path (in-band self-reregister, re-poll, resume-after, clear mode).
See ``docs/planning/COMMS_LOSS_MODE.md``.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from py20305.client.csip_client import CsipClient
from py20305.client.state import DerProgramState, EndDeviceState
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
    PrimacyType,
    Sfditype,
    TimeType,
)

PROGRAM_HREF = "/derp/1"
DEVICE_HREF = "/edev/1"


def _make_active_derc(start: int, duration: int = 3600) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=b"\x01" * 16),
        creation_time=TimeType(value=start - 100),
        event_status=EventStatus(
            current_status=0,
            date_time=TimeType(value=start - 50),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=duration, start=TimeType(value=start)),
        dercontrol_base=DercontrolBase(),
        reply_to="/rsps",
        response_required=b"\x07",
    )


def _seed_active_event(client: CsipClient, start: int) -> None:
    """Populate the client's discovered state with one program/device/DDERC and
    an already-active DERControl, then run it through the processor so the store
    holds a live ACTIVE record."""
    state = client._state
    derp_state = DerProgramState(
        program=Derprogram1(m_rid=MRidtype(value=b"\x10" * 16), primacy=PrimacyType(value=0)),
        href=PROGRAM_HREF,
        primacy=0,
        default_dercontrol=DefaultDercontrol(
            m_rid=MRidtype(value=b"\x20" * 16), dercontrol_base=DercontrolBase()
        ),
        der_controls=[_make_active_derc(start)],
    )
    state.der_programs[PROGRAM_HREF] = derp_state
    state.end_devices[DEVICE_HREF] = EndDeviceState(
        device=EndDevice1(
            m_rid=MRidtype(value=b"\x30" * 16),
            s_fdi=Sfditype(value=0),
            changed_time=TimeType(value=0),
        ),
        href=DEVICE_HREF,
        lfdi=b"\xaa" * 20,
    )
    state.device_mapping.add(PROGRAM_HREF, DEVICE_HREF)


@pytest.mark.asyncio
async def test_full_comms_loss_cycle():
    now = int(time.time())
    client = CsipClient("https://example.com", comms_loss_seconds=900)
    # Silence the upstream POSTs the processor would attempt.
    client._http.post = AsyncMock(return_value=None)  # type: ignore[method-assign]

    _seed_active_event(client, start=now - 10)
    await client._event_processor.process_controls(PROGRAM_HREF)
    rec = client._event_processor._store.get(b"\x01" * 16)
    assert rec is not None and rec.state == EventState.ACTIVE

    # --- Enter: sustained silence trips the detector ---
    client._http._last_contact_epoch = now - 1000
    with patch.object(client._dispatcher, "apply_default_control", new_callable=AsyncMock):
        await client._comms_loss_probe()
    assert client._comms_loss.active is True
    assert rec.opted_out is True
    assert client._comms_loss.resume_after_epoch == rec.end

    # --- Recover: fresh contact reregisters, re-polls, and clears the mode ---
    client._own_lfdi = "aa" * 20
    client._http._last_contact_epoch = now
    with (
        # Utility head-end: the EndDevice was removed during the outage.
        patch.object(
            client, "_server_lists_end_device", new_callable=AsyncMock, return_value=False
        ),
        patch.object(client, "register_end_device", new_callable=AsyncMock) as reg,
        patch.object(
            client, "trigger_rediscovery", new_callable=AsyncMock, return_value=True
        ) as redisc,
    ):
        await client._comms_loss_probe()

    reg.assert_awaited_once_with(lfdi="aa" * 20, check_duplicate=False)
    redisc.assert_awaited_once()
    assert client._comms_loss.active is False
    # The boundary is still in the future (the opted-out event is still
    # running), so it is retained: routine polls must keep skipping
    # opted-out-window events until the window has fully passed.
    assert client._comms_loss.resume_after_epoch == rec.end
    # The opted-out event stays flagged (its end is at/before the boundary): it
    # must not resume and stays excluded from "other active" checks until prune.
    assert rec.opted_out is True


@pytest.mark.asyncio
async def test_recovery_resumes_only_post_boundary_events():
    """After recovery the re-polled schedule skips events inside the opted-out
    window and keeps ones that start after it."""
    now = int(time.time())
    client = CsipClient("https://example.com", comms_loss_seconds=900)
    client._http.post = AsyncMock(return_value=None)  # type: ignore[method-assign]
    proc = client._event_processor

    _seed_active_event(client, start=now - 10)
    # Simulate the outage boundary: everything up to the active event's end was
    # opted out.
    boundary = now + 3590
    client._comms_loss.resume_after_epoch = boundary

    # A fresh poll surfaces two new controls: one inside the window, one after.
    in_window = _make_active_derc(start=now + 100, duration=200)
    in_window.m_rid = MRidtype(value=b"\x02" * 16)
    after = _make_active_derc(start=boundary + 1000, duration=200)
    after.m_rid = MRidtype(value=b"\x03" * 16)
    client._state.der_programs[PROGRAM_HREF].der_controls = [in_window, after]

    await proc.process_controls(PROGRAM_HREF)

    assert proc._store.get(b"\x02" * 16) is None  # inside opted-out window -> skipped
    assert proc._store.get(b"\x03" * 16) is not None  # after boundary -> resumed
