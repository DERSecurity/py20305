# Management API

An optional HTTP API for observing a running client and nudging it — refresh a
measurement, reconnect, swap a certificate. It is read-mostly; the client does
its job without it.

Install the `api` extra.

## Standalone

```python
import uvicorn
from py20305.api import create_app, ClientAPIService

service = ClientAPIService(client=client, telemetry=telemetry, der_resources=resources)
app = create_app(service)
uvicorn.run(app, host="127.0.0.1", port=8080)
```

Interactive docs are at `/docs`, the OpenAPI schema at `/openapi.json`, and the
endpoints under `/api/v1`.

## Before the client has connected

The API is most useful when the client *cannot* reach the server, so the app
does not require a service up front. Pass a callable and it is consulted per
request:

```python
app = create_app(lambda: my_service_or_none)
```

Until it returns something, the routes report `not_connected` rather than
failing. That is what lets the API stay up across a connection failure and tell
you why.

## Inside an app you already have

If you are embedding this client in your own service, mount the router rather
than taking our application:

```python
from fastapi import FastAPI
from py20305.api import create_client_router

app = FastAPI()
app.include_router(create_client_router(lambda: service), prefix="/csip")
```

## Adding endpoints of your own

`ClientAPIService` is designed to be subclassed. An application managing many
devices extends it rather than reimplementing it:

```python
from py20305.api import ClientAPIService

class MyService(ClientAPIService):
    def get_fleet_summary(self) -> dict:
        ...
```

Build your own router for the additions and include both.

## Reading the head-end's clock

`GET /api/v1/time` returns the current time as the head-end reports it, already
corrected for however far the local clock has drifted.

This exists for field devices that have no NTP. On many deployments the
head-end is the only reachable host, so the IEEE 2030.5 Time function set is
the only clock available, and the device's own RTC free-runs. The client
already tracks the offset between the two (see the `timebase` block in
`/status`), so this endpoint applies it for you:

```console
$ curl http://127.0.0.1:8080/api/v1/time
{
  "current_time": 1787263352,
  "local_time": 1787245352,
  "tz_offset": -18000,
  "dst_offset": 0,
  "dst_active": false,
  "quality": 3,
  "source": "server",
  "offset_seconds": 12.482,
  "age_seconds": 43.1,
  "href": "/tm",
  "timebase_enabled": true
}
```

`current_time` is epoch seconds (UTC). It is the observed offset applied to the
local clock rather than a cached copy of the last `currentTime` seen, so it
advances smoothly between polls. `local_time` is derived the same way, using
the server's own `tzOffset` and `dstOffset` rather than a local time zone
database, since the head-end is the authority on which it means.

Of the fields above, only `current_time` and `source` are guaranteed on a 200.
`local_time`, `tz_offset` and `dst_offset` are null whenever the client is not
currently holding a Time resource, which is the case for the duration of a
rediscovery: the measured offset survives it, so the clock reading stays good
while the time zone it would be expressed in is briefly unknown.

`quality` is the server's own claim about its time source, carried through
unchanged: 3 is an external authoritative source such as NTP, and larger values
are progressively weaker, up to 7 for intentionally uncoordinated. A server
reporting 7 is telling you its clock is not traceable to anything.

`age_seconds` is how long ago the offset was measured, on a monotonic clock, so
that stepping the device's own RTC from this response does not change how old
an existing reading appears. Refreshes follow the `pollRate` the server
advertises on DeviceCapability (the Time resource carries none of its own),
plus the connectivity heartbeat, and the reading is served from that cached
offset, so polling this endpoint costs nothing upstream.

### When there is no answer

If no Time resource has been observed, the endpoint returns **503** with
`"source": "unavailable"` and every derived field `null`. It does not fall back
to the local clock. That fallback is the one failure a caller cannot detect for
itself: the unsynchronized clock is exactly the value that looks right and is
not, and a device about to set its RTC from the response has no way to tell.

`timebase_enabled` reports whether this client *applies* the offset to its own
scheduling. It is independent of the reading: turning it off puts the client on
the local clock for troubleshooting, but the head-end's time is still observed
and still reported here.

### Per-FSA clocks, and when one is abandoned

This endpoint reports the global (DeviceCapability) offset. Scheduling does not
always use it. IEEE 2030.5 §9.2.3 makes a FunctionSetAssignments' own Time
resource authoritative for events from that FSA's programs, so the client keeps
an offset per scope and classifies each event against its own. The `timebase`
block in `GET /api/v1/status` shows all of them: `global`, and one `per_fsa`
entry per FSA, each with the same `offset_seconds`, `quality`, `href` and
`age_seconds` fields as above.

An FSA's offset is the better answer only while its Time resource is being
refreshed. Once the entry is older than the threshold reported as
`fsa_stale_seconds`, and the global observation is newer, scheduling for that
FSA's programs falls back to the global one. A specific offset nobody is
renewing is a more precise way to be wrong, and it is wrong invisibly, since a
frozen offset and a fresh one look identical where they are used. The fallback
never goes to a *staler* observation, and an FSA with no global to fall back to
keeps using what it has.

You can see this happen in two places. The `per_fsa` entry carries
`"stale": true` while its offset is being bypassed, and the first time each
scope is bypassed the client raises a warning naming the FSA and its Time href.
Both mean the same thing: the number shown for that FSA is no longer the one
its events are scheduled against, and its Time endpoint needs looking at.

`fsa_stale_seconds` follows the cadence Time is actually polled at (three poll
intervals, and never less than an hour), so a server advertising a slow
`pollRate` does not retire healthy scopes between two good refreshes. Set
`server.fsa_stale_seconds` in the configuration to override it; leaving it unset
is right for most deployments.

### For constrained clients

`?format=text` (or `Accept: text/plain`) returns the epoch seconds as a bare
integer and nothing else, for consumers that have no comfortable way to walk a
JSON document. PLC structured text and similar environments are the usual case.

```console
$ curl http://127.0.0.1:8080/api/v1/time?format=text
1787263352
```

The unavailable path emits no number here either. It returns 503 with the body
`unavailable`, so a client doing the equivalent of `int(response)` fails loudly
instead of setting its clock from a plausible-looking wrong value.

Both variants are served with `Cache-Control: no-store`. A cached clock reading
is wrong in a way the consumer has no way to detect, and this is the endpoint
most likely to be reached through an intermediary nobody configured.

### Setting a clock from this

Read `age_seconds` and apply a deadband. The offset is measured against the
device's own clock, so once you correct that clock the offset converges toward
zero on the next observation. Stepping again before that observation arrives
applies the same correction twice.
