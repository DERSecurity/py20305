# Wiring the DER resource PUTs into the runner

## Problem

`DerResourceManager` PUTs DERCapability, DERSettings and DERStatus, and is
covered by unit tests, but nothing in `src/` ever constructs it. Neither the
runner nor an embedding caller starts it, so those three resources are never
PUT. Two related gaps sit beside it:

- `cli.py` called `TelemetryManager.start_metering(lfdi, post_rate)` without
  `log_event_list_href` or `der_availability_href`, so LogEvent POSTs and the
  DERAvailability PUT were skipped even with telemetry enabled.
- The runner never passed `on_structural_change`, so hrefs captured at first
  discovery went stale after rediscovery.

The same composition already exists in the host application this library was
extracted from, expressed as methods on its top-level object. Duplicating that
logic in the runner would leave two copies free to drift, and that application
is expected to consume this library.

## Decisions

- **D1. A reusable coordinator, not runner-private functions.**
  `py20305.telemetry.TelemetryCoordinator` owns both managers, the href
  lookup, the poll-rate choice, the restart-on-rediscovery path and ordered
  shutdown. `cli.py` becomes a thin caller. Method names mirror the host
  application's (`start_device_telemetry`, `restart_device_telemetry`) so that
  application can delete its copy and call this one.
  Rejected: composing on `CsipClient`, which would invert the current
  dependency direction, where telemetry depends on the client.

- **D2. One flag, independent of the MirrorUsagePointList.**
  `telemetry.enabled` gates metering and DER resource PUTs together. A server
  advertising no MirrorUsagePointListLink disables metering only; the DER
  resource PUTs still run, because a server expects DERCapability whether or
  not it mirrors readings.

- **D3. `telemetry.enabled` now defaults to `true`.**
  Behavior change: an existing deployment that did not set the field starts
  posting readings after upgrading. Recorded in the changelog.

- **D4. The server's `EndDevice.postRate` wins.**
  Used for metering and DERStatus when present and greater than zero, with the
  configured rate as the fallback. DERCapability and DERSettings keep their own
  configured rates, 86400 s and 60 s.

- **D5. The coordinator takes a connector resolver, not a dispatcher.**
  Keeps telemetry free of any dependency on the connector dispatcher, and lets
  a host application pass its own resolver and its own store-backed
  `MeasurementSource`.

- **D6. Devices can be added and dropped while the client runs.**
  `start_device_telemetry(lfdi)` registers an LFDI it was not constructed with,
  and `stop_device_telemetry(lfdi)` unregisters one for good, so a host
  application driven by EndDevice-deletion notifications needs no second
  bookkeeping layer. `DerResourceManager.stop_device` was added for the second
  half, symmetric with `TelemetryManager.stop_metering`.

Deliberately not ported: gating mirroring on the server's EndDeviceList. That
is a host-application configuration option with no counterpart here.

## Acceptance criteria

1. With `telemetry.enabled: true` and one configured device, the runner PUTs
   DERCapability, DERSettings and DERStatus to the discovered hrefs.
2. With no MirrorUsagePointListLink advertised, DER resource PUTs still run and
   metering does not, with one warning rather than a failing cycle.
3. `start_metering` receives `log_event_list_href` and
   `der_availability_href` resolved from the discovered EndDevice.
4. A device whose EndDevice advertises `postRate` is metered at that rate; one
   that does not falls back to `telemetry.post_rate_seconds`.
5. Rediscovery re-reads every href, and a MirrorUsagePointList that appears
   only in a later DeviceCapability starts metering without a restart.
6. Shutdown stops metering, then the DER resource manager, then the client.
7. The management API reports telemetry and DER resource state for a runner
   that started them.
8. `telemetry.enabled` defaults to `true`, and both new poll rates reject
   values that are not greater than zero.
9. A device named after construction is registered and started; one that is
   stopped is dropped from both managers and is not revived by rediscovery.
10. A device merely absent from the current discovery stays registered, with no
    hrefs, so its cycles idle until the server publishes it again.
