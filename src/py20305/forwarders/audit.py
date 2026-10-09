"""The control audit trail: event lifecycle and loss-of-communications records.

A utility program asks of a control client what it did with each control it
was sent: when an event was scheduled, when it went active and on which
devices, how it ended, and when the client stopped acting on the server
because the server went quiet. The protocol exchanges themselves are already
forwarded, but they record what the server *said*, not what this client
*decided*. These records are the decisions.

Each record is a flat JSON document published on its own topic, so a search
index stores every field as a field and an audit query hits one small index.

Delivery is best-effort, as for every stream on this transport. What this
module adds is the ability to tell a complete trail from an incomplete one:
every record carries ``boot_id`` and ``seq``, one counter per client shared
with that client's device-write records, so a gap in ``seq`` marks a lost
record and a new ``boot_id`` marks a restart.

Nothing here may raise into event processing. A trail that can stop a control
from being applied is worse than no trail.
"""

from __future__ import annotations

import itertools
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from py20305.forwarders.base import EventFrame

if TYPE_CHECKING:
    from py20305.forwarders.config import AuditConfig
    from py20305.forwarders.manager import ForwarderManager

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

    Shared by that client's device-write and lifecycle records, so a single gap
    check covers both streams. One per client rather than per process: two
    clients interleaving on one counter would each show a gap at every other
    record, which reads as loss.
    """

    def __init__(self) -> None:
        self.boot_id = uuid.uuid4().hex
        # next() on itertools.count is atomic under the GIL, so a write
        # recorded from a worker thread cannot reuse a number.
        self._counter = itertools.count(1)

    def stamp(self) -> dict[str, Any]:
        """The next ``boot_id`` and ``seq`` pair. Each call consumes a number."""
        return {"boot_id": self.boot_id, "seq": next(self._counter)}


class AuditEmitter:
    """Publishes lifecycle and loss-of-communications records.

    Built unconfigured and disabled, then pointed at the transport and the
    operator's configuration once both exist -- the same two-step wiring the
    device telemetry emitter uses, for the same reason: the forwarder is
    built after the client.
    """

    def __init__(
        self,
        forwarder: ForwarderManager | None = None,
        config: AuditConfig | None = None,
        *,
        client_id: str | None = None,
        sequence: AuditSequence | None = None,
    ) -> None:
        from py20305.forwarders.config import AuditConfig

        self._forwarder = forwarder
        self._config = config if config is not None else AuditConfig()
        self._client_id = client_id or ""
        self._sequence = sequence if sequence is not None else AuditSequence()
        #: Records that failed to build or queue, by kind. A trail that
        #: stopped working must not look like a client with nothing to report.
        self.emit_failures: dict[str, int] = {}

    @property
    def enabled(self) -> bool:
        """Whether records will actually be published."""
        return self._config.enabled and self._forwarder is not None

    @property
    def sequence(self) -> AuditSequence:
        """This client's counter. Hand it to the client's device telemetry
        emitter so device writes number in the same stream."""
        return self._sequence

    def attach_forwarder(self, forwarder: ForwarderManager | None) -> None:
        """Point the emitter at the transport once one exists."""
        self._forwarder = forwarder

    def configure(self, config: AuditConfig, *, client_id: str | None = None) -> None:
        """Apply operator configuration after construction."""
        self._config = config
        if client_id is not None:
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
            self._publish(record)
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

    def _publish(self, record: dict[str, Any]) -> None:
        assert self._forwarder is not None  # guarded by `enabled`
        record["schema"] = AUDIT_SCHEMA_VERSION
        record["client_id"] = self._client_id
        record.update(self._sequence.stamp())
        self._forwarder.queue_event(
            EventFrame(payload=record, topic_suffix=self._config.topic_suffix, kind="audit")
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
