# Forwarding traffic

The client can publish every IEEE 2030.5 exchange it sees — request and
response, with endpoints, timing and payload — to a monitoring system over
MQTT. Useful for audit, for debugging an interop problem against a utility, and
for security monitoring.

Nothing in the client's own operation depends on it. A deployment that does not
forward never loads the module.

Install the `mqtt` extra.

## The message

Each exchange becomes one `ProtocolMessage`. Its serialization contract is
stated in full in
[`py20305.forwarders.types`](reference/forwarders.md), and the
short version is:

- `version`, `protocol`, `direction`, `timestamp`, `client_id`,
  `forwarder_id`, `payload`, `source`, `hash` and `is_valid` are always present.
- `destination`, `protocol_data` and `validation_error` appear only when set.
- `hash` is a deterministic UUIDv3 over protocol, client id, timestamp and
  payload, so a consumer can deduplicate replays without coordinating with the
  producer.

Optional fields are omitted rather than emitted as null, and consumers are
expected to match on a key being present. That is what makes adding a field a
compatible change.

## Southbound device traffic

By default the forwarder carries only the client's *northbound* 2030.5
exchanges. A monitoring system watching it then sees every command the client
received from the utility and none of the commands it issued to the equipment.

That asymmetry is the interesting one: a curtailment that arrives over 2030.5
and a curtailment that reaches the inverter are different facts, and the gap
between them is where a misbehaving client shows up. Turning on device
telemetry reports the second half too.

```yaml
forwarders:
  mqtt:
    endpoint: broker.example.com
  device_telemetry:
    enabled: true
```

The `mqtt` block is not optional here. Device telemetry is a second kind of
payload on the forwarder's transport, not a transport of its own, so enabling
it without one configured gives it nowhere to publish. The client says so at
startup rather than appearing to work.

Reading a device only happens when telemetry posting is on, so a runner
configuration wanting both halves needs both:

```yaml
telemetry:
  enabled: true
forwarders:
  mqtt:
    endpoint: broker.example.com
  device_telemetry:
    enabled: true
```

It is off by default, and reports:

- Every set of readings pulled off a device, as `direction: upstream`.
- Every control written to one, as `direction: downstream` — **including
  rejected writes**, carrying the device's error, `is_valid: false` and the
  reason in `validation_error`. A command that was attempted and refused is
  what an audit trail most needs, since the utility-facing side may still
  believe it succeeded.

Direction follows the data, not whoever initiated the exchange.

Two things are deliberately *not* reported. A read that failed produces no
envelope, because there was no reading and inventing one would be a lie; and a
read that came back empty produces none either, because an empty envelope is
indistinguishable from a device genuinely reporting all zeroes.

These envelopes are the same `ProtocolMessage` shape, published to the same
topic, with `protocol` set to `modbus` — so a collector needs no new
subscription and no new parser, and a consumer tells the two halves apart by
that field. Set `topic_suffix` to route them somewhere else instead:

```yaml
forwarders:
  mqtt:
    endpoint: broker.example.com
  device_telemetry:
    enabled: true
    topic_suffix: out/device
```

The `protocol` field carries what the connector speaks, not an assumption:
`modbus` for a SunSpec device, and `generic` for a connector that reaches no
wire, such as the hardware-free demo. Recording those as Modbus would put a
false claim on the channel and mislead a consumer filtering by protocol.

If the broker is unreachable when the client starts, forwarding is retried in
the background every `forwarders.retry_interval_seconds` (60 by default, 0 to
disable) rather than staying off for the life of the process.

A device is identified by its address where the connector exposes one:
`host:port` for Modbus TCP, and for a serial device the line itself
(`rtu:/dev/ttyUSB0`) rather than a fabricated IP.

## The client's own connection outcomes

The two channels above describe traffic the client carried. A third reports
the client's own connection outcomes -- the one record a passive network
sensor beside it cannot produce, because a certificate that fails validation,
a redirect loop or a 500 from the server are conditions known only inside the
TLS session.

```yaml
forwarders:
  mqtt:
    endpoint: broker.example.com
  connection_telemetry:
    enabled: true
```

Off by default, and like device telemetry it rides the forwarder's transport,
so the `mqtt` block is required for it to have anywhere to publish.

Events are [OCSF Network Activity](https://schema.ocsf.io/) (`class_uid`
4001) records, published to their own topic (`out/connection-events` under
the forwarder's topic base by default -- `topic_suffix` moves it, and the
configuration rejects the protocol-message topic, since OCSF envelopes and
`ProtocolMessage` envelopes must not mix on one subscription). Each record
carries the server endpoint, the service label `ieee2030.5`, and where a
connection was established during the request, the client's own source
address and port -- a fact only the client can report.

What becomes an event:

- A **transport failure** -- connect, timeout, TLS handshake -- reports
  activity `Fail` (or `Refuse` when the peer refused) with status `Failure`
  and the reason in `status_detail`. The reason is the record's entire value,
  so it is required, and it is capped at 512 characters so a peer's response
  body cannot ride through it.
