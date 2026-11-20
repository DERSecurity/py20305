"""The command intercept: a host application taking a write before the connector.

The dispatcher offers each permitted write that names a device to an injected
``CommandIntercept``. A write the intercept takes never reaches the connector,
is kept in the write history as ``intercepted``, and is reported to the caller
as a success. A write the intercept declines is applied as if no intercept
were injected.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from py20305.commands import (
    CommandIntercept,
    CommandNotPermittedError,
    CommandOrigin,
    InterceptedWrite,
)
from py20305.connectors.base import BaseConnector
from py20305.connectors.dispatcher import ConnectorDispatcher
from py20305.history import MEMORY
from py20305.history.database import HistoryDatabase
from py20305.history.writes import WriteHistory
from py20305.models.sep.sep import (
    DateTimeInterval,
    DefaultDercontrol,
    Dercontrol1,
    DercontrolBase,
    EventStatus,
    MRidtype,
    SignedPerCentControlType,
    TimeType,
)

LFDI = "deafbeefdeafbeefdeafbeefdeafbeefdeafbeef"
EVENT_MRID = b"\x01" * 16
DEFAULT_MRID = b"\x02" * 16
ENDED_MRID = b"\x03" * 16
APP_ORIGIN = "register_server"


def _registry_for(connector: BaseConnector) -> Mock:
    registry = Mock()

    def get_connector(key: str) -> Any:
        if key.lower() != LFDI.lower():
            return None
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=connector)
        return proxy

    registry.get_connector.side_effect = get_connector
    return registry


class _FixedW(BaseConnector):
    """Implements ``op_mod_fixed_w`` only, and records each call."""

    connector_name = "fixed_w"

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    async def update_op_mod_fixed_w(self, params: dict[str, Any]) -> None:
        self.seen.append(params)


class _NativeClear(_FixedW):
    """Also implements the connector's own single-call clear."""

    connector_name = "native_clear"

    def __init__(self) -> None:
        super().__init__()
        self.clears = 0

    async def clear_control(self) -> None:
        self.clears += 1


class _Intercept:
    """Takes or declines every write, or raises, and records what it was offered."""

    def __init__(self, take: bool = True, error: Exception | None = None) -> None:
        self.take = take
        self.error = error
        self.offered: list[InterceptedWrite] = []

    async def intercept(self, write: InterceptedWrite) -> bool:
        self.offered.append(write)
        if self.error is not None:
            raise self.error
        return self.take


class _NestedMutating(_Intercept):
    """Appends to a list nested in the offered params, then takes or declines the write."""

    async def intercept(self, write: InterceptedWrite) -> bool:
        write.params["curve"]["points"].append(99)
        return self.take


def _nested_params() -> dict[str, Any]:
    return {"enabled": True, "curve": {"points": [1, 2]}}


class _DenyAll:
    def __init__(self) -> None:
        self.asked: list[tuple[str, str]] = []

    def may_command(self, device: str, origin: str) -> bool:
        self.asked.append((device, origin))
        return False


def _fixed_w_control() -> Dercontrol1:
    """A DERControl that translates to one ``update_op_mod_fixed_w`` write."""
    return Dercontrol1(
        m_rid=MRidtype(value=EVENT_MRID),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=0, date_time=TimeType(value=1000), potentially_superseded=False
        ),
        interval=DateTimeInterval(duration=600, start=TimeType(value=1000)),
        dercontrol_base=DercontrolBase(op_mod_fixed_w=SignedPerCentControlType(value=2500)),
    )


def _fixed_w_default() -> DefaultDercontrol:
    """A DefaultDERControl that translates to one ``update_op_mod_fixed_w`` write."""
    return DefaultDercontrol(
        m_rid=MRidtype(value=DEFAULT_MRID),
        dercontrol_base=DercontrolBase(op_mod_fixed_w=SignedPerCentControlType(value=1000)),
    )


@pytest.fixture(name="history")
def _history() -> Iterator[WriteHistory]:
    database = HistoryDatabase(MEMORY)
    yield WriteHistory(database)
    database.close()


