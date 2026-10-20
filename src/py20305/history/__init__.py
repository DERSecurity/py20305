"""The operations history: what the client did, and what the devices measured.

Three parts share one SQLite file, and each can be switched off on its own:

- :class:`EventHistory` keeps every event's lifecycle and the responses posted
  for it.
- :class:`WriteHistory` keeps every control written to a device, with the mRID
  of the control and of the event that caused it.
- :class:`IntervalHistory` integrates device readings into fixed intervals and
  computes comparison-day baselines from them.

The command-line runner builds the history from the ``history`` block of its
configuration. An application embedding the client opens a :class:`History`
itself and hands each part to the component that feeds it: the event part to
``CsipClient.attach_history``, the write part to
``ConnectorDispatcher.attach_write_history``, and the interval part to the
telemetry manager. Or it leaves the history out; nothing in the client
requires one.
"""

from __future__ import annotations

from datetime import UTC, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any

from py20305.history.config import HistoryConfig, HistoryPartConfig, IntervalHistoryConfig
from py20305.history.database import MEMORY, HistoryDatabase
from py20305.history.events import EventHistory
from py20305.history.intervals import IntervalHistory
from py20305.history.writes import WriteHistory

if TYPE_CHECKING:
    from py20305.client.timebase import ServerTimebase

__all__ = [
    "MEMORY",
    "EventHistory",
    "History",
    "HistoryConfig",
    "HistoryDatabase",
    "HistoryPartConfig",
    "IntervalHistory",
    "IntervalHistoryConfig",
    "WriteHistory",
    "build_history",
]


class History:
    """The parts of the history that are switched on, over one database.

    A part that is off is ``None``. Callers check the part, not a flag, so a
    disabled part cannot be written to by mistake.
    """

    def __init__(
        self,
        database: HistoryDatabase,
        *,
        events: EventHistory | None = None,
        writes: WriteHistory | None = None,
        intervals: IntervalHistory | None = None,
    ) -> None:
        self.database = database
        self.events = events
        self.writes = writes
        self.intervals = intervals

    # One keyword per configuration setting.
    @classmethod
    def open(  # pylint: disable=too-many-arguments
        cls,
        path: str | Path,
        *,
        events: bool = True,
        writes: bool = True,
        intervals: bool = True,
        retention_days: float = 400.0,
        interval_seconds: int = 900,
        max_gap_seconds: float = 900.0,
    ) -> History:
        """Open or create the history file with the chosen parts."""
        database = HistoryDatabase(path, retention_days=retention_days)
        history = cls(
            database,
            events=EventHistory(database) if events else None,
            writes=WriteHistory(database) if writes else None,
            intervals=(
                IntervalHistory(
                    database,
                    interval_seconds=interval_seconds,
                    max_gap_seconds=max_gap_seconds,
                )
                if intervals
                else None
            ),
        )
        database.prune()
        return history

    def use_timebase(self, timebase: ServerTimebase) -> None:
        """Follow the server's timebase, as events and readings do.

        Device writes are moved onto it, and a running event's time so far is
        measured against it.
        """
        if self.writes is not None:
            self.writes.offset = timebase.offset
        if self.events is not None:
            self.events.clock = timebase.now

    # The options of IntervalHistory.baseline, passed through.
    def baseline(  # pylint: disable=too-many-arguments
        self,
        start: float,
        end: float,
        *,
        lfdi: str | None = None,
        days: int = 10,
        lookback_days: int = 45,
        weekdays_only: bool = True,
        tz: tzinfo = UTC,
        min_coverage: float = 0.5,
    ) -> dict[str, Any]:
        """A comparison-day baseline for ``[start, end)``, skipping event days.

        Days on which any recorded event had devices under control are left
        out, when the event part is on. See :meth:`IntervalHistory.baseline`.
        """
        if self.intervals is None:
            raise RuntimeError("the interval history is switched off")
        excluded: list[tuple[float, float]] = []
        if self.events is not None:
            excluded = self.events.control_windows(
                start - (lookback_days + 2) * 86400.0, start
            )
        return self.intervals.baseline(
            start,
            end,
            lfdi=lfdi,
            days=days,
            lookback_days=lookback_days,
            weekdays_only=weekdays_only,
            excluded=excluded,
            tz=tz,
            min_coverage=min_coverage,
        )

    def status(self) -> dict[str, Any]:
        """What is switched on, where the file is, and how much it holds."""
        db = self.database
        return {
            "enabled": True,
            "path": db.path,
            "retention_days": db.retention_seconds / 86400.0,
            "events": self.events is not None,
            "writes": self.writes is not None,
            "intervals": self.intervals is not None,
            "interval_seconds": (
                self.intervals.interval_seconds if self.intervals is not None else None
            ),
            "rows": {
                table: db.count(table)
                for table, on in (
                    ("event_records", self.events is not None),
                    ("event_responses", self.events is not None),
                    ("device_writes", self.writes is not None),
                    ("device_intervals", self.intervals is not None),
                )
                if on
            },
            "write_failures": dict(db.write_failures),
            "dropped_writes": dict(db.dropped),
        }

    def close(self) -> None:
        """Write unfinished intervals and close the file."""
        if self.intervals is not None:
            self.intervals.flush()
        self.database.close()


def build_history(config: HistoryConfig) -> History | None:
    """Open the history a configuration asks for, or None when it asks for none."""
    if not config.any_part_enabled:
        return None
    return History.open(
        config.path,
        events=config.events.enabled,
        writes=config.writes.enabled,
        intervals=config.intervals.enabled,
        retention_days=config.retention_days,
        interval_seconds=config.intervals.interval_seconds,
        max_gap_seconds=config.intervals.max_gap_seconds,
    )
