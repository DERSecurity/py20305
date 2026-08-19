"""Control dispatcher protocol for applying DER controls to devices.

``NullDispatcher`` is the no-op default; ``ConnectorDispatcher`` is the
implementation backed by real connectors.
"""

from __future__ import annotations

from typing import Protocol

from py20305.commands import CommandOrigin
from py20305.connectors.base import ScheduleNotification
from py20305.models.sep.sep import (
    DefaultDercontrol,
    Dercontrol1,
    Dercurve1,
)


class ControlDispatcher(Protocol):
    """Interface for applying DER controls to devices."""

    async def apply_control(
        self,
        device_href: str,
        derc: Dercontrol1,
        curves: list[Dercurve1],
    ) -> None:
        """Apply a DERControl's settings to a device."""
        ...

    async def apply_default_control(
        self,
        device_href: str,
        dderc: DefaultDercontrol,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.DDERC_REAPPLY,
    ) -> None:
        """Apply default DER control (DDERC fallback) to a device.

        ``origin`` says why the default is being applied -- an event ending and
        an upstream outage land on the same write and read very differently in
        an audit trail.
        """
        ...

    async def clear_control(
        self,
        device_href: str,
    ) -> None:
        """Clear active control from a device."""
        ...

    async def apply_control_by_lfdi(
        self,
        lfdi: str,
        derc: Dercontrol1,
        curves: list[Dercurve1],
    ) -> None:
        """Apply a DERControl directly to a device identified by LFDI.

        Used when a caller supplies a group lookup, so a single
        server-side EndDevice href resolves to several locally-managed
        sub-device LFDIs.
        """
        ...

    async def apply_default_control_by_lfdi(
        self,
        lfdi: str,
        dderc: DefaultDercontrol,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.DDERC_REAPPLY,
    ) -> None:
        """Apply DDERC directly to a device identified by LFDI."""
        ...

    async def clear_control_by_lfdi(
        self,
        lfdi: str,
    ) -> None:
        """Clear active control from a device identified by LFDI.

        The by-LFDI counterpart of ``clear_control``, used by the comms-loss
        safe-default when a server-side EndDevice href resolves to several
        locally-managed sub-device LFDIs.
        """
        ...

    async def relay_schedule_notification(
        self,
        lfdis: list[str],
        notification: ScheduleNotification,
    ) -> None:
        """Relay a schedule notification to each connector owning an affected LFDI.

        Informational push (see ScheduleNotification). Resolves connectors,
        de-duplicates connector instances (one connector may own several LFDIs),
        and isolates per-connector errors so a throwing connector can't affect
        event processing or the others.
        """
        ...


class NullDispatcher:
    """Dispatcher that accepts every control and does nothing with it."""

    async def apply_control(
        self,
        device_href: str,
        derc: Dercontrol1,
        curves: list[Dercurve1],
    ) -> None:
        pass

    async def apply_default_control(
        self,
        device_href: str,
        dderc: DefaultDercontrol,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.DDERC_REAPPLY,
    ) -> None:
        pass

    async def clear_control(
        self,
        device_href: str,
    ) -> None:
        pass

    async def apply_control_by_lfdi(
        self,
        lfdi: str,
        derc: Dercontrol1,
        curves: list[Dercurve1],
    ) -> None:
        pass

    async def apply_default_control_by_lfdi(
        self,
        lfdi: str,
        dderc: DefaultDercontrol,
        curves: list[Dercurve1],
        *,
        origin: CommandOrigin = CommandOrigin.DDERC_REAPPLY,
    ) -> None:
        pass

    async def clear_control_by_lfdi(
        self,
        lfdi: str,
    ) -> None:
        pass

    async def relay_schedule_notification(
        self,
        lfdis: list[str],
        notification: ScheduleNotification,
    ) -> None:
        pass
