"""Tests for EventState, EventRecord, and EventStore."""

from __future__ import annotations

import pytest

from py20305.events.state_machine import EventRecord, EventState, EventStore
from py20305.models.sep.sep import (
    DateTimeInterval,
    Dercontrol1,
    DercontrolBase,
    EventStatus,
    MRidtype,
    TimeType,
)


def _make_derc(mrid_byte: int = 0x01, start: int = 1000, duration: int = 3600) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=bytes([mrid_byte]) * 16),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=0,
            date_time=TimeType(value=950),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=duration, start=TimeType(value=start)),
        dercontrol_base=DercontrolBase(),
    )


def _make_record(
    mrid_byte: int = 0x01,
    state: EventState = EventState.SCHEDULED,
    start: int = 1000,
    duration: int = 3600,
    primacy: int = 0,
    server_status_time: int = 950,
) -> EventRecord:
    return EventRecord(
        mrid=bytes([mrid_byte]) * 16,
        derc=_make_derc(mrid_byte, start, duration),
        program_href="/derp/1",
        primacy=primacy,
        state=state,
        start=start,
        duration=duration,
        server_status_time=server_status_time,
    )


class TestEventRecord:
    def test_end_property(self):
        r = _make_record(start=1000, duration=3600)
        assert r.end == 4600

    def test_superseded_devices_default_empty(self):
        r = _make_record()
        assert r.superseded_devices == set()

    def test_opted_out_defaults_false(self):
        r = _make_record()
        assert r.opted_out is False


class TestEventStore:
    def test_upsert_and_get(self):
        store = EventStore()
        rec = _make_record()
        store.upsert(rec)
        assert store.get(rec.mrid) is rec

    def test_get_missing_returns_none(self):
        store = EventStore()
        assert store.get(b"\xff" * 16) is None

    def test_upsert_overwrites(self):
        store = EventStore()
        r1 = _make_record()
        r2 = _make_record()
        r2.primacy = 5
        store.upsert(r1)
        store.upsert(r2)
        assert store.get(r1.mrid).primacy == 5
        assert len(store) == 1

    def test_by_state(self):
        store = EventStore()
        store.upsert(_make_record(0x01, EventState.SCHEDULED))
        store.upsert(_make_record(0x02, EventState.ACTIVE))
        store.upsert(_make_record(0x03, EventState.SCHEDULED))
        assert len(store.by_state(EventState.SCHEDULED)) == 2
        assert len(store.by_state(EventState.ACTIVE)) == 1
        assert len(store.by_state(EventState.COMPLETED)) == 0

    def test_transition(self):
        store = EventStore()
        rec = _make_record(state=EventState.SCHEDULED)
        store.upsert(rec)
        result = store.transition(rec.mrid, EventState.ACTIVE)
        assert result.state == EventState.ACTIVE
        assert store.get(rec.mrid).state == EventState.ACTIVE

    def test_transition_missing_raises(self):
        store = EventStore()
        with pytest.raises(KeyError):
            store.transition(b"\xff" * 16, EventState.ACTIVE)

    def test_all_active_states(self):
        store = EventStore()
        store.upsert(_make_record(0x01, EventState.SCHEDULED))
        store.upsert(_make_record(0x02, EventState.ACTIVE))
        store.upsert(_make_record(0x03, EventState.COMPLETED))
        store.upsert(_make_record(0x04, EventState.CANCELLED))
        assert len(store.all_active_states()) == 2

    def test_prune_expired(self):
        store = EventStore()
        # Event ends at 1000 + 3600 = 4600, grace = 60 -> prune after 4660
        store.upsert(_make_record(0x01, EventState.COMPLETED, start=1000, duration=3600))
        # Not expired yet
        assert store.prune_expired(4660) == []
        assert len(store) == 1
        # Expired (4661 > 4660)
        pruned = store.prune_expired(4661)
        assert len(pruned) == 1
        assert pruned[0] == bytes([0x01]) * 16
        assert len(store) == 0

    def test_prune_expired_keeps_recent(self):
        store = EventStore()
        store.upsert(_make_record(0x01, start=1000, duration=3600))
        store.upsert(_make_record(0x02, start=5000, duration=3600))
        # Only first is expired at now=4661 (end 4600 + grace 60)
        pruned = store.prune_expired(4661)
        assert len(pruned) == 1
        assert len(store) == 1

    def test_contains(self):
        store = EventStore()
        rec = _make_record()
        store.upsert(rec)
        assert rec.mrid in store
        assert b"\xff" * 16 not in store

    def test_len(self):
        store = EventStore()
        assert len(store) == 0
        store.upsert(_make_record(0x01))
        assert len(store) == 1
        store.upsert(_make_record(0x02))
        assert len(store) == 2
