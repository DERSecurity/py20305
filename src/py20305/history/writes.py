"""Device-write history: every control written to a device, and why.

The dispatcher already knows, for each write, which control it came from --
the mRID of the DERControl or DefaultDERControl that was applied, and the
event whose start or end caused it. The device-telemetry stream publishes
those, but nothing kept them. This part stores each write with both mRIDs, so
the writes an event caused can be listed by the event's mRID, including the
ones a device rejected and the ones the command gate refused.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from py20305.history.database import HistoryDatabase

#: Outcomes a write can have.
APPLIED = "applied"
FAILED = "failed"
REFUSED = "refused"

_DDL = (
    """CREATE TABLE IF NOT EXISTS device_writes (
        id INTEGER PRIMARY KEY,
        at REAL NOT NULL,
        device TEXT NOT NULL,
        lfdi TEXT,
        control TEXT NOT NULL,
        params TEXT NOT NULL,
        origin TEXT,
        applied_mrid TEXT,
        cause_mrid TEXT,
        outcome TEXT NOT NULL,
        error TEXT,
        error_type TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS device_writes_at ON device_writes (at)",
    "CREATE INDEX IF NOT EXISTS device_writes_applied ON device_writes (applied_mrid)",
    "CREATE INDEX IF NOT EXISTS device_writes_cause ON device_writes (cause_mrid)",
    "CREATE INDEX IF NOT EXISTS device_writes_lfdi ON device_writes (lfdi, at)",
)

_INSERT = (
    "INSERT INTO device_writes (at, device, lfdi, control, params, origin, applied_mrid, "
    "cause_mrid, outcome, error, error_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

#: Longest error text kept per write. A device can put any string in an
#: exception, and the history must not grow by it.
_MAX_ERROR = 512


def _mrid_hex(mrid: bytes | None) -> str | None:
    """An mRID as uppercase hex, the form the audit trail and the wire use."""
    return mrid.hex().upper() if mrid is not None else None


class WriteHistory:
    """Stores device writes and reads them back by device, event and time."""

    def __init__(
        self,
        database: HistoryDatabase,
        *,
        offset: Callable[[], float] = lambda: 0.0,
    ) -> None:
        self._db = database
        #: Added to the local wall-clock time a write was issued at, so writes
        #: line up with events, which are timed on the server's timebase.
        self.offset = offset
        database.register_table("device_writes", "at", _DDL)

    # One keyword per stored column.
    def record(  # pylint: disable=too-many-arguments
        self,
        device: str,
        control: str,
        params: Mapping[str, Any],
        *,
        outcome: str,
        at: float | None = None,
        lfdi: str | None = None,
        origin: str | None = None,
        applied_mrid: bytes | None = None,
        cause_mrid: bytes | None = None,
        error: str | None = None,
        error_type: str | None = None,
    ) -> bool:
        """Store one write.

        Args:
            device: The name the dispatcher knows the device by.
            control: The connector operation, e.g. ``p_lim``.
            params: What was written.
            outcome: ``applied``, ``failed`` (the connector raised) or
                ``refused`` (the command gate stopped it before the device).
            at: When it was issued, local epoch seconds. Defaults to now.
            lfdi: The device's LFDI when known.
            origin: Who issued it, e.g. ``ieee2030_5`` or ``local_api``.
            applied_mrid: The DERControl or DefaultDERControl written.
            cause_mrid: The event whose start or end caused the write.
            error: The failure text, for ``failed`` and ``refused``.
            error_type: The failure's exception class name.
        """
        try:
            row = (
                (time.time() if at is None else at) + self.offset(),
                device,
                lfdi.lower() if lfdi else None,
                control,
                json.dumps(dict(params), default=str, sort_keys=True),
                origin,
                _mrid_hex(applied_mrid),
                _mrid_hex(cause_mrid),
                outcome,
                error[:_MAX_ERROR] if error else None,
                error_type,
            )
        except (TypeError, ValueError):
            return False
        return self._db.write("device_writes", _INSERT, row)

    # One keyword per filter.
    def query(  # pylint: disable=too-many-arguments
        self,
        *,
        event_mrid: str | None = None,
        lfdi: str | None = None,
        control: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Stored writes, oldest first.

        ``event_mrid`` matches a write that applied that control or was caused
        by that event, so one query lists everything an event did to the
        devices, including the fallback writes when it ended.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if event_mrid is not None:
            clauses.append("(applied_mrid = ? OR cause_mrid = ?)")
            params += [event_mrid.upper(), event_mrid.upper()]
        if lfdi is not None:
            clauses.append("lfdi = ?")
            params.append(lfdi.lower())
        if control is not None:
            clauses.append("control = ?")
            params.append(control)
        if since is not None:
            clauses.append("at >= ?")
            params.append(since)
        if until is not None:
            clauses.append("at < ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._db.query(
            "SELECT at, device, lfdi, control, params, origin, applied_mrid, cause_mrid, "
            f"outcome, error, error_type FROM device_writes {where} ORDER BY at, id LIMIT ?",
            [*params, max(1, limit)],
        )
        return [
            {
                "at": row["at"],
                "device": row["device"],
                "lfdi": row["lfdi"],
                "control": row["control"],
                "params": json.loads(row["params"]),
                "origin": row["origin"],
                "applied_mrid": row["applied_mrid"],
                "cause_mrid": row["cause_mrid"],
                "outcome": row["outcome"],
                "error": row["error"],
                "error_type": row["error_type"],
            }
            for row in rows
        ]
