# Changelog

Notable changes to this project, newest first. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html): while the major
version is `0`, a minor bump may carry a breaking change and the release note
below says so explicitly.

## [Unreleased]

- A command gate on the write path: an application serving more than one command
  interface can now enforce which of them may command a device, through
  `CommandGate` passed as `command_gate` to `ConnectorDispatcher`. Checked at the
  one funnel every apply path shares, so translated controls, default-control
  fallbacks, comms-loss clears and directly named controls are all covered. A
  refusal is reported and dropped rather than raised, and is not recorded as a
  command. Omitted, every origin may command everything, as before.
- `ConnectorDispatcher.apply_operation` applies one named control to one device,
  for a caller that already knows which control it wants rather than a DERControl
  to translate. It goes through the same funnel, so the command is recorded with
  its origin and a failure is recorded as rejected and re-raised.
- An inherited `BaseConnector` no-op no longer counts as an implemented mode. A
  connector that overrides nothing is no longer credited with accepting every
  command; a mode that resolves to nothing at all still reports the actionable
  warning, and an inherited no-op is skipped quietly. This also applies to the
  clear fan-out, which touches every mode and is recorded.
- Measured device state travels the forwarder transport as `TelemetryFrame`,
  alongside protocol messages and events, publishing to `out/telemetry` under the
  forwarder's topic base with its own queue counter. Values carry the time the
  device was read rather than the time they were published, and the device's own
  quality separately from freshness. Forwarders that carry only protocol messages
  decline it by default.
- `CommandOrigin.SUNSPEC`, for a SunSpec Modbus master writing a control register.
- `CommandNotPermittedError`, raised by `apply_operation` when a gate refuses the
  write. A caller that named one control needs to tell a refusal from a failure;
  a server-issued control is still reported and dropped rather than raised.
- **Compatibility:** `queue_telemetry` joins `BaseForwarder`, so a forwarder that
  satisfies that protocol structurally -- without inheriting `AbstractForwarder`
  -- must define it to keep satisfying `isinstance`. Inheriting the abstract base
  needs no change, since it declines by default. This follows `queue_event` in
  0.3.0 rather than making telemetry a second-class kind routed by capability
  check.

- Connection-telemetry hardening: peer-controlled content stays off the
  metadata topic (protocol errors report the status code alone, payload
  errors report where and how big, a redirect's Location is stripped to
  scheme/host/path, and the retained server URL drops userinfo, query and
  fragment); plain-HTTP sessions report their sockets through the observer
  seam too; a failure closes an expired success window so buffered attempts
  do not wait for a next success that may never come; and a never-opened
  failure no longer wears an earlier attempt's local port.

## [0.3.0] — 2026-08-19

- Connection telemetry: the client reports its own connection outcomes as
  OCSF Network Activity (4001) events on their own MQTT topic
  (`forwarders.connection_telemetry`, off by default). Transport failures
  report `Fail`/`Refuse` with the reason; application-layer failures over a
  connection that opened stay `Open` with a `Failure` status; successes are
  coalesced into windows, failures never are. Where a connection was
  established during the request, the record carries the client's own source
  address and port. Embedders attach the same machinery through the new
  `Sep2Client.connection_observer` seam, or implement
  `py20305.client.observer.ConnectionObserver` to route outcomes elsewhere.
- Southbound telemetry coverage: the DER resource manager and the telemetry
  manager report the nameplate, configuration, status and availability reads
  they issue themselves, and clear-control writes are audited through the
  same path as every other control -- including the comms-loss safe default.
- Wire correction: the device-telemetry envelope's catch-all `protocol` value
  is `generic`, the spelling the consumer contract parses. It was `other`,
  which failed enum parsing and schema validation on the receiving side.
- Telemetry topic hygiene: `topic_suffix` fields reject MQTT wildcards, the
  connection-telemetry topic rejects the protocol-message topic, and the two
  telemetry channels refuse to share one effective topic.
- Intrusion-detection wire tests: a scriptable MQTT 3.1.1 broker double joins
  the scenario servers, and the three channels -- upstream 2030.5 capture,
  connection-outcome session tracking, downstream device telemetry -- are
  asserted end to end on the bytes that reach the broker.

## [0.2.0] — 2026-08-19

- Subscribe/notify from the runner: a `subscription:` section constructs the
  subscription manager and notification listener and wires them into the
  client. Off by default; enabling it requires `notification_external_host`,
  the address the server delivers notifications to.
- A lightweight SunSpec 700-series Modbus TCP server for wire tests,
  packing its register image with sunspec2's own model definitions
  (Common, 701, 702, 704). The connector is exercised over a real
  socket -- scan, scaled measurement reads, nameplate, control writes,
  Modbus exceptions -- and one test closes the whole loop: an IEEE
  2030.5 control arriving over mutual TLS ends as registers written
  into the controls model, with the Response posted back.
- Registration PINs from the runner: `registration_pins` maps a device
  LFDI to the PIN its Registration resource should carry, verified at
  discovery. Scenario coverage for the auto-registration flow: register
  once when absent, never duplicate, and report a PIN mismatch without
  taking the device off the program.
- Scenario integration tests: a scriptable IEEE 2030.5 test double under
  `tests/scenario/` serves a resource tree over real mutual TLS, records
  every request, and injects faults on demand. The client is driven end
  to end through discovery, control dispatch and Response posting,
  in-band registration and its refusals, error bursts, malformed
  payloads, an outage with recovery, a CA rotation, and a real
  HTTP-to-HTTPS redirect.
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
