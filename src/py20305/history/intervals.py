"""Interval history: what each device measured, in fixed intervals.

The telemetry cycle reads every device on a schedule and keeps only the latest
value. The energy a control moved (curtailed, shifted, absorbed as reactive
power) is an integral over time, so it cannot be worked out from the latest
value. This part integrates the readings as they arrive into fixed intervals
(15 minutes by default) and stores one row per device per interval.

Integration holds each reading until the next one, which is how a meter that
samples does it. A gap longer than ``max_gap_seconds`` is not bridged: a device
that went quiet contributes nothing for the gap, and the row's
``covered_seconds`` says how much of the interval was measured, so a reader can
tell a quiet device from a missing one.

Readings arrive on whatever thread reads the device, and queries come from
the management API's threads, so the running sums are guarded by one lock that
is held only for in-memory work, never for the database. Each process writes
its intervals as its own rows, tagged with a per-process ``boot``; a query adds
an unfinished interval from memory only when this process has not written that
interval yet, so an interval that ends while a query runs is counted exactly
once, from whichever copy is newer.

Signs are the connector's own. Positive and negative real and reactive energy
are kept apart, so a reader applies whichever convention the device uses
without the two cancelling inside one interval. Curtailed energy assumes the
common convention, positive real power for output. It is computed only while
the device reports its available power (``statWAvail``): available power
minus actual output, never below zero.
"""

from __future__ import annotations

import math
import threading
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from py20305.history.database import HistoryDatabase

#: The table, and the one 0.14.0 wrote before rows carried their process.
_TABLE = "interval_rows"
_OLD_TABLE = "device_intervals"

_DDL = (
    """CREATE TABLE IF NOT EXISTS interval_rows (
        lfdi TEXT NOT NULL,
        start REAL NOT NULL,
        seconds INTEGER NOT NULL,
        boot TEXT NOT NULL,
        covered_seconds REAL NOT NULL,
        samples INTEGER NOT NULL,
        ws_positive REAL NOT NULL,
        ws_negative REAL NOT NULL,
        w_min REAL,
        w_max REAL,
        var_seconds REAL NOT NULL,
        vars_positive REAL NOT NULL,
        vars_negative REAL NOT NULL,
        avail_seconds REAL NOT NULL,
        avail_ws REAL NOT NULL,
        curtail_seconds REAL NOT NULL,
        curtailed_ws REAL NOT NULL,
        PRIMARY KEY (lfdi, start, seconds, boot)
    )""",
    "CREATE INDEX IF NOT EXISTS interval_rows_start ON interval_rows (start)",
)

#: Moves 0.14.0's rows into the new table, as written by no current process.
_MIGRATE = (
    "INSERT OR IGNORE INTO interval_rows (lfdi, start, seconds, boot, covered_seconds, samples, "
    "ws_positive, ws_negative, w_min, w_max, var_seconds, vars_positive, vars_negative, "
    "avail_seconds, avail_ws, curtail_seconds, curtailed_ws) SELECT lfdi, start, seconds, '', "
    "covered_seconds, samples, ws_positive, ws_negative, w_min, w_max, var_seconds, "
    "vars_positive, vars_negative, avail_seconds, avail_ws, curtail_seconds, curtailed_ws "
    "FROM device_intervals",
    "DROP TABLE device_intervals",
)

# A process writes each interval once, when it ends, or partial when the
# history closes. Written twice by one process all the same, the sums add and
# the extremes widen. Another process's row for the interval (the same interval
# before and after a restart) is a separate row, and reads add them up.
_UPSERT = """
INSERT INTO interval_rows (lfdi, start, seconds, boot, covered_seconds, samples, ws_positive,
    ws_negative, w_min, w_max, var_seconds, vars_positive, vars_negative, avail_seconds,
    avail_ws, curtail_seconds, curtailed_ws)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (lfdi, start, seconds, boot) DO UPDATE SET
    covered_seconds = covered_seconds + excluded.covered_seconds,
    samples = samples + excluded.samples,
    ws_positive = ws_positive + excluded.ws_positive,
    ws_negative = ws_negative + excluded.ws_negative,
    w_min = CASE WHEN w_min IS NULL THEN excluded.w_min
                 WHEN excluded.w_min IS NULL THEN w_min
                 ELSE MIN(w_min, excluded.w_min) END,
    w_max = CASE WHEN w_max IS NULL THEN excluded.w_max
                 WHEN excluded.w_max IS NULL THEN w_max
                 ELSE MAX(w_max, excluded.w_max) END,
    var_seconds = var_seconds + excluded.var_seconds,
    vars_positive = vars_positive + excluded.vars_positive,
    vars_negative = vars_negative + excluded.vars_negative,
    avail_seconds = avail_seconds + excluded.avail_seconds,
    avail_ws = avail_ws + excluded.avail_ws,
    curtail_seconds = curtail_seconds + excluded.curtail_seconds,
    curtailed_ws = curtailed_ws + excluded.curtailed_ws
"""