def _dispatcher(
    connector: BaseConnector,
    intercept: CommandIntercept | None = None,
    *,
    gate: Any = None,
    observer: Any = None,
    telemetry: Any = None,
    history: WriteHistory | None = None,
) -> ConnectorDispatcher:
    return ConnectorDispatcher(
        _registry_for(connector),
        lfdi_resolver=lambda _href: LFDI,
        command_observer=observer,
        command_gate=gate,
        telemetry=telemetry,
        write_history=history,
        command_intercept=intercept,
    )


def test_the_test_intercept_satisfies_the_protocol() -> None:
    assert isinstance(_Intercept(), CommandIntercept)


class TestEveryApplyPathReachesTheIntercept:
    """Each apply path offers its writes with its own kind, origin and mRIDs."""

    async def test_a_derc_control_is_offered_as_kind_control(self) -> None:
        intercept = _Intercept()
        dispatcher = _dispatcher(_FixedW(), intercept)

        await dispatcher.apply_control("/edev/1", _fixed_w_control(), [])

        assert intercept.offered == [
            InterceptedWrite(
                lfdi=LFDI,
                control="op_mod_fixed_w",
                params={"enabled": True, "value": 2500},
                origin="ieee2030_5",
                kind="control",
                applied_mrid=EVENT_MRID,
                cause_mrid=EVENT_MRID,
            )
        ]

    async def test_a_default_control_is_offered_as_kind_default_control(self) -> None:
        intercept = _Intercept()
        dispatcher = _dispatcher(_FixedW(), intercept)

        await dispatcher.apply_default_control_by_lfdi(
            LFDI, _fixed_w_default(), [], cause_mrid=ENDED_MRID
        )

        assert intercept.offered == [
            InterceptedWrite(
                lfdi=LFDI,
                control="op_mod_fixed_w",
                params={"enabled": True, "value": 1000},
                origin="dderc_reapply",
                kind="default_control",
                applied_mrid=DEFAULT_MRID,
                cause_mrid=ENDED_MRID,
            )
        ]

    async def test_a_comms_loss_clear_is_offered_as_kind_clear(self) -> None:
        intercept = _Intercept()
        dispatcher = _dispatcher(_FixedW(), intercept)

        await dispatcher.clear_control_by_lfdi(LFDI, cause_mrid=ENDED_MRID)

        assert intercept.offered == [
            InterceptedWrite(
                lfdi=LFDI,
                control="op_mod_fixed_w",
                params={"enabled": False},
                origin="comms_loss",
                kind="clear",
                applied_mrid=None,
                cause_mrid=ENDED_MRID,
            )
        ]

    async def test_a_native_clear_is_offered_as_kind_clear(self) -> None:
        intercept = _Intercept()
        connector = _NativeClear()
        dispatcher = _dispatcher(connector, intercept)

        await dispatcher.clear_control_by_lfdi(LFDI)

        assert intercept.offered == [
            InterceptedWrite(
                lfdi=LFDI,
                control="clear_control",
                params={},
                origin="comms_loss",
                kind="clear",
                applied_mrid=None,
                cause_mrid=None,
            )
        ]
        assert connector.clears == 0

    async def test_a_mode_disabled_at_event_end_is_offered_as_kind_clear(self) -> None:
        intercept = _Intercept()
        dispatcher = _dispatcher(_FixedW(), intercept)

        await dispatcher.disable_modes_by_lfdi(
            LFDI, frozenset({"op_mod_fixed_w"}), cause_mrid=ENDED_MRID
        )

        assert intercept.offered == [
            InterceptedWrite(
                lfdi=LFDI,
                control="op_mod_fixed_w",
                params={"enabled": False},
                origin="event_end",
                kind="clear",
                applied_mrid=None,
                cause_mrid=ENDED_MRID,
            )
        ]

    async def test_a_named_operation_is_offered_as_kind_operation(self) -> None:
        intercept = _Intercept()
        dispatcher = _dispatcher(_FixedW(), intercept)

        await dispatcher.apply_operation(
            LFDI, "op_mod_fixed_w", {"enabled": True, "value": 5000}, origin=APP_ORIGIN
        )

        assert intercept.offered == [
            InterceptedWrite(
                lfdi=LFDI,
                control="op_mod_fixed_w",
                params={"enabled": True, "value": 5000},
                origin=APP_ORIGIN,
                kind="operation",
                applied_mrid=None,
                cause_mrid=None,
            )
        ]

    async def test_a_write_with_no_lfdi_is_not_offered(self) -> None:
        """The intercept is keyed on the device, and this write names none."""
        intercept = _Intercept()
        connector = _FixedW()
        dispatcher = ConnectorDispatcher(
            _registry_for(connector),
            lfdi_resolver=lambda _href: None,
            command_intercept=intercept,
        )

        await dispatcher._apply_one(
            connector.update_op_mod_fixed_w,
            "update_op_mod_fixed_w",
            {"enabled": True, "value": 5000},
            lfdi=None,
            origin=APP_ORIGIN,
            kind="operation",
            label="/edev/1",
        )

        assert intercept.offered == []
        assert connector.seen == [{"enabled": True, "value": 5000}]


