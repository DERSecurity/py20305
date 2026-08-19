"""Track which event owns which control modes per device.

Supports per-mode DDERC fallback: when an event completes, only the modes
it owned (and that no other active event covers) fall back to DDERC.
"""

from __future__ import annotations


class ActiveModeTracker:
    """Track active modes per device per event mRID.

    register() records which modes an event provides.
    unregister() removes the event and returns modes that need DDERC fallback
    (i.e., modes no longer covered by any remaining active event).
    """

    def __init__(self) -> None:
        # device_href -> mrid -> frozenset of mode field names
        self._registry: dict[str, dict[bytes, frozenset[str]]] = {}

    def register(self, device_href: str, mrid: bytes, modes: frozenset[str]) -> None:
        """Record that event ``mrid`` provides ``modes`` for ``device_href``."""
        self._registry.setdefault(device_href, {})[mrid] = modes

    def unregister(self, device_href: str, mrid: bytes) -> frozenset[str]:
        """Remove event and return modes needing DDERC fallback.

        Returns the set of modes that were owned by this event and are
        not covered by any other active event for the same device.
        """
        device_events = self._registry.get(device_href)
        if device_events is None:
            return frozenset()

        owned = device_events.pop(mrid, frozenset())
        if not owned:
            return frozenset()

        # Compute modes still covered by remaining events
        still_covered: set[str] = set()
        for remaining_modes in device_events.values():
            still_covered |= remaining_modes

        return owned - still_covered

    def clear(self) -> None:
        """Clear all tracked state."""
        self._registry.clear()
