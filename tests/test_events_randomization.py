"""Tests for RandomizationCache."""

from __future__ import annotations

from unittest.mock import patch

from py20305.events.randomization import RandomizationCache
from py20305.models.sep.sep import (
    DateTimeInterval,
    Dercontrol1,
    DercontrolBase,
    EventStatus,
    MRidtype,
    OneHourRangeType,
    TimeType,
)


def _ohr(val: int) -> OneHourRangeType:
    return OneHourRangeType(value=val)


def _make_derc(
    mrid_byte: int = 0x01,
    randomize_start: int | None = None,
    randomize_duration: int | None = None,
) -> Dercontrol1:
    return Dercontrol1(
        m_rid=MRidtype(value=bytes([mrid_byte]) * 16),
        creation_time=TimeType(value=900),
        event_status=EventStatus(
            current_status=0,
            date_time=TimeType(value=950),
            potentially_superseded=False,
        ),
        interval=DateTimeInterval(duration=3600, start=TimeType(value=1000)),
        dercontrol_base=DercontrolBase(),
        randomize_start=_ohr(randomize_start) if randomize_start is not None else None,
        randomize_duration=_ohr(randomize_duration) if randomize_duration is not None else None,
    )


class TestRandomizationCache:
    def test_no_randomization_returns_zero(self):
        cache = RandomizationCache()
        derc = _make_derc()
        s, d = cache.get_offsets(derc, 1000, 3600)
        assert s == 0
        assert d == 0

    def test_zero_randomization_returns_zero(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=0, randomize_duration=0)
        s, d = cache.get_offsets(derc, 1000, 3600)
        assert s == 0
        assert d == 0

    def test_determinism_same_mrid(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=100, randomize_duration=50)
        first = cache.get_offsets(derc, 1000, 3600)
        second = cache.get_offsets(derc, 1000, 3600)
        assert first == second

    def test_positive_range(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=100)
        s, _ = cache.get_offsets(derc, 1000, 3600)
        assert 0 <= s <= 100

    def test_negative_preserves_sign(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=-200)
        s, _ = cache.get_offsets(derc, 1000, 3600)
        assert -200 <= s <= 0

    def test_duration_offset(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_duration=60)
        _, d = cache.get_offsets(derc, 1000, 3600)
        assert 0 <= d <= 60

    def test_different_mrids_independent(self):
        cache = RandomizationCache()
        d1 = _make_derc(0x01, randomize_start=3600)
        d2 = _make_derc(0x02, randomize_start=3600)
        o1 = cache.get_offsets(d1, 1000, 3600)
        o2 = cache.get_offsets(d2, 1000, 3600)
        # Can't assert they're different (probabilistic), but both should be cached
        assert len(cache) == 2
        # Each returns same value on re-query
        assert cache.get_offsets(d1, 1000, 3600) == o1
        assert cache.get_offsets(d2, 1000, 3600) == o2

    def test_prune_removes_expired(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=0, randomize_duration=0)
        cache.get_offsets(derc, 1000, 3600)
        assert len(cache) == 1
        # end = 1000 + 0 + 3600 + 0 = 4600, grace=10 -> prune after 4610
        cache.prune(4610)
        assert len(cache) == 1
        cache.prune(4611)
        assert len(cache) == 0

    def test_prune_keeps_recent(self):
        cache = RandomizationCache()
        d1 = _make_derc(0x01)
        d2 = _make_derc(0x02)
        cache.get_offsets(d1, 1000, 3600)  # ends 4600
        cache.get_offsets(d2, 5000, 3600)  # ends 8600
        cache.prune(4611)
        assert len(cache) == 1

    def test_magnitude_one(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=1)
        s, _ = cache.get_offsets(derc, 1000, 3600)
        assert s in (0, 1)

    def test_negative_magnitude_one(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=-1)
        s, _ = cache.get_offsets(derc, 1000, 3600)
        assert s in (-1, 0)

    def test_zero_offset_accepted(self):
        """IEEE 2030.5: randomization range includes zero."""
        with patch("py20305.events.randomization.random.randint", return_value=0):
            cache = RandomizationCache()
            derc = _make_derc(randomize_start=100)
            s, _ = cache.get_offsets(derc, 1000, 3600)
            assert s == 0


class TestOverrideStartOffset:
    """IEEE 10.2.2.3 rule m): successive events override start offset."""

    def test_override_changes_cached_start(self):
        cache = RandomizationCache()
        derc = _make_derc(randomize_start=100, randomize_duration=50)
        cache.get_offsets(derc, 1000, 3600)
        mrid = derc.m_rid.value

        cache.override_start_offset(mrid, new_start_offset=42, raw_start=1000, raw_duration=3600)
        s, d = cache.get_offsets(derc, 1000, 3600)
        assert s == 42
        # Duration offset should be unchanged
        assert 0 <= d <= 50

    def test_override_noop_when_not_cached(self):
        cache = RandomizationCache()
        # Override a non-existent mRID should not raise
        cache.override_start_offset(b"\xff" * 16, 42, 1000, 3600)
        assert len(cache) == 0
