# Running a client

Two ways to use this package. Embedding it in your own application is covered
in the [quickstart](quickstart.md); this page covers running it as a service.

```bash
pip install "py20305[cli,sunspec]"
py20305 --config client.yaml
```

## The configuration file

One document describes the running client. A copy of the common options, commented, is at
[`examples/client.example.yaml`](https://github.com/DERSecurity/py20305/blob/main/examples/client.example.yaml);
the minimum is:

```yaml
server:
  url: https://server.example.com:8443

tls:
  client_cert: certs/client.pem
  client_key: certs/client.key
  ca_cert: certs/ca.pem

devices:
  - type: sunspec
    lfdi: "0000000000000000000000000000000000000000"
    host: 192.168.1.50
    port: 502
```

Paths are relative to the configuration file, not the working directory —
systemd will not start the process where you wrote it.

YAML and JSON are both accepted. YAML needs the `cli` extra; JSON works with
no extra dependency.

## Check before you run

```bash
py20305 --config client.yaml --check
```

Validates the file, resolves the certificate, and prints the LFDI the utility
has to register. Connects to nothing. A mistake is reported with the field that
caused it:

```
error: invalid configuration in client.yaml:
  devices.0.sunspec.lfdi: Value error, lfdi must be 40 hex characters (length is 8, must be 40)
```

## What it does on start

1. Loads and validates the configuration, then configures logging.
2. Connects, retrying with backoff while the server is unreachable — a client
   on a gateway usually starts before the server is up, and exiting would just
   move the problem to whatever restarts it.
3. Registers an EndDevice for its own certificate identity, but only if the
   server does not already have one. Registering unconditionally would create a
   duplicate on every restart and the utility would see one device as several.
   A server that refuses in-band registration is not fatal; many provision out
   of band.
4. Starts reporting each configured device, unless `telemetry.enabled` is
   `false`.
5. Polls once, then runs the schedule until stopped.

## What it reports

With `telemetry.enabled` — on by default — each configured device is read on a
schedule and reported to the server:

| Resource | How often |
|---|---|
| Meter readings, as MirrorUsagePoints | `post_rate_seconds` |
| DERStatus | `post_rate_seconds` |
| DERAvailability | `post_rate_seconds` |
| DERSettings, when it has changed | `der_settings_poll_rate_seconds` |
| DERCapability, when it has changed | `der_capability_poll_rate_seconds` |
| LogEvents, when a device raises an alarm | On the reading cycle |

The server has the final say on the posting rate: an `EndDevice.postRate` above
zero replaces `post_rate_seconds` for that device, which is what IEEE 2030.5
specifies. A server exposing no MirrorUsagePointList stops the readings only —
the DER resources are a separate conversation and are still PUT — and the client
says so once rather than failing a cycle forever.

Rediscovery re-reads every path. A server that moves its resources, or that
brings the MirrorUsagePoint function set online only after the client
connected, is picked up without a restart.

## How an event ends

An event runs to the end of its interval unless the server ends it sooner. Two
signals do that, and the client honors both:

- `EventStatus.currentStatus` set to 2 on an event still in the list.
- The event removed from the DERControlList before its Effective Scheduled
  Period is over. IEEE 2030.5-2023 §10.2.2.3 rule p) makes removal a
  cancellation, and notes that this differs from earlier revisions: a server
  built against IEEE 2030.5-2018 signals cancellation only by setting the
  status.

Either signal reverts the device to the DefaultDERControl and POSTs "The event
has been cancelled" when the event's `responseRequired` asks for it. Cancelling
an event that is already running applies the wind-down randomization §10.2.3.3
requires, so the revert can land later than the cancellation that caused it.

Removal is acted on only when the list was fetched completely. A DERControl
fetch that fails or does not parse leaves the client holding no list rather than
an empty one. The distinction matters: an empty list is a server that removed
every event, and treating a failed fetch as one would revert every device on a
single transient error.

## Stopping

`SIGINT` or `SIGTERM` asks it to stop; it finishes what it is doing and closes
its session in order. `Ctrl-C` works on every platform.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Stopped on request |
| 2 | The configuration is wrong — retrying will not help |
| 3 | The server could not be reached, or the run loop failed |

Distinguished so a supervisor can restart on 3 and not restart-loop on 2.

## Under systemd

A complete unit is at
[`examples/systemd/py20305.service`](https://github.com/DERSecurity/py20305/blob/main/examples/systemd/py20305.service),
with the install steps -- service account, virtualenv, certificate
permissions -- in the [README beside
it](https://github.com/DERSecurity/py20305/blob/main/examples/systemd/README.md).
The essentials:

```ini
[Service]
Type=simple
ExecStart=/opt/py20305/venv/bin/py20305 --config /etc/py20305/client.yaml
Restart=on-failure
RestartPreventExitStatus=2
User=py20305
Group=py20305
```

`RestartPreventExitStatus=2` is the point of the distinct exit codes: a broken
configuration stops rather than restarting forever.

The shipped unit adds the sandboxing a client needs none of the privileges
around -- read-only filesystem, no devices, no new privileges. Those assume a
device reached over Modbus TCP; one on a serial port needs its tty granted
back, and the unit says how.

This package deliberately does not supervise itself. Retrying the *connection*
is its job; restarting a crashed *process* is systemd's, and doing it here
would hide failures from the thing meant to observe them.

## The management API

Off by default. When enabled it binds to loopback, because it is
unauthenticated — exposing it on a routable address makes the client's controls
available to that network.

```yaml
api:
  enabled: true
  host: 127.0.0.1
  port: 8080
```

Needs the `api` extra. See [Management API](api.md).

## Logging

Always to stderr, which is what journald and `docker logs` collect. Add a file
if you also want one:

```yaml
logging:
  level: INFO
  file: /var/log/py20305/client.log
```

`--log-level DEBUG` overrides the configured level for one run. It does not change where the logs go.

Only the runner configures logging. The library itself just calls `getLogger`,
so embedding it never takes handler policy away from your application.
