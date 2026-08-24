# One buffer per payload kind on the MQTT forwarder

## Problem

`MQTTForwarder` carried captured protocol exchanges (`MessageFrame`), OCSF
events (`EventFrame`) and device measurements (`TelemetryFrame`) on one
`asyncio.Queue`, and `_enqueue` dropped the oldest item when that queue was
full, regardless of kind. Under broker backpressure a telemetry sample could
therefore evict a captured exchange or an event that nothing will send again,
and the item it evicted could belong to an unrelated device. Periodic telemetry
raised the rate at which this happened, because it arrives on a timer rather
than in response to traffic.

The eviction predated telemetry -- events already shared the queue and the same
policy -- which is why it was filed as issue #7 out of the telemetry PR (#6)
rather than fixed inside it.

Two remedies were suggested in that review and they differ in kind. Per-kind
capacity or priority is a queueing change. Coalescing by device is a semantic
one, and it is correct only for measurements, where the newest value supersedes
the older; it is wrong for capture, where every exchange matters. That
asymmetry is the substance of the issue: the kinds want different buffer
policies rather than one shared buffer with priorities bolted on.

## Decisions

- **D1. Two buffers, not one with per-kind capacity.** Captured messages and
  events keep today's bounded FIFO with drop-oldest eviction, so behavior for
  the existing kinds is unchanged. Telemetry moves to a
  `dict[device, TelemetryFrame]`: a new frame for a device replaces that
  device's pending frame and never touches capture.
  Rejected: two FIFO queues with separate capacities, which still drops
  arbitrary older samples rather than superseded ones, and sizes the buffer by
  sample rate instead of by device count.

- **D2. Capture drains first, in bounded runs.** Each pass publishes up to
  `_CAPTURE_RUN` (64) captured items, then up to `_TELEMETRY_SLICE` (8)
  telemetry frames. Capture has priority because it is the payload nothing will
  resend; the bound on the run is what keeps a client under sustained protocol
  load from starving telemetry entirely. Since telemetry coalesces, starvation
  would leave the upstream with no reading at all for the period rather than a
  late one.
  Rejected: strict priority (starves telemetry) and round-robin (halves the
  capture drain rate exactly when capture is backing up).

- **D3. Supersession is not a drop.** Replacing a pending frame for a device is
  normal operation: no diagnostic, counted as `telemetry_superseded`. Only
  capture eviction keeps the `mqtt_queue_full` warning and the
  `messages_dropped` counter, so an operator reading either still sees what
  they mean today -- captured traffic was lost.

- **D4. The telemetry buffer is bounded by device count.** A map keyed by
  device is unbounded if device identifiers are. `telemetry_device_limit`
  (1000, matching the queue default) caps it; a frame for a *new* device
  arriving at the cap is dropped, counted as `telemetry_dropped` and reported
  under its own dedup key. A device already pending consumes no additional slot
  and is always accepted.

- **D5. An arrival wakes the publish loop.** With two buffers the loop can no
  longer block on `Queue.get`, so a `_wakeup` event set by both entry points
  replaces the poll. Without it a telemetry frame arriving at an idle forwarder
  would wait up to a second for the shutdown tick. `stop()` sets it too, so the
  drain begins immediately.

- **D6. `queue_size` keeps its meaning.** It reports everything buffered across
  both kinds, as it did. `capture_queue_size`, `telemetry_pending`,
  `telemetry_superseded` and `telemetry_dropped` are added beside it rather
  than redefining it under anything already reading it.

## Acceptance criteria

1. A telemetry flood against a full capture buffer costs no captured message:
   all are published and `messages_dropped` stays 0. (Fails before the change.)
2. An event is never evicted by telemetry and is never coalesced.
3. Two frames for one device collapse to one carrying the newer value, counted
   as `telemetry_superseded`, with no backpressure warning.
4. A device reporting often does not displace a quiet device's pending frame.
5. With both buffers occupied, capture publishes before telemetry.
6. A full capture buffer still drops its oldest item and still emits exactly
   one deduped `MQTT queue full` warning.
7. Telemetry pending at `stop()` is published during the drain.
8. A new device arriving at `telemetry_device_limit` is dropped, counted and
   reported once; a device already pending is still accepted.
