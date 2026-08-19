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
speaks. SunSpec Modbus ships with the package; anything else is a subclass or a
factory you supply.

**Reports back.** DERStatus, DERCapability, DERSettings, DERAvailability and
metered readings, posted as MirrorUsagePoints.

**Optionally, exposes itself.** An HTTP management API for observing and
nudging a running client, and a forwarder that publishes every captured
protocol exchange to a monitoring system over MQTT.

## Standards

- **IEEE 2030.5-2018 and 2030.5-2023.** Both schemas ship with the package;
  the generated bindings track 2023, and a `server_2018_compat` flag adjusts
  behavior for servers still on 2018.
- **CSIP-AUS.** The Australian Common Smart Inverter Profile, including DOE
  (dynamic operating envelope) limits.

The client maps one certificate identity to one end device: it registers and
drives that device, rather than fanning one server-side EndDevice out across
several local ones.

## Part of Project Satori

Satori — “awakening” — is an LF Energy open-source project led by
the SunSpec Alliance, DER Security, and industry consortium members. It pairs this IEEE 2030.5 / CSIP client with
[PySunSpec2](https://github.com/sunspec/pysunspec2), the SunSpec Modbus
reference library, so a certified Modbus device can join a utility DER
program without a firmware rewrite.

## Getting started

```bash
pip install py20305
```

Then follow the [quickstart](quickstart.md), which gets you talking to a real
server without any hardware attached.

## License

Apache-2.0.