class TestATakenWrite:
    """True: the intercept owns the write and the connector never sees it."""

    async def test_the_connector_is_not_called(self) -> None:
        connector = _FixedW()
        dispatcher = _dispatcher(connector, _Intercept(take=True))

        await dispatcher.apply_control_by_lfdi(LFDI, _fixed_w_control(), [])

        assert connector.seen == []

    async def test_the_observer_records_no_command_and_telemetry_reports_no_write(
        self,
    ) -> None:
        observer = Mock()
        telemetry = Mock()
        dispatcher = _dispatcher(
            _FixedW(), _Intercept(take=True), observer=observer, telemetry=telemetry
        )

        await dispatcher.apply_operation(
            LFDI, "op_mod_fixed_w", {"enabled": True, "value": 5000}, origin=APP_ORIGIN
        )

        observer.record_command.assert_not_called()
        telemetry.record_write.assert_not_called()

    async def test_the_history_keeps_the_write_as_intercepted(
        self, history: WriteHistory
    ) -> None:
        dispatcher = _dispatcher(_FixedW(), _Intercept(take=True), history=history)

        await dispatcher.apply_control_by_lfdi(LFDI, _fixed_w_control(), [])

        (row,) = history.query()
        assert row["outcome"] == "intercepted"
        assert row["control"] == "op_mod_fixed_w"
        assert row["params"] == {"enabled": True, "value": 2500}
        assert row["origin"] == "ieee2030_5"
        assert row["applied_mrid"] == "01" * 16
        assert row["cause_mrid"] == "01" * 16
        assert row["error"] is None

    async def test_the_caller_sees_success_not_a_refusal(self) -> None:
        """A control or named operation whose write was taken does not raise, so the
        event processor reports the event as applied."""
        dispatcher = _dispatcher(_FixedW(), _Intercept(take=True))

        await dispatcher.apply_control_by_lfdi(LFDI, _fixed_w_control(), [])
        await dispatcher.apply_operation(
            LFDI, "op_mod_fixed_w", {"enabled": True, "value": 5000}, origin=APP_ORIGIN
        )

    async def test_changing_the_offered_params_changes_nothing_recorded(
        self, history: WriteHistory
    ) -> None:
        class _Mutating(_Intercept):
            async def intercept(self, write: InterceptedWrite) -> bool:
                write.params["enabled"] = True
                return True

        dispatcher = _dispatcher(_FixedW(), _Mutating(), history=history)

        await dispatcher.clear_control_by_lfdi(LFDI)
        await dispatcher.clear_control_by_lfdi(LFDI)

        assert [row["params"] for row in history.query()] == [{"enabled": False}] * 2

    async def test_changing_nested_offered_params_changes_nothing_recorded(
        self, history: WriteHistory
    ) -> None:
        dispatcher = _dispatcher(_FixedW(), _NestedMutating(take=True), history=history)
        params = _nested_params()

        await dispatcher.apply_operation(LFDI, "op_mod_fixed_w", params, origin=APP_ORIGIN)

        assert params == _nested_params()
        assert [row["params"] for row in history.query()] == [_nested_params()]


