"""Application-level server timebase (IEEE 2030.5 Time function set).

Tracks the offset between the head-end's Time resource and the local clock so
time-of-day-sensitive operations (event classification, timer firing,
server-facing timestamps) can follow **server** time. The OS clock is never
touched -- that belongs to NTP. Elapsed-time measurements (connectivity
staleness, comms-loss silence detection, subscription ages) stay on the local
clock: they are drift-immune by construction and must not move when the offset
updates.

A single ``ServerTimebase`` is created by ``CsipClient`` and shared: attached
to ``Sep2Client`` (so ``discover()`` and the telemetry managers can reach it)
and threaded into ``EventProcessor`` (same pattern as ``CommsLossState``).
Written and read on the event loop only; no locking.

Offsets are scoped per FSA (IEEE 2030.5 §9.2.3: events SHALL use the Time
resource from the same FunctionSetAssignments) with fallback to the global
(DeviceCapability) Time, then to the local clock when nothing has been
observed. Per-FSA entries are not cleared on rediscovery: lookups are driven by
the *current* state's program->FSA mapping, so entries for removed FSAs are
simply never consulted.

"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimeObservation:
    """One Time-resource observation: offset and provenance."""

    offset: float  # server currentTime - local receipt time (seconds)
    receipt_epoch: float  # local time.time() at receipt
    quality: int  # Time.quality (3=NTP-derived ... 7=uncoordinated)
    href: str  # Time resource href observed


class ServerTimebase:
    """Server-adjusted wall clock for time-of-day-sensitive operations.

    ``observe()`` records ``offset = server currentTime - local receipt time``.
    ``now()`` returns ``time.time() + offset(scope)``. When ``enabled`` is
    False the offset is forced to 0.0 (local clock) but observations -- and the
    drift diagnostic -- still run, so an operator who disabled the timebase for
    troubleshooting keeps drift visibility.
    """

    def __init__(self, *, enabled: bool = True, drift_warn_seconds: int = 30) -> None:
        self._enabled = enabled
        self._drift_warn_seconds = drift_warn_seconds
        self._global: TimeObservation | None = None
        self._per_fsa: dict[str, TimeObservation] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def observe(
        self,
        server_time: int,
        *,
        fsa_href: str | None = None,
        quality: int = 0,
        href: str = "",
    ) -> float:
        """Record a Time observation; returns the computed offset (seconds)."""
        # Single receipt-time capture: offset and receipt_epoch must describe
        # the same instant or the stored age/offset pair skews subtly.
        receipt = time.time()
        offset = float(server_time) - receipt
        obs = TimeObservation(offset=offset, receipt_epoch=receipt, quality=quality, href=href)
        scope = fsa_href or "global"
        if fsa_href is None:
            self._global = obs
        else:
            self._per_fsa[fsa_href] = obs
        logger.debug(
            "Server time observed (%s): offset=%+.3fs quality=%d href=%s",
            scope,
            offset,
            quality,
            href,
        )
        if self._drift_warn_seconds > 0 and abs(offset) >= self._drift_warn_seconds:
            from py20305.diagnostics import report

            mode = (
                "use the server timebase"
                if self._enabled
                else "stay on the LOCAL clock (use_server_time is off)"
            )
            report(
                "warnings",
                f"Local clock drifts {offset:+.1f}s from server time at "
                f"{href or 'Time'} (threshold {self._drift_warn_seconds}s, "
                f"quality={quality}). Time-of-day operations {mode}; "
                "consider fixing host NTP.",
                source="client",
                dedup_key=f"timebase:drift:{scope}",
                details={
                    "offset_seconds": round(offset, 3),
                    "quality": quality,
                    "href": href,
                    "scope": scope,
                },
            )
        return offset

    def offset(self, fsa_href: str | None = None) -> float:
        """Current offset for the scope; 0.0 when disabled or never observed."""
        if not self._enabled:
            return 0.0
        obs = self._per_fsa.get(fsa_href) if fsa_href is not None else None
        obs = obs or self._global
        return obs.offset if obs is not None else 0.0

    def now(self, fsa_href: str | None = None) -> float:
        """Server-adjusted wall time for time-of-day-sensitive operations."""
        return time.time() + self.offset(fsa_href)

    def snapshot(self) -> dict[str, Any]:
        """Offset/quality/age per scope, for status surfacing."""

        def _entry(obs: TimeObservation) -> dict[str, Any]:
            return {
                "offset_seconds": round(obs.offset, 3),
                "quality": obs.quality,
                "href": obs.href,
                "age_seconds": round(time.time() - obs.receipt_epoch, 1),
            }

        return {
            "enabled": self._enabled,
            "global": _entry(self._global) if self._global else None,
            "per_fsa": {k: _entry(v) for k, v in self._per_fsa.items()},
        }


def observe_time_resource(
    timebase: ServerTimebase,
    time_resource: object,
    *,
    fsa_href: str | None = None,
    href: str = "",
) -> None:
    """Feed a fetched IEEE 2030.5 ``Time`` resource into the timebase.

    Mock/shape-tolerant: silently skips anything without an integer
    ``current_time.value`` (e.g. AsyncMock responses in tests, or a malformed
    resource) rather than raising inside a poll loop.
    """
    value = getattr(getattr(time_resource, "current_time", None), "value", None)
    if not isinstance(value, int):
        return
    quality = getattr(time_resource, "quality", 0)
    timebase.observe(
        value,
        fsa_href=fsa_href,
        quality=quality if isinstance(quality, int) else 0,
        href=href,
    )
