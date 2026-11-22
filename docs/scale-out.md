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
- cancelling the subscriptions at shutdown;
- registering EndDevices (`register_end_device` raises `RuntimeError` in a
  process without the work), and re-registering the client's own EndDevice
  after a loss of communications;
- the DERCapability, DERSettings and DERStatus PUTs and the MirrorUsagePoint
  posts a `TelemetryCoordinator` makes for the client's own LFDI.

A process without the work still subscribes to the resources it needs that
nobody has subscribed to yet. A `TelemetryCoordinator` in each process reports
the devices that process acts for: give it their LFDIs, and use
`start_device_telemetry` and `stop_device_telemetry` as devices move.

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
)
client.attach_subscriptions(manager, notification_server)
```

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
  written back to the store, so every process sees them.
- A notification is classified by the subscription record, whichever process
  received it.
- A process with a device filter never cancels a subscription during
  rediscovery: a resource it does not want may be one another process needs.

The calls are synchronous and made from the event loop, so they must return
quickly. The library takes no lock across processes: two processes that
subscribe to the same resource at the same moment can both POST. Connect the
processes one at a time, or make the store refuse a second active record for
one resource.

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
error from acting on it, so the host can keep the notification and retry.

## Share the request budget

A server can limit how often one client sends requests, and the processes
share that limit. Pass each client a limiter:

```python
client = CsipClient(url, tls=tls, request_limiter=shared_budget)
```

`shared_budget` implements `py20305.client.request_budget.RequestLimiter`: an
`async def acquire()` that returns when one more request may be sent. The
client awaits it before every request to the server, each retry included.

## What the host provides

- **Stores shared by every process.** The subscription store above.
- **An assignment of EndDevices to processes**, as each process's device
  filter, and one process with the own-EndDevice work.
- **A notification queue** from the listener to the processes, when the
  listener runs in a process that does not act for every EndDevice.
- **A shared request limiter**, if the server limits the client's request rate.

## Defaults

With none of these options, a client behaves exactly as it did before:

| Option | Default | Behavior |
|---|---|---|
| `SubscriptionManager(store=...)` | None | Records kept in a dict in the process |
| `CsipClient(device_filter=...)` | None | Every EndDevice is walked and acted for |
| `CsipClient(own_end_device=...)` | True | This process does all own-EndDevice work |
| `NotificationServer(forward=...)` | None | Each notification is processed here, after the 201 |
| `CsipClient(request_limiter=...)` | None | Requests are sent at once |