class TestADeclinedWrite:
    """False: the write is applied as it would be with no intercept."""

    async def test_the_connector_applies_it_and_it_is_recorded_as_applied(
        self, history: WriteHistory
    ) -> None:
        connector = _FixedW()
        observer = Mock()
        telemetry = Mock()
        intercept = _Intercept(take=False)
        dispatcher = _dispatcher(
            connector, intercept, observer=observer, telemetry=telemetry, history=history
        )

        await dispatcher.apply_control_by_lfdi(LFDI, _fixed_w_control(), [])

        assert len(intercept.offered) == 1
        assert connector.seen == [{"enabled": True, "value": 2500}]
        observer.record_command.assert_called_once()
        telemetry.record_write.assert_called_once()
        assert [row["outcome"] for row in history.query()] == ["applied"]

    async def test_changing_nested_offered_params_changes_nothing_applied(
        self, history: WriteHistory
    ) -> None:
        connector = _FixedW()
        dispatcher = _dispatcher(connector, _NestedMutating(take=False), history=history)
        params = _nested_params()

        await dispatcher.apply_operation(LFDI, "op_mod_fixed_w", params, origin=APP_ORIGIN)

        assert connector.seen == [_nested_params()]
        assert [row["params"] for row in history.query()] == [_nested_params()]


class TestARefusedWrite:
    """The gate decides first. A write it refuses never reaches the intercept."""

    async def test_a_refused_control_is_not_offered(self, history: WriteHistory) -> None:
        intercept = _Intercept()
        gate = _DenyAll()
        dispatcher = _dispatcher(_FixedW(), intercept, gate=gate, history=history)

        with pytest.raises(CommandNotPermittedError):
            await dispatcher.apply_control_by_lfdi(LFDI, _fixed_w_control(), [])

        assert gate.asked == [(LFDI, CommandOrigin.IEEE2030_5)]
        assert intercept.offered == []
        assert [row["outcome"] for row in history.query()] == ["refused"]


class TestAnInterceptFailure:
    """An exception is kept as a failed write and raised, as a connector failure is."""

    async def test_the_exception_is_recorded_and_raised(self, history: WriteHistory) -> None:
        connector = _FixedW()
        dispatcher = _dispatcher(
            connector, _Intercept(error=RuntimeError("planner unavailable")), history=history
        )

        with pytest.raises(RuntimeError, match="planner unavailable"):
            await dispatcher.apply_operation(
                LFDI, "op_mod_fixed_w", {"enabled": True, "value": 5000}, origin=APP_ORIGIN
            )

        assert connector.seen == []
        (row,) = history.query()
        assert row["outcome"] == "failed"
        assert row["error"] == "planner unavailable"
        assert row["error_type"] == "RuntimeError"


class TestNoIntercept:
    """With no intercept injected, every write goes to the connector."""

    async def test_every_apply_path_reaches_the_connector(self, history: WriteHistory) -> None:
        connector = _FixedW()
        dispatcher = _dispatcher(connector, history=history)

        await dispatcher.apply_control_by_lfdi(LFDI, _fixed_w_control(), [])
        await dispatcher.apply_default_control_by_lfdi(LFDI, _fixed_w_default(), [])
        await dispatcher.disable_modes_by_lfdi(LFDI, frozenset({"op_mod_fixed_w"}))
        await dispatcher.clear_control_by_lfdi(LFDI)
        await dispatcher.apply_operation(
            LFDI, "op_mod_fixed_w", {"enabled": True, "value": 5000}, origin=APP_ORIGIN
        )

        assert connector.seen == [
            {"enabled": True, "value": 2500},
            {"enabled": True, "value": 1000},
            {"enabled": False},
            {"enabled": False},
            {"enabled": True, "value": 5000},
        ]
        assert {row["outcome"] for row in history.query()} == {"applied"}