#: Most interval starts read in one statement, well under SQLite's limit on
#: bound parameters.
_BATCH = 500

#: Longest window a baseline is computed for. A baseline compares one event's
#: hours with the same hours on other days, so a day is the most it needs, and
#: a bound keeps a bad request from allocating without limit.
MAX_BASELINE_SECONDS = 86400.0

_COLUMNS = (
    "covered_seconds",
    "samples",
    "ws_positive",
    "ws_negative",
    "var_seconds",
    "vars_positive",
    "vars_negative",
    "avail_seconds",
    "avail_ws",
    "curtail_seconds",
    "curtailed_ws",
)
#: One row per device and interval, summed over every process that wrote it,
#: and whether this process (the first bound parameter) is one of them.
_SELECT = (
    "lfdi, start, MIN(w_min) AS w_min, MAX(w_max) AS w_max, "
    + ", ".join(f"SUM({col}) AS {col}" for col in _COLUMNS)
    + ", MAX(boot = ?) AS mine"
)


# One running sum per stored column.
@dataclass
class _Bucket:  # pylint: disable=too-many-instance-attributes
    """Running sums for one device and one interval."""

    covered_seconds: float = 0.0
    samples: int = 0
    ws_positive: float = 0.0
    ws_negative: float = 0.0
    w_min: float | None = None
    w_max: float | None = None
    var_seconds: float = 0.0
    vars_positive: float = 0.0
    vars_negative: float = 0.0
    avail_seconds: float = 0.0
    avail_ws: float = 0.0
    curtail_seconds: float = 0.0
    curtailed_ws: float = 0.0


@dataclass
class _Device:
    """The last reading of one device and its unfinished intervals."""

    at: float | None = None
    w: float | None = None
    var: float | None = None
    avail: float | None = None
    avail_at: float | None = None
    buckets: dict[float, _Bucket] = field(default_factory=dict)


