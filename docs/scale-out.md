# Running one client in several processes

A host application can run several client processes that act as one IEEE
2030.5 client: one certificate, so one LFDI, one EndDevice and one
subscription set at the server, behind one notification address. The
EndDevices the certificate can see are split between the processes, and moved
from one to another when a process stops. This page says what the host
provides, what the library does, and what stays the same when the host
provides nothing.

Everything here is for code that embeds the library. The `py20305` runner
runs one process and uses none of it.

## Split the EndDevices between processes

Give each process's client a device filter. Discovery then walks the
FunctionSetAssignments, DERPrograms and DERControls of only the EndDevices the
filter accepts, so the process holds events for, applies controls to and posts
Responses for only those devices:

```python
from py20305.client import CsipClient
from py20305.client.scope import DeviceSet

mine = DeviceSet(hrefs=["/edev/3", "/edev/4"])   # or lfdis=[...]
client = CsipClient(url, tls=tls, device_filter=mine, own_end_device=False)
```

A filter is any callable that takes an EndDevice href and its LFDI (40
lowercase hex characters) and returns whether this process acts for it.
`DeviceSet` accepts the EndDevices named by href or by LFDI, and the host can
`add` and `discard` entries as it moves devices. The client reads the filter at
each discovery.

An EndDevice the filter rejects is not fetched below the EndDeviceList. Its
SubscriptionListLink is still recorded, so a process that does not act for the
client's own EndDevice can still subscribe.

## Give the own-EndDevice work to one process

Some work belongs to the client's own EndDevice rather than to the devices it
acts for. `own_end_device=True` (the default) does all of it; give it to one
process and pass `own_end_device=False` to the rest. The work is:

- renewing the subscriptions, reconciling them with the server's
  SubscriptionList, and checking restored subscriptions at connect;
- renewing the subscriptions it adopts and removing duplicate ones;
- cancelling subscriptions, at rediscovery and at shutdown;
- registering EndDevices (`register_end_device` raises `RuntimeError` in a
  process without the work), and re-registering the client's own EndDevice
  after a loss of communications;
- the DERCapability, DERSettings and DERStatus PUTs and the MirrorUsagePoint
  posts a `TelemetryCoordinator` makes for the client's own LFDI.

A process without the work still subscribes to the resources it needs that
nobody has subscribed to yet, and records the subscriptions it adopts. Its
subscription manager is set to `maintains_set=False` when it is attached, so
it never renews, cancels or removes a shared subscription, whatever it is
asked to do: `shutdown(unsubscribe=True)` included.

A `TelemetryCoordinator` in each process reports the devices that process acts
for: give it their LFDIs, and use `start_device_telemetry` and
`stop_device_telemetry` as devices move. A site meter mirrored under the
client's own EndDevice is own-EndDevice work too; configure it in the process
that has the work.

## Share the subscription set

The server holds one SubscriptionList for the client's EndDevice. Every
process POSTs to it with the same notificationURI, so the processes have to
agree on what is already subscribed, or each one subscribes to everything again
and the server delivers every notification several times.

Give each process's `SubscriptionManager` the same store:

```python
from py20305.subscription.manager import SubscriptionManager

manager = SubscriptionManager(
    client.http,
    notification_uri,
    store=shared_subscriptions,   # the same store in every process
    unsubscribe_on_close=False,   # a process that stops leaves the set in place
)
client.attach_subscriptions(manager, notification_server)
```

Build every manager with `unsubscribe_on_close=False`. With the default, the
process with the own-EndDevice work DELETEs the whole set when it stops, and
the processes still running lose their subscriptions. To remove the set when
the whole client stops for good, stop the other processes first, then call
`shutdown(unsubscribe=True)` in the process with the own-EndDevice work.

`shared_subscriptions` implements
`py20305.subscription.store.SubscriptionStore`: `get`, `put`, `remove` and
`values` over the subscription records, keyed by subscription href. A record
has the fields of a `SubscriptionManager.to_checkpoint()` entry, and
`subscription_record()` and `subscription_from_record()` convert between the
two, so a database table can hold checkpoint rows.

With a shared store:

- A process that finds an active subscription for a resource makes no POST for
  it, whichever process created it.
- A renewal, a new href from the server, and a cancellation by the server are
  written back to the store, so every process sees them. A renewal is written
  back only if the record is still active when the server answers, so a
  cancellation recorded while the renewal was in flight is kept.
- A cancellation by the server with status 1 stops every process from
  subscribing to that resource again, until a reconnect.
- A notification is classified by the subscription record, whichever process
  received it.
- Only the process with the own-EndDevice work and no device filter cancels a
  subscription during rediscovery. Any other process may not know which
  resources the others need.
- The process with the own-EndDevice work drops, at rediscovery, only the
  records of resources it wants that the server no longer lists, and never a
  record written while it was reading the list.

The calls are synchronous and made from the event loop, so they must return
quickly. The library takes no lock across processes. Looking up a record,
POSTing the subscription and storing the result are separate steps, so two
processes that subscribe to the same resource at the same moment can both
POST. A store that refuses the second record does not prevent this: the
second POST has already reached the server, and refusing the record leaves
that subscription untracked. Connect the processes one at a time. After that,
two processes POST for one resource only when both discover it at the same
moment, as when the server adds a DERProgram that serves EndDevices of both.
If two subscriptions for one resource are created anyway, a server that
applies the renewal rule of IEEE 2030.5 §8.9.3.4 keeps one; with any other
server, notifications for that resource can arrive twice until the extra
subscription expires, and the process with the own-EndDevice work removes it
when it next connects with `adopt_on_connect=True, remove_duplicates=True`.

