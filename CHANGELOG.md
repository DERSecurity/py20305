# Changelog

Notable changes to this project, newest first. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html): while the major
version is `0`, a minor bump may carry a breaking change and the release note
below says so explicitly.

## [Unreleased]

- The client can now locate an IEEE 2030.5 server on the local network instead
  of being told where one is. IEEE 2030.5 6.9.2 puts this on the client --
  "Clients SHALL locate local services by performing DNS service discovery
  (DNS-SD) queries to the local network" -- and it was the one part of Clause 7
  this client did not implement. `server.url` is now optional; leave it out and
  the client queries at startup, following the sequence Annex C describes: a
  subtype query for the server already holding this client's own EndDevice,
  keyed by its SFDI, and only then a query for any server at all. Asking the
  narrow question first matters on a network with several servers, because the
  one holding your registration is the one to talk to and a generic query
  returns it alongside servers that have never heard of this device. A
  configured `server.url` still wins and suppresses the query entirely, which
  is not a shortcut: 7.6 a) lists "use known URI(s) to DeviceCapability
  resource(s) of interest" as one of three equally valid ways to find a server,
  so an operator who named one has already answered the question. `--discover`
  runs the query, prints what answers and exits without connecting.
- The four TXT rules of 7.4 are applied as the standard states them, because
  each one fails silently when it is not. A record whose `txtvers` is anything
  but 1, or whose `dcap` or `level` is absent or empty, is discarded. The
  `https` key is read as the three states it has: absent means the server
  offers only plain HTTP, present with no value means HTTPS on 443, and present
  with a value means that port. Collapsing the middle state into "absent" is
  the natural mistake, and it downgrades a TLS-only server to a plaintext
  connection. Relatedly, the TLS port comes from that key and never from the
  SRV record: 7.5 fixes the SRV port as the one "specified for the default
  (http) scheme" and requires every SRV record on a device to be identical, so
  an implementation reaching for `srv.port` over TLS is using a number the
  standard guarantees is wrong. A server advertising only plain HTTP is skipped
  with a warning rather than connected to, since IEEE 2030.5 is mutual TLS
  throughout and this client has no way to use one.
- A discovered server's schema extensibility level is reported, and 5.7 ties it
  to an edition: `S1` is IEEE 2030.5-2018 and `S2` is IEEE 2030.5-2023. That
  makes it the server's own statement about which edition it implements, which
  is a better answer than asking an operator to know it -- so the client acts
  on it, and a discovered `-S1` sets `server_2018_compat` where the
  configuration has not. An explicit setting still wins either way, because an
  operator who answered the question should not be overruled by a record
  arriving off a multicast group.
- The DeviceCapability path travels with the discovered URL. 7.4 gives the TXT
  `dcap` key as the path of that resource, so a server advertising
  `dcap=/smartenergy/dcap` is contacted there rather than at the configured
  `server.dcap_path` -- without which a server not using `/dcap` is discovered,
  logged correctly and then asked for a resource it does not serve. The value
  must be a path rooted at `/`; an absolute URL, a bare word or a
  protocol-relative `//host/x` is discarded, since it arrives unauthenticated
  and becomes part of a URL this client then requests.
- A single reply can name more instances than a segment plausibly holds, and
  each name costs a follow-up query multicast to the whole group. The client
  considers the first sixteen and logs what it dropped, so one packet from an
  unauthenticated source cannot turn into a burst on the link.
- `--discover` honors the switches that silence discovery. `discovery.enabled:
  false` and `--multicast-transport off` now stop the diagnostic from querying,
  rather than only stopping the client from querying at startup.
- Announcement joins its multicast group on the configured `interface`, not
  only sending on it. Joining on the default interface while sending on the
  chosen one gives a responder that announces where it was told to and listens
  somewhere else, which presents as one that answers nothing.
- The client can announce itself on the local network, so an inventory tool, a
  commissioning laptop or a passive monitor on the same segment can find it
  without probing. Off by default. This is **not** part of IEEE 2030.5 and is
  not claimed to be: the standard gives the advertising role to servers and the
  querying role to clients, and no clause describes a client publishing a
  record about itself. It is here because operators need to know what is
  running on a network they own. The default service name is `_py20305._tcp`
  rather than the registered `_smartenergy._tcp` for that reason -- announcing
  under the registered name would make every conformant client on the link
  believe it had found a server and then fail against a DeviceCapability
  resource this process does not serve. The records follow the standard's own
  conventions where they apply: the instance name ends with the SFDI as 7.2
  requires, rendered as 12 decimal digits with leading zeros, and the TXT
  record leads with `txtvers=1`. Announcement discloses this client's LFDI and
  SFDI to the segment. Both already cross the wire in the clear on every TLS
  handshake it makes, so this is not a new secret, but it does make collecting
  them much easier, which is the reason the default is off.
