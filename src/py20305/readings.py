"""Measurement vocabulary, and the source interface that supplies it.

One acquisition of a device's monitoring data becomes one :class:`DeviceSnapshot`
holding a :class:`PointEntry` per monitoring key. Entries are immutable: an
acquisition replaces a device's snapshot rather than mutating it, so a reader
that holds a snapshot always sees a coherent set of points from a single device
read.

:class:`MeasurementSource` is where a consumer gets those snapshots. It exists so
the telemetry path states what it needs -- a reading for this device, no older
than this -- without naming who satisfies it. :class:`DirectConnectorSource` is
the answer for a deployment with one consumer: read the connector, cache nothing.
A deployment serving several upstream interfaces over one device model supplies a
caching, demand-coalescing implementation instead, and nothing in the telemetry
path changes.
"""

from __future__ import annotations

import inspect
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from py20305.connectors.base import BaseConnector, ReadingOverride

if TYPE_CHECKING:
    from py20305.connectors.device_telemetry import DeviceTelemetryEmitter

#: Suffix a connector uses to report a per-quantity protocol quality value
#: inline with the value itself, as ``"<key>__quality"``. Part of the connector
#: contract rather than of any store, so it travels with the vocabulary.
QUALITY_SUFFIX = "__quality"


def split_quality(values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    """Separate measured keys from their ``"<key>__quality"`` companions.

    Lives with the vocabulary rather than with any store because it encodes the
    connector convention itself, and every path that turns a raw
    ``fetch_monitoring`` response into entries has to apply it identically. A
    second copy would drift.
    """
    measured: dict[str, Any] = {}
    quality: dict[str, int] = {}
    for key, value in values.items():
        if key.endswith(QUALITY_SUFFIX):
            base = key[: -len(QUALITY_SUFFIX)]
            # A non-int here would be a connector bug. Drop it rather than
            # carrying it: the MUP path validates qualityFlags again and would
            # log the same complaint once per cycle forever. bool is excluded
            # explicitly -- it is an int subclass, and True would post as 1.
            if isinstance(value, int) and not isinstance(value, bool):
                quality[base] = value
        else:
            measured[key] = value
    return measured, quality


def typed_overrides(overrides: dict[str, Any] | None) -> dict[str, ReadingOverride] | None:
    """Keep only well-formed :class:`ReadingOverride` values from a connector's dict.

    Here for the same reason as :func:`split_quality`: it encodes the connector
    contract, and every path that turns a raw ``reading_overrides()`` response
    into a snapshot has to apply it identically or the snapshots disagree.
    :attr:`DeviceSnapshot.reading_overrides` is typed
    ``Mapping[str, ReadingOverride]``, and the values arrive from third-party
    code through an ``Any``, so nothing but this check keeps the annotation
    honest.
    """
    if overrides is None:
        return None
    return {k: v for k, v in overrides.items() if isinstance(v, ReadingOverride)}


class Quality(Enum):
    """The store's own health model for a value.

    Deliberately *not* a protocol quality value. IEEE 2030.5 ``qualityFlags``
    is a separate bitfield the connector supplies and the MirrorUsagePoint path
    posts; it rides along on :attr:`PointEntry.protocol_quality` rather than
    being mapped onto this enum. The two answer different questions -- "did the
    read succeed" versus "is this value still current".
    """

    GOOD = "good"
    #: Acquired successfully but older than the tightest demand asked for.
    #: Applied by ``with_freshness`` at read time rather than stored, because
    #: the same sample is fresh to one consumer and stale to another.
    STALE = "stale"
    COMM_LOST = "comm_lost"


@dataclass(frozen=True, slots=True)
class PointEntry:
    """One monitoring key's value and everything acquired alongside it."""

    value: Any
    #: Wall-clock epoch seconds at which the acquisition that produced this
    #: value *began*. Not read-completion time: on a multi-second Modbus scan
    #: the two differ, and stamping completion would make the sample look
    #: fresher than it is. The true per-register instant is unknowable, so this
    #: is the closest available answer to "when the device produced it" that
    #: errs old rather than new.
    #: For a COMM_LOST entry it stays at the last *successful* acquisition, so
    #: the age a reader sees grows while the device is unreachable.
    source_timestamp: float
    quality: Quality
    #: The connector's per-cycle ``"<key>__quality"``, carried verbatim. None
    #: when the connector supplied none for this key this cycle.
    protocol_quality: int | None


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """A device's points as of one acquisition.

    ``age`` is deliberately absent: it depends on a clock, and the store has
    none. Callers compute it from :attr:`last_success` against whatever clock
    they already hold.
    """

    entries: Mapping[str, PointEntry]
    #: The connector's ``reading_overrides()`` as of this acquisition, keyed by
    #: monitoring key. Device-level rather than per-entry because it legitimately
    #: covers keys absent from this cycle's values: ``create_mup`` registers the
    #: *full* ReadingType set, so an override for a quantity the connector isn't
    #: reporting right now still shapes what gets registered. Read after the
    #: values because an out-of-process connector populates it from the same
    #: ``fetch_monitoring`` response.
    reading_overrides: Mapping[str, ReadingOverride]
    #: Store-wide monotonic counter identifying the acquisition these entries
    #: came from. Orders acquisitions across devices.
    sequence: int
    #: Epoch seconds of the last acquisition that succeeded, or None if none
    #: ever has. Distinct from a COMM_LOST entry's ``source_timestamp`` only
    #: when the device has never been read.
    last_success: float | None
    quality: Quality
    #: What the failed acquisition raised, when quality is COMM_LOST. Carried
    #: rather than reported here so each consumer keeps its own operator-facing
    #: identity -- the metering cycle and the management API describe the same
    #: failure differently, and the store has no business choosing between them.
    error: Exception | None = None


def with_freshness(snapshot: DeviceSnapshot, max_age: float | None, now: float) -> DeviceSnapshot:
    """Downgrade a snapshot's GOOD entries to STALE once they exceed ``max_age``.

    Kept out of the store because staleness is not a property of the data: the
    same sample is fresh to a consumer wanting minute-old values and stale to
    one wanting second-old values. The store records when a value was acquired;
    what counts as too old belongs to whoever declared the demand.

    COMM_LOST is left alone -- it already says something stronger than stale,
    and a value from an unreachable device is not merely late.
    """
    if max_age is None or snapshot.quality is not Quality.GOOD:
        return snapshot
    if snapshot.last_success is None or now - snapshot.last_success <= max_age:
        return snapshot

    entries = {
        key: (
            entry
            if entry.quality is not Quality.GOOD
            else PointEntry(
                value=entry.value,
                source_timestamp=entry.source_timestamp,
                quality=Quality.STALE,
                protocol_quality=entry.protocol_quality,
            )
        )
        for key, entry in snapshot.entries.items()
    }
    return DeviceSnapshot(
        entries=MappingProxyType(entries),
        reading_overrides=snapshot.reading_overrides,
        sequence=snapshot.sequence,
        last_success=snapshot.last_success,
        quality=Quality.STALE,
        error=snapshot.error,
    )


@runtime_checkable
class MeasurementSource(Protocol):
    """Where the telemetry path gets a device's measurements.

    Three operations, because a consumer's interest in a device has a lifetime:
    it announces what freshness it needs, reads repeatedly, and eventually stops
    caring. An implementation that coalesces demand across several consumers
    needs the first and last to do its job; one serving a single consumer can
    ignore them, which is exactly what :class:`DirectConnectorSource` does.
    """

    def declare(self, device: str, max_age: float) -> None:
        """Announce that this consumer needs ``device`` no staler than ``max_age``."""
        ...

    def release(self, device: str) -> None:
        """Withdraw interest in ``device``; any cached state for it may be dropped."""
        ...

    async def read(self, device: str, *, max_age: float | None = None) -> DeviceSnapshot:
        """Return the best available snapshot of ``device``, judged against ``max_age``.

        ``max_age`` is the freshness the caller wants, not a guarantee it gets:
        a source may return an older sample marked ``STALE``, or a ``COMM_LOST``
        one carrying the last known values. What it will not do is return a
        stale sample looking fresh -- the verdict is already applied, so callers
        do not call :func:`with_freshness` themselves. ``None`` means the caller
        accepts whatever the source considers current.
        """
        ...


class DirectConnectorSource:
    """Reads the connector on every request; never serves one from cache.

    The single-consumer answer. With one upstream interface there is no second
    reader to share a cached value with and no competing cadence to coalesce, so
    caching a *read* would add staleness without removing a device read.
    ``declare`` and ``release`` are deliberately inert: freshness is whatever the
    read just returned.

    It does retain the last successful snapshot per device. That is history, not
    a cache -- it is never returned in place of a read, only used so a failed
    read can report comm-lost against the last known values instead of an empty
    set, matching what the store-backed path does.
    """

    def __init__(
        self,
        connector_resolver: Callable[[str], BaseConnector | Awaitable[BaseConnector]],
        *,
        clock: Callable[[], float] = time.time,
        telemetry: DeviceTelemetryEmitter | None = None,
    ) -> None:
        """
        Args:
            connector_resolver: LFDI -> connector. **Must be memoizing**: this
                source resolves on every read, and a caller may resolve the same
                device separately in the same cycle for other work. A resolver
                that constructs on each call would repeat first-touch setup -- a
                Modbus scan plus readiness retries -- and could hand out two
                instances for one device in one cycle. Both real resolvers
                satisfy this: the client's goes through
                ``LazyConnectorProxy.aresolve``, which has a cache fast-path,
                and an embedded caller typically closes over a single instance.
                ``AcquisitionService`` carries the same requirement, so this is
                the existing contract stated rather than a new one.
            clock: Source of acquisition timestamps. Injectable so tests can
                assert on age without sleeping.
            telemetry: Reports each read to the monitoring system. Optional and
                disabled by default; omitted, reads behave exactly as before.
        """
        self._resolve_connector = connector_resolver
        self._clock = clock
        self._telemetry = telemetry
        self._sequence = 0
        #: Last successful read per device, so a failed read can report
        #: comm-lost against real history rather than as a never-read device.
        self._last: dict[str, DeviceSnapshot] = {}

    def declare(self, device: str, max_age: float) -> None:
        """No-op: a single consumer's demand is the ``max_age`` it passes to read."""

    def release(self, device: str) -> None:
        """Drop the retained last-good snapshot for ``device``."""
        self._last.pop(device, None)

    async def read(self, device: str, *, max_age: float | None = None) -> DeviceSnapshot:
        """Read ``device`` through its connector, now."""
        self._sequence += 1
        try:
            resolved = self._resolve_connector(device)
            connector = await resolved if inspect.isawaitable(resolved) else resolved
            # Stamped after resolution and before the read, matching
            # AcquisitionService. First-touch construction of a real connector
            # can take seconds (a Modbus scan plus readiness retries); counting
            # that against the sample would report it older than it is. Stamping
            # completion instead would report it fresher, which is the more
            # dangerous direction for a field that drives staleness.
            started = self._clock()
            values = await connector.fetch_monitoring()
            # A connector is third-party code and may return anything. Coerce a
            # non-dict override map away, and drop individual values that aren't
            # ReadingOverride, rather than letting either reach the
            # MirrorUsagePoint path; reject a non-dict value map outright --
            # there is no reading to salvage from it.
            raw_overrides = connector.reading_overrides()
            overrides = typed_overrides(raw_overrides if isinstance(raw_overrides, dict) else None)
            if not isinstance(values, dict):
                raise TypeError(f"fetch_monitoring returned {type(values).__name__}, expected dict")
        except Exception as exc:
            # Broad by intent. A connector is third-party code, including
            # out-of-process connectors, so any failure has to degrade to
            # comm-lost rather than kill the caller's cycle. ConnectorError and
            # OSError are the expected shapes; anything else still means "no
            # reading this time" and is carried on the snapshot for the
            # consumer to report in its own words.
            previous = self._last.get(device)
            # Each entry is rebuilt COMM_LOST rather than carried over as-is.
            # Keeping the value and its original source_timestamp is deliberate
            # -- the age a reader sees grows through the outage -- but leaving
            # the per-entry quality at GOOD under a COMM_LOST snapshot would
            # contradict both the snapshot and PointStore.record_failure, which
            # any reader inspecting entries individually would then disagree
            # with.
            carried = {
                key: PointEntry(
                    value=entry.value,
                    source_timestamp=entry.source_timestamp,
                    quality=Quality.COMM_LOST,
                    protocol_quality=entry.protocol_quality,
                )
                for key, entry in (previous.entries if previous else {}).items()
            }
            return DeviceSnapshot(
                entries=MappingProxyType(carried),
                reading_overrides=previous.reading_overrides if previous else MappingProxyType({}),
                sequence=self._sequence,
                last_success=previous.last_success if previous else None,
                quality=Quality.COMM_LOST,
                error=exc,
            )

        measured, protocol_quality = split_quality(values)
        entries = {
            key: PointEntry(
                value=value,
                source_timestamp=started,
                quality=Quality.GOOD,
                protocol_quality=protocol_quality.get(key),
            )
            for key, value in measured.items()
        }
        snapshot = DeviceSnapshot(
            entries=MappingProxyType(entries),
            reading_overrides=MappingProxyType(dict(overrides or {})),
            sequence=self._sequence,
            last_success=started,
            quality=Quality.GOOD,
            error=None,
        )
        self._last[device] = snapshot
        # After the snapshot, not before: telemetry reports a read that
        # actually produced one, and a read that raised produced no reading.
        if self._telemetry is not None:
            self._telemetry.record_read(device, values, connector=connector)
        return snapshot
