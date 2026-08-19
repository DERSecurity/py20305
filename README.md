<!-- Project Satori logo. Drop docs/assets/satori-logo.svg (see docs/assets/ASSETS.txt),
     then replace this comment with:
<p align="center"><img src="docs/assets/satori-logo.svg" alt="Satori" width="360"></p>
-->

# py20305

**The IEEE 2030.5 / CSIP client of [Project Satori](https://dersec.io/satori)** —
an LF Energy open-source project led by the SunSpec Alliance, DER Security,
and industry consortium members.

[![CI](https://github.com/DERSecurity/py20305/actions/workflows/ci.yml/badge.svg)](https://github.com/DERSecurity/py20305/actions/workflows/ci.yml)
[![Tests](.github/badges/tests.svg)](https://github.com/DERSecurity/py20305/actions/workflows/ci.yml)
[![Coverage](.github/badges/coverage.svg)](https://github.com/DERSecurity/py20305/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/github/license/DERSecurity/py20305)](LICENSE)
[![Project Satori](https://img.shields.io/badge/Project-Satori-b7410e)](https://dersec.io/satori)

An open IEEE 2030.5 client for distributed energy resources, with CSIP-AUS
support. It speaks the utility's protocol so your device doesn't have to.

Point it at an IEEE 2030.5 server and it registers, discovers what the server
exposes, runs the DERControl schedule it is given, applies the resulting
setpoints to a device, and posts telemetry back.

## Install

```bash
pip install py20305
```

The base install is the protocol client. Extras add what you may not need:
`sunspec` for Modbus devices, `api` for the HTTP management API, `mqtt` for
traffic forwarding, or `all`.

## Running it as a service

```bash
pip install "py20305[cli,sunspec]"
py20305 --config client.yaml --check   # validate, print your LFDI
py20305 --config client.yaml           # run
```

One YAML or JSON file describes the server, the certificate and the devices. It
retries the connection while the server is down, registers itself only if the
server does not already know it, and stops cleanly on SIGTERM. See
[Running a client](https://dersecurity.github.io/py20305/running/)
and [`examples/client.example.yaml`](examples/client.example.yaml).

## Running it in a container

```bash
docker build -t py20305 .
docker run -d -v "$PWD/config:/etc/py20305:ro" py20305
```

The image carries the client and nothing else -- configuration and certificates
are mounted, never baked in. It runs unprivileged and stops in order on
`SIGTERM`. See [Running in a container](https://dersecurity.github.io/py20305/docker/)
and [`examples/docker-compose.yml`](examples/docker-compose.yml).

## Embedding it: talking to a server in five minutes

IEEE 2030.5 is mutual TLS throughout, and the server identifies your client by
its certificate. Your LFDI — the identifier the utility registers — is derived
from it:

```python
from pathlib import Path
from py20305.security import compute_lfdi

print(compute_lfdi(Path("certs/client.pem").read_text()))
```

Give that to whoever runs the server, then:

```bash
python examples/quickstart.py \
  --url https://server.example.com:8443 \
  --cert certs/client.pem --key certs/client.key --ca certs/ca.pem
```

That drives the print connector, which needs no hardware — it logs the controls
it would have applied. So you can exercise the whole discovery, event and
telemetry path against a real server before an inverter is wired up.

Swap in `SunSpecDeviceConfig` when you have one. Full walkthrough in the
[quickstart](https://dersecurity.github.io/py20305/quickstart/).

## What's in it

| | |
|---|---|
| **Protocol client** | Discovery, registration, mTLS transport, retry with backoff, redirect probing, server timebase |
| **Event engine** | DERControl five-state machine, supersession across programs, randomized start and duration, `Response` acknowledgments |
| **Telemetry** | DERStatus, DERCapability, DERSettings, DERAvailability, metered readings as MirrorUsagePoints |
| **Connectors** | SunSpec Modbus over TCP, RTU and TLS; a no-hardware print connector; your own by subclass or factory |
| **Subscriptions** | Subscribe/notify as an alternative to polling, with a notification listener |
| **Runner** | A CLI, config file, connection retry and signal-handled shutdown |
| **Management API** | Optional HTTP API for observing and nudging a running client |
| **Forwarding** | Optional MQTT publication of every captured exchange |

## Standards

**IEEE 2030.5-2018 and 2030.5-2023.** Both XSDs ship with the package and
validation runs against them. The generated bindings track 2023; a
`server_2018_compat` flag adjusts behavior for servers still on 2018.

**CSIP-AUS**, including DOE (dynamic operating envelope) limits.

The client maps one certificate identity to one end device. It registers and
drives that device, and does not fan a server-side EndDevice out across several
local ones.

## Project Satori

*Any certified DER. Any utility program.* Satori — “awakening” — is an
open-source initiative to make any certified DER a compliant participant in
utility DER programs: point it at the Modbus port of a UL 1741 SB device —
inverter, battery, or EV — and it joins a program without a firmware rewrite.
The bundle builds on SunSpec-certified implementations contributed by
DER Security, and this repository is its IEEE 2030.5 half:

| Project | Role |
|---|---|
| [PySunSpec2](https://github.com/sunspec/pysunspec2) | SunSpec Modbus reference library, used in more than 80% of inverter-based products shipped globally |
| **py20305** (this repository) | IEEE 2030.5 client stack with CSIP and CSIP-AUS support, and a SunSpec Modbus bridge |
| SunSpec DevKit CE | Discover, read and identify SunSpec devices on the wire |

## Documentation

<https://dersecurity.github.io/py20305/>

## Development

```bash
pip install -e ".[dev]"
pytest tests -q                 # unit tests, no network
ruff check src tests examples scripts
mypy
mkdocs serve                    # docs at http://127.0.0.1:8000
```

There is also an end-to-end suite that runs this client against
[envoy](https://github.com/bsgip/envoy), the open-source CSIP-AUS utility
server from the Australian National University — the implementation ANU's
certification program runs on. It needs Docker:

```bash
python scripts/e2e_server.py up
pytest tests/e2e -v
python scripts/e2e_server.py down
```

The unit suite proves this client does what we believe the standard requires.
That one proves the belief survives contact with an implementation nobody here
wrote. See [Testing](https://dersecurity.github.io/py20305/testing/).

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
