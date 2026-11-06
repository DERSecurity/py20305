# Observers

The client logs what it does on the wire. An embedding application that keeps
a record of every exchange, passes the client's connections to a monitoring
system, or watches for protocol anomalies needs the same facts as fields.
Observers hand them over: each is a plain callable that receives a frozen
dataclass, and the application decides what to do with it, whether that is a
log line, a database row, or a message on its own transport.

Every observer is optional and off by default. With none attached the client
builds no records at all.

| Observer | Record | Reported for |
|---|---|---|
| exchange | `ExchangeRecord` | every HTTP exchange: each attempt of each request, including one that received no response, and each notification POSTed to the listener |
| connection | `ConnectionEvent` | the client connecting, failing to connect or losing a connection, and the listener accepting or refusing a notification request |
| anomaly | `ProtocolAnomaly` | a body the client could not use, a body that failed schema validation, a status the client did not expect, and a notification refused or dropped |
| device | `DeviceRecord` | every read from and write to a device through its connector |
| audit | `AuditRecord` | every record of the control audit trail |

All five live in `py20305.observers`.

## Attaching them

```python
from py20305.observers import Observers, QueuedObserver

def on_exchange(record):
    ...

client.attach_observers(
    Observers(
        exchange=QueuedObserver(on_exchange),
        connection=on_connection,
        anomaly=on_anomaly,
    )
)
```

`CsipClient.attach_observers` covers the requests the client sends and the
notifications its listener receives. A `Sep2Client` used on its own takes the
same `Observers` through its `observers` argument or property.

The device observer goes to the emitter the dispatcher and the telemetry
managers share, and the audit observer to the client's audit trail:

```python
from py20305.connectors.device_telemetry import DeviceTelemetryEmitter

devices = DeviceTelemetryEmitter(on_device)
dispatcher = ConnectorDispatcher(registry, lfdi_resolver=resolve, telemetry=devices)
client.audit.attach_observer(on_audit)
```

Pass the same emitter to the reading source, the telemetry manager and the
DER resource manager (each takes it as `telemetry` or `device_telemetry`), so
every device read is reported. The configuration-file runner attaches no
observers: observers are for an application that embeds the client.

### Options

`Observers` also says how exchanges are captured:

| Field | Default | Meaning |
|---|---|---|
| `capture_bodies` | `True` | Put request and response bodies in exchange records. Off, `request_body` and `response_body` are `None`; validation and the resource type still use the bodies. Captured bodies include identity payloads such as the Registration `pIN`; an application that stores or forwards records should treat them accordingly, or set this to `False`. |
| `redacted_headers` | `authorization`, `cookie`, `set-cookie` | Header names whose values are replaced by `[redacted]` in exchange records. Compared without regard to case. |
| `validate` | `True` | Validate each XML body against the schemas and report the result in the exchange record, with an `invalid_body` anomaly for a successful response that fails. |
| `schema_dir` | `None` | Directory holding the schema files to validate with; the packaged schemas fill in any it lacks. `Sep2Client.set_schema_validator` sets it too. |

Validation is layered: the IEEE 2030.5 body against the IEEE 2030.5-2023
schema, and each CSIP-AUS extension element on its own against its namespace's
schema. A body that fails validation is still used.

## The stability contract

These records are a public API, and change only as follows:

- Every record carries `record_version`, currently 1.
- Fields are only added. A field is removed only after one minor release in
  which reading it raises a `DeprecationWarning`, and only in the next major
  release. `record_version` is raised when a field is removed or its meaning
  changes. Adding a field does not raise it.
- Records are frozen dataclasses with keyword-only fields. Build them by
  keyword and read them by name, so an added field breaks nothing.
- The values of the enumerations (`ErrorKind`, `ConnectionOutcome`,
  `AnomalyCode` and the others) are stable strings. New members may be added.
- An observer that raises never affects the exchange, the connection or the
  control. `ObserverGuard` catches the exception and counts it: the first
  failure of an observer is logged with its traceback, later ones at DEBUG.
  `Sep2Client.observer_failures`, `DeviceTelemetryEmitter.observer_failures`
  and `AuditEmitter.observer_failures` report the counts.

An observer is called on the event loop, or for a device record on whatever
thread called the connector, and must return quickly. `QueuedObserver` wraps
a slow one: calling it puts the record on a bounded queue and returns at once,
and a daemon thread hands each record to the wrapped callable in order. A
record that arrives while the queue is full is dropped and counted in
`dropped`; the wrapped callable's exceptions are counted in `failures`.
`close()` delivers what is queued and stops the thread.

```python
queued = QueuedObserver(publish, maxsize=10_000)
...
queued.close()
print(queued.dropped, queued.failures)
```

## Exchange records

One `ExchangeRecord` per attempt: a request that is retried is reported once
per attempt, with `attempt` counting from 1 within the logical request.