def _number(value: Any) -> float | None:
    """A reading as a finite float, or None when it is absent or not a number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def active_power(value: Any) -> float | None:
    """Watts from a plain number or an IEEE 2030.5 ``{value, multiplier}`` pair."""
    if isinstance(value, Mapping):
        base = _number(value.get("value"))
        multiplier = value.get("multiplier") or 0
        if base is None or isinstance(multiplier, bool) or not isinstance(multiplier, int):
            return None
        return base * (10.0**multiplier)
    return _number(value)


class IntervalHistory:
    """Integrates device readings into fixed intervals and stores them."""

    def __init__(
        self,
        database: HistoryDatabase,
        *,
        interval_seconds: int = 900,
        max_gap_seconds: float = 900.0,
    ) -> None:
        if interval_seconds <= 0 or 86400 % interval_seconds:
            raise ValueError("interval_seconds must divide a day evenly")
        if max_gap_seconds <= 0:
            raise ValueError("max_gap_seconds must be positive")
        self._db = database
        self.interval_seconds = interval_seconds
        self.max_gap_seconds = max_gap_seconds
        self._devices: dict[str, _Device] = {}
        self._lock = threading.Lock()
        #: Tags the rows this process writes; see the module docstring.
        self._boot = uuid.uuid4().hex
        old = database.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (_OLD_TABLE,)
        )
        database.register_table(_TABLE, "start", _DDL + (_MIGRATE if old else ()))

    # -- Recording -------------------------------------------------------------

    def observe(self, lfdi: str, at: float, values: Mapping[str, Any]) -> None:
        """Take one reading of a device: ``W`` and ``Var`` from its monitoring data.

        ``at`` is when the device was read. A reading no newer than the last
        one is ignored, so a cached acquisition offered twice counts once.
        """
        with self._lock:
            device = self._devices.setdefault(lfdi.lower(), _Device())
            if device.at is not None and at <= device.at:
                return
            if device.at is not None and at - device.at <= self.max_gap_seconds:
                self._integrate(device, device.at, at)
            device.at = at
            device.w = _number(values.get("W"))
            device.var = _number(values.get("Var"))
            bucket = self._bucket(device, at)
            bucket.samples += 1
            if device.w is not None:
                bucket.w_min = device.w if bucket.w_min is None else min(bucket.w_min, device.w)
                bucket.w_max = device.w if bucket.w_max is None else max(bucket.w_max, device.w)
            self._flush_before(lfdi.lower(), device, self._floor(at))

    def observe_availability(self, lfdi: str, at: float, w_avail: Any) -> None:
        """Take one reading of a device's available real power, ``statWAvail``."""
        with self._lock:
            device = self._devices.setdefault(lfdi.lower(), _Device())
            device.avail = active_power(w_avail)
            device.avail_at = at if device.avail is not None else None

    def interrupt(self, lfdi: str) -> None:
        """Stop holding a device's last reading: the device could not be read."""
        with self._lock:
            device = self._devices.get(lfdi.lower())
            if device is not None:
                device.at = None

    def flush(self) -> None:
        """Write every unfinished interval, e.g. before the process stops.

        A later process adds its own row for the same interval, and reads add
        the rows up.
        """
        with self._lock:
            rows = [
                self._row(lfdi, start, bucket)
                for lfdi, device in self._devices.items()
                for start, bucket in device.buckets.items()
            ]
            for device in self._devices.values():
                device.buckets.clear()
            self._db.write_many(_TABLE, _UPSERT, rows)

    def _integrate(self, device: _Device, t0: float, t1: float) -> None:
        """Hold the device's last reading from ``t0`` to ``t1``, split at interval ends."""
        avail = device.avail
        if avail is not None and (
            device.avail_at is None or abs(t0 - device.avail_at) > self.max_gap_seconds
        ):
            avail = None
        while t0 < t1:
            start = self._floor(t0)
            end = min(t1, start + self.interval_seconds)
            dt = end - t0
            bucket = self._bucket(device, t0)
            if device.w is not None:
                bucket.covered_seconds += dt
                bucket.w_min = device.w if bucket.w_min is None else min(bucket.w_min, device.w)
                bucket.w_max = device.w if bucket.w_max is None else max(bucket.w_max, device.w)
                if device.w >= 0:
                    bucket.ws_positive += device.w * dt
                else:
                    bucket.ws_negative += device.w * dt
            if device.var is not None:
                bucket.var_seconds += dt
                if device.var >= 0:
                    bucket.vars_positive += device.var * dt
                else:
                    bucket.vars_negative += device.var * dt
            if avail is not None:
                bucket.avail_seconds += dt
                bucket.avail_ws += avail * dt
                if device.w is not None:
                    bucket.curtail_seconds += dt
                    bucket.curtailed_ws += max(0.0, avail - max(device.w, 0.0)) * dt
            t0 = end

    def _bucket(self, device: _Device, at: float) -> _Bucket:
        return device.buckets.setdefault(self._floor(at), _Bucket())

    def _floor(self, at: float) -> float:
        return math.floor(at / self.interval_seconds) * float(self.interval_seconds)

    def _flush_before(self, lfdi: str, device: _Device, start: float) -> None:
        done = [s for s in device.buckets if s < start]
        if not done:
            return
        rows = [self._row(lfdi, s, device.buckets.pop(s)) for s in sorted(done)]
        self._db.write_many(_TABLE, _UPSERT, rows)

    def _row(self, lfdi: str, start: float, b: _Bucket) -> tuple[Any, ...]:
        return (
            lfdi,
            start,
            self.interval_seconds,
            self._boot,
            b.covered_seconds,
            b.samples,
            b.ws_positive,
            b.ws_negative,
            b.w_min,
            b.w_max,
            b.var_seconds,
            b.vars_positive,
            b.vars_negative,
            b.avail_seconds,
            b.avail_ws,
            b.curtail_seconds,
            b.curtailed_ws,
        )

    # -- Reading ---------------------------------------------------------------

    # Filters, then the merge with intervals still open in memory.
    def query(  # pylint: disable=too-many-locals
        self,
        *,
        lfdi: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int = 100000,
    ) -> list[dict[str, Any]]:
        """Stored intervals, oldest first, with the unfinished ones included.

        An interval is returned when it starts inside ``[since, until)``.
        """
        clauses = ["seconds = ?"]
        params: list[Any] = [self.interval_seconds]
        if lfdi is not None:
            clauses.append("lfdi = ?")
            params.append(lfdi.lower())
        if since is not None:
            clauses.append("start >= ?")
            params.append(self._floor(since))
        if until is not None:
            clauses.append("start < ?")
            params.append(until)
        low = self._floor(since) if since is not None else None
        # Memory first, then the database: see the module docstring.
        pending = self._pending(
            lambda name, start: (lfdi is None or name == lfdi.lower())
            and (low is None or start >= low)
            and (until is None or start < until),
        )
        rows = self._db.query(
            f"SELECT {_SELECT} FROM {_TABLE} WHERE {' AND '.join(clauses)} "
            "GROUP BY lfdi, start ORDER BY start, lfdi LIMIT ?",
            [self._boot, *params, max(1, limit)],
        )
        merged = _merge(rows, pending)
        ordered = sorted(merged.values(), key=lambda r: (r["start"], r["lfdi"]))
        return [self._present(row) for row in ordered[: max(1, limit)]]

    def _pending(self, keep: Callable[[str, float], bool]) -> list[dict[str, Any]]:
        """Copies of the unfinished intervals ``keep`` picks, taken under the lock."""
        out: list[dict[str, Any]] = []
        with self._lock:
            for name, device in self._devices.items():
                for start, bucket in device.buckets.items():
                    if keep(name, start):
                        row = {"lfdi": name, "start": start, "w_min": bucket.w_min}
                        row["w_max"] = bucket.w_max
                        row.update({col: getattr(bucket, col) for col in _COLUMNS})
                        out.append(row)
        return out

    def _at(self, starts: list[float], lfdi: str | None) -> list[dict[str, Any]]:
        """Every interval starting at one of ``starts``, read in bounded batches.

        Reads exactly the intervals a baseline compares, so a long history
        cannot crowd the recent days out of a row limit.
        """
        wanted = set(starts)
        pending = self._pending(
            lambda name, start: (lfdi is None or name == lfdi.lower()) and start in wanted
        )
        rows: list[Any] = []
        ordered = sorted(wanted)
        for i in range(0, len(ordered), _BATCH):
            batch = ordered[i : i + _BATCH]
            params: list[Any] = [self._boot, self.interval_seconds, *batch]
            device_clause = ""
            if lfdi is not None:
                device_clause = " AND lfdi = ?"
                params.append(lfdi.lower())
            rows += self._db.query(
                f"SELECT {_SELECT} FROM {_TABLE} WHERE seconds = ? "
                f"AND start IN ({', '.join('?' * len(batch))}){device_clause} "
                "GROUP BY lfdi, start",
                params,
            )
        return [self._present(row) for row in _merge(rows, pending).values()]

    def _present(self, row: Mapping[str, Any]) -> dict[str, Any]:
        covered = row["covered_seconds"]
        var_seconds = row["var_seconds"]
        avail_seconds = row["avail_seconds"]
        return {
            "lfdi": row["lfdi"],
            "start": row["start"],
            "end": row["start"] + self.interval_seconds,
            "seconds": self.interval_seconds,
            "covered_seconds": covered,
            "samples": row["samples"],
            "w_avg": (row["ws_positive"] + row["ws_negative"]) / covered if covered else None,
            "w_min": row["w_min"],
            "w_max": row["w_max"],
            "wh_positive": row["ws_positive"] / 3600.0,
            "wh_negative": row["ws_negative"] / 3600.0,
            "var_avg": (
                (row["vars_positive"] + row["vars_negative"]) / var_seconds
                if var_seconds
                else None
            ),
            "varh_positive": row["vars_positive"] / 3600.0,
            "varh_negative": row["vars_negative"] / 3600.0,
            "w_avail_avg": row["avail_ws"] / avail_seconds if avail_seconds else None,
            # Unknown, not zero, unless output was measured while available
            # power was known.
            "wh_curtailed": row["curtailed_ws"] / 3600.0 if row["curtail_seconds"] else None,
        }

    # The options of the baseline rule.
    def baseline(  # pylint: disable=too-many-arguments,too-many-locals
        self,
        start: float,
        end: float,
        *,
        lfdi: str | None = None,
        days: int = 10,
        lookback_days: int = 45,
        weekdays_only: bool = True,
        excluded: Iterable[tuple[float, float]] = (),
        tz: tzinfo = UTC,
        min_coverage: float = 0.5,
    ) -> dict[str, Any]:
        """Compare a window with the same hours on recent comparable days.

        The baseline for each interval of ``[start, end)`` is the average real
        power in the same interval, by local time in ``tz``, on the ``days``
        most recent earlier days that qualify. A day qualifies when it is a
        weekday (unless ``weekdays_only`` is off), overlaps none of the
        ``excluded`` windows (pass the events' times under control), and has
        every interval measured for at least ``min_coverage`` of its length on
        every device. This is the shape of the "10 of 10" baselines demand
        response programs settle on; a program with its own rule should
        compute it from :meth:`query`.

        With ``lfdi`` omitted, the devices measured in the window are summed,
        or, when the window has no readings yet, those measured before it.
        ``delta_w`` is actual minus baseline, in the connector's sign.
        """
        if not (math.isfinite(start) and math.isfinite(end)):
            raise ValueError("start and end must be finite")
        if end <= start:
            raise ValueError("end must be after start")
        if end - start > MAX_BASELINE_SECONDS:
            raise ValueError(f"the window may be at most {MAX_BASELINE_SECONDS:.0f} seconds")
        if days < 1 or lookback_days < 1:
            raise ValueError("days and lookback_days must be at least 1")
        s = self.interval_seconds
        first = self._floor(start)
        slots = [first + i * s for i in range(math.ceil((end - first) / s))]

        first_day = datetime.fromtimestamp(slots[0], tz).date()
        candidates = [
            _same_local_time(slot, first_day - timedelta(days=back), tz, self._floor)
            for back in range(1, lookback_days + 1)
            for slot in slots
        ]
        actual_rows = self._at(slots, lfdi)
        history = self._at(candidates, lfdi)
        # The devices measured in the window. A window not yet measured (an
        # upcoming event, or one that just started) takes the devices from
        # the comparison days, so its baseline can still be computed.
        devices = sorted({row["lfdi"] for row in actual_rows or history})
        if lfdi is not None:
            devices = [lfdi.lower()]
        measured = _Measured(
            {(row["lfdi"], row["start"]): row for row in history + actual_rows},
            devices,
            min_coverage * s,
        )
        used, samples = self._comparison_days(
            slots,
            measured,
            days=days,
            lookback_days=lookback_days,
            weekdays_only=weekdays_only,
            excluded=list(excluded),
            tz=tz,
        )
        return {
            "lfdi": lfdi.lower() if lfdi is not None else None,
            "devices": devices,
            "start": slots[0],
            "end": slots[-1] + s,
            "interval_seconds": s,
            "days_requested": days,
            "days_used": [d.isoformat() for d in used],
            **_compare(slots, samples, measured, s),
        }

    # The options of the baseline rule, passed through.
    def _comparison_days(  # pylint: disable=too-many-locals,too-many-arguments
        self,
        slots: list[float],
        measured: _Measured,
        *,
        days: int,
        lookback_days: int,
        weekdays_only: bool,
        excluded: list[tuple[float, float]],
        tz: tzinfo,
    ) -> tuple[list[date], list[list[float]]]:
        """The most recent qualifying days before ``slots``, and their power per slot."""
        first_local = datetime.fromtimestamp(slots[0], tz)
        used: list[date] = []
        samples: list[list[float]] = []
        for back in range(1, lookback_days + 1):
            if len(used) >= days or not measured.devices:
                break
            day = first_local.date() - timedelta(days=back)
            if weekdays_only and day.weekday() >= 5:
                continue
            day_start = datetime.combine(day, datetime.min.time(), tz).timestamp()
            # The next local midnight: a day with a clock change is 23 or 25 hours.
            day_end = datetime.combine(
                day + timedelta(days=1), datetime.min.time(), tz
            ).timestamp()
            if any(a < day_end and b > day_start for a, b in excluded):
                continue
            power = measured.day([_same_local_time(slot, day, tz, self._floor) for slot in slots])
            if power is not None:
                used.append(day)
                samples.append(power)
        return used, samples


