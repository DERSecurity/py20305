"""Tests for TimeTariffInterval active-interval selection (events/tariff.py)."""

from __future__ import annotations

from types import SimpleNamespace as NS

from py20305.events.randomization import RandomizationCache
from py20305.events.tariff import active_interval, effective_window

HOUR = 3600


def _interval(
    mrid: bytes,
    start: int,
    duration: int = HOUR,
    *,
    creation: int = 0,
    status: int = 0,
    rand_start: int = 0,
    rand_dur: int = 0,
) -> NS:
    return NS(
        m_rid=NS(value=mrid),
        interval=NS(start=NS(value=start), duration=duration),
        creation_time=NS(value=creation),
        event_status=NS(current_status=status),
        randomize_start=NS(value=rand_start),
        randomize_duration=NS(value=rand_dur),
    )


class TestGetOffsetsWidening:
    def test_zero_randomization_for_tariff_interval(self):
        cache = RandomizationCache()
        tti = _interval(b"m1", 1000)
        assert cache.get_offsets(tti, 1000, HOUR) == (0, 0)

    def test_positive_randomization_in_range_and_cached(self):
        cache = RandomizationCache()
        tti = _interval(b"m1", 1000, rand_start=300)
        start_off, dur_off = cache.get_offsets(tti, 1000, HOUR)
        assert 0 <= start_off <= 300
        assert dur_off == 0
        # Cached: same offsets on the second call.
        assert cache.get_offsets(tti, 1000, HOUR) == (start_off, dur_off)


class TestEffectiveWindow:
    def test_no_randomization(self):
        cache = RandomizationCache()
        assert effective_window(_interval(b"m1", 1000, HOUR), cache) == (1000, 1000 + HOUR)

    def test_randomization_shifts_window_and_selection(self, monkeypatch):
        # Patch the specific randint the cache uses (deterministic, no global RNG
        # mutation that could make other tests order-dependent).
        monkeypatch.setattr(
            "py20305.events.randomization.random.randint", lambda a, b: 1800
        )
        cache = RandomizationCache()
        interval = _interval(b"r", 0, HOUR, rand_start=HOUR)
        start, end = effective_window(interval, cache)
        # A +1800s start offset shifts the window; duration is preserved.
        assert (start, end) == (1800, 1800 + HOUR)
        # active_interval uses the same cached offset, so selection respects the
        # randomized (not raw) boundaries.
        assert active_interval([interval], 1800, cache) is interval
        assert active_interval([interval], 1799, cache) is None
        assert active_interval([interval], end, cache) is None


class TestActiveInterval:
    def test_single_active_returned(self):
        cache = RandomizationCache()
        intervals = [
            _interval(b"a", 0),
            _interval(b"b", HOUR),
            _interval(b"c", 2 * HOUR),
        ]
        # now falls inside the second interval [HOUR, 2*HOUR).
        result = active_interval(intervals, HOUR + 100, cache)
        assert result is intervals[1]

    def test_none_when_now_outside_all(self):
        cache = RandomizationCache()
        intervals = [_interval(b"a", HOUR), _interval(b"b", 2 * HOUR)]
        assert active_interval(intervals, 0, cache) is None
        assert active_interval(intervals, 10 * HOUR, cache) is None

    def test_boundary_is_half_open(self):
        cache = RandomizationCache()
        intervals = [_interval(b"a", 0, HOUR), _interval(b"b", HOUR, HOUR)]
        # Exactly at HOUR belongs to the second interval (start inclusive), not
        # the first (end exclusive).
        assert active_interval(intervals, HOUR, cache) is intervals[1]

    def test_overlap_resolved_by_creation_time(self):
        cache = RandomizationCache()
        older = _interval(b"old", 0, 2 * HOUR, creation=100)
        newer = _interval(b"new", 0, 2 * HOUR, creation=200)
        # Both windows contain `now`; the newer creationTime wins.
        assert active_interval([older, newer], HOUR, cache) is newer
        assert active_interval([newer, older], HOUR, cache) is newer

    def test_cancelled_interval_skipped(self):
        cache = RandomizationCache()
        cancelled = _interval(b"x", 0, 2 * HOUR, status=2)
        active = _interval(b"y", 0, 2 * HOUR, creation=1)
        assert active_interval([cancelled, active], HOUR, cache) is active
        # With only a cancelled interval covering now, nothing is active.
        assert active_interval([cancelled], HOUR, cache) is None
