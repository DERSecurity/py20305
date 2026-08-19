"""Event state machine: EventState enum, EventRecord, and EventStore."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from py20305.models.sep.sep import Dercontrol1


class EventState(enum.Enum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"


@dataclass
class EventRecord:
    """Single event tracked in the store, keyed by mRID."""

    mrid: bytes
    derc: Dercontrol1
    program_href: str
    primacy: int
    state: EventState
    start: int  # effective (post-randomization) epoch seconds
    duration: int  # effective (post-randomization) seconds
    server_status_time: int  # EventStatus.dateTime (legacy, kept for compat)
    creation_time: int = 0  # Event.creationTime for IEEE tie-breaking
    superseded_devices: set[str] = field(default_factory=set)
    superseded_modes: dict[str, frozenset[str]] = field(default_factory=dict)
    #: Devices whose control dispatch failed at activation, so they were sent a
    #: rejection (251/252/253) instead of ACTIVE. They never ran the event, so they
    #: are excluded from the COMPLETED response at its end.
    rejected_devices: set[str] = field(default_factory=set)
    #: Devices that were told the event started -- their dispatch returned
    #: successfully. COMPLETED is posted to exactly this set, rather than to the
    #: program's current device list minus the rejected ones: the device list can
    #: change under a mid-event rediscovery, and a device that was never told
    #: ACTIVE must not be told the event completed. Empty alongside an empty
    #: ``rejected_devices`` means dispatch never had a target at all, which is the
    #: one case where the any-LFDI fallback still announces the lifecycle.
    applied_devices: set[str] = field(default_factory=set)
    #: Set when the event was opted out during loss-of-communications mode: its
    #: control is not applied (or is reverted to DDERC) and no response is
    #: posted. Stays set until the record is pruned (recovery clears only the
    #: shared CommsLossState, not this flag) so the event never resumes and stays
    #: excluded from "other active" DDERC checks. The state stays SCHEDULED/ACTIVE
    #: so supersession/classification are unaffected.
    opted_out: bool = False

    @property
    def end(self) -> int:
        return self.start + self.duration


class EventStore:
    """Single-dict event store keyed by mRID.

    Replaces the original's 4+ separate dicts, eliminating move-related races.
    """

    def __init__(self) -> None:
        self._events: dict[bytes, EventRecord] = {}

    def upsert(self, record: EventRecord) -> EventRecord:
        """Insert or update an event record. Returns the stored record."""
        self._events[record.mrid] = record
        return record

    def get(self, mrid: bytes) -> EventRecord | None:
        return self._events.get(mrid)

    def by_state(self, state: EventState) -> list[EventRecord]:
        return [r for r in self._events.values() if r.state == state]

    def transition(self, mrid: bytes, new_state: EventState) -> EventRecord:
        """Transition an event to a new state. Raises KeyError if not found."""
        record = self._events[mrid]
        record.state = new_state
        return record

    def all_active_states(self) -> list[EventRecord]:
        """Return events that are scheduled or active (for supersession)."""
        return [
            r for r in self._events.values() if r.state in (EventState.SCHEDULED, EventState.ACTIVE)
        ]

    def prune_expired(self, now: int, grace: int = 60) -> list[bytes]:
        """Remove events whose end time + grace period has passed.

        The grace window keeps a just-ended event in the store long enough that
        an immediate re-poll still recognises it as already-handled rather than
        re-classifying it as a new (expired) event. Returns list of pruned mRIDs.
        """
        expired: list[bytes] = []
        for mrid, record in list(self._events.items()):
            if record.end + grace < now:
                expired.append(mrid)
                del self._events[mrid]
        return expired

    def __len__(self) -> int:
        return len(self._events)

    def __contains__(self, mrid: bytes) -> bool:
        return mrid in self._events
