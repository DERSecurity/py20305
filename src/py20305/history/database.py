"""The SQLite file the operations history is kept in.

One file holds every part of the history, so an operator backs up, moves or
deletes one thing. Each part registers its own tables and names the column its
rows age by; the database prunes all of them against one retention period.

Writes come from the control path: an event changing state, a device write, a
completed measurement interval. None of them may fail because the history did.
Every write here catches its own database error, counts it and logs the first,
and reports failure as a return value rather than an exception.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: How often, at most, a write also prunes rows past the retention period.
#: Pruning on every write would scan the time indexes once per row.
PRUNE_INTERVAL_SECONDS = 3600.0

#: The path that keeps the database in memory, for tests and for hosts that
#: want the query API without a file.
MEMORY = ":memory:"


class HistoryDatabase:
    """One SQLite connection shared by the history parts.

    The connection is opened with ``check_same_thread=False`` and every call
    takes one lock, because device writes can be recorded from a connector's
    worker thread while the event loop records an event transition.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: float = 400.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self.path = str(path)
        if self.path != MEMORY:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        if self.path != MEMORY:
            # WAL lets the API read while the control path writes, and NORMAL
            # sync is durable across a process crash, which is the failure
            # this file has to survive.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        self._lock = threading.Lock()
        self._clock = clock
        self.retention_seconds = retention_days * 86400.0
        self._aged_tables: list[tuple[str, str]] = []
        self._last_prune = 0.0
        self._closed = False
        #: Failed writes by table. A history that stopped recording must not
        #: look like a client with nothing to record.
        self.write_failures: dict[str, int] = {}

    def register_table(self, table: str, time_column: str, ddl: Iterable[str]) -> None:
        """Create a table and its indexes, and prune it by ``time_column``."""
        with self._lock:
            for statement in ddl:
                self._conn.execute(statement)
        self._aged_tables.append((table, time_column))

    def write(self, table: str, sql: str, params: Sequence[Any]) -> bool:
        """Run one write statement. Return False when it failed."""
        return self.write_many(table, sql, [params])

    def write_many(self, table: str, sql: str, rows: Sequence[Sequence[Any]]) -> bool:
        """Run one write statement per row in a single transaction."""
        if not rows:
            return True
        with self._lock:
            if self._closed:
                return False
            try:
                self._conn.execute("BEGIN")
                self._conn.executemany(sql, rows)
                self._conn.execute("COMMIT")
            except sqlite3.Error:
                self._rollback()
                self._failed(table)
                return False
            self._prune_if_due()
        return True

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run one read statement and return its rows."""
        with self._lock:
            if self._closed:
                return []
            return list(self._conn.execute(sql, params))

    def count(self, table: str) -> int:
        """Number of rows in ``table``."""
        rows = self.query(f"SELECT COUNT(*) AS n FROM {table}")  # a registered name
        return int(rows[0]["n"]) if rows else 0

    def prune(self, now: float | None = None) -> int:
        """Delete rows older than the retention period. Return how many went."""
        with self._lock:
            return self._prune(self._clock() if now is None else now)

    def close(self) -> None:
        """Close the connection. Later writes return False and reads return nothing."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    def _prune_if_due(self) -> None:
        now = self._clock()
        if now - self._last_prune >= PRUNE_INTERVAL_SECONDS:
            self._prune(now)

    def _prune(self, now: float) -> int:
        self._last_prune = now
        cutoff = now - self.retention_seconds
        removed = 0
        for table, column in self._aged_tables:
            try:
                cursor = self._conn.execute(
                    f"DELETE FROM {table} WHERE {column} < ?",  # registered names
                    (cutoff,),
                )
                removed += max(cursor.rowcount, 0)
            except sqlite3.Error:
                self._failed(table)
        return removed

    def _rollback(self) -> None:
        with contextlib.suppress(sqlite3.Error):
            self._conn.execute("ROLLBACK")

    def _failed(self, table: str) -> None:
        count = self.write_failures.get(table, 0) + 1
        self.write_failures[table] = count
        if count == 1:
            logger.warning(
                "History failed to write to %s in %s; further failures log at debug",
                table,
                self.path,
                exc_info=True,
            )
        else:
            logger.debug("History failed to write to %s", table, exc_info=True)
