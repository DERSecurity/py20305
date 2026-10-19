"""Event history: what this client did with each control it was sent.

The audit trail already describes every lifecycle decision -- scheduled, active
with which devices, and how it ended -- but it only publishes them, and the
in-memory event store forgets an event a minute after it ends. This part keeps
the same records on disk, together with every DERControlResponse this client
posted, so "which programs ran, for how long and on which devices" can be
answered for any day inside the retention period.

The records are stored as the audit trail builds them, plus the control's
description and settings on an event's first record, so a reader can say
"export limit 50%" without the server still serving that control.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from py20305.history.database import HistoryDatabase

#: States an event's trail ends in. Anything else is still in progress.
TERMINAL_STATES = frozenset(
    {"completed", "cancelled", "superseded", "opted_out", "expired", "skipped"}
)

_RECORD_DDL = (
    """CREATE TABLE IF NOT EXISTS event_records (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        kind TEXT NOT NULL,
        event_mrid TEXT,
        program_href TEXT,
        from_state TEXT,
        to_state TEXT,
        effective_start REAL,
        effective_end REAL,
        record TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS event_records_at ON event_records (at)",
    "CREATE INDEX IF NOT EXISTS event_records_mrid ON event_records (event_mrid)",
    "CREATE INDEX IF NOT EXISTS event_records_window ON event_records (effective_start)",
)

