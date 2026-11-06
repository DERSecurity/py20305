# Connecting devices

A **connector** is the thing that turns an IEEE 2030.5 control into whatever
your device actually speaks. The client resolves a device's LFDI to its
connector, then calls methods on it.

The package ships two connectors, the SunSpec Modbus connector and the print
connector, and the interface for your own. A connector converts its device's
own points to the IEEE 2030.5 values described below; everything above it is
IEEE 2030.5.

## The print connector

Logs what it would have done instead of doing it. Needs no hardware, which
makes it the right thing to point at when bringing up a server connection for
the first time.

```python
from py20305.connectors.config import PrintDemoDeviceConfig

PrintDemoDeviceConfig(lfdi=LFDI, description="bench test")
```

## SunSpec Modbus

A downstream adapter for a SunSpec device. It reads and writes the SunSpec
700-series models (or a meter model, below) and converts them to and from the
connector contract inside its own package, so the client sees readings,
DERCapability and DERSettings element names, DERStatus codes and
`update_<element>` calls like any other connector's. It speaks three
transports:

```python
from py20305.connectors.config import SunSpecDeviceConfig

# Modbus TCP
SunSpecDeviceConfig(lfdi=LFDI, host="192.168.1.50", port=502, unit_id=1)

# Modbus RTU over serial
SunSpecDeviceConfig(lfdi=LFDI, transport="rtu", serial_port="/dev/ttyUSB0", baudrate=9600)

# Modbus TCP wrapped in TLS
SunSpecDeviceConfig(
    lfdi=LFDI, transport="tcp+tls", host="192.168.1.50", port=802,
    ca_path="certs/ca.pem", cert_path="certs/client.pem", key_path="certs/client.key",
)
```

Install it with the `sunspec` extra (`pip install py20305[sunspec]`).

What it reports:

- **Readings.** Real, reactive and apparent power, power factor, frequency,
  voltage and current, per line for the lines the device's wiring has. The
  system voltage is the line-to-line average, so it carries the wiring's
  line-to-line PhaseCode (AB for single- and split-phase, ABC for
  three-phase); the per-line voltages are line to neutral (AN, BN, CN) and
  the line-to-line pairs AB, BC and CA. The system current is a total across
  the lines (A, AB or ABC). Under the signed-load reading profile the power
  totals carry the wiring's PhaseCode too, as that profile registers them.
- **Ratings and settings** from model 702, under the `rtg*` and `set*` names,
  with `rtgMaxA` from the current rating.
- **Status** from model 701 in the DERStatus types: `inverterStatus`,
  `operationalModeStatus`, the 2030.5-2023 `connectStatus` and 2030.5-2018
  `genConnectStatus` bitmaps, and the AC over- and under-voltage and over-
  and under-frequency alarms at their `alarmStatus` bits.

What it applies: the volt-var, volt-watt and watt-var curves, `opModMaxLimW`
and its inject and absorb forms, `opModFixedW`, `opModTargetW`,
`opModFixedVar`, the two fixed power factors, `opModFreqDroop`, the CSIP-AUS
generation and load limits, and `opModConnect` / `opModEnergize` through the
model 703 enter-service permit. Any other element is unsupported by design.

The connector does not apply ramp times. A control's `rampTms` is accepted and
ignored: the device ramps at its own rate. The connector logs that once at INFO
the first time a control sets one.

Under IEEE 2030.5 the absorbed reactive power limits, `rtgMaxVarNeg` and
`setMaxVarNeg`, are negative. The connector reports the model 702 magnitude
as a negative number.

### Meters

