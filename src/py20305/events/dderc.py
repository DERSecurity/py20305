"""Default DER Control (DDERC) fallback tracking.

Tracks which (lfdi, derp_path) pairs have had DDERC applied and at what
primacy, so that DDERC is only reapplied when the completing event has
equal or higher priority (lower primacy number).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _DdercEntry:
    mrid: bytes
    primacy: int


class DdercTracker:
    """Tracks DDERC application state per (lfdi, derp_path)."""

    def __init__(self) -> None:
        self._applied: dict[tuple[bytes, str], _DdercEntry] = {}

    def should_apply(self, lfdi: bytes, derp_path: str, primacy: int) -> bool:
        """Check if DDERC should be applied for this (lfdi, derp_path).

        Used by the fallback path after event completion. Only checks
        same-program primacy (no cross-program blocking). Returns True if:
        - First time for this (lfdi, derp_path), OR
        - Current primacy <= previously applied primacy (equal or higher priority)
        """
        key = (lfdi, derp_path)
        existing = self._applied.get(key)
        if existing is None:
            return True
        return primacy <= existing.primacy

    def should_apply_initial(self, lfdi: bytes, derp_path: str, mrid: bytes, primacy: int) -> bool:
        """Check if initial DDERC should be applied for this (lfdi, derp_path).

        Used at startup/rediscovery. Blocks application when:
        - Another program has already applied a higher-priority DDERC to
          the same device (cross-program check), OR
        - This program already applied this exact DDERC (same mrid) at this
          primacy (no redundant re-apply for unchanged content).

        A DDERC update on the same program (different mrid) is allowed through
        even at the same primacy: fresh defaults from the utility replace the
        ones it previously published.
        """
        # Cross-program check: if any other program already applied a
        # higher-priority DDERC to the same device, block this one.
        for (other_lfdi, other_path), other in self._applied.items():
            if other_lfdi == lfdi and other_path != derp_path and other.primacy < primacy:
                return False

        key = (lfdi, derp_path)
        existing = self._applied.get(key)
        if existing is None:
            return True
        if primacy < existing.primacy:
            return True
        # Same primacy: re-dispatch only when DDERC content has changed.
        return primacy == existing.primacy and mrid != existing.mrid

    def record_application(self, lfdi: bytes, derp_path: str, mrid: bytes, primacy: int) -> None:
        """Record that DDERC was applied for this (lfdi, derp_path)."""
        self._applied[(lfdi, derp_path)] = _DdercEntry(mrid=mrid, primacy=primacy)

    def clear_devices(self, lfdis: set[bytes]) -> None:
        """Clear all tracking state for a set of devices across all programs.

        Called when new events affect these devices, signalling that all
        DDERC state for them should be re-evaluated (including cross-program).
        """
        to_remove = [key for key in self._applied if key[0] in lfdis]
        for key in to_remove:
            del self._applied[key]

    def clear(self) -> None:
        """Clear all tracking state."""
        self._applied.clear()

    def __len__(self) -> int:
        return len(self._applied)
