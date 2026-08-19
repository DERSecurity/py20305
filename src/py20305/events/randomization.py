"""Deterministic per-mRID randomization of event start time and duration."""

from __future__ import annotations

import random

from py20305.models.sep.sep import OneHourRangeType, RandomizableEvent


class RandomizationCache:
    """Cache randomized offsets per mRID for session determinism.

    Offsets are generated once per mRID and reused on subsequent calls.
    The cache is prunable to avoid unbounded memory growth.
    """

    def __init__(self) -> None:
        self._cache: dict[bytes, tuple[int, int]] = {}
        self._expiry: dict[bytes, int] = {}

    def get_offsets(self, event: RandomizableEvent, start: int, duration: int) -> tuple[int, int]:
        """Return (start_offset, duration_offset) for the given randomizable event.

        Works for any ``RandomizableEvent`` (DERControl, TimeTariffInterval, ...)
        since it only reads the base ``m_rid`` / ``randomize_start`` /
        ``randomize_duration`` fields. Offsets are cached per mRID. Range is
        [0, abs(value)] with sign preserved from the randomization field. Zero
        randomization fields produce zero offsets.
        """
        mrid = event.m_rid.value
        if mrid in self._cache:
            return self._cache[mrid]

        start_offset = self._compute_offset(event.randomize_start)
        dur_offset = self._compute_offset(event.randomize_duration)

        self._cache[mrid] = (start_offset, dur_offset)
        # Track expiry based on effective end time
        self._expiry[mrid] = start + start_offset + duration + dur_offset
        return (start_offset, dur_offset)

    def prune(self, now: int, grace: int = 10) -> None:
        """Remove cache entries for events that have expired."""
        expired = [mrid for mrid, end in self._expiry.items() if end + grace < now]
        for mrid in expired:
            del self._cache[mrid]
            del self._expiry[mrid]

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
