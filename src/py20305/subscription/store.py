"""Where a subscription manager keeps its subscription records.

A client's subscriptions belong to its EndDevice at the server: one
SubscriptionList, one notificationURI. A host application that runs several
client processes for one certificate gives every process's
:class:`~py20305.subscription.manager.SubscriptionManager` the same store, so the
processes see one subscription set. A process then finds a subscription another
process created and makes no POST for that resource, and classifies a
notification by the subscription record another process wrote.

The default, :class:`MemorySubscriptionStore`, keeps the records in a dict in
the process, which is how the manager has always held them.

A record has the same fields as an entry of
:meth:`SubscriptionManager.to_checkpoint`. :func:`subscription_record` and
:func:`subscription_from_record` convert between the two, so a store backed by a
database can keep rows in the checkpoint format.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from py20305.subscription.manager import SubscriptionState

__all__ = [
    "MemorySubscriptionStore",
    "SubscriptionStore",
    "subscription_from_record",
    "subscription_record",
]


@runtime_checkable
class SubscriptionStore(Protocol):
    """The subscription records of one client, keyed by subscription href.

    The manager calls these methods from its event loop and does not await them,
    so each call must return quickly: a local database, or a cache the host keeps
    in step with a shared one.

    ``get`` and ``values`` may return copies. The manager calls ``put`` after
    every change it makes to a record, so a store that copies on read loses
    nothing.

    Concurrency: the library takes no lock across processes. One manager
    maintains the set: only it renews, reconciles and cancels subscriptions.
    Every other manager is built with ``maintains_set=False``, which a client
    without own-EndDevice work sets when the manager is attached, and only adds
    subscriptions for resources nobody has subscribed to yet. A manager re-reads
    a record after each await before writing it back, so a cancellation or
    removal written in the meantime is kept. Two processes that subscribe to the
    same resource at the same moment can both POST, because the lookup, the POST
    and the ``put`` are not one atomic step; see the guide for how to avoid it.
    """

    def get(self, subscription_uri: str) -> SubscriptionState | None:
        """Return the record for ``subscription_uri``, or None."""
        ...

    def put(self, state: SubscriptionState) -> None:
        """Insert or replace the record keyed by ``state.subscription_uri``."""
        ...

    def remove(self, subscription_uri: str) -> None:
        """Delete the record for ``subscription_uri``. An unknown href is not an error."""
        ...

    def values(self) -> list[SubscriptionState]:
        """Return every record, in insertion order where the store keeps one."""
        ...


class MemorySubscriptionStore:
    """Subscription records in a dict, in this process. The default store.

    Returns the stored objects themselves, not copies, so two managers given the
    same instance in one process share one set of records.
    """

    def __init__(self) -> None:
        self._records: dict[str, SubscriptionState] = {}

    def get(self, subscription_uri: str) -> SubscriptionState | None:
        """Return the record for ``subscription_uri``, or None."""
        return self._records.get(subscription_uri)

    def put(self, state: SubscriptionState) -> None:
        """Insert or replace the record keyed by ``state.subscription_uri``."""
        self._records[state.subscription_uri] = state

    def remove(self, subscription_uri: str) -> None:
        """Delete the record for ``subscription_uri``, if present."""
        self._records.pop(subscription_uri, None)

    def values(self) -> list[SubscriptionState]:
        """Return every record, in insertion order."""
        return list(self._records.values())


def subscription_record(state: SubscriptionState) -> dict[str, Any]:
    """Return ``state`` as a checkpoint entry: a dict of JSON types."""
    return {
        "subscription_uri": state.subscription_uri,
        "subscribed_resource": state.subscribed_resource,
        "notification_uri": state.notification_uri,
        "resource_type": state.resource_type,
        "status": state.status,
        "created_at": state.created_at,
        "is_list_subscription": state.is_list_subscription,
        "unmanaged": state.unmanaged,
        "sub_list_href": state.sub_list_href,
        "resubscribe_suppressed": state.resubscribe_suppressed,
    }


def subscription_from_record(entry: dict[str, Any]) -> SubscriptionState:
    """Build a record from a checkpoint entry. Absent optional fields take their defaults."""
    from py20305.subscription.manager import SubscriptionState

    return SubscriptionState(
        subscription_uri=entry["subscription_uri"],
        subscribed_resource=entry["subscribed_resource"],
        notification_uri=entry["notification_uri"],
        resource_type=entry["resource_type"],
        status=entry.get("status", "active"),
        created_at=entry.get("created_at", 0.0),
        is_list_subscription=entry.get("is_list_subscription", False),
        unmanaged=entry.get("unmanaged", False),
        sub_list_href=entry.get("sub_list_href"),
        resubscribe_suppressed=entry.get("resubscribe_suppressed", False),
    )