class _Measured:
    """Stored intervals for a set of devices, summed per slot."""

    def __init__(
        self,
        rows: Mapping[tuple[str, float], Mapping[str, Any]],
        devices: list[str],
        min_seconds: float,
    ) -> None:
        self.rows = rows
        self.devices = devices
        self.min_seconds = min_seconds

    def slot(self, slot: float) -> float | None:
        """Summed average power for one slot.

        None when any device is missing the slot or was measured for less than
        the minimum: an interval measured for a minute is not a measured
        interval.
        """
        total = 0.0
        for device in self.devices:
            row = self.rows.get((device, slot))
            if row is None or row["w_avg"] is None or row["covered_seconds"] < self.min_seconds:
                return None
            total += row["w_avg"]
        return total

    def day(self, slots: list[float]) -> list[float] | None:
        """Summed power per slot, or None when any slot is missing or too short."""
        out = []
        for slot in slots:
            value = self.slot(slot)
            if value is None:
                return None
            out.append(value)
        return out


def _compare(
    slots: list[float], samples: list[list[float]], measured: _Measured, seconds: int
) -> dict[str, Any]:
    """Baseline, actual and difference per slot and in total."""
    actual = measured.day(slots)
    out_slots = []
    baseline_wh = actual_wh = 0.0
    for i, slot in enumerate(slots):
        base = sum(day[i] for day in samples) / len(samples) if samples else None
        act = actual[i] if actual is not None else measured.slot(slot)
        delta = act - base if act is not None and base is not None else None
        if act is not None and base is not None:
            baseline_wh += base * seconds / 3600.0
            actual_wh += act * seconds / 3600.0
        out_slots.append({"start": slot, "baseline_w": base, "actual_w": act, "delta_w": delta})
    compared = any(slot["delta_w"] is not None for slot in out_slots)
    return {
        "slots": out_slots,
        "baseline_wh": baseline_wh if compared else None,
        "actual_wh": actual_wh if compared else None,
        "delta_wh": actual_wh - baseline_wh if compared else None,
    }


