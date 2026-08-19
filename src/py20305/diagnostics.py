"""In-memory diagnostics store for the management API.

Holds structured ``errors`` / ``warnings`` / ``info`` entries with bounded
capacity per level so long-running processes don't accumulate unbounded
memory. Shape matches what the UI ``diagnostics.js`` consumer expects:
each entry has a ``timestamp`` string, a ``message``, and optional
``details`` / ``source`` fields.

The schema previously included a ``faults`` bucket. It was unused -- no
emit site ever called ``report("faults", ...)`` -- and the term invited
confusion with downstream-device-reported alarm states (which the
the client already surfaces separately via the IEEE 2030.5 LogEvent
flow). The diagnostics store is exclusively for *client-internal*
operational events; device faults are out of scope.

Two surfaces are exposed:

* :class:`DiagnosticsStore` -- the underlying bounded, thread-safe store.
* :func:`report` -- the canonical helper for callsites that want to both
  log and surface a diagnostic event in the UI in one call. ``report``
  reads from a process-wide module-level store initialised by
  :func:`init_store` at entry-point time.

Dedup conventions
-----------------

Callers that expect a problem to potentially recur should pass a
``dedup_key`` so repeated emissions collapse to a single entry. The store
preserves the original ``timestamp`` / ``message`` (the operator's
"what happened first" record stays stable) and updates ``last_seen`` and
``count`` on every dedup-suppressed hit. Suggested key formulas live in
the diagnostics design notes.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

Level = Literal["errors", "warnings", "info"]

LEVELS: tuple[Level, ...] = ("errors", "warnings", "info")
DEFAULT_MAX_ENTRIES = 500

_LEVEL_TO_LOGLEVEL: dict[Level, int] = {
    "errors": logging.ERROR,
    "warnings": logging.WARNING,
    "info": logging.INFO,
}


@dataclass(frozen=True)
class DiagnosticEntry:
    """A single diagnostic record."""

    timestamp: str
    message: str
    source: str | None = None
    details: dict[str, Any] | None = None
    dedup_key: str | None = None
    last_seen: str = ""
    count: int = 1
    # Stable identifier set at creation and preserved across dedup hits, so
    # the UI can address an individual entry for per-row dismissal.
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "timestamp": self.timestamp,
            "message": self.message,
        }
        if self.source is not None:
            d["source"] = self.source
        if self.details is not None:
            d["details"] = self.details
        if self.count > 1:
            d["count"] = self.count
        if self.last_seen and self.last_seen != self.timestamp:
            d["last_seen"] = self.last_seen
        return d


class DiagnosticsStore:
    """Bounded, thread-safe diagnostics store keyed by severity level."""

    def __init__(self, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        self._entries: dict[Level, deque[DiagnosticEntry]] = {
            level: deque(maxlen=max_entries) for level in LEVELS
        }
        self._lock = threading.Lock()

    def add(
        self,
        level: Level,
        message: str,
        *,
        source: str | None = None,
        details: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> bool:
        """Add an entry.

        If ``dedup_key`` is provided and an entry at the same level already
        carries the same key, the existing entry's ``last_seen`` is updated
        to now and its ``count`` is incremented; ``False`` is returned. The
        original ``timestamp`` / ``message`` / ``details`` / ``source`` are
        preserved so the operator's "what happened first" record stays
        stable. Otherwise a new entry is appended and ``True`` is returned.
        """
        if level not in self._entries:
            raise ValueError(f"unknown level: {level!r}")
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            dq = self._entries[level]
            if dedup_key is not None:
                for idx, existing in enumerate(dq):
                    if existing.dedup_key == dedup_key:
                        dq[idx] = replace(
                            existing,
                            last_seen=now,
                            count=existing.count + 1,
                        )
                        return False
            entry = DiagnosticEntry(
                timestamp=now,
                message=message,
                source=source,
                details=details,
                dedup_key=dedup_key,
                last_seen=now,
                count=1,
            )
            dq.append(entry)
            return True

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """Return a dict of all entries by level, safe to serialize as JSON."""
        with self._lock:
            return {level: [e.to_dict() for e in self._entries[level]] for level in LEVELS}

    def clear(self) -> None:
        """Remove all entries across every level."""
        with self._lock:
            for dq in self._entries.values():
                dq.clear()

    def clear_level(self, level: Level) -> None:
        """Remove all entries at a single level."""
        if level not in self._entries:
            raise ValueError(f"unknown level: {level!r}")
        with self._lock:
            self._entries[level].clear()

    def dismiss(self, entry_id: str) -> bool:
        """Remove a single entry by id; return True if found, False otherwise.

        The id is the same one ``DiagnosticEntry.to_dict()`` exposes. A
        dismissed entry is gone for good -- if the underlying condition
        re-fires, the next ``add()`` call (with the same dedup_key) will
        create a fresh entry with a new id, so dismissal is "acknowledge
        this signal as resolved", not "blacklist this dedup_key forever".

        The audit-log INFO line is emitted *outside* the lock. Logging
        can block (handler I/O, root-logger lock); keeping it inside
        ``self._lock`` would extend the critical section unnecessarily
        and would block concurrent ``add()`` / ``snapshot()`` callers
        for as long as the log write takes.
        """
        dismissed: DiagnosticEntry | None = None
        dismissed_level: Level | None = None
        with self._lock:
            for level in LEVELS:
                dq = self._entries[level]
                for idx, existing in enumerate(dq):
                    if existing.id == entry_id:
                        dismissed = existing
                        dismissed_level = level
                        del dq[idx]
                        break
                if dismissed is not None:
                    break

        if dismissed is None:
            return False

        _logger.info(
            "Diagnostic dismissed: level=%s source=%s message=%r id=%s",
            dismissed_level,
            dismissed.source,
            dismissed.message,
            dismissed.id,
        )
        return True

    def resolve_dedup(self, dedup_key: str) -> bool:
        """Remove the entry carrying ``dedup_key`` (any level); return True if found.

        For a self-clearing condition: a caller that emits a deduped warning
        while a fault persists (e.g. "server unreachable, retrying") calls this
        once the condition clears so the stale entry doesn't linger in the UI.
        Like :meth:`dismiss`, a later ``add()`` with the same key re-creates it.
        """
        with self._lock:
            for level in LEVELS:
                dq = self._entries[level]
                for idx, existing in enumerate(dq):
                    if existing.dedup_key == dedup_key:
                        del dq[idx]
                        return True
        return False


# ---------------------------------------------------------------------------
# Module-level store + report() helper
# ---------------------------------------------------------------------------

_store: DiagnosticsStore | None = None
_logger = logging.getLogger("py20305.diagnostics")


def init_store(store: DiagnosticsStore | None = None) -> DiagnosticsStore:
    """Initialise the module-level diagnostics store and return it.

    Idempotent: callers can pass an existing store instance (used by
    tests and callers that want to share a specific store). When no
    argument is given, a fresh :class:`DiagnosticsStore` is created.
    Re-initialising replaces the module reference; callers that hold a
    saved reference will keep working against the old instance, which
    is fine for tests.
    """
    global _store
    _store = store if store is not None else DiagnosticsStore()
    return _store


def get_store() -> DiagnosticsStore | None:
    """Return the module-level store, or ``None`` if not yet initialised."""
    return _store


def report(
    level: Level,
    message: str,
    *,
    source: str | None = None,
    dedup_key: str | None = None,
    details: dict[str, Any] | None = None,
    exc_info: bool | BaseException = False,
) -> None:
    """Log AND store a diagnostic event in one call.

    Pre-formatted ``message`` only -- callers use f-strings / ``.format()``
    if they need interpolation. Lazy ``%``-formatting through the logger
    would diverge from the stored message text and complicate dedup.

    ``level`` controls both the diagnostic bucket and the log level:

    * ``errors``   -> ``logging.ERROR``
    * ``faults``   -> ``logging.ERROR`` (operationally an error; the
      bucket distinction is for the UI)
    * ``warnings`` -> ``logging.WARNING``
    * ``info``     -> ``logging.INFO``

    ``exc_info`` is forwarded to the logger only. The diagnostics store
    keeps a flat ``details`` dict; callers that want structured
    exception info visible in the UI should pass it explicitly via
    ``details={"error_kind": ..., "error_msg": str(exc)}``.

    No-op-safe before init: if the module store has not yet been
    initialised (very-early startup, tests that don't init), the log
    call still fires and the store call is skipped silently. Callers
    don't need to guard.
    """
    log_level = _LEVEL_TO_LOGLEVEL[level]
    if exc_info:
        # Forward only when truthy so the resulting LogRecord's exc_info is
        # left at its default (None) for callers that opted out -- matching
        # plain logger.warning(msg) behavior.
        _logger.log(log_level, message, exc_info=exc_info)
    else:
        _logger.log(log_level, message)
    store = _store
    if store is None:
        return
    store.add(
        level,
        message,
        source=source,
        details=details,
        dedup_key=dedup_key,
    )


def resolve(dedup_key: str) -> None:
    """Clear a deduped diagnostic once its condition resolves. No-op-safe.

    Counterpart to :func:`report` for self-clearing conditions: report a
    deduped warning while the condition persists, then ``resolve`` it when the
    condition clears. Silently no-ops before the store is initialised.
    """
    store = _store
    if store is not None:
        store.resolve_dedup(dedup_key)
