# py20305

An open IEEE 2030.5 client for distributed energy resources, with CSIP-AUS
support. It speaks the utility's protocol so your device doesn't have to.

Point it at an IEEE 2030.5 server and it will register, discover the resources
the server exposes, run the DERControl schedule it is given, apply the
resulting setpoints to a device, and post telemetry back.

## What it does

**Talks to the head-end.** Discovery, registration, mutual-TLS transport,
retry with backoff, redirect probing, and the server-time base that keeps
schedules aligned with the utility rather than with the local clock.

**Runs the schedule.** DERControl events are not setpoints — they are a
schedule with start times, durations, primacy and randomization, and they
supersede one another under rules the standard spells out precisely. The event
engine implements that: a five-state machine per event, supersession across
overlapping programs, randomized start and duration, and the `Response`
acknowledgments the server expects back.

**Drives the device.** A connector turns a control into whatever the device
speaks. You supply it, as a subclass or a factory; the package ships a
no-hardware print connector for bringing up a server connection.

**Reports back.** DERStatus, DERCapability, DERSettings, DERAvailability and
metered readings, posted as MirrorUsagePoints.

**Reads the price.** With the Pricing function set switched on, the client walks
the server's tariff tree and relays each active interval's price to connectors
as the schedule moves through it. It does not act on the price itself: relaying
is where the library stops and the connector's price handler begins.

**Optionally, exposes itself.** An HTTP management API for observing and
nudging a running client, and observer hooks that hand an embedding
application a record of every exchange, connection and anomaly, to log or
pass to a monitoring system.

## Standards

- **IEEE 2030.5-2018 and 2030.5-2023.** Both schemas ship with the package;
  the generated bindings track 2023, and a server schema profile
  (`server.schema`) sends a 2018 server only what 2018 defines, plus any
  function it borrows from 2023.
- **CSIP-AUS.** The Australian Common Smart Inverter Profile, including DOE
  (dynamic operating envelope) limits. The client writes its CSIP-AUS
  extensions in the namespace the server declares, so it works with servers on
  CSIP-AUS 1.3 (`https://csipaus.org/ns/v1.3`) and on earlier versions
  (`https://csipaus.org/ns`).

The client maps one certificate identity to one end device: it registers and
drives that device, rather than fanning one server-side EndDevice out across
several local ones.

## Certification

py20305 is not SunSpec CSIP certified, and using it does not make a product
certified. Certification is a property of the *product* -- the assembled
client device or system, tested as a whole -- not of a library inside it.

If you are building a client product that needs SunSpec CSIP certification,
follow the SunSpec Alliance's certification process:
[IEEE 2030.5 / CSIP certification](https://sunspec.org/ieee-2030-5-csip-certification/).

## Part of Project Satori

Satori — “awakening” — is an open-source project led by
the SunSpec Alliance, DER Security, and industry consortium members. It pairs an IEEE 2030.5 / CSIP client with
[PySunSpec2](https://github.com/sunspec/pysunspec2), the SunSpec Modbus
reference library, so a certified Modbus device can join a utility DER
program without a firmware rewrite. This package is the IEEE 2030.5 half; the
application that drives the device supplies the connector between the two.

## Getting started

```bash
pip install py20305
```

Then follow the [quickstart](quickstart.md), which gets you talking to a real
server without any hardware attached.

## License

Apache-2.0.
