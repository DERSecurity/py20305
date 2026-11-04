"""The control audit trail: event lifecycle and loss-of-communications records.

A utility program asks of a control client what it did with each control it
was sent: when an event was scheduled, when it went active and on which
devices, how it ended, and when the client stopped acting on the server
because the server went quiet. The exchange records say what the server
*said*, not what this client *decided*. These records are the decisions.

Each record is a flat document. It is kept in the operations history when one
is attached, and handed to the audit observer as an
:class:`~py20305.observers.AuditRecord` when one is attached. Either, both or
neither may be attached.

Every record carries ``boot_id`` and ``seq``, one counter per client, so a gap
in ``seq`` marks a lost record and a new ``boot_id`` marks a restart.

Nothing here may raise into event processing. A trail that can stop a control
from being applied is worse than no trail.
"""

from __future__ import annotations

import itertools
import logging
import re
import uuid
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from py20305.observers import AuditObserver, AuditRecord, ObserverGuard

if TYPE_CHECKING:
    from py20305.history.events import EventHistory

logger = logging.getLogger(__name__)

#: Version of the record shapes below. Bumped on any change a consumer would
#: have to handle.
AUDIT_SCHEMA_VERSION = 1


def mrid_hex(mrid: bytes | None) -> str | None:
    """An mRID as uppercase hex, the form the ``mRID`` element takes on the wire.

    Matching the wire form lets a record be joined to the raw control by a
    plain text match on the identifier.
    """
    return mrid.hex().upper() if mrid is not None else None


_HEX = re.compile(r"[0-9A-Fa-f]+")


def _lfdi(value: str) -> str:
    """An LFDI as lowercase hex, whatever case the host or the server used.

    The records join on these, so two spellings of one device would read as two
    devices. A device whose LFDI is not known is named by its EndDevice href,
    which is a case-sensitive URI and is left exactly as the server wrote it.
    """
    return value.lower() if _HEX.fullmatch(value) else value


class AuditSequence:
    """One client's audit counter: a random ``boot_id`` and a rising ``seq``.

    One per client rather than per process: two clients interleaving on one
    counter would each show a gap at every other record, which reads as loss.
    """

    def __init__(self) -> None:
        self.boot_id = uuid.uuid4().hex
        # next() on itertools.count is atomic under the GIL, so a record
        # made from a worker thread cannot reuse a number.
        self._counter = itertools.count(1)

    def stamp(self) -> dict[str, Any]:
        """The next ``boot_id`` and ``seq`` pair. Each call consumes a number."""
        return {"boot_id": self.boot_id, "seq": next(self._counter)}


