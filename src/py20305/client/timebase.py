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

Every scope must be *renewed*, not merely recorded. Classification reads the
FSA scope, so an entry left at its discovery-time value is the offset the
client actually schedules against, and a host whose clock drifts drags event
timing along with it while the global scope -- the one status surfaces report
-- stays correct. A per-FSA entry older than ``fsa_stale_seconds`` therefore
yields to a newer global one: specificity is worth having only while somebody
is keeping it current. Age is measured on the monotonic clock, because the wall
clock is the thing being distrusted and is stepped deliberately on exactly the
deployments that need this.

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
    #: ``time.monotonic()`` at receipt, for measuring how old this observation
    #: is. Age cannot be derived from ``receipt_epoch``: the clock that stamps
    #: it is the one a caller of ``GET /time`` is about to step, and a backward
    #: step would make a stale observation read as fresh (or negative). Optional
    #: so an observation constructed directly still works; age then falls back
    #: to the wall clock.
    receipt_monotonic: float | None = None


def _age_seconds(obs: TimeObservation) -> float:
    """How long ago *obs* was taken, measured on the monotonic clock where possible.

    Never from ``receipt_epoch``. The wall clock is the one this whole module
    exists to distrust, and on the deployments that need the timebase most it is
    stepped deliberately -- by an operator, or by a device syncing its RTC from
    ``GET /time``. A ten-minute forward step would age every observation by ten
    minutes at once, and a backward step would make a stale one read as fresh.
    """
    if obs.receipt_monotonic is not None:
        return max(0.0, time.monotonic() - obs.receipt_monotonic)
    return max(0.0, time.time() - obs.receipt_epoch)


def _is_newer(candidate: TimeObservation, other: TimeObservation) -> bool:
    """Whether *candidate* was observed more recently than *other*."""
    if candidate.receipt_monotonic is not None and other.receipt_monotonic is not None:
        return candidate.receipt_monotonic > other.receipt_monotonic
    return candidate.receipt_epoch > other.receipt_epoch


class ServerTimebase:
    """Server-adjusted wall clock for time-of-day-sensitive operations.

    ``observe()`` records ``offset = server currentTime - local receipt time``.
    ``now()`` returns ``time.time() + offset(scope)``. When ``enabled`` is
    False the offset is forced to 0.0 (local clock) but observations -- and the
    drift diagnostic -- still run, so an operator who disabled the timebase for
    troubleshooting keeps drift visibility.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        drift_warn_seconds: int = 30,
        fsa_stale_seconds: float = 3600.0,
    ) -> None:
        self._enabled = enabled
        self._drift_warn_seconds = drift_warn_seconds
        #: How old a per-FSA observation may get before the global one is
        #: preferred instead. The FSA scope is the more *correct* answer only
        #: while it is being renewed; once its Time endpoint stops responding,
        #: it becomes the more specific way to be wrong, and it fails silently
        #: because a stale offset is indistinguishable from a fresh one at the
        #: point of use. Falling back trades §9.2.3 specificity for an offset
        #: somebody is still checking.
        self._fsa_stale_seconds = fsa_stale_seconds
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
        obs = TimeObservation(
            offset=offset,
            receipt_epoch=receipt,
            quality=quality,
            href=href,
            receipt_monotonic=time.monotonic(),
        )
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
        obs = self._resolve(fsa_href)
        return obs.offset if obs is not None else 0.0

    def _resolve(self, fsa_href: str | None) -> TimeObservation | None:
        """The observation a scope should read: its own, or global when stale.

        Per §9.2.3 the FSA's own Time resource is the right answer for that
        FSA's events, so it wins whenever it is being kept current. It stops
        winning once it is older than ``fsa_stale_seconds``: past that, a newer
        global observation is a better estimate of the head-end's clock than a
        specific but abandoned one, and preferring specificity there is how a
        client ends up scheduling against a frozen offset while reporting a
        correct one.
        """
        if fsa_href is None:
            return self._global
        scoped = self._per_fsa.get(fsa_href)
        if scoped is None:
            return self._global
        if self._global is None:
            return scoped
        age = _age_seconds(scoped)
        if age > self._fsa_stale_seconds and _is_newer(self._global, scoped):
            logger.debug(
                "FSA %s observation is %.0fs old; using the global timebase instead",
                fsa_href,
                age,
            )
            return self._global
        return scoped

    def now(self, fsa_href: str | None = None) -> float:
        """Server-adjusted wall time for time-of-day-sensitive operations."""
        return time.time() + self.offset(fsa_href)

    def server_now(self, fsa_href: str | None = None) -> float | None:
        """Head-end wall time, or ``None`` when no Time resource was observed.

        Two deliberate differences from :meth:`now`, both about callers who
        publish this value rather than merely schedule against it (setting a
        device clock, stamping a record another system reads).

        It never falls back to the local clock. :meth:`now` returning the
        unadjusted local time when nothing has been observed is right for
        scheduling, where carrying on beats stalling; for a caller about to
        write the value somewhere it is the worst outcome, because a wrong
        answer and a correct one are indistinguishable at the call site.

        It ignores ``enabled``. That flag governs whether *this* client
        follows server time, not what time the head-end reports -- an
        operator who dropped the client onto the local clock to troubleshoot
        can still ask what the server says, and observations keep flowing
        either way.
        """
        obs = self._resolve(fsa_href)
        if obs is None:
            return None
        return time.time() + obs.offset

    def snapshot(self) -> dict[str, Any]:
        """Offset/quality/age per scope, for status surfacing."""

        def _age(obs: TimeObservation) -> float:
            """How long ago the observation was taken, in seconds.

            Measured on the monotonic clock so that a device stepping its own
            RTC -- the thing this client's Time reporting exists to enable --
            does not change how old an existing observation appears. Clamped at
            zero for the fallback path, where the wall clock can still run
            backwards between receipt and read.
            """
            if obs.receipt_monotonic is not None:
                return round(max(0.0, time.monotonic() - obs.receipt_monotonic), 1)
            return round(max(0.0, time.time() - obs.receipt_epoch), 1)

        def _entry(obs: TimeObservation) -> dict[str, Any]:
            return {
                "offset_seconds": round(obs.offset, 3),
                "quality": obs.quality,
                "href": obs.href,
                "age_seconds": _age(obs),
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
