"""Per-mRID randomization of event start time and duration, drawn once and reused."""

from __future__ import annotations

import random
from collections.abc import Callable

from py20305.models.sep.sep import OneHourRangeType, RandomizableEvent


class RandomizationCache:
    """Cache randomized offsets per mRID so an event keeps one timing.

    Offsets are drawn at random once per mRID and reused on subsequent calls.
    The cache is prunable to avoid unbounded memory growth.

    ``claim`` shares the offsets beyond this process: it is called with each
    mRID and the offsets just drawn, and returns the offsets to use, which are
    the first ever stored for that mRID (see
    :meth:`~py20305.events.shared_state.EventStateStore.claim_offsets`). None
    keeps them in this cache only.
    """

    def __init__(
        self, claim: Callable[[bytes, tuple[int, int]], tuple[int, int]] | None = None
    ) -> None:
        self._cache: dict[bytes, tuple[int, int]] = {}
        self._expiry: dict[bytes, int] = {}
        self._claim = claim

    def get_offsets(
        self,
        event: RandomizableEvent,
        start: int,
        duration: int,
        *,
        start_offset: int | None = None,
    ) -> tuple[int, int]:
        """Return (start_offset, duration_offset) for the given randomizable event.

        Works for any ``RandomizableEvent`` (DERControl, TimeTariffInterval, ...)
        since it only reads the base ``m_rid`` / ``randomize_start`` /
        ``randomize_duration`` fields. Offsets are cached per mRID. Range is
        [0, abs(value)] with sign preserved from the randomization field. Zero
        randomization fields produce zero offsets.

        ``start_offset``, when given, replaces the drawn start offset of an
        event not cached yet, before the offsets are claimed: a successive
        event's start follows its predecessor's end.
        """
        mrid = event.m_rid.value
        if mrid in self._cache:
            return self._cache[mrid]

        if start_offset is None:
            start_offset = self._compute_offset(event.randomize_start)
        dur_offset = self._compute_offset(event.randomize_duration)
        if self._claim is not None:
            start_offset, dur_offset = self._claim(mrid, (start_offset, dur_offset))

        self._cache[mrid] = (start_offset, dur_offset)
        # Track expiry based on effective end time
        self._expiry[mrid] = start + start_offset + duration + dur_offset
        return (start_offset, dur_offset)

    def prune(self, now: int, grace: int = 10) -> list[bytes]:
        """Remove cache entries for events that have expired. Return their mRIDs."""
        expired = [mrid for mrid, end in self._expiry.items() if end + grace < now]
        for mrid in expired:
            del self._cache[mrid]
            del self._expiry[mrid]
        return expired

    def seed(self, mrid: bytes, offsets: tuple[int, int], end: int) -> None:
        """Use ``offsets`` for ``mrid``, an event loaded with its timing already drawn."""
        self._cache[mrid] = offsets
        self._expiry[mrid] = end

    @staticmethod
    def _compute_offset(value: OneHourRangeType | None) -> int:
        """Compute a random offset from the randomization field value.

        Range is [0, abs(value)], sign preserved. Returns 0 if value is
        None or 0.
        """
        if value is None or value.value == 0:
            return 0
        raw = value.value
        magnitude = abs(raw)
        sign = 1 if raw > 0 else -1
        return sign * random.randint(0, magnitude)

    def override_start_offset(
        self,
        mrid: bytes,
        new_start_offset: int,
        raw_start: int,
        raw_duration: int,
    ) -> None:
        """Override the cached start offset for an event.

        Used by IEEE 10.2.2.3 rule m) to prevent randomization gaps
        between successive events: the earlier event's effective end time
        becomes the later event's effective start time.
        """
        if mrid not in self._cache:
            return
        _, dur_offset = self._cache[mrid]
        self._cache[mrid] = (new_start_offset, dur_offset)
        self._expiry[mrid] = raw_start + new_start_offset + raw_duration + dur_offset

    def __len__(self) -> int:
        return len(self._cache)
