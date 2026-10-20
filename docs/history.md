# Operations history

A client that applies utility controls gets asked, afterwards, what it did:
which programs ran, for how long, on which devices, whether each device took
the control, and what that did to the power the devices delivered. The
operations history keeps the answers on disk, in one SQLite file, for as long
as you configure.

It has three parts. Each is on by default and each can be switched off on its
own:

| Part | What it keeps | Fed by |
|---|---|---|
| `events` | Every event's lifecycle (scheduled, active with which devices, how it ended), the control's description and settings, and every DERControlResponse posted or failed | The event processor |
| `writes` | Every control written to a device: applied, failed, or refused by the command gate, with the mRID of the control and of the event that caused it | The dispatcher |
| `intervals` | Each device's readings integrated into fixed intervals: real and reactive energy, coverage, and curtailed energy | The telemetry cycle |

Nothing in the client's operation depends on the history. A history that
cannot be written is logged and skipped, and never stops a control.

## Configuration

```yaml
history:
  enabled: true                       # false: no history, no file
  path: py20305-history.sqlite3       # relative to the working directory
  retention_days: 400
  events:
    enabled: true
  writes:
    enabled: true
  intervals:
    enabled: true
    interval_seconds: 900             # must divide a day evenly
    max_gap_seconds: 900
```

Every key is optional; the values above are the defaults. To keep no history
at all:

```yaml
history:
  enabled: false
```

To keep events and writes but not measurements:

```yaml
history:
  intervals:
    enabled: false
```

If the file cannot be opened, for example because the working directory is
read-only or the path is a directory, the client logs an error naming the path
and runs without a history. Set `history.path` to a writable location, or switch the history off
to silence it.

A new file is created readable by its owner only (0600), and the directory that
holds it 0700 when the client creates that directory. On POSIX an existing file
loses its group and other permissions. The file uses SQLite's write-ahead log,
so a `-wal` and a `-shm` file sit beside it while the client runs. Copying the files while the client writes can
produce a copy that is missing records or cannot be opened. Back it up with
SQLite's own backup, which is safe while the client runs:

```bash
sqlite3 py20305-history.sqlite3 ".backup history-backup.sqlite3"
```

or stop the client and copy the file.

Writes never wait on the disk. They are queued and written by one background
thread; if the disk falls far behind, writes are dropped and counted in
`GET /api/v1/history` rather than slowing the client.

### Cost and size

The control path only queues a write; one background thread writes it, at
about a tenth of a millisecond per row on a desktop SSD. Reads run in the
management API's thread pool, not on the event loop. A large read is not free:
reading 100,000 device writes takes about half a second on the same disk, and
holds back the history writer, not the controls, while it runs. Ask for what you
need with `since`, `until` and `limit`.

The file is bounded by age (`retention_days`), not by size. A device write
takes about a third of a kilobyte, so a site writing one control a minute
grows `device_writes` by roughly 70 MB a year. Intervals add one row per device
per interval, 96 a day at the default length.

### In a container

The image's working directory is the `py20305` user's home, so the default
path is inside the container and is lost with it. Keep it on a volume:

```bash
docker run -d \
  -v "$PWD/config:/etc/py20305:ro" \
  -v py20305-home:/home/py20305 \
  py20305
```

A new named volume takes its ownership from the image, so the client can
write to it.

## What each part records

### Events