| Field | Meaning |
|---|---|
| `exchange_id` | Unique per record. Anomalies of the exchange carry it. |
| `direction` | `client_request`, or `notification` for a POST to the listener. |
| `method`, `url`, `path` | The request. `path` includes the query. |
| `request_headers`, `response_headers` | Ordered `(name, value)` pairs, as sent and received, with the redacted ones replaced. For a request that received a response, the headers aiohttp sent, including the ones it adds itself; for one that did not, the client's own headers. |
| `request_body`, `response_body` | The request body exactly as sent. The response body after content decoding: the client asks for `gzip` and `deflate` and aiohttp decompresses, so a compressed response is recorded decompressed while `response_headers` still shows `Content-Encoding` and the compressed `Content-Length`. `None` when there is no body. |
| `status` | The response status, or `None` when no response arrived. |
| `sent_at`, `received_at` | Epoch seconds, UTC. |
| `sent_monotonic`, `received_monotonic`, `duration` | `time.monotonic()` values and the seconds between them, or to the failure. |
| `error_kind`, `error` | Why the attempt failed: `connect`, `tls`, `timeout`, `disconnected`, `invalid_url` or `other`, and the failure in a few words. Set when no response arrived, and when a transport failure ends the exchange after the status was received (a body cut short, a connection reset): then `status` is set too, and a `lost` connection event follows. `None` when the exchange completed, whatever the status. |
| `local`, `remote` | The two ends of the connection the exchange used. |
| `server_host` | The server's host name as configured. |
| `tls_version`, `tls_cipher`, `server_cert_sha256` | The negotiated version and cipher suite, and the SHA-256 fingerprint of the certificate the peer presented. For a notification, the certificate the notifying server presented as a client. |
| `client_lfdi`, `client_sfdi` | This client's certificate identity. |
| `device_lfdi`, `end_device_href` | The device the exchange concerns, when the client can tell (see below). |
| `resource_type` | The root element of the body: the response's for a GET, the request's for a write. Falls back to the expected type when the body has none. |
| `request_valid`, `request_validation_error`, `response_valid`, `response_validation_error` | The schema validation result of each body, or `None` when it was not checked: validation off, no body, or a body that is not XML. |

### Device attribution

After discovery and after every rediscovery the client registers each
EndDevice it found, by its href, with its LFDI. An exchange whose path falls
under one of those hrefs carries that device's `device_lfdi` and, as
`end_device_href`, the href it was registered under. An application that needs
other paths attributed, such as a function set served outside the EndDevice,
registers them with `Sep2Client.update_device_lfdi_prefixes`, mapping each
prefix to an LFDI; those entries take precedence and survive rediscovery.

A MirrorUsagePoint is POSTed to the list, which names no device. The client
attributes that POST to the device its `deviceLFDI` names, when that device is
registered, and remembers the location the server returns, so the readings
POSTed under it are attributed to the same device.

### What the transport cannot tell

The TLS facts and the endpoints come from the connection a response arrived
on, read as soon as the response arrives. They are unavailable, and left
`None` rather than guessed:

- for an attempt that received no response, whose connection failed or was
  never made: the failure is in `error_kind` and `error`, and a connection
  event reports the failed attempt;
- on a plain HTTP connection, which has no TLS facts;
- for the peer's certificate chain beyond its own certificate, and for TLS
  session resumption, which the transport does not expose per exchange;
- for a TLS handshake that fails at the notification listener, which the web
  server completes before any application code runs, so the listener never
  sees it.

## Connection events

| Outcome | Role | When |
|---|---|---|
| `connected` | `client` | A connection was made and passed the TLS handshake and the IEEE 2030.5 certificate profile check. Carries the endpoints and TLS facts. |
| `failed` | `client` | A connection attempt failed: TCP, TLS handshake, certificate check, or a connect timeout. Carries `error_kind` and `reason`, and the server's host and port as `remote` when no socket was made. |
| `lost` | `client` | A connection closed while an exchange was in progress. |
| `accepted` | `listener` | A notification request arrived. |
| `refused` | `listener` | The listener refused a notification request for want of a client certificate, with `status` 401. |

A pooled connection carries many exchanges and is reported once, when it is
made. The listener reports per request, because the web server accepts the
connection before any application code runs.

## Protocol anomalies

| Code | Severity | When |
|---|---|---|
| `unparseable_body` | medium | A successful response whose body is not well-formed XML or not the element expected. |
| `invalid_body` | low | A successful response whose body failed schema validation. It was still used. |
| `unexpected_status` | low | A status the request did not expect. Redirects and 429 are protocol signals the client acts on, and a GET answered 404 says the resource is absent, so none of them is reported. |
| `unreadable_notification` | medium | A notification request whose body could not be read. |
| `rejected_notification` | medium | A notification refused: no client certificate, not a valid Notification, or failing the `xsi:type` check. |
| `dropped_notification` | info | A notification accepted and not acted on while a loss-of-communications simulation runs. |

