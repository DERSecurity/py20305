# Changelog

Notable changes to this project, newest first. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html): while the major
version is `0`, a minor bump may carry a breaking change and the release note
below says so explicitly.

## [0.2.0] — unreleased

- The runner now PUTs DERCapability, DERSettings and DERStatus, and posts
  LogEvents and DERAvailability. The managers behind them existed and were
  tested; nothing constructed them, so a deployment reported readings and
  nothing else. `telemetry.post_rate_seconds` gains two companions,
  `der_capability_poll_rate_seconds` and `der_settings_poll_rate_seconds`.
- **Behavior change:** `telemetry.enabled` now defaults to `true`. A
  deployment that never set the field starts reading its devices and
  reporting them after upgrading. Set it to `false` to keep observing and
  dispatching without reporting.
- The server's `EndDevice.postRate` now takes precedence over the configured
  posting rate, per IEEE 2030.5.
- Telemetry survives rediscovery: hrefs are re-read when the server's topology
  changes, and a MirrorUsagePointList that appears only in a later
  DeviceCapability starts metering without a restart.
- `py20305.telemetry.TelemetryCoordinator` composes both telemetry managers --
  which devices, at what rate, to which discovered hrefs -- so an embedding
  application starts telemetry the way the runner does rather than
  reimplementing it. `CsipClient.set_on_structural_change()`,
  `ClientAPIService.attach_telemetry()` and `DerResourceManager.stop_device()`
  are the seams it needs.
- The management API now reports telemetry for a runner that started it.
  Previously its telemetry endpoints answered "not initialized" for the
  lifetime of the process, because the API starts before discovery and was
  built without the managers that only exist afterwards.
- Subscribe/notify from the runner: a `subscription:` section constructs the
  subscription manager and notification listener and wires them into the
  client. Off by default; enabling it requires `notification_external_host`,
  the address the server delivers notifications to.
- `POST /api/v1/proxy/http-probe`: issue an HTTP GET to the configured
  server, follow its 301/302 to HTTPS, and report both legs -- the
  instrumentation call the IEEE 2030.5 error-handling conformance test
  drives. Targets only the configured server host.
- Two management-API endpoints, `GET /api/v1/subscriptions` and
  `GET /api/v1/notifications`, reporting the client's active subscriptions
  and received notifications.
- A public seam for embedders: `CsipClient.attach_subscriptions()` and a
  `subscription_manager` property replace reaching into private attributes.

## [0.1.1] — 2026-08-19

No functional changes. The README now uses absolute image and link
addresses, so the PyPI project page renders the project banner and
badges rather than their alt text — PyPI resolves no relative URLs.

## [0.1.0] — 2026-08-18

First release.

### Protocol client

- IEEE 2030.5 discovery from a DeviceCapability document, through
  EndDevice, FunctionSetAssignments, DERProgram and DERControl.
- Mutual-TLS transport with retry and exponential backoff, redirect probing,
  and a connection heartbeat.
- In-band EndDevice registration for the client's own certificate identity,
  performed only when the server does not already have that device, so a
  restart cannot create a duplicate.
- Server timebase: time-sensitive behavior follows the server's `Time`
  resource, and the host clock is never modified.
- LFDI and SFDI derivation from a client certificate.

### Event engine

- DERControl scheduling as a five-state machine, with supersession across
  overlapping programs and across DERControl and DefaultDERControl.
- Randomized start and duration per the standard's randomization fields.
- `Response` acknowledgments at each state transition, gated on what the
  server asked to be told about.
- Communication-loss detection over a configurable silence window, which gates
  control application while upstream contact is lost and, on recovery, resumes
  at the schedule that follows the outage rather than replaying it.
- Pricing: TariffProfile and TimeTariffInterval, off by default.

### Telemetry

- DERStatus, DERCapability, DERSettings and DERAvailability posting.
- Metered readings published as MirrorUsagePoints, with scaling and quality
  flags applied per reading type.
- LogEvent posting.

### Connectors

- SunSpec Modbus over TCP, RTU and TLS.
- A print connector that needs no hardware, for exercising the full discovery,
  event and telemetry path before a device is wired up.
- Custom connectors by subclass or by factory, resolved from configuration.
- Control-mode translation between the standard's vocabulary and device
  points.

### Subscriptions

- Subscribe/notify as an alternative to polling, with a notification listener,
  renewal ahead of server-side expiry, and reconciliation against the server's
  subscription list.
- An active subscription suppresses the corresponding poll, with a slow
  heartbeat poll retained as a safety net for missed notifications.

### Runner

- A `py20305` command driven by one YAML or JSON configuration file.
- `--check` validates the configuration, resolves the certificate and prints
  the LFDI without connecting.
- Connection retry while the server is unreachable, and ordered shutdown on
  `SIGINT` or `SIGTERM`.
- Exit codes distinguish a configuration error from a runtime failure, so a
  supervisor can decline to restart on the former.

### Optional surfaces

- Telemetry posting from the runner, off by default: each configured device is
  read on a schedule and its readings mirrored upstream. This is also what
  makes southbound read telemetry observable from the packaged command, since
  nothing else drives a device read.

- An HTTP management API for observing and nudging a running client, mountable
  into a host application's own app. Requires the `api` extra.
- MQTT forwarding of captured protocol exchanges. Requires the `mqtt` extra.
- Southbound device telemetry, off by default: the Modbus reads and control
  writes between this client and its device, reported on the same channel and
  in the same envelope as the 2030.5 traffic, distinguished by `protocol`.
  Rejected writes are reported with their error; failed and empty reads are
  not reported at all.

### Standards

- IEEE 2030.5-2018 and IEEE 2030.5-2023. Both schemas ship with the package
  and validation runs against them; generated bindings track 2023, and a
  compatibility flag adjusts behavior for servers still on 2018.
- CSIP-AUS, including dynamic operating envelope limits.

### Container

- A `Dockerfile` building an image that carries the client alone: the wheel is
  built in one stage and installed in another, so no build backend or source
  tree reaches the runtime. Runs as a non-root user with a fixed uid, takes its
  configuration from a mounted directory, and receives `SIGTERM` directly so it
  shuts down in order rather than being killed. A compose example is in
  `examples/`.

### Supported Python

- 3.11, 3.12 and 3.13, on Linux, macOS and Windows.