_RESPONSE_DDL = (
    """CREATE TABLE IF NOT EXISTS event_responses (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        event_mrid TEXT NOT NULL,
        lfdi TEXT NOT NULL,
        status INTEGER NOT NULL,
        status_name TEXT NOT NULL,
        posted INTEGER NOT NULL,
        error TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS event_responses_mrid ON event_responses (event_mrid)",
    "CREATE INDEX IF NOT EXISTS event_responses_at ON event_responses (at)",
)

_INSERT_RECORD = (
    "INSERT INTO event_records (at, kind, event_mrid, program_href, from_state, to_state, "
    "effective_start, effective_end, record) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_RESPONSE = (
    "INSERT INTO event_responses (at, event_mrid, lfdi, status, status_name, posted, error) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)

#: Longest error text kept per failed response.
_MAX_ERROR = 512


class EventHistory:
    """Stores audit records and posted responses, and reads them back per event."""

    def __init__(
        self, database: HistoryDatabase, *, clock: Callable[[], float] = time.time
    ) -> None:
        self._db = database
        self._clock = clock
        database.register_table("event_records", "at", _RECORD_DDL)
        database.register_table("event_responses", "at", _RESPONSE_DDL)

    # -- Recording -------------------------------------------------------------

    def record(
        self, record: Mapping[str, Any], *, control: Mapping[str, Any] | None = None
    ) -> bool:
        """Store one audit record: ``der_event``, ``comms_loss`` or ``late_dispatch``.

        ``control`` describes the control on an event's first record. It is
        stored with that record only: later records of the same event change
        its state, not what it asks the devices to do.
        """
        try:
            stored = dict(record)
            if control is not None:
                stored["control"] = dict(control)
            start = stored.get("effective_start")
            duration = stored.get("effective_duration")
            end = start + duration if start is not None and duration is not None else None
            row = (
                float(stored.get("at") or self._clock()),
                str(stored.get("kind", "")),
                stored.get("event_mrid"),
                stored.get("program_href"),
                stored.get("from_state"),
                stored.get("to_state"),
                start,
                end,
                json.dumps(stored, default=str, sort_keys=True),
            )
        except (TypeError, ValueError):
            return False
        return self._db.write("event_records", _INSERT_RECORD, row)

    def record_response(
        self,
        *,
        event_mrid: str,
        lfdi: str,
        status: int,
        status_name: str,
        at: float,
        posted: bool,
        error: str | None = None,
    ) -> bool:
        """Store one DERControlResponse this client posted, or failed to post."""
        row = (
            float(at),
            event_mrid,
            lfdi.lower(),
            int(status),
            status_name,
            1 if posted else 0,
            error[:_MAX_ERROR] if error else None,
        )
        return self._db.write("event_responses", _INSERT_RESPONSE, row)

    # -- Reading ---------------------------------------------------------------

    def records(
        self,
        *,
        event_mrid: str | None = None,
        kind: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Stored audit records, oldest first, filtered by event, kind and time."""
        clauses, params = _time_clauses("at", since, until)
        if event_mrid is not None:
            clauses.append("event_mrid = ?")
            params.append(event_mrid.upper())
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        rows = self._db.query(
            f"SELECT record FROM event_records {_where(clauses)} ORDER BY at, id LIMIT ?",
            [*params, max(1, limit)],
        )
        return [json.loads(row["record"]) for row in rows]

    def responses(
        self,
        *,
        event_mrid: str | None = None,
        lfdi: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Stored responses, oldest first."""
        clauses, params = _time_clauses("at", since, until)
        if event_mrid is not None:
            clauses.append("event_mrid = ?")
            params.append(event_mrid.upper())
        if lfdi is not None:
            clauses.append("lfdi = ?")
            params.append(lfdi.lower())
        rows = self._db.query(
            "SELECT at, event_mrid, lfdi, status, status_name, posted, error "
            f"FROM event_responses {_where(clauses)} ORDER BY at, id LIMIT ?",
            [*params, max(1, limit)],
        )
        return [
            {
                "at": row["at"],
                "event_mrid": row["event_mrid"],
                "lfdi": row["lfdi"],
                "status": row["status"],
                "status_name": row["status_name"],
                "posted": bool(row["posted"]),
                "error": row["error"],
            }
            for row in rows
        ]

    def events(
        self,
        *,
        since: float | None = None,
        until: float | None = None,
        program_href: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """One summary per event whose effective window overlaps ``[since, until)``.

        Newest first. The window is the effective one, after randomization,
        because that is when the devices were under the control.
        """
        clauses: list[str] = ["kind = 'der_event'"]
        params: list[Any] = []
        if since is not None:
            clauses.append("effective_end > ?")
            params.append(since)
        if until is not None:
            clauses.append("effective_start < ?")
            params.append(until)
        if program_href is not None:
            clauses.append("program_href = ?")
            params.append(program_href)
        mrids = [
            row["event_mrid"]
            for row in self._db.query(
                f"SELECT event_mrid, MAX(effective_start) AS s FROM event_records "
                f"{_where(clauses)} GROUP BY event_mrid ORDER BY s DESC LIMIT ?",
                [*params, max(1, limit)],
            )
        ]
        return [summary for mrid in mrids if (summary := self.event(mrid)) is not None]

    def event(self, event_mrid: str) -> dict[str, Any] | None:
        """The summary of one event, or None when nothing about it is stored."""
        trail = self.records(event_mrid=event_mrid, kind="der_event")
        if not trail:
            return None
        return summarize(trail, self.responses(event_mrid=event_mrid), now=self._clock())

    def control_windows(self, since: float, until: float) -> list[tuple[float, float]]:
        """Each event's time under control, as ``(from, to)``, inside a range.

        Used to keep event days out of a comparison-day baseline.
        """
        return [
            (summary["active_at"], summary["ended_at"] or summary["effective_end"])
            for summary in self.events(since=since, until=until, limit=100000)
            if summary["active_at"] is not None
        ]


def summarize(
    trail: list[dict[str, Any]], responses: list[dict[str, Any]], *, now: float
) -> dict[str, Any]:
    """Fold one event's audit records, oldest first, into a summary."""
    first = trail[0]
    last = trail[-1]
    control = next((r["control"] for r in trail if "control" in r), None)
    active = next((r for r in reversed(trail) if r.get("to_state") == "active"), None)
    ended = next((r for r in trail if r.get("to_state") in TERMINAL_STATES), None)
    start = last.get("effective_start")
    duration = last.get("effective_duration")
    effective_end = start + duration if start is not None and duration is not None else None

    active_at = active["at"] if active is not None else None
    ended_at = ended["at"] if ended is not None else None
    ran_seconds: float | None = None
    if active_at is not None:
        stop = ended_at
        if stop is None:
            stop = min(now, effective_end) if effective_end is not None else now
        ran_seconds = max(0.0, stop - active_at)

    posted: dict[str, int] = {}
    failed = 0
    for response in responses:
        if response["posted"]:
            posted[response["status_name"]] = posted.get(response["status_name"], 0) + 1
        else:
            failed += 1

    superseded_by = next((r["superseded_by"] for r in trail if "superseded_by" in r), None)
    return {
        "event_mrid": first.get("event_mrid"),
        "program_href": last.get("program_href"),
        "primacy": last.get("primacy"),
        "control": control,
        "state": last.get("to_state"),
        "first_seen_at": first.get("at"),
        "effective_start": start,
        "effective_duration": duration,
        "effective_end": effective_end,
        "active_at": active_at,
        "ended_at": ended_at,
        "ran_seconds": ran_seconds,
        "lfdis": last.get("lfdis", []),
        "applied_lfdis": active.get("applied_lfdis", []) if active is not None else [],
        "rejected_lfdis": active.get("rejected_lfdis", []) if active is not None else [],
        "superseded_by": superseded_by,
        "transitions": [
            {"at": r.get("at"), "from_state": r.get("from_state"), "to_state": r.get("to_state")}
            for r in trail
        ],
        "responses_posted": posted,
        "responses_failed": failed,
    }


def _time_clauses(
    column: str, since: float | None, until: float | None
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append(f"{column} >= ?")
        params.append(since)
    if until is not None:
        clauses.append(f"{column} < ?")
        params.append(until)
    return clauses, params


def _where(clauses: list[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""
