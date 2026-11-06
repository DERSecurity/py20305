"""Measurement vocabulary, and the source interface that supplies it.

One acquisition of a device's monitoring data becomes one :class:`DeviceSnapshot`
holding a :class:`PointEntry` per reading, keyed by
:func:`~py20305.connectors.reading.reading_key`. Entries are immutable: an
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

from py20305.connectors.base import BaseConnector
from py20305.connectors.reading import (
    Reading,
    ReadingIdentity,
    effective_multiplier,
    is_valid_reading,
    reading_identity,
    reading_key,
)

if TYPE_CHECKING:
    from py20305.connectors.device_telemetry import DeviceTelemetryEmitter


def index_readings(readings: object) -> dict[str, Reading]:
    """Return a connector's readings keyed by :func:`reading_key`.

    A connector is third-party code and may return anything. A value that is not
    a list or tuple raises ``TypeError``; an entry that is not a valid
    :class:`Reading`, or repeats an earlier reading's identity, is dropped with
    a warning (see :func:`~py20305.telemetry.mup.valid_readings`). Identity
    includes the multiplier, so two readings that differ only in multiplier are
    both kept: the second is keyed by its ``reading_key`` plus ``:m<multiplier>``.
    Every path that turns a ``fetch_monitoring`` response into entries applies
    this, so the snapshots agree.
    """
    if not isinstance(readings, list | tuple):
        raise TypeError(
            f"fetch_monitoring returned {type(readings).__name__}, expected a list of Reading"
        )
    from py20305.telemetry.mup import valid_readings

    indexed: dict[str, Reading] = {}
    for reading in valid_readings(readings):
        key = reading_key(reading)
        if key in indexed:
            key = f"{key}:m{effective_multiplier(reading)}"
        indexed[key] = reading
    return indexed


def typed_declaration(declared: Any) -> frozenset[ReadingIdentity] | None:
    """A connector's ``declared_readings()`` as a set of identities, or None if malformed.

    The value comes from third-party code, and a declaration narrows what the
    MirrorUsagePoint registers, so anything other than a collection of
    :class:`~py20305.connectors.reading.ReadingIdentity` or
    :class:`~py20305.connectors.reading.Reading` is treated as no declaration
    rather than guessed at. A ``Reading`` stands for its identity; its value is
    ignored.
    """
    if not isinstance(declared, set | frozenset | list | tuple):
        return None
    identities: set[ReadingIdentity] = set()
    for item in declared:
        if isinstance(item, ReadingIdentity):
            identities.add(item)
        elif is_valid_reading(item):
            identities.add(reading_identity(item))
        else:
            return None
    return frozenset(identities)


def declared_readings_of(connector: Any) -> frozenset[ReadingIdentity] | None:
    """``connector.declared_readings()``, typed; None when absent or broken.

    Optional on the connector contract, so a connector that predates it, or does
    not subclass BaseConnector, declares nothing. A declaration that raises is
    no declaration: it must not turn a good read of the values into a failed one.
    """
    declare = getattr(connector, "declared_readings", None)
    if not callable(declare):
        return None
    try:
        return typed_declaration(declare())
    except Exception:
        return None


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
    """One reading and when it was acquired."""

    reading: Reading
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

    @property
    def value(self) -> float:
        """Return the reading's value."""
        return self.reading.value

    @property
    def protocol_quality(self) -> int | None:
        """Return the qualityFlags the connector gave the reading, if any."""
        return self.reading.quality_flags


@dataclass(frozen=True, slots=True)
class DeviceSnapshot:
    """A device's points as of one acquisition.

    ``age`` is deliberately absent: it depends on a clock, and the store has
    none. Callers compute it from :attr:`last_success` against whatever clock
    they already hold.
    """

    entries: Mapping[str, PointEntry]
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
    #: The connector's ``declared_readings()``, or None when it declares none.
    #: Device-level, like :attr:`reading_overrides`, and kept through a failed
    #: acquisition because it describes the device rather than a sample.
    declared_readings: frozenset[ReadingIdentity] | None = None


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
                reading=entry.reading,
                source_timestamp=entry.source_timestamp,
                quality=Quality.STALE,
            )
        )
        for key, entry in snapshot.entries.items()
    }
    return DeviceSnapshot(
        entries=MappingProxyType(entries),
        sequence=snapshot.sequence,
        last_success=snapshot.last_success,
        quality=Quality.STALE,
        error=snapshot.error,
        declared_readings=snapshot.declared_readings,
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
            readings = index_readings(await connector.fetch_monitoring())
            declared = declared_readings_of(connector)
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
                    reading=entry.reading,
                    source_timestamp=entry.source_timestamp,
                    quality=Quality.COMM_LOST,
                )
                for key, entry in (previous.entries if previous else {}).items()
            }
            return DeviceSnapshot(
                entries=MappingProxyType(carried),
                sequence=self._sequence,
                last_success=previous.last_success if previous else None,
                quality=Quality.COMM_LOST,
                error=exc,
                declared_readings=previous.declared_readings if previous else None,
            )

        entries = {
            key: PointEntry(reading=reading, source_timestamp=started, quality=Quality.GOOD)
            for key, reading in readings.items()
        }
        snapshot = DeviceSnapshot(
            entries=MappingProxyType(entries),
            sequence=self._sequence,
            last_success=started,
            quality=Quality.GOOD,
            error=None,
            declared_readings=declared,
        )
        self._last[device] = snapshot
        # After the snapshot, not before: telemetry reports a read that
        # actually produced one, and a read that raised produced no reading.
        if self._telemetry is not None:
            self._telemetry.record_read(
                device, {key: r.value for key, r in readings.items()}, connector=connector
            )
        return snapshot