## Device records

One `DeviceRecord` per read and per write through a connector:

- `operation` is `read` or `write`, and `resource` is what was read
  (`monitoring`, `nameplate`, `configuration`, `status`, `availability`) or
  the control written: the connector method without `update_`, such as
  `op_mod_max_lim_w`.
- `values` holds the values read or the parameters written. A monitoring read
  holds each reading's value keyed by `reading_key` (see
  [Connecting devices](connectors.md)); the other reads hold the dict the
  connector returned.
- `outcome` is `ok`, `failed` (the connector raised; `error` and `error_type`
  say why, the error cut to 512 characters) or `refused` (the command gate
  refused the write because another interface holds the command role; nothing
  reached the device).
- `origin` says who issued a write: `ieee2030_5` for an event,
  `dderc_reapply` for a DefaultDERControl, `comms_loss` for a write made
  because the server went quiet, `event_end` for a mode disabled when its
  event ended, or the host application's own origin.
- `applied_mrid` is the mRID of the DERControl or DefaultDERControl written,
  and `cause_mrid` the event whose lifecycle produced the write: the event
  itself when it activates, and the event that ended, was cancelled or was
  opted out when its devices fall back to their default or are cleared. Both
  are uppercase hex, the form the `mRID` element takes on the wire.
- `protocol` and `endpoint` come from the connector's `telemetry_protocol` and
  `endpoint_id`.

A read that returned nothing is not reported. A device with no connector, or
with none of an event's modes implemented, gets no write record.

## The control audit trail

The exchange and device records say what the server sent and what reached
each device. The audit trail records what the client decided in between:
when each event was scheduled, went active, ended or was opted out, and when
the client stopped acting on the server because the server went quiet.

Each record reaches the audit observer as an `AuditRecord` whose `data` is a
flat document, the same one the [operations history](history.md) keeps. An
event record is made once per transition, never per poll:

```json
{
  "kind": "der_event", "schema": 1,
  "event_mrid": "0A1B...", "program_href": "/derp/1",
  "from_state": "scheduled", "to_state": "active",
  "effective_start": 1760000000, "effective_duration": 3600,
  "primacy": 1, "lfdis": ["..."], "at": 1760000002.5,
  "applied_lfdis": ["..."], "rejected_lfdis": [],
  "client_id": "...", "boot_id": "...", "seq": 42
}
```

- `to_state` is `scheduled`, `active`, `completed`, `cancelled`,
  `superseded`, `opted_out`, `expired` (already over when first received;
  the client answers it EXPIRED) or `skipped` (inside a loss-of-communications
  window the client opted out of). `from_state` is `null` for an event first
  seen already in that state.
- `effective_start` and `effective_duration` are after randomization.
- An `active` record is made once dispatch has finished, so `applied_lfdis`
  and `rejected_lfdis` are final. They are the outcome each device's dispatch
  returned, which is what the server was told.
- A whole supersession is the transition to `superseded`, with
  `superseded_by`. A partial one has `from_state` equal to `to_state` and
  carries `superseded_by`, `superseded_lfdis` and `superseded_modes`, the
  modes named by their IEEE 2030.5 element, such as `opModMaxLimW`.
- LFDIs are lowercase hex. A device whose LFDI is not known is named by its
  EndDevice href, exactly as the server wrote it.

A dispatch that misses the activation deadline is listed as rejected in the
`active` record, and its apply carries on. When it finishes, a
`late_dispatch` record says how:

```json
{
  "kind": "late_dispatch", "schema": 1,
  "event_mrid": "0A1B...", "lfdi": "...", "applied": true, "at": 1760000031.0,
  "client_id": "...", "boot_id": "...", "seq": 44
}
```

Loss of communications is recorded in the order it happens: `entered`,
`recovering` once contact returns (it can repeat if a re-poll fails), and
`cleared`:

```json
{
  "kind": "comms_loss", "schema": 1, "transition": "entered",
  "elapsed_seconds": 905, "threshold": 900, "simulated": false,
  "at": 1760000000.0, "client_id": "...", "boot_id": "...", "seq": 43
}
```

`entered` carries `elapsed_seconds`; `recovering` and `cleared` carry
`duration_seconds`, and `cleared` adds `resume_after`.

`at` is on the server's timebase, so it lines up with the event times the
server set. `client_id` is set with `client.audit.set_client_id`, usually to
the client certificate's LFDI. `boot_id` and `seq` number the records of one
client: a gap in `seq` within a `boot_id` marks a lost record, and a new
`boot_id` marks a restart. The `schema` field is the version of the document
in `data`, which follows the same additive rule as the records themselves.

Nothing in the trail can stop a control: a failure building or storing a
record is logged once and counted in `AuditEmitter.emit_failures`.
