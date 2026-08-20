"""Command vocabulary, and the observer interface that records it.

A control reaching a device is one :class:`CommandRecord`: what was commanded, by
which interface, when, and whether the device has confirmed it. Monitoring
adapters read that state so a master can see what the command interface is doing
without being able to interfere, and an operator can tell *why* a setpoint moved.

:class:`CommandObserver` exists so the write path can say "this was commanded"
without naming who keeps the record. The write funnels are published client code
and the plane that stores the records is not, so the dependency has to point
down: the funnels depend on this Protocol, and the host application injects the
implementation. :class:`NullCommandObserver` is the default, which is what lets
an embedded consumer run the same write path with no plane at all.

Reflection is deliberately *not* modeled here as a value the device reports.
A device may clamp a setpoint, ramp toward it, or reject it, so commanded and
achieved routinely differ; the record says what was asked for and how far the
confirmation has got, and never pretends the two are the same.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class CommandOrigin(StrEnum):
    """Where a command entered the system.

    The two external origins are command interfaces. The two internal
    ones are not external commands at all -- they are the client reasserting
    state it already owns -- but they change the device just as visibly, and
    leaving them unrecorded is what makes a setpoint appear to move on its own.
    """

    IEEE2030_5 = "ieee2030_5"
    LOCAL_API = "local_api"
    #: A SunSpec Modbus master writing a control register. Its own origin because
    #: "who moved this setpoint" is the question the record exists to answer, and
    #: a second command protocol is exactly when that stops being obvious.
    SUNSPEC = "sunspec"
    #: DdercTracker reapplying DefaultDERControl on the fallback path after an
    #: event completes. The classic "my setpoint reverted and nothing says why".
    DDERC_REAPPLY = "dderc_reapply"
    #: Loss-of-communications handling reasserting the planning limit.
    COMMS_LOSS = "comms_loss"


class CommandStatus(StrEnum):
    """How far a command has got.

    ``UNCONFIRMED`` is the honest default and often the terminal state: the
    connector contract exposes no general setpoint readback, so most controls
    have nothing to confirm against. It means "we asked and the write returned
    without error", not "the device is doing it".
    """

    UNCONFIRMED = "unconfirmed"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """One control applied to one device."""

    #: The connector operation, e.g. ``p_lim`` or ``es_permit_service``. Keyed on
    #: the operation rather than a measured quantity: a limit and the power that
    #: results from it are different things (see the module docstring).
    control: str
    #: What was sent, verbatim. Curve modes carry point lists here; nothing in
    #: this layer interprets the shape.
    params: Mapping[str, Any]
    origin: CommandOrigin
    #: Wall-clock epoch seconds at which the write was issued. Compared against
    #: an observation's read-start time to decide whether that observation could
    #: possibly reflect this command.
    commanded_at: float
    status: CommandStatus
    confirmed_at: float | None = None
    #: What the connector raised, when status is REJECTED.
    error: str | None = None


@runtime_checkable
class CommandObserver(Protocol):
    """Where the write path reports what it did.

    Two operations, because confirmation arrives separately from the command and
    usually from a different code path: the write funnel knows what was asked
    for, and whoever polls the device later knows what it says about itself.
    """

    def record_command(
        self,
        device: str,
        control: str,
        params: Mapping[str, Any],
        *,
        origin: CommandOrigin,
        at: float,
        error: str | None = None,
    ) -> None:
        """Record that ``control`` was applied to ``device``, or failed to be."""
        ...

    def record_readback(
        self, device: str, observed: Mapping[str, Any], *, read_started_at: float
    ) -> None:
        """Offer a device's self-reported state as confirmation evidence.

        ``read_started_at`` is when the read that produced ``observed`` *began*,
        not when it completed. An observation that started before a command was
        issued cannot reflect it, however recent its arrival, and is what the
        implementation uses to keep an in-flight poll from clearing a command it
        never saw.
        """
        ...


class NullCommandObserver:
    """Discards everything. The default where no plane is wired.

    Published write paths run unchanged in a deployment that keeps no commanded
    state -- an embedded single-upstream consumer, or any test that cares about
    the write rather than the record.
    """

    def record_command(
        self,
        device: str,
        control: str,
        params: Mapping[str, Any],
        *,
        origin: CommandOrigin,
        at: float,
        error: str | None = None,
    ) -> None:
        """No-op."""

    def record_readback(
        self, device: str, observed: Mapping[str, Any], *, read_started_at: float
    ) -> None:
        """No-op."""


@runtime_checkable
class CommandGate(Protocol):
    """Whether an origin may command a device.

    A deployment may serve several command interfaces while intending only one of
    them to command any given device. Deciding that in configuration alone leaves
    it unenforced at the write: every interface still reaches the dispatcher, so
    one interface's events and another's setpoints overwrite each other while the
    configuration reads as though a single one were in charge.

    Injected rather than imported, for the same reason :class:`CommandObserver`
    is. Which interface holds authority over a device is the consuming
    application's concern, and the dispatcher is client code, so the dependency
    points that way.
    """

    def may_command(self, device: str, origin: CommandOrigin) -> bool:
        """Whether ``origin`` holds the command role for ``device``."""
        ...


class AllowAllCommands:
    """Permits everything. The default where no authority is wired.

    A consumer with a single command interface, or a test concerned with the
    write rather than with who was allowed to make it, behaves exactly as it did
    before the gate existed.
    """

    def may_command(self, device: str, origin: CommandOrigin) -> bool:
        """Always True."""
        return True


class CommandNotPermittedError(Exception):
    """A gate refused this origin authority over this device.

    Distinct from a connector error because the cause and the remedy are
    different: nothing is wrong with the device or the transport, and the caller
    cannot retry its way out of it. A caller serving a protocol client needs the
    distinction to answer that client honestly -- refused is not the same answer
    as failed.

    Raised only by the entry points whose caller has somewhere to put it. A
    control arriving from an event engine is reported and dropped instead, since
    an interface posting to a device another one commands is a configuration
    being honored rather than a fault.
    """
