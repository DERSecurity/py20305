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
    #: Under a group lookup, each local device's dispatch outcome by its LFDI,
    #: for the audit trail. The two sets above are in the server's terms: one
    #: EndDevice stands for several local devices, its response reports them as
    #: one, and a partial failure there still reads as applied. Empty without a
    #: group lookup, where the sets above already name each device.
    applied_local: set[str] = field(default_factory=set)
    rejected_local: set[str] = field(default_factory=set)
    #: Set when the event was opted out during loss-of-communications mode: its
    #: control is not applied (or is reverted to DDERC) and no response is
    #: posted. Stays set until the record is pruned (recovery clears only the
    #: shared CommsLossState, not this flag) so the event never resumes and stays
    #: excluded from "other active" DDERC checks. The state stays SCHEDULED/ACTIVE
    #: so supersession/classification are unaffected.
    opted_out: bool = False
    #: Each EndDevice's effective window, ``device href -> (start, end)``.
    #: IEEE 2030.5-2023 §10.2.3.2.2 and §10.2.3.2.3 have each device draw its
    #: own start and duration randomization, so devices of one event start and
    #: end at different moments. ``start`` and ``duration`` above then span
    #: every window. Empty for an event whose program has no device mapping:
    #: it runs on ``start`` and ``end``.
    device_windows: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: Devices whose window has started here (the control was dispatched or
    #: the device was found superseded at its start).
    started_devices: set[str] = field(default_factory=set)
    #: Devices whose window has ended here (COMPLETED and fallback handled).
    ended_devices: set[str] = field(default_factory=set)

    @property
    def end(self) -> int:
        return self.start + self.duration

    def window(self, device_href: str) -> tuple[int, int]:
        """Return the effective ``(start, end)`` of the event on one device."""
        return self.device_windows.get(device_href, (self.start, self.end))

    def set_window(self, device_href: str, start: int, end: int) -> None:
        """Record one device's window and widen ``start``/``duration`` to cover every window."""
        self.device_windows[device_href] = (start, end)
        first = min(s for s, _ in self.device_windows.values())
        last = max(e for _, e in self.device_windows.values())
        self.start = first
        self.duration = last - first

    def runs_on(self, device_href: str) -> bool:
        """Return whether the event is in its window on ``device_href`` here.

        False before the device's own start and after its own end. A device
        without a window of its own follows the event's state.
        """
        if device_href in self.ended_devices:
            return False
        return device_href not in self.device_windows or device_href in self.started_devices


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

    def remove(self, mrid: bytes) -> None:
        """Forget an event without a state change. An unknown mRID is not an error."""
        self._events.pop(mrid, None)

    def values(self) -> list[EventRecord]:
        """Return every stored event, whatever its state."""
        return list(self._events.values())

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
