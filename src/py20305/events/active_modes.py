"""Track which event owns which control modes per device.

Supports per-mode DDERC fallback: when an event completes, only the modes
it owned (and that no other active event covers) fall back to DDERC.
"""

from __future__ import annotations


class ActiveModeTracker:
    """Track active modes per device per event mRID.

    register() records which modes an event provides.
    release() removes some of those modes without ending the registration.
    unregister() removes the event and returns modes that need DDERC fallback
    (i.e., modes no longer covered by any remaining active event).
    """

    def __init__(self) -> None:
        # device_href -> mrid -> frozenset of mode field names
        self._registry: dict[str, dict[bytes, frozenset[str]]] = {}

    def register(self, device_href: str, mrid: bytes, modes: frozenset[str]) -> None:
        """Record that event ``mrid`` provides ``modes`` for ``device_href``."""
        self._registry.setdefault(device_href, {})[mrid] = modes

    def release(self, device_href: str, mrid: bytes, modes: frozenset[str]) -> None:
        """Remove ``modes`` from the registration of event ``mrid`` for ``device_href``.

        The counterpart of :meth:`register` for part of a registration: the
        event keeps the modes it still provides and the registration is dropped
        when none are left. Used when another event takes modes over, so that
        event is the only owner and its own end releases them. Releasing modes
        the event never registered, or for an event that has no registration,
        does nothing.
        """
        device_events = self._registry.get(device_href)
        if device_events is None or mrid not in device_events:
            return
        remaining = device_events[mrid] - modes
        if remaining:
            device_events[mrid] = remaining
        else:
            del device_events[mrid]

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

    def devices_of(self, mrid: bytes) -> frozenset[str]:
        """Return the devices event ``mrid`` is registered on.

        Asked of the tracker rather than of the program's current device list
        because the two can differ: a device withdrawn from the program while
        the event runs is no longer in that list, but the event still holds
        modes on it until its registration is released.
        """
        return frozenset(device for device, events in self._registry.items() if mrid in events)

    def covered(self, device_href: str, *, excluding: bytes | None = None) -> frozenset[str]:
        """Return the modes registered for ``device_href``, ignoring event ``excluding``."""
        covered: set[str] = set()
        for mrid, modes in self._registry.get(device_href, {}).items():
            if mrid != excluding:
                covered |= modes
        return frozenset(covered)

    def covered_among(self, device_href: str, mrids: frozenset[bytes]) -> frozenset[str]:
        """Return the modes ``mrids`` hold for ``device_href``.

        Narrower than :meth:`covered`, which answers for every registration
        there is. A caller asking what is still being controlled wants the
        events that are still running, not one that ended and left its
        registration behind.
        """
        covered: set[str] = set()
        for mrid, modes in self._registry.get(device_href, {}).items():
            if mrid in mrids:
                covered |= modes
        return frozenset(covered)

    def clear(self) -> None:
        """Clear all tracked state."""
        self._registry.clear()
