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
    #: The control modes the writes of this default carried, or None when the
    #: caller did not say. A default can be written without the modes an event
    #: held at the time, so the mRID alone does not say what the device has.
    modes: frozenset[str] | None = None


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

    def should_apply_initial(
        self,
        lfdi: bytes,
        derp_path: str,
        mrid: bytes,
        primacy: int,
        modes: frozenset[str] | None = None,
    ) -> bool:
        """Check if initial DDERC should be applied for this (lfdi, derp_path).

        Used at startup/rediscovery. Blocks application when:
        - Another program has already applied a higher-priority DDERC to
          the same device (cross-program check), OR
        - This program already applied this exact DDERC (same mrid) at this
          primacy (no redundant re-apply for unchanged content).

        A DDERC update on the same program (different mrid) is allowed through
        even at the same primacy: fresh defaults from the utility replace the
        ones it previously published.

        ``modes`` is what this write would carry. The same default is written
        again when that includes a mode no earlier write of it carried, which is
        what happens when a mode an event was holding is released. Without it,
        or when the earlier writes did not record their modes, only the mRID
        decides.
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
        # Same primacy: re-dispatch when the DDERC content has changed, or when
        # this write carries a mode the earlier writes of it did not.
        if primacy != existing.primacy:
            return False
        if mrid != existing.mrid:
            return True
        return modes is not None and existing.modes is not None and not modes <= existing.modes

    def record_application(
        self,
        lfdi: bytes,
        derp_path: str,
        mrid: bytes,
        primacy: int,
        modes: frozenset[str] | None = None,
    ) -> None:
        """Record that DDERC was applied for this (lfdi, derp_path).

        ``modes`` are the control modes the write carried. Writes of one default
        add up: a later write of the same mRID keeps the modes the earlier ones
        carried.
        """
        existing = self._applied.get((lfdi, derp_path))
        if (
            existing is not None
            and existing.mrid == mrid
            and existing.modes is not None
            and modes is not None
        ):
            modes = existing.modes | modes
        self._applied[(lfdi, derp_path)] = _DdercEntry(mrid=mrid, primacy=primacy, modes=modes)

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
