"""The SQLite file the operations history is kept in.

One file holds every part of the history, so an operator backs up, moves or
deletes one thing. Each part registers its own tables and names the column its
rows age by; the database prunes all of them against one retention period.

Writes come from the control path: an event changing state, a device write, a
completed measurement interval. None of them may wait on the disk or fail
because the history did. So a write only queues its rows, and one writer thread
runs every statement. The queue is bounded: when the disk cannot keep up, rows
are dropped and counted rather than held in memory without limit. A database
error is counted and logged once, in the writer, and never reaches the caller.

Reads see every write queued before them: a read waits for the queue to drain.
Reads are for the management API and reports, which run off the event loop.
"""

from __future__ import annotations

import contextlib
import logging
import queue
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

#: Most write batches waiting for the writer thread. Past this the rows are
#: dropped and counted; a few minutes of a busy client's writes fit easily.
MAX_PENDING_WRITES = 10000

#: How long closing waits for queued writes to reach the disk.
CLOSE_TIMEOUT_SECONDS = 10.0

#: The path that keeps the database in memory, for tests and for hosts that
#: want the query API without a file.
MEMORY = ":memory:"


# Connection, lock, clock, retention and failure counts: one object, one file.
class HistoryDatabase:  # pylint: disable=too-many-instance-attributes
    """One SQLite connection shared by the history parts.

    The writer thread runs every write. Reads run on the caller's thread. Both
    use the one connection, opened with ``check_same_thread=False``, under one
    lock.
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
        #: Write batches dropped because the queue was full, by table.
        self.dropped: dict[str, int] = {}
        self._queue: queue.Queue[tuple[str, str, list[Sequence[Any]]] | None] = queue.Queue(
            maxsize=MAX_PENDING_WRITES
        )
        self._writer = threading.Thread(
            target=self._write_queued, name="py20305-history-writer", daemon=True
        )
        self._writer.start()

    def register_table(self, table: str, time_column: str, ddl: Iterable[str]) -> None:
        """Create a table and its indexes, and prune it by ``time_column``."""
        with self._lock:
            for statement in ddl:
                self._conn.execute(statement)
        self._aged_tables.append((table, time_column))

    def write(self, table: str, sql: str, params: Sequence[Any]) -> bool:
        """Queue one write statement. Return False when it could not be queued."""
        return self.write_many(table, sql, [params])

    def write_many(self, table: str, sql: str, rows: Sequence[Sequence[Any]]) -> bool:
        """Queue one write statement per row, run later in a single transaction.

        Returns at once. False means the rows were not queued: the history is
        closed, or the queue is full and the rows were dropped and counted.
        """
        if not rows:
            return True
        if self._closed:
            return False
        try:
            self._queue.put_nowait((table, sql, list(rows)))
        except queue.Full:
            self.dropped[table] = self.dropped.get(table, 0) + 1
            if self.dropped[table] == 1:
                logger.warning("History write queue is full; dropping writes to %s", table)
            return False
        return True

    def drain(self) -> None:
        """Wait until every queued write has been run.

        Stops waiting if the writer thread is gone, so a read can never hang
        on writes nothing will run.
        """
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks and self._writer.is_alive():
                self._queue.all_tasks_done.wait(0.1)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run one read statement, after the writes queued before it."""
        self.drain()
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
        self.drain()
        with self._lock:
            if self._closed:
                return 0
            return self._prune(self._clock() if now is None else now)

    def close(self) -> None:
        """Run the queued writes, then close the connection.

        Later writes return False and reads return nothing.
        """
        if self._closed:
            return
        self._closed = True
        # Bounded, like the join: a writer that is gone leaves a full queue
        # nothing drains, and shutdown must not wait on it.
        try:
            self._queue.put(None, timeout=CLOSE_TIMEOUT_SECONDS)
        except queue.Full:
            logger.warning("History writer is not draining its queue; closing without it")
        self._writer.join(timeout=CLOSE_TIMEOUT_SECONDS)
        if self._writer.is_alive():
            logger.warning("History writer did not finish within %.0f s", CLOSE_TIMEOUT_SECONDS)
            return
        with self._lock:
            self._conn.close()

    def _write_queued(self) -> None:
        """The writer thread: run each queued batch until close() queues None.

        Nothing a batch raises ends the thread. A writer that died would leave
        the queue to fill and every later write to be dropped, with nothing
        saying why.
        """
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                table, sql, rows = item
                with self._lock:
                    try:
                        self._conn.execute("BEGIN")
                        self._conn.executemany(sql, rows)
                        self._conn.execute("COMMIT")
                        self._prune_if_due()
                    # Any error, not only sqlite3.Error: see the docstring.
                    except Exception:  # pylint: disable=broad-exception-caught
                        self._rollback()
                        self._failed(table)
            finally:
                self._queue.task_done()

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