- Both halves take a transport, because the two editions of the standard
  disagree about which multicast carries the exchange: `mdns` is normative in
  IEEE 2030.5-2023 (`.local`, RFC 6762), `xmdns` is the 2018 transport
  (`.site`, site-local `FF05::FB`) and is "DEPRECATED but still normative" in
  2023, and `both` uses each in turn. The records are byte-for-byte the same in
  both editions -- the same service name, the same Table 17 subtypes, the same
  TXT keys down to `txtvers=1` -- so this is a multicast group and a domain,
  not a record format, and it is one setting rather than a schema version.
  `--multicast-transport mdns|xmdns|both|off` overrides both at once.
- Queries are sent from an ephemeral port rather than from 5353. RFC 6762 6.7
  has a responder treat a query whose source port is not 5353 as a legacy query
  and answer it by unicast, which is what lets this run without binding the
  well-known port and joining the group -- a client library that bound 5353
  would fight the host's own responder for it and lose on most systems. The
  announcer does try for 5353, since it has to receive queries to answer them,
  and falls back to announcing from an ephemeral port when the port is taken,
  which leaves the client advertised but unable to answer a later query.
- The second discovery round asks one QTYPE ANY question per instance rather
  than a separate SRV and TXT question. Beyond saving a round trip, this is what
  keeps compressed names readable: a compression pointer is an offset from the
  start of the message that wrote it, so SRV and TXT records collected from two
  different datagrams cannot be read as though they shared one buffer. Asking
  one question that returns both records means every name resolves against the
  bytes it was written against.
- Announcement publishes only an address a receiver can connect to. A
  link-local IPv6 address is not one: the scope identifier that would make it
  usable is meaningful only on the host that holds it and cannot travel in a
  record. A host with no routable IPv6 address now announces over IPv4 alone
  rather than publishing an `fe80::` address nothing off-host can dial, which
  is also what §7.1 requires of xmDNS, where IEEE 2030.5 "SHALL use global
  addresses or Unique Local Addresses (IETF RFC 4193)".
- Responses are rate limited. Multicast answers are capped at one per second
  per interface, which RFC 6762 §6 states as a MUST NOT. Unicast answers are
  capped at ten per second, which the standard does not ask for: a UDP source
  address is trivially spoofed, and an unbounded responder is a small amplifier
  aimed at whoever an attacker names. One exchange is all a genuine querier
  needs.
- A unicast answer keeps the full source address. `recvfrom` on an IPv6 socket
  returns a scope identifier alongside the host and port, and sending to a
  link-local peer without it fails, which would have left the querier waiting
  out its timeout with no indication why.
- Retrying a discovery query that found nothing follows the existing
  `connection` block rather than a switch of its own. "The server is not there
  yet" is the same situation whether a query goes unanswered or a connection is
  refused, and an operator who set `retry_forever: false` so a supervisor owns
  restarts meant that for both.
- Multicast traffic goes out with the TTL and hop limit RFC 6762 §11 requires,
  255 on both address families. That is not a routing decision -- the scope is
  already fixed by the group address -- but a value a receiver checks to tell a
  packet that genuinely came from the local link from one that did not, and a
  responder is permitted to discard anything else.
- A unicast answer to a querier whose source port is not 5353 uses the legacy
  encoding of RFC 6762 §6.7: the query's transaction id, its question echoed
  back, and TTLs capped at ten seconds. That querier is an ordinary DNS
  resolver as far as it knows, so it matches the reply to its request by id.
  Answering with id zero left the reply unmatchable, including by this
  package's own discovery side, which drops replies carrying an id it did not
  send -- so the two halves could not have talked to each other.
- A name inside a record may not run past that record. `read_name` works
  against the whole message, because a compression pointer legitimately reaches
  backwards outside the record, but the uncompressed part of a name is now
  bounded by RDLENGTH. Without that check a short RDLENGTH let a name consume
  the record following it, and the result was accepted as a valid target.
- Announcement no longer falls back to an ephemeral source port when UDP 5353
  is already held. RFC 6762 §6 requires an mDNS response to be sent *from*
  5353 and has receivers ignore responses from any other source port, so the
  fallback produced packets a conformant listener discards while the log
  claimed the client was advertised. The transport is now reported unavailable,
  naming the likely cause: a responder already running on the host owns the
  port.
- A port is inferred for announcement only when its listener is bound to an
  address something else can reach. `api.host` defaults to `127.0.0.1`, so the
  previous behavior published the API's port alongside the LAN address in the
  SRV record, advertising an endpoint that refuses every connection. An
  explicit `advertise.port` is still taken on trust, since an operator naming
  one may have a proxy in front of a loopback listener.
