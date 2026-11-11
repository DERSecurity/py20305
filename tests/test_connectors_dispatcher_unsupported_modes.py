"""A control the connector implements no mode of is rejected, not reported started.

IEEE 2030.5-2023 §10.10.4.2.1 lets a client "reject or partially act upon" a
DERControl for its capabilities. A control with at least one applied mode is
partial action and stays a success. A control that applied nothing must not
complete normally, because the event processor would then tell the server the
control is in force (Table 31 status 2) while the device did not change.
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, Mock

import pytest

from py20305.client.state import DerProgramState, DiscoveredState, EndDeviceState
from py20305.connectors.base import BaseConnector
from py20305.connectors.control_errors import ModeNotSupportedError
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.events.processor import EventProcessor
from py20305.events.response import ResponseCode
from py20305.models.sep.sep import (
    DateTimeInterval,
    DefaultDercontrol,
    Dercontrol1,
    DercontrolBase,
    Derprogram1,
    EndDevice1,
    EventStatus,
    MRidtype,
    PerCentControlType,
    PrimacyType,
    Sfditype,
    SignedPerCentControlType,
    TimeType,
)
from py20305.server_profile import Edition, ServerProfile

LFDI_A = "aa" * 20
LFDI_B = "bb" * 20


class _FixedWOnly(BaseConnector):
    """Implements opModFixedW and inherits the base no-op for every other mode."""

    connector_name = "fixed_w_only"

    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def update_op_mod_fixed_w(self, params):
        self.seen.append(params)


class _Nothing(BaseConnector):
    """Implements no control mode."""

    connector_name = "nothing"

    def __init__(self) -> None:
        pass


def _dispatcher(connectors: dict[str, BaseConnector]) -> ConnectorDispatcher:
    registry = Mock()

    def get_connector(key: str):
        connector = connectors.get(key.lower())
        if connector is None:
            return None
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=connector)
        return proxy

    registry.get_connector.side_effect = get_connector
    return ConnectorDispatcher(registry, lfdi_resolver=lambda _href: LFDI_A)


def _base(*, fixed_w: bool = False, max_lim_w: bool = False) -> DercontrolBase:
    return DercontrolBase(
        op_mod_fixed_w=SignedPerCentControlType(value=2500) if fixed_w else None,
        op_mod_max_lim_w=PerCentControlType(value=5000) if max_lim_w else None,
    )


def _control(base: DercontrolBase, *, start: int = 1000) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=b"\x01" * 16),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=0, date_time=TimeType(value=1000), potentially_superseded=False
        ),
        interval=DateTimeInterval(duration=3600, start=TimeType(value=start)),
        dercontrol_base=base,
        response_required=b"\x07",
        reply_to="/rsps",
    )


def _default(base: DercontrolBase) -> DefaultDercontrol:
    return DefaultDercontrol(m_rid=MRidtype(value=b"\x20" * 16), dercontrol_base=base)


class TestDispatcher:
    @pytest.mark.asyncio
    async def test_a_control_with_no_implemented_mode_is_refused(self) -> None:
        dispatcher = _dispatcher({LFDI_A: _Nothing()})

        with pytest.raises(ModeNotSupportedError, match="max_lim_w"):
            await dispatcher.apply_control("/edev/1", _control(_base(max_lim_w=True)), [])

    @pytest.mark.asyncio
    async def test_a_control_with_one_implemented_mode_is_applied_and_skips_the_rest(
        self,
    ) -> None:
        connector = _FixedWOnly()
        dispatcher = _dispatcher({LFDI_A: connector})

        await dispatcher.apply_control("/edev/1", _control(_base(fixed_w=True, max_lim_w=True)), [])

        assert len(connector.seen) == 1

    @pytest.mark.asyncio
    async def test_a_control_that_names_no_mode_is_not_refused(self) -> None:
        dispatcher = _dispatcher({LFDI_A: _Nothing()})

        await dispatcher.apply_control("/edev/1", _control(_base()), [])

    @pytest.mark.asyncio
    async def test_a_control_by_lfdi_with_no_implemented_mode_is_refused(self) -> None:
        dispatcher = _dispatcher({LFDI_A: _Nothing()})

        with pytest.raises(ModeNotSupportedError):
            await dispatcher.apply_control_by_lfdi(LFDI_A, _control(_base(max_lim_w=True)), [])

    @pytest.mark.asyncio
    async def test_a_default_control_with_no_implemented_mode_is_refused(self) -> None:
        dispatcher = _dispatcher({LFDI_A: _Nothing()})

        with pytest.raises(ModeNotSupportedError):
            await dispatcher.apply_default_control("/edev/1", _default(_base(max_lim_w=True)), [])

    @pytest.mark.asyncio
    async def test_a_default_control_with_one_implemented_mode_is_applied(self) -> None:
        connector = _FixedWOnly()
        dispatcher = _dispatcher({LFDI_A: connector})

        await dispatcher.apply_default_control(
            "/edev/1", _default(_base(fixed_w=True, max_lim_w=True)), []
        )

        assert len(connector.seen) == 1

    @pytest.mark.asyncio
    async def test_a_default_control_by_lfdi_with_no_implemented_mode_is_refused(self) -> None:
        dispatcher = _dispatcher({LFDI_A: _Nothing()})

        with pytest.raises(ModeNotSupportedError):
            await dispatcher.apply_default_control_by_lfdi(
                LFDI_A, _default(_base(max_lim_w=True)), []
            )


def _state(control: Dercontrol1, default: DefaultDercontrol | None = None) -> DiscoveredState:
    state = DiscoveredState()
    state.der_programs["/derp/1"] = DerProgramState(
        program=Derprogram1(m_rid=MRidtype(value=b"\x10" * 16), primacy=PrimacyType(value=0)),
        href="/derp/1",
        primacy=0,
        default_dercontrol=default,
        der_controls=[control],
    )
    state.end_devices["/edev/1"] = EndDeviceState(
        device=EndDevice1(
            m_rid=MRidtype(value=b"\x30" * 16),
            s_fdi=Sfditype(value=0),
            changed_time=TimeType(value=0),
        ),
        href="/edev/1",
        lfdi=bytes.fromhex(LFDI_A),
    )
    state.device_mapping.add("/derp/1", "/edev/1")
    return state


def _statuses(http: AsyncMock) -> list[int]:
    return [call.args[1].status for call in http.post.await_args_list]


def _http(edition: Edition) -> AsyncMock:
    http = AsyncMock()
    http.profile = ServerProfile(base=edition)
    http.post = AsyncMock(return_value=None)
    return http


async def _activate(
    connectors: dict[str, BaseConnector],
    base: DercontrolBase,
    *,
    edition: Edition = Edition.E2023,
    group: list[str] | None = None,
) -> list[int]:
    """Run one already-started event through the processor and return the posted statuses."""
    http = _http(edition)
    control = _control(base, start=int(time.time()) - 10)
    processor = EventProcessor(
        http,
        _state(control),
        _dispatcher(connectors),
        asyncio.Event(),
        group_lookup=(lambda _program: list(group)) if group is not None else None,
    )
    await processor.process_controls("/derp/1")
    await processor.shutdown()
    return _statuses(http)


class TestReportedStatus:
    @pytest.mark.asyncio
    async def test_a_control_with_no_implemented_mode_is_answered_not_supported(self) -> None:
        statuses = await _activate({LFDI_A: _Nothing()}, _base(max_lim_w=True))

        assert ResponseCode.NOT_SUPPORTED.value in statuses
        assert ResponseCode.ACTIVE.value not in statuses

    @pytest.mark.asyncio
    async def test_a_2018_server_is_answered_252(self) -> None:
        statuses = await _activate(
            {LFDI_A: _Nothing()}, _base(max_lim_w=True), edition=Edition.E2018
        )

        assert 252 in statuses
        assert 251 not in statuses
        assert ResponseCode.ACTIVE.value not in statuses

    @pytest.mark.asyncio
    async def test_a_control_with_one_implemented_mode_is_reported_active(self) -> None:
        statuses = await _activate({LFDI_A: _FixedWOnly()}, _base(fixed_w=True, max_lim_w=True))

        assert ResponseCode.ACTIVE.value in statuses
        assert ResponseCode.NOT_SUPPORTED.value not in statuses

    @pytest.mark.asyncio
    async def test_a_group_is_rejected_when_every_local_device_implements_no_mode(self) -> None:
        statuses = await _activate(
            {LFDI_A: _Nothing(), LFDI_B: _Nothing()},
            _base(max_lim_w=True),
            group=[LFDI_A, LFDI_B],
        )

        assert ResponseCode.NOT_SUPPORTED.value in statuses
        assert ResponseCode.ACTIVE.value not in statuses

    @pytest.mark.asyncio
    async def test_a_group_is_active_when_one_local_device_applies_a_mode(self) -> None:
        statuses = await _activate(
            {LFDI_A: _Nothing(), LFDI_B: _FixedWOnly()},
            _base(fixed_w=True),
            group=[LFDI_A, LFDI_B],
        )

        assert ResponseCode.ACTIVE.value in statuses
        assert ResponseCode.NOT_SUPPORTED.value not in statuses


class TestDefaultControlFallback:
    @pytest.mark.asyncio
    async def test_a_default_no_mode_of_which_is_implemented_is_not_warned_about_each_poll(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Nothing can be retried, and a default has no response to reject."""
        control = _control(_base(max_lim_w=True), start=int(time.time()) + 3600)
        state = _state(control, _default(_base(max_lim_w=True)))
        http = _http(Edition.E2023)
        dispatcher = _dispatcher({LFDI_A: _Nothing()})
        processor = EventProcessor(http, state, dispatcher, asyncio.Event())

        with caplog.at_level(logging.WARNING, logger="py20305.events.processor"):
            await processor.process_controls("/derp/1")
            await processor.process_controls("/derp/1")
        await processor.shutdown()

        assert [r for r in caplog.records if "DDERC dispatch" in r.getMessage()] == []
