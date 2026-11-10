# Changelog

Notable changes to this project, newest first. Versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html): while the major
version is `0`, a minor bump may carry a breaking change and the release note
below says so explicitly.

## [Unreleased]

Nothing yet.

## [1.0.1] - 2026-11-10

### Fixed

- **A fixed power factor sent as opModFixedPFInjectW now writes the excitation it asks for to model 704 (#94).**
  The SunSpec Modbus connector wrote `PFWInj.Ext` inverted: `excitation` false (inject
  reactive power) wrote 1 (under-excited), so a model 704 device absorbed reactive power when
  told to inject it, and the reverse. Both sides now write `Ext` 1 when `excitation` is true
  and 0 when it is false, as IEEE 2030.5 and SunSpec define them. opModFixedPFAbsorbW was
  already correct.

- **A poll that sends no request no longer hides a server outage (#96).**
  A poll that sent no request, such as the backstop poll of FSA lists that are all
  subscribed, counted as contact with the server and cleared the poll-failure streak, so an
  outage never reached the restart threshold: the reconnect listeners did not run and no
  rediscovery followed. A poll now counts as contact only when the server answered a request
  while it ran, read from the new `Sep2Client.contacts`.

- **The backstop poll refreshes FSA lists that are all subscribed (#97).**
  With every FSA list subscribed, the backstop poll that runs in place of the suppressed FSA
  poll skipped each subscribed list and fetched nothing, so a change whose notification the
  server failed to send was not picked up until a rediscovery. It now refreshes every FSA
  list; the regular poll still skips the subscribed ones.

## [1.0.0] - 2026-11-06

### Added

- **Observers report every exchange, connection, anomaly, device read and write,
  and audit record (#85).** `py20305.observers` defines frozen records and the
  callables that receive them, all optional and off by default:

  - `ExchangeRecord`, one per HTTP exchange: each attempt of each request,
    including one that received no response, and each notification POSTed to
    the listener. It carries the request and response bytes exactly as sent and
    received, headers with `Authorization`, `Cookie` and `Set-Cookie` redacted,
    method, URL, status, wall-clock and monotonic send and receive times, the
    attempt number, the error kind and text, the local and remote endpoints, the
    server host, the TLS version, cipher and peer certificate SHA-256 where the
    transport exposes them, the client LFDI and SFDI, the device LFDI and
    EndDevice href the exchange concerns, the resource type, and the layered
    schema validation result of each body. Body capture and validation can be
    turned off.
  - `ConnectionEvent` for connections made, failed and lost, and notification
    requests accepted or refused.
  - `ProtocolAnomaly` for bodies that cannot be used or fail validation,
    unexpected statuses, and notifications refused or dropped.
  - `DeviceRecord` for each read from and write to a device through a connector,
    through `DeviceTelemetryEmitter`, and `AuditRecord` for the control audit
    trail, through `AuditEmitter`.

  Every record carries `record_version`; fields are only added, and a removal
  follows one minor release of deprecation. An observer that raises never
  affects the exchange: `ObserverGuard` catches and counts the exception.
  `QueuedObserver` hands records to a slow observer through a bounded queue on a
  background thread, counting what it drops. Attach them with
  `CsipClient.attach_observers(Observers(...))`. See `docs/observers.md`.

- **A subscription can be renewed with a PUT to its own href (#88).**
  `subscription.renewal_method: put` or `SubscriptionManager(renewal_method="put")`
  PUTs the Subscription to its href (IEEE 2030.5 Annex A.2.7.2 lists PUT on a
  Subscription as Mandatory) instead of re-POSTing it to its list. A 404 or 405
  means the server no longer holds that href, so a new subscription is POSTed to
  the list it was created on, without a rediscovery. Each subscription now
  records that list, since a server may place the subscription outside it. The
  default stays `post`.

  **The backstop poll of subscribed resources has a configurable interval (#88).**
  `subscription.backstop_poll_seconds`, `CsipClient(heartbeat_seconds=...)` and
  `PollScheduler(heartbeat_seconds=...)` set how often a resource with an active
  subscription is still polled, for a server that stops notifying while it still
  lists the subscription. It is never faster than the resource's pollRate. The
  default stays 900 s.

  **DERCapability and DERSettings can be PUT on change only (#88).**
  `telemetry.der_resource_put_policy: on_change`,
  `TelemetryCoordinator(der_resource_put_policy=...)` or
  `DerResourceManager(put_policy="on_change")` PUTs each once, then only when it
  changes; a changed DERCapability also resends DERSettings, and a rediscovery
  does not resend an unchanged one (CSIP G29). Under the coordinator, a reconnect
  after a sustained loss of contact resends both. `CsipClient(on_reconnect=...)`
  and `CsipClient.add_reconnect_listener()` report that reconnect, before the
  rediscovery that follows, and `forget_sent()` resends both for a host that
  builds its own manager. The default stays `periodic`, which PUTs DERCapability
  every cycle.

  **A `signed_as_reported` reading profile (#88).** W, var and power factor are
  posted with the sign the connector reports (negative W while a DER absorbs, a
  battery charging), flowDirection is omitted from every ReadingType that does
  not state one, and no energy totals are given a slot. Its readings use mRIDs
  of their own (slots 71 to 77 and 80 to 97, and hashed mRIDs that cover the
  profile), so a device
  switched to it from another profile starts new reading series in the same
  MirrorUsagePoint. A cycle with no reading to post sends no readings POST (CSIP
  G27). Selected with `telemetry.reading_profile: signed_as_reported` or
  `ReadingProfile.SIGNED_AS_REPORTED`.

  **A connector can declare the readings a device provides (#88).**
  `BaseConnector.declared_readings()` returns the readings as identities (the
  `ReadingIdentity` that sets a reading's mRID, or a `Reading` standing for it);
  under `signed_as_reported` the MirrorUsagePoint registers exactly those from the
  first cycle, also one with no values, and is not re-posted when a reading
  appears. A returned reading that was not declared is not posted and is logged
  once.
  `DeviceSnapshot.declared_readings` carries the declaration, and
  `readings.declared_readings_of()` reads it from a connector defensively. Other
  profiles ignore it.

- **A server schema profile describes which IEEE 2030.5 schemas a server speaks (#89).**
  `server.schema` holds `base` (`auto`, `2018` or `2023`), `features` and
  `extensions`. A `ServerProfile` answers every edition question the client has:
  the response status table, the DERStatus connect fields, `schemaVer`,
  `modesResponded`, multiple DER objects and the control mode bitmaps. A feature
  is one entry in `py20305.server_profile.FEATURES`: the elements it borrows from
  another edition, the schema that defines them and the DERControlType2 bits it
  permits. The first is `import_export_limits`: `opModMaxLimWAbsorb` and
  `opModMaxLimWInject` for a 2018 server, with bits 7 and 8 in DERCapability
  `modesSupported2`, DERSettings `modesEnabled2` and DERControlResponse
  `modesResponded2`. Features are configured, never detected. Inbound, the client
  reads any element either edition defines and logs which borrowed elements a
  server actually sends. Outbound, it sends a 2023 element to a 2018 server only
  when an enabled feature covers it. The per-parent tables of what 2023 added are
  derived from the two bundled schemas (`py20305.schema_editions`), covering
  elements and attributes, so `UsagePointLink` is caught inside
  `MirrorUsagePoint` and `updatedTime` inside `DefaultDERControl`.

  **A connector says which additional control modes it can apply (#89).**
  DERCapability `modesSupported2` and DERControlResponse `modesResponded2` report
  DERControlType2 bits for the controls a connector implements (for example
  `update_op_mod_max_lim_w_inject` sets bit 8), limited to what the server profile
  permits. A connector that implements a method it cannot honor on the device
  defines `supported_modes2()` and returns the bitmap; the SunSpec connector
  leaves out bit 7 for a device with no charge-rate limit (model 702 `WChaRteMax`
  unimplemented or a zero `WChaRteMaxRtg`). A response reads the bits only from a
  connector that is already constructed, so it never waits on a device.
  `to_xml` masks the bitmaps of a prebuilt model to the profile as well.
  DERSettings `modesEnabled2` is sent when `fetch_configuration` returns it, never
  beyond the supported bits, and as 0 when the connector reports 0 and an
  additional mode is available, so the server learns the modes are disabled.

  **A pre-release tag is published as a pre-release (#89).** `release.yml` passes
  `--prerelease` to `gh release create` for a PEP 440 pre-release (`a`, `b`, `rc`
  or `dev`, alone or combined, as in `v1.0.0rc1.dev1`), so `v1.0.0b1` is not marked as the latest release.

### Changed

- **Validation is layered, so a body with CSIP-AUS extensions validates (#85).**
  `validate_xml` and `validate_xml_result` validated against the IEEE 2030.5-2023
  schema alone, which does not declare the CSIP-AUS extension elements, so a
  CSIP-AUS body with an extension outside a schema wildcard position (for example
  `DERCapability` or `DERSettings`) was reported invalid, including in the
  validation result of each captured exchange of that kind. Without `schema_path` they now set aside every element
  outside the IEEE 2030.5 namespace, at any depth and position, validate the rest
  against the IEEE 2030.5-2023 schema, and validate each element set aside on its
  own against its namespace's schema (`csipaus-ext.xsd` for
  `https://csipaus.org/ns` and `https://csipaus.org/ns/v1.3`,
  `csipaus-ext-v1.3-beta_storage.xsd` for its namespace). An element of a
  namespace with no known schema is an error naming the namespace and the
  element. Profile requirements, such as a mandatory CSIP-AUS element, are not
  checked. A body whose root element is outside the IEEE 2030.5 namespace is an
  error, except the CSIP-AUS `ConnectionPoint` resource. A new `schema_dir`
  argument supplies the schema files; a directory that holds `csipaus-ext.xsd`
  without the IEEE 2030.5-2018 schema it imports uses the packaged copy. Passing
  `schema_path` keeps the single-schema check.

  **The connector contract is IEEE 2030.5 throughout (#85).** Every key and value
  a connector exchanges with the client is an IEEE 2030.5 element name and value.
  This breaks every connector outside the package; the print connector is
  updated. See "Writing your own" in `docs/connectors.md`.

  - `fetch_monitoring` returns a list of `Reading` (`py20305.connectors.base` or
    `py20305.connectors.reading`) instead of a dict keyed by SunSpec point names.
    Each reading states its `uom`, `phase`, `kind`, `data_qualifier`,
    `accumulation_behaviour`, optional `flow_direction`, `multiplier` and
    `quality_flags`. `ACType`, the `"<key>__quality"` keys, `ReadingOverride` and
    `BaseConnector.reading_overrides()` are removed: each reading carries what
    they set.
  - A reading's MirrorMeterReading mRID is derived from its ReadingType. Readings
    that 0.16 registered keep their mRIDs through a fixed table of 0.16 slots, so
    a server holding a 0.16 registration keeps receiving them; any other
    ReadingType gets an mRID hashed from it. A reading 0.16 registered with a
    `ReadingOverride` that changed its ReadingType gets a new mRID, since its
    ReadingType is not the one 0.16 registered for that slot.
  - A MirrorUsagePoint registers the readings the connector supplies, not a fixed
    set of quantities. When a reading first appears after registration, the
    client posts the MirrorUsagePoint again under both reading profiles, with
    every registered ReadingType unchanged, before posting readings for it.
    Readings are posted only for registered mRIDs.
  - `fetch_nameplate` and `fetch_configuration` use DERCapability and DERSettings
    element names (`rtgMaxW`, `rtgMaxVA`, `modesSupported`, `setMaxW`,
    `setGradW`, `modesEnabled`, `doeModesSupported`, `doeModesEnabled`, ...).
    `WMaxOvrExt` and `WMaxUndExt` are removed: no DERSettings element carries
    them.
  - Controls arrive through one method per IEEE 2030.5 element,
    `update_<element in snake case>` (`update_op_mod_volt_var`,
    `update_op_mod_max_lim_w`, `update_op_mod_fixed_pf_inject_w`,
    `update_op_mod_exp_lim_w`, `update_set_grad_w`, ...), with
    `{"enabled": ..., "value": ..., "rampTms": ...}` where `value` is the
    element's value in IEEE 2030.5 terms. The old methods (`update_qv`,
    `update_p_lim`, `update_const_pf`, `update_fixed_w`, `update_connect`,
    `update_es_permit_service`, `update_pricing_mode`, ...) and their SunSpec
    parameter names are removed. `opModConnect` and `opModEnergize` now arrive
    separately, and the may-trip curves and `opModWattPF` reach a connector for
    the first time. A clear or an event end sends `{"enabled": False}`, and an
    event end now withdraws every element the ended event set except connect,
    energize and the permits, where it used to withdraw only the elements the
    old method table covered.
    `apply_operation` takes the new control names (`op_mod_max_lim_w`).
  - A command observer, `WriteHistory`, the audit trail and device records now
    receive the new vocabulary. `CommandObserver.record_command`, `WriteHistory`,
    `AuditRecord` and `DeviceRecord` carry `control="op_mod_max_lim_w"` with
    `{"enabled", "value", "rampTms"}` where they carried `p_lim` with
    `{"p_lim_mode_enable", "p_lim_w"}`. The same holds for every control: the
    name is the method without `update_` (`op_mod_volt_var`, `op_mod_fixed_w`,
    ...) and the values are the connector contract's. Code that filters or
    stores records by the old names must change.
  - A connector class that defines an `update_*` method the client no longer
    calls (for example one written for the old method table) is logged once at
    WARNING, naming each such method. The controls for the elements it does not
    define are not applied.
  - A control element marked `disabled="true"` now withdraws its mode
    (`{"enabled": False}`) instead of being skipped. IEEE 2030.5-2023 says a
    disabled DERControl mode follows the rules as if it were not disabled, so it
    takes part in event precedence, and the curve or setpoint of a superseded
    event no longer stays on the device.
  - The DefaultDERControl settings (`update_set_grad_w`, `update_set_soft_grad_w`
    and the enter-service settings) update the setting and are never withdrawn.
  - `modesSupported` and `modesEnabled` must be an `int` from 0 to 4294967295.
    Any other value is logged as a warning and reported as `00000000`
    (`modesSupported`) or left out (`modesEnabled`).
  - `rtgMaxVarNeg` and `setMaxVarNeg` are negative in IEEE 2030.5. The print
    connector and the SunSpec adapter now send them negative; they sent the
    magnitude.
  - `fetch_monitoring` readings that differ only in multiplier are two readings:
    the snapshot keeps both, the second keyed by its `reading_key` plus
    `:m<multiplier>`.
  - `PointEntry` holds the `Reading` it was acquired as, and `DeviceSnapshot`
    entries are keyed by `reading_key`. `DeviceSnapshot.reading_overrides`,
    `split_quality`, `typed_overrides` and `QUALITY_SUFFIX` are removed.
  - In `py20305.telemetry.mup`, `create_mup` and `create_meter_reading_list` take
    the readings and a `registered` map from `plan_registration`;
    `registration_slots`, `ac_type_of`, `READING_TYPE_SPECS`,
    `PHASED_SLOT_BY_SYSTEM_SLOT` and the override arguments are removed.

- **The SunSpec Modbus connector stays, as an adapter on the IEEE 2030.5 connector
  contract (#87).** It moves to `py20305.connectors.sunspec` (`SunSpecConnector`,
  `SunSpecDeviceConfig`) and keeps everything SunSpec inside that package. For its
  users:

  - The `sunspec` device type, `SunSpecDeviceConfig` with its transports and the
    meter-only `power_sign`, the meter reader and its power-sign calibration, and
    the `sunspec` extra are unchanged. `all` and the container image include the
    extra.
  - `fetch_monitoring` returns readings. The system voltage carries the wiring's
    line-to-line PhaseCode and the system current its total PhaseCode, so under
    the default reading profile both register under new mRIDs; every other
    reading keeps the mRID 0.16 registered for it. The line-to-line voltages and
    the state of energy are posted under the default profile too.
  - Ratings and settings use the `rtg*` and `set*` names, with `rtgMaxA` from the
    model 702 current rating. Status uses the DERStatus codes: `inverterStatus`
    is the model 701 state plus one, `operationalModeStatus` is off (1) or
    operational (2), `connectStatus` and `genConnectStatus` are the 2023 and
    2018 bitmaps, and only the alarms IEEE 2030.5 names reach `alarmStatus`.
  - Controls arrive as `update_<element>` calls. `opModConnect` and
    `opModEnergize` are now applied, through the model 703 enter-service permit.
  - `ConnectorSunSpec` and `py20305.connectors.sunspec_core` are gone. The Modbus
    core, `SunSpecModbusConnector`, with its SunSpec-named points and `update_*`
    methods, is now `py20305.connectors.sunspec.SunSpecModbusConnector`, for an
    application that keeps its own conversion. `py20305.connectors.translation`
    and the points list are removed.
  - `ConnectorConfigRegistry` takes a `reading_profile` and passes it to the
    connector.

- **Two defaults change with the server schema profile (#89).**
  A server whose DeviceCapability has no `schemaVer`, or one below 2.2, and a
  default configuration now get 2018-shaped messages. Before, the default was
  2023-shaped messages unless `server_2018_compat` was set. A server that
  implements 2023 but omits `schemaVer` needs `server.schema.base: 2023`. The
  decision and its evidence are logged at INFO. Until that decision is made, an
  `auto` profile acts as 2018 as well, so a host that sends before discovery
  sends the same shape. The synthetic URI for a created
  subscription whose response has no Location header is now its own option,
  `server.missing_location_fallback`, and is off unless it or the deprecated
  `server_2018_compat: true` is set; both editions require the header.

  **The internal functions take `profile=` in place of `server_2018_compat` (#89).**
  `to_xml`, `build_der_status`, `create_log_event_xml`,
  `translate_code_for_revision` and `compat_hint_suffix` take a `ServerProfile`.
  `to_xml` leaves 2023 elements and attributes off the model before it
  serializes, instead of removing `schemaVer` and `subscribable` from the root tag
  afterwards, and it does so for every element the profile does not permit, not
  only those two attributes. `Sep2Client`, `CsipClient` and `SubscriptionManager`
  keep `server_2018_compat` as a deprecated keyword (see the deprecation entry).

### Deprecated

- **`server_2018_compat` is deprecated (#89).**
  In the configuration (`server.server_2018_compat`) and as a keyword of
  `Sep2Client`, `CsipClient` and `SubscriptionManager`, `true` means base 2018
  plus the missing-Location fallback (`server.missing_location_fallback`, or
  `missing_location_fallback=True`), and `false` means base 2023. Setting it
  raises a `DeprecationWarning`; the configuration also logs a warning that names
  the replacement. Passing it to a constructor together with `profile` is a
  `TypeError`, and an explicit `server.schema.base` wins over it in the
  configuration. It is kept for one release line.

### Removed

- **The forwarders package, the MQTT transport and the `forwarders` configuration
  section are removed (#85).** py20305 no longer publishes traffic itself; it
  hands records to observers (see the `added` entry), and an embedding
  application publishes them on whatever transport it uses. Removed:

  - `py20305.forwarders`: `ForwarderManager`, `MQTTForwarder`,
    `MQTTForwarderAdapter`, the `ProtocolMessage` envelope and its types, the
    OCSF Network Activity builder, the connection-telemetry emitter, LFDI
    extraction, and the forwarder configuration models;
  - the `forwarders` section of the client configuration, which is now refused
    with a message naming the replacement, and the runner's forwarder wiring;
  - the `mqtt` extra (aiomqtt), which `all` and the container image no longer
    include;
  - `py20305.client.observer.ConnectionObserver` and the `connection_observer`
    argument and property of `Sep2Client`, and the `forwarder` property;
  - `NotificationServer.connection_observer`.

  A configuration file that used `forwarders` now fails to start. The runner
  attaches no observers, so a deployment that published traffic from the
  configuration file has to embed the client in an application instead: build
  `CsipClient`, call `attach_observers(Observers(...))` with callables that publish
  the records, and run it. See `docs/observers.md`.

  `py20305.forwarders.audit` moves to `py20305.audit`, and its `AuditEmitter`
  takes an observer instead of a forwarder.

### Fixed

- **DERCapability carries `rtgMaxA` (#83).** A connector's nameplate may report
  `rtgMaxA`, the maximum continuous AC current in amperes RMS, as a number or a
  value and multiplier. It is sent as DERCapability `rtgMaxA`, and omitted when
  absent.

- **A phased system voltage or current is registered under its own mRID (#84).**
  A reading's mRID now follows its whole ReadingType, so a system voltage or
  current that carries a phase code is a different reading from the unphased
  one and is registered under a different mRID. IEEE 2030.5-2023 §10.11.3 Rule n
  forbids changing a reading's ReadingType once created, and a server holding the
  unphased reading keeps it until it removes it.

- **DERStatus values are checked against their IEEE 2030.5 types (#85).**
  `build_der_status` leaves out, with a warning, an `inverterStatus` outside 0 to
  10, an `operationalModeStatus` outside 0 to 3, a `localControlModeStatus`
  outside 0 to 1, a `storageModeStatus` outside 0 to 2, a `stateOfChargeStatus`
  outside 0 to 10000, a `connectStatus` with bits other than 0 and 1, a
  `genConnectStatus` or `storConnectStatus` with bits above 4, and an
  `alarmStatus` with bits above 10, instead of sending a code the server would
  take as fact. Each edition sends only the connect field the connector supplied for it:
  `connectStatus` to a 2023 server, which never receives `genConnectStatus` or
  `storConnectStatus` (that edition forbids them), and `genConnectStatus` and
  `storConnectStatus` to a 2018 server, which never receives `connectStatus`. The
  bitmaps are not converted into each other, so a field the connector does not
  supply is not sent. This is a behavior change: the earlier release sent
  `connect_data or gen_connect_data`, one value, to both editions. A server now
  receives only its own edition's field. Every payload sent to a 2023
  server carries `schemaVer="2.2"`. The print connector reports a running device
  (`inverterStatus` 4, `operationalModeStatus` 2).

  **A poll that loses lines 2 and 3 no longer moves a multi-phase device's
  readings (#85).** Under the signed-load convention the reading profile now
  decides the wiring from something stable: a device is multi-phase once any
  poll showed a line 2 or line 3 reading, a phase total or a line-to-line
  voltage, or once a line 2, line 3 or multi-phase total reading is registered.
  A later poll with only line 1 posts it under line 1's mRID, never under the
  system total's. When two readings of one poll resolve to the same mRID, the
  reading dropped is logged instead of silently skipped. The SunSpec adapter
  keeps the last `ACType` it read, so a read that omits it keeps the wiring.

  **An exchange that fails after its status arrived is recorded as failed (#85).**
  A body cut short or a connection reset after the headers produces an
  `ExchangeRecord` with `status` and also `error_kind` and `error`, and a `lost`
  `ConnectionEvent`. It was recorded as a success. Protocol-level errors
  (an error status, a redirect, a rate limit, an unusable body) still leave
  `error_kind` empty.

  **Net readings keep their sign under the signed-load convention (#85).** A
  connector reading of real or reactive power with flowDirection 4 (Net) is signed
  by the role like any other power reading instead of being posted as a
  magnitude.

  **A hashed MirrorMeterReading mRID depends on the reading profile (#85).** Each
  profile derives a different ReadingType from the same reading, and a
  ReadingType never changes under an mRID.

  **Exchanges under a discovered EndDevice name its LFDI (#85).** The client
  registers each discovered EndDevice href with its LFDI after discovery and
  rediscovery, so `device_lfdi` and `end_device_href` are set on those records
  without the embedder registering prefixes.

  **`QueuedObserver.close(timeout=...)` returns within the timeout (#85).** It
  blocked forever when the queue was full and the target stuck. A record offered
  while the observer closes is queued or counted in `dropped`, and the records
  left queued when `close` gives up are counted too.

  **The SunSpec adapter says it does not apply `rampTms` (#85).** The ramp time is
  ignored, with one INFO line per device, instead of being passed to a core that
  never read it.

  **The print connector supplies the 2018 connect status too (#85).** It reports
  `genConnectStatus` beside `connectStatus` (connected), so a 2018 server receives a
  connect status from it.

  **Registered readings survive a restart (#85).** IEEE 2030.5-2023 section
  10.10.3 rule n) says that once created, the ReadingType of a MirrorMeterReading
  SHALL NOT be modified. Under the default reading profile a power reading's
  flowDirection comes from the sign of its value when it is registered, so a client
  that restarted while a battery was charging registered the same mRID as Reverse
  (19) where it had registered Forward (1). `TelemetryManager`
  and `TelemetryCoordinator` now have `to_checkpoint()` and
  `restore_from_checkpoint()` for the registered readings of every device, with a
  version field. Restore ignores, with a warning, data of another version or shape,
  data written under another reading profile or power direction, and any device or
  reading it cannot parse. `set_on_registration_change()` calls back after the set of
  registered readings changes. In the runner, the new `telemetry.state_file` setting
  (and `--telemetry-state-file`) names a file that is loaded at start, written
  atomically after each registration change and at shutdown, and shown by `--check`.
  It is off when unset. With no saved registration for a device (an upgrade from
  0.16, a lost file, or the setting unset) the readings register from the current sign
  as before, and the client logs one warning per device saying so. A registration
  made by 0.16 cannot be recovered this way: the first start with a state file records
  whatever the current sign gives.

- **MirrorUsagePoint exchanges name the mirrored device (#86).** A
  MirrorUsagePoint is POSTed to the MirrorUsagePointList and its readings to the
  location the server returns, and neither path names an EndDevice, so every
  device's meter readings were attributed to the client certificate's LFDI. A
  MirrorUsagePoint whose `deviceLFDI` names a device registered with
  `update_device_lfdi_prefixes` is now attributed to that device, and so is every
  readings POST to its location, including after rediscovery; the exchange
  record carries it as `device_lfdi` and `end_device_href`. A `deviceLFDI` that
  names no registered device names no device.

- **Supersession compares only events whose programs reach a common device (#92).**
  `compute_supersession` compared every pair of active and scheduled events and
  worked out both events' compared modes again for each pair, so its cost grew
  with the square of the events held, across every program. It runs after each
  control fetch, so a client relaying many programs, each reaching its own
  devices, spent its event loop there and answered notifications late. A pair
  can only supersede when its programs reach a common device, so only those
  pairs are compared now, and each event's modes are worked out once. The
  results, and the order they are returned in, are unchanged.

## [0.16.2] - 2026-10-29

### Fixed

- **CSIP-AUS extensions are written in the namespace the server declares (#80).**
  `doeModesSupported`, `doeModesEnabled` and `ConnectionPoint` were always written
  in `https://csipaus.org/ns`. CSIP-AUS 1.3 (`csipaus-ext-v1.3.xsd`) moved them to
  `https://csipaus.org/ns/v1.3`, so a 1.3 server refused the DERCapability PUT or
  dropped the extension. Discovery now records the CSIP-AUS namespace the
  DeviceCapability declares, and `Sep2Client.csipaus_namespace` holds it. A server
  that declares the pre-1.3 namespace, or none, receives the same bodies as before.

  **A CSIP-AUS 1.3 TimeTariffIntervalList parses (#80).** CSIP-AUS 1.3
  (`csipaus-core-v1.3.xsd`) adds a mandatory `RateComponentLink` to each
  TimeTariffInterval. The model had no such field, so the list failed to parse and
  the client found no tariff intervals. Discovery now parses tariff lists with
  `CsipausTimeTariffIntervalList` (`py20305.models.csipaus.tariff`), whose intervals
  have an optional `rate_component_link`, left empty for servers that do not send
  it.

- **An href is requested exactly as the server wrote it, percent-escapes included (#81).** aiohttp re-quotes a URL given as a string, which decoded escapes such as `%3B` and `%3A` in a server's href to `;` and `:` before the request was sent. A server that reads `;` as the start of a path parameter then stripped the rest of the segment and answered 404 for a resource it had listed itself, while Live Traffic showed the href as written. Every request now sends the href's escapes unchanged, and only characters that cannot appear in a URI at all, or a `%` that does not start an escape, are encoded.

## [0.16.1] - 2026-10-23

### Fixed

- **`ResponseCode` numbers the superseded and resumed statuses as IEEE 2030.5
  does (#78).** `SUPERSEDED_ALTERNATE_SERVER`, `SUPERSEDED_ALTERNATE_PROGRAM`
  and `RESUMED` were 12, 13 and 14, one below the standard's 13, 14 and 15
  (IEEE 2030.5-2018 Table 27 and -2023 Table 31; 15 is defined only in 2023,
  and 2018 reserves 15 to 251). 12 is "Cannot be displayed", a messaging
  status. Only the constants are corrected: this client emits none of the
  three, so nothing it posts to a server changes.
- **A site meter's real and reactive power carry the right sign under the signed
  load convention profile (#78).** `signed_load_convention` negated W and var for
  every device, because a DER's connector reports them positive for its output.
  A site meter reports them import positive, from the grid to the premises, which
  is already the load convention, so its import was posted as export and its
  export as import, and the power factor took the same wrong sign. A site meter's
  W and var are now posted as the meter reports them: import positive, export
  negative. A DER and a DER meter are unchanged. Under the `default` profile,
  `power_direction: net` and `split` already posted a site meter's import as
  positive Net and as Forward; tests now pin that. `create_meter_reading_list`
  takes the device's `role`, which `TelemetryManager` passes; a caller that builds
  readings for a site meter itself must pass `role=DeviceRole.SITE_METER`.

## [0.16.0] - 2026-10-21

### Added

- **A battery's real and reactive power can be mirrored with its direction kept (#75).**
  Under the default reading profile, a MirrorMeterReading's flowDirection was set
  once, from the sign when the MirrorUsagePoint was created, and later readings
  posted the magnitude. IEEE 2030.5 does not let a ReadingType change, so a
  battery that registered while discharging and later charged was still reported
  as delivering power. `telemetry.power_direction` (and `PowerDirection` on
  `TelemetryManager` and `TelemetryCoordinator`) now chooses how W and var carry
  their direction: `default` keeps the historical encoding, `net` posts
  flowDirection 4 with the sign on the value, and `split` posts a Forward (1) and
  a Reverse (19) reading for each quantity, each a magnitude. `net` and `split`
  use new mRIDs, so switching modes never posts a value under a ReadingType
  registered for another mode. The option applies to total and per-line W and var
  under the default profile, and is refused with `signed_load_convention`.

- **A SunSpec meter can be configured as the site meter or as a DER's meter (#76).**
  Each device now has a `role`. `der`, the default, changes nothing. `site_meter`
  is the meter at the premises point of delivery: its MirrorUsagePoint carries
  roleFlags 0x03 (isMirror, isPremisesAggregationPoint), the value CSIP-AUS v1.1a
  Annex A lists for the site readings, and carries the client's own EndDevice LFDI
  in deviceLFDI unless `mirror_under: own` says to use the meter's `lfdi`. `der_meter`
  is a meter on one DER's circuit, mirrored with roleFlags 0x49 under its own
  `lfdi`. `revenue_quality: true` adds isRevenueQuality (0x10). A meter's mRIDs
  always derive from its own `lfdi`, so a site meter under the site's EndDevice
  never collides with the DER mirrored under the same LFDI. A meter takes no
  controls and gets no DER resources: no DERCapability, DERSettings, DERStatus or
  DERAvailability, and no LogEvents. A control addressed to a meter is answered
  with status 252 (Not Applicable), and a control fanned out across several local
  devices skips the meters. The SunSpec connector reads models 201-204, 211-214
  and 220 for a meter, reports real and reactive power import positive for a site
  meter and output positive for a DER meter, and adds two energy totals in Wh
  (accumulationBehaviour 9, flowDirection 1 and 19) in new mRID slots 61 and 62.
  The meter models do not say which sign of `W` is import, so `power_sign: auto`
  learns it from which of `TotWhImp` and `TotWhExp` rises and posts no real or
  reactive power until it has; `import_positive` and `export_positive` set it. A
  device configured as a meter that exposes no meter model fails at scan naming
  the models it has, and a DER that exposes only meter models is warned about
  once. The operations history keeps site meter readings in a series of their
  own, `site_intervals`, which `GET /api/v1/history/intervals` and
  `/api/v1/history/baseline` return with `series=site`; responses without the
  parameter are unchanged apart from naming their series.

## [0.15.0] - 2026-10-20

### Fixed

- **The MQTT forwarder reconnects after the broker closes the connection, instead of failing every later publish until the process restarts (#71).**
  The forwarder opened one broker connection at start and never reconnected. When
  the broker dropped it, every publish failed, first with "The client is not
  currently connected." and then with "Message queue full.", and the forwarder
  kept reporting itself connected, because it marked the link down only when an
  error message contained the word "connection". Connection events released into
  that dead client were lost. Now any MQTT client error or socket error from a
  publish, or the client's own report that its socket closed, marks the link
  down. The forwarder stops publishing, keeps buffered items in its existing
  bounded queues, and reconnects with exponential backoff and jitter
  (`reconnect_initial_delay_seconds`, default 1, doubling up to
  `reconnect_max_delay_seconds`, default 60), reusing its client ID and loading
  its TLS files again on each attempt. The backoff resets only once a new
  connection is stable, meaning a publish succeeded on it or it stayed up for
  `reconnect_max_delay_seconds`, so a broker that accepts the connection and
  drops it at once is retried at the growing delay. The message whose publish
  failed is sent first after reconnecting. A message whose publish loses the
  connection three times in a row is dropped, counted as a publish error and
  logged with its topic, so a message the broker answers by closing the
  connection cannot hold back the queue.
  `connected`, `can_deliver` and `can_deliver_events` stay false while the link
  is down, so held connection events wait and go out after the reconnect. A loss
  logs one warning, an outage logs a reminder and sends a connection failure
  event at most every five minutes, and a reconnect logs one info line with the
  downtime and attempt count. A connection that keeps dropping right after it is
  made is reported at the same five-minute rate. A broker that refuses the
  connection for bad credentials or missing authorization is logged as an error.
  Stopping the forwarder during a connect attempt closes a connection that
  completes afterwards. The forwarder's statistics add `disconnects`,
  `reconnects`, `last_disconnect_reason` and `reconnect_backoff_seconds`. A full
  queue now logs its warning once until the queue drains, rather than once per
  dropped item. **Behavior change:** when the first broker connection fails,
  `MQTTForwarder.start()` now reports the failure and returns with the forwarder
  running and retrying in the background, so `ForwarderManager` no longer lists
  it in `failed_forwarders()` and items queued during the outage are kept rather
  than dropped. Set `require_initial_connection: true` to keep the old behavior,
  in which `start()` raises and `forwarders.retry_interval_seconds` governs the
  retry. A missing aiomqtt or TLS files that do not load still make `start()`
  raise.

- **The operations history is safe to read while readings arrive, and its file is readable by its owner only (#72).**
  The interval part kept its unfinished intervals in memory with no lock, so a
  query on a management API thread could fail with "dictionary changed size during
  iteration" while the event loop recorded a reading, and an interval that ended
  between a query's database read and its look at memory could be missed. The
  running sums are now guarded by a lock held only for in-memory work. Each write
  batch is numbered, each unfinished interval records the batch that stored it,
  and a query adds the copy from memory only when its database read does not
  include that batch, so an interval ending during a query is counted exactly
  once. The file format is unchanged. A read now waits only for the writes queued
  before it, so a steady stream of writes cannot hold a read off, and for at
  most 10 s: past that it reads without them and logs a warning, so a writer that
  stops making progress cannot hang every read. A new history
  file is created 0600, exclusively, so a file that appears at the path first is
  tightened rather than opened with its own mode, and its directory 0700 when the history creates it. On
  POSIX an existing file loses its group and other permissions and keeps its
  owner's, so a read-only file stays read-only. SQLite gives its journal files the
  database file's mode.

- **Discovery reads every page of the DER lists, and reports a list that fails between pages as an error (#73).** A list GET without `l` returns one entry (IEEE 2030.5 GEN.019), and discovery read the DERList with a plain GET, so a server that lists more than one DER, as a 2018 server may, had its later DERs missed. Discovery now pages the DERList with `s` and `l`, and the SubscriptionList read for its `pollRate` asks for one entry explicitly. A 404 or 204 that arrives after a list's first page means the server stopped serving a list it had begun; discovery and refresh logged it as a missing list, and now log it as an error.

## [0.14.0] - 2026-10-20

### Added

- **The client keeps an operations history on disk: event lifecycles and responses, device writes, and measured intervals (#68).**
  One SQLite file (`history.path`, default `py20305-history.sqlite3` in the
  working directory) with three parts, each on by default and each switched off
  on its own under `history.events`, `history.writes` and `history.intervals`;
  `history.enabled: false` keeps no history and writes no file. Events keep every
  audit-trail lifecycle record whether or not the trail is published, the
  control's description and settings, and every DERControlResponse posted or
  failed. Writes keep each device write, including failed and gate-refused ones,
  with the mRID of the control and of the event that caused it. Intervals
  integrate telemetry readings into 15-minute intervals with energy by sign,
  coverage, and curtailed energy where the device reports available power, and
  compute comparison-day baselines that leave out event days. Rows are kept for
  `history.retention_days` (400). New read endpoints under `/api/v1/history`.
  **Upgrading:** the history is on by default, so an upgraded client starts
  writing the file in its working directory. In a container, mount the
  `py20305` home on a volume to keep it, or set `history.enabled: false`. A file
  that cannot be opened is logged and the client runs without a history.
  Published audit records are unchanged.

### Fixed

- **A server-issued control that the command gate refuses is answered with status 252 (Not Applicable), not status 2 (Active) (#69).** When a `CommandGate` refused the IEEE 2030.5 origin for a device, the dispatcher recorded the refusal and returned, and the event processor then told the server the event had started on a device that was never written. `ConnectorDispatcher.apply_control` and `apply_control_by_lfdi` now raise `CommandNotPermittedError` after offering every mode to the gate, so each refused write is still recorded. The event processor reports the device as rejected and leaves it out of the event's Completed response (IEEE 2030.5-2023 Table 31). Table 31 has no code for a refusal by local configuration; 252 is the code for a control the device is not set up to take, as for `DeviceNotConfiguredError`. This is a breaking change for a caller of `apply_control` or `apply_control_by_lfdi` outside the event processor, which now has to handle the exception. A refused default control or clear is still reported and dropped, since neither sends a response.

## [0.13.0] - 2026-10-16

### Added

- **The SunSpec connector enforces the CSIP-AUS load limit, opModLoadLimW, on
  storage devices (#65).** It was accepted and silently ignored. The limit is the
  maximum charge rate, written in watts to model 702 `WChaRteMax`; when
  `opModMaxLimWAbsorb` is active too, the lower of the two is written. A limit
  above `WChaRteMaxRtg` is written as the rating, for the absorb limit as well,
  instead of being refused. A device that does not declare charge-rate limiting
  reports the control as not supported (251).

- **Connection events now say which link they describe, who produced them, and can be logged one per attempt (#67).** Every connection event carries one role label in `metadata.labels` -- `role:northbound`, `role:southbound`, `role:management`, `role:server` or `role:telemetry` -- alongside the direction already in `connection_info.direction_id`, so a consumer can separate the IEEE 2030.5 link from device, console, listener and broker connections without guessing from ports.

  `ConnectionTelemetryEmitter` takes `product_name` and `vendor_name`, so an application embedding the library is named as the producer in `metadata.product` instead of `py20305`; the default is unchanged. Its new `record_connection` and `record_connection_failure` methods report any other link through the same topic, coalescing successes per link (role, direction, service and destination, plus the source address for inbound connections) and never coalescing failures.

  `forwarders.connection_telemetry.coalesce_successes` (default `true`) can be set to `false` where every connection attempt must be logged as its own record. The runner now also reports the forwarder's own broker connection (`role:telemetry`) and each request to the notification listener (`role:server`). `tests/fixtures/connection_events.json` holds one emitted event per role for testing a consumer's parser.

### Changed

- **`doeModesSupported` and `doeModesEnabled` include the load limit (bit 3) on a
  SunSpec device that declares charge-rate limiting (#65).** A storage inverter
  with `WMaxLimPct` reports `0C`; a PV inverter still reports `04`.

- **Breaking: a coalescing `Window` carries `src` and `dst` endpoints instead of `socket` (#67).** `CoalescingWindow.record(now_ms, src, dst)` takes the two `Endpoint`s in place of a `SocketPair`, because a window now describes any link, not only the IEEE 2030.5 client's socket. Code that builds `Window` or calls `CoalescingWindow.record` directly passes `src=`/`dst=`; the emitter's own entry points are unchanged.

- **Connection events produced while the broker is unreachable are held and sent when it recovers, instead of being dropped (#67).** This is what lets a failed broker connection be reported at all: its record is delivered once the connection succeeds, with the time it actually failed. The hold keeps up to 1,000 events and drops the oldest beyond that. Held events go out with the next event, or when the host calls `ConnectionTelemetryEmitter.flush_expired()` or `release_held()`.

### Fixed

- **A cleared charge-rate limit no longer stays on a SunSpec device (#65).** When
  no charge limit (`opModLoadLimW` or `opModMaxLimWAbsorb`) remains, the connector
  writes `WChaRteMaxRtg` to `WChaRteMax`, as IEEE 2030.5-2018 10.10.4.2, CSIP
  4.4.2 and CSIP-AUS 4.4 require of a mode the DefaultDERControl does not carry.
  It does so on every clear, so a clear after a connector restart still releases
  the battery. Before, the absorb limit left its value in force after the event.
  The discharge-rate setting `opModMaxLimWInject` writes is not released yet (#66).

## [0.12.0] - 2026-10-12

### Added

- **The SunSpec connector enforces the CSIP-AUS generation limit, opModGenLimW (#61).**
  It was accepted and silently ignored. The limit is converted to a percent of WMax
  and merged into model 704 `WMaxLimPct` with opModMaxLimW, the more restrictive
  value winning, and it is re-expressed whenever an inject limit moves WMax so the
  watts it allows stay fixed. A device without `WMaxLimPct`, or without WMax and
  WMaxRtg, reports the control as not supported (251).

### Fixed

- **A superseded event no longer leaves its modes on the device when it ends
  (#54).** A device goes into an event's superseded set when another event takes
  over any one of its modes there, and the end-of-event pass left out every such
  device, which conflated two things. Nothing was unregistered, so the tracker
  went on believing the ended event owned those modes and the event that took
  them over released nothing when it ended. And nothing was released, so on a
  device taken over for only some of its modes the rest were never disabled
  either. The event's registration is now removed for every device it targeted,
  and the device is passed over only while another event is still running on it,
  so whichever of the two ends last releases it. The fallback write is still
  withheld for a device another event took over entirely, where the superseding
  event's own activation is the write that moves it. What counts as "entirely" is
  measured in the same terms the supersession recorded, which leave out connect
  and energize and do not reach the CSIP-AUS limits -- measuring it against every
  mode an event sets would read a device taken over whole as only partly taken
  over, and for an event carrying nothing but a CSIP-AUS limit it would do so
  every time.

- **A DefaultDERControl is no longer written over a mode another event is still
  running (#57).** Two events can hold the same device for different modes
  without either superseding the other. When one ended, the default that came
  back was written whole, so a mode the default named and the other event was
  running was taken from the event that owned it. The default now goes out
  naming only the modes nothing is controlling: the one still being run is left
  to the event running it, and the rest are set as the program says. Where the
  default names nothing but modes another event is running there is nothing to
  send, and the modes the ending event released are disabled instead so none is
  left on with no event behind it. A default sent without some of its modes is
  not recorded as the program's default being in place, so the whole of it is
  written when the other event ends. Under a group lookup the default reaches
  every local device of its program, so what counts as being controlled is read
  across the program's devices rather than the one the default was chosen for.

- **A DefaultDERControl no longer reaches past the devices it was decided for
  (#58).** Eligibility for a program's initial default is settled one
  server-side device at a time, and a device with an active event is not a
  target. Under a group lookup the write is not sent that way: each target fans
  out to every local device of the program, so a device correctly left out as a
  target was written to anyway as part of another target's fan-out. What is
  being controlled is now read from everything the write will touch -- the local
  devices it fans out to, and the events of any program that resolves to one of
  them, which need not be a program this one names -- and the default goes out
  without those modes, or not at all when it names nothing else. The
  end-of-event fallback reads the same way, from the program whose default was
  chosen rather than from the event that ended, since those can differ and it is
  the first that decides where the write lands. What counts as being controlled is read from the devices the write
  actually lands on: without a group lookup an event registered on a program's
  other devices is not controlling this one, which it never was where the event
  was refused there.

- **The SunSpec connector reports `doeModesSupported` and `doeModesEnabled` as
  opModGenLimW only (`0x04`), or as no limits on a device without model 704
  `WMaxLimPct` (#61).** It reported opModExpLimW (`0x01`), a limit it never
  enforced.

## [0.11.0] - 2026-10-12

### Added

- **A control audit trail, behind one forwarder switch (#46).** With
  `forwarders.audit.enabled`, each device write carries `origin`, `applied_mrid`
  and `cause_mrid` in `protocol_data.extra`, linking it to the event that produced
  it, and a rejected write carries `error_type`. Event lifecycle transitions
  (scheduled, active, completed, cancelled, superseded, opted out, expired,
  skipped), the outcome of a dispatch that missed its deadline, and
  loss-of-communications entry, recovery and exit are published, in order, as
  flat JSON records on `out/der-events`. A write the command gate refused is
  recorded with `error_type: refused`. Every audit record carries the client's
  `boot_id` and `seq`, so a lost record shows as a gap. The switch publishes writes without device
  reads; `device_telemetry` keeps publishing reads and writes as before, now with
  the cause fields. Events dropped while the forwarder is stopped, or by a
  forwarder whose broker was unreachable at start, are counted in
  `events_dropped_not_running` and `events_dropped_by_forwarder`. See [Forwarding](docs/forwarding.md).

### Changed

- **`ControlDispatcher` takes a `cause_mrid` keyword on its default-control and
  clear methods (#46).** `apply_default_control`, `apply_default_control_by_lfdi`,
  `clear_control` and `clear_control_by_lfdi` gain `cause_mrid: bytes | None =
  None`, the event whose end or opt-out led to the write, and the event processor
  passes it. A dispatcher of your own that implements the protocol must accept the
  keyword, or those calls raise `TypeError`.

  **Device telemetry records a refused write, and the forwarder manager skips a
  forwarder whose start failed (#46).** With `device_telemetry` on, a write the
  command gate refused now appears as a write record with `is_valid: false` and
  `error_type: refused`, where before it was absent. `ForwarderManager` no longer
  hands messages, telemetry or events to a forwarder whose start raised and has
  not since been retried successfully; events it skips are counted in
  `events_dropped_by_forwarder`.

### Fixed

- **An ended event's modes that the default does not carry are now disabled
  (#51).** When an event completed or was cancelled, the processor applied the
  DefaultDERControl, which writes only the modes it names, so a mode the event
  had set and the default did not mention (a fixed setpoint, an export limit)
  stayed in effect on the connector and the device. The processor now disables
  those modes through the dispatcher's new `disable_modes` /
  `disable_modes_by_lfdi`, with the origin `CommandOrigin.EVENT_END`. Under a
  group lookup a mode is left alone while another event still holds it on any of
  the program's devices, or a restored default carries it. With no default on
  any program the processor disables everything the event released instead of
  taking no action. The CSIP-AUS limits are tracked by element name so they are
  released too. `ControlDispatcher` gains the two methods; a dispatcher of your
  own needs them.
- **A CSIP-AUS limit is translated whichever module loaded first (#50).** The
  parser types an extension element (`opModExpLimW` and the other limits) only
  when its class is already imported, and left it a generic element otherwise,
  which the translation did not read: a control received before anything had
  imported the CSIP-AUS models activated with no modes. `py20305.xml` now
  imports the models, and the translation, the active-mode tracking and the
  JSON form read a typed model and a generic element alike through
  `models.csipaus.elements`.

- **A DefaultDERControl is no longer written to a device twice after an event
  ends (#47).** The fallback recorded the ended event's mRID where the default's own
  belonged, so the next poll's initial-default pass took the default for a new
  one and wrote it again. A default the server actually changes is still applied.

## [0.10.0] - 2026-10-06

### Changed

- **A command origin is any string, and an application names its own (#44).**
  `apply_operation`, `CommandGate.may_command`, `CommandObserver.record_command`
  and `CommandRecord.origin` took a `CommandOrigin`, a closed list, so an
  application that put another command interface in front of the same devices had
  to have that interface added to this package before it could issue a command.
  They now take a `str`. The dispatcher hands the origin to the gate and the
  observer and prints it in diagnostics, and does nothing else with it, so nothing
  here has to learn an interface's name first. `CommandOrigin` remains, holding the
  origins this package produces itself, and its members are strings, so existing
  calls that pass one are unchanged.

  A gate or an observer written against the old signatures needs its `origin`
  parameter annotated `str`. Compare origins with `==`: an origin that arrives as a
  plain string is equal to a member with the same value and is not the same
  object. A gate should deny an origin it does not recognize, so that a mistyped
  name fails closed.

- **Server-advertised poll rates are no longer capped at 7200 s (#45).** Every pollRate above
  two hours used to be cut to 7200 s, so a server asking for a daily Time poll was polled 12
  times as often. A rate is now honored however long it is, so a consumer that relied on
  never waiting more than two hours between polls waits as long as the server says. A rate
  over a day is logged as a warning naming the resource, so a resource that stops refreshing
  has a stated reason. The safety-net poll for a subscribed resource,
  `max(900 s, interval)`, stretches with it. The 10 s lower bound is unchanged.

### Removed

- **`CommandOrigin.LOCAL_API` and `CommandOrigin.SUNSPEC` are removed (#44).**
  Neither is an origin this package produces: both named interfaces of an
  application built on it, listed here only because the list was closed. With an
  origin now any string, an application passes its own name for each interface it
  has. Replace `CommandOrigin.LOCAL_API` with `"local_api"` and
  `CommandOrigin.SUNSPEC` with `"sunspec"` to keep recorded origins reading as they
  did, or choose new names and use them in the gate and the observer too.

- **`MAX_POLL_RATE` is removed from `py20305.client.poll_rate` (#45).** It held the 7200 s cap
  that no longer exists, and importing it now fails. Nothing replaces it; code that read it
  to bound its own waits should use the rate the server advertised.

### Fixed

- **A device with no connector is reported once, not once per control (#43).** A server
  can list a device this process has no connector for, and every control aimed at it
  reached the connector lookup, which logged `No connector found for LFDI ...` each
  time. The dispatcher now reports the condition when it first sees it, clears the
  standing diagnostic if the device later gains a connector, and reports again only if
  the device then loses it. The message now also says that controls for the device are
  not applied.

- **Meter readings are posted at the postRate the server returns on the MirrorUsagePoint
  (#45).** The rate was read back after the MUP POST but never reached the posting loop or
  the device read cadence, so both stayed at the EndDevice or configured rate (300 s by
  default), and a rediscovery reset the stored rate as well. Both now follow the server's
  rate. Because that rate now sets how often the device is read, a server cannot push it
  below 30 s, nor below the rate the client itself posted with if that is shorter, so a
  server echoing a client's own short rate is left alone. A warning is logged when the
  minimum applies. A postRate of 0 is ignored, and a failed readback is logged as a
  warning.

  **The Time resource is polled at its own pollRate (#45).** The Time poll used the
  DeviceCapability rate. It now runs at the shortest pollRate any Time resource advertises,
  global or per-FSA, since one poll refreshes them all. A Time resource advertising 0 is
  left out rather than switching off the poll the others need, and the DeviceCapability rate
  applies only when no Time resource gives a usable rate. The connectivity heartbeat, which
  also GETs Time, is unaffected.

## [0.9.0] - 2026-10-02

### Added

- **Every tagged release now carries a software bill of materials, in CycloneDX
  1.6 and SPDX 2.3, attached as release assets (#40).** The documents name each
  dependency with its version, PURL and license, so a consumer can run a
  vulnerability scan against the exact set resolved when this release was
  built instead of re-resolving the declared ranges and hoping the answer
  matches. The release job cross-checks the tag against the version in
  `pyproject.toml`, so a mismatch fails the build rather than publishing a
  document labeled for a release it does not describe.

- **A `telemetry.reading_profile` setting, also accepted by `TelemetryManager` and
  `TelemetryCoordinator`; the new `signed_load_convention` profile carries the sign
  on every MirrorMeterReading value instead of in flowDirection (#42).** Under it,
  flowDirection is 0, active power is negative on export and reactive power
  negative on injection, power factor takes the reactive power sign, commodity,
  dataQualifier and kind are Not Applicable, totals carry a phase code from
  `ACType`, current is in whole amperes, and values are scaled from the effective
  multiplier and rounded half away from zero. It also posts line-to-line voltage
  and State of Energy (model 713 `WHAvail`) when the device supplies them,
  re-POSTing the MirrorUsagePoint so each new reading is registered before it is
  posted. The default profile's output is unchanged; the SunSpec connector now
  also reads those points under it, skipping model 713 for ten minutes after a
  failed read.

### Fixed

- **A release no longer carries the "Nothing yet." placeholder into the new section (#39).** `build_changelog.py --release` folded the whole `[Unreleased]` body, placeholder included, so a release began with the placeholder and left `[Unreleased]` bare, which the 0.8.1 release had to correct by hand. The placeholder is stripped from what is folded and written back under `[Unreleased]`, and the release test now pins where it ends up.

- **A release removes only the leading "Nothing yet." placeholder (#41).** The strip introduced in #39 removed every standalone placeholder line from the `[Unreleased]` body, which could have edited a hand-written entry; only the one standing first under the heading is removed now, and the release guide says so.

## [0.8.1] - 2026-09-30

### Changed

- **Changelog entries are now files in `changelog.d/` rather than edits to `CHANGELOG.md` (#36).** Two pull requests adding entries used to insert at the same line under `[Unreleased]`, so they conflicted on every pair even though the resolution was always "keep both". Each entry is its own file now, so there is no shared hunk to conflict on.

  `changelog.d/README.md` has the naming rules and the reasoning. A release folds the pending fragments in with `python scripts/build_changelog.py --release X.Y.Z`, which also empties `[Unreleased]` and names the fragments it consumed so they can be deleted in the same commit.

### Fixed

- **An absolute `Location` header is now followed rather than appended to the server URL (#37).** HTTP permits a server to answer a POST with a full URL instead of a path, and the client prefixed every returned `Location` with the base URL on the next request, producing a URL that named the host twice. Against such a server, every readings POST after the MirrorUsagePoint POST failed and no telemetry was delivered; subscription URIs were affected the same way. A `Location` is now resolved against the URL that was POSTed to, as RFC 3986 specifies, and reduced to its path, so a relative one such as `upt/7` also resolves correctly rather than being appended to the server URL with no separator. When the server URL itself carries a path, that path is removed from the result, so it is not repeated on the next request. A `Location` on another origin cannot be reached through the configured server, so it is treated as absent and the caller takes its existing no-`Location` path.

  **A request URL the HTTP library rejects is reported as invalid, not as the server being unreachable (#37).** The rejection was retried three times as a transient connection failure and surfaced as `Cannot reach <host>`, which pointed diagnosis at the network when the fault was in the client. It now raises `Sep2Error` ("Invalid request URL") at once.

## [0.8.0] - 2026-09-18

- The SunSpec connector no longer writes control registers a device reports as
  unimplemented. Almost every setpoint in model 704 is optional, so a device can
  implement the model and none of the individual registers, and until now every
  control path assigned to its target regardless. That went wrong two ways.
  Where the point's scale factor happened to be implemented the write was
  accepted and the head-end was told a control had been applied to a register
  the device never advertised. Where the scale factor was unimplemented too,
  pysunspec2 raised out of the `cvalue` setter — after the enable point beside
  it had already been written, leaving an enable standing over a setpoint that
  was never set.
- Each control now checks its target points before writing anything, including
  before the enable, and a device that cannot accept the control is told so
  rather than written to. This covers `opModMaxLimW`, `opModFixedVar`,
  `opModFixedW`, `opModTargetW` and the two power-factor controls. Disabling is
  unaffected: lowering an enable is safe whether or not the setpoint exists, and
  refusing to would leave a device stuck in whatever state it was already in.
- **The refusal reaches the server.** A declined control raises
  `ModeNotSupportedError`, which the event layer reports as Table 31
  `NOT_SUPPORTED` (251). Withholding the write alone would have left the
  dispatch completing normally, so the head-end would still have been told the
  control was in force, with only a local log line to show otherwise — the
  symptom this change exists to remove. The operator-facing log is emitted once
  per control per connector; the refusal is sent every event, since a head-end
  re-sending a control needs each dispatch answered rather than only the first.
- `opModTargetW` prefers whichever form the device implements. Absolute watts go
  to `WSet` where it exists, and to `WSetPct` as a percent of `WMax` where only
  the percent form does, rather than declining a control the device could
  actually honour.
- **Behavior change.** A control that previously appeared to apply on such a
  device now reports as unsupported. That is the point — the head-end was being
  told a limit was in force that nothing was holding — but an operator watching
  a device of this kind will see controls stop claiming success.
- A device that maps a model 702 rate setting without supporting it is no longer
  sent a limit it cannot apply. 0.7.0 decided a device could be limited through
  `WDisChaRteMax` / `WChaRteMax` by reading whether the register was
  implemented, which a device of this kind answers with a number — usually 0 —
  rather than by reporting the point as absent. The write then came back as a
  Modbus exception and the limit went unapplied, on devices the
  percent-of-`WMax` fallback had been enforcing correctly before 0.7.0. The
  question is now put to the nameplate: a device that can limit a direction
  publishes a non-zero maximum rate rating for it (`WDisChaRteMaxRtg`,
  `WChaRteMaxRtg`), and zero, absent or missing is taken as no declared
  capability. The setting register is not consulted for this, since zero is a
  legitimate setting on a device that can hold it and reading it would misjudge
  a device currently limited to zero as one that cannot be limited at all.
- **`opModMaxLimWAbsorb` can lose enforcement on one device shape.** Model 704
  has no absorb-direction active-power limit, so absorb has no fallback: where
  inject reroutes, absorb is simply not applied. A device that implements
  `WChaRteMax` but leaves the optional `WChaRteMaxRtg` rating unimplemented had
  its absorb limit applied under 0.7.0 and is now refused instead. Neither
  rating point is mandatory, so this is a real shape rather than a hypothetical
  one. It is still the better trade than answering a head-end with a Modbus
  exception, and it matches pre-0.7.0 behavior, where absorb was never applied
  at all — but for absorb specifically, erring towards the fallback costs
  enforcement rather than preserving it. The warning is emitted once per
  connector, so it appears when the control is first enabled rather than on
  every dispatch.
- Erring towards the fallback, and towards declining, is deliberate everywhere
  else. A device misjudged that way keeps behaving as it did before; one
  misjudged the other way silently fails to enforce a limit the head-end
  believes is in force.

## [0.7.0] - 2026-09-18

- The SunSpec connector now applies `opModMaxLimWInject` and
  `opModMaxLimWAbsorb` to the model 702 rate settings. Both controls are
  `UnsignedActivePowerControlType` — absolute watts — so each now maps onto the
  setting carrying the same quantity in the same units: inject limits
  generation, which is the discharge direction (`WDisChaRteMax`), and absorb
  limits absorption, which is the charge direction (`WChaRteMax`).
  `opModMaxLimW`, which is genuinely a `PerCent`, is unaffected and remains the
  only routine writer of `WMaxLimPct`.
- This is a behavior change for `opModMaxLimWInject`, which previously converted
  its watts to a percent of `WMax` and merged that into the model 704
  `WMaxLimPct`. The conversion lost precision to the percent register's
  resolution, and it put two semantically distinct IEEE 2030.5 controls in
  contention for one SunSpec register.
- `opModMaxLimWAbsorb` is enforced for the first time. Model 704 has no
  absorb-direction active-power limit, so the control had nowhere to go and was
  recorded for diagnostics but never written; a device implementing
  `WChaRteMax` now gets the limit applied.
- `opModMaxLimWInject` also writes `WMax`, in the same model write as
  `WDisChaRteMax`. Some PV systems implement no charge/discharge rate points at
  all and honour only `WMax`, so a limit sent solely to the rate setting would
  not be enforced on them; where both are honoured the two agree and the extra
  write is inert. The mirrored value is clamped to `WMaxRtg`, since a `WMax`
  above nameplate is meaningless and a device that rejects it would fail the
  whole model write. `WMax` is itself optional in model 702, so it is written
  only where the device implements it.
- The mirrored `WMax` is restored when the inject control clears, back to the
  value the device held before the event lowered it. The rate settings stay
  where the event put them, but `WMax` is different in kind: no head-end control
  writes it, and the connector lowered it on its own initiative, so the
  connector puts it back — the same reasoning that has the fallback route drive
  `WMaxLimPctEna` low on teardown. It also matters because `WMax` is the base
  that `WMaxLimPct` is a percent of: leaving it lowered would size a later
  `opModMaxLimW` to an event that has ended.
- Neither model 702 rate setting carries an IEEE 1547 standards tag, so a device
  need not implement either. Where `WDisChaRteMax` is absent, inject falls back
  to the previous percent-of-`WMax` route, so no existing deployment loses
  inject-limit enforcement. Absorb has no fallback, for the same reason it had
  no home before, and still logs once per device when it cannot be applied.
- Clearing either control writes nothing to model 702. These are settings
  registers with no enable bit, and restoring a captured baseline would fight
  whatever the DefaultDERControl establishes on revert, so the post-event state
  is left to that control. The fallback route still drives `WMaxLimPctEna` low,
  since that lever is one the connector raised itself.
- A watts limit that is negative, non-numeric or cannot be encoded into the
  `uint16` register at the device's `W_SF` is now refused before any write and
  reported as Table 31 status 253, rather than failing inside the encoder
  partway through. The negative and non-numeric checks run before the route is
  chosen, so they also cover the fallback, where a negative would previously
  have been divided into a percent and clamped to 0 — commanding a full
  curtailment nobody asked for. Unlike the percent route, which clamps a derived
  value above 100, an unencodable watts value has nothing faithful to write.
- `WDisChaRteMax` and `WDisChaRteMaxRtg` are now read back in
  `fetch_configuration` and `fetch_nameplate`. Both were already declared on the
  connector base and mapped into DERSettings and DERCapability, but no SunSpec
  connector populated them, so a head-end could not read back the discharge-rate
  limit it had set.
- A client can now be told to simulate a loss of communications with its
  head-end, so loss-of-communications behavior can be verified against a
  production server without taking that server out of service.
  `CsipClient.simulate_comm_loss(duration_seconds)` fails outbound requests as
  though the network were gone and stops acting on notifications;
  `clear_comm_loss_simulation()` restores the link and drives recovery.
  Everything downstream of the silence is the ordinary machinery: the detector,
  the diagnostics, the retry ladder and the recovery path all run for real,
  because the simulation supplies only the input they already react to. The
  window always closes — it expires on its own, and a restart clears it — and
  the client applies no policy beyond that, leaving whether simulation is
  permitted at all, and for how long, to whatever drives it.
- Activation waits for work already in progress before reporting the link down.
  A request already talking to the server, or a notification handler already
  running, would otherwise finish just afterwards and refresh the contact clock
  or apply a setpoint to a device the operator believes is isolated. The
  returned status says whether both drains completed, so an isolation that was
  not clean is visible rather than assumed.
- Clearing a simulation drives recovery rather than waiting for the schedule.
  The detector only leaves loss-of-communications mode once a request actually
  reaches the server, which waits on the connectivity heartbeat and then a probe
  tick — on default settings, more than two minutes in which a restored link
  looks like a failed one. That recovery runs under the same per-key lock as the
  scheduled probe, because recovery checks for and may re-POST the client's own
  EndDevice before it reaches the rediscovery lock, and two overlapping runs
  would send a live head-end two registrations for the same device.
- The loss-of-communications diagnostic carries a `simulated` marker when the
  injected failure is what produced the silence. Attribution follows the
  failures themselves rather than whether a window happened to be open, so a
  link already down when a simulation is armed, or a request that was in flight
  and fails on its own, is still reported as the genuine outage it is. The
  marker is on that entry specifically, not on every diagnostic emitted during a
  window: a real fault occurring while a simulation runs must stay legible as a
  real fault rather than being filed as a test artifact.

## [0.6.1] - 2026-09-10

No functional changes. The README has been updated.

## [0.6.0] - 2026-09-08

- An event that disappears from a server's DERControlList is now cancelled
  rather than left running to its end. IEEE 2030.5-2023 §10.2.2.3 rule p) is
  explicit that "Clients SHALL consider Events removed from the server before
  the end of their Effective Scheduled Period as cancelled", and equally
  explicit that this is a change from earlier revisions, where setting
  `EventStatus.currentStatus` to 2 was the only way a server could call an
  event off. Both signals now end an event, which matters because head-end
  systems differ on which one they send: an operator cancelling an active event
  by editing it out of the list had no way to stop the device, and the client
  went on holding a control the utility believed it had withdrawn. The
  cancellation takes the path a status change already took, so the wind-down
  randomization of §10.2.3.3, the revert to the DefaultDERControl, and the
  "The event has been cancelled" response are unchanged. An event the server
  drops once it is already over still completes normally: the rule is scoped to
  removal before the end of the Effective Scheduled Period, and reporting
  status 6 for an event that ran its course would be wrong twice over, since
  the status 3 it owes would never be sent.
- Reconciliation runs only against a DERControl list that was fetched in full.
  A fetch that fails or does not parse leaves the client with no list, and a
  list of no events is what a server sends when it has withdrawn all of them —
  so acting on the two alike would revert every device on one transient error.
  `DerProgramState.der_controls_complete` marks a list as served rather than
  merely empty, and only a marked list can cancel anything.

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

## [0.5.0] - 2026-08-25

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

## [0.4.0] - 2026-08-21

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

## [0.3.0] - 2026-08-19

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

## [0.2.0] - 2026-08-19

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

## [0.1.1] - 2026-08-19

No functional changes. The README now uses absolute image and link
addresses, so the PyPI project page renders the project banner and
badges rather than their alt text — PyPI resolves no relative URLs.

## [0.1.0] - 2026-08-18

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