Each lifecycle record the [control audit trail](forwarding.md#the-control-audit-trail)
builds is kept here, whether or not the audit trail is published. The first
record of each event also carries the control itself: its description, its
program's description, its interval and its `DERControlBase` settings, so a
report can say "export limit 50%" after the server has stopped serving the
control. Published audit records do not change.

Every DERControlResponse is kept per device with its status code, and a POST
that failed is kept with its error.

Times are on the server's timebase, as everywhere else in the event path.

### Device writes

One row per write, including writes that failed and writes the command gate
refused. `applied_mrid` is the control or default control written;
`cause_mrid` is the event whose start or end caused it. Querying by an event's
mRID matches either, so it returns the event's own writes and the fallback
writes made when it ended.

Write times are moved onto the server's timebase so they line up with events.

### Intervals

The telemetry cycle reads each device on a schedule. Each reading is held
until the next one and integrated into fixed intervals, 15 minutes by default.
So the intervals need `telemetry.enabled: true`, and they cover only devices
the telemetry cycle reads.

- **Coverage.** `covered_seconds` is how much of the interval was measured. A
  gap between readings longer than `max_gap_seconds` is not bridged, and a
  reading the device could not refresh is not held, so a quiet device shows as
  unmeasured rather than as steady.
- **Signs.** `wh_positive` and `wh_negative` (and the `varh_` pair) are kept
  apart in the connector's own sign, so they do not cancel inside an interval.
- **Curtailment.** `wh_curtailed` is available power minus actual output,
  never below zero, while the device reports its available power
  (`statWAvail`). It is `null` unless output was measured while available power was known.
  It assumes positive real power means output. Available power is read with
  the DERAvailability report, so it is present only when the server exposes
  that resource.

A row is written when the interval ends. Intervals still open when the client
stops are written then, and readings after a restart are added to the same
row.

## Baselines

A baseline answers "what would the devices have done without the control?" The
history computes the common comparison-day form: for each interval of a
window, the average power in the same interval, by local time, on the 10 most
recent earlier weekdays that qualify. A day qualifies when no recorded event
had devices under control on it, and every interval was measured for at least
half its length on every device.

The window can be at most one day. The result lists the days used, and the
baseline, actual and difference for each interval and in total. An interval of
the window measured for less than half its length has no actual value. A program that settles on a different rule can
compute it from the intervals directly.

## Reading it

### Over the management API

With the [management API](api.md) enabled:

| Endpoint | Returns |
|---|---|
| `GET /api/v1/history` | Which parts are on, the file, the retention, row counts and write failures |
| `GET /api/v1/history/events?since=&until=&program_href=` | One summary per event whose time under control overlaps the window, newest first |
| `GET /api/v1/history/events/{mrid}` | One event's summary, audit records, responses and device writes |
| `GET /api/v1/history/writes?event_mrid=&lfdi=&control=&since=&until=` | Device writes, oldest first |
| `GET /api/v1/history/intervals?lfdi=&since=&until=` | Measured intervals, oldest first, including the one in progress |
| `GET /api/v1/history/baseline?start=&end=&lfdi=&days=&tz=` | A comparison-day baseline for the window |

Times are epoch seconds. `tz` is an IANA zone name such as
`America/Los_Angeles` and decides where local days begin; it defaults to UTC.
On Windows, named zones need the `tzdata` package.

An event summary looks like this:

```json
{
  "event_mrid": "7C1E...A410",
  "program_href": "/derp/1",
  "control": {"description": "Export limit 50%", "control_base": {"...": "..."}},
  "state": "completed",
  "effective_start": 1792432800,
  "effective_duration": 10800,
  "active_at": 1792432800.4,
  "ended_at": 1792443600.1,
  "ran_seconds": 10799.7,
  "applied_lfdis": ["..."],
  "rejected_lfdis": [],
  "responses_posted": {"ACKNOWLEDGED": 6, "ACTIVE": 6, "COMPLETED": 6},
  "responses_failed": 0
}
```

Every endpoint answers `{"enabled": false, ...}` when that part is off.

### From Python

```python
from py20305.history import History

history = History.open("py20305-history.sqlite3")
events = history.events.events(since=start, until=end)
writes = history.writes.query(event_mrid=events[0]["event_mrid"])
intervals = history.intervals.query(lfdi=lfdi, since=start, until=end)
baseline = history.baseline(event_start, event_end, lfdi=lfdi)
```

## In an application of your own

The command-line runner builds the history from its configuration. An
application constructing the client itself opens one and hands each part to
what feeds it:

```python
from py20305.history import History

history = History.open("/var/lib/myapp/history.sqlite3")
client.attach_history(history)                        # events and responses
dispatcher.attach_write_history(history.writes)       # device writes
manager = TelemetryManager(..., interval_history=history.intervals)
...
history.close()                                       # writes open intervals
```

Pass `events=False`, `writes=False` or `intervals=False` to
`History.open` to leave a part out; that part is then `None`.
