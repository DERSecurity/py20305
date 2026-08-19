"""Track currently dispatched DER control modes per device.

Maintains per-device state of which modes are active and their parameters,
enabling construction of CurrentDERControls resources for PUT to the server.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class CurrentControlsTracker:
    """Track active DER control modes per device.

    Provides update/clear operations and can build summary dicts per device.
    """

    def __init__(self) -> None:
        # device_href -> {mode_name -> params}
        self._state: dict[str, dict[str, dict[str, Any]]] = {}

    def update(
        self,
        device_href: str,
        dispatched_modes: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Update tracked modes for a device after dispatch.

        Args:
            device_href: The device being controlled.
            dispatched_modes: List of (method_name, params) from translation.
        """
        dev = self._state.setdefault(device_href, {})
        for method_name, params in dispatched_modes:
            dev[method_name] = dict(params)

    def clear_modes(self, device_href: str, mode_names: frozenset[str]) -> None:
        """Remove specific modes from a device's tracked state.

        Args:
            device_href: The device to update.
            mode_names: Set of mode field names to clear.
        """
        dev = self._state.get(device_href)
        if dev is None:
            return
        # mode_names are DercontrolBase field names, but state keys are
        # method names. Remove entries whose method name contains the mode.
        to_remove = [k for k in dev if any(m in k for m in mode_names)]
        for k in to_remove:
            del dev[k]

    def clear_device(self, device_href: str) -> None:
        """Clear all tracked modes for a device."""
        self._state.pop(device_href, None)

    def get_active_modes(self, device_href: str) -> dict[str, dict[str, Any]]:
        """Return the active modes for a device (method_name -> params)."""
        return dict(self._state.get(device_href, {}))

    def has_changes(self, device_href: str) -> bool:
        """Check if a device has any tracked modes."""
        return bool(self._state.get(device_href))

    def clear(self) -> None:
        """Clear all tracked state."""
        self._state.clear()