class AuditEmitter:
    """Builds lifecycle and loss-of-communications records, and hands them on.

    Disabled until an operations history or an audit observer is attached.
    """

    def __init__(
        self,
        observer: AuditObserver | None = None,
        *,
        client_id: str | None = None,
        sequence: AuditSequence | None = None,
    ) -> None:
        self._observer = observer
        self._guard = ObserverGuard("audit observer")
        self._client_id = client_id or ""
        self._sequence = sequence if sequence is not None else AuditSequence()
        self._history: EventHistory | None = None
        #: Records that failed to build or store, by kind. A trail that
        #: stopped working must not look like a client with nothing to report.
        self.emit_failures: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        """Whether records go anywhere: to the observer, the history, or both."""
        return self._observer is not None or self._history is not None

    @property
    def observing(self) -> bool:
        """Whether an audit observer is attached."""
        return self._observer is not None

    @property
    def observer_failures(self) -> int:
        """How many times the audit observer raised."""
        return self._guard.failures

    @property
    def keeps_history(self) -> bool:
        """Whether records are kept in the local operations history."""
        return self._history is not None

    def attach_history(self, history: EventHistory | None) -> None:
        """Keep every record in ``history`` as well, whether or not it is observed."""
        self._history = history

    def attach_observer(self, observer: AuditObserver | None) -> None:
        """Hand every record to ``observer``, or stop with None."""
        self._observer = observer

    @property
    def sequence(self) -> AuditSequence:
        """This client's counter."""
        return self._sequence

    def set_client_id(self, client_id: str) -> None:
        """Name the client in every record, usually by its certificate's LFDI."""
        self._client_id = client_id

    def event_transition(
        self,
        *,
        event_mrid: bytes,
        program_href: str,
        from_state: str | None,
        to_state: str,
        effective_start: int,
        effective_duration: int,
        primacy: int,
        lfdis: list[str],
        at: float,
        applied_lfdis: list[str] | None = None,
        rejected_lfdis: list[str] | None = None,
        superseded_by: bytes | None = None,
        superseded_lfdis: list[str] | None = None,
        superseded_modes: dict[str, list[str]] | None = None,
        control: Mapping[str, Any] | None = None,
    ) -> None:
        """Record one event moving from one state to another.

        Args:
            event_mrid: The event's mRID.
            program_href: The program the event belongs to.
            from_state: The state before, or ``None`` for an event first seen.
            to_state: ``scheduled``, ``active``, ``completed``, ``cancelled``,
                ``superseded``, ``opted_out``, ``expired`` (already over when
                first received) or ``skipped`` (inside a loss-of-communications
                window the client opted out of).
            effective_start: Start after randomization, epoch seconds.
            effective_duration: Duration after randomization, seconds.
            primacy: The program's primacy.
            lfdis: The devices the event targets.
            at: When the transition happened, on the server's timebase.
            applied_lfdis: On ``active``, the devices whose dispatch returned
                without error -- what was reported to the server as started.
            rejected_lfdis: On ``active``, the devices whose dispatch failed or
                missed the activation deadline.
            superseded_by: On ``superseded``, the superseding event.
            superseded_lfdis: On a partial supersession, the devices it covers.
            superseded_modes: On a partial supersession, the modes per device.
            control: The control's description and settings, kept in the local
                history only. Published records keep their existing shape.
        """
        if not self.enabled:
            return
        try:
            record: dict[str, Any] = {
                "kind": "der_event",
                "event_mrid": mrid_hex(event_mrid),
                "program_href": program_href,
                "from_state": from_state,
                "to_state": to_state,
                "effective_start": effective_start,
                "effective_duration": effective_duration,
                "primacy": primacy,
                "lfdis": sorted(_lfdi(x) for x in lfdis),
                "at": at,
            }
            if applied_lfdis is not None:
                record["applied_lfdis"] = sorted(_lfdi(x) for x in applied_lfdis)
            if rejected_lfdis is not None:
                record["rejected_lfdis"] = sorted(_lfdi(x) for x in rejected_lfdis)
            if superseded_by is not None:
                record["superseded_by"] = mrid_hex(superseded_by)
            if superseded_lfdis is not None:
                record["superseded_lfdis"] = sorted(_lfdi(x) for x in superseded_lfdis)
            if superseded_modes is not None:
                record["superseded_modes"] = {
                    _lfdi(dev): sorted(modes) for dev, modes in superseded_modes.items()
                }
            self._publish(record, control=control)
        except Exception:
            self._failed("der_event")

    def comms_loss(
        self,
        *,
        transition: str,
        threshold: int,
        simulated: bool,
        at: float,
        elapsed_seconds: int | None = None,
        duration_seconds: int | None = None,
        resume_after: int | None = None,
    ) -> None:
        """Record a step into or out of loss-of-communications mode.

        Published in the order things happen: ``entered`` before any event is
        opted out, ``recovering`` once contact returns and before the schedule
        is polled again, ``cleared`` once the mode ends. A recovery that fails
        is retried, so ``recovering`` can repeat before one ``cleared``.

        Args:
            transition: ``entered``, ``recovering`` or ``cleared``.
            threshold: The configured silence threshold.
            simulated: Whether an operator-triggered simulation caused it.
            at: When it happened, on the server's timebase.
            elapsed_seconds: On ``entered``, how long the server had been
                silent.
            duration_seconds: On ``recovering`` and ``cleared``, how long the
                client had been in the mode.
            resume_after: On ``cleared``, the epoch after which the schedule
                resumes, when the opted-out window has not yet passed.
        """
        if not self.enabled:
            return
        try:
            record: dict[str, Any] = {
                "kind": "comms_loss",
                "transition": transition,
                "threshold": threshold,
                "simulated": simulated,
                "at": at,
            }
            if transition == "entered":
                record["elapsed_seconds"] = elapsed_seconds
            else:
                record["duration_seconds"] = duration_seconds
            if transition == "cleared":
                record["resume_after"] = resume_after
            self._publish(record)
        except Exception:
            self._failed("comms_loss")

    def late_dispatch(
        self,
        *,
        event_mrid: bytes,
        lfdi: str,
        applied: bool,
        at: float,
        error: str | None = None,
        error_type: str | None = None,
    ) -> None:
        """Record how a dispatch that missed the activation deadline ended.

        The ``active`` record lists such a device as rejected, because that is
        what the server was told. The apply carries on regardless, and this is
        the record of whether it then reached the device.

        Args:
            event_mrid: The event whose control it was.
            lfdi: The device, or its href where no LFDI is known.
            applied: Whether the apply finished without error.
            at: When it finished, on the server's timebase.
            error: The failure, when it failed.
            error_type: The failure's exception class name.
        """
        if not self.enabled:
            return
        try:
            record: dict[str, Any] = {
                "kind": "late_dispatch",
                "event_mrid": mrid_hex(event_mrid),
                "lfdi": _lfdi(lfdi),
                "applied": applied,
                "at": at,
            }
            if error is not None:
                record["error"] = error[:512]
                record["error_type"] = error_type
            self._publish(record)
        except Exception:
            self._failed("late_dispatch")

    def _publish(self, record: dict[str, Any], *, control: Mapping[str, Any] | None = None) -> None:
        record["schema"] = AUDIT_SCHEMA_VERSION
        record["client_id"] = self._client_id
        record.update(self._sequence.stamp())
        if self._history is not None and not self._history.record(record, control=control):
            self._failed(f"{record['kind']} history")
        if self._observer is not None:
            self._guard.deliver(
                self._observer,
                AuditRecord(kind=str(record["kind"]), data=MappingProxyType(dict(record))),
            )

    def _failed(self, kind: str) -> None:
        count = self.emit_failures.get(kind, 0) + 1
        self.emit_failures[kind] = count
        if count == 1:
            logger.warning(
                "Audit trail failed to record a %s; further failures log at debug",
                kind,
                exc_info=True,
            )
        else:
            logger.debug("Audit trail failed to record a %s", kind, exc_info=True)
