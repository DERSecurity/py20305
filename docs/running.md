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

## Simulating a loss of communications

`comms_loss_seconds` puts the client into loss-of-communications mode after that
long without reaching the server: it opts out of the rest of any active event,
manages the DER at the planning limit, and keeps opting out until contact
returns. Verifying that behavior normally means taking the server away, which is
not something you can do to a production head-end.

`CsipClient.simulate_comm_loss(duration_seconds)` produces the silence instead.
Outbound requests fail as though the network were gone, and notifications are
accepted but not acted on, so the ordinary detector sees exactly what a real
outage looks like and reacts the same way. Nothing is faked downstream of that:
the detector, the diagnostics, the retry ladder and the recovery path all run
for real.

```python
status = await client.simulate_comm_loss(1200)   # 20 minutes
...
status = await client.clear_comm_loss_simulation()
```

A few things worth knowing before you point this at a live site:

- **The window always closes.** It expires on its own, and a restart clears it.
  There is no way to leave a site isolated by forgetting about it.
- **Activation waits for work already in progress.** A request already talking
  to the server, or a notification handler already running, is allowed to
  finish first — otherwise one of them completes just after you were told the
  link was down, and refreshes the contact clock or applies a setpoint. The
  returned status reports whether both drains finished; if one did not, the
  isolation was not clean and the window is still open.
- **Clearing drives recovery immediately.** Left to the schedule, the client
  would not leave comms-loss mode until the connectivity heartbeat and then a
  probe tick had both come round, which on default settings is over two minutes
  of looking like nothing happened.
- **`phase` tells you how recovery went.** Clearing the gate and leaving
  comms-loss mode are different events, and recovery re-polls schedules against
  the real server, so it can fail. Watch for `recovered` rather than assuming
  it.
- **The device does not rejoin the event it was opted out of.** That is the
  ordinary comms-loss rule, not an artifact of the simulation: the resume-after
  boundary holds the DER at the planning limit until an event starting after it
  arrives.
- **The loss-of-communications record is marked.** When the injected failure is
  what produced the silence, that diagnostic carries a `simulated` flag, so it
  cannot be mistaken for a genuine outage. Attribution follows the failures
  themselves, not merely whether a window was open: a link already down when you
  arm a simulation still reports a genuine outage. The flag is on that entry
  specifically — other diagnostics raised while a window is open are not marked,
  deliberately, so that a real fault occurring during a test stays legible as
  one.
- **The redirect probe is not gated.** `run_redirect_probe` opens its own
  connection for its first leg, so that step still reaches the network during a
  window and will appear to succeed while the second leg fails. It does not feed
  connectivity health, so the detector is unaffected — but the mixed result is
  confusing if you run the probe mid-simulation.

The client applies no policy of its own here: it will isolate itself whenever
asked, for as long as asked. Deciding whether simulation should be available at
all, and bounding how long a window may run, belongs to whatever is driving it.

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