A device with `role: site_meter` or `role: der_meter` (see
[Meters](running.md#meters)) is read through a SunSpec meter model instead of
the 700-series models. The connector reads the first of these the device
exposes: 201 to 204 (single-phase, split-phase, wye and delta, with scale
factors), 211 to 214 (the same in floating point), then 220 (the secure AC
meter). A device configured as a meter that exposes none of them fails at scan
with a message naming the models it does expose.

A device left as a DER that exposes a meter model and no 700-series model is
not changed to a meter. The connector logs a WARNING once suggesting
`site_meter` or `der_meter`, and the scan fails as it does for any device
without model 702.

The connector reports a meter's real and reactive power in its role's frame:
import positive for a site meter, the DER's output positive for a DER meter.
The meter models do not say which sign of real power is import, so the
connector learns it from the meter's import and export energy totals, or takes
it from `power_sign`, and leaves real and reactive power out until it knows.
The energy totals are reported as forward and reverse energy readings in the
role's frame.

### CSIP-AUS operating envelope limits

Of the four CSIP-AUS limits, the SunSpec connector enforces the generation
limit, `opModGenLimW`, and the load limit, `opModLoadLimW`. The generation
limit is written as a percent of the device's maximum power to its power
limit; when `opModMaxLimW` is active too, the more restrictive of the two is
written. The load limit is the maximum charge rate of a storage device; when
`opModMaxLimWAbsorb` is active too, the lower of the two is written, and when
no charge limit remains the connector writes the device's rated charge rate,
the value for no limit. `doeModesSupported` and `doeModesEnabled` name only
the limits the device can take. The export and import limits constrain power
at the site's connection point, which an inverter cannot measure, so the
connector does not apply them.

## Writing your own

Subclass `BaseConnector` and override only the methods for what your device
does. Everything you don't override is a silent no-op, which matches the
physical reality that not every device does every IEEE 2030.5 function.

Every key and value a connector exchanges with the client is an IEEE 2030.5
element name and value. Converting the device's own points to them is the
connector's job.

```python
from typing import Any
from py20305.connectors.base import BaseConnector, ConnectorPayload, Reading

class MyInverter(BaseConnector):
    connector_name = "MyInverter"
    der_type = 83  # combined PV + storage

    async def fetch_monitoring(self) -> list[Reading]:
        return [
            Reading(uom=38, value=await self._read_power()),
            Reading(uom=33, value=await self._read_frequency()),
        ]

    async def update_op_mod_max_lim_w(self, params: dict[str, Any]) -> ConnectorPayload:
        if params["enabled"]:
            await self._write_limit_percent(params["value"] / 100)
        else:
            await self._clear_limit()
        return None
```

### Readings

`fetch_monitoring` returns a list of `Reading`. Each reading states its own
IEEE 2030.5 ReadingType and its value in engineering units:

| Field | Meaning |
|---|---|
| `uom` | UomType: 38 W, 63 var, 61 VA, 29 V, 5 A, 33 Hz, 65 cos(theta), 72 Wh |
| `value` | engineering units; signed, see [Signs](#signs) |
| `phase` | PhaseCode: 0 not applicable (default), 128 A, 64 B, 32 C, 129 AN, 65 BN, 33 CN, 132 AB, 66 BC, 40 CA, 224 ABC |
| `kind` | KindType: 37 power (default), 12 energy, 0 not applicable |
| `data_qualifier` | DataQualifierType: 12 normal (default), 2 average, 8 maximum, 9 minimum |
| `accumulation_behaviour` | 12 instantaneous (default), 9 summation |
| `flow_direction` | set only for a direction-specific reading, such as an energy total delivered forward (1) or in reverse (19) |
| `multiplier` | powerOfTenMultiplier to post with; the default is -3 for Hz and PF, -1 for V and A, 0 otherwise |
| `quality_flags` | this sample's qualityFlags; the default is valid |

Leave out a quantity the device does not measure; it is not posted rather than
posted as zero. Give each reading a distinct ReadingType, multiplier included: a
second reading with the same one is dropped with a warning, and so is a reading
whose fields are not valid codes. Two readings that differ only in multiplier
are two readings.

Common readings:

| Quantity | Reading |
|---|---|
| Real, reactive, apparent power and current totals | `uom` 38, 63, 61, 5; `phase` 0, or the phases the total covers (132 AB, 224 ABC) |
| Line-to-line voltage, averaged across lines | `uom` 29, `data_qualifier` 2, `phase` 132 or 224 |
| Line-to-neutral voltage of one line | `uom` 29, `data_qualifier` 2, `phase` 129, 65 or 33 |
| Frequency | `uom` 33 |
| Power factor | `uom` 65, `kind` 0 |
| Per-line power, power factor or current | the same `uom` with `phase` 128, 64 or 32 |
| Line-to-line voltage of one pair | `uom` 29, `phase` 132, 66 or 40 |
| State of energy | `uom` 72, `kind` 0 |
| Energy delivered forward or in reverse | `uom` 72, `kind` 12, `accumulation_behaviour` 9, `flow_direction` 1 or 19 |

#### mRIDs

A reading's MirrorMeterReading mRID is derived from its identity: `uom`,
`phase`, `kind`, `data_qualifier`, `accumulation_behaviour`, `flow_direction`
and the effective `multiplier`. A reading whose ReadingType differs from
another's therefore always has a different mRID, so the client never changes a
ReadingType a server already holds (IEEE 2030.5-2023 §10.11.3 Rule n).

Readings that release 0.16 registered keep the mRID it gave them: a fixed table
maps each of those identities to its 0.16 slot (see `py20305.telemetry.mup`),
so a server holding a 0.16 registration keeps receiving readings under the
mRIDs it knows. Every other identity, a phased system voltage among them, gets
an mRID hashed from the identity and the reading profile, in a layout that
cannot collide with the slot layout: the SFDI, a marker, the hash, and the
Private Enterprise Number. The profile is part of the hash because each profile
derives a different ReadingType from the same reading.

The client registers each reading the first time it appears. Under the default
profile a power reading registers with the flowDirection its sign gives at that
moment, and the server never lets a ReadingType change. The runner's
`telemetry.state_file` (see
[Running](running.md#keeping-registrations-across-restarts)) keeps the
registrations across restarts; an embedding application stores
`TelemetryCoordinator.to_checkpoint()` and restores it with
`restore_from_checkpoint()` before it starts metering. When a later cycle
supplies a reading the MirrorUsagePoint did not register, the client posts the
MirrorUsagePoint again with every earlier ReadingType unchanged plus the new
one, before posting readings for it.

The reading profile (`default`, `signed_load_convention`, `signed_as_reported`)
and `power_direction` apply to the readings with `uom` 38 and 63.

### Signs

Real and reactive power are signed in the frame of the device's role:

- **DER and DER meter.** Positive real power is power the DER delivers (its
  output); negative is power it absorbs. Positive reactive power is reactive
  power the DER injects (over-excited); negative is reactive power it absorbs
  (under-excited).
- **Site meter.** Positive real and reactive power are import, from the area
  EPS to the premises, which IEEE 2030.5 FlowDirectionType calls Forward.

A power factor carries the sign of its reactive power. The default profile
posts the sign as flowDirection; the `SIGNED_LOAD_CONVENTION` profile negates a
DER's and a DER meter's power and posts a site meter's as reported, since
import positive is already the load convention. The `SIGNED_AS_REPORTED`
profile posts the sign the connector reports, for every role. A connector that
reports another convention posts the wrong sign.

### Ratings and settings

`fetch_nameplate` returns DERCapability element names and `fetch_configuration`
DERSettings element names. A value is a number, `{value, multiplier}`, or for a
power factor `{displacement, multiplier}`. An absent or `None` key is not
reported; an explicit 0 is.

| `fetch_nameplate` key | Notes |
|---|---|
| `rtgMaxW` | required |
| `rtgMaxVA`, `rtgMaxVar`, `rtgMaxVarNeg`, `rtgMaxA` | `rtgMaxA` is the maximum continuous AC current, amperes RMS. `rtgMaxVarNeg` is negative (see below) |
| `rtgOverExcitedW`, `rtgOverExcitedPF`, `rtgUnderExcitedW`, `rtgUnderExcitedPF` | |
| `rtgMaxChargeRateW`, `rtgMaxChargeRateVA`, `rtgMaxDischargeRateW`, `rtgMaxDischargeRateVA` | |
| `rtgMaxWh`, `rtgMaxAh` | |
| `rtgVNom`, `rtgMaxV`, `rtgMinV` | |
| `rtgReactiveSusceptance`, `rtgNormalCategory`, `rtgAbnormalCategory` | |
| `modesSupported` | DERControlType bitmap, as a Python `int` from 0 to 4294967295. Any other type or range is reported as `00000000` and logged as a warning |
| `doeModesSupported` | CSIP-AUS extension bitmap; defaults to all four limits |

| `fetch_configuration` key | Notes |
|---|---|
| `setMaxW` | required |
| `setMaxVA`, `setMaxVar`, `setMaxVarNeg` | `setMaxVarNeg` is negative (see below) |
| `setMinPFOverExcited`, `setMinPFUnderExcited` | |
| `setMaxChargeRateW`, `setMaxChargeRateVA`, `setMaxDischargeRateW`, `setMaxDischargeRateVA` | |
| `setMaxWh`, `setMaxAh` | |
| `setVNom`, `setMaxV`, `setMinV` | |
| `setGradW` | |
| `modesEnabled` | DERControlType bitmap, as a Python `int` from 0 to 4294967295. Any other type or range is left out and logged as a warning |
| `doeModesEnabled` | CSIP-AUS extension bitmap; defaults to the export and import limits |

The absorbed reactive power limits are negative numbers. IEEE 2030.5-2023 says
of `setMaxVarNeg`: "If present, SHALL be a negative value >= rtgMaxVarNeg
(default). If absent, defaults to negative setMaxVar." and of `rtgMaxVarNeg`:
"If absent, defaults to negative rtgMaxVar." A connector that reads a magnitude
from its device sends its negation, for example `{"value": -4400, "multiplier": 0}`.
The client posts the sign it is given.

### Status and availability

`fetch_status` returns IEEE 2030.5 values. The client checks each against its
IEEE 2030.5 type and leaves out, with a warning, a value outside it:

| Key | IEEE 2030.5 values |
|---|---|
| `inverterStatus` | 0 to 10 (InverterStatusType) |
| `operationalModeStatus` | 0 to 3 |
| `localControlModeStatus` | 0 to 1 |
| `storageModeStatus` | 0 to 2 |
| `stateOfChargeStatus` | 0 to 10000, hundredths of a percent |
| `connectStatus` | ConnectStatusType2 bitmap (2030.5-2023): bit 0 connected, bit 1 energized |
| `genConnectStatus`, `storConnectStatus` | ConnectStatusType bitmap (2030.5-2018): bits 0 to 4, connected, available, operating, test, fault |
| `alarmStatus` | bitmap of bits 0 to 10 |

Each edition sends only the field the connector supplied for it, and the
bitmaps are never converted into each other. Supply `connectStatus` for a 2023
server, and `genConnectStatus` and `storConnectStatus` for a 2018 server. A 2023
server never receives `genConnectStatus` or `storConnectStatus`, which that
edition deprecates and forbids, and a 2018 server never receives
`connectStatus`. A field the connector does not supply is not sent.

`fetch_availability` returns DERAvailability element names
(`availabilityDuration`, `maxChargeDuration`, `readingTime`,
`reserveChargePercent`, `reservePercent`, `statVarAvail`, `statWAvail`).

### Controls

Each IEEE 2030.5 control element has one method, `update_<element>`, named
after the element in snake case: `opModVoltVar` is `update_op_mod_volt_var`,
`opModHVRTMustTrip` is `update_op_mod_hvrt_must_trip`, `opModFixedPFInjectW` is
`update_op_mod_fixed_pf_inject_w`. There is one for every DERControlBase
element, for the CSIP-AUS limits (`update_op_mod_exp_lim_w`,
`update_op_mod_imp_lim_w`, `update_op_mod_gen_lim_w`,
`update_op_mod_load_lim_w`) and for the DefaultDERControl settings
(`update_set_grad_w`, `update_set_soft_grad_w`, `update_set_es_delay` and the
other enter-service settings). `py20305.connectors.modes.ALL_ELEMENTS` lists
them.

Each method receives one dict:

| Key | Meaning |
|---|---|
| `enabled` | True when the control sets the element. False when the element is no longer commanded: return the function to the device's own default |
| `value` | the element's value as IEEE 2030.5 defines it, present when `enabled` is True |
| `rampTms` | the DERControlBase `rampTms`, hundredths of a second, when the control sets it |

The value uses the IEEE 2030.5 element names:

| Element type | `value` |
|---|---|
| Curve (`opModVoltVar`, `opModFreqWatt`, the ride-through curves, ...) | the DERCurve the link references: `curveType`, `xMultiplier`, `yMultiplier`, `yRefType`, `CurveData` (a list of `{xvalue, yvalue}`, plus `excitation` on each point of an `opModWattPF` curve), and `openLoopTms`, `rampDecTms`, `rampIncTms`, `rampPT1Tms`, `vRef`, `autonomousVRefEnable`, `autonomousVRefTimeConstant` when set |
| PerCent, SignedPerCent (`opModMaxLimW`, `opModFixedW`, `opModFixedV`, ...) | the integer in hundredths of a percent |
| Power (`opModMaxLimWInject`, `opModTargetW`, `opModTargetVar`, the CSIP-AUS limits, ...) | `{"value": ..., "multiplier": ...}` |
| Delta power (`opModDeltaW`, `opModDeltaVar`) | `{"value": ..., "multiplier": ..., "bidirectional": ...}` |
| Power factor (`opModFixedPFInjectW`, `opModFixedPFAbsorbW`) | `{"displacement": ..., "excitation": ..., "multiplier": ...}` |
| Fixed var (`opModFixedVar`, `opModMaxLimPctVarInject`, ...) | `{"refType": ..., "value": ...}` |
| Frequency droop (`opModFreqDroop`) | `{"dBOF", "dBUF", "kOF", "kUF", "openLoopTms", "pMin"}` |
| Boolean (`opModConnect`, `opModEnergize`, the permits) | a bool |
| DefaultDERControl settings | the integer |

The DefaultDERControl settings are never withdrawn: IEEE 2030.5-2023 says of
each that, "When present, this value SHALL update the value of the corresponding
setting" (for example DERSettings::setGradW). Their methods receive
`enabled: True` and the integer, and never `{"enabled": False}`.

`opModConnect` and `opModEnergize` arrive separately. IEEE 2030.5 combines
them with a logical AND: the device is connected only when neither is false.

A control that names a curve the client does not hold withdraws that element
(`{"enabled": False}`). An element a server marks `disabled="true"` withdraws
the element too: IEEE 2030.5-2023 says a disabled DERControl mode "follows the
rules and guidelines as if the DERControl Mode were not disabled", so it takes
part in event precedence like any other and then withdraws the mode.
When an event ends, the client withdraws each element it set that the
DefaultDERControl does not carry, except connect, energize and the permits:
ending an event that disconnected a device is not an order to reconnect it.

### Configuring a device

A connector whose readings are fixed for the device's life can say so with
`declared_readings()`, returning the readings it provides as their IEEE 2030.5
identities (a `ReadingIdentity`, or a `Reading` standing for its identity):

```python
from py20305.connectors.reading import Reading

def declared_readings(self) -> frozenset[ReadingIdentity] | None:
    return frozenset({reading_identity(Reading(value=0.0, uom=38))})  # real power
```

Under the `SIGNED_AS_REPORTED` profile the MirrorUsagePoint then registers
exactly those readings from the first cycle, also one with no values, and is
never re-posted for a reading that appears later. A reading `fetch_monitoring`
returns without declaring it is not posted, and is logged once. Without a
declaration (the default, `None`) the readings register as they appear. The
other profiles ignore it.

Point a device at it by import path — no registration needed:

```python
from py20305.connectors.config import CustomDeviceConfig

CustomDeviceConfig(
    lfdi=LFDI,
    class_path="myproject.connectors.MyInverter",
    init_kwargs={"address": "192.168.1.50"},
)
```

## Connectors that need shared state

`class_path` constructs your connector with plain keyword arguments. When that
is not enough — a connection pool shared across devices, a client for a
transport this package knows nothing about, a configuration model of your own —
supply a `factory_resolver` instead.

It is consulted for every device before the built-in resolution runs. Return a
factory to claim the device, or `None` to let the package handle it:

```python
from py20305.connectors.registry import ConnectorConfigRegistry

pool = MyConnectionPool()

def resolve(device):
    if getattr(device, "type", None) == "my-transport":
        return lambda: MyConnector(pool, device.lfdi)
    return None

registry = ConnectorConfigRegistry(devices, factory_resolver=resolve)
```

The factory is invoked lazily, exactly like a bundled connector's — so it may
close over expensive state without constructing it up front, and it inherits
the same permanent-failure caching and single-flight resolution.

## Errors

Two vocabularies, and the distinction matters because IEEE 2030.5 makes the
head-end care about it:

- `py20305.connectors.errors` — the transport broke.
  `ConnectorConnectionError` (with a `permanent` flag), `ConnectorTimeoutError`.
- `py20305.connectors.control_errors` — the transport was fine and
  the control still did not happen. The device is offline, the mode is
  unsupported, the value is out of range, or the site declined.

Each maps to a specific IEEE 2030.5 Table 31 response status. Raise the one
that describes what actually happened and the right code reaches the server.

## The write path

Every control that reaches a device goes through one funnel, whether it came from
a translated DERControl, a default-control fallback, a comms-loss clear, or a
caller naming a control directly. Two seams hang off that funnel, and both are
optional: omit them and the dispatcher behaves as though neither existed.

A device configured as a meter never reaches the funnel. A control or a named
operation addressed to it raises `DeviceNotConfiguredError`, which the event
processor reports as status 252 (Not Applicable); a default control, a clear or
a mode disable skips it. When a control is fanned out across several local
devices, the meters are left out of the fan-out, so they are neither sent the
control nor counted as refusing it.

### Naming a control directly

`apply_control` takes a DERControl and translates it. A caller that already knows
which control it wants — a protocol server serving register writes, for instance
— uses `apply_operation` instead:

```python
await dispatcher.apply_operation(
    lfdi, "op_mod_max_lim_w", {"enabled": True, "value": 8000},
    origin="register_server",
)
```

It goes through the same funnel, so everything an event-driven write gets applies:
the command is recorded with its origin, and a failure is recorded as rejected and
re-raised. Reaching a connector directly would bypass both.

### Naming an origin

An origin is a string, and the caller chooses it. The dispatcher hands it to the
gate and to the observer and prints it in diagnostics, and does nothing else with
it, so an application's own interfaces need no entry anywhere in this package.
Pick one name per interface and use it in all three places: the call above, your
`CommandGate`, and whatever reads the audit trail.

`CommandOrigin` holds only the origins this package produces: `IEEE2030_5` for a
control from the utility server, `DDERC_REAPPLY` for a default control reapplied
when an event ends, and `COMMS_LOSS` for the fallback on a loss of
communications. They are strings too, so a gate compares them like any other:

```python
def may_command(self, device: str, origin: str) -> bool:
    if origin in (CommandOrigin.DDERC_REAPPLY, CommandOrigin.COMMS_LOSS):
        origin = CommandOrigin.IEEE2030_5      # the same interface acting
    return self.commander_for(device) == origin
```

Deny an origin your gate does not recognize. A new write path should have to say
who owns it, and a mistyped name then fails closed where it would otherwise
command.

A refusal here raises `CommandNotPermittedError`. This caller named one control
and has somewhere to put the answer -- a protocol server owes its own client the
difference between a write that failed and one that was not permitted, and
cannot report either from silence.

### Who may command

An application serving more than one command interface usually intends only one
of them to command any given device. Deciding that in its own configuration
leaves it unenforced here, because every interface still reaches the dispatcher.

`CommandGate` is how the dispatcher asks. Implement `may_command(device, origin)`
and pass it as `command_gate`:

```python
class OneCommanderPerDevice:
    def may_command(self, device: str, origin: str) -> bool:
        return self.commander_for(device) == origin
```

Which interface holds authority is the application's concern, not this package's,
so the dependency points that way — as it does for `CommandObserver`. Omitted,
every origin may command everything, which is right for a single-interface
consumer. That includes an origin nothing claims, so with no gate a mistyped
name commands like a correct one.

The dispatcher asks once per element of a control, so a gate must give the same
answer for a device and origin while one control is applied. A gate whose answer
changes partway through can let some modes reach the device while the server is
told the control was rejected.

A refused *server-issued* control is reported through `diagnostics`, and
`apply_control` and `apply_control_by_lfdi` raise `CommandNotPermittedError`
once every element has been offered to the gate. The event processor answers the
server with status 252 (Not Applicable) for that device instead of status 2
(Active), and leaves the device out of the event's Completed response (IEEE
2030.5-2023 Table 31). Status 2 would tell the server the event runs on a device
that was never written. Table 31 has no code for a refusal by local
configuration; 252 is the code for a control the device is not set up to take,
the same code a `DeviceNotConfiguredError` gets. A server on IEEE 2030.5-2018
receives 252 unchanged.

A refused default control or clear is reported and dropped, since neither sends
a response. In every case the command observer records nothing, because a
command that never left must not appear in the device's commanded state. The
refusal is still kept: the audit trail records it as a write with
`error_type: refused` and outcome `refused` (see [Observers](observers.md)).

One case is deliberately ungated. Authority is held per device, so a control the
resolver cannot name a device for has no rule to apply -- an href that resolves
to a connector but not to an LFDI. Those writes proceed, and say so at debug
level. Denying them would fail closed by accident rather than by decision, and
would drop writes that worked before the gate existed.

### What counts as an implemented control

`BaseConnector` declares each control method as a concrete method returning
`None`, so a presence check finds one whether or not the connector implements
it. The dispatcher therefore distinguishes two ways a control can be
unsupported, because only one of them is anybody's mistake:

- The connector inherits the base no-op. It has no register for that control and
  never claimed one — expected, and skipped with a debug line.
- Nothing resolves at all. For a connector that supplies modes dynamically from a
  live offer, that means the offer is missing something it should carry, which is
  actionable and reported as a warning.

Overriding the method is what support means. A connector that overrides nothing
is not silently credited with accepting every command.

## Reacting to the schedule

The write path delivers setpoints. Separately, the client pushes a connector the
forward-looking schedule behind those setpoints, so logic that wants to plan (an
optimizer, a price-aware dispatcher) has it without polling.

Override `on_schedule_notification`, or one of the per-stream convenience hooks
it fans out to. Each is informational: an exception raised in one is logged and
swallowed, never affecting dispatch, and none of them apply a setpoint, which
stays the `update_*` path's job.

| Hook | Fires on |
|---|---|
| `notification_control` | a scheduled `DERControl` was added, updated, activated, superseded, cancelled or completed |
| `notification_default_baseline` | the program's `DefaultDERControl` baseline was added or changed |
| `notification_doe` | a Dynamic Operating Envelope (export / import / generation / load watt-limit) changed |
| `notification_price` | the active pricing interval changed (Pricing function set) |

`notification_drlc` and `notification_flow_reservation` exist for streams the
client does not emit yet.

Each hook receives a `ScheduleNotification`: the `stream`, the `transition` and
`status` that fired it, the control's `mrid`, `program_href` and `primacy`, the
post-randomization `start` / `duration` / `end`, the `affected_lfdis` the event
scopes to, the `randomization` window, and a stream-specific `payload`. For
`price` the payload carries the tariff profile's price multiplier and the active
interval's consumption blocks; the relay is global and does not actuate, so the
hook decides what the price means for the device, for example curtailing export
when the price goes negative.
