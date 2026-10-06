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

#: An origin of the application's own: the name it gives an interface this
#: package has never heard of.
REGISTER_SERVER = "register_server"


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

    def __init__(self, denied: str) -> None:
        self.denied = denied
        self.asked: list[tuple[str, str]] = []

    def may_command(self, device: str, origin: str) -> bool:
        self.asked.append((device, origin))
        return origin != self.denied


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
            LFDI, "fixed_w", {"WSetEna": 1, "WSet": 50.0}, origin=REGISTER_SERVER
        )

        assert connector.seen == [{"WSetEna": 1, "WSet": 50.0}]

    @pytest.mark.asyncio
    async def test_an_inherited_no_op_is_refused_rather_than_acknowledged(self) -> None:
        """The caller is told, because a protocol server that acknowledged this
        would be reporting success for a write that reached no device."""
        dispatcher = _dispatcher(_InheritsEverything())

        with pytest.raises(ConnectorError, match="does not implement update_fixed_w"):
            await dispatcher.apply_operation(
                LFDI, "fixed_w", {"WSetEna": 1}, origin=REGISTER_SERVER
            )

    @pytest.mark.asyncio
    async def test_an_unresolvable_device_is_refused(self) -> None:
        dispatcher = _dispatcher(_Implements())

        with pytest.raises(ConnectorError, match="no connector for LFDI"):
            await dispatcher.apply_operation("ab" * 20, "fixed_w", {}, origin=REGISTER_SERVER)


class TestCommandGate:
    """Authority is checked at the one funnel every apply path shares."""

    @pytest.mark.asyncio
    async def test_a_refused_named_control_raises_rather_than_returning(self) -> None:
        """This caller named one control and has an error channel. Returning
        quietly would have it report success for a write that never happened --
        a protocol server would acknowledge its client for nothing."""
        connector = _Implements()
        gate = _Gate(REGISTER_SERVER)
        dispatcher = _dispatcher(connector, gate)

        with pytest.raises(CommandNotPermittedError, match="may not command"):
            await dispatcher.apply_operation(
                LFDI, "fixed_w", {"WSetEna": 1}, origin=REGISTER_SERVER
            )

        assert connector.seen == []
        assert (LFDI, REGISTER_SERVER) in gate.asked

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
        gate = _Gate(REGISTER_SERVER)
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
            origin=REGISTER_SERVER,
            label="/edev/1",
        )

        assert len(connector.seen) == 1
        assert gate.asked == []

    @pytest.mark.asyncio
    async def test_a_permitted_origin_still_applies(self) -> None:
        connector = _Implements()
        dispatcher = _dispatcher(connector, _Gate(CommandOrigin.IEEE2030_5))

        await dispatcher.apply_operation(
            LFDI, "fixed_w", {"WSetEna": 1}, origin=REGISTER_SERVER
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
            command_gate=_Gate(REGISTER_SERVER),
        )

        with pytest.raises(CommandNotPermittedError):
            await dispatcher.apply_operation(
                LFDI, "fixed_w", {"WSetEna": 1}, origin=REGISTER_SERVER
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
            LFDI, "fixed_w", {"WSetEna": 1}, origin=REGISTER_SERVER
        )

        assert len(connector.seen) == 1


class TestAnOriginIsALabel:
    """An origin is a string the caller chooses, carried through untouched.

    The package lists the origins it produces and no others. An application
    that fronts the same devices with an interface of its own names that
    interface itself, and nothing here has to learn the name first.
    """

    def test_the_package_lists_only_the_origins_it_produces(self) -> None:
        """An IEEE 2030.5 control, and the two times the client reasserts its
        own state. An application's interfaces are not this package's to list."""
        assert {origin.value for origin in CommandOrigin} == {
            "ieee2030_5",
            "dderc_reapply",
            "comms_loss",
        }

    @pytest.mark.asyncio
    async def test_an_applications_own_origin_reaches_the_gate_and_the_record(self) -> None:
        observer = Mock()
        connector = _Implements()
        gate = _Gate("nobody")
        dispatcher = ConnectorDispatcher(
            _registry_for(connector),
            lfdi_resolver=lambda _href: LFDI,
            command_observer=observer,
            command_gate=gate,
        )

        await dispatcher.apply_operation(LFDI, "fixed_w", {"WSetEna": 1}, origin="plant_controller")

        assert gate.asked == [(LFDI, "plant_controller")]
        assert observer.record_command.call_args.kwargs["origin"] == "plant_controller"

    @pytest.mark.asyncio
    async def test_a_refusal_names_the_origin_as_the_caller_gave_it(self) -> None:
        dispatcher = _dispatcher(_Implements(), _Gate("plant_controller"))

        with pytest.raises(CommandNotPermittedError, match="^plant_controller may not command"):
            await dispatcher.apply_operation(LFDI, "fixed_w", {}, origin="plant_controller")

    @pytest.mark.asyncio
    async def test_a_gate_sees_the_packages_own_origins_as_strings(self) -> None:
        """So one comparison serves both kinds: a gate holding ``"comms_loss"``
        as a plain string denies the package's own comms-loss clear."""
        connector = _Implements()
        gate = _Gate("comms_loss")
        dispatcher = _dispatcher(connector, gate)

        await dispatcher.clear_control_by_lfdi(LFDI)

        assert connector.seen == []
        assert gate.asked == [(LFDI, "comms_loss")]

    @pytest.mark.asyncio
    async def test_a_dropped_control_is_reported_with_the_origin_as_plain_text(
        self, monkeypatch
    ) -> None:
        """The diagnostic is read by people and serialized by whatever shows it,
        so it carries the name, not an enumeration member."""
        from py20305 import diagnostics
        from py20305.diagnostics import DiagnosticsStore

        fresh = DiagnosticsStore()
        monkeypatch.setattr(diagnostics, "_store", fresh)
        dispatcher = _dispatcher(_Implements(), _Gate(CommandOrigin.COMMS_LOSS))

        await dispatcher.clear_control_by_lfdi(LFDI)

        (entry,) = [e for e in fresh.snapshot()["warnings"] if "may not command" in e["message"]]
        assert entry["message"].startswith("comms_loss may not command")
        assert type(entry["details"]["origin"]) is str

    def test_a_record_holds_whatever_origin_it_was_given(self) -> None:
        from py20305.commands import CommandRecord, CommandStatus

        record = CommandRecord(
            control="p_lim",
            params={},
            origin="plant_controller",
            commanded_at=1.0,
            status=CommandStatus.UNCONFIRMED,
        )

        assert record.origin == "plant_controller"
