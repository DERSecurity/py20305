"""Per-device randomization of event start time and duration, drawn once and reused.

IEEE 2030.5-2023 §10.2.3.2.2: "The device SHALL randomly select a number in
seconds from zero to the randomizeStart specified for this event." §10.2.3.2.3:
"Devices SHALL randomly select a number in seconds from zero to the
randomizeDuration value." The purpose (§10.2.3.1) is to keep a large group of
devices from acting at the same moment, and §10.2.3.3 has a proxy for devices
that do not speak IEEE 2030.5 apply a distribution of values across them. So a
client acting for several EndDevices draws one offset per (event, device), not
one per event.
"""

from __future__ import annotations

import random
from collections.abc import Callable

from py20305.models.sep.sep import OneHourRangeType, RandomizableEvent

#: Key of the offsets drawn for an event with no device, such as a tariff
#: interval or a control whose program has no device mapping.
NO_DEVICE = ""

#: ``(mrid, device, drawn offsets) -> offsets to use``.
OffsetClaim = Callable[[bytes, str, tuple[int, int]], tuple[int, int]]


class RandomizationCache:
    """Cache randomized offsets per (mRID, device) so a device keeps one timing per event.

    Offsets are drawn at random once per event and device, and reused on
    subsequent calls. Each device draws its own (IEEE 2030.5-2023 §10.2.3.2.2
    and §10.2.3.2.3), so the devices of one event spread out. The cache is
    prunable to avoid unbounded memory growth.

    ``claim`` shares the offsets beyond this process: it is called with the
    mRID, the device and the offsets just drawn, and returns the offsets to
    use, which are the first ever stored for that event and device (see
    :meth:`~py20305.events.shared_state.EventStateStore.claim_offsets`). None
    keeps them in this cache only.

    ``draw`` returns a random offset in ``[0, bound]`` for a positive bound;
    None uses :func:`random.randint`. Tests inject it to make the draws known.
    """

    def __init__(
        self,
        claim: OffsetClaim | None = None,
        draw: Callable[[int], int] | None = None,
    ) -> None:
        self._cache: dict[tuple[bytes, str], tuple[int, int]] = {}
        self._expiry: dict[tuple[bytes, str], int] = {}
        self._claim = claim
        self._draw = draw if draw is not None else (lambda bound: random.randint(0, bound))

    def get_offsets(
        self,
        event: RandomizableEvent,
        start: int,
        duration: int,
        *,
        device: str = NO_DEVICE,
        start_offset: int | None = None,
    ) -> tuple[int, int]:
        """Return (start_offset, duration_offset) of ``event`` on ``device``.

        Works for any ``RandomizableEvent`` (DERControl, TimeTariffInterval, ...)
        since it only reads the base ``m_rid`` / ``randomize_start`` /
        ``randomize_duration`` fields. Offsets are cached per mRID and device.
        Range is [0, abs(value)] with sign preserved from the randomization
        field. Zero randomization fields produce zero offsets.

        ``start_offset``, when given, replaces the drawn start offset of an
        event not cached yet for the device, before the offsets are claimed:
        a successive event's start follows its predecessor's end on that
        device (§10.2.2.3 rule m.1).
        """
        key = (event.m_rid.value, device)
        if key in self._cache:
            return self._cache[key]

        if start_offset is None:
            start_offset = self._offset(event.randomize_start)
        dur_offset = self._offset(event.randomize_duration)
        if self._claim is not None:
            start_offset, dur_offset = self._claim(key[0], device, (start_offset, dur_offset))

        self._cache[key] = (start_offset, dur_offset)
        # Track expiry based on effective end time
        self._expiry[key] = start + start_offset + duration + dur_offset
        return (start_offset, dur_offset)

    def prune(self, now: int, grace: int = 10) -> list[tuple[bytes, str]]:
        """Remove entries for events that have ended. Return their ``(mrid, device)`` keys."""
        expired = [key for key, end in self._expiry.items() if end + grace < now]
        for key in expired:
            del self._cache[key]
            del self._expiry[key]
        return expired

    def seed(self, mrid: bytes, device: str, offsets: tuple[int, int], end: int) -> None:
        """Use ``offsets`` for ``mrid`` on ``device``, loaded with their timing already drawn."""
        self._cache[(mrid, device)] = offsets
        self._expiry[(mrid, device)] = end

    def _offset(self, value: OneHourRangeType | None) -> int:
        """Draw an offset from a randomization field. Returns 0 for None or 0."""
        if value is None or value.value == 0:
            return 0
        raw = value.value
        sign = 1 if raw > 0 else -1
        return sign * self._draw(abs(raw))

    def override_start_offset(
        self,
        mrid: bytes,
        new_start_offset: int,
        raw_start: int,
        raw_duration: int,
        *,
        device: str = NO_DEVICE,
    ) -> None:
        """Override the cached start offset of an event on one device.

        Used by IEEE 10.2.2.3 rule m) to prevent randomization gaps
        between successive events: the earlier event's effective end time
        becomes the later event's effective start time.
        """
        key = (mrid, device)
        if key not in self._cache:
            return
        _, dur_offset = self._cache[key]
        self._cache[key] = (new_start_offset, dur_offset)
        self._expiry[key] = raw_start + new_start_offset + raw_duration + dur_offset

    def __len__(self) -> int:
        return len(self._cache)
