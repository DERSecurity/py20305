"""ConnectorDispatcher: implements ControlDispatcher backed by connectors.

Bridges the event processing system to the connector system.
Translates DERControl Pydantic models into connector method calls via the
modes translation layer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from py20305.commands import (
    AllowAllCommands,
    CommandGate,
    CommandNotPermittedError,
    CommandObserver,
    CommandOrigin,
    NullCommandObserver,
)
from py20305.connectors.base import BaseConnector, ScheduleNotification
from py20305.connectors.device_telemetry import DeviceTelemetryEmitter
from py20305.connectors.errors import ConnectorError
from py20305.connectors.modes import (
    translate_controls,
    translate_default_controls,
)
from py20305.connectors.registry import ConnectorConfigRegistry
from py20305.models.sep.sep import (
    DefaultDercontrol,
    Dercontrol1,
    Dercurve1,
)

logger = logging.getLogger(__name__)


#: The connector inherits ``BaseConnector``'s no-op for this mode, so it has no
#: register for it and never claimed one. Expected, not actionable.
BY_DESIGN = "by_design"

#: No implementation resolves at all. For a plugin-backed connector that means
#: the live offer is missing a mode it should carry. Actionable.
OFFER_MISSING = "offer_missing"


class ConnectorDispatcher:
    """Implements ControlDispatcher protocol using the connector system.

    Resolves device_href to a connector via the registry and an LFDI
    resolver callback, then translates control models to connector calls.
    """

    def __init__(
        self,
        registry: ConnectorConfigRegistry,
        lfdi_resolver: Callable[[str], str | None],
        command_observer: CommandObserver | None = None,
        command_gate: CommandGate | None = None,
        telemetry: DeviceTelemetryEmitter | None = None,
    ) -> None:
        """
        Args:
            registry: Connector registry.
            lfdi_resolver: device_href -> LFDI.
            command_observer: Where applied controls are reported. Injected
                rather than imported: the commanded plane is a host-application
                product concern and this module is client code, so the
                dependency has to point the other way. Omitted, controls apply
                exactly as before and nothing is recorded.
            telemetry: Reports each applied control to the monitoring system.
                Optional and disabled by default.
        """
        self._registry = registry
        self._lfdi_resolver = lfdi_resolver
        # `is None`, not `or`: an observer accumulates records, so a
        # collection-backed one is falsy while empty and `or` would silently
        # replace it with the no-op.
        self._gate = AllowAllCommands() if command_gate is None else command_gate
        self._commands = NullCommandObserver() if command_observer is None else command_observer
        self._telemetry = telemetry

    @property
    def telemetry(self) -> DeviceTelemetryEmitter | None:
        """The southbound telemetry emitter, if one was injected.

        The metering cycle reports through the same emitter as the control
        path, so both halves of a device's traffic land on one channel with
        one configuration behind them.
        """
        return self._telemetry

    @property
    def registry(self) -> ConnectorConfigRegistry:
        """The registry this dispatcher resolves through.

        Exposed so a caller wiring a second consumer -- the metering cycle,
        which reads the same devices this writes to -- resolves through the
        same registry. Constructing a second one would build a second
        connector per device and open duplicate connections to it.
        """
        return self._registry

    async def _resolve_connector(self, device_href: str) -> BaseConnector | None:
        """Resolve a device_href to a connector instance.

        Async because first-touch construction of a real Modbus connector
        runs scan + readiness retries that block whichever thread does the
        work; offload to a worker via ``aresolve``.
        """
        from py20305.diagnostics import report

        lfdi = self._lfdi_resolver(device_href)
        if lfdi is None:
            report(
                "warnings",
                f"No LFDI found for device href {device_href}",
                source="dispatcher",
                dedup_key=f"unresolved_href:{device_href}",
                details={"device_href": device_href},
            )
            return None

        return await self._resolve_connector_by_lfdi(lfdi)

    async def _resolve_connector_by_lfdi(self, lfdi: str) -> BaseConnector | None:
        """Resolve a device LFDI directly to a connector instance.

        Used by multi-device dispatch where the processor
        has already expanded a server-side EndDevice href into a list of
        local sub-device LFDIs and we skip the href→LFDI step.
        """
        from py20305.diagnostics import report

        proxy = self._registry.get_connector(lfdi)
        if proxy is not None:
            return await proxy.aresolve()  # type: ignore[no-any-return]

        report(
            "warnings",
            f"No connector found for LFDI {lfdi}",
            source="dispatcher",
            dedup_key=f"no_connector:{lfdi}",
            details={"lfdi": lfdi},
        )
        return None

    async def apply_control(
        self,
        device_href: str,
        derc: Dercontrol1,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.IEEE2030_5,
    ) -> None:
        """Apply a DERControl's settings to a device."""
        connector = await self._resolve_connector(device_href)
        await self._apply_control_to_connector(
            connector,
            derc,
            curves,
            lfdi=self._lfdi_resolver(device_href),
            device_href=device_href,
            origin=origin,
        )

    async def apply_control_by_lfdi(
        self,
        lfdi: str,
        derc: Dercontrol1,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.IEEE2030_5,
    ) -> None:
        """Apply a DERControl directly to a device identified by LFDI."""
        connector = await self._resolve_connector_by_lfdi(lfdi)
        await self._apply_control_to_connector(
            connector, derc, curves, lfdi=lfdi, device_href=None, origin=origin
        )

    async def _apply_control_to_connector(
        self,
        connector: BaseConnector | None,
        derc: Dercontrol1,
        curves: list[Dercurve1],
        *,
        lfdi: str | None,
        device_href: str | None,
        origin: CommandOrigin = CommandOrigin.IEEE2030_5,
    ) -> None:
        if connector is None:
            return

        label = lfdi or device_href or "<unknown>"
        translations = translate_controls(derc.dercontrol_base, curves)
        logger.debug(
            "Applying control to %s via %s (%d mode(s))",
            label,
            connector.connector_name,
            len(translations),
        )

        # Pass ramp_tms through to each mode's params when set
        ramp_tms = derc.dercontrol_base.ramp_tms
        if ramp_tms is not None:
            for _method, p in translations:
                p["ramp_tms"] = ramp_tms

        for method_name, params in translations:
            method, reason = self._control_support(connector, method_name)
            if method is not None:
                logger.debug("  %s.%s(%s)", connector.connector_name, method_name, params)
                await self._apply_one(
                    method, method_name, params, lfdi=lfdi, origin=origin, label=label
                )
            elif reason == BY_DESIGN:
                # This connector has no register for the mode and never claimed
                # one. Nothing an operator can do, so it does not belong in a
                # warnings surface -- and a control carrying it alongside modes
                # the connector does implement is normal traffic, not a fault.
                logger.debug(
                    "  %s does not implement %s; skipped",
                    connector.connector_name,
                    method_name,
                )
            else:
                self._report_unimplemented_mode(
                    connector, lfdi=lfdi, device_href=device_href, method_name=method_name
                )

    async def _apply_one(
        self,
        method: Callable[[dict[str, Any]], Any],
        method_name: str,
        params: dict[str, Any],
        *,
        lfdi: str | None,
        origin: CommandOrigin,
        label: str,
        connector: BaseConnector | None = None,
    ) -> bool:
        """Apply one translated mode and report it to the command observer.

        Returns True when the control reached the connector, False when the gate
        refused it. A caller dispatching a server-issued control ignores the
        result, since a refusal there is reported rather than raised; a caller
        that named one control needs to tell the two apart.

        Stamped before the call, like an acquisition: the write shadow compares
        this against when a readback *started*, so a command has to be marked at
        the moment it was issued rather than when it completed.

        A failure is recorded and re-raised. Swallowing it here would change the
        dispatcher's contract with the event processor, and a rejected command is
        exactly the thing an audit trail should retain.
        """
        # ``control`` drops the ``update_`` prefix so the recorded name matches
        # the management API's operation vocabulary -- one name per control,
        # whichever interface issued it.
        control = method_name.removeprefix("update_")

        # Authority is checked here because this is the one place every apply
        # path passes through -- a control, a default control, a clear and a
        # named operation. Checking at each caller instead would mean the DDERC
        # reapply and comms-loss routes each needing their own gate, which is
        # exactly how a write path ends up ungated.
        if lfdi:
            if not self._gate.may_command(lfdi, origin):
                self._report_not_commanding(lfdi, control, origin)
                return False
        else:
            # Deliberately ungated: authority is held per device, and there is no
            # device here to hold it over. Denying instead would drop writes for
            # an href that resolves to a connector but not to an LFDI -- a case
            # that predates the gate and is handled below -- so the choice is to
            # let it through and say so rather than to fail closed by accident.
            logger.debug("Ungated %s on %s: no LFDI to resolve authority for", control, label)
        issued_at = time.time()
        # A bound method carries its connector, so the device's address and
        # protocol are reachable without widening every call site. A caller
        # that wraps the method -- the native clear-control path passes a
        # lambda -- has no ``__self__`` to introspect, so it supplies the
        # connector explicitly and that takes precedence.
        if connector is None:
            connector = getattr(method, "__self__", None)
        try:
            await method(params)
        except Exception as exc:
            if lfdi:
                self._commands.record_command(
                    lfdi, control, params, origin=origin, at=issued_at, error=str(exc)
                )
            # A rejected command is reported too. The northbound side may still
            # believe it succeeded, and that divergence is the point.
            if self._telemetry is not None:
                self._telemetry.record_write(
                    label, control, params, connector=connector, lfdi=lfdi, error=str(exc)
                )
            raise
        if self._telemetry is not None:
            self._telemetry.record_write(label, control, params, connector=connector, lfdi=lfdi)
        if lfdi:
            self._commands.record_command(lfdi, control, params, origin=origin, at=issued_at)
        else:
            # No LFDI means no key to file the record under. Rare (an href that
            # resolves to a connector but not to an LFDI) and worth seeing.
            logger.debug("Applied %s to %s with no LFDI; not recorded", control, label)
        return True

    async def apply_operation(
        self,
        lfdi: str,
        control: str,
        params: dict[str, Any],
        *,
        origin: CommandOrigin,
    ) -> None:
        """Apply one named control to one device by LFDI, and record it.

        The write funnel for callers that already know which control they want,
        rather than a DERControl to be translated -- a Modbus master writing a
        register, for instance. Everything an event-driven write gets applies:
        the command is recorded with its origin, a failure is recorded as
        rejected and re-raised.

        Raises:
            ConnectorError: no connector is registered for ``lfdi``, or the
                connector does not implement this control.
            CommandNotPermittedError: a gate refused this origin authority over
                this device. Raised rather than reported because this caller
                named the control and has somewhere to put the answer -- a
                protocol server owes its own client the distinction between a
                write that failed and one that was not permitted.
        """
        connector = await self._resolve_connector_by_lfdi(lfdi)
        if connector is None:
            raise ConnectorError(f"no connector for LFDI {lfdi}")
        method_name = f"update_{control}"
        method, _reason = self._control_support(connector, method_name)
        if method is None:
            raise ConnectorError(f"connector for {lfdi} does not implement {method_name}")
        applied = await self._apply_one(
            method, method_name, params, lfdi=lfdi, origin=origin, label=lfdi
        )
        if not applied:
            raise CommandNotPermittedError(
                f"{origin.value} may not command {lfdi}: another interface holds the "
                f"command role, so {control!r} was not applied"
            )

    async def apply_default_control(
        self,
        device_href: str,
        dderc: DefaultDercontrol,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.DDERC_REAPPLY,
    ) -> None:
        """Apply default DER control (DDERC fallback) to a device."""
        connector = await self._resolve_connector(device_href)
        await self._apply_default_control_to_connector(
            connector,
            dderc,
            curves,
            lfdi=self._lfdi_resolver(device_href),
            device_href=device_href,
            origin=origin,
        )

    async def apply_default_control_by_lfdi(
        self,
        lfdi: str,
        dderc: DefaultDercontrol,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.DDERC_REAPPLY,
    ) -> None:
        """Apply default DER control directly to a device identified by LFDI."""
        connector = await self._resolve_connector_by_lfdi(lfdi)
        await self._apply_default_control_to_connector(
            connector, dderc, curves, lfdi=lfdi, device_href=None, origin=origin
        )

    async def _apply_default_control_to_connector(
        self,
        connector: BaseConnector | None,
        dderc: DefaultDercontrol,
        curves: list[Dercurve1],
        *,
        lfdi: str | None,
        device_href: str | None,
        origin: CommandOrigin = CommandOrigin.DDERC_REAPPLY,
    ) -> None:
        if connector is None:
            return

        label = lfdi or device_href or "<unknown>"
        translations = translate_default_controls(dderc, curves)
        logger.debug("Applying DDERC fallback to %s (%d mode(s))", label, len(translations))
        for method_name, params in translations:
            method, reason = self._control_support(connector, method_name)
            if method is not None:
                await self._apply_one(
                    method, method_name, params, lfdi=lfdi, origin=origin, label=label
                )
            elif reason == BY_DESIGN:
                # This connector has no register for the mode and never claimed
                # one. Nothing an operator can do, so it does not belong in a
                # warnings surface -- and a control carrying it alongside modes
                # the connector does implement is normal traffic, not a fault.
                logger.debug(
                    "  %s does not implement %s; skipped",
                    connector.connector_name,
                    method_name,
                )
            else:
                self._report_unimplemented_mode(
                    connector, lfdi=lfdi, device_href=device_href, method_name=method_name
                )

    async def relay_schedule_notification(
        self,
        lfdis: list[str],
        notification: ScheduleNotification,
    ) -> None:
        """Relay a schedule notification to each connector owning an affected LFDI.

        Resolves each LFDI to its connector (the same ``registry.get_connector``
        path dispatch uses), de-duplicates so a connector that owns several affected
        LFDIs is notified once, and isolates per-connector errors -- a
        throwing/slow connector here must never affect event processing or the
        other connectors.

        De-dup is keyed by ``connector.relay_group`` when present. A connector
        fronting several devices over one connection sets it to that
        connection's name, so N per-LFDI instances collapse to a single
        notification -- which is correct, because the notification already
        carries every affected LFDI. Connectors that don't set it fall back to
        instance identity.

        The de-duped connectors are notified **concurrently** so one slow
        connector can't delay delivery to the others; each call is
        error-isolated.
        """
        from py20305.diagnostics import report

        seen: set[object] = set()
        targets: list[tuple[str, BaseConnector]] = []
        for lfdi in lfdis:
            try:
                connector = await self._resolve_connector_by_lfdi(lfdi)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Resolution (e.g. LazyConnectorProxy.aresolve permanent errors)
                # must not abort the whole relay -- isolate and keep going.
                report(
                    "warnings",
                    (
                        f"Resolving connector for {lfdi} failed during schedule relay "
                        f"({notification.stream}/{notification.transition}): {exc}"
                    ),
                    source="dispatcher",
                    dedup_key=f"schedule_notify_resolve:{lfdi}:{notification.stream}",
                    details={"lfdi": lfdi, "stream": notification.stream, "error": str(exc)},
                )
                continue
            if connector is None:
                continue
            group = getattr(connector, "relay_group", None) or id(connector)
            if group in seen:
                continue
            seen.add(group)
            targets.append((lfdi, connector))

        if targets:
            await asyncio.gather(
                *(self._notify_one(lfdi, connector, notification) for lfdi, connector in targets)
            )

    async def _notify_one(
        self,
        lfdi: str,
        connector: BaseConnector,
        notification: ScheduleNotification,
    ) -> None:
        """Relay to a single connector, isolating any error.

        An informational relay must never propagate into event processing or
        affect the other connectors (gather sees no exception).
        """
        from py20305.diagnostics import report

        logger.debug(
            "relay %s/%s mrid=%s -> %s (lfdi=%s, affects=%d)",
            notification.stream,
            notification.transition,
            notification.mrid,
            connector.connector_name,
            lfdi,
            len(notification.affected_lfdis),
        )
        try:
            await connector.on_schedule_notification(notification)
        except asyncio.CancelledError:
            # Shutdown cancels in-flight relay tasks -- a cancellation is not a
            # connector failure; stay quiet and propagate.
            raise
        except Exception as exc:
            report(
                "warnings",
                (
                    f"Connector {connector.connector_name} raised in "
                    f"on_schedule_notification ({notification.stream}/"
                    f"{notification.transition}): {exc}"
                ),
                source="dispatcher",
                dedup_key=f"schedule_notify:{lfdi}:{notification.stream}:{notification.mrid}",
                details={
                    "lfdi": lfdi,
                    "stream": notification.stream,
                    "mrid": notification.mrid,
                    "transition": notification.transition,
                    "error": str(exc),
                },
            )

    @staticmethod
    def _report_not_commanding(lfdi: str, control: str, origin: CommandOrigin) -> None:
        """Surface a write refused because its origin does not command the device.

        Reported rather than raised: a 2030.5 server posting events to a device
        that SunSpec commands is a configuration statement being honored, not a
        fault, and raising would turn every such event into an error the event
        engine has to interpret. Silence is the wrong answer too -- an operator
        seeing a posted control never arrive needs this row to know why.
        """
        from py20305.diagnostics import report

        report(
            "warnings",
            f"{origin.value} may not command {lfdi[:8]}: another interface holds "
            f"the command role. '{control}' was not applied.",
            source="dispatcher",
            dedup_key=f"not-commanding-{origin.value}-{lfdi}",
            details={"device": lfdi, "origin": origin.value, "control": control},
        )

    @staticmethod
    def _control_support(
        connector: BaseConnector, method_name: str
    ) -> tuple[Callable[[dict[str, Any]], Any] | None, str | None]:
        """``(bound method, reason)``. ``reason`` is None when implemented.

        ``BaseConnector`` declares 40 ``update_*`` modes as concrete methods that
        return ``None``, so ``getattr`` finds one whether or not the connector
        implements it. Reading that as support makes a connector look like it
        accepted a command it never carried out: the dispatch succeeds, the
        commanded plane records it, and a Modbus master is acknowledged for a
        write that reached no device.

        The two ways a mode can be unsupported need telling apart, because only
        one of them is anybody's mistake:

        ``BY_DESIGN``
            The connector inherits the base no-op. ``ConnectorSunSpec`` does this
            for 29 of the 40 modes and says so in its docstring -- the base
            defaults exist precisely to cover modes SunSpec has no register for.
            Expected, and not something an operator can act on.

        ``OFFER_MISSING``
            No implementation resolves at all. For a plugin-backed connector,
            whose modes arrive through ``__getattr__`` from a live offer, that
            means the offer lacks a mode it should carry -- typically a plugin
            built against an older SDK. Actionable, and what the unimplemented
            diagnostic was written for.
        """
        own = getattr(type(connector), method_name, None)
        if own is not None and own is getattr(BaseConnector, method_name, None):
            return None, BY_DESIGN
        bound: Callable[[dict[str, Any]], Any] | None = getattr(connector, method_name, None)
        if bound is None:
            return None, OFFER_MISSING
        return bound, None

    @staticmethod
    def _report_unimplemented_mode(
        connector: BaseConnector,
        *,
        lfdi: str | None,
        device_href: str | None,
        method_name: str,
    ) -> None:
        """Surface a translated mode the connector can't accept.

        When a server-issued DERControl translates to ``method_name`` but the
        resolved connector doesn't expose that method, the control has nowhere
        to go. Dropping it silently is the wrong answer: the operator sees the
        post land at the client and never reach the device, with nothing
        saying why. The usual cause is a connector built against an older
        revision of this package that predates the mode.

        Routed through ``diagnostics.report`` so it surfaces alongside
        ``no_connector`` and ``unresolved_href``. The dedup key is scoped per
        (LFDI-or-href, method), so a device polled repeatedly collapses to one
        entry with ``count`` ticking up rather than flooding the log.
        """
        from py20305.diagnostics import report

        identifier = lfdi or device_href or "<unknown>"
        report(
            "warnings",
            (
                f"Connector {connector.connector_name} for {identifier} "
                f"does not implement {method_name}; control dropped. "
                "A connector supplied outside this package may predate "
                "this mode -- rebuild it against the current version."
            ),
            source="dispatcher",
            dedup_key=f"unimplemented_mode:{identifier}:{method_name}",
            details={
                "lfdi": lfdi,
                "device_href": device_href,
                "method": method_name,
                "connector": connector.connector_name,
            },
        )

    async def clear_control(
        self,
        device_href: str,
    ) -> None:
        """Clear active control from a device by sending disable params.

        Prefers the connector's own ``clear_control`` when it has one. A
        connector talking to a remote device should implement it as a single
        round-trip rather than paying for the per-mode fan-out below; the
        bundled connectors talk to the device directly, so they don't
        override it and take the fan-out.
        """
        logger.debug("Clearing all controls on %s", device_href)
        connector = await self._resolve_connector(device_href)
        await self._clear_control_on_connector(
            connector, lfdi=self._lfdi_resolver(device_href), label=device_href
        )

    async def clear_control_by_lfdi(
        self,
        lfdi: str,
    ) -> None:
        """Clear active control from a device identified by LFDI.

        The by-LFDI counterpart of ``clear_control`` (the comms-loss
        safe-default when dispatching by LFDI). Skips the href->LFDI
        resolution step.
        """
        logger.debug("Clearing all controls on LFDI %s", lfdi)
        connector = await self._resolve_connector_by_lfdi(lfdi)
        await self._clear_control_on_connector(connector, lfdi=lfdi, label=lfdi)

    async def _clear_control_on_connector(
        self,
        connector: BaseConnector | None,
        *,
        lfdi: str | None = None,
        label: str = "",
    ) -> None:
        """Disable every active control on a device.

        Both branches route through ``_apply_one`` so a clear is recorded like
        any other write. It reaches the device the same way, and it is the
        comms-loss safe default -- the one control action most worth being able
        to account for after the fact -- so leaving it out of the audit trail
        and the monitoring stream would be the wrong asymmetry.
        """
        if connector is None:
            return

        native_clear = getattr(connector, "clear_control", None)
        if callable(native_clear):
            # One round-trip that clears everything, so it is one record
            # rather than the fan-out's per-mode ones.
            await self._apply_one(
                lambda _params: native_clear(),
                "clear_control",
                {},
                lfdi=lfdi,
                origin=CommandOrigin.COMMS_LOSS,
                label=label,
                # The lambda has no ``__self__``; without this the clear would
                # report as generic with no destination -- losing exactly the
                # attribution this path most needs.
                connector=connector,
            )
            return

        disable_calls: list[tuple[str, dict[str, Any]]] = [
            ("update_qv", {"qv_mode_enable": 0}),
            ("update_pv", {"pv_mode_enable": 0}),
            ("update_qp", {"qp_mode_enable": 0}),
            ("update_p_lim", {"p_lim_mode_enable": 0}),
            ("update_p_lim_inj", {"p_lim_mode_enable": 0}),
            ("update_p_lim_abs", {"p_lim_mode_enable": 0}),
            ("update_pf", {"pf_mode_enable": 0}),
            ("update_const_q", {"const_q_mode_enable": 0}),
            ("update_const_pf", {"inj": {"mode": 0}, "abs": {"mode": 0}}),
            ("update_fixed_w", {"WSetEna": 0}),
            ("update_ov", {"ov_mode_enable": 0}),
            ("update_uv", {"uv_mode_enable": 0}),
            ("update_ov_mc", {"ov_mode_enable": 0}),
            ("update_uv_mc", {"uv_mode_enable": 0}),
            ("update_of", {"of_mode_enable": 0}),
            ("update_uf", {"uf_mode_enable": 0}),
            ("update_freq_watt", {"fw_mode_enable": 0}),
            ("update_connect", {"connected": True}),
            ("update_max_lim_pct_va_absorb", {"mode_enable": 0}),
            ("update_max_lim_pct_va_inject", {"mode_enable": 0}),
            ("update_max_lim_pct_var_absorb", {"mode_enable": 0}),
            ("update_max_lim_pct_var_inject", {"mode_enable": 0}),
            ("update_max_lim_pct_w_absorb", {"mode_enable": 0}),
            ("update_max_lim_var_absorb", {"mode_enable": 0}),
            ("update_max_lim_var_inject", {"mode_enable": 0}),
            ("update_target_v", {"mode_enable": 0}),
            ("update_target_var", {"mode_enable": 0}),
            ("update_target_w", {"mode_enable": 0}),
            ("update_delta_w", {"delta_w_mode_enable": 0}),
            ("update_delta_var", {"delta_var_mode_enable": 0}),
            ("update_fixed_v", {"fixed_v_mode_enable": 0}),
            ("update_grid_connect_permit", {"permit": True}),
            ("update_island_permit", {"permit": False}),
            ("update_exp_lim", {"exp_lim_mode_enable": 0}),
            ("update_imp_lim", {"imp_lim_mode_enable": 0}),
            ("update_gen_lim", {"gen_lim_mode_enable": 0}),
            ("update_load_lim", {"load_lim_mode_enable": 0}),
        ]

        for method_name, params in disable_calls:
            # Same distinction the translation loops draw, and it matters more
            # here: this fan-out touches every mode, and a clear is recorded like
            # any other write. Taking an inherited no-op for an implementation
            # would file twenty commands that reached no register, for a device
            # that implements a handful of modes.
            method, _reason = self._control_support(connector, method_name)
            if method is not None:
                await self._apply_one(
                    method,
                    method_name,
                    params,
                    lfdi=lfdi,
                    origin=CommandOrigin.COMMS_LOSS,
                    label=label,
                )