- An **application-layer failure** over a connection that did open -- a 500,
  a 429, a redirect, an unusable body -- keeps activity `Open` with status
  `Failure`. Reporting it as `Fail` would tell a reader the client never
  reached the server, which is not what happened.
- **Successes** are coalesced: within `coalesce_window_seconds` (60 by
  default) they collapse into one record carrying the window's bounds and an
  attempt count, so a polling client does not out-publish the passive capture
  beside it. Zero disables coalescing. Failures are never coalesced -- each
  keeps its own reason.

A 204 No Content counts as a success: it is a validated contact that happens
to signal itself by raising. Outcomes are reported per logical request, not
per retry attempt, and the open success window is flushed when the client
closes.

Embedders not using the runner attach the same machinery through the
client's observer seam: construct a
`py20305.forwarders.connection_telemetry.ConnectionTelemetryEmitter` and
assign it to `Sep2Client.connection_observer` -- or implement
`py20305.client.observer.ConnectionObserver` to route outcomes anywhere
else.

## The control audit trail

The streams above record what the server said and what reached each device.
The audit trail records what the client decided in between, and links the two:
which event a write carried out, when each event was scheduled, went active,
ended or was opted out, and when the client stopped acting on the server
because the server went quiet.

```yaml
forwarders:
  mqtt:
    endpoint: broker.example.com
  audit:
    enabled: true
```

Off by default. Like the other telemetry streams it rides the forwarder's
transport, so the `mqtt` block is required.

One switch publishes two things:

| `audit` | `device_telemetry` | Reads | Writes | Lifecycle records |
|---|---|---|---|---|
| off | off | no | no | no |
| off | on | yes | yes, with cause fields | no |
| on | off | no | yes, with cause fields and sequence | yes |
| on | on | yes | yes, once, with cause fields and sequence | yes |

### Writes

Writes stay on the device telemetry topic. Whichever switch publishes them,
each write's `protocol_data.extra` carries, next to `device`:

- `origin`: who issued the write: `ieee2030_5` for an event,
  `dderc_reapply` for a DefaultDERControl, `comms_loss` for a write made
  because the server went quiet, or the host application's own origin for a
  write it issued.
- `applied_mrid`: the mRID of the DERControl or DefaultDERControl written.
  Absent for a clear and for a write the host application issued directly.
- `cause_mrid`: the event whose lifecycle produced the write: the event itself
  when it activates, and the event that ended, was cancelled or was opted out
  when its devices fall back to their default or are cleared. Absent when no
  single event caused the write.

mRIDs are uppercase hex, the form the `mRID` element takes on the wire. A
rejected write also carries `error_type`, the exception's class name, in its
body beside `error`. A write the command gate refused, because another
interface holds the command role for the device, is recorded too, with
`error_type: refused` and `is_valid: false`; nothing reached the device. These
fields sit in `protocol_data.extra` rather than in the body because the body
travels as one string, and a search index can only query the fields of the
envelope.

The write records are the authority on what reached a device. A device with no
connector, or with none of the event's modes implemented, is dispatched to and
gets no write record at all.

### Lifecycle records

Published as flat JSON documents on their own topic, `out/der-events` under
the forwarder's topic base by default. `topic_suffix` moves it; the
configuration requires it to start with `out/`, rejects the MQTT wildcards,
and rejects any topic another stream uses, including `out/telemetry`.

An event record is published once per transition, never per poll:

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
- An `active` record is published once dispatch has finished, so
  `applied_lfdis` and `rejected_lfdis` are final. They are the outcome each
  device's dispatch returned, which is what the server was told: `applied`
  means the dispatch returned without error, `rejected` that it failed or
  missed the activation deadline. Under a group lookup, where one server
  EndDevice stands for several local devices, they list the local devices one
  by one, so a partial failure shows here even though the server is told the
  event started. Whether a write then reached each device is in the write
  records above.
- An event opted out during loss of communications is no longer in force, so
  any later record for it, such as a cancellation, has `from_state:
  opted_out`.
- A whole supersession is the transition to `superseded`, with
  `superseded_by`. A partial one, where another event takes over only some
  devices or modes, is not a transition: its record has `from_state` equal to
  `to_state`, the event's current state, and carries `superseded_by`,
  `superseded_lfdis` and `superseded_modes`. Under a group lookup these name
  the program's local devices.
- LFDIs are lowercase hex throughout.

A DefaultDERControl is not an event and produces no lifecycle record; its
writes carry `origin: dderc_reapply` and the `cause_mrid` of the event that
ended.

A dispatch that misses the activation deadline is listed as rejected in the
`active` record, because that is what the server is told, and its apply
carries on. When it finishes, a `late_dispatch` record says how:

```json
{
  "kind": "late_dispatch", "schema": 1,
  "event_mrid": "0A1B...", "lfdi": "...", "applied": true, "at": 1760000031.0,
  "client_id": "...", "boot_id": "...", "seq": 44
}
```

A failed one also carries `error` and `error_type`.

