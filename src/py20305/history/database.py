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

Reads see every write queued before them: a read waits until those writes have
run, but not for writes queued after it. Each queued batch gets a number, so a
caller that keeps the same data in memory can tell whether a read includes its
write. Reads are for the management API and reports, which run off the event
loop.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import sqlite3
import stat
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

#: Longest a read waits for the writes queued before it. Past this the read
#: returns what is on disk and logs a warning, so a writer that stops making
#: progress cannot hang every read.
DRAIN_TIMEOUT_SECONDS = 10.0

#: The path that keeps the database in memory, for tests and for hosts that
#: want the query API without a file.
MEMORY = ":memory:"


def _create_owner_only(path: Path) -> None:
    """Create the history file, and its directory, readable by their owner only.

    The history holds what was commanded and measured on a site. A new file
    gets 0600, and the directory that holds it gets 0700 when it is created
    here. Parent directories created along the way get the default mode; the
    file's own mode keeps its contents private. SQLite gives its journal files
    the database file's permissions. On POSIX an existing regular file loses
    its group and other permissions; its owner's are kept, so a read-only file
    stays read-only. Anything else already at the path, such as a directory,
    is left for SQLite to refuse.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        # O_EXCL: a file that appears after any earlier check is not opened
        # with its own mode; it falls through to the tightening below.
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        return
    except FileExistsError:
        pass
    if os.name != "posix" or not path.is_file():
        return
    with contextlib.suppress(OSError):
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            os.chmod(path, mode & ~0o077)


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
            _create_owner_only(Path(self.path))
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
        #: Batches accepted and batches run. A batch's number is the value of
        #: ``_queued`` once it is accepted, and the writer runs batches in that
        #: order, so batch ``n`` has run once ``_written >= n``. A read waits
        #: only for the batches queued before it: waiting for an empty queue
        #: instead lets a steady stream of writes hold a read off indefinitely.
        self._queued = 0
        self._written = 0
        self._progress = threading.Condition()
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
        return not rows or self.queue_batch(table, sql, rows) is not None

    def queue_batch(self, table: str, sql: str, rows: Sequence[Sequence[Any]]) -> int | None:
        """Queue one write statement per row, like :meth:`write_many`.

        Return the batch's number, to compare with the number
        :meth:`query_through` returns, or None when the rows were not queued.
        """
        if self._closed:
            return None
        with self._progress:
            # The number and the queue position are taken together, so batch
            # numbers follow the order the writer runs them in.
            try:
                self._queue.put_nowait((table, sql, list(rows)))
            except queue.Full:
                pass
            else:
                self._queued += 1
                return self._queued
        self.dropped[table] = self.dropped.get(table, 0) + 1
        if self.dropped[table] == 1:
            logger.warning("History write queue is full; dropping writes to %s", table)
        return None

    def drain(self) -> None:
        """Wait until every write queued before this call has been run.

        Writes queued after it are not waited for. Stops waiting if the writer
        thread is gone, or after ``DRAIN_TIMEOUT_SECONDS``, so a read can never
        hang on writes nothing will run. A read that stops on the timeout logs
        a warning and may miss those writes.
        """
        deadline = time.monotonic() + DRAIN_TIMEOUT_SECONDS
        with self._progress:
            target = self._queued
            while self._written < target and self._writer.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "History read waited %.0f s for %d queued write batch(es); "
                        "reading without them",
                        DRAIN_TIMEOUT_SECONDS,
                        target - self._written,
                    )
                    return
                self._progress.wait(min(0.1, remaining))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run one read statement, after the writes queued before it."""
        return self.query_through(sql, params)[0]

    def query_through(self, sql: str, params: Sequence[Any] = ()) -> tuple[list[sqlite3.Row], int]:
        """Run one read statement, after the writes queued before it.

        Return the rows and the number of the last batch that had run when the
        statement read the database. The rows include every batch up to that
        number and none after it.
        """
        self.drain()
        with self._lock:
            if self._closed:
                return [], self._written
            return list(self._conn.execute(sql, params)), self._written

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
                        # Counted while the database lock is held, so a read
                        # under that lock knows which batches it includes.
                        with self._progress:
                            self._written += 1
                            self._progress.notify_all()
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