- A configured `discovery.subtype` reaches the query. It was being used only to
  suppress the SFDI lookup, after which a generic `_smartenergy._tcp` query ran
  -- so an operator asking about one function set silently got every server
  instead. The subtype goes into the PTR name, so it has to be part of the
  question rather than a filter applied to the answer.
- The second discovery round now runs when either the SRV or the TXT record is
  missing, rather than only when SRV is. A PTR answered with an SRV and no TXT
  is just as unusable as one with neither, and treating it as a rejection lost
  the server silently.
- A link-local source address is no longer accepted as a server's host. A
  responder on IPv6 commonly answers from one, and the scope identifier that
  would make it dialable does not survive the source tuple, so accepting it
  produced a URL like `https://[fe80::1]:8443` that fails at connect. This is
  the same rule already applied to a link-local AAAA record, now applied to the
  fallback as well.
- Announcement starts before discovery rather than after it. A client with no
  configured URL retries its query until a server answers, so building the
  advertiser afterwards left it invisible during exactly the local outage where
  someone would go looking for it.
- The DNS decoder only follows a compression pointer that points strictly
  backwards. A forward or self-referential pointer is the shape every
  "malformed DNS packet hangs the parser" bug takes, and bounding the iteration
  count instead would still let a crafted datagram cost far more work than it
  took to send. A multicast group is the one place on this path where bytes
  arrive from an unauthenticated source.

## [0.5.0] — 2026-08-25

- Telemetry can no longer evict captured protocol traffic from the MQTT
  forwarder under broker backpressure. One queue carried all three payload
  kinds and dropped the oldest item when full regardless of kind, so a
  measurement arriving on a timer could displace a captured exchange or an OCSF
  event -- records nothing will send again -- and the item it displaced could
  belong to an unrelated device. The forwarder now holds two buffers with the
  policies the kinds actually want: captured messages and events keep the
  bounded FIFO and its drop-oldest eviction, unchanged, while telemetry is held
  newest-per-device and never touches the capture buffer. A second frame for a
  device supersedes the first rather than queueing behind it, which is correct
  for a measurement and wrong for a capture. The publish loop drains capture
  first, in bounded runs, so sustained protocol traffic delays measurements
  rather than starving them. `MQTTForwarder` takes a `telemetry_device_limit`
  (default 1000) bounding that buffer by device count, and statistics gain
  `capture_queue_size`, `telemetry_pending`, `telemetry_superseded` and
  `telemetry_dropped` -- the counters seeded at zero, like `events_queued`, so
  the schema does not depend on what has happened to arrive. `telemetry_queued`
  joins them on `AbstractForwarder` for the same reason. `queue_size` still
  reports everything buffered and `messages_dropped` still counts only lost
  capture, so both mean what they did.
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
- The Time poll now drives off the FSA Time hrefs the server *advertises*,
  rather than the resources previously read from them. An FSA Time endpoint
  that was down or 404 during discovery left no record, so nothing scheduled
  the poll that would have retried it and nothing asked again until the next
  rediscovery. An FSA the server withdraws is now dropped from the poll and the
  timebase instead of being fetched forever.
- An error the poll-recovery path exists to act on -- a redirect, or any
  protocol error on the global Time href -- is raised even when other Time
  scopes refreshed successfully, and is selected up front rather than being
  whichever failure came last, so it no longer depends on href iteration order.
  Isolating those was the same silent stall one level up: the global href
  moves, the refresh quietly stops, and the rediscovery that repairs it never
  runs. A 404 or 204 on an *FSA's* Time href stays benign absence, the reading
  discovery already gives it.
- A per-FSA observation older than `fsa_stale_seconds` now yields to a newer
  global one. §9.2.3 specificity is worth having only while
  the FSA's Time resource is being kept current; past that it is the more
  precise way to be wrong, and it fails silently because a frozen offset is
  indistinguishable from a fresh one where it is used. Staleness is measured on
  the monotonic clock, so stepping the wall clock -- exactly what a device
  syncing its RTC from this client does -- does not age an observation. The
  threshold follows the cadence Time is actually polled at (three poll
  intervals, and never less than an hour) rather than a fixed hour, because a
  server advertising a slow `pollRate` would otherwise retire a healthy FSA
  scope between two successful refreshes; `server.fsa_stale_seconds` overrides
  it. The fallback is no longer silent: `/status` marks the bypassed `per_fsa`
  entry `"stale": true` and reports the threshold, and the client raises a
  warning naming the FSA the first time each scope is bypassed.

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