Loss of communications is recorded in the order it happens: `entered` when the
mode is entered, before any event is opted out; `recovering` once contact
returns, before the schedule is polled again; and `cleared` once the mode
ends. A recovery whose re-poll fails is retried on the next probe, so
`recovering` can repeat before one `cleared`. The `opted_out` records and the
fallback writes come between `entered` and `recovering`, and what the re-poll
finds comes between `recovering` and `cleared`.

```json
{
  "kind": "comms_loss", "schema": 1, "transition": "entered",
  "elapsed_seconds": 905, "threshold": 900, "simulated": false,
  "at": 1760000000.0, "client_id": "...", "boot_id": "...", "seq": 43
}
```

`entered` carries `elapsed_seconds`, how long the server had been silent.
`recovering` and `cleared` carry `duration_seconds` instead, how long the
client had been in the mode, and `cleared` adds `resume_after`: the epoch after
which the schedule resumes, or `null` when the opted-out window has already
passed.

Lifecycle, `late_dispatch` and loss-of-communications records give `at` on the
server's timebase, so they line up with the event times the server set. Write
records carry the envelope's own `timestamp`, which is the client's clock.

### Telling a complete trail from an incomplete one

Delivery is best-effort, as for every stream on this transport: a full queue
drops its oldest entry, and a stopped forwarder drops what it is given. Each is
counted in the forwarder's statistics: `messages_dropped` for a full queue,
`events_dropped_not_running` while the whole transport is stopped, and
`events_dropped_by_forwarder`, per forwarder, for events a forwarder did not
take because its broker was unreachable at start or queueing raised. That
counter is keyed by the forwarder's name, with its position added when two
share a name.

Every audit record, and every write while `audit` is on, carries `boot_id` and
`seq`: one counter per client, shared by its writes and its lifecycle records.
A gap in `seq` within a `boot_id` marks a lost record, and a new `boot_id`
marks a restart.

Nothing in the trail can stop a control: a failure building or queueing a
record is logged once and counted, and event processing carries on.

Embedders not using the runner configure the client's emitter themselves:
`client.audit.attach_forwarder(manager)` and
`client.audit.configure(config.forwarders.audit)`. Pass the same `audit`
section, and the client's counter, to the client's `DeviceTelemetryEmitter`:
`telemetry.configure(config.forwarders.device_telemetry,
audit=config.forwarders.audit, sequence=client.audit.sequence)`.

## Measured device state

A third payload kind rides the same transport: `TelemetryFrame`, a device's
measured values as of one acquisition. It is a separate kind rather than a
`MessageFrame` because nine of that type's fields describe an HTTP exchange and
none of them mean anything for a measurement.

```python
from py20305.forwarders.base import TelemetryFrame, TelemetryPoint

forwarder.queue_telemetry(
    TelemetryFrame(
        device=lfdi,
        points={"W": TelemetryPoint(value=4200, source_timestamp=read_at, quality="good")},
        quality="good",
        last_success=read_at,
    )
)
```

Frames publish to `out/telemetry` under the forwarder's topic base, and are
counted separately from protocol messages, so a subscriber that wants only
measurements says so at the broker rather than filtering every message on
arrival.

Two fields carry more than they appear to. `source_timestamp` is when the device
was read, not when the frame was published: a consumer judging freshness cannot
get that from arrival time, because a retained value arrives just as promptly as
a fresh one. And `protocol_quality` is the device's own opinion of the reading,
kept separate from `quality`, which is whether it was read recently enough --
they answer different questions and a consumer usually cares about both.

Declining is the default. A forwarder built to carry protocol capture is not
wrong to ignore telemetry, so `queue_telemetry` drops the frame unless the
forwarder overrides it. The direction holds as it does for every other kind: a
forwarder is a sink, fed by whoever produced the frame, with no read path back
into it.

## Under a slow broker

The MQTT forwarder buffers what it cannot yet publish, and what it sacrifices
when a buffer fills depends on the kind, because the kinds differ in what a
lost item costs.

- **Captured messages and events** share a bounded FIFO. Each is a distinct
  record that nothing will send again, so nothing else may displace them; when
  that buffer is itself full the oldest is dropped, counted in
  `messages_dropped`, and reported as a single deduped backpressure warning.
- **Telemetry** is held newest-per-device in its own buffer. A second frame for
  a device supersedes the pending one rather than queueing behind it — the
  newest reading is what a monitoring upstream wants — and it is counted in
  `telemetry_superseded`, not as a drop. The buffer is bounded by device count
  (`telemetry_device_limit`, 1000 by default); a frame for a *new* device
  arriving at that bound is dropped and reported.

The publish loop drains capture first and in bounded runs, so sustained
protocol traffic delays measurements rather than starving them — and because
telemetry coalesces, starvation would mean no reading at all for the period
rather than a late one.

`get_statistics()` reports both buffers: `queue_size` is everything waiting,
with `capture_queue_size` and `telemetry_pending` beside it.

## Round-tripping

`to_dict()` and `from_dict()` are inverses, and unknown keys under
`protocol_data` survive the trip in `extra` — so a consumer built against one
version can read a message from a later one without losing anything.

```python
from py20305.forwarders.types import ProtocolMessage

restored = ProtocolMessage.from_dict(received)
assert restored.to_dict() == received
```
