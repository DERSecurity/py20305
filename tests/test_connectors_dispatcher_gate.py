"""The write funnel: who may command, and what counts as an implementation.

Two properties are covered here, both of which are invisible until something
goes wrong with them.

A consuming application may serve several command interfaces while intending
only one of them to command any given device. ``CommandGate`` is how the
dispatcher asks. Without it every interface reaches the connector, and the
configuration reads as though a single one were in charge.

``BaseConnector`` declares each control mode as a concrete method returning
``None``, so ``getattr`` finds one whether or not the connector implements it.
Taking that as support makes a connector look like it accepted a command it
never carried out.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from py20305.commands import CommandNotPermittedError, CommandOrigin
from py20305.connectors.base import BaseConnector
from py20305.connectors.dispatcher import BY_DESIGN, OFFER_MISSING, ConnectorDispatcher
from py20305.connectors.errors import ConnectorError

LFDI = "deafbeefdeafbeefdeafbeefdeafbeefdeafbeef"


def _registry_for(connector: BaseConnector) -> Mock:
    registry = Mock()

    def get_connector(key: str):
        if key.lower() != LFDI.lower():
            return None
        proxy = Mock()
        proxy.aresolve = AsyncMock(return_value=connector)
        return proxy

    registry.get_connector.side_effect = get_connector
    return registry


class _Gate:
    """Denies one origin, permits the rest, and records what it was asked."""

    def __init__(self, denied: CommandOrigin) -> None:
        self.denied = denied
        self.asked: list[tuple[str, CommandOrigin]] = []

    def may_command(self, device: str, origin: CommandOrigin) -> bool:
        self.asked.append((device, origin))
        return origin is not self.denied


class _Implements(BaseConnector):
    """Overrides one mode, so that mode is genuinely supported."""

    connector_name = "implements"

    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def update_fixed_w(self, params):
        self.seen.append(params)
        return None


class _InheritsEverything(BaseConnector):
    """Overrides nothing, so every mode is the base no-op."""

    connector_name = "inherits"

    def __init__(self) -> None:
        pass


def _dispatcher(connector: BaseConnector, gate=None) -> ConnectorDispatcher:
    return ConnectorDispatcher(
        _registry_for(connector),
        lfdi_resolver=lambda _href: LFDI,
        command_gate=gate,
    )


class TestControlSupport:
    """An inherited base implementation is the absence of an implementation."""

    def test_an_overridden_mode_is_supported(self) -> None:
        method, reason = ConnectorDispatcher._control_support(_Implements(), "update_fixed_w")

        assert method is not None
        assert reason is None

    def test_an_inherited_no_op_is_unsupported_by_design(self) -> None:
        method, reason = ConnectorDispatcher._control_support(
            _InheritsEverything(), "update_fixed_w"
        )

        assert method is None
        assert reason == BY_DESIGN

    def test_a_mode_that_resolves_to_nothing_is_a_missing_offer(self) -> None:
        """A plugin-backed connector supplies modes through ``__getattr__`` from
        a live offer. A mode absent there is actionable, unlike a base no-op."""
        method, reason = ConnectorDispatcher._control_support(
            _InheritsEverything(), "update_never_declared"
        )

        assert method is None
        assert reason == OFFER_MISSING


class TestApplyOperation:
    """The entry point for a caller that already knows which control it wants."""

    @pytest.mark.asyncio
    async def test_a_named_control_reaches_the_connector(self) -> None:
        connector = _Implements()
        dispatcher = _dispatcher(connector)

        await dispatcher.apply_operation(
            LFDI, "fixed_w", {"WSetEna": 1, "WSet": 50.0}, origin=CommandOrigin.SUNSPEC
        )

        assert connector.seen == [{"WSetEna": 1, "WSet": 50.0}]

    @pytest.mark.asyncio
    async def test_an_inherited_no_op_is_refused_rather_than_acknowledged(self) -> None:
        """The caller is told, because a protocol server that acknowledged this
        would be reporting success for a write that reached no device."""
        dispatcher = _dispatcher(_InheritsEverything())

        with pytest.raises(ConnectorError, match="does not implement update_fixed_w"):
            await dispatcher.apply_operation(
                LFDI, "fixed_w", {"WSetEna": 1}, origin=CommandOrigin.SUNSPEC
            )

    @pytest.mark.asyncio
    async def test_an_unresolvable_device_is_refused(self) -> None:
        dispatcher = _dispatcher(_Implements())

        with pytest.raises(ConnectorError, match="no connector for LFDI"):
            await dispatcher.apply_operation("ab" * 20, "fixed_w", {}, origin=CommandOrigin.SUNSPEC)


class TestCommandGate:
    """Authority is checked at the one funnel every apply path shares."""

    @pytest.mark.asyncio
    async def test_a_refused_named_control_raises_rather_than_returning(self) -> None:
        """This caller named one control and has an error channel. Returning
        quietly would have it report success for a write that never happened --
        a protocol server would acknowledge its client for nothing."""
        connector = _Implements()
        gate = _Gate(CommandOrigin.SUNSPEC)
        dispatcher = _dispatcher(connector, gate)

        with pytest.raises(CommandNotPermittedError, match="may not command"):
            await dispatcher.apply_operation(
                LFDI, "fixed_w", {"WSetEna": 1}, origin=CommandOrigin.SUNSPEC
            )

        assert connector.seen == []
        assert (LFDI, CommandOrigin.SUNSPEC) in gate.asked

    @pytest.mark.asyncio
    async def test_a_refused_server_control_is_dropped_not_raised(self) -> None:
        """The other half of the contract: an interface posting to a device
        another one commands is a configuration being honored, so the event
        engine is not handed an exception to interpret."""
        connector = _Implements()
        dispatcher = _dispatcher(connector, _Gate(CommandOrigin.COMMS_LOSS))

        await dispatcher.clear_control_by_lfdi(LFDI)

        assert connector.seen == []

    @pytest.mark.asyncio
    async def test_a_device_with_no_lfdi_is_ungated(self) -> None:
        """Stated rather than incidental: authority is held per device, and there
        is no device here to hold it over. Denying would drop writes for an href
        that resolves to a connector but not to an LFDI."""
        connector = _Implements()
        gate = _Gate(CommandOrigin.SUNSPEC)
        dispatcher = ConnectorDispatcher(
            _registry_for(connector),
            lfdi_resolver=lambda _href: None,
            command_gate=gate,
        )

        await dispatcher._apply_one(
            connector.update_fixed_w,
            "update_fixed_w",
            {"WSetEna": 1},
            lfdi=None,
            origin=CommandOrigin.SUNSPEC,
            label="/edev/1",
        )

        assert len(connector.seen) == 1
        assert gate.asked == []

    @pytest.mark.asyncio
    async def test_a_permitted_origin_still_applies(self) -> None:
        connector = _Implements()
        dispatcher = _dispatcher(connector, _Gate(CommandOrigin.IEEE2030_5))

        await dispatcher.apply_operation(
            LFDI, "fixed_w", {"WSetEna": 1}, origin=CommandOrigin.SUNSPEC
        )

        assert len(connector.seen) == 1

    @pytest.mark.asyncio
    async def test_a_refused_command_is_not_recorded(self) -> None:
        """A command that never left must not appear in the audit trail, or the
        record claims a setpoint the device never received."""
        observer = Mock()
        connector = _Implements()
        dispatcher = ConnectorDispatcher(
            _registry_for(connector),
            lfdi_resolver=lambda _href: LFDI,
            command_observer=observer,
            command_gate=_Gate(CommandOrigin.SUNSPEC),
        )

        with pytest.raises(CommandNotPermittedError):
            await dispatcher.apply_operation(
                LFDI, "fixed_w", {"WSetEna": 1}, origin=CommandOrigin.SUNSPEC
            )

        observer.record_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_comms_loss_clear_is_gated_too(self) -> None:
        """The clear routes through the same funnel, so the safe default cannot
        revert a device some other interface commands."""
        connector = _Implements()
        gate = _Gate(CommandOrigin.COMMS_LOSS)
        dispatcher = _dispatcher(connector, gate)

        await dispatcher.clear_control_by_lfdi(LFDI)

        assert connector.seen == []
        assert [origin for _device, origin in gate.asked] == [CommandOrigin.COMMS_LOSS]

    @pytest.mark.asyncio
    async def test_no_gate_permits_everything(self) -> None:
        """A consumer with a single command interface behaves as it did before
        the gate existed."""
        connector = _Implements()
        dispatcher = ConnectorDispatcher(_registry_for(connector), lfdi_resolver=lambda _href: LFDI)

        await dispatcher.apply_operation(
            LFDI, "fixed_w", {"WSetEna": 1}, origin=CommandOrigin.SUNSPEC
        )

        assert len(connector.seen) == 1
