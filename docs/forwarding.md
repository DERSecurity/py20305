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

## Round-tripping

`to_dict()` and `from_dict()` are inverses, and unknown keys under
`protocol_data` survive the trip in `extra` — so a consumer built against one
version can read a message from a later one without losing anything.

```python
from py20305.forwarders.types import ProtocolMessage

restored = ProtocolMessage.from_dict(received)
assert restored.to_dict() == received
```