## Share the event state

Give each process's client the same event state store:

```python
client = CsipClient(url, tls=tls, device_filter=mine, event_state_store=shared_events)
```

`shared_events` implements `py20305.events.shared_state.EventStateStore`. The
client keeps in it, for each EndDevice it acts for:

- a snapshot of the events the device is under: their state, their randomized
  start and duration, whether the control was applied to the device or
  rejected there, whether a higher-priority event superseded it there, and
  whether it was opted out during a loss of communications;
- the DefaultDERControl last applied to the device;
- the loss-of-communications resume boundary, before which recovered events
  are not resumed;
- each Response posted for the device, recorded as it is posted.

It also keeps the randomization offsets of each event, so an event whose
DERProgram serves devices of several processes starts and ends at the same
moment in all of them. Values are JSON types, so a store can write them to a
database as they are.

The store's concurrency rules are in its docstring. In short: a device's
snapshot has one writer, the process acting for it; `claim_offsets` must be
compare-and-set across processes; `record_response` only appends.

Each process detects a loss of communications on its own, from its own
requests to the server. The shared state carries what the detection did to a
device (events opted out, the resume boundary), not the detection itself.

## Move devices from one process to another

To move EndDevices from process A to process B:

```python
# In process A
a_devices.discard(hrefs=moving)
await client_a.release_devices(moving)

# In process B, once A has released them
b_devices.add(hrefs=moving)
await client_b.take_over_devices(moving)
```

`release_devices` saves the devices' snapshots, forgets the events that
concern only them without posting a Response or writing to a device, and
rediscovers without them. The subscriptions stay as they are.

`take_over_devices` loads the snapshots, rediscovers so the devices' programs
and controls are read from the server again, and then:

- carries on each event the previous process handled, with its timing, and
  posts no Response that was already posted for the device;
- applies a running event to a device that was neither told it started nor
  told it was rejected, as when the previous process stopped in the middle of
  an activation;
- starts the activation and completion timers of the events it loaded,
  after the devices are mapped, so an event due while no process acted for
  the device starts once, on the device;
- handles a control the server added or changed while no process acted for
  the device, or while the take-over ran: the rediscovery fetches it, and a
  notification that arrives during the rediscovery is left to it.

A process that stops without `release_devices`, by crashing, leaves the last
snapshot it saved. Snapshots are saved after each pass over a program's
controls and after each activation and completion, and each Response is
recorded as it is posted, so what can repeat is narrow: a control applied in
the moments before the process stopped can be applied again, never left
unapplied, and a Response posted between its POST returning and its record
being written can be posted again.

Moving devices under a `group_lookup`, which fans one EndDevice out to
several local devices, carries the event state over but does not apply a
running event to devices that never received it.

## Receive notifications in one process, act on them in another

The server sends every notification to the one notificationURI. Run the
listener in one process and hand each notification to a queue:

```python
async def enqueue(body: bytes) -> None:
    await queue.put(body)        # commit it before returning

listener = NotificationServer(host="0.0.0.0", port=10443, tls=tls, forward=enqueue)
```

With `forward` set, the listener checks each notification as before, awaits
`forward(body)`, and answers 201 only after it returns. If it raises, the
listener answers 503, so the server does not take a notification nobody holds
as delivered.

Each process reads the queue and calls `process_notification`:

```python
acted = await client.process_notification(body)
```

It returns False, and does nothing, for a notification about a resource that
concerns no EndDevice this process acts for (`client.acts_for_resource(href)`
says which do), so the host can deliver every notification to every process.
It raises `ValueError` for a body that is not a Notification, and passes on any
error from acting on it, a failed rediscovery included, so the host can keep
the notification and retry; the retry is not skipped as a duplicate. A
notification that arrives while a rediscovery runs is left to that
rediscovery, except one that reports a resource cancelled, moved or deleted:
that one is matched against the EndDevices the process acted for when the
rediscovery began, so a process never acts on the deletion of another
process's EndDevice.

## Share the request budget

A server can limit how often one client sends requests, and the processes
share that limit. Pass each client a limiter:

```python
client = CsipClient(url, tls=tls, request_limiter=shared_budget)
```

`shared_budget` implements `py20305.client.request_budget.RequestLimiter`: an
`async def acquire()` that returns when one more request may be sent. The
client awaits it before every request to the server, each retry included.
An exception from `acquire` reaches the code that made the request as it was
raised: it is not retried, and it does not count as a failure to reach the
server.

## What the host provides

- **Stores shared by every process.** The subscription store and the event
  state store above.
- **An assignment of EndDevices to processes**, as each process's device
  filter, and one process with the own-EndDevice work. When a process stops,
  the host moves its devices to the others with `release_devices` and
  `take_over_devices`.
- **A notification queue** from the listener to the processes, when the
  listener runs in a process that does not act for every EndDevice.
- **A shared request limiter**, if the server limits the client's request rate.

## Defaults

With none of these options, a client behaves exactly as it did before:

| Option | Default | Behavior |
|---|---|---|
| `SubscriptionManager(store=...)` | None | Records kept in a dict in the process |
| `SubscriptionManager(maintains_set=...)` | True | This manager renews, reconciles and cancels the set |
| `CsipClient(device_filter=...)` | None | Every EndDevice is walked and acted for |
| `CsipClient(own_end_device=...)` | True | This process does all own-EndDevice work |
| `CsipClient(event_state_store=...)` | None | Event state kept in this process only |
| `NotificationServer(forward=...)` | None | Each notification is processed here, after the 201 |
| `CsipClient(request_limiter=...)` | None | Requests are sent at once |
