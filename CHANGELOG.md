# Changelog

Notable changes to this project, newest first. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html): while the major
version is `0`, a minor bump may carry a breaking change and the release note
below says so explicitly.

## [Unreleased]

- **Fixed: events drifted with the local clock while the reported offset stayed
  correct.** Event classification and timer firing read the FSA-scoped offset
  (IEEE 2030.5 §9.2.3), but only the global offset was refreshed. The per-FSA
  observation was taken once at discovery and never renewed, so on a host
  without NTP the client gradually reverted to scheduling on its own drifting
  clock -- while `/status` and `GET /time`, which report the global scope, stayed
  correct throughout. A control scheduled a minute out could be answered
  `EXPIRED` and never fire. `_do_poll_time` now refreshes every scope the
  timebase serves, fetching each distinct href once so a server that points
  DeviceCapability and all its FSAs at one `/tm` is polled once rather than once
  per FSA, and one unreachable Time resource no longer costs the others their
  refresh. A poll where *every* Time resource fails still raises, so a server
  answering nothing is not mistaken for a reachable one.
- The Time poll is now scheduled when either DeviceCapability or an FSA
  advertises a `TimeLink`. It was gated on the DeviceCapability link alone, so a
  server publishing per-FSA Time resources and no global one never polled Time
  at all, and its FSA observations stayed frozen at discovery.
- A per-FSA observation older than `fsa_stale_seconds` (default one hour) now
  yields to a newer global one. §9.2.3 specificity is worth having only while
  the FSA's Time resource is being kept current; past that it is the more
  precise way to be wrong, and it fails silently because a frozen offset is
  indistinguishable from a fresh one where it is used. Staleness is measured on
  the monotonic clock, so stepping the wall clock -- exactly what a device
  syncing its RTC from this client does -- does not age an observation.

## [0.4.0] — 2026-08-21

- **Behavior change:** `telemetry.enabled` now defaults to `true`. A
  deployment that never set the field starts reading its devices and
  reporting them after upgrading, which means writing to the utility server
  and polling its devices on a schedule. Set it to `false` to keep observing
  and dispatching without reporting.
- **Compatibility:** `queue_telemetry` joins `BaseForwarder`, so a forwarder that
  satisfies that protocol structurally -- without inheriting `AbstractForwarder`
  -- must define it to keep satisfying `isinstance`. Inheriting the abstract base
  needs no change, since it declines by default. This follows `queue_event` in
  0.3.0 rather than making telemetry a second-class kind routed by capability
  check.
- A device that joins a DERProgram the client already knows is now mapped onto
  it during a refresh. `refresh_der_programs` branched on whether the *program*
  was new, while the entry that needs creating is the *(program, device)* pair,
  so such a device received none of that program's controls and appeared in no
  response until a full discovery rebuilt the mapping. `DeviceMapping.add` now
  tests membership on the device's program list rather than the program's device
  list, which is the short side of that relation: a refresh of one program
  shared by 5000 devices costs 0.74 ms instead of 58 ms.
- `GET /api/v1/time` returns the head-end's current time with the observed
  clock offset already applied, for field devices that have no NTP and can
  reach nothing but the utility server. `?format=text` returns bare epoch
  seconds for consumers that cannot comfortably parse JSON. When no Time
  resource has been observed it answers 503 rather than falling back to the
  local clock, so a caller about to set its RTC cannot mistake an
  unsynchronized reading for a synchronized one. `ServerTimebase.server_now()`
  is the accessor behind it, distinct from `now()` in that it returns `None`
  instead of the local clock and reports the server's time even when the
  client is configured not to follow it. The response carries
  `Cache-Control: no-store`, since a cached clock reading is wrong in a way
  the consumer cannot detect.
- The `timebase` block in `/status` now measures `age_seconds` on the
  monotonic clock rather than the wall clock, so a device stepping its own RTC
  no longer changes how old an existing observation appears. A backward step
  previously made a stale reading look fresh, or negative.
- Duplicate `EventStarted` responses. Status 2 is posted per device as each
  device's apply settles, concurrently, and the dedup check was separated from
  its mark by the POST itself -- so two targets resolving to one LFDI both got
  through and the server saw one `EventReceived`, two `EventStarted` and one
  `EventCompleted` for a single event. The tracker now reserves the
  `(mrid, code, lfdi)` key before posting and releases it in a `finally`, so a
  failed or cancelled POST still leaves the response retryable. A caller that
  arrives while another is posting waits for that POST's outcome and sends only
  if it failed -- the status-2 path runs once per state transition and has no
  retry driver, so a missing `EventStarted` would be permanent.
- `ResponseTracker.already_sent` now reports an in-flight response as sent, so a
  concurrent caller does not send a second copy. `has_responded` is unchanged
  and still answers whether anything reached the server, so the two disagree
  for the duration of a POST. `ResponseTracker.reserve` is a coroutine.
- A device reachable through discovery twice -- two function set assignments
  naming one program, or a program repeated across pages -- was added to the
  program's device mapping twice, so the control was applied to it twice and
  answered for twice. `DeviceMapping.add` now admits a pair once, and the
  dispatch fan-out no longer trusts a repeated target.
- The runner now PUTs DERCapability, DERSettings and DERStatus, and posts
  LogEvents and DERAvailability. The managers behind them existed and were
  tested; nothing constructed them, so a deployment reported readings and
  nothing else. `telemetry.post_rate_seconds` gains two companions,
  `der_capability_poll_rate_seconds` and `der_settings_poll_rate_seconds`.
- The server's `EndDevice.postRate` now takes precedence over the configured
  posting rate, per IEEE 2030.5.
- Telemetry survives rediscovery: every href is re-read after a rediscovery
  completes, whatever triggered it, and a MirrorUsagePointList that appears
  only in a later DeviceCapability starts metering without a restart.
- `py20305.telemetry.TelemetryCoordinator` composes both telemetry managers --
  which devices, at what rate, to which discovered hrefs -- so an embedding
  application starts telemetry the way the runner does rather than
  reimplementing it. `CsipClient.set_on_structural_change()`,
  `CsipClient.set_on_rediscovered()`, `ClientAPIService.attach_telemetry()`
  and `DerResourceManager.stop_device()` are the seams it needs.
- The management API now reports telemetry for a runner that started it, and
  keeps reporting it when a late MirrorUsagePointList creates the metering
  manager after startup. Previously its telemetry endpoints answered
  "not initialized" for the lifetime of the process.
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
