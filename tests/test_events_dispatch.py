"""Tests for the ControlDispatcher protocol and NullDispatcher."""

from __future__ import annotations

import pytest

from py20305.events.dispatch import ControlDispatcher, NullDispatcher
from py20305.models.sep.sep import (
    DefaultDercontrol,
    Dercontrol1,
    DercontrolBase,
    MRidtype,
    TimeType,
)


def _make_derc() -> Dercontrol1:
    """Build a minimal DERControl for testing."""
    from py20305.models.sep.sep import DateTimeInterval, EventStatus

    return Dercontrol1(
        m_rid=MRidtype(value=b"\x01" * 16),
        creation_time=TimeType(value=1000),
        event_status=EventStatus(
            current_status=0,
            date_time=TimeType(value=1000),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=3600, start=TimeType(value=2000)),
        dercontrol_base=DercontrolBase(),
    )


def _make_dderc() -> DefaultDercontrol:
    return DefaultDercontrol(
        m_rid=MRidtype(value=b"\x02" * 16),
        dercontrol_base=DercontrolBase(),
    )


def test_null_dispatcher_conforms_to_protocol():
    """NullDispatcher must satisfy the ControlDispatcher protocol."""
    dispatcher: ControlDispatcher = NullDispatcher()
    assert isinstance(dispatcher, NullDispatcher)


@pytest.mark.asyncio
async def test_null_dispatcher_apply_control():
    d = NullDispatcher()
    await d.apply_control("/edev/1", _make_derc(), [])


@pytest.mark.asyncio
async def test_null_dispatcher_apply_default_control():
    d = NullDispatcher()
    await d.apply_default_control("/edev/1", _make_dderc(), [])


@pytest.mark.asyncio
async def test_null_dispatcher_clear_control():
    d = NullDispatcher()
    await d.clear_control("/edev/1")