def _same_local_time(
    slot: float, day: date, tz: tzinfo, floor: Callable[[float], float]
) -> float:
    """The interval starting at the same local wall time as ``slot``, on ``day``."""
    local = datetime.fromtimestamp(slot, tz)
    return floor(datetime.combine(day, local.time(), tz).timestamp())


def _merge(
    rows: Iterable[Any], pending: Iterable[Mapping[str, Any]]
) -> dict[tuple[str, float], dict[str, Any]]:
    """Stored rows plus the unfinished intervals this process has not written yet.

    A stored row this process wrote (``mine``) was written after the copy in
    ``pending`` was taken, since the copy is taken first; the stored row is the
    newer, so the copy is dropped. Otherwise the copy is added to whatever other
    processes wrote for the interval.
    """
    merged: dict[tuple[str, float], dict[str, Any]] = {
        (row["lfdi"], row["start"]): dict(row) for row in rows
    }
    for item in pending:
        key = (item["lfdi"], item["start"])
        stored = merged.get(key)
        if stored is None:
            merged[key] = dict(item)
        elif not stored.get("mine"):
            merged[key] = _add(stored, item)
    return merged


def _add(stored: Mapping[str, Any], pending: Mapping[str, Any]) -> dict[str, Any]:
    """Combine a stored interval with the unfinished part of the same interval."""
    out = dict(stored)
    for col in _COLUMNS:
        out[col] = stored[col] + pending[col]
    for col, pick in (("w_min", min), ("w_max", max)):
        values = [v for v in (stored[col], pending[col]) if v is not None]
        out[col] = pick(values) if values else None
    return out
