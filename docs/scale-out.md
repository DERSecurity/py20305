# Running one client in several processes

A host application can run several client processes that act as one IEEE
2030.5 client: one certificate, so one LFDI, one EndDevice and one
subscription set at the server. This page says what the host provides, what
the library does, and what stays the same when the host provides nothing.

Everything here is for code that embeds the library. The `py20305` runner
runs one process and uses none of it.

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

The calls are synchronous and made from the event loop, so they must return
quickly. The library takes no lock across processes: two processes that
subscribe to the same resource at the same moment can both POST. See
[What the host provides](#what-the-host-provides) for how to avoid that.

## What the host provides

- **One store per kind of state, shared by every process.** The subscription
  store above.
- **A decision about which process manages the subscription set.** Renewing,
  reconciling and cancelling subscriptions from several processes at once
  sends the same request several times. Run those in one process.

## Defaults

With no store given, the subscription manager keeps its records in a dict in
the process, exactly as before.
